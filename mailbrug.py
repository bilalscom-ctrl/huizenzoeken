#!/usr/bin/env python3
"""
mailbrug.py - Zet je alertmails om in een schreeuwende pushmelding.

Het probleem dat dit oplost:
Je betaalt voor Woonbot. Die mailt je zodra er iets nieuws is. Maar een mail
zie je pas als je toevallig kijkt. Dat kan een uur later zijn. Dan is de
woning al 40 reacties verder.

Wat dit doet:
Checkt elke 20 seconden je mailbox. Komt er een alertmail binnen van Woonbot,
Pararius, Funda of een makelaar? Dan haalt hij de woninglinks eruit en stuurt
ze meteen als pushmelding naar dezelfde Telegram-chat als huurbot.py.

Dit dekt precies de bronnen die huurbot.py NIET mag scrapen (Funda, Pararius),
zonder dat je hun voorwaarden overtreedt: het is gewoon je eigen mail lezen.

Gebruik:
    python3 mailbrug.py --test    # toont de laatste alertmails, stuurt niks
    python3 mailbrug.py           # blijft draaien
"""

import argparse
import email
import imaplib
import json
import os
import re
import time
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "mailbrug_state.json"
SOURCES_FILE = HERE / "sources.yaml"
LOG_FILE = DATA_DIR / "mailbrug.log"

# Afzenders die woningalerts sturen. Vul aan met je eigen makelaars.
ALERT_SENDERS = [
    "woonbot", "rentslam", "pararius", "funda", "huurwoningen.nl",
    "kamernet", "huurstunt", "nederwoon", "123wonen", "rentbird",
    "stekkies", "woonbusters", "directwonen", "rotsvast", "huispingt",
    "makelaar", "wittemakelaars", "rickzeedijk", "vanbeusichem",
]

# Links die nooit een woning zijn
JUNK = re.compile(
    r"(unsubscribe|uitschrijv|afmeld|privacy|voorwaarden|facebook\.com|"
    r"twitter\.com|instagram\.com|linkedin\.com|youtube\.com|apple\.com|"
    r"play\.google|mailchimp|list-manage|sendgrid|mailgun|\.png|\.jpg|"
    r"\.gif|\.css|\.js)",
    re.I,
)

# Links die wél op een woning wijzen
LOOKS_LIKE_LISTING = re.compile(
    r"(/huurwoning|/te-huur|/woning|/aanbod|/huis-|/appartement|/listing|"
    r"/property|/huren/|/object)",
    re.I,
)


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_env():
    envfile = HERE / ".env"
    if envfile.exists():
        for raw in envfile.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_cfg():
    if SOURCES_FILE.exists():
        with open(SOURCES_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return set()


def save_seen(seen):
    # laatste 2000 bewaren, anders groeit het bestand eindeloos
    STATE_FILE.write_text(json.dumps(sorted(seen)[-2000:]), encoding="utf-8")


# ---------------------------------------------------------------- mail

def decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return str(value)


def body_of(msg):
    """Pakt de HTML-body, anders platte tekst."""
    html_part, text_part = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_filename():
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="replace")
            except Exception:  # noqa: BLE001
                continue
            if ctype == "text/html" and html_part is None:
                html_part = content
            elif ctype == "text/plain" and text_part is None:
                text_part = content
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace") if payload else ""
        except Exception:  # noqa: BLE001
            content = ""
        if msg.get_content_type() == "text/html":
            html_part = content
        else:
            text_part = content
    return html_part, text_part


def unwrap_tracking(url):
    """
    Alertmails verstoppen de echte link vaak achter een klikteller.
    Zoekt in de querystring naar een ingepakte http-url en pakt die.
    """
    try:
        qs = parse_qs(urlparse(url).query)
    except ValueError:
        return url
    for key in ("url", "u", "target", "redirect", "link", "dest", "r"):
        if key in qs and qs[key]:
            candidate = unquote(qs[key][0])
            if candidate.startswith("http"):
                return candidate
    return url


def extract_links(html, text):
    """Haalt woninglinks uit een alertmail."""
    urls = {}

    if html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.startswith("http"):
                continue
            real = unwrap_tracking(href)
            if JUNK.search(real):
                continue
            label = " ".join(a.get_text(" ", strip=True).split())
            if not LOOKS_LIKE_LISTING.search(real):
                continue
            key = real.split("?")[0].rstrip("/")
            if key not in urls or len(label) > len(urls[key]):
                urls[key] = label[:160]

    if not urls and text:
        for m in re.finditer(r"https?://[^\s<>\"')]+", text):
            real = unwrap_tracking(m.group(0))
            if JUNK.search(real) or not LOOKS_LIKE_LISTING.search(real):
                continue
            urls.setdefault(real.split("?")[0].rstrip("/"), "")

    return urls


def is_alert(sender, subject):
    blob = f"{sender} {subject}".lower()
    if any(s in blob for s in ALERT_SENDERS):
        return True
    # vangnet op onderwerp
    return bool(re.search(
        r"(nieuwe? woning|nieuw aanbod|huurwoning|huurhuis|te huur|match|"
        r"appartement|woningaanbod|zoekopdracht|alert|beschikbaar)", blob))


