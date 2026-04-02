"""
performance_report.py
Genererer ukentlig ytelsesrapport og skriver til logs/performance_report.txt.
Kalles automatisk hver mandag kl. 08:00 fra main.py.
"""

import csv
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

LOG_DIR     = os.path.join(os.path.dirname(__file__), "..", "logs")
CSV_FILE    = os.path.join(LOG_DIR, "trades.csv")
REPORT_FILE = os.path.join(LOG_DIR, "performance_report.txt")


def _read_csv_rows() -> list[dict]:
    if not os.path.exists(CSV_FILE):
        return []
    try:
        with open(CSV_FILE, encoding="utf-8", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    except Exception as e:
        logger.error(f"Feil ved lesing av trades.csv: {e}")
        return []


def _compute_symbol_stats(rows: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for row in rows:
        sym = row.get("symbol", "")
        if not sym:
            continue
        if sym not in stats:
            stats[sym] = {"pnl": 0.0, "sell_count": 0, "wins": 0, "fees": 0.0}
        if row.get("handling") == "SELG":
            pnl = float(row.get("gevinst_usdt") or 0)
            stats[sym]["pnl"] += pnl
            stats[sym]["sell_count"] += 1
            if pnl > 0:
                stats[sym]["wins"] += 1
        if row.get("handling") in ("KJØP", "SELG"):
            stats[sym]["fees"] += float(row.get("fee_usdt") or 0)
    return stats


def _compute_hold_times(rows: list[dict]) -> dict[str, list[float]]:
    """Beregn holdetider i minutter per symbol (fra DCA#1-kjøp til salg)."""
    by_sym: dict[str, list[dict]] = {}
    for row in rows:
        sym = row.get("symbol", "")
        by_sym.setdefault(sym, []).append(row)

    hold_times: dict[str, list[float]] = {}
    for sym, sym_rows in by_sym.items():
        times: list[float] = []
        for i, row in enumerate(sym_rows):
            if row.get("handling") != "SELG":
                continue
            for j in range(i - 1, -1, -1):
                if (sym_rows[j].get("handling") == "KJØP"
                        and str(sym_rows[j].get("dca_level", "1")) == "1"):
                    try:
                        t_sell = datetime.fromisoformat(row["tidspunkt"])
                        t_buy  = datetime.fromisoformat(sym_rows[j]["tidspunkt"])
                        times.append((t_sell - t_buy).total_seconds() / 60)
                    except Exception:
                        pass
                    break
        hold_times[sym] = times
    return hold_times


def _fmt_minutes(minutes: float) -> str:
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h}t {m}m" if h > 0 else f"{m}m"


def generate_weekly_report() -> None:
    """Skriv ukentlig ytelsesrapport til logs/performance_report.txt."""
    try:
        all_rows = _read_csv_rows()
        now        = datetime.now()
        week_start = now - timedelta(days=7)

        week_rows = [
            r for r in all_rows
            if r.get("tidspunkt")
            and datetime.fromisoformat(r["tidspunkt"]) >= week_start
        ]

        sell_rows  = [r for r in week_rows if r.get("handling") == "SELG"]
        total_pnl  = sum(float(r.get("gevinst_usdt") or 0) for r in sell_rows)
        total_fees = sum(
            float(r.get("fee_usdt") or 0)
            for r in week_rows if r.get("handling") in ("KJØP", "SELG")
        )
        total_wins    = sum(1 for r in sell_rows if float(r.get("gevinst_usdt") or 0) > 0)
        total_win_rate = (total_wins / len(sell_rows) * 100) if sell_rows else 0.0

        sym_stats  = _compute_symbol_stats(week_rows)
        hold_times = _compute_hold_times(week_rows)

        best_trade  = max(sell_rows, key=lambda r: float(r.get("gevinst_usdt") or 0), default=None)
        worst_trade = min(sell_rows, key=lambda r: float(r.get("gevinst_usdt") or 0), default=None)

        lines = [
            "=" * 62,
            "  UKENTLIG YTELSESRAPPORT",
            "=" * 62,
            f"  Generert:  {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Periode:   {week_start.strftime('%Y-%m-%d')} – {now.strftime('%Y-%m-%d')}",
            "",
            "TOTAL YTELSE",
            f"  P&L siste 7 dager:    {'+' if total_pnl >= 0 else ''}{total_pnl:.2f} USDT",
            f"  Antall salg:          {len(sell_rows)}",
            f"  Samlet vinnrate:      {total_win_rate:.1f}%",
            f"  Totale fees betalt:   {total_fees:.4f} USDT",
            "",
            "PER MYNT",
        ]

        for sym, st in sorted(sym_stats.items()):
            wr     = (st["wins"] / st["sell_count"] * 100) if st["sell_count"] > 0 else 0.0
            ht_list = hold_times.get(sym, [])
            avg_ht  = _fmt_minutes(sum(ht_list) / len(ht_list)) if ht_list else "–"
            pnl_str = f"{'+' if st['pnl'] >= 0 else ''}{st['pnl']:.2f} USDT"
            lines.append(
                f"  {sym:<12}  P&L: {pnl_str:<16}"
                f"  Salg: {st['sell_count']:>3}  Vinnrate: {wr:>5.1f}%"
                f"  Gj.snitt holdetid: {avg_ht}"
            )

        lines.append("")

        if best_trade:
            pnl_b = float(best_trade.get("gevinst_usdt") or 0)
            pct_b = float(best_trade.get("gevinst_prosent") or 0)
            lines.append(
                f"BESTE HANDEL:      {best_trade.get('symbol', '?')}  "
                f"{'+' if pnl_b >= 0 else ''}{pnl_b:.2f} USDT ({pct_b:.2f}%)  "
                f"{best_trade.get('tidspunkt', '?')}"
            )

        if worst_trade:
            pnl_w = float(worst_trade.get("gevinst_usdt") or 0)
            pct_w = float(worst_trade.get("gevinst_prosent") or 0)
            lines.append(
                f"DÅRLIGSTE HANDEL:  {worst_trade.get('symbol', '?')}  "
                f"{'+' if pnl_w >= 0 else ''}{pnl_w:.2f} USDT ({pct_w:.2f}%)  "
                f"{worst_trade.get('tidspunkt', '?')}"
            )

        lines += ["", "=" * 62]

        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        logger.info(f"Ukentlig ytelsesrapport skrevet: {REPORT_FILE}")

    except Exception as e:
        logger.error(f"Feil ved generering av ytelsesrapport: {e}")
