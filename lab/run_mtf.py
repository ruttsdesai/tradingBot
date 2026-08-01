"""
Multi-Timeframe Lab — does a higher-timeframe trend filter help?

Idea under test: keep the 5m entry signals, but only BUY when a SLOWER
timeframe (15m / 60m) agrees the trend is up. "Trade with the bigger trend."

PRE-REGISTERED PREDICTION (written before running): this FAILS.
The signal lab already measured trend filters directly and every one had a
NEGATIVE forward-return spread (trend_above_sma200 -1.63%, golden_cross
-1.78% at 20d) with |t| < 1.4 — i.e. no predictive power, if anything mildly
inverted. A 60m trend filter is the same mechanism on a shorter clock, so the
prior is that it filters out good trades as often as bad ones and simply
reduces sample size. Recording this up front so a negative result cannot be
rationalised away, and a positive one has to be genuinely surprising.

Design discipline: ONE configuration per higher timeframe (SMA20 on the
resampled bars), not a sweep. We already have ~13 variants x 3 intervals on a
single 55-day sample; sweeping filter widths here would be overfitting.

No new data needed — 5m bars resample into 15m/60m locally.

Usage:
    python lab/run_mtf.py --days 55
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.intraday_runner import IntradayBacktester
from engine.risk_manager import RiskManager
from lab.run_experiment import (LIVE_BASKET, LIVE_STRATEGIES, DEFAULT_COST_PCT,
                                _build_strategy, load_bars, RESULTS_DIR)


def htf_trend_ok(df: pd.DataFrame, rule: str, window: int = 20) -> pd.Series:
    """True where the higher-timeframe close is above its SMA.

    Resamples the 5m bars up to `rule` (e.g. '15min', '60min'), computes the
    SMA there, then forward-fills back onto the 5m index. Uses .shift(1) so a
    bar is judged only by HIGHER-timeframe bars that had already CLOSED —
    without that shift the filter would peek at the future.
    """
    htf = df["close"].resample(rule).last().dropna()
    sma = htf.rolling(window).mean()
    ok = (htf > sma).shift(1)               # only closed HTF bars
    return ok.reindex(df.index, method="ffill").fillna(False).astype(bool)


def main():
    ap = argparse.ArgumentParser(description="Multi-timeframe trend filter test")
    ap.add_argument("--days", type=int, default=55)
    ap.add_argument("--cost-pct", type=float, default=DEFAULT_COST_PCT)
    ap.add_argument("--window", type=int, default=20, help="SMA window on the higher timeframe")
    args = ap.parse_args()

    print("=" * 78)
    print("  MULTI-TIMEFRAME LAB — 5m entries filtered by a higher-timeframe trend")
    print("=" * 78)
    print(f"  Basket    : {', '.join(LIVE_BASKET)}")
    print(f"  Filter    : higher-TF close > SMA{args.window} (shifted, no look-ahead)")
    print(f"  Cost/side : {args.cost_pct:.4%}")
    print("  PREDICTION: this fails — trend signals showed no predictive power")
    print("=" * 78)

    print("\nLoading 5m bars...")
    data = {}
    for t in LIVE_BASKET:
        df = load_bars(t, args.days, interval="5m")
        if df is not None and not df.empty:
            data[t] = df
            print(f"  {t:16} {len(df):>5} bars")

    # The exit policy we actually run live, held constant across all arms.
    exit_params = dict(trailing_stop_enabled=True, trailing_stop_pct=0.008,
                       disable_strategy_sells=True)

    arms = {"no_filter": None, "filter_15m": "15min", "filter_60m": "60min"}
    results = {}

    print("\nRunning arms...")
    for arm, rule in arms.items():
        rows = []
        for ticker, df in data.items():
            ok = htf_trend_ok(df, rule, args.window) if rule else None
            for sname in LIVE_STRATEGIES:
                risk = RiskManager(
                    max_positions=1, max_allocation_pct=0.95,
                    max_daily_loss_pct=0.03, stop_loss_pct=0.05,
                    take_profit_pct=0.15,
                    trailing_stop_enabled=exit_params["trailing_stop_enabled"],
                    trailing_stop_pct=exit_params["trailing_stop_pct"],
                )
                bt = IntradayBacktester(
                    strategy=_build_strategy(sname), risk_manager=risk,
                    initial_capital=100_000.0, commission_pct=args.cost_pct,
                    min_volatility_pct=0.0015,
                    disable_strategy_sells=exit_params["disable_strategy_sells"],
                    htf_ok=ok,
                )
                try:
                    res = bt.run(df, ticker=ticker)
                except Exception as e:
                    print(f"    [skip] {ticker}/{sname}: {e}")
                    continue
                rows.append({
                    "ticker": ticker, "strategy": sname,
                    "return_pct": (res.portfolio.total_value - 100_000.0) / 1000.0,
                    "trades": res.total_trades,
                    "win_rate": res.win_rate * 100,
                    "sharpe": res.sharpe_ratio,
                })
        if rows:
            n = len(rows)
            results[arm] = {
                "avg_return": sum(r["return_pct"] for r in rows) / n,
                "profitable": sum(1 for r in rows if r["return_pct"] > 0),
                "combos": n,
                "trades": sum(r["trades"] for r in rows) / n,
                "win_rate": sum(r["win_rate"] for r in rows) / n,
                "sharpe": sum(r["sharpe"] for r in rows) / n,
                "detail": rows,
            }
        pct_on = f"{ok.mean()*100:.0f}%" if rule else "100%"
        print(f"  {arm:12} done (filter allows {pct_on} of bars)")

    print("\n" + "=" * 78)
    print("  RESULTS")
    print("=" * 78)
    print(f"{'arm':14}{'avg ret':>10}{'profitable':>12}{'trades':>9}{'win%':>8}{'sharpe':>9}")
    print("-" * 78)
    for arm in arms:
        if arm not in results:
            continue
        r = results[arm]
        print(f"{arm:14}{r['avg_return']:>+9.2f}%{r['profitable']:>7}/{r['combos']:<4}"
              f"{r['trades']:>9.0f}{r['win_rate']:>7.0f}%{r['sharpe']:>9.2f}")

    print("\n" + "=" * 78)
    print("  VERDICT")
    print("=" * 78)
    base = results.get("no_filter", {}).get("avg_return", 0.0)
    best = max((a for a in results if a != "no_filter"),
               key=lambda a: results[a]["avg_return"], default=None)
    if best:
        d = results[best]["avg_return"] - base
        print(f"  unfiltered      : {base:+.2f}%")
        print(f"  best filtered   : {best} {results[best]['avg_return']:+.2f}%  ({d:+.2f} pp)")
        print(f"  trades          : {results['no_filter']['trades']:.0f} -> "
              f"{results[best]['trades']:.0f}")
        if d > 0:
            print("\n  Filter HELPED — contradicts the pre-registered prediction.")
            print("  Treat with suspicion: re-test on other windows before believing it.")
        else:
            print("\n  Filter did NOT help — matches the prediction. The higher-timeframe")
            print("  trend carries no usable information here; it just removes trades.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = os.path.join(RESULTS_DIR, f"mtf_{stamp}.json")
    with open(out, "w") as f:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"),
                   "days": args.days, "cost_pct": args.cost_pct,
                   "window": args.window, "results": results}, f, indent=2)
    print(f"\n  Detail saved to: {out}\n")


if __name__ == "__main__":
    main()
