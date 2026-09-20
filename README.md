# Huurbot v2 — Tiel e.o.

Twee programma's die samenwerken:

| | wat het doet | dekt |
|---|---|---|
| **huurbot.py** | pollt makelaarssites elke 60 sec | lokale kantoren |
| **mailbrug.py** | leest je alertmails en pusht ze direct | Woonbot, Pararius, Funda |

Samen zit je op élk kanaal als eerste.

Waarom dit werkt: de aggregators (Woonbot, Pararius) hebben zelf een vertraging van
tientallen minuten tot uren voordat ze een nieuwe advertentie oppikken. Een lokale
makelaarssite zet 'm direct online. Door de bron zelf te pollen zit je er als eerste bij.

---

## Stap 1 — Installeren

**Draai je op Railway? Sla deze stap over.** Railway installeert Python en
alle pakketten zelf in de cloud. Je hoeft op je eigen computer niets te
installeren; je uploadt alleen bestanden naar GitHub via de website.

Alleen als je het op je eigen computer wilt draaien heb je Python 3 nodig:

```bash
cd huurbot
pip install requests beautifulsoup4 pyyaml
```

## Stap 2 — Telegram-bot aanmaken (5 min)

1. Open Telegram, zoek **@BotFather**
2. Stuur `/newbot`, kies een naam. Je krijgt een **token** (`123456:ABC-DEF...`)
3. Zoek je eigen nieuwe bot op en stuur 'm `/start` — anders mag hij je niks sturen
4. Chat-ID ophalen: open in je browser
   `https://api.telegram.org/bot<JOUW_TOKEN>/getUpdates`
   en zoek naar `"chat":{"id":123456789`

Zet Linda erbij: maak een Telegram-groep, voeg de bot toe, en gebruik het
groeps-ID (begint met een `-`). Dan krijgen jullie allebei de melding.

Maak nu een bestand `.env` naast `huurbot.py`:

```
TELEGRAM_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
```

**Zet in Telegram de melding voor deze chat op geluid + niet dempen.** Anders mis je 'm 's nachts.

Alternatief zonder account: zet in plaats daarvan `NTFY_TOPIC=iets-willekeurigs-abc123`
en installeer de gratis ntfy-app. Werkt ook, minder betrouwbaar dan Telegram.

## Stap 3 — Controleren of de bronnen werken

```bash
python3 huurbot.py --test
```

Je ziet per site wat er gevonden wordt. Krijg je bij een bron `0 gevonden`, dan klopt
het `link_pattern` niet meer — zie stap 5.

## Stap 4 — Aanzetten

```bash
python3 huurbot.py --init    # slaat het huidige aanbod op, meldt niks
python3 huurbot.py           # blijft draaien, meldt alleen wat nieuw is
```

Laat dit draaien op iets dat **altijd aan staat**. Een laptop die dichtklapt is nutteloos.
Opties:
- Oude laptop of Raspberry Pi thuis (gratis)
- VPS bij Hetzner/TransIP, ~€4/mnd — betrouwbaarst

Op een server met systemd, zodat hij herstart na een crash of reboot:

```ini
# /etc/systemd/system/huurbot.service
[Unit]
Description=Huurbot
After=network.target

[Service]
Type=simple
User=jouwgebruiker
WorkingDirectory=/home/jouwgebruiker/huurbot
ExecStart=/usr/bin/python3 /home/jouwgebruiker/huurbot/huurbot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now huurbot
```

Voor de mailbrug maak je hetzelfde bestand aan als `huurbot-mail.service`, met
`ExecStart=/usr/bin/python3 /home/jouwgebruiker/huurbot/mailbrug.py`.

## Stap 5 — Zelf een makelaar toevoegen

Dit is het belangrijkste onderhoud. Elke extra lokale makelaar = meer kans.

1. Open de huurpagina van het kantoor
2. Klik op een losse woning, kijk naar de URL, bijvoorbeeld
   `https://makelaarx.nl/aanbod/woningaanbod/tiel/huis-5204623-Binnenweg-26/`
3. Zoek het stukje dat bij álle advertenties hetzelfde is: hier `/aanbod/woningaanbod/.+/huis-`
4. Zet dat in `sources.yaml` als `link_pattern`
5. `python3 huurbot.py --test` om te controleren

Let op: veel Nederlandse makelaars gebruiken dezelfde softwareleverancier, dus het
patroon `/aanbod/woningaanbod/.+/huis-` werkt vaak meteen.

---

### exclude_text: filteren op de tekst van de link

