"""
startup_checks.py
Kjører en sekvens av oppstartskontroller før trading-loopen begynner.
Stopper boten ved kritiske feil. Logger resultat for hvert steg.
"""

import logging
import os
import socket
from datetime import datetime

from binance.client import Client

from bot.circuit_breaker import CircuitBreakerState
from bot.state_manager import load_state
from bot.strategy import CoinState, SYMBOLS, CAPITAL_RESERVE, TESTNET

logger = logging.getLogger(__name__)

REQUIRED_ENV_KEYS = ["BINANCE_API_KEY", "BINANCE_SECRET_KEY"]
_SEP = "-" * 56


# ---------------------------------------------------------------------------
# Individual checks — each returns (critical: bool, passed: bool, message: str)
# critical=True means the bot must stop if passed=False
# ---------------------------------------------------------------------------

def _check_internet() -> tuple[bool, bool, str]:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True, True, "OK"
    except OSError:
        return True, False, "Ingen internettforbindelse – kan ikke starte"


def _check_env() -> tuple[bool, bool, str]:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return True, False, ".env-fil mangler"
    missing = [k for k in REQUIRED_ENV_KEYS if not os.getenv(k)]
    if missing:
        return True, False, f"Manglende nøkler: {', '.join(missing)}"
    return True, True, "Alle påkrevde nøkler tilstede"


def _check_api(client: Client) -> tuple[bool, bool, str]:
    try:
        client.ping()
        return True, True, "Tilkobling OK"
    except Exception as e:
        return True, False, f"Feilet: {e}"


def _check_state(
    states: dict[str, CoinState],
    cb_state: CircuitBreakerState,
) -> tuple[bool, bool, str]:
    try:
        restored = load_state(states, cb_state)
        if restored:
            return False, True, f"{len(restored)} mynt(er) med åpne posisjoner gjenopprettet"
        if os.path.exists(os.path.join(os.path.dirname(__file__), "..", "state.json")):
            return False, True, "Ingen åpne posisjoner i state.json"
        return False, True, "Ingen state.json – starter uten posisjoner"
    except Exception as e:
        return False, False, f"Kunne ikke lese state.json: {e}"


def _check_positions_vs_balances(
    states: dict[str, CoinState],
    balances: dict[str, float],
) -> tuple[bool, bool, str]:
    open_states = {s.symbol: s for s in states.values() if s.in_position}
    if not open_states:
        return False, True, "Ingen åpne posisjoner å verifisere"

    mismatches = []
    for symbol, state in open_states.items():
        asset = symbol.replace("USDT", "")
        held = balances.get(asset, 0.0)
        expected = state.total_coin_amount
        if held < expected * 0.99:  # 1% tolerance for avrunding
            mismatches.append(
                f"{asset}: forventer {expected:.6f}, Binance har {held:.6f}"
            )

    if mismatches:
        return False, False, f"Avvik i {len(mismatches)} posisjon(er): " + " | ".join(mismatches)
    return False, True, f"{len(open_states)} posisjon(er) verifisert mot Binance-saldo"


def _check_usdt_reserve(balances: dict[str, float]) -> tuple[bool, bool, str]:
    usdt = balances.get("USDT", 0.0)
    if usdt < CAPITAL_RESERVE:
        available = usdt - CAPITAL_RESERVE
        return False, False, (
            f"Saldo {usdt:,.2f} USDT er under reserve {CAPITAL_RESERVE:,.0f} USDT "
            f"({available:,.2f} tilgjengelig) – ingen kjøp vil bli utført"
        )
    available = usdt - CAPITAL_RESERVE
    return False, True, (
        f"Saldo {usdt:,.2f} USDT | {available:,.2f} tilgjengelig over reserve"
    )


def _check_test_mode() -> tuple[bool, bool, str] | None:
    """Returnerer None hvis TEST_MODE ikke er definert i miljøet (funksjonen ikke aktiv)."""
    raw = os.getenv("TEST_MODE")
    if raw is None:
        return None
    active = raw.strip().lower() == "true"
    if active:
        return False, True, "AKTIV – RSI_BUY=55, EMA200-filter deaktivert"
    return False, True, "Inaktiv – full strategi (RSI_BUY=35, EMA200 på)"


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_result(label: str, passed: bool, critical: bool, message: str) -> None:
    if passed:
        icon = "[OK]"
        level = logging.INFO
    elif critical:
        icon = "[!!]"
        level = logging.ERROR
    else:
        icon = "[!] "
        level = logging.WARNING
    logger.log(level, f"  {icon} {label:<28} {message}")


