"""
strategy.py
Multi-coin trading med DCA for BTC, ETH og SOL mot USDT.

Strategi per mynt:
- Kjøp når RSI < 35 OG pris over EMA200 (eller TEST_MODE)
- DCA: tillat opptil 3 kjøp per mynt, 100 USDT per kjøp
- Selg alle posisjoner for mynten når RSI > 65 ELLER stoploss/takeprofit utløses
- Stoploss: -2% fra gjennomsnittlig kjøpspris
- Takeprofit: +4% fra gjennomsnittlig kjøpspris
- Kapitalreserve: 7000 USDT deles mellom alle mynter
- Ordrer sendes til Binance Spot Testnet API (market orders)
"""

import logging
import math
import os
import time
from dataclasses import dataclass
from dotenv import load_dotenv
from binance.client import Client
import pandas as pd
import ta

from bot.fee_calculator import (
    calculate_fee,
    is_profitable,
    net_profit,
    profit_percent,
    minimum_sell_price,
)
from bot.logger import log_decision

load_dotenv()

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = Client.KLINE_INTERVAL_15MINUTE
CANDLES = 250
TRADE_USDT = 100.0
CAPITAL_RESERVE = 7000.0
MAX_DCA = 3
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04
RSI_BUY = 55 if TEST_MODE else 35
RSI_SELL = 65

# Antall desimaler for coin-mengde ved salgsordrer (trunkeres, ikke rundes)
QUANTITY_PRECISION = {"BTCUSDT": 5, "ETHUSDT": 4, "SOLUSDT": 2}

logger = logging.getLogger(__name__)

if TEST_MODE:
    logger.warning("TEST_MODE er aktivt – RSI_BUY=55 (normalt 35), EMA200-filter deaktivert")


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


TESTNET = True  # Sett til False ved overgang til live trading


def get_client() -> Client:
    api_key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_SECRET_KEY")
    client = Client(api_key, secret, testnet=TESTNET)
    return client


def fetch_candles(client: Client, symbol: str) -> pd.DataFrame:
    klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=CANDLES)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    df["ema200"] = ta.trend.EMAIndicator(df["close"], window=200).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()
    return df


def _get_usdt_balance(client: Client) -> float:
    balances = client.get_account()["balances"]
    for b in balances:
        if b["asset"] == "USDT":
            return float(b["free"])
    return 0.0


def _place_buy_order(client: Client, symbol: str, usdt_amount: float) -> dict | None:
    """
    Send markedskjøpsordre til Binance. Bruker quoteOrderQty (kjøp for X USDT).
    Returnerer faktiske fyllingsdata, eller None ved feil.
    """
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
    """
    Send markedssalgsordre til Binance. Trunkerer coin-mengde til gyldig presisjon.
    Returnerer faktiske fyllingsdata, eller None ved feil.
    """
    precision = QUANTITY_PRECISION.get(symbol, 5)
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


