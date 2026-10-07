# Strategy Lab

A sandbox for testing changes **before** they touch the live bot.

The live bot is not the place to try ideas. A forward paper day costs a full
trading session, reports **gross** P&L, and mixes your change in with market
noise. The lab replays weeks of real 5m bars in seconds and charges brokerage,
STT and slippage on every fill — so an idea has to prove itself on
**net-of-cost** numbers first.

---

## The pipeline

Nothing reaches real money without clearing every stage:

| Stage | What it is | Speed | Faithful? |
|---|---|---|---|
| **1. LAB** | `lab/run_experiment.py` — replays cached bars, all costs charged | seconds | approximate |
| **2. PAPER** | `dhan-live --paper` — live prices, real consensus vote, simulated fills | 1 day/point | faithful |
| **3. LIVE** | `dhan-live --live` | real money | — |

**A lab win is necessary but not sufficient.** The lab runs one strategy at a
time; the live bot trades on a *consensus vote* across three. So use the lab to
**rank** options and **find levels** (which tickers, which trailing-stop %),
then confirm the winner in paper trading before promoting.

---

## Running it

```bash
python lab/run_experiment.py                  # default variants, 55 days
python lab/run_experiment.py --list           # show variants, run nothing
python lab/run_experiment.py --days 30        # shorter window
python lab/run_experiment.py --variants baseline,trail_0.5
python lab/run_experiment.py --tickers TATAMOTORS.NS,ADANIENT.NS
python lab/run_experiment.py --cost-pct 0.001 # stress-test double costs
```

Bars are cached under `data/lab_cache/` — the first run downloads, later runs
are instant. Use `--refresh` to re-download.

Every run writes full per-ticker/per-strategy detail to `lab/results/`.

### Reading the output

Variants are sorted by average **net** return, and compared against `baseline`
(the current live settings). The summary line tells you the delta in percentage
points and how the trade count changed — watch that number: a variant that wins
by trading *more* is usually just taking more cost risk.

If nothing beats `baseline`, **promote nothing.** That is a real result.

---

## Adding an experiment

Edit the `VARIANTS` dict in `run_experiment.py`:

```python
"my_idea": {
    "desc": "One line explaining what this tests",
    "params": {"trailing_stop_enabled": True, "trailing_stop_pct": 0.004},
},
```

Supported params: `stop_loss_pct`, `take_profit_pct`, `max_daily_loss_pct`,
`trailing_stop_enabled`, `trailing_stop_pct`, `trailing_stop_atr_mult`,
`max_hold_minutes`, `min_profit_threshold_pct`, `min_volatility_pct`.

**Change one thing at a time.** If you sweep three parameters at once and the
result improves, you have learned nothing about which one mattered.

---

## Promoting a winner

The lab **never** writes to `config.yaml` or `start_paper_bot.bat`. Promotion is
deliberate and manual:

1. Variant beats `baseline` in the lab by a margin that isn't noise
2. It still wins when you stress the costs (`--cost-pct 0.001`)
3. Copy the winning params into `config.yaml` under `dhan_live_trading`
   (tickers go in `start_paper_bot.bat`)
4. Run it in **paper** for several days
5. Compare the paper ledger against the previous baseline days
6. Only then consider real money

---

## Guardrails

- **Overfitting is the main risk.** ~55 days of 5m bars is a small sample. If a
  variant wins by a hair, or only on one ticker, treat it as noise.
- **Costs dominate at this trade frequency.** Any change that increases trades
  needs a large edge to pay for itself. Trade count is in the results table for
  exactly this reason.
- **The lab does not model consensus voting** (see above), overnight gaps, or
  real fill slippage beyond the flat cost assumption.
- **Sample size beats cleverness.** A variant that wins by 0.1pp over 55 days is
  not a discovery.

---

## `run_consensus.py` — does requiring more strategies to agree help?

Closes a gap the other scripts name in their own docstrings: the lab ran one
strategy at a time and had never modelled the **consensus vote** the live bot
actually trades. `LiveConsensusStrategy` mirrors `dhan_live_trader` exactly —
BUY when BUY votes > SELL votes and >= `min_agree`, SELL when SELL > BUY, HOLD
abstains — using the **config** parameters (Bollinger 10/1.5, not the library
default 20/2.0).

