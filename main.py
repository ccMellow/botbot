"""
main.py
Hovedloop for trading-boten.
Kjører strategien hvert 15. minutt og pusher til GitHub hver time.
"""

import logging
import time
import schedule
from dotenv import load_dotenv

from bot.strategy import get_client, fetch_candles, compute_indicators, evaluate, BotState
from bot.github_pusher import push_to_github

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

state = BotState()


def run_strategy():
    try:
        client = get_client()
        df = fetch_candles(client)
        df = compute_indicators(df)
        evaluate(df, state)
    except Exception as e:
        logger.error(f"Feil i strategi-kjøring: {e}")


def hourly_push():
    logger.info("Kjører auto-push til GitHub...")
    push_to_github()


if __name__ == "__main__":
    logger.info("Trading-bot starter. Symbol: BTCUSDT | Testnet: True")

    # Kjør umiddelbart ved oppstart
    run_strategy()

    # Planlegg iterasjoner
    schedule.every(15).minutes.do(run_strategy)
    schedule.every(1).hour.do(hourly_push)

    while True:
        schedule.run_pending()
        time.sleep(30)
