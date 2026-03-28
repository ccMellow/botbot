"""
status_writer.py
Skriver status.json med åpne posisjoner og kontosaldo til dashboard/.
Kalles etter hver strategisyklus og ved GitHub-push.
"""

import json
import logging
import os
from datetime import datetime

from binance.client import Client
from bot.strategy import CoinState, STOP_LOSS_PCT, TAKE_PROFIT_PCT, RSI_BUY, RSI_SELL

logger = logging.getLogger(__name__)

STATUS_FILE = os.path.join(os.path.dirname(__file__), "..", "dashboard", "status.json")
BALANCE_ASSETS = ["USDT", "BTC", "ETH", "SOL"]


def _get_balances(client: Client) -> dict:
    """Hent live saldo fra Binance. Nøyaktig siden boten nå plasserer ekte ordrer."""
    raw = client.get_account()["balances"]
    result = {}
    for b in raw:
        if b["asset"] in BALANCE_ASSETS:
            result[b["asset"]] = round(float(b["free"]) + float(b["locked"]), 8)
    for asset in BALANCE_ASSETS:
        result.setdefault(asset, 0.0)
    return result


def write_status(states: dict[str, CoinState], client: Client) -> None:
    """Skriv nåværende posisjoner og saldo til dashboard/status.json."""
    try:
        balances = _get_balances(client)

        positions = {}
        for symbol, state in states.items():
            if state.in_position:
                avg = state.avg_buy_price
                positions[symbol] = {
                    "dca_count": state.dca_count,
                    "avg_entry_price": round(avg, 4),
                    "take_profit_price": round(avg * (1 + TAKE_PROFIT_PCT), 4),
                    "stop_loss_price": round(avg * (1 - STOP_LOSS_PCT), 4),
                    "total_coin": round(state.total_coin_amount, 8),
                    "total_usdt": round(state.total_usdt_invested, 2),
                    "entries": [
                        {
                            "dca_level": p.dca_level,
                            "entry_price": round(p.buy_price, 4),
                            "coin_amount": round(p.coin_amount, 8),
                            "usdt_amount": round(p.usdt_amount, 2),
                        }
                        for p in state.positions
                    ],
                }
            else:
                positions[symbol] = {
                    "dca_count": 0,
                    "entries": [],
                }

        # Alltid inkluder alle symboler — selv om evaluate() ikke har kjørt ennå
        indicators = {}
        for symbol, state in states.items():
            rsi = state.last_rsi or None
            ema200 = state.last_ema200 or None
            price = state.last_price or None
            pct_vs_ema = round((price - ema200) / ema200 * 100, 2) if (price and ema200) else None
            indicators[symbol] = {
                "price": round(price, 4) if price else None,
                "rsi": rsi,
                "ema200": round(ema200, 4) if ema200 else None,
                "rsi_buy_threshold": RSI_BUY,
                "rsi_sell_threshold": RSI_SELL,
                "rsi_to_buy": round(rsi - RSI_BUY, 2) if rsi else None,
                "rsi_to_sell": round(RSI_SELL - rsi, 2) if rsi else None,
                "price_above_ema200": (price > ema200) if (price and ema200) else None,
                "price_vs_ema200_pct": pct_vs_ema,
            }

        data = {
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "balances": balances,
            "positions": positions,
            "indicators": indicators,
        }

        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("status.json oppdatert.")

    except Exception as e:
        logger.error(f"Feil ved skriving av status.json: {e}")
