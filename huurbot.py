#!/usr/bin/env python3
"""
huurbot.py v2 - Meldt nieuwe huurwoningen binnen ~1 minuut op je telefoon.

Nieuw in v2:
  - Slim tijdschema: overdag elke 60s, 's nachts elke 10 min
  - Detailverrijking: haalt prijs en aantal kamers op bij een nieuwe woning
  - Filter: alleen melden wat binnen budget en kamereis past
  - Bij twijfel meldt hij ALTIJD (liever een melding te veel dan een gemist huis)

Gebruik:
    python3 huurbot.py --test      # laat zien wat er NU gevonden wordt
    python3 huurbot.py --init      # bouwt de baseline, meldt nog niks
    python3 huurbot.py             # draait door en meldt alles wat nieuw is
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
# DATA_DIR wijst op een Railway volume als die er is, anders gewoon deze map.
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
SOURCES_FILE = HERE / "sources.yaml"
LOG_FILE = DATA_DIR / "huurbot.log"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
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
        cfg = yaml.safe_load(f)
    sources = [s for s in cfg.get("sources", []) if s.get("enabled", True)]
    if not sources:
        sys.exit("Geen actieve bronnen in sources.yaml.")
    return cfg, sources


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("state.json onleesbaar, begin opnieuw")
    return {"seen": {}, "last_ok": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- tijdschema

def current_interval(cfg):
    """Overdag gaan woningen online: dan vaak kijken. 's Nachts rustiger."""
    sched = cfg.get("schedule", {})
    fast = int(sched.get("fast_seconds", 60))
    slow = int(sched.get("slow_seconds", 600))
    start = int(sched.get("fast_from_hour", 7))
    end = int(sched.get("fast_until_hour", 21))
    weekdays_only = bool(sched.get("fast_on_weekdays_only", False))

    now = datetime.now()
    if weekdays_only and now.weekday() >= 5:
        return slow
    return fast if start <= now.hour < end else slow


# ---------------------------------------------------------------- scraping

