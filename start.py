#!/usr/bin/env python3
"""
start.py v3 - Draait huurbot en mailbrug samen in EEN proces.

Zo heb je op Railway maar een service nodig. Valt een van de twee om,
dan start dit script hem opnieuw op zonder de ander te storen.

Omgevingsvariabelen:
  TEST_MODE=true          alleen bronnen controleren, niks melden
  MAILBRUG_ENABLED=false  alleen de website-bot draaien
"""

import os
import random
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def banner(msg):
    print(f"[start] {msg}", flush=True)


def supervise(naam, functie, cooldown=60):
    """Draait functie(); crasht die, dan na cooldown opnieuw. Voor altijd."""
    while True:
        try:
            banner(f"{naam} gestart")
            functie()
            banner(f"{naam} is gestopt - herstart over {cooldown}s")
        except Exception:  # noqa: BLE001
            banner(f"{naam} gecrasht:\n{traceback.format_exc()}")
            banner(f"herstart {naam} over {cooldown}s")
        time.sleep(cooldown)


def bronnen_rapport(huurbot, sources, cfg=None):
    """
    Controleert elke bron en zet het resultaat in de logs. Zo zie je in het
    Railway-dashboard of een site werkt, zonder Python op je eigen computer.
    """
    banner("=" * 56)
    banner("BRONNENCONTROLE")
    banner("=" * 56)
    stuk = 0
    vrij = 0
    min_gap = float(cfg.get("min_seconds_per_site", 8)) if cfg else 8.0

    for source in sources:
        naam = source["name"]
        try:
            html, _ = huurbot.fetch(source["url"], min_gap=min_gap)
        except huurbot.Geblokkeerd as e:
            banner(f"  GEBLOKKEERD  {naam}: {e}")
            banner("                 -> site weert Railway. Zet deze bron uit.")
            stuk += 1
            continue
        except Exception as e:  # noqa: BLE001
            banner(f"  FOUT         {naam}: {type(e).__name__}")
            banner("                 -> URL klopt niet of site onbereikbaar")
            stuk += 1
            continue

        if html is None:
            banner(f"  OK           {naam}: onveranderd sinds vorige keer")
            continue

        try:
            # rauw = alles wat op het patroon past, nog zonder exclude_text
            rauw_bron = {k: v for k, v in source.items()
                         if k not in ("exclude_text", "require_text")}
            rauw = huurbot.extract_listings(html, rauw_bron)
            gevonden = huurbot.extract_listings(html, source)
        except Exception as e:  # noqa: BLE001
            banner(f"  FOUT         {naam}: uitlezen mislukt ({type(e).__name__})")
            stuk += 1
            continue

        if gevonden:
            vrij += len(gevonden)
            banner(f"  WERKT        {naam}: {len(gevonden)} beschikbaar "
                   f"(van {len(rauw)} op de pagina)")
            for label in list(gevonden.values())[:2]:
                banner(f"                 - {label[:56]}")
        elif rauw:
            banner(f"  OK           {naam}: {len(rauw)} gezien, 0 beschikbaar")
            banner("                 -> site werkt, alles verhuurd/verkocht")
        else:
            banner(f"  LEEG         {naam}: geen link past op het patroon")
            banner("                 -> patroon klopt niet, of JavaScript-site")
            stuk += 1

    banner("=" * 56)
    if stuk:
        banner(f"{stuk} van de {len(sources)} bronnen werkt niet. Zie README.")
    else:
        banner(f"Alle {len(sources)} bronnen werken. {vrij} woning(en) beschikbaar.")
    banner("=" * 56)

    if cfg is not None:
        werkend = len(sources) - stuk
        tekst = (f"{werkend} van de {len(sources)} bronnen werkt.\n"
                 f"Nu beschikbaar: {vrij} woning(en).\n\n"
                 "Vanaf nu hoor je alleen iets bij een NIEUWE woning.")
        if stuk:
            tekst += f"\n\nLet op: {stuk} bron(nen) werkt niet. Zie de Railway-logs."
        try:
            huurbot.notify(cfg, "Huurbot staat aan", tekst, urgent=False)
        except Exception:  # noqa: BLE001
            banner("startbericht naar Telegram mislukt")


def run_huurbot():
    import huurbot

    huurbot.load_env()
    cfg, sources = huurbot.load_sources()

    if os.environ.get("TEST_MODE", "").lower() in ("true", "1", "yes"):
        banner("TEST_MODE staat aan - er worden GEEN meldingen verstuurd")
        bronnen_rapport(huurbot, sources)
        banner("Klaar. Zet TEST_MODE op false om echt te gaan draaien.")
        while True:
            time.sleep(3600)

    state = huurbot.load_state()
    eerste_keer = not huurbot.STATE_FILE.exists()

    bronnen_rapport(huurbot, sources, cfg)

    if eerste_keer:
        banner("eerste start - huidige aanbod onthouden (meldt niks)")
        huurbot.run_round(cfg, sources, state, announce=False)
        banner(f"klaar: {sum(len(v) for v in state['seen'].values())} onthouden")

    hb_uren = int(cfg.get("heartbeat_hours", 12))
    laatste_hb = time.time()

    while True:
        try:
            huurbot.run_round(cfg, sources, state)
        except Exception as e:  # noqa: BLE001
            huurbot.log(f"ronde mislukt: {type(e).__name__}: {e}")

        if hb_uren and time.time() - laatste_hb > hb_uren * 3600:
            totaal = sum(len(v) for v in state["seen"].values())
            huurbot.notify(cfg, "Huurbot leeft nog",
                           f"Actief, {totaal} advertenties in de gaten.", urgent=False)
            laatste_hb = time.time()

        time.sleep(max(20, huurbot.current_interval(cfg) + random.uniform(-10, 15)))


def run_mailbrug():
    import mailbrug

    mailbrug.load_env()
    if not os.environ.get("IMAP_USER") or not os.environ.get("IMAP_PASSWORD"):
        banner("mailbrug: IMAP_USER/IMAP_PASSWORD niet ingesteld - overgeslagen")
        while True:
            time.sleep(3600)

    cfg = mailbrug.load_cfg()
    seen = mailbrug.load_seen()
    interval = int(cfg.get("mail_interval_seconds", 20))
    eerste = not mailbrug.STATE_FILE.exists()
    conn = None

    while True:
        try:
            if conn is None:
                conn = mailbrug.connect()
                mailbrug.log("verbonden met mailbox")
                if eerste:
                    mailbrug.check_once(conn, seen, cfg, announce=False, limit=50)
                    mailbrug.log(f"baseline: {len(seen)} links onthouden")
                    eerste = False
            mailbrug.check_once(conn, seen, cfg)
        except Exception as e:  # noqa: BLE001
            mailbrug.log(f"fout ({type(e).__name__}) - opnieuw verbinden over 60s")
            try:
                if conn:
                    conn.logout()
            except Exception:  # noqa: BLE001
                pass
            conn = None
            time.sleep(60)
            continue
        time.sleep(interval)


def main():
    banner(f"DATA_DIR = {os.environ.get('DATA_DIR', HERE)}")

    threads = [threading.Thread(target=supervise, args=("huurbot", run_huurbot),
                                daemon=True)]
    if os.environ.get("MAILBRUG_ENABLED", "true").lower() not in ("false", "0", "no"):
        threads.append(threading.Thread(target=supervise,
                                        args=("mailbrug", run_mailbrug), daemon=True))
    else:
        banner("mailbrug staat uit (MAILBRUG_ENABLED=false)")

    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        banner("gestopt")


if __name__ == "__main__":
    main()
