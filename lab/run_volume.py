"""
Volume Lab — does intraday volume predict the NEXT candle?

Why this exists: the signal lab already tested `volume_above_avg` and found
nothing — but that test ran on DAILY bars against a 50-day average, which is
the wrong instrument for an intraday question. Intraday volume follows a fixed
U-shape (heavy at the open and close, thin midday), so a naive "volume above
average" test mostly detects TIME OF DAY rather than unusual activity.

This tests it properly: every measure below is either time-of-day adjusted or
self-normalising, so the daily volume shape cannot leak in as fake signal.

PRE-REGISTERED PREDICTION (written before running):
  Mostly no edge — every price-derived signal tested so far has come up empty
  and volume is drawn from the same public tape. The one I would least be
  surprised to see work is VWAP deviation, since reversion to VWAP is a real
  and widely-traded intraday phenomenon. I expect |t| < 2 for the rest.
  Recorded up front so a null result cannot be re-spun and a positive one has
  to genuinely surprise.

Method
------
For every 5m bar, compute the signal, then measure the return over the NEXT
1 / 3 / 6 bars (5m / 15m / 30m). Forward windows never cross a session
boundary — an overnight gap is not an intraday prediction. Overlapping
windows are not independent, so effective sample size is deflated by the
horizon before computing t (conservative).

Bar: |t| >= 2 AND same-sign on >= 80% of tickers.

Usage:
    python lab/run_volume.py --days 55
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from lab.run_experiment import LIVE_BASKET, load_bars, RESULTS_DIR


def build_volume_signals(df: pd.DataFrame) -> dict:
    """Volume signals, all immune to the intraday volume U-shape."""
    out = {}
    close = df["close"]
    vol = df["volume"].astype(float)
    day = df.index.date
    tod = df.index.time

    # --- 1. Relative volume vs the SAME TIME OF DAY on other days ----------
    # Expanding median per time slot, shifted so today's own value is excluded.
    tod_ser = pd.Series(tod, index=df.index)
    rel = pd.Series(index=df.index, dtype=float)
    for t, grp in vol.groupby(tod_ser):
        med = grp.expanding().median().shift(1)
        rel.loc[grp.index] = (grp / med).values
    out["relvol_tod_high"] = rel > 1.5      # busy *for this time of day*
    out["relvol_tod_dry"] = rel < 0.7       # unusually thin

    # --- 2. Volume surge vs trailing median (self-normalising) -------------
    med20 = vol.rolling(20).median()
    surge = vol / med20
    out["vol_surge_2x"] = surge > 2.0

    # --- 3. VWAP deviation (session VWAP, resets daily) --------------------
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum()
    vwap = pv / cv.replace(0, np.nan)
    out["above_vwap"] = close > vwap
    out["below_vwap"] = close < vwap        # the mean-reversion side

    # --- 4. Volume-price agreement ----------------------------------------
    ret1 = close.pct_change()
    out["up_on_high_vol"] = (ret1 > 0) & (rel > 1.2)     # conviction
    out["up_on_low_vol"] = (ret1 > 0) & (rel < 0.8)      # weak move
    out["down_on_high_vol"] = (ret1 < 0) & (rel > 1.5)   # capitulation

    return out


def evaluate(df: pd.DataFrame, sig: pd.Series, horizon: int) -> dict | None:
    """Forward return when signal is ON vs OFF, staying inside the session."""
    close = df["close"]
    day = pd.Series(df.index.date, index=df.index)

    fwd = close.shift(-horizon) / close - 1.0
    # invalidate windows that would cross into the next session
    same_session = day.shift(-horizon) == day
    fwd = fwd.where(same_session)

    s = sig.reindex(df.index).fillna(False).astype(bool)
    valid = fwd.notna()
    on, off = fwd[valid & s], fwd[valid & ~s]
    if len(on) < 100 or len(off) < 100:
        return None

    spread = on.mean() - off.mean()
    eff_on, eff_off = max(len(on) / horizon, 2), max(len(off) / horizon, 2)
    se = np.sqrt(on.var(ddof=1) / eff_on + off.var(ddof=1) / eff_off)
    t = spread / se if se > 0 else 0.0
    return {"mean_on": on.mean() * 100, "mean_off": off.mean() * 100,
            "spread": spread * 100, "t_stat": float(t),
            "n_on": int(len(on)), "pct_on": len(on) / (len(on) + len(off)) * 100}


def main():
    ap = argparse.ArgumentParser(description="Volume Lab — intraday volume predictive power")
    ap.add_argument("--days", type=int, default=55)
    ap.add_argument("--horizons", default="1,3,6",
                    help="Forward horizons in 5m bars (1=5min, 3=15min, 6=30min)")
    args = ap.parse_args()
    horizons = [int(h) for h in args.horizons.split(",")]

    print("=" * 92)
    print("  VOLUME LAB — does intraday volume predict the next candle?")
    print("=" * 92)
    print(f"  Basket   : {', '.join(LIVE_BASKET)}")
    print(f"  Horizons : {', '.join(f'{h*5}min' for h in horizons)}")
    print("  All measures are time-of-day adjusted or self-normalising, so the")
    print("  intraday volume U-shape cannot masquerade as signal.")
    print("  PREDICTION: mostly no edge; VWAP deviation the likeliest exception.")
    print("=" * 92)

    print("\nLoading 5m bars...")
    data = {}
    for t in LIVE_BASKET:
        df = load_bars(t, args.days, interval="5m")
        if df is not None and not df.empty:
            data[t] = df
            print(f"  {t:16} {len(df):>5} bars")

    all_rows = []
    for h in horizons:
        per_sig = {}
        for ticker, df in data.items():
            for name, s in build_volume_signals(df).items():
                r = evaluate(df, s, h)
                if r:
                    per_sig.setdefault(name, []).append({"ticker": ticker, **r})

        print("\n" + "=" * 92)
        print(f"  FORWARD {h*5} MINUTES")
        print("=" * 92)
        print(f"{'signal':22}{'ON':>9}{'OFF':>9}{'spread':>9}{'t':>7}"
              f"{'%time ON':>10}{'consistent':>12}")
        print("-" * 92)
        rows = []
        for name, lst in per_sig.items():
            k = len(lst)
            spread = sum(x["spread"] for x in lst) / k
            same = sum(1 for x in lst if np.sign(x["spread"]) == np.sign(spread))
            rows.append({"horizon_bars": h, "signal": name,
                         "mean_on": sum(x["mean_on"] for x in lst) / k,
                         "mean_off": sum(x["mean_off"] for x in lst) / k,
                         "spread": spread,
                         "t_stat": sum(x["t_stat"] for x in lst) / k,
                         "pct_on": sum(x["pct_on"] for x in lst) / k,
                         "consistent": same / k * 100, "n_tickers": k})
        for r in sorted(rows, key=lambda r: -abs(r["spread"])):
            print(f"{r['signal']:22}{r['mean_on']:>+8.3f}%{r['mean_off']:>+8.3f}%"
                  f"{r['spread']:>+8.3f}%{r['t_stat']:>7.2f}{r['pct_on']:>9.0f}%"
                  f"{r['consistent']:>11.0f}%")
        all_rows.extend(rows)

    print("\n" + "=" * 92)
    print("  VERDICT")
    print("=" * 92)
    strong = [r for r in all_rows if abs(r["t_stat"]) >= 2.0 and r["consistent"] >= 80]
    best = max(all_rows, key=lambda r: abs(r["t_stat"]))
    print(f"  Strongest: {best['signal']} @ {best['horizon_bars']*5}min — "
          f"spread {best['spread']:+.3f}%, t={best['t_stat']:.2f}, "
          f"consistent {best['consistent']:.0f}%")
    if strong:
        print(f"\n  {len(strong)} signal/horizon pair(s) cleared |t|>=2 AND >=80% consistency:")
        for r in sorted(strong, key=lambda r: -abs(r["t_stat"])):
            print(f"    - {r['signal']} @ {r['horizon_bars']*5}min: "
                  f"{r['spread']:+.3f}% (t={r['t_stat']:.2f}, on {r['pct_on']:.0f}% of bars)")
        print("\n  Worth a follow-up — but this is ONE 55-day sample and many")
        print("  signal/horizon pairs were examined, so re-test before believing it.")
    else:
        print("\n  NO volume signal cleared the bar. Volume does not predict the next")
        print("  candle here — this time tested properly, with the intraday volume")
        print("  U-shape removed.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = os.path.join(RESULTS_DIR, f"volume_{stamp}.json")
    with open(out, "w") as f:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"),
                   "days": args.days, "horizons": horizons, "rows": all_rows},
                  f, indent=2)
    print(f"\n  Detail saved to: {out}\n")


if __name__ == "__main__":
    main()
