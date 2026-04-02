"""
strategy.py
Multi-coin trading med DCA mot USDT på Binance.

Aktiv strategi velges i config.yaml (strategy.active).
Tilgjengelige strategier: RSI_EMA | BOLLINGER | MACD | MA_CROSS | COMBINED

Alle parametre (mynter, terskelverdi, DCA-grenser osv.) leses fra config.yaml.
"""

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import date

from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import ta

from bot.config_loader import get_config, active_strategy_cfg
from bot.fee_calculator import (
    calculate_fee,
    is_profitable,
    net_profit,
    profit_percent,
    minimum_sell_price,
)
from bot.logger import log_decision

load_dotenv()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers (leses én gang og caches)
# ---------------------------------------------------------------------------

def _cfg():
    return get_config()

def _trading():
    return _cfg()["trading"]

def _symbols() -> list[str]:
    return _cfg()["coins"]

# Eksporter for bruk i andre moduler
def get_symbols() -> list[str]:
    return _cfg()["coins"]

TESTNET: bool = get_config()["system"]["testnet"]


# ---------------------------------------------------------------------------
# Dataklasser
# ---------------------------------------------------------------------------

@dataclass
class Position:
    buy_price: float
    coin_amount: float
    usdt_amount: float
    dca_level: int


class CoinState:
    """Holder styr på åpne DCA-posisjoner og siste indikatorverdier for én mynt."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.positions: list[Position] = []
        self.last_price: float = 0.0
        self.last_rsi: float = 0.0
        self.last_ema200: float = 0.0
        self.stop_loss_cooldown_until: float = 0.0  # unix-timestamp, 0 = ingen cooldown
        self.daily_buy_count: int = 0               # kjøp gjort i dag
        self.daily_buy_date: str = ""               # dato for siste telledag (YYYY-MM-DD)
        self.volatility_paused: bool = False        # True = kjøp pauset pga. høy volatilitet
        self.trailing_peak_price: float = 0.0       # Høyeste pris siden posisjon ble åpnet

    @property
    def dca_count(self) -> int:
        return len(self.positions)

    @property
    def in_position(self) -> bool:
        return len(self.positions) > 0

    @property
    def avg_buy_price(self) -> float:
        total_usdt = sum(p.usdt_amount for p in self.positions)
        if total_usdt == 0:
            return 0.0
        return sum(p.buy_price * p.usdt_amount for p in self.positions) / total_usdt

    @property
    def total_coin_amount(self) -> float:
        return sum(p.coin_amount for p in self.positions)

    @property
    def total_usdt_invested(self) -> float:
        return sum(p.usdt_amount for p in self.positions)


# ---------------------------------------------------------------------------
# Binance-klient
# ---------------------------------------------------------------------------

def get_client() -> Client:
    api_key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_SECRET_KEY")
    return Client(api_key, secret, testnet=TESTNET)


# ---------------------------------------------------------------------------
# Datahenting og indikatorer
# ---------------------------------------------------------------------------

def fetch_candles(client: Client, symbol: str) -> pd.DataFrame:
    cfg = _cfg()
    interval = cfg["trading"]["candle_interval"]
    limit = cfg["trading"]["candle_limit"]
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore"
    ])
    df["open"] = df["open"].astype(float)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Beregner alle indikatorer som trengs på tvers av alle strategier.
    Aktiv strategi bestemmer hvilke parametre som brukes.
    """
    cfg = _cfg()
    s = active_strategy_cfg()

    # --- RSI ---
    rsi_period = s.get("rsi_period", 14)
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=rsi_period).rsi()

    # --- EMA (alle aktuelle perioder) ---
    ema_periods = {50, 200}
    for key in ("ema_period", "fast_ma", "slow_ma"):
        if key in s:
            ema_periods.add(s[key])
    for p in ema_periods:
        df[f"ema{p}"] = ta.trend.EMAIndicator(df["close"], window=p).ema_indicator()

    # --- SMA (for MA_CROSS når ma_type=SMA) ---
    active_name = cfg["strategy"]["active"]
    if active_name == "MA_CROSS" and s.get("ma_type", "EMA").upper() == "SMA":
        for p in {s.get("fast_ma", 50), s.get("slow_ma", 200)}:
            df[f"sma{p}"] = ta.trend.SMAIndicator(df["close"], window=p).sma_indicator()

    # --- Bollinger Bands ---
    bb_period = s.get("period", s.get("bb_period", 20))
    bb_std = float(s.get("std_dev", s.get("bb_std", 2.0)))
    bb = ta.volatility.BollingerBands(df["close"], window=bb_period, window_dev=bb_std)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    # BB-bredde brukes av squeeze_filter (BOLLINGER-strategi)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_width_ma"] = df["bb_width"].rolling(bb_period).mean()

    # --- Volum glidende snitt (volumfilter) ---
    df["volume_ma20"] = df["volume"].rolling(20).mean()

    # --- ATR (dynamisk stoploss) ---
    atr_period = cfg.get("trading", {}).get("atr_period", 14)
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"], window=atr_period
    ).average_true_range()

    # --- MACD ---
    macd_fast = s.get("fast", s.get("macd_fast", 12))
    macd_slow = s.get("slow", s.get("macd_slow", 26))
    macd_sig = s.get("signal", s.get("macd_signal", 9))
    macd_ind = ta.trend.MACD(
        df["close"],
        window_slow=macd_slow,
        window_fast=macd_fast,
        window_sign=macd_sig,
    )
    df["macd"] = macd_ind.macd()
    df["macd_signal"] = macd_ind.macd_signal()

    return df


