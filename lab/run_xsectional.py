"""
Cross-Sectional Lab — is a stock cheap RELATIVE TO ITS PEERS?

Why this is not another variation on the same idea
--------------------------------------------------
Everything tested in this repo so far -- RSI, Bollinger, MA crossover, MACD,
momentum, regime filters, volume, multi-timeframe -- asks one question:
"will this stock go up?" All of them are published, 40-year-old technical
indicators computed on a single price series. Six independent tests found
nothing clearing |t| >= 2, which is exactly what theory predicts for the
most-arbitraged signals in existence.

This asks a structurally different question: "has ASIANPAINT fallen more than
its sector peers, and does that gap close?" The object under test is a SPREAD
between correlated names, not a price level. Two reasons that matters:

  1. A spread has an economic anchor (the names share sector-wide shocks), so
     mean reversion in it has a mechanism. Mean reversion in a price level
     does not -- which is probably why every test of it here has failed.
  2. It is market-neutral by construction: equal notional long and short.
     Index direction cancels. That removes the exposure that dominated and
     ultimately sank the long-only and long/short tests.

Method
------
Every H bars (NON-OVERLAPPING, so each observation is independent and the
t-stat needs no overlap correction -- this repo has been burned by overlapping
windows before), within each sector:

    rank members by return over the last L bars
      -> LONG the biggest underperformer, SHORT the biggest outperformer
      -> hold H bars, close, repeat

Equal notional per leg. Intraday only: no signal crosses the 15:10 square-off,
because holding a cash-equity short overnight is not permitted in India (that
needs futures). Costs are charged on all FOUR fills of a long/short round trip
-- roughly double a directional trade, and the hurdle this has to clear.

`momentum` (long the winner, short the loser) is run as a CONTROL, not a
candidate. It is the sign-flip of reversal, so it must come out as the mirror
image. If both look good, the harness is broken.

PRE-REGISTERED PREDICTION (written before the first run; do not edit after)
--------------------------------------------------------------------------
1. NET edge at 5m: NONE. A 4-fill round trip costs ~0.21%, and intraday
   cross-sectional reversal in liquid large caps is documented to be small and
   largely bid-ask bounce.
2. GROSS reversal: positive, possibly significant at the SHORTEST lookback,
   shrinking as L grows -- because the effect is a liquidity-provision premium
   that decays.
3. THE FALSIFIABLE PART: if the effect is real it must DECAY MONOTONICALLY
   with L. A ragged pattern across L, or a peak at some middle value, is
   noise wearing a pattern's clothes and will be reported as such.
4. Best guess: gross t in 1..3 at L=6; net t < 1 everywhere.
5. Multiple testing: 9 (L, H) combinations are swept, so the bar for calling
   anything real is |t| > 2.7, not 2.0.

    python lab/run_xsectional.py                  # 5m bars, sector-neutral
    python lab/run_xsectional.py --cost-pct 0.001 # stress at double costs
    python lab/run_xsectional.py --gross          # costs off, to isolate signal
"""

import argparse
import json
import os
import statistics as st
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "xsec_cache")

SECTORS = {
    "BANK":   ["HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS",
               "SBIN.NS", "INDUSINDBK.NS"],
    "IT":     ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
    "AUTO":   ["MARUTI.NS", "M&M.NS", "TATAMOTORS.NS", "BAJAJ-AUTO.NS",
               "HEROMOTOCO.NS", "EICHERMOT.NS"],
    "PHARMA": ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "LUPIN.NS"],
    "FMCG":   ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS"],
    "METAL":  ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS"],
    "ENERGY": ["RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS"],
    "CEMENT": ["ULTRACEMCO.NS", "GRASIM.NS", "AMBUJACEM.NS", "SHREECEM.NS"],
    "NBFC":   ["BAJFINANCE.NS", "BAJAJFINSV.NS", "HDFCLIFE.NS", "SBILIFE.NS"],
}

SQUARE_OFF_MIN = 15 * 60 + 10       # 15:10 IST, matching the live bot


def _safe(t: str) -> str:
    return t.replace(".", "_").replace("&", "and").replace("-", "_")


