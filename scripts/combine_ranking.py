"""
Combine all backtest CSV files into a single cross-market ranking table.

Reads every backtest_*.csv in reports/, merges them, ranks by Return %,
and writes reports/combined_ranking.csv + prints a formatted table.

Usage:  python combine_ranking.py
"""
import csv
import os
import sys
from collections import defaultdict

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

HEADERS = [
    "Rank", "Market", "Strategy", "Ticker", "Return", "Sharpe", "Sortino",
    "Win Rate", "Max DD", "Trades", "Wins", "Losses", "DD Days", "P&L",
    "Start Date", "End Date"
]


def load_all_backtest_csvs() -> list[dict]:
    """Read every backtest_*.csv in reports/ and return combined rows."""
    rows = []
    if not os.path.isdir(REPORTS_DIR):
        print(f"ERROR: reports/ directory not found at {REPORTS_DIR}")
        sys.exit(1)

    for fname in sorted(os.listdir(REPORTS_DIR)):
        if not fname.startswith("backtest_") or not fname.endswith(".csv"):
            continue
        fpath = os.path.join(REPORTS_DIR, fname)
        try:
            with open(fpath, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Convert numeric fields
                    row["Return"] = float(row.get("Return", 0))
                    row["Sharpe"] = float(row.get("Sharpe", 0))
                    row["Sortino"] = float(row.get("Sortino", 999))
                    row["Win Rate"] = float(row.get("Win Rate", 0))
                    row["Max DD"] = float(row.get("Max DD", 0))
                    row["Trades"] = int(row.get("Trades", 0))
                    row["P&L"] = float(row.get("P&L", 0))
                    row["DD Days"] = int(row.get("DD Days", 0))
                    row["Wins"] = int(row.get("Wins", 0))
                    row["Losses"] = int(row.get("Losses", 0))
                    rows.append(row)
        except Exception as e:
            print(f"  WARN: Skipping {fname}: {e}")
    return rows


def rank_rows(rows: list[dict], sort_col: str = "Return") -> list[dict]:
    """Sort by sort_col descending and assign rank."""
    rows.sort(key=lambda r: r[sort_col], reverse=True)
    for i, r in enumerate(rows):
        r["Rank"] = i + 1
    return rows


def write_combined_csv(rows: list[dict], fpath: str):
    """Write ranked rows to CSV."""
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"\n[CSV] {len(rows)} rows written to {fpath}")


def print_top_table(rows: list[dict], top_n: int = 30, title: str = "Top"):
    """Print a formatted table of the top N rows."""
    from tabulate import tabulate

    print(f"\n{'='*110}")
    print(f"  {title} — Cross-Market Strategy Ranking (sorted by Return %)")
    print(f"{'='*110}\n")

    table_rows = []
    for r in rows[:top_n]:
        table_rows.append([
            r["Rank"],
            r["Market"],
            r["Strategy"],
            r["Ticker"],
            f"{r['Return']:+.2%}",
            r["Sharpe"],
            r["Sortino"],
            f"{r['Win Rate']:.1%}",
            f"{r['Max DD']:+.2%}",
            r["Trades"],
            r["Wins"],
            r["Losses"],
            r["DD Days"],
            f"${r['P&L']:+,.0f}",
        ])

    headers = ["#", "Market", "Strategy", "Ticker", "Return", "Sharpe",
               "Sortino", "Win Rate", "Max DD", "Trades", "W", "L", "DD Days", "P&L"]
    print(tabulate(table_rows, headers=headers, tablefmt="grid", stralign="right",
                   numalign="right"))


