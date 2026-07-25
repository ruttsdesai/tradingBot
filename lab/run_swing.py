"""
Swing Lab — does daily-bar (multi-day hold) trading clear costs?

The intraday lab reached a hard conclusion: scalping liquid large-caps on 5m
bars nets ~2%/yr after realistic costs — below a risk-free FD. The suspected
reason is trade frequency: ~34 trades/day means costs dominate any edge.

This tests the opposite end. Daily bars with multi-day holds fire a handful of
trades per month, so costs become nearly irrelevant. If there is a real edge in
these names, this is where it should show up.

The benchmark that actually matters
-----------------------------------
Every result is printed against BUY & HOLD on the same ticker over the same
window. A strategy returning +40% sounds great until buy-and-hold returned +60%
— then the strategy destroyed value and added risk for nothing. Most published
backtests quietly omit this. We don't.

All costs are charged on BOTH sides (see the PaperTrader commission fix).

Usage
-----
    python lab/run_swing.py                 # 5y daily, live basket
    python lab/run_swing.py --years 10
    python lab/run_swing.py --cost-pct 0.001
    python lab/run_swing.py --tickers RELIANCE.NS,TCS.NS
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.paper_trader import PaperTrader
from engine.risk_manager import RiskManager

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "lab_cache")

BASKET = ["ASIANPAINT.NS", "BAJFINANCE.NS", "M&M.NS", "SUNPHARMA.NS", "KOTAKBANK.NS"]
STRATEGIES = ["ma_crossover", "rsi_mean_revert", "macd", "bollinger_bands", "momentum_breakout"]

# Swing trades are far rarer, so a slightly higher per-side cost (incl. slippage
# and, for delivery, higher STT) is the conservative assumption.
DEFAULT_COST_PCT = 0.001

TRADING_DAYS_PER_YEAR = 252


def _build_strategy(name: str):
    if name == "ma_crossover":
        from strategies.ma_crossover import MACrossoverStrategy
        return MACrossoverStrategy()
    if name == "rsi_mean_revert":
        from strategies.rsi_mean_revert import RSIMeanReversionStrategy
        return RSIMeanReversionStrategy()
    if name == "macd":
        from strategies.macd import MACDStrategy
        return MACDStrategy()
    if name == "bollinger_bands":
        from strategies.bollinger_bands import BollingerBandsStrategy
        return BollingerBandsStrategy()
    if name == "momentum_breakout":
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        return MomentumBreakoutStrategy()
    raise ValueError(f"Unknown strategy: {name}")


def load_daily(ticker: str, years: int, refresh: bool = False,
               start: str = "", end: str = ""):
    """Fetch daily bars (cached), optionally sliced to a [start, end] window.

    fetch_stock_data always pulls a window ending today, so to test a
    historical regime (e.g. a bear market) we cache one long history per
    ticker and slice it — no repeated downloads per window.
    """
    import pandas as pd

    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = ticker.replace(".", "_").replace("&", "and")
    path = os.path.join(CACHE_DIR, f"{safe}_1d_{years}y.csv")
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        from data.stocks import fetch_stock_data
        df = fetch_stock_data(ticker, years=years)
        if df is not None and not df.empty:
            df.to_csv(path)

    if df is None or df.empty:
        return df

    if start or end:
        idx = df.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
            df = df.copy()
            df.index = idx
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
    return df


def annualize(total_return: float, n_bars: int) -> float:
    """Convert a period return into a CAGR using the bar count."""
    yrs = max(n_bars / TRADING_DAYS_PER_YEAR, 1e-9)
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (1.0 / yrs) - 1.0


def main():
    ap = argparse.ArgumentParser(description="Swing Lab — daily bars vs buy & hold")
    ap.add_argument("--years", type=int, default=5,
                    help="History to fetch/cache (also the window when no --start/--end)")
    ap.add_argument("--cost-pct", type=float, default=DEFAULT_COST_PCT)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--strategies", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--start", default="", help="Window start YYYY-MM-DD (e.g. a bear market)")
    ap.add_argument("--end", default="", help="Window end YYYY-MM-DD")
    ap.add_argument("--label", default="", help="Name for this window in the output/results file")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or BASKET
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()] or STRATEGIES

    print("=" * 86)
    print("  SWING LAB — daily bars, multi-day holds, benchmarked against BUY & HOLD")
    print("=" * 86)
    print(f"  Tickers   : {', '.join(tickers)}")
    print(f"  Strategies: {', '.join(strategies)}")
    if args.start or args.end:
        print(f"  Window    : {args.start or 'earliest'} -> {args.end or 'latest'}"
              f"{'  [' + args.label + ']' if args.label else ''}")
    else:
        print(f"  Window    : {args.years} years of daily bars")
    print(f"  Cost/side : {args.cost_pct:.3%} (charged on BOTH sides)")
    print("=" * 86)

    print("\nLoading daily bars...")
    data, bh = {}, {}
    for t in tickers:
        try:
            df = load_daily(t, args.years, refresh=args.refresh,
                            start=args.start, end=args.end)
            if df is None or df.empty:
                print(f"  {t:16} no data")
                continue
            data[t] = df
            r = float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1.0
            bh[t] = {"total": r, "cagr": annualize(r, len(df)), "bars": len(df)}
            print(f"  {t:16} {len(df):>5} bars   buy&hold {r:+7.1%} "
                  f"({bh[t]['cagr']:+.1%}/yr)")
        except Exception as e:
            print(f"  {t:16} FAILED: {e}")

    if not data:
        print("\nNo data — aborting.")
        return

    rows = []
    print("\nRunning strategies...")
    for sname in strategies:
        for ticker, df in data.items():
            trader = PaperTrader(
                strategy=_build_strategy(sname),
                risk_manager=RiskManager(max_positions=1, max_allocation_pct=0.95),
                initial_capital=100_000.0,
                commission_pct=args.cost_pct,
            )
            try:
                res = trader.run(df, ticker=ticker)
            except Exception as e:
                print(f"  [skip] {ticker}/{sname}: {e}")
                continue
            tot = res.portfolio.total_pnl_pct
            rows.append({
                "ticker": ticker, "strategy": sname,
                "total_return": tot * 100,
                "cagr": annualize(tot, len(df)) * 100,
                "bh_total": bh[ticker]["total"] * 100,
                "bh_cagr": bh[ticker]["cagr"] * 100,
                "excess_cagr": (annualize(tot, len(df)) - bh[ticker]["cagr"]) * 100,
                "trades": res.total_trades,
                "win_rate": res.win_rate * 100,
                "sharpe": res.sharpe_ratio,
                "max_dd": res.portfolio.max_drawdown * 100,
            })

    if not rows:
        print("\nNo results.")
        return

    # ---- Per-strategy aggregate -------------------------------------------
    print("\n" + "=" * 86)
    print("  BY STRATEGY (avg across tickers) — 'vs B&H' is what actually matters")
    print("=" * 86)
    print(f"{'strategy':20} {'CAGR':>8} {'B&H CAGR':>10} {'vs B&H':>9} "
          f"{'trades':>7} {'win%':>6} {'sharpe':>7} {'maxDD':>7}")
    print("-" * 86)
    per_strat = {}
    for s in strategies:
        sub = [r for r in rows if r["strategy"] == s]
        if not sub:
            continue
        n = len(sub)
        agg = {
            "cagr": sum(r["cagr"] for r in sub) / n,
            "bh_cagr": sum(r["bh_cagr"] for r in sub) / n,
            "excess": sum(r["excess_cagr"] for r in sub) / n,
            "trades": sum(r["trades"] for r in sub) / n,
            "win": sum(r["win_rate"] for r in sub) / n,
            "sharpe": sum(r["sharpe"] for r in sub) / n,
            "dd": sum(r["max_dd"] for r in sub) / n,
            "beat": sum(1 for r in sub if r["excess_cagr"] > 0),
            "n": n,
        }
        per_strat[s] = agg
    for s, a in sorted(per_strat.items(), key=lambda kv: -kv[1]["excess"]):
        print(f"{s:20} {a['cagr']:>+7.1f}% {a['bh_cagr']:>+9.1f}% {a['excess']:>+8.1f}% "
              f"{a['trades']:>7.0f} {a['win']:>5.0f}% {a['sharpe']:>7.2f} {a['dd']:>6.1f}%")

    # ---- Verdict -----------------------------------------------------------
    beat = [r for r in rows if r["excess_cagr"] > 0]
    best = max(rows, key=lambda r: r["excess_cagr"])
    avg_excess = sum(r["excess_cagr"] for r in rows) / len(rows)
    avg_trades = sum(r["trades"] for r in rows) / len(rows)

    print("\n" + "=" * 86)
    print("  VERDICT")
    print("=" * 86)
    print(f"  Combos tested            : {len(rows)}")
    print(f"  Beat buy & hold          : {len(beat)}/{len(rows)} "
          f"({len(beat)/len(rows)*100:.0f}%)")
    print(f"  Avg excess CAGR vs B&H   : {avg_excess:+.2f}%/yr")
    print(f"  Avg trades per combo     : {avg_trades:.0f} over {args.years}y "
          f"(~{avg_trades/args.years:.0f}/yr)")
    print(f"  Best combo               : {best['ticker']}/{best['strategy']} "
          f"{best['cagr']:+.1f}%/yr vs B&H {best['bh_cagr']:+.1f}%/yr "
          f"({best['excess_cagr']:+.1f}pp)")
    if avg_excess <= 0:
        print("\n  Strategies did NOT beat simply holding the shares.")
        print("  Active trading added risk and effort for no excess return.")
    else:
        print("\n  Positive average excess — but check how many combos actually beat")
        print("  B&H; a single outlier can carry the average.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = os.path.join(RESULTS_DIR, f"swing_{stamp}.json")
    with open(out, "w") as f:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"),
                   "years": args.years, "cost_pct": args.cost_pct,
                   "per_strategy": per_strat, "rows": rows}, f, indent=2)
    print(f"\n  Detail saved to: {out}\n")


if __name__ == "__main__":
    main()
