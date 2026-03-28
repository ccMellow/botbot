"""
fee_calculator.py
Fee-logikk og lønnsomhetssjekk for Binance Spot.
Standard taker/maker-fee: 0.10%
"""

FEE_RATE = 0.001  # 0.10%


def calculate_fee(amount_usdt: float) -> float:
    """Returner fee i USDT for en gitt handlestørrelse."""
    return amount_usdt * FEE_RATE


def round_trip_fee(amount_usdt: float) -> float:
    """Total fee for kjøp + salg (to handler)."""
    return calculate_fee(amount_usdt) * 2


def is_profitable(buy_price: float, sell_price: float, amount_usdt: float) -> bool:
    """
    Sjekk om en potensiell handel er lønnsom etter fees.
    amount_usdt: størrelse på handelen i USDT
    """
    gross_profit = (sell_price - buy_price) / buy_price * amount_usdt
    total_fees = round_trip_fee(amount_usdt)
    return gross_profit > total_fees


def net_profit(buy_price: float, sell_price: float, amount_usdt: float) -> float:
    """Beregn netto gevinst/tap i USDT etter fees."""
    gross = (sell_price - buy_price) / buy_price * amount_usdt
    fees = round_trip_fee(amount_usdt)
    return gross - fees


def profit_percent(buy_price: float, sell_price: float) -> float:
    """Prosentvis prisendring fra kjøp til salg."""
    return (sell_price - buy_price) / buy_price * 100


def minimum_sell_price(buy_price: float) -> float:
    """Laveste salgspris som gir positiv netto etter round-trip fees."""
    # gross_profit > 2 * fee_rate * amount => sell_price > buy_price * (1 + 2*FEE_RATE)
    return buy_price * (1 + 2 * FEE_RATE)