# ---------------------------------------------------------------------------
# Strategi-signalfunksjoner
# Alle tar (rows, s) der rows er en DataFrame-slice med bekreftede lys.
# rows.iloc[-1] = siste bekreftede lys, rows.iloc[-2] = forrige lys.
# Returnerer: (buy_signal, buy_reason, sell_signal, sell_reason)
# ---------------------------------------------------------------------------

def _signal_rsi_ema(rows: pd.DataFrame, s: dict) -> tuple[bool, str, bool, str]:
    last = rows.iloc[-1]
    rsi = float(last["rsi"])
    price = float(last["close"])
    ema = float(last[f"ema{s['ema_period']}"])

    buy = rsi < s["rsi_buy"] and price > ema
    sell = rsi > s["rsi_sell"]

    buy_r = (
        f"RSI={rsi:.1f} < {s['rsi_buy']}, "
        f"pris {price:,.2f} > EMA{s['ema_period']} {ema:,.2f}"
    )
    sell_r = f"RSI={rsi:.1f} > {s['rsi_sell']}"
    return buy, buy_r, sell, sell_r


def _signal_bollinger(rows: pd.DataFrame, s: dict) -> tuple[bool, str, bool, str]:
    last = rows.iloc[-1]
    rsi = float(last["rsi"])
    price = float(last["close"])
    bb_lower = float(last["bb_lower"])
    bb_upper = float(last["bb_upper"])

    buy = price < bb_lower and rsi < s["rsi_buy"]
    sell = price > bb_upper

    # squeeze_filter: kjøp kun etter BB-squeeze (bands trange → begynner å utvide seg)
    if buy and s.get("squeeze_filter", False):
        prev = rows.iloc[-2]
        bb_w_now = float(last["bb_width"])
        bb_w_ma_now = float(last["bb_width_ma"])
        bb_w_prev = float(prev["bb_width"])
        bb_w_ma_prev = float(prev["bb_width_ma"])
        # Squeeze: forrige lys hadde width <= ma (squeezet), nåværende > ma (ekspansjon starter)
        squeeze_expanding = (bb_w_prev <= bb_w_ma_prev) and (bb_w_now > bb_w_ma_now)
        if not squeeze_expanding:
            buy = False

    buy_r = (
        f"RSI={rsi:.1f} < {s['rsi_buy']}, "
        f"pris {price:,.2f} < BB_lower {bb_lower:,.2f}"
    )
    if s.get("squeeze_filter", False):
        buy_r += " | squeeze-filter aktiv"
    sell_r = f"Pris {price:,.2f} > BB_upper {bb_upper:,.2f}"
    return buy, buy_r, sell, sell_r


