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
