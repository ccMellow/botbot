"""
circuit_breaker.py
Stopper all trading hvis total portefølje taper mer enn konfigurerbar prosent på 24 timer.
Terskelverdier leses fra config.yaml (safety.circuit_breaker_pct og
safety.circuit_breaker_window_hours).
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerState:
    triggered: bool = False
    snapshot_value: float = 0.0
    snapshot_time: float = 0.0  # unix-timestamp, 0.0 = ingen snapshot ennå


def check_and_update(
    current_value: float,
    state: CircuitBreakerState,
    loss_threshold_pct: float,
    snapshot_window_sec: int,
) -> bool:
    """
    Sjekk porteføljetap mot rullerende snapshot.
    Returnerer True hvis circuit breaker er utløst og trading skal stoppes.

    Args:
        current_value: Nåværende total porteføljeverdi i USDT.
        state: Muterbar circuit breaker-tilstand (lagres i state.json).
        loss_threshold_pct: f.eks. 0.05 for 5% tapsgrense.
        snapshot_window_sec: f.eks. 86400 for 24-timers vindu.
    """
    if state.triggered:
        logger.critical(
            "CIRCUIT BREAKER AKTIV – all trading er stoppet. "
            "Start boten på nytt for å nullstille."
        )
        return True

    now = time.time()

    # Ingen snapshot ennå — ta en og fortsett
    if state.snapshot_time == 0.0:
        state.snapshot_value = current_value
        state.snapshot_time = now
        logger.info(
            f"Circuit breaker: {int(snapshot_window_sec // 3600)}t-snapshot tatt "
            f"({current_value:,.2f} USDT)"
        )
        return False

    # Sjekk tap mot snapshot
    if state.snapshot_value > 0:
        loss_pct = (state.snapshot_value - current_value) / state.snapshot_value
        if loss_pct >= loss_threshold_pct:
            state.triggered = True
            logger.critical(
                f"CIRCUIT BREAKER UTLØST – portefølje ned {loss_pct * 100:.2f}% "
                f"siste {int(snapshot_window_sec // 3600)}t: "
                f"{state.snapshot_value:,.2f} → {current_value:,.2f} USDT "
                f"(grense: {loss_threshold_pct * 100:.0f}%). All trading stoppet."
            )
            return True

    # Forny snapshot etter konfigurert vindu
    if now - state.snapshot_time >= snapshot_window_sec:
        logger.info(
            f"Circuit breaker: snapshot fornyet "
            f"({state.snapshot_value:,.2f} → {current_value:,.2f} USDT)"
        )
        state.snapshot_value = current_value
        state.snapshot_time = now

    return False
