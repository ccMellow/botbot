"""
strategy.py
Trading-logikk basert på RSI + EMA-kryssing for BTC/USDT.

Strategi:
- Kjøp når RSI < 35 OG pris over EMA200
- Selg når RSI > 65 ELLER stoploss/takeprofit utløses
- Stoploss: -2% fra kjøpspris
- Takeprofit: +4% fra kjøpspris
- Sjekker alltid om handel er lønnsom etter fees før ordre sendes
"""

import os
import time
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

SYMBOL = "BTCUSDT"
INTERVAL = Client.KLINE_INTERVAL_15MINUTE
CANDLES = 250          # Nok data for EMA200
TRADE_USDT = 100.0     # Handlestørrelse i USDT
STOP_LOSS_PCT = 0.02   # 2%
TAKE_PROFIT_PCT = 0.04 # 4%
RSI_BUY = 35
RSI_SELL = 65


def get_client() -> Client:
    api_key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_SECRET_KEY")
    client = Client(api_key, secret, testnet=True)
    return client


def fetch_candles(client: Client) -> pd.DataFrame:
    klines = client.get_klines(symbol=SYMBOL, interval=INTERVAL, limit=CANDLES)
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


class BotState:
    """Holder styr på åpen posisjon mellom iterasjoner."""
    def __init__(self):
        self.in_position = False
        self.buy_price = 0.0
        self.btc_amount = 0.0


def evaluate(df: pd.DataFrame, state: BotState) -> None:
    """
    Kjør strategilogikk på siste komplette stearinlys.
    Logger ALLE beslutninger, ikke bare faktiske handler.
    """
    last = df.iloc[-2]  # Siste *lukkede* stearinlys
    price = float(last["close"])
    rsi = float(last["rsi"])
    ema200 = float(last["ema200"])

    if pd.isna(rsi) or pd.isna(ema200):
        log_decision("VENTER", price, grunn="Ikke nok data for indikatorer")
        return

    if not state.in_position:
        # --- Kjøpssignal ---
        if rsi < RSI_BUY and price > ema200:
            btc_amount = TRADE_USDT / price
            fee = calculate_fee(TRADE_USDT)
            min_sell = minimum_sell_price(price)
            grunn = (
                f"RSI={rsi:.1f} < {RSI_BUY}, pris {price:,.2f} > EMA200 {ema200:,.2f}"
                f" | Min salgspris: {min_sell:,.2f}"
            )
            log_decision(
                "KJØP", price,
                mengde_btc=btc_amount,
                beløp_usdt=TRADE_USDT,
                fee_usdt=fee,
                grunn=grunn,
            )
            state.in_position = True
            state.buy_price = price
            state.btc_amount = btc_amount
        else:
            grunn = (
                f"VENTER – RSI={rsi:.1f} (trenger < {RSI_BUY})"
                if rsi >= RSI_BUY
                else f"VENTER – pris {price:,.2f} under EMA200 {ema200:,.2f}"
            )
            log_decision("VENTER", price, grunn=grunn)

    else:
        # --- Salgs- / stoploss / takeprofit-sjekk ---
        pct_change = (price - state.buy_price) / state.buy_price

        if pct_change <= -STOP_LOSS_PCT:
            reason = f"STOPLOSS utløst ({pct_change*100:.2f}%)"
            _sell(state, price, reason)

        elif pct_change >= TAKE_PROFIT_PCT:
            reason = f"TAKEPROFIT utløst ({pct_change*100:.2f}%)"
            _sell(state, price, reason)

        elif rsi > RSI_SELL:
            if is_profitable(state.buy_price, price, TRADE_USDT):
                reason = f"RSI={rsi:.1f} > {RSI_SELL}, lønnsomt salg"
                _sell(state, price, reason)
            else:
                grunn = (
                    f"RSI={rsi:.1f} > {RSI_SELL} men handel ikke lønnsom etter fees"
                    f" – holder posisjon"
                )
                log_decision("VENTER", price, grunn=grunn)
        else:
            grunn = (
                f"Holder posisjon – RSI={rsi:.1f}, "
                f"endring={pct_change*100:.2f}%"
            )
            log_decision("VENTER", price, grunn=grunn)


def _sell(state: BotState, price: float, reason: str) -> None:
    gevinst = net_profit(state.buy_price, price, TRADE_USDT)
    pct = profit_percent(state.buy_price, price)
    fee = calculate_fee(state.btc_amount * price)

    log_decision(
        "SELG", price,
        mengde_btc=state.btc_amount,
        beløp_usdt=state.btc_amount * price,
        fee_usdt=fee,
        grunn=reason,
        gevinst_usdt=gevinst,
        gevinst_prosent=pct,
    )
    state.in_position = False
    state.buy_price = 0.0
    state.btc_amount = 0.0
