# TradingView / Manual Trading

`dhan_bot_signals.pine` mirrors the Python bot's decision logic on a chart, so
you can see the same BUY/SELL calls and trade them by hand.

## Install (2 minutes)

1. Open TradingView → any NSE symbol (e.g. `NSE:KOTAKBANK`)
2. Set the chart to **5 minutes** (matches the bot)
3. Bottom panel → **Pine Editor**
4. Paste the contents of `dhan_bot_signals.pine`, click **Save**, then **Add to chart**

## What you'll see

- **Green BUY / red SELL labels** — where the bot's consensus rule fires
- **Grey ✕** — a BUY the bot would have *skipped* (past 15:00, dead market, or
  inside the re-entry cooldown). Useful for understanding why nothing fired.
- **Vote table** (top right) — each strategy's current call, the consensus, live
  ATR/close, which gate is blocking, and whether a position is open
- **Bollinger bands + the two moving averages**

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
