# TradingView / Manual Trading

Two scripts:

| File | For | Best timeframe |
|---|---|---|
| `dhan_bot_signals.pine` | NSE equities — mirrors the live bot exactly | 5m |
| `crypto_bot_signals.pine` | BTC / ETH / crypto — 24/7, rescaled | 4H or 1D |


`dhan_bot_signals.pine` mirrors the Python bot's decision logic on a chart, so
you can see the same BUY/SELL calls and trade them by hand.

## Install (2 minutes)

1. Open TradingView → any NSE symbol (e.g. `NSE:KOTAKBANK`)
2. Set the chart to **5 minutes** (matches the bot)
3. Bottom panel → **Pine Editor**
4. Paste the contents of `dhan_bot_signals.pine`, click **Save**, then **Add to chart**

## What you'll see

- **LIVE ACTION PANEL** (top right) — the first row is the answer to "what do I
  do right now?": `BUY NOW`, `SELL NOW`, `HOLDING`, `BUY BLOCKED`, or `WAIT`,
  colour-coded. Below it: each strategy's vote, the consensus, how many bars ago
  the last signal fired, position state, ATR/close, which gate is blocking, and
  the timestamp of the bar being read.
- **Green BUY / red SELL labels** — where the bot's consensus rule fires
- **Grey ✕** — a BUY the bot would have *skipped* (past 15:00, dead market, or
  inside the re-entry cooldown). Useful for understanding why nothing fired.
- **Bollinger bands + the two moving averages**

### "The panel doesn't move when I scroll the chart"

That is correct behaviour. The panel is anchored to the top-right of the
*viewport*, not to a bar, and it always reports the **latest** candle — that is
what makes it a live readout rather than a history browser. Scrolling back to
look at old bars does not change it.

If it isn't changing at all, check the **Bar time** row. NSE trades 09:15–15:30
IST on weekdays; outside those hours the last candle is frozen and there is
nothing new to compute. Crypto charts run 24/7, so that row should always be
recent.

## It matches the bot, not the textbook

Parameters come from `config.yaml`, which differs from the library defaults:

| Strategy | Setting | Note |
|---|---|---|
| RSI Mean Reversion | RSI(14), buy < 30, sell > 70 | |
| Bollinger Bands | SMA(**10**) ± **1.5** std | **not** the usual 20 ± 2.0 |
| MA Crossover | SMA(20) vs SMA(50) | fires **only on the cross bar** |

The consensus rule is the bot's: count BUY votes against SELL votes and act on
the majority. A tie or all-HOLD does nothing — HOLD abstains, it does not veto.

Intraday guards are also mirrored: no new BUYs after 15:00, flat by 15:10,
BUYs skipped when ATR/close < 0.15%, and a 15-minute re-entry cooldown.

## Alerts

Right-click the chart → **Add alert** → Condition: *Dhan Bot Signals* → pick
`Bot BUY` or `Bot SELL`. TradingView can then notify you by app, email, or SMS
so you don't have to watch the screen.

## Two honest caveats

**1. This mirrors a strategy that has not been shown to work.** Over the paper
test to date the account is slightly down, and the backtests put it below simply
buying and holding. These signals are a structured second opinion for
discretionary trading — not calls to follow blindly.

**2. Small differences from the bot are expected.** TradingView computes on
completed candles from its own data feed; the bot polls every 60 seconds against
Yahoo data and can act mid-candle. Signals will usually agree, but timing and
the occasional marginal call will not match exactly.

## Dhan

Dhan has no Pine Script equivalent — it isn't a charting-script platform in that
sense. The practical path is to keep the signals in TradingView and place orders
in Dhan manually, or use TradingView alerts as the prompt to act.


---

# Crypto script (`crypto_bot_signals.pine`)

Not a copy of the NSE one — crypto differs in ways that make a naive port wrong:

- **24/7 market** → the 15:00 stop-buy and 15:10 square-off rules are meaningless and are removed
- **~10x the volatility** (BTC moves 2-4%/day vs ~0.3% intraday on NSE large-caps) → volatility filter
  raised from 0.15% to 1.0%, and the trailing stop from 0.8% to 8%. A 0.8% trail would be stopped out
  by ordinary hourly noise.
- **Costs are HIGHER, not lower** — Binance taker ~0.1%/side (~0.2% round trip) vs ~0.1% round trip on
  Dhan. Frequent trading is punished harder, which is why it defaults to 4H/1D rather than 5m.
- Adds **momentum breakout**, which was the best performer on ETH.

## What the backtest actually said

5 years of daily bars, costs charged on both sides:

| BTC-USD | CAGR | vs buy & hold |
|---|---|---|
| **buy & hold** | **+7.3%/yr** | — |
| rsi_mean_revert | +8.2% | **+0.9** |
| macd | +5.1% | -2.1 |
| ma_crossover | +4.1% | -3.1 |
| momentum | +2.3% | -4.9 |
| bollinger | +0.8% | -6.4 |

| ETH-USD | CAGR | vs buy & hold |
|---|---|---|
| **buy & hold** | **-9.4%/yr** | — |
| momentum | +12.3% | **+21.7** |
| ma_crossover | +4.8% | +14.2 |
| rsi_mean_revert | +3.1% | +12.5 |

**Read this honestly.** The strategies beat buy & hold on ETH mainly because **ETH fell 39%** and they
sat out part of the decline. On BTC, which rose, only one edged past holding — by 0.9pp, well inside
noise. That is the same *insurance, not alpha* pattern found on NSE: helpful in downtrends, a drag in
uptrends. Drawdowns were also severe, -23% to -58%.