Sommige sites zetten "Verhuurd" alleen in de zichtbare tekst en niet in het
webadres. Dan werkt `exclude` (dat kijkt naar het adres) niet. Gebruik dan
`exclude_text`, dat naar de linktekst kijkt:

```yaml
exclude_text: ["verhuurd", "verkocht", "onder bod", "k.k.", "v.o.n."]
```

`k.k.` en `v.o.n.` houden koopwoningen buiten de deur op sites waar koop en
huur door elkaar staan.

### Als een site 0 woningen geeft terwijl er wel aanbod is

Waarschijnlijk laadt die site zijn woningen via JavaScript. De bot haalt alleen
de kale HTML op en ziet dan niks. Dit is niet op te lossen met een ander
`link_pattern`. Zo controleer je het: open de pagina in je browser, klik
rechts en kies "paginabron weergeven". Zoek daarin (Ctrl+F) naar een
straatnaam die je op de pagina ziet staan. Staat die er niet in, dan is het
een JavaScript-site.

Oplossing: zet die bron uit en meld je aan voor hun mailalert. De mailbrug
vangt hem dan alsnog op.


## Stap 6 — De mailbrug aanzetten (dit is de grootste winst)

Je betaalt al voor Woonbot. Die mailt je bij nieuw aanbod. Maar een mail zie je
pas als je toevallig kijkt — en dan is de woning al 40 reacties verder.

`mailbrug.py` checkt elke 20 seconden je mailbox. Komt er een alertmail binnen van
Woonbot, Pararius of Funda? Dan haalt hij de woninglinks eruit en stuurt ze meteen
als pushmelding. Je alertmails worden zo net zo snel als je eigen bot.

Dit is ook precies hoe je Funda en Pararius dekt zónder te scrapen: je leest gewoon
je eigen mail.

**Instellen:**

1. Gmail: maak een **app-wachtwoord** aan op `myaccount.google.com/apppasswords`
   (je gewone wachtwoord werkt niet)
2. Zet in `.env`:
   ```
   IMAP_HOST=imap.gmail.com
   IMAP_USER=jouwmail@gmail.com
   IMAP_PASSWORD=abcd efgh ijkl mnop
   ```
3. Zorg dat je alertmails in je **inbox** komen, niet in een map of promoties-tab
4. Testen: `python3 mailbrug.py --test`
5. Aanzetten: `python3 mailbrug.py --init` en daarna `python3 mailbrug.py`

De bot laat je mails **ongelezen**, dus je ziet ze zelf ook nog gewoon.

Staat jouw makelaar er niet bij? Zet zijn domein in de lijst `ALERT_SENDERS`
bovenin `mailbrug.py`.

---

## Wat er in v2 bij is gekomen

**Slim tijdschema.** Tussen 7 en 21 uur elke minuut, 's nachts elke 10 minuten.
Woningen gaan vrijwel altijd overdag online, dus je verliest niks en belast de
sites minder.

**Prijs en kamers in de melding.** Bij een nieuwe woning haalt de bot de
detailpagina op en zet de huurprijs en het aantal kamers in het bericht. Je ziet
in één blik of het de moeite is, zonder de link te openen.

**Filter.** In `sources.yaml` staat nu:
```yaml
filter:
  enabled: true
  max_price: 1650
  min_price: 600
  min_rooms: 3
```
Alles daarbuiten wordt overgeslagen. Belangrijk: **bij twijfel meldt hij altijd.**
Kan hij de prijs niet vinden? Melden. Staat er "3 kamers" in plaats van
"3 slaapkamers"? Melden, want dat kan 2 slaapkamers + woonkamer zijn.
Een melding te veel kost je 30 seconden; een gemiste woning kost je een huis.

---

---

# Draaien op Railway (aanbevolen)

Railway houdt het 24/7 draaiend en herstart automatisch. Je hebt maar **een service**
nodig: `start.py` draait huurbot en mailbrug samen in een proces.

## A. Naar GitHub

Maak op github.com een **prive** repository (belangrijk: prive, niet publiek).
Dan lokaal in de map met deze bestanden:

```bash
git init
git add .
git commit -m "huurbot"
git branch -M main
git remote add origin https://github.com/JOUWNAAM/huurbot.git
git push -u origin main
```

`.gitignore` zorgt dat `.env` **niet** meegaat. Controleer dat ook echt even:
je wachtwoorden horen niet op GitHub. Ga naar je repo op github.com en kijk of
`.env` er niet tussen staat.

## B. Railway-project

1. Railway → **New Project** → **Deploy from GitHub repo** → kies je repo
2. Railway ziet `requirements.txt` en installeert alles vanzelf
3. `railway.json` zet de startopdracht al goed op `python3 start.py`

