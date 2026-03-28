"""
check_balance.py
Kobler til Binance Spot Testnet og printer nåværende saldo.
Kjør med: python check_balance.py
"""

import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

def main():
    api_key = os.getenv("BINANCE_API_KEY")
    secret = os.getenv("BINANCE_SECRET_KEY")

    if not api_key or not secret:
        print("Feil: BINANCE_API_KEY og BINANCE_SECRET_KEY mangler i .env")
        return

    client = Client(api_key, secret, testnet=True)

    print("Kobler til Binance Spot Testnet...")
    balances = client.get_account()["balances"]

    # Filtrer bort tomme saldoer
    active = [b for b in balances if float(b["free"]) > 0 or float(b["locked"]) > 0]

    if not active:
        print("Ingen saldo funnet på testnet-kontoen.")
        return

    print(f"\n{'Valuta':<10} {'Tilgjengelig':>16} {'Låst':>16}")
    print("-" * 44)
    for b in active:
        print(f"{b['asset']:<10} {float(b['free']):>16.6f} {float(b['locked']):>16.6f}")

if __name__ == "__main__":
    main()
