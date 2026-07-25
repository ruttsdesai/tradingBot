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