def _log_startup_summary(
    states: dict[str, CoinState],
    cb_state: CircuitBreakerState,
) -> None:
    mode_line = (
        "*** TESTNET ***  (ingen ekte penger)"
        if TESTNET
        else "*** LIVE TRADING ***  (ekte penger!)"
    )
    logger.info(_SEP)
    logger.info("  OPPSTARTSSTATUS")
    logger.info(_SEP)
    logger.info(f"  Modus     : {mode_line}")
    logger.info(f"  Tidspunkt : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Mynter    : {', '.join(SYMBOLS)}")

    open_count = sum(1 for s in states.values() if s.in_position)
    if open_count == 0:
        logger.info("  Posisjoner: ingen åpne")
    else:
        for symbol, state in states.items():
            if state.in_position:
                coin = symbol.replace("USDT", "")
                logger.info(
                    f"  {coin:<10}: {state.dca_count} pos | "
                    f"snitt {state.avg_buy_price:,.2f} | "
                    f"{state.total_usdt_invested:.2f} USDT investert"
                )

    if cb_state.triggered:
        logger.critical("  CIRCUIT BREAKER: AKTIV – trading er stoppet!")
    elif cb_state.snapshot_time > 0:
        logger.info(
            f"  Circuit br. : snapshot {cb_state.snapshot_value:,.2f} USDT (24t-vindu)"
        )

    logger.info(_SEP)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_startup_checks(
    states: dict[str, CoinState],
    client: Client,
    cb_state: CircuitBreakerState,
) -> bool:
    """
    Kjør alle oppstartskontroller i sekvens.
    Returnerer True hvis boten kan starte, False ved kritisk feil.
    """
    logger.info(_SEP)
    logger.info("  OPPSTARTSKONTROLLER")
    logger.info(_SEP)

    critical_failed = False

    # --- Internett (kritisk) ---
    crit, passed, msg = _check_internet()
    _log_result("Internett", passed, crit, msg)
    if crit and not passed:
        critical_failed = True

    # --- .env og API-nøkler (kritisk) ---
    crit, passed, msg = _check_env()
    _log_result(".env / API-nøkler", passed, crit, msg)
    if crit and not passed:
        critical_failed = True

    # --- Binance API (kritisk) ---
    crit, passed, msg = _check_api(client)
    _log_result("Binance API", passed, crit, msg)
    if crit and not passed:
        critical_failed = True

    # Hent saldo én gang for de neste sjekkene
    balances: dict[str, float] = {}
    if not critical_failed:
        try:
            raw = client.get_account()["balances"]
            balances = {b["asset"]: float(b["free"]) + float(b["locked"]) for b in raw}
        except Exception as e:
            logger.error(f"  [!!] Kunne ikke hente Binance-saldo: {e}")
            critical_failed = True

    # --- state.json — laster posisjoner, cooldowns og circuit breaker (advarsel ved feil) ---
    crit, passed, msg = _check_state(states, cb_state)
    _log_result("state.json", passed, crit, msg)

    # --- Posisjoner vs Binance-saldo (advarsel) ---
    if balances:
        crit, passed, msg = _check_positions_vs_balances(states, balances)
        _log_result("Posisjoner vs saldo", passed, crit, msg)

    # --- USDT over kapitalreserve (advarsel) ---
    if balances:
        crit, passed, msg = _check_usdt_reserve(balances)
        _log_result("USDT-reserve", passed, crit, msg)

    # --- TEST_MODE (dynamisk – vises bare hvis variabelen er definert i .env) ---
    test_result = _check_test_mode()
    if test_result is not None:
        crit, passed, msg = test_result
        _log_result("TEST_MODE", passed, crit, msg)

    logger.info(_SEP)

    if critical_failed:
        logger.error("Kritisk oppstartsfeil – boten starter ikke.")
        return False

    _log_startup_summary(states, cb_state)
    return True