**Belangrijk:** dit is een *worker*, geen website. Railway kan proberen een
poort te vinden en dan klagen dat er niks luistert. Dat is prima — negeer het.
Zet geen domein of healthcheck aan.

## C. Variabelen instellen

Railway → je service → **Variables**. Voeg toe (dit vervangt je `.env`):

```
TELEGRAM_TOKEN      = 123456:AAxxxxxxxx
TELEGRAM_CHAT_ID    = 123456789
DATA_DIR            = /data
IMAP_HOST           = imap.gmail.com
IMAP_USER           = jouwmail@gmail.com
IMAP_PASSWORD       = abcd efgh ijkl mnop
MAILBRUG_ENABLED    = true
```

Wil je de mailbrug (nog) niet: zet `MAILBRUG_ENABLED=false` en laat de IMAP-regels weg.

## D. Volume aankoppelen (niet overslaan)

Zonder dit vergeet de bot bij elke redeploy wat hij al gezien heeft.

Railway → je service → **Settings** → **Volumes** → **New Volume**
→ mount path: `/data`

Dat moet overeenkomen met de `DATA_DIR=/data` die je net instelde.

Gebeurt dit niet, dan is het niet dramatisch: bij een lege state bouwt de bot
eerst stilletjes een nieuwe baseline en spamt hij je dus niet vol. Je mist alleen
even je geschiedenis.

## E. Controleren of het loopt

Railway → **Deployments** → **View Logs**. Je hoort te zien:

```
[start] DATA_DIR = /data
[start] huurbot gestart
[start] mailbrug gestart
[start] geen state gevonden - eerst baseline bouwen (meldt niks)
2026-01-01 12:00:00  [Witte Makelaars - huur] 3 advertenties, 3 nieuw
[start] baseline klaar: 6 bestaande advertenties
```

Staat er bij elke bron `0 advertenties` of `403 Forbidden`? Zie hieronder.

## F. Als een site Railway blokkeert

Railway draait in een datacenter. Sommige sites weren datacenter-IP's. Je ziet dan
`403 Forbidden` in de logs terwijl de site vanaf je laptop prima werkt.

Je ziet dit terug in het bronnenrapport dat bij elke start in de logs komt:

- **FOUT ... 403** bij alle bronnen van een site -> die site weert Railway.
  Zet die bron op `enabled: false`. Wil je hem toch, dan moet hij thuis
  draaien op een computer die altijd aan staat.
- **LEEG ... 0 woningen** -> het `link_pattern` klopt niet, of de site laadt
  zijn woningen via JavaScript. Zie stap 5.

De mailbrug heeft hier geen last van: die praat alleen met je mailserver.

De mailbrug heeft hier geen last van: die praat alleen met je mailserver.

## G. Kosten

Dit is een klein programma dat vooral zit te wachten. Reken op een paar euro per
maand aan Railway-verbruik, plus wat het volume kost. Ruim binnen een gewoon
Hobby-abonnement.

## H. Iets aanpassen

Nieuwe makelaar toevoegen of je budget wijzigen? Pas `sources.yaml` aan, dan:

```bash
git add sources.yaml
git commit -m "makelaar erbij"
git push
```

Railway pakt de wijziging automatisch op en herstart. Met een volume blijft je
geschiedenis bewaard, dus je krijgt geen stortvloed aan oude meldingen.


## Belangrijk

**Alleen melden, niet automatisch reageren.** De bot stuurt geen formulieren in. Dat is
bewust: automatische reacties zijn herkenbaar voor makelaars, leveren een ban op, en
kosten je precies de goodwill die je nodig hebt. De winst zit in de melding — jij
reageert zelf, binnen 2 minuten, met een persoonlijk bericht.

**Wees netjes.** 2 minuten interval op een handvol pagina's is verwaarloosbaar verkeer.
Zet het niet op 10 seconden — dan word je geblokkeerd en heb je niks meer.

**Funda en Pararius staan er bewust niet in.** Die hebben actieve anti-botmaatregelen
(Cloudflare, ratelimiting) en scrapen is in strijd met hun voorwaarden. Gebruik daar
gewoon hun eigen gratis e-mailalert voor. Je edge zit bij de lokale kantoren die de
aggregators tóch te laat oppikken.

**Controleer wekelijks of hij nog leeft.** Sites veranderen hun HTML. De bot stuurt elke
12 uur een levensteken en klaagt als een bron 5x achter elkaar faalt, maar kijk zelf ook
af en toe in `huurbot.log`.