def _signal_macd(rows: pd.DataFrame, s: dict) -> tuple[bool, str, bool, str]:
    last = rows.iloc[-1]
    prev = rows.iloc[-2]
    rsi = float(last["rsi"])
    macd_curr = float(last["macd"])
    sig_curr = float(last["macd_signal"])
    macd_prev = float(prev["macd"])
    sig_prev = float(prev["macd_signal"])

    cross_up = (macd_prev < sig_prev) and (macd_curr >= sig_curr)
    cross_down = (macd_prev > sig_prev) and (macd_curr <= sig_curr)

    buy = cross_up and rsi > s["rsi_buy"]

    # histogram_filter: histogram (MACD - signal) må være positivt ved kjøp
    if buy and s.get("histogram_filter", True):
        if not (macd_curr - sig_curr) > 0:
            buy = False

    # zero_cross_filter: MACD-linjen må ligge over null-linjen ved kjøp
    if buy and s.get("zero_cross_filter", False):
        if not macd_curr > 0:
            buy = False

    sell = cross_down

    filters = []
    if s.get("histogram_filter", True):
        filters.append(f"hist={macd_curr - sig_curr:.4f}")
    if s.get("zero_cross_filter", False):
        filters.append(f"MACD={macd_curr:.4f}")
    filter_str = f" | {', '.join(filters)}" if filters else ""

    buy_r = (
        f"MACD krysset over signallinje, "
        f"RSI={rsi:.1f} > {s['rsi_buy']}{filter_str}"
    )
    sell_r = f"MACD krysset under signallinje ({macd_curr:.4f} < {sig_curr:.4f})"
    return buy, buy_r, sell, sell_r


def _signal_ma_cross(rows: pd.DataFrame, s: dict) -> tuple[bool, str, bool, str]:
    ma_type = s.get("ma_type", "EMA").upper()
    prefix = ma_type.lower()
    fast_key = f"{prefix}{s['fast_ma']}"
    slow_key = f"{prefix}{s['slow_ma']}"
    confirmation = s.get("confirmation_candles", 1)

    last = rows.iloc[-1]
    rsi = float(last["rsi"])
    fast_curr = float(last[fast_key])
    slow_curr = float(last[slow_key])

    if len(rows) < confirmation + 1:
        return False, "Ikke nok data for confirmation_candles", False, ""

    # Crossover skal ha skjedd ved rows[-(confirmation+1)] → rows[-confirmation]
    before_cross = rows.iloc[-(confirmation + 1)]
    at_cross = rows.iloc[-confirmation]

    cross_up = (
        float(before_cross[fast_key]) < float(before_cross[slow_key])
        and float(at_cross[fast_key]) >= float(at_cross[slow_key])
    )
    cross_down = (
        float(before_cross[fast_key]) > float(before_cross[slow_key])
        and float(at_cross[fast_key]) <= float(at_cross[slow_key])
    )

    # Verifiser at crossover er opprettholdt frem til siste lys
    if cross_up and confirmation > 1:
        cross_up = all(
            float(rows.iloc[i][fast_key]) >= float(rows.iloc[i][slow_key])
            for i in range(-confirmation, 0)
        )
    if cross_down and confirmation > 1:
        cross_down = all(
            float(rows.iloc[i][fast_key]) <= float(rows.iloc[i][slow_key])
            for i in range(-confirmation, 0)
        )

    buy = cross_up and rsi < s["rsi_buy"]
    sell = cross_down

    conf_str = f" (bekreftet over {confirmation} lys)" if confirmation > 1 else ""
    buy_r = (
        f"{ma_type}{s['fast_ma']} ({fast_curr:,.2f}) krysset over "
        f"{ma_type}{s['slow_ma']} ({slow_curr:,.2f}), RSI={rsi:.1f} < {s['rsi_buy']}{conf_str}"
    )
    sell_r = (
        f"{ma_type}{s['fast_ma']} ({fast_curr:,.2f}) krysset under "
        f"{ma_type}{s['slow_ma']} ({slow_curr:,.2f}){conf_str}"
    )
    return buy, buy_r, sell, sell_r


