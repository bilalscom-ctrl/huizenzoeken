#!/usr/bin/env python3
"""
start.py - Draait huurbot en mailbrug samen in EEN proces.

Zo heb je op Railway maar een service nodig in plaats van twee.
Valt een van de twee om, dan start dit script hem opnieuw op zonder
de ander te storen.

Zet MAILBRUG_ENABLED=false in je omgevingsvariabelen als je alleen
de website-bot wilt draaien.
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def banner(msg):
    print(f"[start] {msg}", flush=True)


def supervise(name, target, cooldown=60):
    """Draait target(); crasht die, dan na cooldown opnieuw. Voor altijd."""
    while True:
        try:
            banner(f"{name} gestart")
            target()
            banner(f"{name} is netjes gestopt - herstart over {cooldown}s")
        except Exception:  # noqa: BLE001
            banner(f"{name} gecrasht:\n{traceback.format_exc()}")
            banner(f"herstart {name} over {cooldown}s")
        time.sleep(cooldown)


def bronnen_rapport(huurbot, sources):
    """
    Draait een controle over alle bronnen en zet het resultaat in de logs.
    Zo kun je vanuit het Railway-dashboard zien of een makelaar werkt,
    zonder dat je Python op je eigen computer nodig hebt.
    """
    banner("=" * 52)
    banner("BRONNENCONTROLE")
    banner("=" * 52)
    stuk = 0
    for source in sources:
        naam = source["name"]
        try:
            html = huurbot.fetch(source["url"])
            gevonden = huurbot.extract_listings(html, source)
        except Exception as e:  # noqa: BLE001
            banner(f"  FOUT    {naam}: {e}")
            stuk += 1
            continue

        if gevonden:
            banner(f"  WERKT   {naam}: {len(gevonden)} woningen")
            for label in list(gevonden.values())[:3]:
                banner(f"            - {label[:60]}")
        else:
            banner(f"  LEEG    {naam}: 0 woningen gevonden")
            banner("            -> patroon klopt niet, of de site gebruikt JavaScript")
            stuk += 1
        time.sleep(1.5)

    banner("=" * 52)
    if stuk:
        banner(f"{stuk} van de {len(sources)} bronnen geeft niks. Zie README stap 5.")
    else:
        banner("Alle bronnen werken.")
    banner("=" * 52)


def run_huurbot():
    import huurbot

    huurbot.load_env()
    cfg, sources = huurbot.load_sources()

    # TEST_MODE=true: alleen controleren, niks melden, niks onthouden
    if os.environ.get("TEST_MODE", "").lower() in ("true", "1", "yes"):
        banner("TEST_MODE staat aan - er worden GEEN meldingen verstuurd")
        bronnen_rapport(huurbot, sources)
        banner("Klaar. Zet TEST_MODE weer op false om echt te gaan draaien.")
        while True:
            time.sleep(3600)

    state = huurbot.load_state()
    bronnen_rapport(huurbot, sources)

    if not huurbot.STATE_FILE.exists():
        banner("geen state gevonden - eerst baseline bouwen (meldt niks)")
        huurbot.run_round(cfg, sources, state, announce=False)
        total = sum(len(v) for v in state["seen"].values())
        banner(f"baseline klaar: {total} bestaande advertenties")

    heartbeat_hours = int(cfg.get("heartbeat_hours", 12))
    last_heartbeat = time.time()

    while True:
        try:
            huurbot.run_round(cfg, sources, state)
        except Exception as e:  # noqa: BLE001
            huurbot.log(f"ronde mislukt: {e}")

        if heartbeat_hours and time.time() - last_heartbeat > heartbeat_hours * 3600:
            total = sum(len(v) for v in state["seen"].values())
            huurbot.notify(
                cfg, "Huurbot leeft nog", f"Actief, {total} advertenties in de gaten.", urgent=False
            )
            last_heartbeat = time.time()

        import random

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

    first_run = not mailbrug.STATE_FILE.exists()
    conn = None

    while True:
        try:
            if conn is None:
                conn = mailbrug.connect()
                mailbrug.log("verbonden met mailbox")
                if first_run:
                    # eerste keer: onthouden wat er al is, niet melden
                    mailbrug.check_once(conn, seen, cfg, announce=False, limit=50)
                    mailbrug.log(f"baseline: {len(seen)} links onthouden")
                    first_run = False
            mailbrug.check_once(conn, seen, cfg)
        except Exception as e:  # noqa: BLE001
            mailbrug.log(f"fout ({e}) - opnieuw verbinden over 60s")
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

    threads = [
        threading.Thread(target=supervise, args=("huurbot", run_huurbot), daemon=True)
    ]

    if os.environ.get("MAILBRUG_ENABLED", "true").lower() not in ("false", "0", "no"):
        threads.append(
            threading.Thread(target=supervise, args=("mailbrug", run_mailbrug), daemon=True)
        )
    else:
        banner("mailbrug staat uit (MAILBRUG_ENABLED=false)")

    for t in threads:
        t.start()

    # hoofdthread levend houden
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        banner("gestopt")


if __name__ == "__main__":
    main()
