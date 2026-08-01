"""
Strategy Lab — a sandbox for testing changes BEFORE they touch the live bot.

Runs variants (parameter sets) of the intraday strategy over cached 5m NSE
bars and prints a side-by-side, NET-OF-COST comparison so a change can be
judged on evidence instead of a hunch.

Why this exists
---------------
Forward paper trading reports GROSS P&L and takes a full trading day per
data point. That combination hid the real problem for days: a system that
looks ~breakeven gross is clearly loss-making once brokerage, STT and
slippage are charged. The lab charges costs on every fill and replays
weeks of bars in seconds.

Pipeline (nothing is promoted until it earns it)
------------------------------------------------
    1. LAB    (this file)          fast, approximate, net-of-cost screening
    2. PAPER  (dhan-live --paper)  slow, faithful, live prices + consensus
    3. LIVE   (dhan-live --live)   real money

IMPORTANT — what the lab does NOT model
---------------------------------------
The live bot trades on a CONSENSUS vote across several strategies; the
backtester runs one strategy at a time. So the lab is for *ranking* and
*level-finding* (which tickers, which trailing-stop level), not for
predicting live P&L. Paper trading remains the final check before real money.

The lab never writes to config.yaml or start_paper_bot.bat. Promotion is a
separate, deliberate step (see lab/README.md).

Usage
-----
    python lab/run_experiment.py                  # run the default variants
    python lab/run_experiment.py --days 60        # more history
    python lab/run_experiment.py --cost-pct 0.001 # stress-test higher costs
    python lab/run_experiment.py --list           # show variants, run nothing
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.intraday_runner import IntradayBacktester
from engine.risk_manager import RiskManager

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "lab_cache")

# The live paper basket (AXISBANK dropped 2026-07-24 as the worst performer).
LIVE_BASKET = ["ASIANPAINT.NS", "BAJFINANCE.NS", "M&M.NS", "SUNPHARMA.NS", "KOTAKBANK.NS"]
LIVE_STRATEGIES = ["rsi_mean_revert", "bollinger_bands", "ma_crossover"]

# Realistic Dhan intraday round-trip cost, charged per side by the backtester.
# 0.0005/side ~= 0.1% round trip (brokerage + STT + exchange + GST + stamp
# + a little slippage). Sweep it to see how sensitive a variant is to costs.
DEFAULT_COST_PCT = 0.0005


def _build_strategy(name: str):
    """Construct a strategy by its CLI name, using default parameters."""
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


def load_bars(ticker: str, days: int, interval: str = "5m", refresh: bool = False):
    """Fetch 5m bars, caching to CSV so repeat sweeps don't re-download."""
    import pandas as pd

    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = ticker.replace(".", "_").replace("&", "and")
    path = os.path.join(CACHE_DIR, f"{safe}_{interval}_{days}d.csv")

    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if not df.empty:
            return df

    from data.stocks import fetch_intraday_data
    df = fetch_intraday_data(ticker, interval=interval, days=days)
    if df is not None and not df.empty:
        df.to_csv(path)
    return df


# ---------------------------------------------------------------------------
# Variants — each is a set of overrides tested against the same data.
# Add new ideas here; nothing here affects the live bot.
# ---------------------------------------------------------------------------

