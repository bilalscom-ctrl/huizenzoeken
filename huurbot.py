#!/usr/bin/env python3
"""
huurbot.py v3 - Meldt nieuwe huurwoningen binnen ~1 minuut op je telefoon.

Wat v3 beter doet dan v2:
  - Nooit crashen: elke bron is geisoleerd, elke fout wordt opgevangen
  - Beleefd: per website maximaal 1 verzoek per X seconden, met spreiding
  - Zuinig: gebruikt ETag/Last-Modified, dus onveranderde pagina's kosten niks
  - Slim opnieuw proberen: bij een tijdelijke fout 3x met oplopende pauze
  - Respecteert blokkades: 403/429 zet een bron tijdelijk op pauze i.p.v.
    doorrammen (dat is precies hoe je een permanente ban oploopt)
  - Kant-en-klare reactiebrief per woning, direct in Telegram
"""

import argparse
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    DATA_DIR = HERE
STATE_FILE = DATA_DIR / "state.json"
SOURCES_FILE = HERE / "sources.yaml"
LOG_FILE = DATA_DIR / "huurbot.log"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Per website onthouden wanneer we er voor het laatst waren, zodat we
# nooit twee verzoeken kort achter elkaar naar dezelfde server sturen.
_laatste_bezoek = defaultdict(float)
_sessie = None


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if LOG_FILE.stat().st_size > 5_000_000:
            LOG_FILE.write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- config

