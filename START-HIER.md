# START HIER

Lees dit eerst. Kost 2 minuten, bespaart je een uur.

## Wat is dit

Twee programma's die samen zorgen dat jij als eerste weet dat er een
huurwoning online komt.

| | wat het doet |
|---|---|
| **huurbot.py** | kijkt elke minuut op makelaarssites |
| **mailbrug.py** | leest je alertmails en pusht ze meteen naar je telefoon |

## Status op 20 september 2026

Ik heb de sites echt gecontroleerd. Dit is de eerlijke stand:

| bron | status |
|---|---|
| Rick Zeedijk | **werkt**, staat aan |
| Witte Makelaars | **werkt niet** - laadt woningen via JavaScript, bot ziet niks |
| 7 andere makelaars | **onbekend**, staan uit - zelf testen met `--test` |
| mailbrug | **werkt**, maar jij moet je nog aanmelden voor de mailalerts |

Je hoeft niks te installeren. Alles draait op Railway.

## Doe dit in deze volgorde

**1. Meld je aan voor mailalerts (15 min, grootste effect)**

Dit is belangrijker dan de bot zelf. Elke makelaar heeft een gratis
mailalert. Die werkt altijd, ook bij JavaScript-sites, en breekt nooit.

- Rick Zeedijk: https://www.rickzeedijk.nl/jouw-droomhuis-in-je-inbox/
- Witte Makelaars: via hun site, plus kijk op
  https://www.wittemakelaars.nl/binnenkort-te-huur-tiel/
- Pararius, Funda, Huurwoningen.nl: gratis zoekprofiel met mailalert
- Woonbot: heb je al

Gebruik voor alle alerts hetzelfde mailadres. Dat wordt straks het
adres dat mailbrug.py uitleest.

**2. Vul het acceptatieformulier van Rick Zeedijk alvast in (20 min)**

https://www.rickzeedijk.nl/wp-content/uploads/2022/01/Acceptatieformulier-huurwoningen.pdf

Zij nemen je aanvraag pas in behandeling als dit formulier met bijlagen
binnen is. Vul het nu in, met alle bijlagen erbij, en zet het klaar in
een map. Anders ben je je voorsprong kwijt op het moment dat het telt.

**3. Zet de bot op Railway (30 min)**

Je hoeft NIETS te installeren op je eigen computer. Geen Python, geen git.
Railway doet dat allemaal in de cloud. Jij uploadt alleen bestanden naar
GitHub via de website.

Zie README.md, hoofdstuk "Draaien op Railway".

**4. Test de uitgeschakelde makelaars (mag later)**

Ook dit kan zonder Python op je computer. Bij elke start zet de bot in de
Railway-logs een overzicht:

```
BRONNENCONTROLE
  WERKT   Rick Zeedijk - huur: 2 woningen
            - Nieuw Tiel - Lingedijk 8 EUR 1.450 p/m 4 kamers
  LEEG    Van Beusichem: 0 woningen gevonden
            -> patroon klopt niet, of de site gebruikt JavaScript
```

Zo werkt het:
1. Zet in `sources.yaml` op github.com een makelaar op `enabled: true`
2. Commit. Railway herstart vanzelf.
3. Kijk in de Railway-logs naar het rapport.
4. Staat er WERKT? Laten staan. Staat er LEEG of FOUT? Weer op `false`.

Wil je alleen testen zonder dat er meldingen uitgaan: zet in Railway de
variabele `TEST_MODE` op `true`. De bot controleert dan alleen de bronnen
en stuurt niks. Vergeet hem daarna weer op `false` te zetten.

## Verwachting

Ik ben eerlijk: in Tiel werden het afgelopen jaar ongeveer 44 woningen
verhuurd. Bij Rick Zeedijk stonden op het moment van controle 48
huurwoningen, waarvan er 0 beschikbaar waren. Zo krap is het.

De bot maakt je sneller. Meer aanbod maakt hij niet. Breder zoeken
(Geldermalsen, Culemborg, Zaltbommel, Buren) en rechtstreeks contact met
makelaars doen meer voor je kans dan welke code dan ook.