VARIANTS = {
    "baseline": {
        "desc": "Current live settings (5% stop, no trailing, 120m time-exit)",
        "params": {},
    },
    "trail_0.3": {
        "desc": "Trailing stop 0.3% below peak",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.003},
    },
    "trail_0.5": {
        "desc": "Trailing stop 0.5% below peak",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.005},
    },
    "trail_0.8": {
        "desc": "Trailing stop 0.8% below peak",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.008},
    },
    "trail_atr1.5": {
        "desc": "Trailing stop at 1.5x ATR below peak (adapts to volatility)",
        "params": {"trailing_stop_enabled": True, "trailing_stop_atr_mult": 1.5},
    },
    "stop_1.5pct": {
        "desc": "Tighter hard stop 1.5% (control — data says it rarely fires)",
        "params": {"stop_loss_pct": 0.015},
    },
    "hold_60m": {
        "desc": "Time-exit at 60m instead of 120m (big losers were all ~120m holds)",
        "params": {"max_hold_minutes": 60},
    },
    "trail0.5_hold60": {
        "desc": "Trailing 0.5% + 60m time-exit (combined)",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.005,
                   "max_hold_minutes": 60},
    },

    # ---- Trailing stop REPLACES the strategy SELL --------------------------
    # Live post-exit drift analysis (120 exits, controlled against a 2898-bar
    # random-time baseline) showed strategy sells are followed by +0.29pp MORE
    # upside than a random moment: the bot sells into continuing momentum.
    # The trail_* variants above ADD a trailing stop while keeping those sells.
    # These REPLACE them, so a trailing stop alone decides when to exit.
    "replace_trail_0.3": {
        "desc": "No strategy sells; exit on 0.3% trailing stop",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.003,
                   "disable_strategy_sells": True},
    },
    "replace_trail_0.5": {
        "desc": "No strategy sells; exit on 0.5% trailing stop",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.005,
                   "disable_strategy_sells": True},
    },
    "replace_trail_0.8": {
        "desc": "No strategy sells; exit on 0.8% trailing stop",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.008,
                   "disable_strategy_sells": True},
    },
    "replace_trail_1.2": {
        "desc": "No strategy sells; exit on 1.2% trailing stop",
        "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.012,
                   "disable_strategy_sells": True},
    },
    "replace_hold_eod": {
        "desc": "No strategy sells, no trailing; hold to 15:10 square-off",
        "params": {"disable_strategy_sells": True, "max_hold_minutes": 10_000},
    },
}


def run_variant(name: str, spec: dict, tickers: list[str], strategies: list[str],
                data: dict, cost_pct: float) -> dict:
    """Run one variant across every ticker x strategy; aggregate the results."""
    p = spec["params"]
    per_combo = []

    for ticker in tickers:
        df = data.get(ticker)
        if df is None or df.empty:
            continue
        for sname in strategies:
            risk = RiskManager(
                max_positions=1,                      # one ticker per run
                max_allocation_pct=0.95,
                max_daily_loss_pct=p.get("max_daily_loss_pct", 0.03),
                stop_loss_pct=p.get("stop_loss_pct", 0.05),
                take_profit_pct=p.get("take_profit_pct", 0.15),
                trailing_stop_enabled=p.get("trailing_stop_enabled", False),
                trailing_stop_pct=p.get("trailing_stop_pct", 0.08),
                trailing_stop_atr_mult=p.get("trailing_stop_atr_mult", 0.0),
            )
            bt = IntradayBacktester(
                strategy=_build_strategy(sname),
                risk_manager=risk,
                initial_capital=100_000.0,
                commission_pct=cost_pct,
                max_hold_minutes=p.get("max_hold_minutes", 120),
                min_profit_threshold_pct=p.get("min_profit_threshold_pct", 0.005),
                min_volatility_pct=p.get("min_volatility_pct", 0.0015),
                disable_strategy_sells=p.get("disable_strategy_sells", False),
            )
            try:
                res = bt.run(df, ticker=ticker)
            except Exception as e:
                print(f"    [skip] {ticker}/{sname}: {e}")
                continue

            ret = (res.portfolio.total_value - 100_000.0) / 100_000.0
            per_combo.append({
                "ticker": ticker, "strategy": sname,
                "return_pct": ret * 100,
                "trades": res.total_trades,
                "win_rate": res.win_rate * 100,
                "sharpe": res.sharpe_ratio,
            })

    if not per_combo:
        return {"variant": name, "desc": spec["desc"], "combos": 0}

    n = len(per_combo)
    avg_ret = sum(c["return_pct"] for c in per_combo) / n
    tot_trades = sum(c["trades"] for c in per_combo)
    profitable = sum(1 for c in per_combo if c["return_pct"] > 0)
    avg_wr = sum(c["win_rate"] for c in per_combo) / n
    avg_sharpe = sum(c["sharpe"] for c in per_combo) / n

    return {
        "variant": name, "desc": spec["desc"], "combos": n,
        "avg_return_pct": avg_ret,
        "profitable_combos": profitable,
        "pct_profitable": profitable / n * 100,
        "total_trades": tot_trades,
        "avg_trades_per_combo": tot_trades / n,
        "avg_win_rate": avg_wr,
        "avg_sharpe": avg_sharpe,
        "detail": sorted(per_combo, key=lambda c: -c["return_pct"]),
    }


