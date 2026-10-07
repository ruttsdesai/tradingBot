"""
Regime Lab — can a trend filter tell us when to be invested?

The regime tests showed the strategies are insurance: they cost ~11pp/yr in
rising markets and roughly break even in falling ones, so they lose over a
full cycle. The only way that flips is if we can RELIABLY detect which regime
we are in, and act differently in each.

This tests the most-studied version of that idea: a moving-average trend
filter (Faber's timing model). Be invested while price is above its long
moving average; sit in cash otherwise. It is simple, famous, and has survived
decades of out-of-sample scrutiny — so if regime detection has any value here,
this should show it. If a bespoke rule beat this, suspect overfitting.

Variants
--------
  buy_hold        always invested (the benchmark that must be beaten)
  faber_200       long while close > 200-day SMA, else cash
  faber_100       long while close > 100-day SMA, else cash
  faber_50        long while close > 50-day SMA, else cash
  regime_trend    long while above SMA200; when below, trade ma_crossover
                  (uses the strategy that actually won in the bear window)

What to look at
---------------
CAGR alone is the wrong lens. Timing's real product is a smaller drawdown, so
the table reports max drawdown and return/drawdown. A rule that matches
buy & hold's return with half the drawdown is a genuine win even at equal CAGR.

Costs are charged on every switch.

Usage
-----
    python lab/run_regime.py --years 12
    python lab/run_regime.py --years 12 --start 2018-01-01 --end 2020-03-31
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from lab.run_swing import load_daily, annualize, TRADING_DAYS_PER_YEAR, BASKET

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
DEFAULT_COST_PCT = 0.001


def _metrics(equity: np.ndarray, n_bars: int) -> dict:
    """CAGR, max drawdown, Sharpe and return/drawdown from an equity curve."""
    if len(equity) < 2:
        return {"cagr": 0.0, "max_dd": 0.0, "sharpe": 0.0, "ret_dd": 0.0}
    total = equity[-1] / equity[0] - 1.0
    cagr = annualize(total, n_bars)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    max_dd = float(dd.min())
    rets = np.diff(equity) / equity[:-1]
    sharpe = 0.0
    if rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    ret_dd = cagr / abs(max_dd) if max_dd < 0 else 0.0
    return {"cagr": cagr * 100, "max_dd": max_dd * 100,
            "sharpe": sharpe, "ret_dd": ret_dd}


def simulate_timing(close: np.ndarray, position: np.ndarray, cost_pct: float):
    """Equity curve for a rule holding `position[i]` over bar i -> i+1.

    position: +1 long, 0 cash, -1 short. Cost is charged in proportion to the
    SIZE of the position change, so a long->short flip costs twice a
    long->cash exit (it closes one trade and opens another).
    """
    position = np.asarray(position, dtype=float)
    equity = np.ones(len(close))
    switches = 0
    for i in range(1, len(close)):
        r = close[i] / close[i - 1] - 1.0
        equity[i] = equity[i - 1] * (1.0 + position[i - 1] * r)
        delta = abs(position[i] - position[i - 1])
        if delta > 0:
            equity[i] *= (1.0 - cost_pct * delta)
            switches += 1
        if equity[i] <= 0:            # a short can in principle wipe out
            equity[i:] = 1e-9
            break
    return equity, switches


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def run_ticker(df, cost_pct: float) -> dict:
    """Run every regime variant on one ticker."""
    close = df["close"].to_numpy(dtype=float)
    n = len(close)
    out = {}

    # Benchmark: always invested.
    eq_bh, _ = simulate_timing(close, np.ones(n, dtype=bool), cost_pct)
    out["buy_hold"] = {**_metrics(eq_bh, n), "switches": 0, "time_in_mkt": 100.0}

    for label, win in (("faber_200", 200), ("faber_100", 100), ("faber_50", 50)):
        if n <= win:
            continue
        ma = sma(close, win)
        above = np.nan_to_num(close > ma, nan=False).astype(bool)

        # Long / cash (the classic timing rule)
        pos = above.astype(float)
        pos[:win] = 1.0           # no signal yet -> stay invested (neutral start)
        eq, sw = simulate_timing(close, pos, cost_pct)
        out[label] = {**_metrics(eq, n), "switches": sw,
                      "time_in_mkt": float((pos != 0).mean() * 100)}

        # Long / SHORT — same signal, but sell short instead of sitting in cash.
        # This is the user's proposed architecture: one sub-strategy per regime.
        ls = np.where(above, 1.0, -1.0)
        ls[:win] = 1.0
        eq2, sw2 = simulate_timing(close, ls, cost_pct)
        out[label.replace("faber", "longshort")] = {
            **_metrics(eq2, n), "switches": sw2,
            "time_in_mkt": 100.0,
        }

    return out


def main():
    ap = argparse.ArgumentParser(description="Regime Lab — trend filter vs buy & hold")
    ap.add_argument("--years", type=int, default=12)
    ap.add_argument("--cost-pct", type=float, default=DEFAULT_COST_PCT)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or BASKET

    print("=" * 84)
    print("  REGIME LAB — can a trend filter beat simply staying invested?")
    print("=" * 84)
    win = f"{args.start or 'earliest'} -> {args.end or 'latest'}" if (args.start or args.end) \
        else f"last {args.years} years"
    print(f"  Window   : {win}{'  [' + args.label + ']' if args.label else ''}")
    print(f"  Tickers  : {', '.join(tickers)}")
    print(f"  Cost     : {args.cost_pct:.3%} per switch")
    print("=" * 84)

    per_ticker = {}
    print("\nLoading + simulating...")
    for t in tickers:
        df = load_daily(t, args.years, refresh=args.refresh,
                        start=args.start, end=args.end)
        if df is None or df.empty:
            print(f"  {t:16} no data")
            continue
        per_ticker[t] = run_ticker(df, args.cost_pct)
        print(f"  {t:16} {len(df):>5} bars")

    if not per_ticker:
        print("\nNo data — aborting.")
        return

    variants = ["buy_hold",
                "faber_200", "faber_100", "faber_50",
                "longshort_200", "longshort_100", "longshort_50"]
    agg = {}
    for v in variants:
        vals = [m[v] for m in per_ticker.values() if v in m]
        if not vals:
            continue
        agg[v] = {
            "cagr": sum(x["cagr"] for x in vals) / len(vals),
            "max_dd": sum(x["max_dd"] for x in vals) / len(vals),
            "sharpe": sum(x["sharpe"] for x in vals) / len(vals),
            "ret_dd": sum(x["ret_dd"] for x in vals) / len(vals),
            "switches": sum(x["switches"] for x in vals) / len(vals),
            "time_in_mkt": sum(x["time_in_mkt"] for x in vals) / len(vals),
        }

    bh = agg.get("buy_hold", {})
    print("\n" + "=" * 84)
    print("  RESULTS (avg across tickers)")
    print("=" * 84)
    print(f"{'variant':14} {'CAGR':>8} {'vs B&H':>8} {'maxDD':>8} {'ret/DD':>7} "
          f"{'sharpe':>7} {'%inMkt':>7} {'switch':>7}")
    print("-" * 84)
    for v in variants:
        if v not in agg:
            continue
        a = agg[v]
        excess = a["cagr"] - bh.get("cagr", 0.0)
        exc = "  —   " if v == "buy_hold" else f"{excess:+7.1f}%"
        print(f"{v:14} {a['cagr']:>+7.1f}% {exc:>8} {a['max_dd']:>7.1f}% "
              f"{a['ret_dd']:>7.2f} {a['sharpe']:>7.2f} {a['time_in_mkt']:>6.0f}% "
              f"{a['switches']:>7.0f}")

    print("\n" + "=" * 84)
    print("  VERDICT")
    print("=" * 84)
    best = max((v for v in agg if v != "buy_hold"), key=lambda v: agg[v]["cagr"], default=None)
    if best:
        b, h = agg[best], bh
        print(f"  Best timing rule : {best}  {b['cagr']:+.1f}%/yr vs B&H {h['cagr']:+.1f}%/yr "
              f"({b['cagr']-h['cagr']:+.1f}pp)")
        print(f"  Drawdown         : {b['max_dd']:.1f}% vs B&H {h['max_dd']:.1f}%  "
              f"({'BETTER' if b['max_dd'] > h['max_dd'] else 'worse'})")
        print(f"  Return/drawdown  : {b['ret_dd']:.2f} vs B&H {h['ret_dd']:.2f}")
        if b["cagr"] < h["cagr"] and b["max_dd"] > h["max_dd"]:
            print("\n  Lower return but a smaller drawdown — timing bought risk reduction,")
            print("  not extra return. Whether that trade is worth it is a personal call;")
            print("  it is NOT free alpha.")
        elif b["cagr"] >= h["cagr"]:
            print("\n  Timing beat buy & hold on return. Check it holds across windows")
            print("  and tickers before believing it.")
        else:
            print("\n  Timing lost on BOTH return and drawdown — no case for it here.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = os.path.join(RESULTS_DIR, f"regime_{stamp}.json")
    with open(out, "w") as f:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"),
                   "window": win, "label": args.label, "cost_pct": args.cost_pct,
                   "aggregate": agg, "per_ticker": per_ticker}, f, indent=2)
    print(f"\n  Detail saved to: {out}\n")


if __name__ == "__main__":
    main()
