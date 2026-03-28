"""
circuit_breaker.py
Stopper all trading hvis total portefølje taper mer enn 5% på 24 timer.
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LOSS_THRESHOLD_PCT  = 0.05   # 5% tap utløser circuit breaker
SNAPSHOT_WINDOW_SEC = 86400  # 24 timer — snapshot fornyes etter dette


@dataclass
class CircuitBreakerState:
    triggered: bool = False
    snapshot_value: float = 0.0
    snapshot_time: float = 0.0  # unix-timestamp, 0.0 = ingen snapshot ennå


def check_and_update(current_value: float, state: CircuitBreakerState) -> bool:
    """
    Sjekk porteføljetap mot 24-timers snapshot.
    Oppdaterer snapshot hvis 24 timer har gått.
    Returnerer True hvis circuit breaker er utløst og trading skal stoppes.
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
            f"Circuit breaker: 24t-snapshot tatt ({current_value:,.2f} USDT)"
        )
        return False

    # Sjekk tap mot snapshot
    if state.snapshot_value > 0:
        loss_pct = (state.snapshot_value - current_value) / state.snapshot_value
        if loss_pct >= LOSS_THRESHOLD_PCT:
            state.triggered = True
            logger.critical(
                f"CIRCUIT BREAKER UTLØST – portefølje ned {loss_pct * 100:.2f}% "
                f"siste 24t: {state.snapshot_value:,.2f} → {current_value:,.2f} USDT "
                f"(grense: {LOSS_THRESHOLD_PCT * 100:.0f}%). All trading stoppet."
            )
            return True

    # Forny snapshot etter 24 timer
    if now - state.snapshot_time >= SNAPSHOT_WINDOW_SEC:
        logger.info(
            f"Circuit breaker: snapshot fornyet "
            f"({state.snapshot_value:,.2f} → {current_value:,.2f} USDT)"
        )
        state.snapshot_value = current_value
        state.snapshot_time = now

    return False
