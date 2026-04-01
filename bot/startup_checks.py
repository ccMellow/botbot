"""
startup_checks.py
Kjører en sekvens av oppstartskontroller og config-validering før trading-loopen begynner.
Stopper boten ved kritiske feil. Logger resultat for hvert steg.
"""

import logging
import os
import socket
from datetime import datetime

from binance.client import Client

from bot.circuit_breaker import CircuitBreakerState
from bot.config_loader import get_config, active_strategy_cfg
from bot.state_manager import load_state
from bot.strategy import CoinState, TESTNET

logger = logging.getLogger(__name__)

REQUIRED_ENV_KEYS = ["BINANCE_API_KEY", "BINANCE_SECRET_KEY"]
_SEP = "-" * 56

# Anbefalte grenser for config-validator (advarsel ved avvik, ikke kritisk)
_RANGES = {
    "trading.trade_usdt":          (10.0,   10_000.0),
    "trading.max_dca":             (1,      10),
    "trading.capital_reserve":     (0.0,    None),
    "trading.stop_loss_pct":       (0.005,  0.20),
    "trading.take_profit_pct":     (0.005,  0.50),
    "trading.candle_limit":        (50,     1000),
    "safety.circuit_breaker_pct":  (0.01,   0.50),
    "safety.stop_loss_cooldown_minutes": (0, 1440),
}


# ---------------------------------------------------------------------------
# Individual checks — hvert returner (critical, passed, message)
# critical=True → boten stopper hvis passed=False
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


def _check_config() -> tuple[bool, bool, str]:
    """Valider config.yaml — sjekk at aktiv strategi finnes og verdier er i anbefalte grenser."""
    try:
        cfg = get_config()
    except FileNotFoundError as e:
        return True, False, str(e)
    except Exception as e:
        return True, False, f"Kunne ikke lese config.yaml: {e}"

    warnings = []

    # Aktiv strategi må finnes
    active = cfg.get("strategy", {}).get("active", "")
    known = list(cfg.get("strategies", {}).keys())
    if active not in known:
        return True, False, (
            f"strategy.active='{active}' er ikke definert. "
            f"Gyldige: {known}"
        )

    # Coins-liste kan ikke være tom
    if not cfg.get("coins"):
        return True, False, "coins-listen i config.yaml er tom – legg til minst én mynt"

    # Sjekk numeriske grenser
    trading = cfg.get("trading", {})
    safety = cfg.get("safety", {})

    sections = {
        "trading": trading,
        "safety": safety,
    }
    for key, (lo, hi) in _RANGES.items():
        section, field = key.split(".")
        val = sections.get(section, {}).get(field)
        if val is None:
            continue
        if lo is not None and val < lo:
            warnings.append(f"{key}={val} er lavere enn anbefalt minimum ({lo})")
        if hi is not None and val > hi:
            warnings.append(f"{key}={val} er høyere enn anbefalt maksimum ({hi})")

    # RSI buy < RSI sell for aktiv strategi
    try:
        s = active_strategy_cfg()
        rsi_buy = s.get("rsi_buy", s.get("rsi_confirm"))
        rsi_sell = s.get("rsi_sell")
        if rsi_buy is not None and rsi_sell is not None and rsi_buy >= rsi_sell:
            warnings.append(
                f"{active}: rsi_buy ({rsi_buy}) må være lavere enn rsi_sell ({rsi_sell})"
            )
    except Exception:
        pass

    if warnings:
        for w in warnings:
            logger.warning(f"  [!]  config.yaml: {w}")
        return False, False, f"{len(warnings)} advarsel(er) i config.yaml – se over"

    return False, True, (
        f"OK – aktiv strategi: {active} | "
        f"{len(cfg['coins'])} mynt(er): {', '.join(cfg['coins'])}"
    )


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
        if held < expected * 0.99:
            mismatches.append(
                f"{asset}: forventer {expected:.6f}, Binance har {held:.6f}"
            )

    if mismatches:
        return False, False, f"Avvik i {len(mismatches)} posisjon(er): " + " | ".join(mismatches)
    return False, True, f"{len(open_states)} posisjon(er) verifisert mot Binance-saldo"


def _check_usdt_reserve(balances: dict[str, float]) -> tuple[bool, bool, str]:
    capital_reserve = get_config()["trading"]["capital_reserve"]
    usdt = balances.get("USDT", 0.0)
    if usdt < capital_reserve:
        available = usdt - capital_reserve
        return False, False, (
            f"Saldo {usdt:,.2f} USDT er under reserve {capital_reserve:,.0f} USDT "
            f"({available:,.2f} tilgjengelig) – ingen kjøp vil bli utført"
        )
    available = usdt - capital_reserve
    return False, True, (
        f"Saldo {usdt:,.2f} USDT | {available:,.2f} tilgjengelig over reserve"
    )


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
    cfg = get_config()
    mode_line = (
        "*** TESTNET ***  (ingen ekte penger)"
        if TESTNET
        else "*** LIVE TRADING ***  (ekte penger!)"
    )
    logger.info(_SEP)
    logger.info("  OPPSTARTSSTATUS")
    logger.info(_SEP)
    logger.info(f"  Modus     : {mode_line}")
    logger.info(f"  Strategi  : {cfg['strategy']['active']}")
    logger.info(f"  Tidspunkt : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Mynter    : {', '.join(cfg['coins'])}")

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
            f"  Circuit br. : snapshot {cb_state.snapshot_value:,.2f} USDT "
            f"({int(cfg['safety']['circuit_breaker_window_hours'])}t-vindu)"
        )

    logger.info(_SEP)


# ---------------------------------------------------------------------------
# Hovedfunksjon
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

    # --- config.yaml (kritisk ved ugyldig strategi/tom coins-liste) ---
    crit, passed, msg = _check_config()
    _log_result("config.yaml", passed, crit, msg)
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

    # --- state.json — laster posisjoner, cooldowns og circuit breaker ---
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

    logger.info(_SEP)

    if critical_failed:
        logger.error("Kritisk oppstartsfeil – boten starter ikke.")
        return False

    _log_startup_summary(states, cb_state)
    return True
