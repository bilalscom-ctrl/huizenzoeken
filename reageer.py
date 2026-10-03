#!/usr/bin/env python3
"""
reageer.py - Reageren op een woning met EEN tik in Telegram.

Hoe het werkt:
  1. Er komt een woning binnen. De bot zoekt op de advertentiepagina
     het mailadres van de makelaar.
  2. Je krijgt in Telegram een knop "Verstuur reactie".
  3. Tik je die aan, dan mailt de bot jouw brief naar de makelaar,
     met je dossierlink erbij. Jij staat in de afzender, dus een
     antwoord komt gewoon bij jou binnen.

Waarom een knop en niet automatisch:
  - Je reageert nooit per ongeluk op een woning die je niet wilt
  - Je ziet eerst prijs, plaats en kamers
  - Een mens drukt op verzenden; dat is ook wat makelaars verwachten

Wat dit NIET doet: inloggen op portalen, webformulieren invullen of
DigiD-stappen doorlopen. Dat kan niet betrouwbaar en hoort niet
geautomatiseerd te worden.
"""

import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    DATA_DIR = HERE
WACHTRIJ = DATA_DIR / "reacties.json"
LOG_FILE = DATA_DIR / "reageer.log"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Adressen die nooit de makelaar zijn
GEEN_MAKELAAR = re.compile(
    r"(noreply|no-reply|donotreply|privacy|webmaster|postmaster|abuse|"
    r"security|support@(google|apple|microsoft)|sentry|wordpress|example\.|"
    r"@sentry|@wixpress|\.png|\.jpg)",
    re.I,
)


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  [reageer] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- wachtrij

def laad_wachtrij():
    if WACHTRIJ.exists():
        try:
            return json.loads(WACHTRIJ.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"items": {}, "offset": 0}


def bewaar_wachtrij(w):
    # niet eindeloos laten groeien
    items = w.get("items", {})
    if len(items) > 200:
        oud = sorted(items.items(), key=lambda kv: kv[1].get("tijd", ""))
        for k, _ in oud[: len(items) - 200]:
            items.pop(k, None)
    try:
        tmp = WACHTRIJ.with_suffix(".tmp")
        tmp.write_text(json.dumps(w, indent=1), encoding="utf-8")
        tmp.replace(WACHTRIJ)
    except OSError as e:
        log(f"wachtrij opslaan mislukt: {e}")


# ---------------------------------------------------------------- mailadres

def vind_makelaar_email(html, url=""):
    """
    Zoekt het mailadres van de makelaar op de advertentiepagina.
    Eerst mailto-links (meest betrouwbaar), dan losse adressen in de tekst.
    Geeft None als er niks bruikbaars staat.
    """
    if not html:
        return None

    kandidaten = []

    for m in re.finditer(r'mailto:([^"\'>?\s]+)', html, re.I):
        kandidaten.append(m.group(1).strip())

    for m in EMAIL_RE.finditer(html):
        kandidaten.append(m.group(0))

    site = urlparse(url).netloc.replace("www.", "").lower() if url else ""

    schoon = []
    for adres in kandidaten:
        adres = adres.strip().strip(".,;:")
        if not EMAIL_RE.fullmatch(adres) or GEEN_MAKELAAR.search(adres):
            continue
        if adres.lower() in schoon:
            continue
        schoon.append(adres.lower())

    if not schoon:
        return None

    # Voorkeur 1: adres op hetzelfde domein als de advertentie
    if site:
        kern = site.split(".")[0]
        for a in schoon:
            if kern and kern in a.split("@")[-1]:
                return a

    # Voorkeur 2: typische verhuuradressen
    for voorkeur in ("huren@", "verhuur@", "wonen@", "info@", "contact@"):
        for a in schoon:
            if a.startswith(voorkeur):
                return a

    return schoon[0]


def zoek_email_bij_url(url, haal_op=None):
    """
    Haalt de advertentiepagina op en zoekt het mailadres van de makelaar.
    haal_op: functie die de html teruggeeft (zodat we huurbot's nette
    fetch met rate limiting kunnen gebruiken).
    """
    if not url:
        return None
    try:
        if haal_op:
            html = haal_op(url)
        else:
            rr = requests.get(url, timeout=15,
                              headers={"User-Agent": "Mozilla/5.0"})
            html = rr.text if rr.ok else None
    except Exception as e:  # noqa: BLE001
        log(f"pagina ophalen mislukt voor mailadres ({type(e).__name__})")
        return None
    return vind_makelaar_email(html, url)


# ---------------------------------------------------------------- versturen