# ---------------------------------------------------------------- melden

def notify(title, body, url=None):
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
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
            ok = r.ok
            if not r.ok:
                log(f"telegram fout {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log(f"telegram fout: {e}")

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            headers = {"Title": title.encode("utf-8"), "Priority": "urgent", "Tags": "email"}
            if url:
                headers["Click"] = url
            r = requests.post(
                f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, timeout=15
            )
            ok = ok or r.ok
        except requests.RequestException as e:
            log(f"ntfy fout: {e}")

    if not ok:
        log(f"GEEN MELDING -> {title} | {body} | {url}")
    return ok


# ---------------------------------------------------------------- hoofdlus

def connect():
    host = os.environ.get("IMAP_HOST", "imap.gmail.com")
    user = os.environ.get("IMAP_USER")
    password = os.environ.get("IMAP_PASSWORD")
    if not user or not password:
        raise SystemExit(
            "IMAP_USER en IMAP_PASSWORD ontbreken in .env.\n"
            "Gmail: maak een app-wachtwoord aan op myaccount.google.com/apppasswords"
        )
    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)
    conn.select(os.environ.get("IMAP_FOLDER", "INBOX"))
    return conn


def check_once(conn, seen, cfg, announce=True, limit=10):
    """Kijkt naar ongelezen mail en meldt nieuwe woninglinks."""
    # NOOP is essentieel: zonder dit blijft de IMAP-sessie naar de toestand
    # van het moment van verbinden kijken en zie je nieuwe mail NOOIT.
    try:
        conn.noop()
    except Exception as e:  # noqa: BLE001
        raise ConnectionError(f"noop mislukt: {e}") from e

    typ, data = conn.search(None, "UNSEEN")
    if typ != "OK":
        return 0
    ids = data[0].split()
    if not ids:
        return 0

    sent = 0
    for msg_id in ids[-limit:]:
        # BODY.PEEK: laat de mail ongelezen, zodat jij hem zelf nog ziet
        typ, raw = conn.fetch(msg_id, "(BODY.PEEK[])")
        if typ != "OK" or not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])

        sender = decode(msg.get("From"))
        subject = decode(msg.get("Subject"))

        if not is_alert(sender, subject):
            log(f"  overgeslagen (geen alert): {subject[:55]}")
            continue

        html, text = body_of(msg)
        links = extract_links(html, text)
        if not links:
            log(f"  alertmail zonder woninglinks: {subject[:55]}")
            continue

        source_name = re.sub(r".*<|>.*", "", sender).split("@")[-1] or sender

        for url, label in links.items():
            if url in seen:
                continue
            seen.add(url)
            if not announce:
                continue
            sent += 1
            log(f"MAIL-ALERT [{source_name}] {label or url}")
            body = label or subject
            body += f"\n\n{cfg.get('reply_reminder', 'Reageer nu.')}"
            notify(f"Via mail: {source_name}", body, url)

    save_seen(seen)
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="toon wat er gevonden wordt, stuur niks")
    ap.add_argument("--init", action="store_true", help="huidige mails onthouden, niet melden")
    args = ap.parse_args()

    load_env()
    cfg = load_cfg()
    seen = load_seen()

    if args.test:
        conn = connect()
        print("\n=== TESTMODUS - er wordt niks verstuurd ===\n")
        typ, data = conn.search(None, "ALL")
        ids = data[0].split()[-30:]
        hits = 0
        for msg_id in ids:
            typ, raw = conn.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            sender, subject = decode(msg.get("From")), decode(msg.get("Subject"))
            if not is_alert(sender, subject):
                continue
            html, text = body_of(msg)
            links = extract_links(html, text)
            hits += 1
            print(f"--- {subject[:70]}\n    van: {sender[:60]}")
            if links:
                for u, l in list(links.items())[:5]:
                    print(f"      - {l[:70] or '(geen tekst)'}\n        {u}")
            else:
                print("      geen woninglinks herkend")
            print()
        if not hits:
            print("Geen alertmails gevonden in de laatste 30 berichten.")
            print("Check of ALERT_SENDERS bovenin dit bestand jouw afzenders bevat.")
        conn.logout()
        return

    interval = int(cfg.get("mail_interval_seconds", 20))
    log(f"Mailbrug draait, checkt elke {interval}s.")

    if args.init:
        conn = connect()
        check_once(conn, seen, cfg, announce=False, limit=50)
        conn.logout()
        log(f"Baseline: {len(seen)} links onthouden.")
        return

    conn = None
    while True:
        try:
            if conn is None:
                conn = connect()
                log("Verbonden met mailbox.")
            check_once(conn, seen, cfg)
        except KeyboardInterrupt:
            log("Gestopt.")
            if conn:
                try:
                    conn.logout()
                except Exception:  # noqa: BLE001
                    pass
            return
        except Exception as e:  # noqa: BLE001
            log(f"Fout ({e}) - opnieuw verbinden over 60s")
            try:
                if conn:
                    conn.logout()
            except Exception:  # noqa: BLE001
                pass
            conn = None
            time.sleep(60)
            continue
        time.sleep(interval)


if __name__ == "__main__":
    main()
