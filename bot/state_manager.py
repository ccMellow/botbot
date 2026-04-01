"""
state_manager.py
Lagrer og gjenoppretter CoinState og CircuitBreakerState til/fra state.json.
Kalles etter hvert evalueringssyklus og ved oppstart.
"""

import json
import logging
import os

from bot.circuit_breaker import CircuitBreakerState
from bot.strategy import CoinState, Position

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "state.json")


def save_state(states: dict[str, CoinState], cb_state: CircuitBreakerState) -> None:
    """Skriv alle åpne posisjoner og circuit breaker-tilstand til state.json."""
    data: dict = {}
    for symbol, state in states.items():
        data[symbol] = {
            "positions": [
                {
                    "buy_price": p.buy_price,
                    "coin_amount": p.coin_amount,
                    "usdt_amount": p.usdt_amount,
                    "dca_level": p.dca_level,
                }
                for p in state.positions
            ],
            "stop_loss_cooldown_until": state.stop_loss_cooldown_until,
            "daily_buy_count": state.daily_buy_count,
            "daily_buy_date": state.daily_buy_date,
        }
    data["_circuit_breaker"] = {
        "triggered": cb_state.triggered,
        "snapshot_value": cb_state.snapshot_value,
        "snapshot_time": cb_state.snapshot_time,
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Feil ved lagring av state.json: {e}")


def load_state(
    states: dict[str, CoinState],
    cb_state: CircuitBreakerState,
) -> list[str]:
    """
    Les state.json og gjenopprett posisjoner, cooldowns og circuit breaker-tilstand.
    Returnerer liste med sammendragsstrenger for gjenopprettede posisjoner.
    Kaster unntak ved lesefeil slik at kalleren kan håndtere dem.
    """
    if not os.path.exists(STATE_FILE):
        return []

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Gjenopprett per-mynt state
    restored = []
    for symbol, state in states.items():
        coin_data = data.get(symbol, {})

        for p in coin_data.get("positions", []):
            state.positions.append(Position(
                buy_price=p["buy_price"],
                coin_amount=p["coin_amount"],
                usdt_amount=p["usdt_amount"],
                dca_level=p["dca_level"],
            ))

        state.stop_loss_cooldown_until = coin_data.get("stop_loss_cooldown_until", 0.0)
        state.daily_buy_count = coin_data.get("daily_buy_count", 0)
        state.daily_buy_date = coin_data.get("daily_buy_date", "")

        if state.dca_count > 0:
            coin = symbol.replace("USDT", "")
            restored.append(
                f"{coin}: {state.dca_count} posisjon(er) | "
                f"snitt {state.avg_buy_price:,.2f} | "
                f"{state.total_usdt_invested:.2f} USDT investert"
            )

    # Gjenopprett circuit breaker-tilstand
    cb_data = data.get("_circuit_breaker", {})
    cb_state.triggered = cb_data.get("triggered", False)
    cb_state.snapshot_value = cb_data.get("snapshot_value", 0.0)
    cb_state.snapshot_time = cb_data.get("snapshot_time", 0.0)

    return restored
