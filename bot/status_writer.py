"""
status_writer.py
Skriver status.json med åpne posisjoner, saldo og indikatorer til dashboard/.
Kalles etter hver strategisyklus og ved GitHub-push.
"""

import json
import logging
import os
from datetime import datetime

from binance.client import Client

from bot.config_loader import get_config, active_strategy_cfg
from bot.strategy import CoinState

logger = logging.getLogger(__name__)

STATUS_FILE = os.path.join(os.path.dirname(__file__), "..", "dashboard", "status.json")


def _balance_assets() -> list[str]:
    """Hent liste over assets å vise i saldo (USDT + base-assets for aktive mynter)."""
    cfg = get_config()
    assets = ["USDT"] + [s.replace("USDT", "") for s in cfg["coins"]]
    return assets


def _get_balances(client: Client) -> dict:
    assets = _balance_assets()
    raw = client.get_account()["balances"]
    result = {}
    for b in raw:
        if b["asset"] in assets:
            result[b["asset"]] = round(float(b["free"]) + float(b["locked"]), 8)
    for asset in assets:
        result.setdefault(asset, 0.0)
    return result


def write_status(states: dict[str, CoinState], client: Client) -> None:
    """Skriv nåværende posisjoner, saldo og indikatorer til dashboard/status.json."""
    try:
        cfg = get_config()
        trading = cfg["trading"]
        s = active_strategy_cfg()

        # RSI-terskelverdier for aktiv strategi (for gauge i dashboard)
        rsi_buy_threshold = s.get("rsi_buy", s.get("rsi_confirm", 35))
        rsi_sell_threshold = s.get("rsi_sell", 65)

        balances = _get_balances(client)

        # Posisjoner
        positions = {}
        for symbol, state in states.items():
            if state.in_position:
                avg = state.avg_buy_price
                positions[symbol] = {
                    "dca_count": state.dca_count,
                    "avg_entry_price": round(avg, 4),
                    "take_profit_price": round(avg * (1 + trading["take_profit_pct"]), 4),
                    "stop_loss_price": round(avg * (1 - trading["stop_loss_pct"]), 4),
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
                positions[symbol] = {"dca_count": 0, "entries": []}

        # Indikatorer — alltid inkluder alle symboler
        indicators = {}
        for symbol, state in states.items():
            rsi = state.last_rsi or None
            ema200 = state.last_ema200 or None
            price = state.last_price or None
            pct_vs_ema = (
                round((price - ema200) / ema200 * 100, 2)
                if (price and ema200) else None
            )
            indicators[symbol] = {
                "price": round(price, 4) if price else None,
                "rsi": rsi,
                "ema200": round(ema200, 4) if ema200 else None,
                "rsi_buy_threshold": rsi_buy_threshold,
                "rsi_sell_threshold": rsi_sell_threshold,
                "rsi_to_buy": round(rsi - rsi_buy_threshold, 2) if rsi else None,
                "rsi_to_sell": round(rsi_sell_threshold - rsi, 2) if rsi else None,
                "price_above_ema200": (price > ema200) if (price and ema200) else None,
                "price_vs_ema200_pct": pct_vs_ema,
                "active_strategy": cfg["strategy"]["active"],
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