`strategies/ensemble.py` is deliberately not reused: it exits the moment
buy-consensus is lost, a much tighter exit rule, which would confound the entry
threshold under test with an exit change.

```
python lab/run_consensus.py                    # 5m, 55 days, live basket
python lab/run_consensus.py --cost-pct 0.001   # stress at double costs
```

### Result (2026-08-21): more agreement is worse

| `min_agree` | avg return | trades | Rs/round-trip | at 2x costs |
|---|---|---|---|---|
| 1 (live setting) | +1.13% | 890 | +12.66 | -2.80% |
| 2 | +0.00% | 174 | +0.24 | -0.99% |
| 3 | never fires | 0 | — | — |

Motivated by three weeks of paper logs where the 6 live entries with 2-of-3
agreement returned +Rs314 against +Rs27 for the other 67. That was a post-hoc
slice of n=6 (Welch t=+1.89) and it did not survive: the threshold trades 5x
less and earns nothing per trade. **Do not ship 2-of-3.**

Watch `Rs/round-trip`, not total return — it separates "better signal" from
"merely fewer trades", which is the failure mode every filter tested here has
turned out to have.

---

## `run_xsectional.py` — is a stock cheap RELATIVE TO ITS PEERS?

The first test here that asks a structurally different question. Everything
else — RSI, Bollinger, MA, MACD, momentum, regime, volume, MTF — asks "will
this stock go up?" and computes a published indicator on one price series.
This ranks sector peers against each other, buys the laggard, shorts the
leader, and holds. The object under test is a **spread**, which has an
economic anchor a price level does not, and it is market-neutral by
construction.

```
python lab/run_xsectional.py                    # 5m bars, intraday only
python lab/run_xsectional.py --daily --gross    # daily bars, signal isolated
python lab/run_xsectional.py --daily --control  # + momentum sign-flip check
```

Universe: 38 NSE large caps in 8 sectors, 5 years of daily bars
(`data/xsec_cache/`, git-ignored, re-fetchable). Rebalances are
**non-overlapping**, so each observation is independent and the t-stat needs
no overlap correction. Costs are charged on all **four** fills of a
long/short round trip.

**Two guards worth copying into any future experiment here:**

- `--control` runs momentum, the exact sign-flip of reversal. The two net
  means must sum to precisely `-2 x cost`. If both ever look good, the
  harness is broken, not the market.
- The **split-sample check runs automatically** on the strongest cell. This is
  the step that has killed every candidate found in this repo, so it is not
  left to the reader.

### Result: a real effect, one-third the size it needs to be

**Intraday (5m) — dead.** Gross edge under 1bp against a 20bp round-trip cost.
Not close, and the pattern across lookbacks is ragged, which the
pre-registered prediction named in advance as the signature of noise.

**Daily — a genuine finding.** 1-day cross-sectional reversal, `L=3, H=1`:

| | mean | t | n |
|---|---|---|---|
| Full sample | **+6.76 bp/day** | **+3.80** | 9,872 |
| First half | +6.20 bp | +2.50 | 4,936 |
| Second half | +7.72 bp | +3.00 | 4,936 |

It survives the split-sample check, it is positive in all four quarters of
the sample, and it got *stronger* as tickers were added — the opposite of
what noise does. **This is the only effect in this repo to survive that
gauntlet.** Gross Sharpe ≈ 0.58.

**And it is still not tradeable.** Four fills cost ~20bp; the effect is 6.8bp.

| Per-fill cost | Round trip | Net/day | |
|---|---|---|---|
| 5.0 bp — cash equity, small size | 20 bp | −13.1 bp | loses money |
| 3.5 bp — cash equity, ₹2L+ positions | 14 bp | −7.1 bp | loses money |
| 1.5 bp — single-stock futures, large | 6 bp | +0.9 bp | marginal |
| **1.74 bp** | **7 bp** | **0** | **breakeven** |

Longer holds do not rescue it: the effect is specifically a *1-day* one, so
stretching the horizon adds noise without adding edge (H=20..60 is ragged and
insignificant on collapsing sample sizes).

**The transferable lesson.** Two independent investigations — the live equity
bot and this one — landed on the same shape: *the signal is real and the cost
is several times larger than the signal.* Cost per round trip, not signal
discovery, is the binding constraint on this whole enterprise.