def evaluate(df: pd.DataFrame, state: CoinState, client: Client) -> None:
    """
    Kjør strategilogikk på siste komplette stearinlys for én mynt.
    Logger ALLE beslutninger, ikke bare faktiske handler.
    """
    last = df.iloc[-2]
    price = float(last["close"])
    rsi = float(last["rsi"])
    ema200 = float(last["ema200"])
    symbol = state.symbol

    if pd.isna(rsi) or pd.isna(ema200):
        log_decision("VENTER", price, symbol=symbol, grunn="Ikke nok data for indikatorer")
        return

    state.last_price = price
    state.last_rsi = round(rsi, 2)
    state.last_ema200 = round(ema200, 4)

    # --- Kjøpssignal ---
    if rsi < RSI_BUY and (TEST_MODE or price > ema200):
        if state.dca_count >= MAX_DCA:
            grunn = f"Maks DCA-nivå ({MAX_DCA}) nådd for {symbol} – ingen ny kjøpsordre"
            log_decision("VENTER", price, symbol=symbol, grunn=grunn)
        elif time.time() < state.stop_loss_cooldown_until:
            remaining = (state.stop_loss_cooldown_until - time.time()) / 60
            grunn = f"Stoploss-cooldown aktiv – {remaining:.0f} min igjen før kjøp tillates"
            log_decision("VENTER", price, symbol=symbol, grunn=grunn)
        else:
            usdt_balance = _get_usdt_balance(client)
            usable_usdt = usdt_balance - CAPITAL_RESERVE

            if usable_usdt < TRADE_USDT:
                grunn = (
                    f"KAPITALVERN – saldo {usdt_balance:,.2f} USDT, "
                    f"tilgjengelig over reserve: {usable_usdt:,.2f} USDT. Ingen kjøp."
                )
                logger.warning(grunn)
                log_decision("VENTER", price, symbol=symbol, grunn=grunn)
            else:
                trade_amount = min(TRADE_USDT, usable_usdt)
                dca_level = state.dca_count + 1

                order = _place_buy_order(client, symbol, trade_amount)
                if order is None:
                    log_decision("VENTER", price, symbol=symbol,
                                 grunn=f"Kjøpsordre til Binance feilet – se feillogg")
                else:
                    fill_price = order["fill_price"]
                    min_sell = minimum_sell_price(fill_price)
                    grunn = (
                        f"RSI={rsi:.1f} < {RSI_BUY}, DCA#{dca_level}"
                        + ("" if TEST_MODE else f", pris {fill_price:,.2f} > EMA200 {ema200:,.2f}")
                        + f" | Handelsbeløp: {order['usdt_amount']:,.2f} USDT"
                        + f" | Min salgspris: {min_sell:,.2f}"
                    )
                    log_decision(
                        "KJØP", fill_price,
                        symbol=symbol,
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

    # --- Salgs- / stoploss / takeprofit-sjekk ---
    if state.in_position:
        avg_price = state.avg_buy_price
        pct_change = (price - avg_price) / avg_price

        if pct_change <= -STOP_LOSS_PCT:
            reason = f"STOPLOSS utløst ({pct_change*100:.2f}% fra snitt {avg_price:,.2f})"
            _sell(state, price, reason, client)

        elif pct_change >= TAKE_PROFIT_PCT:
            reason = f"TAKEPROFIT utløst ({pct_change*100:.2f}% fra snitt {avg_price:,.2f})"
            _sell(state, price, reason, client)

        elif rsi > RSI_SELL:
            if is_profitable(avg_price, price, state.total_usdt_invested):
                reason = f"RSI={rsi:.1f} > {RSI_SELL}, lønnsomt salg (snitt {avg_price:,.2f})"
                _sell(state, price, reason, client)
            else:
                grunn = (
                    f"RSI={rsi:.1f} > {RSI_SELL} men handel ikke lønnsom etter fees – holder"
                )
                log_decision("VENTER", price, symbol=symbol, grunn=grunn)

        elif not (rsi < RSI_BUY and (TEST_MODE or price > ema200)):
            grunn = (
                f"Holder {state.dca_count} posisjon(er) for {symbol} – "
                f"RSI={rsi:.1f}, endring={pct_change*100:.2f}% fra snitt"
            )
            log_decision("VENTER", price, symbol=symbol, grunn=grunn)


def _sell(state: CoinState, price: float, reason: str, client: Client) -> None:
    total_coin = state.total_coin_amount
    total_usdt = state.total_usdt_invested
    avg_price = state.avg_buy_price
    dca_count = state.dca_count
    symbol = state.symbol

    order = _place_sell_order(client, symbol, total_coin)
    if order is None:
        # Ikke tøm state — prøv igjen neste syklus
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
        state.stop_loss_cooldown_until = time.time() + 30 * 60
        logger.info(
            f"Stoploss-cooldown aktivert for {symbol} – "
            f"ingen kjøp de neste 30 minuttene."
        )
