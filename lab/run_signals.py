"""
Signal Lab — does ANY regime signal actually predict forward returns?

Everything downstream of a regime signal (which sub-strategy, long vs short,
position size) only multiplies that signal's edge. The long/short test showed
what happens when you multiply an edge of zero: -4.1%/yr with -79% drawdowns.

So this asks the question directly, before any strategy is involved:

    When signal X says "bull", are the NEXT N days actually better than
    when it says "bear"? By how much? And is that gap real or noise?

This is a much cleaner test than backtesting a strategy, because it separates
signal quality from execution, costs, and position sizing. If no signal shows
a real gap here, no amount of strategy engineering downstream will help.

Statistical honesty
-------------------
Forward-return windows on daily bars OVERLAP heavily (today's 20-day forward
return shares 19 days with tomorrow's), which massively inflates naive
t-statistics. We deflate the effective sample size by the horizon, which is
conservative and closer to the truth. Even so, treat |t| < 2 as noise, and be
suspicious of anything that only works on one ticker.

Usage
-----
    python lab/run_signals.py --years 12
    python lab/run_signals.py --years 12 --horizon 5
    python lab/run_signals.py --tickers "^NSEI"      # test the index itself
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from lab.run_swing import load_daily, BASKET
from lab.run_regime import sma

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def _rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        out[i] = x[i - n:i].std()
    return out


def build_signals(close: np.ndarray, volume: np.ndarray | None) -> dict:
    """Each signal returns a boolean array: True = 'expect better returns'.

    All are computed using ONLY past data at each point (no look-ahead).
    """
    n = len(close)
    rets = np.zeros(n)
    rets[1:] = close[1:] / close[:-1] - 1.0

    sig = {}

    ma200, ma100, ma50 = sma(close, 200), sma(close, 100), sma(close, 50)

    # 1. Classic trend filter — price above long MA
    sig["trend_above_sma200"] = close > ma200
    sig["trend_above_sma50"] = close > ma50

    # 2. Trend SLOPE — is the long MA itself rising?
    slope200 = np.full(n, np.nan)
    slope200[20:] = ma200[20:] - ma200[:-20]
    sig["sma200_rising"] = slope200 > 0

    # 3. Golden cross — fast MA above slow MA
    sig["golden_cross_50_200"] = ma50 > ma200

    # 4. Volatility regime — is recent vol BELOW its own median?
    #    (high-vol periods are widely believed to precede poorer returns)
    vol20 = _rolling_std(rets, 20)
    med_vol = np.full(n, np.nan)
    for i in range(250, n):
        med_vol[i] = np.nanmedian(vol20[max(0, i - 250):i])
    sig["low_volatility"] = vol20 < med_vol

    # 5. Drawdown state — are we near the highs rather than deep in a drawdown?
    peak = np.maximum.accumulate(close)
    dd = close / peak - 1.0
    sig["near_highs_dd_lt_10pct"] = dd > -0.10

    # 6. Momentum (12-1): return over the past year excluding the last month
    mom = np.full(n, np.nan)
    mom[252:] = close[231:-21] / close[:-252] - 1.0
    sig["momentum_12_1_positive"] = mom > 0

    # 7. Short-term mean reversion — price below its 10-day MA (buy dips)
    ma10 = sma(close, 10)
    sig["below_sma10_dip"] = close < ma10

    # 8. Volume confirmation — volume above its 50-day average
    if volume is not None:
        vma = sma(volume.astype(float), 50)
        sig["volume_above_avg"] = volume > vma

    return sig


def evaluate(close: np.ndarray, sig: np.ndarray, horizon: int) -> dict | None:
    """Compare forward returns when the signal is ON vs OFF."""
    n = len(close)
    fwd = np.full(n, np.nan)
    fwd[:n - horizon] = close[horizon:] / close[:n - horizon] - 1.0

    s = np.asarray(sig, dtype=object)
    valid = ~np.isnan(fwd) & np.array([x is not None and x == x for x in s])
    mask_on = np.array([bool(x) if x == x else False for x in s]) & valid
    mask_off = (~np.array([bool(x) if x == x else False for x in s])) & valid

    n_on, n_off = int(mask_on.sum()), int(mask_off.sum())
    if n_on < 60 or n_off < 60:
        return None

    on, off = fwd[mask_on], fwd[mask_off]
    spread = on.mean() - off.mean()

    # Deflate sample sizes by the horizon: overlapping windows are not
    # independent observations, so the naive t-stat is badly overstated.
    eff_on, eff_off = max(n_on / horizon, 2), max(n_off / horizon, 2)
    se = np.sqrt(on.var(ddof=1) / eff_on + off.var(ddof=1) / eff_off)
    t = spread / se if se > 0 else 0.0

    return {
        "mean_on": on.mean() * 100, "mean_off": off.mean() * 100,
        "spread": spread * 100, "t_stat": float(t),
        "n_on": n_on, "n_off": n_off,
        "pct_on": n_on / (n_on + n_off) * 100,
    }


def main():
    ap = argparse.ArgumentParser(description="Signal Lab — do regime signals predict?")
    ap.add_argument("--years", type=int, default=12)
    ap.add_argument("--horizon", type=int, default=20,
                    help="Forward return horizon in trading days (default 20 ~ 1 month)")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or BASKET

    print("=" * 88)
    print("  SIGNAL LAB — does any regime signal predict forward returns?")
    print("=" * 88)
    print(f"  Tickers : {', '.join(tickers)}")
    print(f"  Horizon : {args.horizon} trading days forward")
    print(f"  History : {args.years} years")
    print("  Reading : 'spread' = avg forward return when ON minus when OFF.")
    print("            |t| < 2 is noise. Overlap-adjusted (conservative).")
    print("=" * 88)

    per_signal = {}
    print("\nLoading + evaluating...")
    for t in tickers:
        df = load_daily(t, args.years, refresh=args.refresh)
        if df is None or df.empty:
            print(f"  {t:16} no data")
            continue
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy() if "volume" in df.columns else None
        sigs = build_signals(close, vol)
        for name, s in sigs.items():
            r = evaluate(close, s, args.horizon)
            if r:
                per_signal.setdefault(name, []).append({"ticker": t, **r})
        print(f"  {t:16} {len(close):>5} bars, {len(sigs)} signals")

    if not per_signal:
        print("\nNo results.")
        return

    print("\n" + "=" * 88)
    print(f"  RESULTS — avg across tickers, {args.horizon}-day forward returns")
    print("=" * 88)
    print(f"{'signal':28} {'ON':>8} {'OFF':>8} {'spread':>8} {'t':>7} "
          f"{'%time ON':>9} {'consistent':>11}")
    print("-" * 88)

    rows = []
    for name, lst in per_signal.items():
        k = len(lst)
        spread = sum(x["spread"] for x in lst) / k
        # "consistent" = share of tickers where the spread has the same sign
        # as the average. A real signal works on most names, not just one.
        same = sum(1 for x in lst if np.sign(x["spread"]) == np.sign(spread))
        rows.append({
            "signal": name,
            "mean_on": sum(x["mean_on"] for x in lst) / k,
            "mean_off": sum(x["mean_off"] for x in lst) / k,
            "spread": spread,
            "t_stat": sum(x["t_stat"] for x in lst) / k,
            "pct_on": sum(x["pct_on"] for x in lst) / k,
            "consistent": same / k * 100,
            "n_tickers": k,
            "detail": lst,
        })

    for r in sorted(rows, key=lambda r: -abs(r["spread"])):
        print(f"{r['signal']:28} {r['mean_on']:>+7.2f}% {r['mean_off']:>+7.2f}% "
              f"{r['spread']:>+7.2f}% {r['t_stat']:>7.2f} {r['pct_on']:>8.0f}% "
              f"{r['consistent']:>10.0f}%")

    print("\n" + "=" * 88)
    print("  VERDICT")
    print("=" * 88)
    strong = [r for r in rows if abs(r["t_stat"]) >= 2.0 and r["consistent"] >= 80]
    best = max(rows, key=lambda r: abs(r["spread"]))
    print(f"  Best spread : {best['signal']}  {best['spread']:+.2f}% per "
          f"{args.horizon}d  (t={best['t_stat']:.2f}, "
          f"consistent on {best['consistent']:.0f}% of tickers)")
    if strong:
        print(f"\n  {len(strong)} signal(s) cleared |t|>=2 AND worked on >=80% of tickers:")
        for r in strong:
            ann = ((1 + r["spread"] / 100) ** (252 / args.horizon) - 1) * 100
            print(f"    - {r['signal']}: {r['spread']:+.2f}%/{args.horizon}d "
                  f"(~{ann:+.1f}%/yr gross, t={r['t_stat']:.2f})")
        print("\n  Worth pursuing — but confirm on other windows/tickers before building.")
    else:
        print("\n  NO signal cleared the bar (|t|>=2 and consistent on >=80% of tickers).")
        print("  Nothing here reliably predicts forward returns, which is why the")
        print("  regime-switching and long/short variants had no edge to multiply.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = os.path.join(RESULTS_DIR, f"signals_{stamp}.json")
    with open(out, "w") as f:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"),
                   "years": args.years, "horizon": args.horizon,
                   "tickers": tickers, "rows": rows}, f, indent=2, default=str)
    print(f"\n  Detail saved to: {out}\n")


if __name__ == "__main__":
    main()