So crypto is not a way around the earlier conclusion. What it does avoid is the cost problem — at
~10 trades/year the cost drag is negligible, unlike NSE intraday's ~53%/yr.


---

# `ezalgo_cleaned.pine` — a third-party indicator, fixed and made honest

A cleaned-up version of the free "EzAlgo" script (MPL-2.0, © Pineify). Its
Buy/Sell signal is a single Supertrend (ATR 11, ×2, centred on `close`); the
cloud, bands, diamonds, QQE and S&R layers are visual context.

**What changed**

- **Levels go live when a trade fills.** Entry/stop/targets appear at the open of the
  entry candle (or as *planned* levels the moment the signal candle closes), not a
  candle later.
- **No repainting.** Signals count only on closed bars. A faint triangle marks a
  signal still forming on the live bar ("forming — wait for close").
- **Every exit is drawn with its net P&L, losses included.** The original marked
  target hits with ✕ and never showed a losing exit.
- **Stats panel** (bottom right): trades, win rate, average net per trade,
  profit factor, open-trade P&L, all after the cost you set. Fills are assumed
  at the next bar's open.
- Fixed: entry/stop/target lines anchored to the wrong bar after a Sell;
  "Show bands" toggle that did nothing; S&R zones creating 8 new lines on every
  bar; dead inputs and code; misleading labels ("Sensitivity" worked backwards,
  "Risk To Reward" was the stop distance).
- New options: band source (`close` vs standard `hl2`), 200-EMA trend filter,
  long-only, NSE intraday rules (no entries after 15:00, flat at 15:10 IST).

**What testing found (2026-10, 20 NSE large caps)**

| | Result |
|---|---|
| 5m, gross | **+0.0 bp/trade**, profit factor 1.00 — no signal at all |
| 5m, net 0.05%/side | −10 bp/trade, t = −12, win 31% |
| Walk-forward over 160 settings × 4 timeframes | **no setting positive on held-out data with significance** |
| Daily long-only, 5 years | +0.1%/yr vs +5.8%/yr buy-and-hold |

**Signal accuracy tracker** (bottom-left panel). Was each signal *right*? A signal is
correct if price moved its way between the indicator's entry (next open) and its exit —
judged **before costs**, on direction alone; the stats panel shows what it earned after
costs. Split into Buy and Sell signals, with the average final move when right / wrong
and how far each group had *peaked* in your favour before the exit.

The first version counted how often price reached SL and TP levels. With the stop
~5 ATR away and the opposite signal closing ~70% of trades first, the stop was reached
in only 0-2% of trades, so it measured almost nothing. Replaced on user feedback.

Baseline, XAUUSD Oct 2021 - Oct 2026, default settings (Pine logic ported to Python and
checked against the independent Stage 1 engine — identical):

| Chart | Correct | Buys / Sells right | When right (peak) | When wrong (peak) |
|---|---|---|---|---|
| 1-hour | 37.3% of 1,438 | 41% / 33% | +0.97% (+1.93%) | -0.54% (+0.37%) |
| 4-hour | 38.7% of 385 | 46% / 32% | +2.03% (+3.98%) | -1.10% (+0.66%) |
| Daily | 34.8% of 69 | 43% / 27% | +4.99% (+10.81%) | -2.84% (+1.88%) |

Winners give back about half their peak before the exit; 26% of losing 1h trades were
up at least +0.5% first. An observation about exits, not yet a tested fix.

**Your own trade ("My trade" in Settings).** TradingView gives indicators no access
to your account, orders or screen, so the indicator cannot detect an order by
itself. Set *My position* (Long/Short) and *My entry price* when you open a trade,
and back to *None* when you close it. Then the ACTION box shows your live P&L and
warns when the indicator turns against you, the TP/SL lines are drawn from your
entry (dashed, labelled "My ..."), and three extra alerts cover your trade. If you
trade *with* the indicator, your stop is its signal-anchored stop; against it, the
stop is 4 x ATR(14) from your entry.

**Alerts** (right-click chart → Add alert → Condition: *EzAlgo (cleaned)*)

| Alert | Fires | Frequency to choose |
|---|---|---|
| Buy / Sell (confirmed) | at the close of the signal candle | Once per bar close |
| Exit signal | closing signal confirmed — exit at next open | Once per bar close |
| Stop-loss hit / Target hit / 15:10 square-off | at the close of the candle it happened in | Once per bar close |
| Any exit | any of the above | Once per bar close |
| **Stop touched – LIVE / Target touched – LIVE** | **mid-candle, the moment price touches the level** | **Once per bar** |
| *Any alert() function call* | every exit, with reason, price and net P&L in the message | — |

The LIVE alerts exist because the chart labels only appear at candle close. They
are a warning, not protection: put the real stop in Dhan as an order.

**Multiple TP / SL levels — tested, not added.** Ten exit ladders (1–3 targets,
scale-outs, breakeven after TP1, 1–3 staged stops, the full ladder) on the same
signal and fills:

| | best ladder vs baseline (net, bp/trade) |
|---|---|
| 5m | −9.92 vs −9.99 — every variant within ±1bp |
| 15m | −13.59 vs −14.37 — all lose |
| Daily long-only | **every target ladder is worse** (+7 to +13 vs +17.6, none significant) |

Ladders change the *shape* of results (win rate, size of wins) but not the
expectancy, and a zero-edge signal stays zero. On daily bars targets actively
hurt: trend following lives off a few large winners, and targets cap them.
Breakeven stops did nothing at all on 5m, because by the time TP1 is reached the
Supertrend line is already above entry in 95% of trades — the signal exit
always fires first. The indicator's own line is already a trailing stop.

Use it for chart context if you like it. Don't take entries from it — and if
you do, the stats panel will tell you what it is costing.