def _signal_combined(rows: pd.DataFrame, s: dict) -> tuple[bool, str, bool, str]:
    last = rows.iloc[-1]
    rsi = float(last["rsi"])
    price = float(last["close"])
    ema = float(last[f"ema{s['ema_period']}"])
    bb_lower = float(last["bb_lower"])
    bb_upper = float(last["bb_upper"])

    rsi_ok = rsi < s["rsi_buy"]
    ema_ok = price > ema
    bb_ok = price < bb_lower
    all_signals = [rsi_ok, ema_ok, bb_ok]
    n_active = sum(all_signals)

    min_signals = s.get("min_signals_to_buy", 3)
    buy = n_active >= min_signals
    sell = rsi > s["rsi_sell"] or price > bb_upper

    active_desc = []
    if rsi_ok:
        active_desc.append(f"RSI={rsi:.1f}<{s['rsi_buy']}")
    if ema_ok:
        active_desc.append(f"pris>EMA{s['ema_period']}")
    if bb_ok:
        active_desc.append(f"pris<BB_lower")
    buy_r = (
        f"{n_active}/{len(all_signals)} signaler ({', '.join(active_desc) or 'ingen'}), "
        f"krever {min_signals}"
    )
    sell_r = f"RSI={rsi:.1f} > {s['rsi_sell']} eller pris {price:,.2f} > BB_upper {bb_upper:,.2f}"
    return buy, buy_r, sell, sell_r


_STRATEGY_DISPATCH = {
    "RSI_EMA":   _signal_rsi_ema,
    "BOLLINGER": _signal_bollinger,
    "MACD":      _signal_macd,
    "MA_CROSS":  _signal_ma_cross,
    "COMBINED":  _signal_combined,
}


def _dispatch_strategy(rows: pd.DataFrame, s: dict, name: str) -> tuple[bool, str, bool, str]:
    fn = _STRATEGY_DISPATCH.get(name)
    if fn is None:
        raise ValueError(
            f"Ukjent strategi '{name}'. "
            f"Gyldige: {list(_STRATEGY_DISPATCH.keys())}"
        )
    return fn(rows, s)


# ---------------------------------------------------------------------------
# Hjelpe-funksjoner
# ---------------------------------------------------------------------------

def _check_and_reset_daily_count(state: CoinState) -> None:
    """Nullstill daglig kjøpsteller dersom datoen har endret seg."""
    today = date.today().isoformat()
    if state.daily_buy_date != today:
        state.daily_buy_count = 0
        state.daily_buy_date = today


def _volume_too_low(last, trading: dict) -> bool:
    """Returner True hvis gjeldende volum er under minimumskravet (volumfilter)."""
    if not trading.get("volume_filter", True):
        return False
    vol_raw = last.get("volume")
    vol_ma_raw = last.get("volume_ma20")
    if vol_raw is None or vol_ma_raw is None:
        return False
    if pd.isna(vol_raw) or pd.isna(vol_ma_raw) or float(vol_ma_raw) == 0:
        return False
    return float(vol_raw) < trading.get("volume_multiplier", 0.8) * float(vol_ma_raw)


def _htf_confirms_buy(client: Client, symbol: str, trading: dict, s: dict) -> bool:
    """
    Sjekk om kjøpssignalet bekreftes på høyere tidsramme (RSI-sjekk).
    Returnerer True = bekreftelse OK (tillat kjøp), False = ikke bekreftet.
    Feiler åpent — hvis API-kall feiler, tillates kjøpet.
    """
    interval = trading.get("confirmation_timeframe", "1h")
    period = s.get("rsi_period", 14)
    rsi_buy = s.get("rsi_buy", 35)
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=period + 5)
        closes = pd.Series([float(k[4]) for k in klines])
        htf_rsi = float(
            ta.momentum.RSIIndicator(closes, window=period).rsi().iloc[-2]
        )
        if htf_rsi >= rsi_buy:
            logger.debug(
                f"{symbol}: HTF {interval} RSI={htf_rsi:.1f} >= {rsi_buy} – ikke i kjøpssone"
            )
            return False
        return True
    except Exception as e:
        logger.warning(f"HTF-sjekk feilet for {symbol} ({interval}): {e} – tillater kjøp")
        return True


