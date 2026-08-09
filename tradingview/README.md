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
