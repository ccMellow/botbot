# Trading Bot Project

## Prosjektoversikt
Python trading bot for Binance Spot Testnet.
Bygget for å lære automatisert krypto-trading med lav risiko.
Skal eventuelt flyttes til live Binance når testing er fullført.

## Mål
- Automatisk trading basert på tekniske indikatorer
- Grundig logging av alle beslutninger
- HTML/CSS/JS dashboard tilgjengelig via One.com (passordbeskyttet)
- Auto-push av logg og dashboard til GitHub hver time

## Teknisk stack
- Python + python-binance
- Binance Spot Testnet API
- CSV logging (maskinlesbar, kan lastes opp til Claude for analyse)
- Tekstlogg (menneskelig lesbar)
- HTML/CSS/JS dashboard (mobiloptimalisert)
- Git auto-push script (hver time)

## Viktige regler
- Aldri hardkod API-nøkler – alltid bruk .env
- Aldri push .env til GitHub – ligger i .gitignore
- All trading logikk skal inkludere fee-kalkulator (0.10% Binance)
- Boten skal ALDRI handle hvis forventet gevinst ikke overstiger fees
- Logg ALLE beslutninger, ikke bare faktiske handler
- Koden skal være modulær og lett å flytte mellom PC-er

## Filstruktur
- bot/strategy.py        → trading logikk og indikatorer
- bot/logger.py          → CSV og tekstlogging
- bot/github_pusher.py   → auto-push til GitHub hver time
- bot/fee_calculator.py  → fee-logikk og lønnsomhetssjekk
- dashboard/index.html   → hovedside (mobiloptimalisert)
- dashboard/style.css    → styling
- dashboard/charts.js    → grafer og visualisering
- logs/trades.csv        → maskinlesbar logg (last opp til Claude for analyse)
- logs/trades.log        → menneskelig lesbar logg
- requirements.txt       → alle avhengigheter
- .env.example           → mal for API-nøkler
- .env                   → faktiske nøkler (aldri push!)
- .gitignore             → ekskluderer .env og sensitive filer

## Trading strategi
Aktiv strategi defineres i bot/strategy.py
Se koden for gjeldende implementasjon.

### Krav til alle strategier
- Må inkludere fee-kalkulator før hver handel
- Boten skal ALDRI handle hvis forventet gevinst ikke overstiger fees
- Alle beslutninger skal logges med begrunnelse
- Marked: BTC/USDT (Binance Spot Testnet)

## Logging format
Hver handling skal logge:
- Tidspunkt
- Handling (KJØP/SELG/VENTER)
- Pris
- Beløp
- Fee
- Grunn (hvilke indikatorer trigget)
- Resultat (gevinst/tap i % og NOK)

## Dashboard
- Mobiloptimalisert – skal fungere på telefon
- Prisgraf med kjøp/selg-punkter markert
- Total gevinst/tap
- Antall handler
- Vinnrate i prosent
- Gjennomsnittlig fee per handel
- Oppdateres automatisk når ny CSV pushes til GitHub

## GitHub oppsett
- Logg og dashboard pushes til GitHub hver time
- Dashboard hostes på One.com (passordbeskyttet via cPanel)
- One.com henter CSV fra GitHub for å oppdatere dashboard
- Repository skal være privat

## Utvikling
- Utvikles på laptop i VS Code med Claude Code
- Flyttes til stasjonær PC via GitHub når klar for lengre testing
- Stasjonær kjører: git clone + pip install -r requirements.txt
- .env kopieres manuelt mellom PC-er

## Fremtidige muligheter
- Flytte fra testnet til live Binance
- Sende krypto fra Firi til Binance når klar for live trading
- Deploye til Railway eller PythonAnywhere for 24/7 kjøring
- Eventuelt Raspberry Pi for hjemmeserver
- Groq API (llama-3.3-70b-versatile) for automatisk AI-analyse inne i boten

## Instruksjoner for Claude Code

### Ved konflikter med CLAUDE.md
Hvis du blir bedt om å gjøre noe som er i strid med det som står
i denne filen, skal du ALLTID:
1. Påpeke konflikten tydelig
2. Spørre om brukeren ønsker å oppdatere CLAUDE.md
3. Vente på bekreftelse før du går videre
4. Oppdatere CLAUDE.md hvis brukeren bekrefter

### Ved endringer i prosjektet
Hvis en endring påvirker filstruktur, teknisk stack, eller andre
deler av CLAUDE.md, foreslå alltid å oppdatere filen så den
holdes synkronisert med koden.