def load_env():
    envfile = HERE / ".env"
    if envfile.exists():
        for raw in envfile.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_sources():
    if not SOURCES_FILE.exists():
        sys.exit(f"sources.yaml niet gevonden op {SOURCES_FILE}")
    with open(SOURCES_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    sources = [s for s in cfg.get("sources", []) if s.get("enabled", True)]
    if not sources:
        sys.exit("Geen actieve bronnen in sources.yaml.")
    return cfg, sources


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log("state.json onleesbaar, begin opnieuw")
    return {"seen": {}, "http_cache": {}, "pauze": {}}


def save_state(state):
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as e:
        log(f"state opslaan mislukt: {e}")


# ---------------------------------------------------------------- tijdschema

def current_interval(cfg):
    sched = cfg.get("schedule", {}) or {}
    fast = int(sched.get("fast_seconds", 60))
    slow = int(sched.get("slow_seconds", 600))
    start = int(sched.get("fast_from_hour", 7))
    end = int(sched.get("fast_until_hour", 21))
    if sched.get("fast_on_weekdays_only") and datetime.now().weekday() >= 5:
        return slow
    return fast if start <= datetime.now().hour < end else slow


# ---------------------------------------------------------------- ophalen

class Geblokkeerd(Exception):
    """De site wil ons (even) niet. Niet blijven proberen."""


def sessie():
    global _sessie
    if _sessie is None:
        _sessie = requests.Session()
        _sessie.headers.update({
            "User-Agent": UA,
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
    return _sessie


def fetch(url, timeout=20, cache=None, min_gap=8.0, pogingen=3):
    """
    Haalt een pagina op.

    - wacht minstens min_gap seconden sinds het vorige bezoek aan DEZELFDE site
    - stuurt ETag/Last-Modified mee; bij 304 (niets veranderd) → None
    - probeert het bij een tijdelijke fout 3x met oplopende pauze
    - bij 403/429/503 → Geblokkeerd, zodat de aanroeper de bron kan pauzeren
    """
    host = urlparse(url).netloc

    wacht = min_gap - (time.time() - _laatste_bezoek[host])
    if wacht > 0:
        time.sleep(wacht + random.uniform(0, 1.5))

    headers = {}
    if cache:
        if cache.get("etag"):
            headers["If-None-Match"] = cache["etag"]
        if cache.get("modified"):
            headers["If-Modified-Since"] = cache["modified"]

    laatste_fout = None
    for poging in range(1, pogingen + 1):
        try:
            _laatste_bezoek[host] = time.time()
            r = sessie().get(url, headers=headers, timeout=timeout)

            if r.status_code in (403, 429, 503):
                raise Geblokkeerd(f"HTTP {r.status_code}")
            if r.status_code == 304:
                return None, cache  # niks veranderd
            r.raise_for_status()

            nieuw_cache = {
                "etag": r.headers.get("ETag"),
                "modified": r.headers.get("Last-Modified"),
            }
            return r.text, nieuw_cache

        except Geblokkeerd:
            raise
        except requests.RequestException as e:
            laatste_fout = e
            if poging < pogingen:
                time.sleep(2 ** poging + random.uniform(0, 1.5))

    raise laatste_fout


# ---------------------------------------------------------------- uitlezen

def extract_listings(html, source):
    """Haalt kandidaat-advertenties uit een overzichtspagina."""
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(source["link_pattern"], re.I)
    excludes = [re.compile(p, re.I) for p in source.get("exclude", [])]
    require_text = [w.lower() for w in source.get("require_text", [])]
    exclude_text = [w.lower() for w in source.get("exclude_text", [])]

    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(source["url"], href)
        p = urlparse(absolute)
        clean = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")

        if not pattern.search(clean) or any(x.search(clean) for x in excludes):
            continue

        label = " ".join(a.get_text(" ", strip=True).split())
        if not label:
            parent = a.find_parent(["li", "article", "div", "section"])
            if parent:
                label = " ".join(parent.get_text(" ", strip=True).split())[:160]
        if not label:
            label = clean.rsplit("/", 2)[-1].replace("-", " ")

        low = label.lower()
        if require_text and not any(w in low for w in require_text):
            continue
        if exclude_text and any(w in low for w in exclude_text):
            continue

        if clean not in found or len(label) > len(found[clean]):
            found[clean] = label[:200]

    return found


# ---------------------------------------------------------------- details

PRICE_RE = re.compile(r"[\u20ac]\s?([\d]{1,3}(?:[.\s][\d]{3})+|[\d]{3,5})")
ROOMS_RE = re.compile(r"(\d+)\s*kamers?", re.I)
BEDROOMS_RE = re.compile(r"(\d+)\s*slaapkamers?", re.I)
ADDRESS_RE = re.compile(
    r"\b([A-Z][a-zA-Z\u00c0-\u017f'.]+(?:\s+[a-zA-Z\u00c0-\u017f'.]+){0,3}\s+\d+[a-zA-Z]?)\b"
)


def parse_price(text):
    kandidaten = []
    for m in PRICE_RE.finditer(text):
        raw = m.group(1).replace(".", "").replace(" ", "")
        try:
            v = int(raw)
        except ValueError:
            continue
        if 300 <= v <= 5000:
            kandidaten.append(v)
    return min(kandidaten) if kandidaten else None


def parse_rooms(text):
    m = BEDROOMS_RE.search(text)
    if m:
        return int(m.group(1)), "slaapkamers"
    m = ROOMS_RE.search(text)
    if m:
        return int(m.group(1)), "kamers"
    return None, None


def parse_address(label, url):
    """Probeert het adres uit het label te halen, anders uit de URL."""
    m = ADDRESS_RE.search(label)
    if m:
        return m.group(1).strip()
    staart = url.rstrip("/").rsplit("/", 1)[-1]
    staart = re.sub(r"^[0-9a-f]{6,}-?", "", staart)
    mooi = staart.replace("-", " ").strip().title()
    return mooi if len(mooi) > 3 else ""


def enrich(url, label, min_gap):
    """Haalt prijs/kamers/adres van de detailpagina. Faalt zacht."""
    tekst = label
    try:
        html, _ = fetch(url, timeout=15, min_gap=min_gap, pogingen=2)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            tekst = label + " " + " ".join(soup.get_text(" ", strip=True).split())[:6000]
    except (Geblokkeerd, requests.RequestException, Exception) as e:  # noqa: BLE001
        log(f"  detailpagina overgeslagen ({type(e).__name__}) - meld zonder details")

    prijs = parse_price(tekst)
    kamers, soort = parse_rooms(tekst)
    return {
        "price": prijs,
        "rooms": kamers,
        "roomkind": soort,
        "address": parse_address(label, url),
        "_tekst": tekst.lower(),
    }


def passes_filter(cfg, d):
    """(melden?, reden). BIJ TWIJFEL ALTIJD MELDEN."""
    f = cfg.get("filter", {}) or {}
    if not f.get("enabled", False):
        return True, ""

    # Woorden die de woning onbruikbaar maken, ook als ze niet in de
    # link stonden maar wel op de detailpagina (zoals "woningruil").
    tekst = d.get("_tekst") or ""
    for woord in (f.get("exclude_keywords") or []):
        if woord.lower() in tekst:
            return False, f"bevat '{woord}'"

    prijs, kamers = d.get("price"), d.get("rooms")
    if prijs is not None:
        if f.get("max_price") and prijs > f["max_price"]:
            return False, f"te duur (EUR {prijs})"
        if f.get("min_price") and prijs < f["min_price"]:
            return False, f"verdacht goedkoop (EUR {prijs})"

    if (kamers is not None and f.get("min_rooms")
            and d.get("roomkind") == "slaapkamers" and kamers < f["min_rooms"]):
        return False, f"te weinig slaapkamers ({kamers})"

    return True, ""


def format_details(d):
    bits = []
    if d.get("price"):
        bits.append(f"EUR {d['price']} p/m")
    if d.get("rooms"):
        bits.append(f"{d['rooms']} {d.get('roomkind') or 'kamers'}")
    return "  |  ".join(bits)


# ---------------------------------------------------------------- brief

def maak_brief(cfg, details, url):
    """Bouwt een kant-en-klare reactiebrief met adres en prijs erin."""
    b = cfg.get("brief", {}) or {}
    if not b.get("enabled", True):
        return None

    adres = details.get("address") or "de woning"
    prijs = f" (EUR {details['price']} p/m)" if details.get("price") else ""

    sjabloon = b.get("tekst") or (
        "Geachte heer/mevrouw,\n\n"
        "Graag reageren wij op {adres}{prijs}.\n\n"
        "Wij zijn {namen}, {leeftijden}, beiden in vaste dienst met een "
        "gezamenlijk bruto inkomen van {inkomen} per maand. Wij zoeken een "
        "woning voor de lange termijn en kunnen per {beschikbaar} beschikken.\n\n"
        "Ons dossier is compleet: loonstroken, werkgeversverklaringen, "
        "verhuurdersverklaring, uittreksel BRP en identiteitsbewijzen liggen "
        "klaar en kunnen wij direct aanleveren.\n\n"
        "Wij komen graag kijken. U kunt ons bereiken op {telefoon}.\n\n"
        "Met vriendelijke groet,\n{namen}\n{telefoon}\n{email}"
    )

    velden = {
        "adres": adres,
        "prijs": prijs,
        "namen": b.get("namen", "[namen]"),
        "leeftijden": b.get("leeftijden", "[leeftijden]"),
        "inkomen": b.get("inkomen", "[inkomen]"),
        "beschikbaar": b.get("beschikbaar", "per direct"),
        "telefoon": b.get("telefoon", "[telefoon]"),
        "email": b.get("email", "[email]"),
        "url": url,
    }
    try:
        return sjabloon.format(**velden)
    except (KeyError, IndexError) as e:
        log(f"brief-sjabloon fout ({e}) - standaardtekst gebruikt")
        return None


# ---------------------------------------------------------------- melden

def _telegram(payload):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    payload["chat_id"] = chat_id
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=15,
        )
        if not r.ok:
            log(f"telegram fout {r.status_code}: {r.text[:180]}")
        return r.ok
    except requests.RequestException as e:
        log(f"telegram fout: {e}")
        return False


def notify(cfg, titel, body, url=None, urgent=True, knoppen=None):
    ok = False

    tekst = f"*{titel}*\n{body}"
    if url and not knoppen:
        tekst += f"\n\n{url}"

    payload = {
        "text": tekst,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
        "disable_notification": not urgent,
    }
    if knoppen:
        payload["reply_markup"] = {"inline_keyboard": [knoppen]}
    ok = _telegram(payload) or ok

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            h = {"Title": titel.encode("utf-8"),
                 "Priority": "urgent" if urgent else "default", "Tags": "house"}
            if url:
                h["Click"] = url
            r = requests.post(f"https://ntfy.sh/{topic}",
                              data=body.encode("utf-8"), headers=h, timeout=15)
            ok = ok or r.ok
        except requests.RequestException as e:
            log(f"ntfy fout: {e}")

    if not ok:
        log(f"GEEN MELDING VERSTUURD -> {titel} | {body[:90]} | {url}")
    return ok


def meld_woning(cfg, bron, url, label, details):
    """Stuurt de alert + direct daarna de kant-en-klare brief."""
    extra = format_details(details)
    body = label[:160]
    if extra:
        body += f"\n\n{extra}"
    herinnering = cfg.get("reply_reminder")
    if herinnering:
        body += f"\n\n{herinnering.strip()}"

    knoppen = [{"text": "Bekijk woning", "url": url}]
    dossier = (cfg.get("brief", {}) or {}).get("dossier_url")
    if dossier and dossier.startswith("http"):
        knoppen.append({"text": "Mijn dossier", "url": dossier})

    notify(cfg, f"Nieuwe woning - {bron}", body, url, knoppen=knoppen)

    brief = maak_brief(cfg, details, url)
    if brief:
        # Aparte pure-tekst melding: in Telegram kun je die in een keer
        # kopieren met lang indrukken. Geen opmaak, anders plak je sterretjes.
        _telegram({
            "text": brief,
            "disable_web_page_preview": True,
            "disable_notification": True,
        })


# ---------------------------------------------------------------- ronde

def run_round(cfg, sources, state, announce=True):
    seen = state.setdefault("seen", {})
    cache = state.setdefault("http_cache", {})
    pauze = state.setdefault("pauze", {})
    fouten = state.setdefault("failures", {})
    min_gap = float(cfg.get("min_seconds_per_site", 8))
    nieuw_totaal = 0

    for source in sources:
        naam = source["name"]
        bak = seen.setdefault(naam, {})

        tot = pauze.get(naam, 0)
        if tot > time.time():
            continue

        try:
            html, nieuw_cache = fetch(
                source["url"], cache=cache.get(naam), min_gap=min_gap
            )
        except Geblokkeerd as e:
            # Niet doorrammen: 30 minuten met rust laten.
            pauze[naam] = time.time() + 1800
            log(f"[{naam}] {e} - 30 min pauze (voorkomt een echte ban)")
            continue
        except Exception as e:  # noqa: BLE001
            n = fouten.get(naam, 0) + 1
            fouten[naam] = n
            log(f"[{naam}] ophalen mislukt ({n}x): {type(e).__name__}")
            if n == 10:
                notify(cfg, f"Bron werkt al een uur niet: {naam}",
                       f"Check of de URL nog klopt.\n{source['url']}", urgent=False)
            continue

        fouten[naam] = 0
        cache[naam] = nieuw_cache

        if html is None:
            continue  # 304: pagina onveranderd, niks te doen

        try:
            listings = extract_listings(html, source)
        except Exception as e:  # noqa: BLE001
            log(f"[{naam}] uitlezen mislukt: {type(e).__name__}")
            continue

        vers = {u: t for u, t in listings.items() if u not in bak}

        for url, label in vers.items():
            entry = {"label": label,
                     "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if announce:
                details = enrich(url, label, min_gap) if cfg.get("enrich", True) else {}
                entry.update({k: v for k, v in details.items()
                              if v and not k.startswith("_")})
                ok, reden = passes_filter(cfg, details)
                if not ok:
                    log(f"OVERGESLAGEN [{naam}] {label[:50]} - {reden}")
                    entry["skipped"] = reden
                else:
                    nieuw_totaal += 1
                    log(f"NIEUW [{naam}] {label[:60]} {format_details(details)}")
                    try:
                        meld_woning(cfg, naam, url, label, details)
                    except Exception as e:  # noqa: BLE001
                        log(f"  melden mislukt: {type(e).__name__}")
            bak[url] = entry

        if vers:
            log(f"[{naam}] {len(listings)} op de pagina, {len(vers)} nieuw")

        # bak niet eindeloos laten groeien
        if len(bak) > 600:
            oud = sorted(bak.items(), key=lambda kv: kv[1].get("first_seen", ""))
            for u, _ in oud[:len(bak) - 600]:
                bak.pop(u, None)

    save_state(state)
    return nieuw_totaal


# ---------------------------------------------------------------- test

def do_test(cfg, sources):
    print("\n=== TESTMODUS - er worden geen meldingen verstuurd ===\n")
    min_gap = float(cfg.get("min_seconds_per_site", 8))
    for source in sources:
        print(f"--- {source['name']}\n    {source['url']}")
        try:
            html, _ = fetch(source["url"], min_gap=min_gap)
        except Geblokkeerd as e:
            print(f"    GEBLOKKEERD: {e}\n")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"    FOUT: {type(e).__name__}: {e}\n")
            continue
        if html is None:
            print("    onveranderd\n")
            continue
        gevonden = extract_listings(html, source)
        if not gevonden:
            print("    0 gevonden. Patroon klopt niet, of JavaScript-site.\n")
            continue
        print(f"    {len(gevonden)} gevonden:")
        for u, l in list(gevonden.items())[:10]:
            print(f"      - {l[:70]}\n        {u}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    load_env()
    cfg, sources = load_sources()

    if args.test:
        do_test(cfg, sources)
        return

    state = load_state()

    if args.init:
        log("Baseline bouwen (geen meldingen)...")
        run_round(cfg, sources, state, announce=False)
        log(f"Klaar. {sum(len(v) for v in state['seen'].values())} advertenties onthouden.")
        return

    if not STATE_FILE.exists():
        run_round(cfg, sources, state, announce=False)

    if args.once:
        run_round(cfg, sources, state)
        return

    while True:
        try:
            run_round(cfg, sources, state)
        except KeyboardInterrupt:
            log("Gestopt.")
            return
        except Exception as e:  # noqa: BLE001
            log(f"Onverwachte fout in ronde: {type(e).__name__}: {e}")
        time.sleep(max(20, current_interval(cfg) + random.uniform(-10, 15)))


if __name__ == "__main__":
    main()