def verstuur_mail(cfg, naar, onderwerp, tekst):
    """Verstuurt via Gmail SMTP met hetzelfde app-wachtwoord als de mailbrug."""
    gebruiker = os.environ.get("SMTP_USER") or os.environ.get("IMAP_USER")
    wachtwoord = os.environ.get("SMTP_PASSWORD") or os.environ.get("IMAP_PASSWORD")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    poort = int(os.environ.get("SMTP_PORT", "587"))

    if not gebruiker or not wachtwoord:
        return False, "SMTP_USER/SMTP_PASSWORD (of IMAP_*) niet ingesteld"

    brief_cfg = cfg.get("brief", {}) or {}
    naam = brief_cfg.get("namen") or gebruiker
    antwoord_naar = brief_cfg.get("email") or gebruiker

    msg = EmailMessage()
    msg["From"] = formataddr((naam, gebruiker))
    msg["To"] = naar
    msg["Subject"] = onderwerp
    if antwoord_naar and antwoord_naar != gebruiker:
        msg["Reply-To"] = antwoord_naar
    msg.set_content(tekst)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, poort, timeout=25) as s:
            s.starttls(context=ctx)
            s.login(gebruiker, wachtwoord)
            s.send_message(msg)
        return True, "verstuurd"
    except smtplib.SMTPAuthenticationError:
        return False, "inloggen bij de mailserver mislukt (app-wachtwoord?)"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- telegram

def _api(methode, payload, timeout=20):
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{methode}",
                          json=payload, timeout=timeout)
        if r.ok:
            return r.json()
        log(f"telegram {methode} fout {r.status_code}: {r.text[:160]}")
    except requests.RequestException as e:
        log(f"telegram {methode} fout: {e}")
    return None


def zet_klaar(cfg, woning):
    """
    Zet een woning in de wachtrij en geeft de knoppen terug voor de melding.
    woning: dict met url, adres, prijs, bron, makelaar_email, brief
    """
    w = laad_wachtrij()
    sleutel = str(abs(hash(woning["url"])) % (10 ** 9))
    woning["tijd"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    woning["verstuurd"] = False
    w["items"][sleutel] = woning
    bewaar_wachtrij(w)

    knoppen = [{"text": "Bekijk woning", "url": woning["url"]}]
    if woning.get("makelaar_email"):
        knoppen.append({"text": "Verstuur reactie",
                        "callback_data": f"r:{sleutel}"})
    dossier = (cfg.get("brief", {}) or {}).get("dossier_url")
    if dossier and dossier.startswith("http"):
        knoppen.append({"text": "Mijn dossier", "url": dossier})
    return knoppen


def verwerk_knoppen(cfg):
    """
    Haalt knopdrukken op uit Telegram en verstuurt de reactie.
    Wordt elke paar seconden aangeroepen.
    """
    w = laad_wachtrij()
    res = _api("getUpdates", {"offset": w.get("offset", 0),
                              "timeout": 0,
                              "allowed_updates": ["callback_query"]})
    if not res or not res.get("ok"):
        return 0

    verwerkt = 0
    for update in res.get("result", []):
        w["offset"] = update["update_id"] + 1
        cq = update.get("callback_query")
        if not cq:
            continue

        data = cq.get("data", "")
        cq_id = cq.get("id")
        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")

        if not data.startswith("r:"):
            _api("answerCallbackQuery", {"callback_query_id": cq_id})
            continue

        sleutel = data[2:]
        woning = w["items"].get(sleutel)

        if not woning:
            _api("answerCallbackQuery", {"callback_query_id": cq_id,
                                         "text": "Deze woning ken ik niet meer.",
                                         "show_alert": True})
            continue

        if woning.get("verstuurd"):
            _api("answerCallbackQuery", {"callback_query_id": cq_id,
                                         "text": "Al verstuurd.",
                                         "show_alert": True})
            continue

        _api("answerCallbackQuery", {"callback_query_id": cq_id,
                                     "text": "Bezig met versturen..."})

        adres = woning.get("adres") or "uw woning"
        onderwerp = f"Bezichtiging aanvraag: {adres}"
        tekst = woning.get("brief") or ""
        if woning.get("url"):
            tekst += f"\n\nAdvertentie: {woning['url']}"

        ok, melding = verstuur_mail(cfg, woning["makelaar_email"], onderwerp, tekst)
        woning["verstuurd"] = ok
        woning["resultaat"] = melding
        verwerkt += 1

        if ok:
            log(f"reactie verstuurd naar {woning['makelaar_email']} voor {adres}")
            bericht = (f"Reactie verstuurd\n\n{adres}\n"
                       f"naar: {woning['makelaar_email']}\n\n"
                       "Een antwoord komt in je mailbox.")
        else:
            log(f"versturen mislukt ({melding}) voor {adres}")
            bericht = (f"Versturen MISLUKT\n\n{adres}\n"
                       f"naar: {woning['makelaar_email']}\n"
                       f"reden: {melding}\n\n"
                       "Reageer zelf even via de link.")

        if chat_id:
            _api("sendMessage", {"chat_id": chat_id, "text": bericht,
                                 "disable_web_page_preview": True})

    bewaar_wachtrij(w)
    return verwerkt