def _get_usdt_balance(client: Client) -> float:
    balances = client.get_account()["balances"]
    for b in balances:
        if b["asset"] == "USDT":
            return float(b["free"])
    return 0.0


def _place_buy_order(client: Client, symbol: str, usdt_amount: float) -> dict | None:
    try:
        order = client.order_market_buy(symbol=symbol, quoteOrderQty=round(usdt_amount, 2))
        coin_amount = float(order["executedQty"])
        usdt_spent = float(order["cummulativeQuoteQty"])
        fill_price = usdt_spent / coin_amount if coin_amount > 0 else 0.0
        fee_usdt = sum(
            float(f["commission"]) for f in order.get("fills", [])
            if f.get("commissionAsset") == "USDT"
        )
        if fee_usdt == 0:
            fee_usdt = calculate_fee(usdt_spent)
        logger.info(f"Kjøpsordre utført: {symbol} | {coin_amount:.6f} coin @ {fill_price:,.2f}")
        return {
            "fill_price": round(fill_price, 4),
            "coin_amount": coin_amount,
            "usdt_amount": round(usdt_spent, 2),
            "fee_usdt": round(fee_usdt, 4),
        }
    except Exception as e:
        logger.error(f"Kjøpsordre feilet for {symbol}: {e}")
        return None