def main():
    ap = argparse.ArgumentParser(description="Strategy Lab — test before promoting")
    ap.add_argument("--interval", default="5m",
                    help="Candle interval: 1m,5m,15m,30m,1h (1m only has ~7 days of history)")
    ap.add_argument("--days", type=int, default=55,
                    help="Days of 5m history (yfinance caps ~60)")
    ap.add_argument("--cost-pct", type=float, default=DEFAULT_COST_PCT,
                    help=f"Per-side cost (default {DEFAULT_COST_PCT} ~= 0.1%% round trip)")
    ap.add_argument("--variants", default="", help="Comma-separated subset to run")
    ap.add_argument("--tickers", default="", help="Comma-separated override basket")
    ap.add_argument("--refresh", action="store_true", help="Re-download cached bars")
    ap.add_argument("--list", action="store_true", help="List variants and exit")
    args = ap.parse_args()

    if args.list:
        print("\nAvailable variants:\n")
        for k, v in VARIANTS.items():
            print(f"  {k:18} {v['desc']}")
        print()
        return

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or LIVE_BASKET
    names = [v.strip() for v in args.variants.split(",") if v.strip()] or list(VARIANTS)

    print("=" * 78)
    print("  STRATEGY LAB — net-of-cost variant comparison")
    print("=" * 78)
    print(f"  Tickers    : {', '.join(tickers)}")
    print(f"  Strategies : {', '.join(LIVE_STRATEGIES)}")
    print(f"  History    : {args.days} days of {args.interval} bars")
    print(f"  Cost/side  : {args.cost_pct:.4%}  (~{args.cost_pct*2:.3%} round trip)")
    print("=" * 78)

    print("\nLoading bars...")
    data = {}
    for t in tickers:
        try:
            df = load_bars(t, args.days, interval=args.interval, refresh=args.refresh)
            data[t] = df
            print(f"  {t:16} {len(df):>5} bars")
        except Exception as e:
            print(f"  {t:16} FAILED: {e}")

    if not any(df is not None and not df.empty for df in data.values()):
        print("\nNo data loaded — aborting.")
        return

    print("\nRunning variants...")
    results = []
    for name in names:
        if name not in VARIANTS:
            print(f"  [skip] unknown variant: {name}")
            continue
        print(f"  {name} ...")
        results.append(run_variant(name, VARIANTS[name], tickers,
                                   LIVE_STRATEGIES, data, args.cost_pct))

    results = [r for r in results if r.get("combos")]
    results.sort(key=lambda r: -r["avg_return_pct"])

    print("\n" + "=" * 78)
    print("  RESULTS (sorted by avg net return; all costs charged)")
    print("=" * 78)
    print(f"{'variant':18} {'avg ret':>9} {'profit%':>8} {'trades':>7} {'win%':>7} {'sharpe':>7}")
    print("-" * 78)
    for r in results:
        print(f"{r['variant']:18} {r['avg_return_pct']:>+8.2f}% "
              f"{r['pct_profitable']:>7.0f}% {r['avg_trades_per_combo']:>7.0f} "
              f"{r['avg_win_rate']:>6.0f}% {r['avg_sharpe']:>7.2f}")

    if results:
        best, base = results[0], next((r for r in results if r["variant"] == "baseline"), None)
        print("\n" + "-" * 78)
        print(f"  BEST: {best['variant']} — {best['desc']}")
        if base and best["variant"] != "baseline":
            delta = best["avg_return_pct"] - base["avg_return_pct"]
            print(f"  vs baseline: {delta:+.2f} pp   "
                  f"(trades {base['avg_trades_per_combo']:.0f} -> {best['avg_trades_per_combo']:.0f})")
            print("\n  A win here is NECESSARY but NOT SUFFICIENT — the lab does not model")
            print("  the live consensus vote. Confirm in paper trading before promoting.")
        elif base and best["variant"] == "baseline":
            print("  No variant beat the baseline. Do not promote anything.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = os.path.join(RESULTS_DIR, f"experiment_{stamp}.json")
    with open(out, "w") as f:
        json.dump({
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "days": args.days, "cost_pct": args.cost_pct,
            "tickers": tickers, "strategies": LIVE_STRATEGIES,
            "results": results,
        }, f, indent=2)
    print(f"\n  Full detail saved to: {out}\n")


if __name__ == "__main__":
    main()