def load_panel(interval: str) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Load every cached ticker into one wide close-price frame (inner join)."""
    series, sectors = {}, {}
    for sec, tickers in SECTORS.items():
        got = []
        for t in tickers:
            p = os.path.join(CACHE, f"{_safe(t)}_{interval}.csv")
            if not os.path.exists(p):
                continue
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            if df.empty or "close" not in df.columns:
                continue
            series[t] = df["close"]
            got.append(t)
        if len(got) >= 3:               # a sector needs 3+ names to rank within
            sectors[sec] = got
    panel = pd.DataFrame(series).sort_index()
    keep = [t for names in sectors.values() for t in names]
    panel = panel[keep].dropna(how="any")
    return panel, sectors


def run(panel: pd.DataFrame, sectors: dict, L: int, H: int,
        cost_pct: float, direction: str = "reversal", intraday: bool = True) -> dict:
    """Non-overlapping sector-neutral long/short. Returns per-rebalance stats.

    `intraday=False` runs on daily bars and drops the same-session constraint.
    Holding a sector-neutral book overnight means the short leg needs single
    stock FUTURES -- India does not permit overnight shorts in cash equity.
    """
    idx = panel.index
    minutes = np.array([ts.hour * 60 + ts.minute for ts in idx])
    days = np.array([ts.date() for ts in idx])
    px = panel.values
    cols = {t: i for i, t in enumerate(panel.columns)}

    legs: list[float] = []              # one net return per leg-pair per rebalance
    t0 = L
    while t0 + H < len(idx):
        # Intraday: never hold across the square-off or a day boundary.
        if intraday and (days[t0] != days[t0 + H] or minutes[t0 + H] > SQUARE_OFF_MIN):
            t0 += 1
            continue

        for sec, names in sectors.items():
            ii = [cols[n] for n in names]
            past = px[t0, ii] / px[t0 - L, ii] - 1.0
            if not np.isfinite(past).all():
                continue
            past = past - past.mean()            # sector-neutral
            lo, hi = int(np.argmin(past)), int(np.argmax(past))
            if lo == hi:
                continue

            fwd = px[t0 + H, ii] / px[t0, ii] - 1.0
            if direction == "reversal":
                long_i, short_i = lo, hi         # buy the laggard
            else:
                long_i, short_i = hi, lo         # control: buy the winner

            gross = fwd[long_i] - fwd[short_i]
            # 4 fills: enter long, enter short, exit long, exit short.
            legs.append(gross - 4.0 * cost_pct)

        t0 += H                                   # non-overlapping

    if len(legs) < 30:
        return {"L": L, "H": H, "n": len(legs)}

    m, s = st.mean(legs), st.stdev(legs)
    t = m / (s / np.sqrt(len(legs)))
    return {
        "L": L, "H": H, "n": len(legs),
        "mean_bp": m * 1e4,
        "t": t,
        "win_rate": sum(1 for x in legs if x > 0) / len(legs) * 100,
        "total_pct": sum(legs) * 100,
        "sd_bp": s * 1e4,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Cross-sectional mean reversion")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--cost-pct", type=float, default=0.0005,
                    help="Per-fill cost. A long/short round trip pays this 4x.")
    ap.add_argument("--gross", action="store_true", help="Costs off — isolate the signal")
    ap.add_argument("--control", action="store_true", help="Also run the momentum sign-flip")
    ap.add_argument("--daily", action="store_true",
                    help="Daily bars, multi-day holds (short leg needs stock futures)")
    args = ap.parse_args()
    if args.daily:
        args.interval = "1d"
    cost = 0.0 if args.gross else args.cost_pct

    panel, sectors = load_panel(args.interval)
    print(f"\nPanel: {panel.shape[1]} tickers x {panel.shape[0]:,} bars "
          f"({args.interval}), {len(sectors)} sectors")
    for sec, names in sectors.items():
        print(f"  {sec:<8} {len(names)}  {', '.join(n.replace('.NS','') for n in names)}")
    print(f"\nCost: {cost:.4%}/fill  =>  {4*cost:.3%} per long/short round trip"
          f"{'  (GROSS — costs off)' if args.gross else ''}")
    n_combos = 16 if args.daily else 9
    print(f"Bar for significance with {n_combos} combos swept: |t| > 2.7\n")

    unit = "days" if args.daily else "bars"
    spans = (1, 3, 5, 10) if args.daily else (6, 12, 24)
    grid = [(L, H) for L in spans for H in spans]
    results = []
    print(f"  (L = lookback in {unit}, H = holding period in {unit})")
    print(f"  {'L':>4} {'H':>4} {'n':>6} {'mean(bp)':>10} {'t':>7} {'win%':>7} {'total%':>9}")
    print("  " + "-" * 52)
    for L, H in grid:
        r = run(panel, sectors, L, H, cost, "reversal", intraday=not args.daily)
        results.append(r)
        if r.get("n", 0) < 30:
            print(f"  {L:>4} {H:>4} {r.get('n',0):>6}   (too few observations)")
            continue
        flag = "  <-- clears 2.7" if abs(r["t"]) > 2.7 else ""
        print(f"  {L:>4} {H:>4} {r['n']:>6} {r['mean_bp']:>+10.2f} {r['t']:>+7.2f} "
              f"{r['win_rate']:>7.1f} {r['total_pct']:>+9.2f}{flag}")

    if args.control:
        print(f"\n  CONTROL — momentum (sign-flip; must mirror the above):")
        for L, H in grid:
            r = run(panel, sectors, L, H, cost, "momentum", intraday=not args.daily)
            if r.get("n", 0) >= 30:
                print(f"  {L:>4} {H:>4} {r['n']:>6} {r['mean_bp']:>+10.2f} {r['t']:>+7.2f}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = os.path.join(RESULTS_DIR, f"xsectional_{stamp}.json")
    with open(path, "w") as fh:
        json.dump({"interval": args.interval, "cost_pct": cost,
                   "tickers": list(panel.columns), "results": results}, fh, indent=2)
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