def _place_sell_order(client: Client, symbol: str, coin_amount: float) -> dict | None:
    precision = get_config().get("quantity_precision", {}).get(symbol, 5)
    quantity = math.floor(coin_amount * 10 ** precision) / 10 ** precision
    try:
        order = client.order_market_sell(symbol=symbol, quantity=quantity)
        usdt_received = float(order["cummulativeQuoteQty"])
        fill_price = usdt_received / quantity if quantity > 0 else 0.0
        fee_usdt = sum(
            float(f["commission"]) for f in order.get("fills", [])
            if f.get("commissionAsset") == "USDT"
        )
        if fee_usdt == 0:
            fee_usdt = calculate_fee(usdt_received)
        logger.info(f"Salgsordre utført: {symbol} | {quantity:.6f} coin @ {fill_price:,.2f}")
        return {
            "fill_price": round(fill_price, 4),
            "usdt_amount": round(usdt_received, 2),
            "fee_usdt": round(fee_usdt, 4),
        }
    except Exception as e:
        logger.error(f"Salgsordre feilet for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Hoved-evalueringsfunksjon
# ---------------------------------------------------------------------------

def evaluate(df: pd.DataFrame, state: CoinState, client: Client) -> None:
    """
    Kjør aktiv strategi på siste bekreftede lys for én mynt.
    Logger ALLE beslutninger, ikke bare faktiske handler.
    """
    cfg = _cfg()
    trading = cfg["trading"]
    safety = cfg["safety"]
    active_name = cfg["strategy"]["active"]
    s = active_strategy_cfg()

    # Siste bekreftede lys (ekskluder åpent nåværende lys = df.iloc[-1])
    last = df.iloc[-2]
    price = float(last["close"])
    symbol = state.symbol

    rsi_raw = last["rsi"]
    if pd.isna(rsi_raw):
        log_decision("VENTER", price, symbol=symbol, grunn="Ikke nok data for indikatorer")
        return

    rsi = float(rsi_raw)
    state.last_price = price
    state.last_rsi = round(rsi, 2)
    ema200_raw = last.get("ema200", float("nan"))
    state.last_ema200 = round(float(ema200_raw), 4) if not pd.isna(ema200_raw) else 0.0

    # Daglig kjøpsteller: nullstill ved midnatt
    _check_and_reset_daily_count(state)

    # Volatilitetssjekk: pause kjøp hvis siste lysestake hadde > threshold% kursendring
    if safety.get("volatility_pause", False):
        open_price = float(last["open"])
        candle_change_pct = abs(price - open_price) / open_price * 100
        threshold = float(safety.get("volatility_threshold", 10.0))
        if candle_change_pct >= threshold:
            if not state.volatility_paused:
                logger.warning(
                    f"{symbol}: Volatilitetssjekk – lys-endring {candle_change_pct:.2f}% "
                    f">= {threshold:.1f}%. Pauserer kjøp."
                )
            state.volatility_paused = True
        else:
            state.volatility_paused = False

    # Bygg rader for strategi-funksjoner (bekreftede lys, ekskluder åpent lys)
    # Window: confirmation_candles + 2 gir nok rader for crossover-sjekk
    confirmation = s.get("confirmation_candles", 1)
    window = confirmation + 2
    rows = df.iloc[-window:-1]

    # Hent signaler fra aktiv strategi
    try:
        buy_sig, buy_reason, sell_sig, sell_reason = _dispatch_strategy(rows, s, active_name)
    except Exception as e:
        log_decision("VENTER", price, symbol=symbol, grunn=f"Strategifeil: {e}")
        return

    # --- Sikkerhetssjekker for åpne posisjoner (alltid aktive) ---
    if state.in_position:
        avg = state.avg_buy_price
        pct = (price - avg) / avg

        # Oppdater trailing peak-pris
        if trading.get("trailing_stop_loss", False):
            if state.trailing_peak_price == 0.0:
                state.trailing_peak_price = price
            else:
                state.trailing_peak_price = max(state.trailing_peak_price, price)

        # Bestem stoploss-trigger (trailing > dynamisk ATR > fast)
        stop_triggered = False
        stop_reason = ""
        if trading.get("trailing_stop_loss", False) and state.trailing_peak_price > 0:
            trail_pct = trading.get("trailing_stop_loss_pct", 1.0) / 100.0
            trail_price = state.trailing_peak_price * (1 - trail_pct)
            if price <= trail_price:
                stop_triggered = True
                stop_reason = (
                    f"TRAILING STOPLOSS utløst (pris {price:,.2f} < "
                    f"{trail_price:,.2f}, topp {state.trailing_peak_price:,.2f})"
                )
        elif trading.get("dynamic_stop_loss", False):
            atr_raw = last.get("atr", float("nan"))
            if not pd.isna(atr_raw) and float(atr_raw) > 0:
                stop_dist = float(atr_raw) * trading.get("atr_multiplier", 1.5)
                if (avg - price) >= stop_dist:
                    stop_triggered = True
                    stop_reason = (
                        f"STOPLOSS (ATR) utløst – ATR={float(atr_raw):.2f} × "
                        f"{trading['atr_multiplier']} = {stop_dist:.2f} avstand"
                    )
        else:
            if pct <= -trading["stop_loss_pct"]:
                stop_triggered = True
                stop_reason = f"STOPLOSS utløst ({pct*100:.2f}% fra snitt {avg:,.2f})"

        if stop_triggered:
            _sell(state, price, stop_reason, client)
            return

        if pct >= trading["take_profit_pct"]:
            _sell(state, price,
                  f"TAKEPROFIT utløst ({pct*100:.2f}% fra snitt {avg:,.2f})", client)
            return

        # Strategi-salgssignal
        if sell_sig:
            if is_profitable(avg, price, state.total_usdt_invested):
                _sell(state, price, sell_reason, client)
            else:
                log_decision("VENTER", price, symbol=symbol,
                             grunn=f"{sell_reason} – ikke lønnsomt etter fees, holder")
            return

        # Ingen salgssignal — holder (DCA-kjøp kan fortsatt skje nedenfor)

    # --- Kjøpssignal (kjører også ved DCA på eksisterende posisjon) ---
    if buy_sig and not sell_sig:
        max_daily = int(safety.get("max_daily_trades", 0))
        if state.dca_count >= trading["max_dca"]:
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Maks DCA ({trading['max_dca']}) nådd – ingen ny kjøpsordre")
        elif time.time() < state.stop_loss_cooldown_until:
            remaining = (state.stop_loss_cooldown_until - time.time()) / 60
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Stoploss-cooldown aktiv – {remaining:.0f} min igjen")
        elif state.volatility_paused:
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Volatilitetssjekk: kjøp pauset (lys-endring over "
                               f"{safety.get('volatility_threshold', 10.0):.1f}%)")
        elif max_daily > 0 and state.daily_buy_count >= max_daily:
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Maks daglige handler ({max_daily}) nådd for {symbol}")
        elif _volume_too_low(last, trading):
            vol = float(last["volume"])
            vol_ma = float(last.get("volume_ma20") or 0)
            log_decision("VENTER", price, symbol=symbol,
                         grunn=(f"Volumfilter: {vol:.0f} < "
                                f"{trading.get('volume_multiplier', 0.8)}x snitt {vol_ma:.0f}"))
        elif trading.get("multi_timeframe", False) and not _htf_confirms_buy(client, symbol, trading, s):
            htf = trading.get("confirmation_timeframe", "1h")
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Multi-timeframe: ingen kjøpsbekreftelse på {htf}")
        else:
            usdt_balance = _get_usdt_balance(client)
            usable = usdt_balance - trading["capital_reserve"]
            if usable < trading["trade_usdt"]:
                grunn = (
                    f"KAPITALVERN – saldo {usdt_balance:,.2f} USDT, "
                    f"tilgjengelig over reserve: {usable:,.2f} USDT"
                )
                logger.warning(grunn)
                log_decision("VENTER", price, symbol=symbol, grunn=grunn)
            else:
                _buy(state, price, buy_reason, client, trading)
    else:
        # Ingen kjøps- eller salgssignal
        if state.in_position:
            avg = state.avg_buy_price
            pct = (price - avg) / avg
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Holder {state.dca_count} pos – RSI={rsi:.1f}, endring={pct*100:.2f}%")
        else:
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Ingen signal – RSI={rsi:.1f} (aktiv: {active_name})")


def _buy(
    state: CoinState,
    price: float,
    reason: str,
    client: Client,
    trading: dict,
) -> None:
    dca_level = state.dca_count + 1
    trade_amount = min(trading["trade_usdt"], _get_usdt_balance(client) - trading["capital_reserve"])

    order = _place_buy_order(client, state.symbol, trade_amount)
    if order is None:
        log_decision("VENTER", price, symbol=state.symbol,
                     grunn="Kjøpsordre til Binance feilet – se feillogg")
        return

    fill_price = order["fill_price"]
    min_sell = minimum_sell_price(fill_price)
    grunn = (
        f"{reason} | DCA#{dca_level} | "
        f"{order['usdt_amount']:,.2f} USDT | "
        f"Min salgspris: {min_sell:,.2f}"
    )
    log_decision(
        "KJØP", fill_price,
        symbol=state.symbol,
        mengde_coin=order["coin_amount"],
        beløp_usdt=order["usdt_amount"],
        fee_usdt=order["fee_usdt"],
        grunn=grunn,
        dca_level=dca_level,
    )
    state.positions.append(Position(
        buy_price=fill_price,
        coin_amount=order["coin_amount"],
        usdt_amount=order["usdt_amount"],
        dca_level=dca_level,
    ))
    state.daily_buy_count += 1
    state.daily_buy_date = date.today().isoformat()
    if dca_level == 1:
        state.trailing_peak_price = fill_price


def _sell(state: CoinState, price: float, reason: str, client: Client) -> None:
    trading = _trading()
    total_coin = state.total_coin_amount
    total_usdt = state.total_usdt_invested
    avg_price = state.avg_buy_price
    dca_count = state.dca_count
    symbol = state.symbol

    order = _place_sell_order(client, symbol, total_coin)
    if order is None:
        logger.error(f"Salgsordre feilet for {symbol} – beholder posisjon, prøver igjen neste syklus")
        return

    fill_price = order["fill_price"]
    gevinst = net_profit(avg_price, fill_price, total_usdt)
    pct = profit_percent(avg_price, fill_price)

    log_decision(
        "SELG", fill_price,
        symbol=symbol,
        mengde_coin=total_coin,
        beløp_usdt=order["usdt_amount"],
        fee_usdt=order["fee_usdt"],
        grunn=reason,
        gevinst_usdt=gevinst,
        gevinst_prosent=pct,
        dca_level=dca_count,
    )
    state.positions.clear()
    state.trailing_peak_price = 0.0

    if "STOPLOSS" in reason:
        cooldown_min = trading["cooldown_after_stoploss_min"]
        state.stop_loss_cooldown_until = time.time() + cooldown_min * 60
        logger.info(
            f"Stoploss-cooldown aktivert for {symbol} – "
            f"ingen kjøp de neste {cooldown_min} minuttene."
        )
