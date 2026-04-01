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
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
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

    # --- Bollinger Bands ---
    bb_period = s.get("period", s.get("bb_period", 20))
    bb_std = float(s.get("std_dev", s.get("bb_std", 2.0)))
    bb = ta.volatility.BollingerBands(df["close"], window=bb_period, window_dev=bb_std)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()

    # --- MACD ---
    macd_fast = s.get("fast", s.get("macd_fast", 12))
    macd_slow = s.get("slow", s.get("macd_slow", 26))
    macd_sig = s.get("signal", s.get("macd_signal", 9))
    macd_ind = ta.trend.MACDIndicator(
        df["close"],
        window_fast=macd_fast,
        window_slow=macd_slow,
        window_sign=macd_sig,
    )
    df["macd"] = macd_ind.macd()
    df["macd_signal"] = macd_ind.macd_signal()

    return df


# ---------------------------------------------------------------------------
# Strategi-signalfunksjoner
# Returnerer: (buy_signal, buy_reason, sell_signal, sell_reason)
# last = df.iloc[-2] (siste bekreftede lys), prev = df.iloc[-3] (forrige lys)
# ---------------------------------------------------------------------------

def _signal_rsi_ema(last, prev, s: dict) -> tuple[bool, str, bool, str]:
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


def _signal_bollinger(last, prev, s: dict) -> tuple[bool, str, bool, str]:
    rsi = float(last["rsi"])
    price = float(last["close"])
    bb_lower = float(last["bb_lower"])
    bb_upper = float(last["bb_upper"])

    buy = price < bb_lower and rsi < s["rsi_buy"]
    sell = price > bb_upper

    buy_r = (
        f"RSI={rsi:.1f} < {s['rsi_buy']}, "
        f"pris {price:,.2f} < BB_lower {bb_lower:,.2f}"
    )
    sell_r = f"Pris {price:,.2f} > BB_upper {bb_upper:,.2f}"
    return buy, buy_r, sell, sell_r


def _signal_macd(last, prev, s: dict) -> tuple[bool, str, bool, str]:
    rsi = float(last["rsi"])
    macd_curr = float(last["macd"])
    sig_curr = float(last["macd_signal"])
    macd_prev = float(prev["macd"])
    sig_prev = float(prev["macd_signal"])

    cross_up = (macd_prev < sig_prev) and (macd_curr >= sig_curr)
    cross_down = (macd_prev > sig_prev) and (macd_curr <= sig_curr)

    buy = cross_up and rsi > s["rsi_buy"]
    sell = cross_down

    buy_r = (
        f"MACD krysset over signallinje, "
        f"RSI={rsi:.1f} > {s['rsi_buy']} (momentumbekreftelse)"
    )
    sell_r = f"MACD krysset under signallinje ({macd_curr:.4f} < {sig_curr:.4f})"
    return buy, buy_r, sell, sell_r


def _signal_ma_cross(last, prev, s: dict) -> tuple[bool, str, bool, str]:
    rsi = float(last["rsi"])
    fast_key = f"ema{s['fast_ma']}"
    slow_key = f"ema{s['slow_ma']}"
    fast_curr = float(last[fast_key])
    slow_curr = float(last[slow_key])
    fast_prev = float(prev[fast_key])
    slow_prev = float(prev[slow_key])

    cross_up = (fast_prev < slow_prev) and (fast_curr >= slow_curr)
    cross_down = (fast_prev > slow_prev) and (fast_curr <= slow_curr)

    buy = cross_up and rsi < s["rsi_buy"]
    sell = cross_down

    buy_r = (
        f"EMA{s['fast_ma']} ({fast_curr:,.2f}) krysset over "
        f"EMA{s['slow_ma']} ({slow_curr:,.2f}), RSI={rsi:.1f} < {s['rsi_buy']}"
    )
    sell_r = (
        f"EMA{s['fast_ma']} ({fast_curr:,.2f}) krysset under "
        f"EMA{s['slow_ma']} ({slow_curr:,.2f})"
    )
    return buy, buy_r, sell, sell_r


def _signal_combined(last, prev, s: dict) -> tuple[bool, str, bool, str]:
    rsi = float(last["rsi"])
    price = float(last["close"])
    ema = float(last[f"ema{s['ema_period']}"])
    bb_lower = float(last["bb_lower"])
    bb_upper = float(last["bb_upper"])

    buy = rsi < s["rsi_buy"] and price > ema and price < bb_lower
    sell = rsi > s["rsi_sell"] or price > bb_upper

    buy_r = (
        f"RSI={rsi:.1f} < {s['rsi_buy']}, "
        f"pris {price:,.2f} > EMA{s['ema_period']} {ema:,.2f} "
        f"og < BB_lower {bb_lower:,.2f}"
    )
    sell_r = f"RSI={rsi:.1f} > {s['rsi_sell']} eller pris {price:,.2f} > BB_upper {bb_upper:,.2f}"
    return buy, buy_r, sell, sell_r


_STRATEGY_DISPATCH = {
    "RSI_EMA":  _signal_rsi_ema,
    "BOLLINGER": _signal_bollinger,
    "MACD":     _signal_macd,
    "MA_CROSS": _signal_ma_cross,
    "COMBINED": _signal_combined,
}


def _dispatch_strategy(last, prev, s: dict, name: str) -> tuple[bool, str, bool, str]:
    fn = _STRATEGY_DISPATCH.get(name)
    if fn is None:
        raise ValueError(
            f"Ukjent strategi '{name}'. "
            f"Gyldige: {list(_STRATEGY_DISPATCH.keys())}"
        )
    return fn(last, prev, s)


# ---------------------------------------------------------------------------
# Ordre-hjelpere
# ---------------------------------------------------------------------------

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
    active_name = cfg["strategy"]["active"]
    s = active_strategy_cfg()

    last = df.iloc[-2]
    prev = df.iloc[-3]
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

    # Hent signaler fra aktiv strategi
    try:
        buy_sig, buy_reason, sell_sig, sell_reason = _dispatch_strategy(last, prev, s, active_name)
    except Exception as e:
        log_decision("VENTER", price, symbol=symbol, grunn=f"Strategifeil: {e}")
        return

    # --- Sikkerhetssjekker for åpne posisjoner (alltid aktive) ---
    if state.in_position:
        avg = state.avg_buy_price
        pct = (price - avg) / avg

        if pct <= -trading["stop_loss_pct"]:
            _sell(state, price,
                  f"STOPLOSS utløst ({pct*100:.2f}% fra snitt {avg:,.2f})", client)
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
        if state.dca_count >= trading["max_dca"]:
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Maks DCA ({trading['max_dca']}) nådd – ingen ny kjøpsordre")
        elif time.time() < state.stop_loss_cooldown_until:
            remaining = (state.stop_loss_cooldown_until - time.time()) / 60
            log_decision("VENTER", price, symbol=symbol,
                         grunn=f"Stoploss-cooldown aktiv – {remaining:.0f} min igjen")
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

    if "STOPLOSS" in reason:
        cooldown_sec = trading.get("stop_loss_cooldown_minutes",
                                   get_config()["safety"]["stop_loss_cooldown_minutes"]) * 60
        state.stop_loss_cooldown_until = time.time() + cooldown_sec
        logger.info(
            f"Stoploss-cooldown aktivert for {symbol} – "
            f"ingen kjøp de neste {int(cooldown_sec // 60)} minuttene."
        )
