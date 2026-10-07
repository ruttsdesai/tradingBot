# Test plan — cleaned EzAlgo on gold

**Pre-registered 2026-10-06, before any gold results were seen.**
Do not edit the pass/fail rules after results arrive. If a rule turns out to be
wrong, record the change and why, below the original — never in place.

Indicator under test: `tradingview/ezalgo_cleaned.pine`, default settings
(Supertrend on close, ×2 ATR(11); 4×ATR(14) stop; exit on opposite signal;
Sell opens a short; NSE intraday rules off — gold trades 23h × 5d).

## Stage 0 — rules fixed in advance

- **Timeframes:** 1-hour, 4-hour, daily. Defaults only in Stage 1; no tuning.
- **Costs:** 0.01%/side base, 0.03%/side stress. Overnight financing added in Stage 2.
- **Benchmarks:** buy-and-hold gold, and a *timing-skill* control — return per bar
  while long / while short versus gold's own average bar. Gold roughly doubled over
  the window, so a long-heavy strategy looks good without any skill; this control
  separates the two.
- **PASS requires all of, on at least one timeframe:**
  1. net per-trade t > 2 **at 0.03%/side**;
  2. positive in **both** halves — Oct 2021–Feb 2024 (range-bound gold) and
     Mar 2024–Oct 2026 (bull run);
  3. positive timing skill on **both** long and short sides, **or** a higher
     Sharpe than buy-and-hold.
- **Any miss → STOP.** No parameter tuning to rescue a failed stage.

## Stage 1 — 5-year backtest
Spot XAUUSD (Dukascopy hourly BID), Oct 2021 → Oct 2026; 4h and daily resampled
at the 22:00 UTC session roll. Bar-level equity curve; fills at next bar's open.
Report: total/CAGR, Sharpe, max drawdown, trades, win rate, profit factor, by year,
split halves, timing skill.

## Stage 2 — robustness (only if Stage 1 passes)
1. Parameter neighbourhood: factor 1.5–3, ATR 7–20 — must be a plateau, not a spike.
2. Walk-forward: choose on rolling 2 years, test the next 6 months; only OOS counts.
3. Costs 0.05%/side + overnight financing.
4. Second data source: COMEX gold futures (`GC=F`).
5. Untouched asset: silver (XAGUSD).
6. Monte Carlo trade reshuffle for the realistic drawdown range.

## Stage 3 — forward paper (6–8 weeks, ≥ 30 trades)
Live alerts; log every signal and the fill actually available. Stop if live
per-trade results fall below the backtest's 5th percentile. Inconclusive is an
answer, not grounds to extend.

## Stage 4 — small live
Minimum size, to measure real spread / financing / slippage against the model.
Scale only after ~30 trades consistent with the backtest.

## Instrument note
Indian residents may not trade XAUUSD via offshore CFD/forex brokers (RBI alert
list; FEMA). Legal routes: MCX gold futures (GOLD, GOLDM, GOLDPETAL — available
on Dhan) or gold ETFs. Stage 1 on XAUUSD answers whether the idea works at all;
if it passes, Stages 2–4 must use MCX prices, costs and session hours
(09:00–23:30 IST).

---

# Stage 1 RESULT — 2026-10-07: **FAIL on every timeframe → STOP**

Data: Dukascopy spot XAUUSD hourly BID, 06 Oct 2021 → 06 Oct 2026 (32,637 bars,
no gaps beyond weekends/holidays). Gold $1,759 → $4,166. Buy-and-hold: **+137%,
CAGR +18.8%, Sharpe 1.02, max drawdown −27%.** Rules above were not edited.

**Pre-registered subject (long + short):**

| | Rule 1: per-trade t @0.03%/side | Rule 2: both halves positive | Rule 3: timing skill both sides / Sharpe > B&H | Verdict |
|---|---|---|---|---|
| 1-hour | −1.45 ✗ | −8.3% / +5.4% CAGR ✗ | +0.13 / +0.12 bp (t ≈ 0.6) — positive, not distinguishable from 0; Sharpe 0.01 | **FAIL** |
| 4-hour | +0.40 ✗ | −3.2% / +11.8% ✗ | +0.41 / +0.46 bp (t ≈ 0.6) — same; Sharpe 0.33 | **FAIL** |
| Daily | −0.26 ✗ | +4.8% / −9.7% ✗ | −0.74 / −0.78 bp ✗; Sharpe −0.07 | **FAIL** |

Five-year totals (long+short, 0.01%/side): 1h −7%, 4h +24%, daily −14% — against +137% for holding.

**Supplementary, NOT pre-registered — long-only:** 1h +57%, 4h +79%, daily +48% at
0.01%/side (Sharpe 0.76 / 0.99 / 0.65; in the market ~51% of the time). Every one is
below buy-and-hold on return and Sharpe. Control: sliding the same in/out pattern to
1,000 random start points, random timing matched or beat the real timing 16% (1h),
20% (4h) and 55% (daily) of the time. Most of the long-only return is simply being long
gold about half the time in a bull market; the timing on 1h/4h is suggestive, not
significant (per-trade t 0.73 / 1.61 at 0.03%), and on 1h costs consume most of it.

Per the plan: no parameter tuning, Stages 2–4 not run.

---

**Correction 2026-10-07 (instrument note only; no test rule changed).** The user is
based in **Canada**, not India — the "Instrument note" above was a wrong assumption.
In Canada, trading spot XAUUSD is legal through a dealer regulated by **CIRO** and
covered by **CIPF**; offshore unregistered brokers are what to avoid. The Stage 1
backtest is therefore directly on the instrument the user would trade, and no MCX
re-validation is needed. Not modelled in Stage 1: overnight financing (swap) on
CFD/spot-metal positions — since the long+short system is in the market ~100% of the
time, including it could only make the FAIL result worse.
