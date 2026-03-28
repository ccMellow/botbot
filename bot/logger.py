"""
logger.py
CSV og tekstlogging av alle beslutninger og handler.
"""

import csv
import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
CSV_FILE = os.path.join(LOG_DIR, "trades.csv")
TEXT_FILE = os.path.join(LOG_DIR, "trades.log")

CSV_HEADERS = [
    "tidspunkt",
    "handling",
    "pris",
    "mengde_btc",
    "beløp_usdt",
    "fee_usdt",
    "grunn",
    "gevinst_usdt",
    "gevinst_prosent",
]


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def _ensure_csv_headers():
    _ensure_log_dir()
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()


def log_decision(
    handling: str,
    pris: float,
    mengde_btc: float = 0.0,
    beløp_usdt: float = 0.0,
    fee_usdt: float = 0.0,
    grunn: str = "",
    gevinst_usdt: float = 0.0,
    gevinst_prosent: float = 0.0,
):
    """
    Logg en handelsbeslutning til både CSV og tekstfil.
    handling: 'KJØP', 'SELG', eller 'VENTER'
    """
    _ensure_csv_headers()
    tidspunkt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = {
        "tidspunkt": tidspunkt,
        "handling": handling,
        "pris": round(pris, 2),
        "mengde_btc": round(mengde_btc, 6),
        "beløp_usdt": round(beløp_usdt, 2),
        "fee_usdt": round(fee_usdt, 4),
        "grunn": grunn,
        "gevinst_usdt": round(gevinst_usdt, 2),
        "gevinst_prosent": round(gevinst_prosent, 4),
    }

    # CSV-logg
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writerow(row)

    # Tekstlogg
    _ensure_log_dir()
    with open(TEXT_FILE, "a", encoding="utf-8") as f:
        gevinst_str = ""
        if handling == "SELG":
            tegn = "+" if gevinst_usdt >= 0 else ""
            gevinst_str = f" | Resultat: {tegn}{gevinst_usdt:.2f} USDT ({tegn}{gevinst_prosent:.3f}%)"

        f.write(
            f"[{tidspunkt}] {handling:6s} | Pris: {pris:,.2f} USDT"
            f" | BTC: {mengde_btc:.6f} | Fee: {fee_usdt:.4f} USDT"
            f" | {grunn}{gevinst_str}\n"
        )


def get_last_n_trades(n: int = 50) -> list[dict]:
    """Les de siste n radene fra CSV-loggen."""
    _ensure_csv_headers()
    rows = []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows[-n:]
