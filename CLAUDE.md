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
- config.yaml            → ALLE parametre: mynter, strategi, trading, sikkerhet, system
- bot/config_loader.py   → laster og cacher config.yaml
- bot/strategy.py        → trading logikk, 5 strategier, ordre-sending
- bot/logger.py          → CSV og tekstlogging
- bot/github_pusher.py   → auto-push til GitHub hvert 30. minutt
- bot/fee_calculator.py  → fee-logikk og lønnsomhetssjekk
- bot/circuit_breaker.py  → circuit breaker (stopper trading ved >N% tap på M timer)
- bot/startup_checks.py  → oppstartskontroller og config-validering
- bot/state_manager.py   → lagrer/gjenoppretter CoinState til/fra state.json
- bot/status_writer.py   → skriver dashboard/status.json med posisjoner og saldo
- dashboard/index.html   → hovedside (mobiloptimalisert)
- dashboard/style.css    → styling
- dashboard/charts.js    → grafer, visualisering og status.json-lesing
- dashboard/status.json  → live posisjoner + saldo (skrives av boten, leses av dashboard)
- state.json             → persistent bot-state (åpne posisjoner, DCA-nivåer) – ikke pushet til GitHub
- logs/trades.csv        → maskinlesbar logg (last opp til Claude for analyse)
- logs/trades.log        → menneskelig lesbar logg
- requirements.txt       → alle avhengigheter
- .env.example           → mal for API-nøkler
- .env                   → faktiske nøkler (aldri push!)
- .gitignore             → ekskluderer .env og sensitive filer

## Trading strategi
Alle parametre og valg av aktiv strategi gjøres i `config.yaml`. Start boten på nytt etter endringer.

### Gjeldende aktive mynter
- BTCUSDT, ETHUSDT, SOLUSDT
- Definert i `coins`-listen i `config.yaml`

### Tilgjengelige strategier (velges med `strategy.active` i config.yaml)
| Navn       | Kjøpssignal                                         | Salgssignal                          |
|------------|-----------------------------------------------------|--------------------------------------|
| RSI_EMA    | RSI < rsi_buy OG pris > EMA(ema_period)                          | RSI > rsi_sell                       |
| BOLLINGER  | Pris < BB_lower OG RSI < rsi_buy                                 | Pris > BB_upper                      |
| MACD       | MACD krysser over signal OG RSI > rsi_confirm (+histogram/zero-filtre) | MACD krysser under signal      |
| MA_CROSS   | MA(fast) krysser over MA(slow) OG RSI < rsi_buy (EMA eller SMA)  | MA(fast) krysser under MA(slow)      |
| COMBINED   | N av 3 aktive: RSI < rsi_buy, pris > EMA, pris < BB_lower        | RSI > rsi_sell ELLER pris > BB_upper |

### Gjeldende implementasjon
- Ordrer sendes til Binance Spot Testnet API (market orders) – faktiske fyllingsdata brukes
- Saldo hentes fra Binance etter hver syklus og reflekterer reelle ordre-utførelser
- DCA: opptil `max_dca` åpne posisjoner per mynt, `trade_usdt` USDT per kjøp
- Kapitalreserve: `capital_reserve` USDT delt mellom ALLE aktive mynter – aldri bruk under dette
- Persistent state: åpne posisjoner, stoploss-cooldowns og circuit breaker-tilstand lagres til state.json etter hvert evalueringssyklus, gjenopprettes ved oppstart
- Oppstartskontroller: internett → .env/nøkler → config.yaml → Binance API → state.json → posisjoner vs saldo → USDT-reserve. Kritiske feil stopper boten
- Circuit breaker: stopper ALL trading hvis porteføljeverdi faller >N% på M timer (config: safety). Nullstilles ved manuell restart. Tilstand lagres i state.json
- Stoploss-cooldown: konfigurerbar ventetid per mynt etter at stoploss er utløst. Lagres i state.json og overlever restart

### Krav til alle strategier
- Må inkludere fee-kalkulator før hver handel
- Boten skal ALDRI handle hvis forventet gevinst ikke overstiger fees
- Alle beslutninger skal logges med begrunnelse
- CoinState-klassen holder styr på åpne DCA-posisjoner per mynt

### Legge til ny mynt — KUN disse stegene trengs
For å legge til en ny mynt er det nok å endre `config.yaml` og dashboard-filer:

1. **config.yaml** — legg til `"XYZUSDT"` i `coins`-listen
2. **config.yaml** — legg til presisjon i `quantity_precision` (valgfritt, standard er 5 desimaler)
3. **dashboard/index.html** — legg til ticker-item og coin-page (`#page-XYZUSDT`) med riktig ID-prefix
4. **dashboard/charts.js** — legg til symbolet i `SYMBOLS`- og `TICKER_SYMBOLS`-arrayene
5. **dashboard/style.css** — legg til `.coin-xyz` fargekodet venstrekant
6. **Verifiser kapitalreserve** — med flere mynter øker risikoen for samtidige handler. Vurder å justere `trade_usdt` eller `capital_reserve` i config.yaml
7. **Oppdater CLAUDE.md** — oppdater "Gjeldende aktive mynter" i denne filen

Ingen endringer i Python-koden er nødvendig for å legge til en ny mynt.

## Logging format
CSV-kolonnene er:
- tidspunkt, symbol, handling (KJØP/SELG/VENTER)
- pris, mengde_coin, beløp_usdt, fee_usdt
- grunn (hvilke indikatorer trigget)
- gevinst_usdt, gevinst_prosent (kun ved SELG)
- dca_level (1/2/3 ved KJØP, totalt antall solgte ved SELG)

