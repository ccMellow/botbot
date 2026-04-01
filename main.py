"""
main.py
Hovedloop for trading-boten.
Alle parametre leses fra config.yaml.
"""

import logging
import time as _time
import schedule
from dotenv import load_dotenv

from bot.config_loader import get_config
from bot.strategy import get_client, fetch_candles, compute_indicators, evaluate, CoinState, get_symbols
from bot.github_pusher import push_to_github
from bot.status_writer import write_status
from bot.state_manager import save_state
from bot.circuit_breaker import CircuitBreakerState, check_and_update
from bot.startup_checks import run_startup_checks

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SYMBOLS = get_symbols()
states = {symbol: CoinState(symbol) for symbol in SYMBOLS}
cb_state = CircuitBreakerState()


def run_strategy():
    try:
        cfg = get_config()
        safety = cfg["safety"]
        loss_threshold = safety["circuit_breaker_pct"]
        snapshot_window = int(safety["circuit_breaker_window_hours"] * 3600)

        client = get_client()

        # Pass 1: hent candles og indikatorer for alle mynter
        dfs = {}
        for symbol in SYMBOLS:
            df = fetch_candles(client, symbol)
            dfs[symbol] = compute_indicators(df)

        # Beregn total porteføljeverdi for circuit breaker-sjekk
        raw_balances = client.get_account()["balances"]
        usdt = next((float(b["free"]) for b in raw_balances if b["asset"] == "USDT"), 0.0)
        portfolio_value = usdt + sum(
            states[symbol].total_coin_amount * float(dfs[symbol].iloc[-2]["close"])
            for symbol in SYMBOLS
        )

        if check_and_update(portfolio_value, cb_state, loss_threshold, snapshot_window):
            save_state(states, cb_state)
            return

        # Pass 2: evaluer strategi per mynt
        for symbol in SYMBOLS:
            evaluate(dfs[symbol], states[symbol], client)
            save_state(states, cb_state)

        write_status(states, client)

    except Exception as e:
        logger.error(f"Feil i strategi-kjøring: {e}")


def hourly_push():
    logger.info("Kjører auto-push til GitHub...")
    push_to_github()


if __name__ == "__main__":
    logger.info(f"Trading-bot starter. Symboler: {', '.join(SYMBOLS)}")

    _startup_client = get_client()
    if not run_startup_checks(states, _startup_client, cb_state):
        raise SystemExit(1)

    # Kjør umiddelbart ved oppstart
    run_strategy()
    hourly_push()

    # Planlegg iterasjoner (push-intervall leses fra config)
    push_interval = get_config()["system"]["github_push_interval_min"]
    schedule.every(15).minutes.do(run_strategy)
    schedule.every(push_interval).minutes.do(hourly_push)
    logger.info(f"Planlagt: strategi hvert 15. min, GitHub-push hvert {push_interval}. min")

    while True:
        schedule.run_pending()
        _time.sleep(30)