def main():
    print("=== Cross-Market Combined Backtest Ranking ===\n")
    print("Loading backtest CSVs...")

    rows = load_all_backtest_csvs()
    print(f"  Loaded {len(rows)} results across 4 markets x 5 strategies")

    # Rank individual ticker+strategy combos
    ranked = rank_rows(rows, sort_col="Return")

    # Write combined CSV
    out_path = os.path.join(REPORTS_DIR, "combined_ranking.csv")
    write_combined_csv(ranked, out_path)

    # Print top 30
    print_top_table(ranked, top_n=30, title="Top 30 Ticker-Strategy Combos (by Return)")

    # Market-level summary
    print(f"\n{'='*110}")
    print(f"  Market-Level Strategy Averages")
    print(f"{'='*110}\n")

    from collections import defaultdict
    key = lambda r: (r["Market"], r["Strategy"])
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)

    from tabulate import tabulate
    agg_table = []
    for (market, strategy), tickers in sorted(groups.items()):
        n = len(tickers)
        sortinos = [t["Sortino"] for t in tickers if t["Sortino"] < 900]
        agg_table.append([
            market,
            strategy,
            n,
            f"{sum(t['Return'] for t in tickers) / n:+.2%}",
            f"{sum(t['Sharpe'] for t in tickers) / n:.2f}",
            f"{sum(sortinos) / len(sortinos):.2f}" if sortinos else "—",
            f"{sum(t['Win Rate'] for t in tickers) / n:.1%}",
            f"{sum(t['Max DD'] for t in tickers) / n:+.2%}",
            sum(t["Trades"] for t in tickers),
        ])

    print(tabulate(agg_table,
                   headers=["Market", "Strategy", "Tickers", "Avg Return", "Avg Sharpe",
                            "Avg Sortino", "Avg Win Rate", "Avg Max DD", "Total Trades"],
                   tablefmt="grid", stralign="right"))

    # Cross-strategy averages
    print(f"\n{'='*110}")
    print(f"  Strategy Averages (across ALL markets)")
    print(f"{'='*110}\n")

    by_strat = defaultdict(list)
    for r in rows:
        by_strat[r["Strategy"]].append(r)

    strat_table = []
    for strat_name in ["MA Crossover", "RSI Mean Reversion", "MACD",
                        "Bollinger Bands", "Momentum Breakout"]:
        vals = by_strat.get(strat_name, [])
        if not vals:
            continue
        n = len(vals)
        sortinos = [v["Sortino"] for v in vals if v["Sortino"] < 900]
        strat_table.append([
            strat_name,
            n,
            f"{sum(v['Return'] for v in vals) / n:+.2%}",
            f"{sum(v['Sharpe'] for v in vals) / n:.2f}",
            f"{sum(sortinos) / len(sortinos):.2f}" if sortinos else "—",
            f"{sum(v['Win Rate'] for v in vals) / n:.1%}",
            f"{sum(v['Max DD'] for v in vals) / n:+.2%}",
            sum(v["Trades"] for v in vals),
        ])

    print(tabulate(strat_table,
                   headers=["Strategy", "Runs", "Avg Return", "Avg Sharpe", "Avg Sortino",
                            "Avg Win Rate", "Avg Max DD", "Total Trades"],
                   tablefmt="grid", stralign="right"))

    # Cross-market averages
    print(f"\n{'='*110}")
    print(f"  Market Averages (across ALL strategies)")
    print(f"{'='*110}\n")

    by_market = defaultdict(list)
    for r in rows:
        by_market[r["Market"]].append(r)

    market_order = ["USA Stocks", "India NSE", "Canada TSX", "Crypto"]
    market_table = []
    for mkt in market_order:
        vals = by_market.get(mkt, [])
        if not vals:
            continue
        n = len(vals)
        sortinos = [v["Sortino"] for v in vals if v["Sortino"] < 900]
        market_table.append([
            mkt,
            n,
            f"{sum(v['Return'] for v in vals) / n:+.2%}",
            f"{sum(v['Sharpe'] for v in vals) / n:.2f}",
            f"{sum(sortinos) / len(sortinos):.2f}" if sortinos else "—",
            f"{sum(v['Win Rate'] for v in vals) / n:.1%}",
            f"{sum(v['Max DD'] for v in vals) / n:+.2%}",
            sum(v["Trades"] for v in vals),
        ])

    print(tabulate(market_table,
                   headers=["Market", "Combos", "Avg Return", "Avg Sharpe", "Avg Sortino",
                            "Avg Win Rate", "Avg Max DD", "Total Trades"],
                   tablefmt="grid", stralign="right"))

    print()


if __name__ == "__main__":
    main()