def fetch(url, timeout=20):
    headers = {
        "User-Agent": UA,
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_listings(html, source):
    """Haalt kandidaat-advertenties uit een overzichtspagina."""
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(source["link_pattern"], re.I)
    excludes = [re.compile(p, re.I) for p in source.get("exclude", [])]
    require_text = [w.lower() for w in source.get("require_text", [])]
    # exclude_text kijkt naar de LINKTEKST. Nodig omdat veel sites "Verhuurd"
    # alleen in de tekst zetten en niet in het webadres.
    exclude_text = [w.lower() for w in source.get("exclude_text", [])]

    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(source["url"], href)
        parsed = urlparse(absolute)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

        if not pattern.search(clean):
            continue
        if any(x.search(clean) for x in excludes):
            continue

        label = " ".join(a.get_text(" ", strip=True).split())
        if not label:
            parent = a.find_parent(["li", "article", "div"])
            if parent:
                label = " ".join(parent.get_text(" ", strip=True).split())[:120]
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


# ---------------------------------------------------------------- detailpagina

PRICE_RE = re.compile(r"[\u20ac]\s?([\d]{1,3}(?:[.\s][\d]{3})+|[\d]{3,5})")
ROOMS_RE = re.compile(r"(\d+)\s*kamers?", re.I)
BEDROOMS_RE = re.compile(r"(\d+)\s*slaapkamers?", re.I)


def parse_price(text):
    """Laagste plausibele huurprijs uit de tekst (300 - 5000)."""
    candidates = []
    for m in PRICE_RE.finditer(text):
        raw = m.group(1).replace(".", "").replace(" ", "")
        try:
            v = int(raw)
        except ValueError:
            continue
        if 300 <= v <= 5000:
            candidates.append(v)
    return min(candidates) if candidates else None


def parse_rooms(text):
    """Slaapkamers als die genoemd worden, anders totaal aantal kamers."""
    m = BEDROOMS_RE.search(text)
    if m:
        return int(m.group(1)), "slaapkamers"
    m = ROOMS_RE.search(text)
    if m:
        return int(m.group(1)), "kamers"
    return None, None


def enrich(url, label):
    """
    Haalt de detailpagina op voor prijs en kamers.
    Alleen bij NIEUWE woningen, dus een paar keer per week.
    Lukt het niet? Dan melden we gewoon zonder details.
    """
    text = label
    try:
        html = fetch(url, timeout=15)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = label + " " + " ".join(soup.get_text(" ", strip=True).split())[:6000]
    except Exception as e:  # noqa: BLE001
        log(f"  detailpagina niet gelukt ({e}) - meld zonder details")

    price = parse_price(text)
    rooms, roomkind = parse_rooms(text)
    return {"price": price, "rooms": rooms, "roomkind": roomkind}


def passes_filter(cfg, details):
    """
    (melden_ja_nee, reden).
    BIJ TWIJFEL ALTIJD MELDEN. Een melding te veel kost 30 seconden,
    een gemiste woning kost je een huis.
    """
    f = cfg.get("filter", {})
    if not f.get("enabled", False):
        return True, ""

    price = details.get("price")
    rooms = details.get("rooms")
    max_price = f.get("max_price")
    min_price = f.get("min_price")
    min_rooms = f.get("min_rooms")

    if price is not None:
        if max_price and price > max_price:
            return False, f"te duur (EUR {price} > {max_price})"
        if min_price and price < min_price:
            return False, f"verdacht goedkoop (EUR {price} < {min_price})"

    # Alleen filteren als er echt over SLAAPkamers gesproken wordt.
    # "3 kamers" kan 2 slaapkamers + woonkamer zijn: dan niet wegfilteren.
    if (
        rooms is not None
        and min_rooms
        and details.get("roomkind") == "slaapkamers"
        and rooms < min_rooms
    ):
        return False, f"te weinig slaapkamers ({rooms} < {min_rooms})"

    return True, ""


def format_details(details):
    bits = []
    if details.get("price"):
        bits.append(f"EUR {details['price']} p/m")
    if details.get("rooms"):
        bits.append(f"{details['rooms']} {details.get('roomkind') or 'kamers'}")
    return " | ".join(bits)


# ---------------------------------------------------------------- meldingen

def notify(cfg, title, body, url=None, urgent=True):
    ok = False

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        text = f"*{title}*\n{body}"
        if url:
            text += f"\n\n{url}"
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                    "disable_notification": not urgent,
                },
                timeout=15,
            )
            if r.ok:
                ok = True
            else:
                log(f"telegram fout {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log(f"telegram fout: {e}")

    ntfy_topic = os.environ.get("NTFY_TOPIC")
    if ntfy_topic:
        try:
            headers = {
                "Title": title.encode("utf-8"),
                "Priority": "urgent" if urgent else "default",
                "Tags": "house",
            }
            if url:
                headers["Click"] = url
            r = requests.post(
                f"https://ntfy.sh/{ntfy_topic}",
                data=body.encode("utf-8"),
                headers=headers,
                timeout=15,
            )
            if r.ok:
                ok = True
        except requests.RequestException as e:
            log(f"ntfy fout: {e}")

    if not ok:
        log(f"GEEN MELDING VERSTUURD -> {title} | {body} | {url}")
    return ok


# ---------------------------------------------------------------- ronde

def run_round(cfg, sources, state, announce=True):
    seen = state.setdefault("seen", {})
    last_ok = state.setdefault("last_ok", {})
    failures = state.setdefault("failures", {})
    new_total = 0

    for source in sources:
        name = source["name"]
        bucket = seen.setdefault(name, {})
        try:
            html = fetch(source["url"])
        except requests.RequestException as e:
            n = failures.get(name, 0) + 1
            failures[name] = n
            log(f"[{name}] ophalen mislukt ({n}x): {e}")
            if n == 5:
                notify(
                    cfg,
                    f"Bron werkt niet: {name}",
                    f"5x achter elkaar mislukt. Check of de URL nog klopt.\n{source['url']}",
                    urgent=False,
                )
            continue

        failures[name] = 0
        try:
            listings = extract_listings(html, source)
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] parsen mislukt: {e}")
            continue

        if not listings:
            log(f"[{name}] 0 advertenties - selector klopt mogelijk niet meer")

        fresh = {u: t for u, t in listings.items() if u not in bucket}

        for url, label in fresh.items():
            entry = {
                "label": label,
                "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

            if announce:
                details = enrich(url, label) if cfg.get("enrich", True) else {}
                entry.update({k: v for k, v in details.items() if v is not None})
                ok, reason = passes_filter(cfg, details)

                if not ok:
                    log(f"OVERGESLAGEN [{name}] {label} - {reason}")
                    entry["skipped"] = reason
                else:
                    new_total += 1
                    extra = format_details(details)
                    log(f"NIEUW [{name}] {label} {extra} -> {url}")
                    body = label
                    if extra:
                        body += f"\n{extra}"
                    body += f"\n\n{cfg.get('reply_reminder', 'Reageer nu.')}"
                    notify(cfg, f"Nieuwe woning: {name}", body, url)

            bucket[url] = entry

        last_ok[name] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log(f"[{name}] {len(listings)} advertenties, {len(fresh)} nieuw")
        time.sleep(random.uniform(1.0, 3.0))

    save_state(state)
    return new_total


# ---------------------------------------------------------------- modes

def do_test(sources):
    print("\n=== TESTMODUS - er worden geen meldingen verstuurd ===\n")
    for source in sources:
        name = source["name"]
        print(f"--- {name}\n    {source['url']}")
        try:
            html = fetch(source["url"])
        except requests.RequestException as e:
            print(f"    FOUT: {e}\n")
            continue
        listings = extract_listings(html, source)
        if not listings:
            print("    0 gevonden. link_pattern klopt waarschijnlijk niet.\n")
            continue
        print(f"    {len(listings)} gevonden:")
        for url, label in list(listings.items())[:12]:
            print(f"      - {label[:80]}")
            print(f"        {url}")
        if len(listings) > 12:
            print(f"      ... en nog {len(listings) - 12}")
        print()
        time.sleep(1.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="laat zien wat er gevonden wordt")
    ap.add_argument("--init", action="store_true", help="baseline bouwen, niet melden")
    ap.add_argument("--once", action="store_true", help="een ronde en dan stoppen")
    args = ap.parse_args()

    load_env()
    cfg, sources = load_sources()

    if args.test:
        do_test(sources)
        return

    state = load_state()

    if args.init:
        log("Baseline bouwen (geen meldingen)...")
        run_round(cfg, sources, state, announce=False)
        total = sum(len(v) for v in state["seen"].values())
        log(f"Klaar. {total} bestaande advertenties opgeslagen.")
        notify(cfg, "Huurbot staat aan", f"Baseline: {total} advertenties.", urgent=False)
        return

    if not STATE_FILE.exists():
        log("Geen state.json - eerst baseline bouwen.")
        run_round(cfg, sources, state, announce=False)

    heartbeat_hours = int(cfg.get("heartbeat_hours", 12))
    last_heartbeat = time.time()

    log(f"Draait over {len(sources)} bronnen.")

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
            log(f"Onverwachte fout: {e}")

        if heartbeat_hours and time.time() - last_heartbeat > heartbeat_hours * 3600:
            total = sum(len(v) for v in state["seen"].values())
            notify(cfg, "Huurbot leeft nog", f"Actief, {total} advertenties in de gaten.", urgent=False)
            last_heartbeat = time.time()

        interval = current_interval(cfg)
        time.sleep(max(20, interval + random.uniform(-10, 15)))


if __name__ == "__main__":
    main()