## Dashboard
- Mobiloptimalisert – skal fungere på telefon
- Navigasjon øverst: "Oversikt"-knapp + én knapp per mynt (BTC, ETH, SOL)
- Hamburger-meny på mobil (<600px), alltid synlig på desktop
- Navigasjonen bytter mellom visninger client-side (show/hide) – ingen server nødvendig

### Oversiktsside (standard)
- Live pris-ticker for BTC, ETH, SOL (Binance API, oppdateres hvert 10. sekund)
- Kontosaldo for USDT, BTC, ETH, SOL (fra status.json)
- Åpne posisjoner med snitt-inngangspris, take profit og stop loss (fra status.json)
- Totalt sammendrag: PnL, antall handler, vinnrate, avg fee (alle mynter samlet)

### Mynt-detaljside (én per mynt)
- Indikatorpanel: pris, RSI-gauge (0–100) med kjøp/selg-markeringslinjer, avstand til kjøpssignal, avstand til salgssignal, EMA200-status (over/under i %)
- Statistikk (PnL, handler, vinnrate, avg fee for mynten)
- Åpne DCA-posisjoner
- Prisgraf
- Logg (siste 20 beslutninger)

- Henter CSV og status.json fra GitHub (offentlig repo: https://github.com/ccMellow/botbot)
- Oppdateres automatisk hvert 5. minutt

## status.json format
```json
{
  "updated": "YYYY-MM-DD HH:MM:SS",
  "balances": { "USDT": 9850.0, "BTC": 0.001496, "ETH": 0.0, "SOL": 0.0 },
  "positions": {
    "BTCUSDT": {
      "dca_count": 1,
      "avg_entry_price": 66846.2,
      "take_profit_price": 69520.05,
      "stop_loss_price": 65509.28,
      "total_coin": 0.001496,
      "total_usdt": 100.0,
      "entries": [{"dca_level": 1, "entry_price": 66846.2, "coin_amount": 0.001496, "usdt_amount": 100.0}]
    },
    "ETHUSDT": { "dca_count": 0, "entries": [] },
    "SOLUSDT": { "dca_count": 0, "entries": [] }
  },
  "indicators": {
    "BTCUSDT": {
      "price": 66844.17,
      "rsi": 52.1,
      "ema200": 67281.98,
      "rsi_buy_threshold": 35,
      "rsi_sell_threshold": 65,
      "rsi_to_buy": 17.1,
      "rsi_to_sell": 12.9,
      "price_above_ema200": false,
      "price_vs_ema200_pct": -0.65
    }
  }
}
```
`indicators` oppdateres etter hver strategisyklus (hvert 15. minutt).
`rsi_to_buy` ≤ 0 betyr at kjøpssignalet er aktivt. `rsi_to_sell` ≤ 0 betyr at salgssignalet er aktivt.

## GitHub oppsett
- Logg og dashboard pushes til GitHub hvert 30. minutt
- Repository er offentlig: https://github.com/ccMellow/botbot
- Dashboard hostes på One.com (passordbeskyttet via cPanel)
- One.com henter CSV fra GitHub raw URL for å oppdatere dashboard

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

## Live trading – overgang fra testnet

### Status
Boten kjører for øyeblikket på **Binance Spot Testnet**. Ingen ekte penger er involvert.

### Når brukeren er klar for live trading
Når brukeren bekrefter at de ønsker å bytte til live trading, skal Claude Code følge disse stegene – **i denne rekkefølgen** – og **aldri starte uten eksplisitt bekreftelse fra brukeren**:

#### Obligatorisk advarsel før start
Claude Code skal ALLTID vise denne advarselen og vente på bekreftelse:

> **ADVARSEL: Du er i ferd med å bytte til LIVE TRADING med ekte penger.**
> Dette kan føre til reelle økonomiske tap.
> Bekreft at du forstår dette og ønsker å fortsette.

Ingen endringer skal gjøres før brukeren eksplisitt bekrefter.

#### Steg som må fullføres (i rekkefølge)

1. **Siste testnet-push** — kjør `push_to_github()` for å sikre at all testnet-historikk er lagret på GitHub før endringer

2. **Bytt API-nøkler** — be brukeren oppgi live Binance API-nøkler (hentes fra binance.com → API Management), oppdater `.env` med nye verdier for `BINANCE_API_KEY` og `BINANCE_SECRET_KEY`

3. **Bytt API-endepunkt** — i `config.yaml`, endre `system.testnet: true` til `system.testnet: false`

4. **Sett riktig strategi og parametre** — i `config.yaml`:
   - Sett `strategy.active` til ønsket strategi (f.eks. `RSI_EMA`)
   - Sjekk at `strategies.RSI_EMA.rsi_buy: 35` (normal produksjonsverdier)

5. **Oppdater kapitalreserve** — spør brukeren: *"Hvor mange USDT vil du ha som minimum reserve?"*, oppdater `CAPITAL_RESERVE` i `bot/strategy.py` til det nye beløpet

6. **Verifiser .gitignore** — sjekk at `.env` fortsatt er ekskludert i `.gitignore` før noen push

7. **Oppdater CLAUDE.md** — oppdater "Status"-feltet over til `**LIVE TRADING** – ekte penger involvert`, oppdater kapitalreserve-beløpet i Trading strategi-seksjonen

8. **Final push** — push oppdatert kode (uten `.env`) til GitHub

#### Etter overgang
- Overvåk de første handlene nøye
- Boten handler fortsatt automatisk – pass på at du har dekning
- Live Binance API-nøkler skal aldri committes til GitHub

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