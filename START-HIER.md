# START HIER - huurbot v3

## Wat is er nieuw

**13 bronnen in plaats van 2.** Alle actieve bronnen zijn op 20-09-2026 echt
gecontroleerd tegen hun werkelijke HTML.

| bron | dekt |
|---|---|
| Huurwoningen.nl | 6 plaatsen, grootste aanbod van Nederland |
| Huislijn.nl | 5 plaatsen, verzamelsite van veel makelaars |
| Rick Zeedijk | lokale makelaar Tiel |

**Kant-en-klare reactiebrief.** Bij elke melding krijg je een tweede
Telegram-bericht met een complete brief, met het adres en de huurprijs er al
in. Lang indrukken, kopieren, plakken, versturen. Klaar in 15 seconden.

**Veel robuuster.** Crasht niet meer bij een kapotte site. Probeert het 3x
opnieuw bij een tijdelijke storing. Herkent een blokkade (403/429) en zet die
bron dan 30 minuten stil in plaats van door te rammen, want doorrammen is
precies hoe je een permanente ban krijgt. Gebruikt ETag, dus een onveranderde
pagina kost geen dataverkeer.

**Beleefd.** Minimaal 8 seconden tussen twee bezoeken aan dezelfde website,
met willekeurige spreiding. Dat is onzichtbaar in hun logboek.

---

## Wat je moet doen

### 1. Vul je gegevens in (5 min, VERPLICHT)

Open `sources.yaml` en zoek het blok `brief:`. Vul in:

```yaml
brief:
  namen: "Bilal en Linda"
  leeftijden: "beiden werkend"      # of "28 en 27 jaar"
  inkomen: "EUR 5.500"
  beschikbaar: "direct"             # of "1 november"
  telefoon: "06-12345678"           # JOUW nummer
  email: "jouwmail@gmail.com"       # JOUW mail
  dossier_url: "https://drive.google.com/..."   # map met je documenten
```

Doe je dit niet, dan staat er `06-XXXXXXXX` in je brief. Dat valt op.

### 2. Zet het op Railway

Vervang op github.com deze bestanden: `huurbot.py`, `start.py`, `sources.yaml`.
Railway herstart vanzelf.

Controleer in de logs dat je dit ziet:

```
WERKT   Huurwoningen.nl Tiel: 4 beschikbaar (van 10 op de pagina)
Alle 13 bronnen werken. 7 woning(en) beschikbaar.
```

Je krijgt ook een Telegram-bericht dat de bot aanstaat.

### 3. Vul het acceptatieformulier van Rick Zeedijk in (20 min)

https://www.rickzeedijk.nl/wp-content/uploads/2022/01/Acceptatieformulier-huurwoningen.pdf

Zij nemen je aanvraag pas in behandeling als dit formulier met bijlagen binnen
is. Vul het nu in, met loonstroken en werkgeversverklaringen erbij, en zet het
klaar in je dossiermap. Anders ben je je voorsprong kwijt op het moment dat het
telt.

### 4. Zet de mailbrug aan (10 min)

Drie variabelen in Railway: `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`.
Gmail heeft een app-wachtwoord nodig: myaccount.google.com/apppasswords

Dit brengt Funda, Pararius, Woonbot en Witte Makelaars ook in je Telegram.
Die kunnen namelijk niet gescrapet worden: Funda en Pararius hebben
anti-botbeveiliging, Witte laadt zijn woningen via JavaScript.

---

## Meer plaatsen toevoegen

Onderaan `sources.yaml` staat hoe. Kort: kopieer een blok, vervang de
plaatsnaam, commit, en kijk in de logs of er WERKT of LEEG staat.

Huurwoningen.nl: `/in/<plaats>/`
Huislijn: `/huurwoning/nederland/<provincie>/<plaats>`

---

## Wat de bot NIET doet

**Automatisch reageren op woningen.** Bewust niet, om drie redenen:

1. Rick Zeedijk wil een ingevuld PDF-formulier met bijlagen. Een bot kan dat
   niet leveren, dus zo'n automatische reactie is per definitie onvolledig en
   gaat direct de prullenbak in.
2. Makelaars herkennen bulkreacties. Een identieke tekst die 40 seconden na
   plaatsing binnenkomt leest als bot, niet als serieuze kandidaat. Je wordt
   dan weggefilterd in plaats van uitgenodigd.
3. Formulieren verschillen per site en hebben captcha's. Een fout betekent dat
   je met je echte gegevens reageert op een woning die je niet wilt.

De kant-en-klare brief geeft je 95% van de tijdwinst zonder die risico's.

---

## Eerlijke verwachting

In Tiel werden vorig jaar ongeveer 44 woningen verhuurd. Op het moment van
bouwen stonden er bij Rick Zeedijk 48 huurwoningen waarvan 0 beschikbaar.

De bot maakt je sneller. Meer aanbod maakt hij niet. Je grootste kansen:

1. Breed zoeken (staat nu al aan: 6 plaatsen)
2. Compleet dossier dat binnen 5 minuten de deur uit kan
3. Makelaars bellen en op hun zoekerslijst komen - daar zit het aanbod dat
   nooit online komt
