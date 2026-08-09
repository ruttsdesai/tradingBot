# Chat Memory — tradingBot

> Updated: 2026-07-20
> Sessions: Dhan SL/TP, crypto multi-strategy, scheduler continuous, security IDs, live trading, SL/TP reconcile + OCO + dual scheduler, **paper-trading P&L fix + consensus voting + session logging**

---

## 🔴 HANDOFF — Read this first (2026-07-20 session)

**Branch:** `claude/continued-session-4tt3yo` (PR #1). All work below is committed + pushed here.

**What we're doing:** zero-cost forward paper testing of the Dhan intraday bot on ₹100k virtual
capital, 6 tickers (AXISBANK, ASIANPAINT, BAJFINANCE, M&M, SUNPHARMA, KOTAKBANK) × 3 strategies
(RSI_MeanReversion, BollingerBands, MA_Crossover). Goal: ~2 weeks of clean sessions → go/no-go.
User runs it on their **laptop** via double-clicking `start_paper_bot.bat` (leaves window open all day).

**Three bugs fixed this session (all pushed):**
1. **False daily-loss lockout (`-34.20%`) — FIXED.** `run_once` in `engine/dhan_live_trader.py`
   mutated `portfolio.positions` on every fill but never updated `portfolio.current_cash`, so
   `total_value` (= cash + position marks) dropped on each sell → spurious ~30% "loss" tripped the
   daily-loss limiter and blocked all buys within minutes. Now every fill site debits (buy) /
   credits (sell) `current_cash` so `total_value` stays invariant across a fill. Confirmed in
   production: 2026-07-20 paper run did 32 trades all day with no lockout, +₹76 realized, 69% win.
2. **Churn (buy/sell same stock every cycle) — FIXED via consensus voting.** Replaced first-mover
   execution with a **majority vote** across all strategies: BUY only if `#BUY > #SELL`, SELL only
   if `#SELL > #BUY`; ties / all-HOLD do nothing. The RSI-BUY-vs-MA-SELL round-trip now nets to
   HOLD. Trade log/notify carry a `Consensus(n/N BUY: names)` label — **that label is the user's
   visual confirmation the new logic is live.**
3. **Config too tight for ₹100k.** `config.yaml` → `dhan_live_trading`: `max_positions` 2→6,
   `max_allocation_pct` 0.35→0.16 (6 × 16% ≈ 96% deployed). Now holds one position per ticker
   instead of capping at 2. (initial_capital stays 1027 for real-money live mode; `--capital`
   overrides it for paper.)

**Session logging added:**
- `cli.py`: new `_Tee` class + `_start_logging()` + `--log-file PATH` option on `dhan-live`.
  Tees stdout+stderr to a file (flush every write, survives Ctrl+C, cross-platform). Verified working.
- `start_paper_bot.bat`: makes `logs/`, builds a timestamped name via PowerShell
  `Get-Date -Format yyyy-MM-dd_HH-mm`, passes `--log-file "logs\paper_<stamp>.log"`.
- `logs/` added to `.gitignore`.
- Result: each session auto-saves the full console to `logs/paper_<date>.log`. User can send that
  file instead of copy-pasting. **Console-only was the old state — nothing was written to disk except
  `state/dhan_paper_state.json` (the trade ledger that `paper-status` reads).**

**Telegram notifications — DONE (BotFather was rate-limited, used user-account path):**
- Used the Telethon **user-account** backend (no bot): alerts post to the user's own Telegram
  "Saved Messages". Creds `TG_API_ID` / `TG_API_HASH` from my.telegram.org/apps live in `.env`
  (config.yaml already maps them). Trader auto-selects `TelegramUserNotifier` when they're set.
- Added `telegram-setup` CLI command: does the one-time interactive phone+code login up front
  (creates the `.session` file) and sends a test message — instead of that login first triggering
  mid-run at end-of-day. **User ran it successfully on 2026-07-20 — logged in, test message delivered.**
- Fixed `engine/notifier.py` `TelegramUserNotifier`: was closing the event loop without
  disconnecting the client → loud 'Event loop is closed' teardown tracebacks (esp. Python 3.14) AND
  only the FIRST send of a run worked (cached client bound to a closed loop). Now connects a fresh
  client per send, disconnects in a `finally`, uses `is_user_authorized()` (interactive login only
  when no session). Runtime sends (buy/sell alert on every trade + EOD summary) are now clean/repeatable.
- **Setup files on user's laptop:** `.env` is a hidden dotfile literally named `.env` (they couldn't
  find it in Explorer — told them `notepad .env` from the tradingBot folder). Laptop is `C:\...`,
  user is "Acer" account, Python 3.14. Session file must live on the laptop (where the bot runs), not
  in this ephemeral cloud container — Telegram login can't be done from here.

**State of play — COLLECTING DATA (paused here, 2026-07-20):**
- All 5 improvements committed+pushed to `claude/continued-session-4tt3yo`: (1) daily-loss cash-sync,
  (2) consensus voting, (3) wider limits, (4) session logging, (5) Telegram alerts.
- Earlier 2026-07-20 run (32 trades, +Rs76, 69% win, KOTAKBANK flip) was PRE-consensus — proved the
  cash-fix works in production (no lockout across 32 trades); the churn won't recur under consensus.
- User is leaving the bot to **collect ~2 weeks of clean paper data** before a go/no-go call.
- **LAST ACTION USER STILL OWES:** one more `git pull` on the laptop before the next session, to pick
  up the notifier fix (commit b4ac2f9). Everything else is already on their machine.
- **NEXT TIME:** ask for `logs/paper_<date>.log` (or the Telegram summaries / `paper-status`). Verify
  in the wild: (a) no daily-loss lockout, (b) `Consensus(...)` labels on trades, (c) no same-stock
  flips, (d) Telegram alerts arriving. Track P&L trend, win rate, avg win/loss, max drawdown, trade
  count/day. After ~2 clean weeks → decide: add real money (start tiny, Rs 5-10k) / keep paper /
  retune (drop weak ticker or strategy).
- Open (offered, NOT requested): crypto backtest with buy-and-hold benchmark column.

**2026-07-21 (Tue) follow-up — slow data fetch broke intraday timing (FIXED behavioral half):**
- Symptom: Tue paper run showed a trade at 16:14 (after 15:30 close) and the 15:10 square-off
  never fired (AXISBANK + ASIANPAINT left open overnight). Clock verified CORRECT
  (`datetime.now(ist)` == laptop == real IST 16:22), so NOT a timezone issue.
- Log (`logs/paper_2026-07-21_10-13.log`) showed only ~4 cycles in 6 hours (heartbeats at 10:13,
  11:25, 11:27, then 16:15) — with 60s polling there should be ~360. **Root cause: `get_historical_bars`
  (yfinance, 6 tickers) takes ~60-90 min/cycle on the laptop (rate-limiting). No cycle fell in the
  15:10-15:30 window → square-off skipped; a cycle that started pre-close finished at 16:15 → traded
  after hours.** Not sleep (user confirmed), not multiple instances (tasklist empty).
- FIX (committed 6b1a94e): extracted `_enforce_intraday_timing(portfolio, tickers)` in
  dhan_live_trader.py; run_once now `sync_portfolio()` → guard → fetch → guard again. Guard squares
  off all positions at/after 15:10 and refuses to trade when market closed, evaluated on the current
  clock BEFORE the slow fetch. Also `timeout=20` on yfinance `history()`. Tested: squares off at
  >=15:10, halts pre-open/post-close, a 16:14 cycle returns {} without calling the fetch.
- **CORRECTED root cause (fetch is NOT slow):** user measured a single intraday fetch at **4.1s / 525
  rows** on the laptop. So the 72-min and 288-min gaps are NOT fetch latency (normal cycles were ~2 min
  apart, e.g. 11:25→11:27). The real cause is almost certainly **the OS suspending/throttling the Python
  process while the laptop is idle** (Windows power-saving / modern standby freezes background apps even
  without formal sleep; gaps line up with user-away periods). User said it didn't sleep/hibernate, but
  modern-standby throttling doesn't register as "sleep".
- Added a **stall detector** (commit 79db49d): run loop prints `[WARN] N min gap since last cycle —
  bot was paused (laptop sleep/power-saving?)` when >~5 min elapse between cycles. Next session's log
  will confirm suspension definitively.
- **FINAL root cause = Windows Console QuickEdit Mode** (power mgmt ruled out: user confirmed laptop is
  never-sleep + always plugged in). Clicking/selecting text in a cmd/PowerShell window PAUSES the running
  process until a key is pressed. The freezes lined up with the user copy-pasting console logs to send —
  each selection froze the bot for the whole time. FIX (commit 97661b1): `_disable_windows_quickedit()`
  in cli.py (ctypes SetConsoleMode clears ENABLE_QUICK_EDIT_MODE), called at dhan-live startup — a click
  can no longer pause the bot. Prints "[CONSOLE] QuickEdit disabled..." at startup. Also: user should
  read `logs/paper_<date>.log` instead of selecting console text (no need to touch the window now).
  Timing guard (6b1a94e) still makes any resume safe; stall detector (79db49d) will flag any remaining gap.
- Tue 07-21 data should be discarded from the go/no-go sample.
- User must `git pull` on the laptop to get 6b1a94e + 79db49d before the next session.
- yfinance `timeout=20` (in stocks.py) is a harmless defensive keeper even though fetch wasn't the cause.

**2026-07-22 (Wed) — FIRST FULLY CLEAN SESSION (all infra fixes confirmed in the wild):**
- Startup showed `[CONSOLE] QuickEdit disabled`; ran continuously 09:15→15:30 with cycles every ~1 min
  and **zero `[WARN]` gap lines** → the freeze is fixed. Square-off fired exactly at 15:10
  (AUTO-SQUARE-OFF M&M). Logging + Telegram + consensus labels all working.
- At open it time-exited the 2 positions stuck overnight from Tuesday's corrupted run (AXISBANK -0.98%,
  ASIANPAINT -0.59%) ≈ -Rs227 — Tuesday carryover, not today's strategy.
- Day result: 30 trades, realized -Rs326.95, equity Rs99,817.15 (-0.18% since inception). Ex-carryover,
  today's own trading was ~flat-to--Rs100.
- **Two STRUCTURAL observations (noted, NOT yet fixed — see decision below):**
  1. **Overtrading/churn:** ~30 trades/day, overwhelmingly single-strategy `1/3 BollingerBands` flips
     (M&M ~8 round-trips, KOTAKBANK ~6). Nets ~flat in paper but would bleed on real brokerage+spread.
     The consensus "act unless opposed" rule permits lone-strategy entries. Candidate fix (NOT applied):
     re-entry cooldown (no re-buy within N min of selling same stock) — kills round-trips without the
     strict-2/3 consensus that memory says votes away the mean-reversion winners.
  2. **Volatility filter mis-calibrated:** min_volatility_pct=0.15% blocked ~80% of the day; these
     large-caps run ATR/close ~0.08-0.14% per 5m bar. Candidate fix (NOT applied): lower to ~0.08-0.10%
     or drop it.
- **DECISION (user, 2026-07-22): collect 3-5 more clean days UNCHANGED before any tuning** — keep the
  sample pure. Do NOT modify churn/filter/consensus/config mid-experiment. After the sample, revisit the
  two structural fixes above with more evidence. NEXT: user sends the next few days' logs / paper-status;
  track P&L trend, trade count/day, win rate, whether churn consistently loses.

**2026-07-24 — 4-CLEAN-DAY ANALYSIS + churn fixes applied (analysis-driven):**
- Analyzed full ledger `state/dhan_paper_state.json` (Mon20, Wed22, Thu23, Fri24; Tue21 excluded).
  Per-day realized: Mon +76, Wed -327, Thu -2, Fri +425. **Net +Rs171.75 (+0.17%)**, or ~+Rs400 excluding
  the Rs229 Tuesday-freeze carryover cleanup on Wed open.
- Stats: 57% win rate, **avg win +51 vs avg loss -68 (payoff 0.76)**, profit factor 1.09, **~34 trades/day**.
  KEY FINDING: paper P&L is GROSS; at 34 trades/day real brokerage+STT+slippage (~Rs30-50/round-trip,
  ~Rs2-3.4k over 4 days) would flip it NET-NEGATIVE. Overtrading is the core problem, as backtests warned.
- Per-ticker (valid days): winners BAJFINANCE +335, ASIANPAINT +132, SUNPHARMA +122; losers AXISBANK -348
  (worst; ~149 of it Tue carryover but still the consistent bleeder), M&M -35, KOTAKBANK -34.
- **FIXES APPLIED (user chose to act; commit 5e00ea6):**
  1. **Re-entry cooldown** — new config `reentry_cooldown_minutes` (default 15, in config.yaml + cli wiring).
     After exiting a symbol, block re-buying it for 15m. `_last_exit_time` marked at ALL exit sites
     (consensus SELL, stop-loss, take-profit, trailing, time-exit); gates the consensus BUY path via
     `_in_reentry_cooldown()`. On historical ledger blocks ~20% of entries (~6 fewer trades/day). Tested.
  2. **Dropped AXISBANK** from the paper basket in `start_paper_bot.bat` (now 5 tickers: ASIANPAINT,
     BAJFINANCE, M&M, SUNPHARMA, KOTAKBANK). Removes the worst ticker (~7 more trades/day).
  - Expected effect: ~34 trades/day → ~21/day. Volatility filter left AS-IS (it's a brake; lowering it
    would add churn).
- **NEXT:** user `git pull` on laptop, restart bot (basket auto-drops AXISBANK, cooldown active). Collect
  more days and re-run this same ledger analysis; compare trades/day (should drop) and whether net-of-cost
  turns positive. Go/no-go still pending. If still net-negative after costs, consider: stricter entries,
  fewer/higher-quality tickers, or concluding intraday-on-these-names isn't profitable.

**2026-07-25 — STRATEGY LAB built (user's idea: sandbox first, promote after) + paper now NET of costs:**
- **`lab/run_experiment.py`** (commit b797e72): replays cached 5m bars through the existing
  `IntradayBacktester` across a declarative `VARIANTS` dict, prints a sorted NET-OF-COST comparison vs
  `baseline` (current live settings). Caches bars to `data/lab_cache/`, writes detail to `lab/results/`
  (both gitignored). CLI: `--days --cost-pct --variants --tickers --refresh --list`.
  **The lab never writes config.yaml or start_paper_bot.bat — promotion is manual/deliberate.**
  `lab/README.md` documents the LAB → PAPER → LIVE pipeline + promote checklist + overfitting guardrails.
- **KNOWN LIMITATION (documented):** the lab runs ONE strategy per ticker; the live bot uses a CONSENSUS
  vote across 3. So the lab RANKS options / finds levels, it does NOT predict live P&L. Paper remains the
  final gate.
- **`DhanPaperTrader` now charges costs**: `COST_PCT_PER_SIDE = 0.0005` (~0.1% round trip) on every
  simulated fill, and stores a cost-inclusive basis so reported pnl is a true NET round-trip figure.
  Verified: buy+sell at the SAME price now books -Rs99 on Rs100k notional (previously +0.00 — this is
  exactly how the churn problem stayed hidden). **NOTE: past ledger P&L is gross; new days are net, so
  don't compare old vs new days naively.**
- **First lab run (55d, 5 tickers x 3 strategies = 15 combos, 0.05%/side):**
  trail_0.8 +2.07% | trail_0.5 +1.80% | stop_1.5pct +1.72% | **baseline +1.70%** | hold_60m +1.59% |
  trail_0.3 +1.52% | trail0.5_hold60 +1.48% | trail_atr1.5 +1.33%.
  - Trailing stop helps but **margin is small (+0.37pp)** — treat as possible noise, not a discovery.
  - **Tight trail (0.3%) HURTS** → confirms user's instinct that tightening stops causes whipsaw on these
    small moves. Also `stop_1.5pct` ≈ baseline, confirming the hard stop is essentially inert.
  - **BIG INSIGHT:** backtest combos trade ~57-65 times over 55 days (**~1 trade/day**) and are NET
    POSITIVE. The live bot does **~34 trades/day**. The single-strategy backtest is profitable *because*
    it trades rarely — strongly implies the live consensus "act unless opposed" rule (which fires on lone
    1/3 signals) is what destroys the edge via costs. Reducing trade frequency likely matters far more
    than any trailing-stop tuning.
- **BUG FOUND BY THE LAB (commit after b797e72): `PaperTrader._handle_sell` charged NO commission** —
  it passed the raw price to `portfolio.sell()`, so every simulated EXIT was free and the simulator only
  ever paid the entry half of the round trip. **This affected EVERY backtest in the repo** (runner.py,
  intraday_runner.py, walk_forward.py all inherit it) and systematically flattered high-turnover
  strategies. Caught because a `--cost-pct 0.001` stress run returned results *identical* to 0.0005
  (impossible if costs were fully charged). Fixed: exits fill at `price * (1 - commission_pct)`.
  Unit-tested (10 shares @100 with 1% commission -> 990.00). **All pre-fix backtest numbers in this
  memory file — including the older 20-year and 57-day studies — are OPTIMISTIC and should be re-run.**
- **CORRECTED lab run (55d, 15 combos, 0.05%/side, full round trip):**
  trail_0.8 **+0.71%** | stop_1.5pct +0.45% | **baseline +0.44%** | trail_0.5 +0.16% | trail_0.3 -0.08% |
  hold_60m -0.10% | trail0.5_hold60 -0.11% | trail_atr1.5 -0.22%.
  Sharpe: best 0.50, baseline 0.37. Only 53-60% of combos profitable.
  - Charging the full round trip cut returns **~60-75%** vs the buggy half-cost run (baseline
    +1.70% -> +0.44%). **Costs are the dominant term, not strategy tuning.**
  - baseline +0.44% over 55 trading days ≈ **~2%/yr annualized**; best variant ≈ ~3.3%/yr — both BELOW
    an Indian FD (~7%) while carrying full equity risk. Half the variants are outright negative.
  - trail_0.8 still ranks first (+0.27pp over baseline) but that is small vs the noise in a 55-day,
    15-combo sample — NOT a discovery.
- **CONVERGING EVIDENCE (two independent sources now agree):** live paper (4 clean days, ~breakeven
  GROSS at ~34 trades/day => net-negative) and the corrected lab (~2%/yr net at ~1 trade/day) both say
  **intraday scalping these liquid large-caps does not clear costs.** The earlier "backtest combos are
  profitable" read was an artifact of the half-cost bug.
- **RECOMMENDED PIVOT (not yet decided by user):** stop tuning intraday params; test the SWING/daily-bar
  approach in the lab instead (far fewer trades => costs become negligible; earlier studies favored daily
  bars for these names). Alternative: higher-volatility instruments so moves clear costs. Do NOT promote
  trail_0.8 on this evidence alone.

**2026-07-25 — SWING LAB (`lab/run_swing.py`) — THE DECISIVE RESULT:**
- Daily bars, 5y, 5 tickers x 5 strategies = 25 combos, 0.1%/side charged BOTH sides, and — for the
  first time — **benchmarked against BUY & HOLD** on the same ticker/window as annualized excess CAGR.
- **Buy & hold CAGR (5y): M&M +34.2%, SUNPHARMA +23.5%, BAJFINANCE +10.4%, KOTAKBANK +2.6%,
  ASIANPAINT -2.8%. Basket average +13.6%/yr.**
- **Strategy CAGR vs B&H (avg across tickers):** rsi_mean_revert +4.8% (-8.8pp), macd +4.7% (-8.9pp),
  bollinger_bands +2.3% (-11.3pp), momentum_breakout +0.9% (-12.7pp), ma_crossover -0.3% (-13.9pp).
- **VERDICT: only 4/25 combos (16%) beat buy & hold; average excess -11.12%/yr.** ~10 trades/yr, so
  costs are NOT the explanation here — the strategies simply exit winning trends (they turned M&M's
  +34%/yr and SUNPHARMA's +23.5%/yr into ~4%). Best combo KOTAKBANK/rsi +9.0%/yr vs its B&H +2.6%/yr,
  and that only "wins" because KOTAKBANK itself was a poor holding.
- **BOTH REGIMES NOW FAIL:** intraday ~2%/yr net (below a risk-free FD); swing +4.8%/yr best vs +13.6%
  just holding. The core premise — that these strategies add value on these names — is NOT supported.
- **HONEST CAVEAT:** the 5y window is a strong Indian equity bull market, which structurally favors
  buy & hold; strategies with stops are designed to shine in bear/sideways regimes. A fair follow-up is
  to re-run over a bear/sideways window (e.g. 2015-2016, or 2020 crash) to see whether they protect
  capital. No evidence either way yet. Sample is also only 5 tickers and includes a huge M&M run.
- **STATUS: awaiting user decision.** Options: (a) accept it and just hold/index, (b) test a
  bear/sideways window (the fair test for stop-based strategies), (c) different instruments/markets.
  The ENGINEERING (bot, lab, cost model, guardrails) is sound and reusable regardless.

**2026-07-25 — REGIME TEST (user chose option b). THE CONCLUSIVE FINDING:**
Added `--start/--end/--label` to `run_swing.py` (caches one long history per ticker, slices it).
Also fixed a self-inflicted display bug: trade rate divided by `--years` (fetch span) instead of the
actual sliced window, so a 2y window reported ~2/yr instead of ~9/yr.

Three windows, same 5 tickers x 5 strategies, costs charged both sides:

| Window | Basket B&H | Avg excess vs B&H | Beat rate |
|---|---|---|---|
| 5y to 2026 (BULL) | +13.6%/yr | **-11.12%/yr** | 4/25 (16%) |
| 2015-01..2016-12 (sideways, basket still +11.7%) | +11.7%/yr | **-11.02%/yr** | 7/25 (28%) |
| 2018-01..2020-03 (BEAR, to COVID bottom) | **-3.4%/yr** | **-0.12%/yr** | 11/25 (44%) |

- **The strategies are INSURANCE, not alpha.** They cost ~11pp/yr in rising markets and roughly
  BREAK EVEN in falling ones. Over a full cycle (equities rise long-term) that nets to a loss.
- **STRATEGY RANKING INVERTS BY REGIME** — this is the key mechanic:
  - BULL: rsi_mean_revert best (+4.8%), ma_crossover worst (-0.3%)
  - BEAR: ma_crossover best (+1.8%, **+5.3pp**), macd +4.4pp, momentum +2.6pp;
    **rsi_mean_revert WORST (-12.0%, -8.6pp)**, bollinger -4.3pp
  - i.e. trend-followers win when markets fall; mean-reversion wins when they rise.
- **Capital protection is real when it matters:** every bull/sideways combo that beat B&H was on a
  stock that FELL (SUNPHARMA B&H -12.7%/yr -> rsi +9.3%/yr = +22pp; M&M B&H -2.2% -> rsi +14.4% =
  +16.5pp). Bear window best: **M&M/macd -3.4%/yr vs B&H -36.7%/yr = +33.3pp saved.**
- **DIRECT IMPLICATION FOR THE LIVE BOT:** it runs RSI_MeanReversion + BollingerBands + MA_Crossover.
  In the bear test RSI and Bollinger were the two WORST (-8.6pp, -4.3pp). So the live config is
  weighted toward the strategies that fail in downturns, AND it runs them intraday where costs
  dominate. Worst of both.
- **BOTTOM LINE:** buy & hold / index beats this system over a full cycle. Profiting from it requires
  RELIABLE REGIME DETECTION (switch to trend-following in downturns, hold otherwise) — which is an
  unsolved problem, not a tuning task. Do not put real money on the current setup.

**2026-07-25 — REGIME DETECTION TESTED (`lab/run_regime.py`). INVESTIGATION CLOSED:**
Tested the most-studied regime rule (Faber timing: long while close > SMA, else cash) over a full
12-year cycle, 5 tickers, cost charged on every switch. Deliberately used the classic rule — if a
bespoke variant beat it, that would signal overfitting, not discovery.

| variant | CAGR | vs B&H | maxDD | ret/DD | sharpe | %inMkt | switches |
|---|---|---|---|---|---|---|---|
| **buy_hold** | **+17.3%** | — | -56.9% | **0.32** | **0.66** | 100% | 0 |
| faber_200 | +7.8% | -9.5pp | -52.3% | 0.16 | 0.40 | 69% | 101 |
| faber_100 | +9.2% | -8.1pp | -48.2% | 0.25 | 0.45 | 65% | 144 |
| faber_50 | +10.0% | -7.3pp | -44.0% | 0.31 | 0.49 | 60% | 204 |

- **Timing bought RISK REDUCTION, not return.** Best rule gave up 7.3pp/yr of CAGR to remove 12.9pp of
  drawdown. **Return/drawdown is a wash (0.31 vs 0.32) and Sharpe is WORSE (0.49 vs 0.66).** No free
  lunch — exactly what the academic literature says.
- **CONCLUSION ACROSS THE WHOLE INVESTIGATION — no edge found anywhere:**
  1. Intraday 5m: ~2%/yr net after costs (below a risk-free FD)
  2. Swing, bull window: -11.1pp/yr vs buy & hold
  3. Swing, sideways window: -11.0pp/yr
  4. Swing, bear window: -0.1pp (insurance works, but only breaks even)
  5. Regime timing, full cycle: -7.3pp return for -12.9pp drawdown => risk-adjusted wash
- **FINAL RECOMMENDATION: do not deploy real money on this system.** Buy & hold (ideally an index
  rather than 5 individual stocks) beat every variant tested on a risk-adjusted basis.
- **CAVEATS (honest):** only 5 individual large-caps (very volatile, ~-57% drawdowns); an index would
  behave differently and is the more standard timing test. Strategy params were library defaults, not
  optimized — but optimizing on this sample would be overfitting, not evidence.
- **WHAT IS STILL VALUABLE:** the engineering (bot, paper trader, lab, honest cost model, regime
  tooling, guardrails) is sound and reusable for any future idea; and the process caught 2 real bugs
  (free exits in the backtester, gross-only paper P&L) that would otherwise have justified deploying
  capital on numbers that were never real.

**2026-07-26 — LONG/SHORT REGIME SWITCHING + SIGNAL PREDICTIVE-POWER TEST. ROOT CAUSE FOUND:**

*(1) User's proposed architecture (master strategy: bull sub-strategy + bear sub-strategy, with
shorting) was implemented in `lab/run_regime.py` as long/short variants (short instead of cash below
the MA). Cost now charged proportional to |position change| so a long->short flip costs 2x an exit.*

| variant | CAGR | vs B&H | maxDD | sharpe |
|---|---|---|---|---|
| buy_hold | +17.3% | — | -56.9% | 0.66 |
| faber_200 (long/cash) | +7.8% | -9.5pp | -52.3% | 0.40 |
| **longshort_200 (long/short)** | **-4.1%** | **-21.5pp** | **-79.5%** | **-0.04** |
| longshort_100 | -2.4% | -19.7pp | -74.4% | -0.01 |
| longshort_50 | -1.0% | -18.4pp | -68.5% | 0.05 |

**Shorting made everything materially WORSE.** It does not create edge, it multiplies the signal's
edge — and the signal has none. Cash is a free option (wrong in cash = opportunity cost; wrong short
= real loss), and shorting also fights equities' long-term upward drift.

*(2) `lab/run_signals.py` (NEW) tests signals DIRECTLY — do forward returns differ when signal is ON
vs OFF? This separates signal quality from execution/costs/sizing. Overlapping forward windows are
handled by deflating effective N by the horizon (conservative). Bar: |t|>=2 AND consistent on >=80%
of tickers.*

**RESULT — 9 signals x 5 tickers x horizons 5/20/60 days: NOT ONE cleared the bar.** Best |t| was
1.36 (noise). Signals tested: trend_above_sma200/50, sma200_rising, golden_cross_50_200,
low_volatility, near_highs, momentum_12_1, below_sma10_dip, volume_above_avg.

**THE ROOT CAUSE, AND IT EXPLAINS EVERYTHING:** every trend signal showed a **NEGATIVE** spread at
*every* horizon — forward returns were *worse* when the trend said "bull" (e.g. 20d:
golden_cross -1.78%, trend_above_sma200 -1.63%; 60d: trend_above_sma200 -4.88%). Not statistically
significant, so the honest reading is "no predictive power", possibly mild mean reversion — but
certainly NOT the positive trend persistence that trend-following assumes. This is exactly why
(a) Faber timing underperformed (it held you invested during slightly *worse* periods) and
(b) long/short was catastrophic (long in worse periods, short in better ones, leveraged).
The only positive-spread signals were `below_sma10_dip` (buy the dip) and volume — both t<0.5, noise.

**PRACTICAL BLOCKER for the user's swing long/short plan:** Indian equity shorts are INTRADAY ONLY
(MIS, square off same day). Overnight shorts require stock futures — lot sizes typically Rs 5-10 lakh
notional, far beyond Rs 1,00,000 capital. So the swing version is not implementable at this capital
level regardless of performance.

**CAVEATS:** 5 individual large-caps (not an index — trend following historically works better on
indices/futures); 12y, one market; all signals are price-based from free data.

**2026-07-30 — 11 SESSION LOGS ANALYZED (07-20 .. 07-30). POST-FIX LIVE DATA:**
- **User DID pull all fixes.** Logs from 07-27 onward show AXISBANK dropped (5 tickers), the
  `[CONSOLE] QuickEdit disabled` line, and `re-entry cooldown` skips firing (4 events over 07-29/30).
- **Costs ARE being charged now** — verified by reconstructing gross P&L from log fill prices and
  comparing to the reported `realized`: 07-27 gross +307.30 vs net +183.81; 07-29 +388.55 vs +127.51;
  07-30 +464.50 vs +215.17. Implied ~Rs17/round-trip on ~Rs16k positions ≈ 0.1% round trip. Matches
  the COST_PCT_PER_SIDE model. Pre-fix days show no such gap.
- **Post-fix results (3 real trading days, NET of costs): 07-27 +183.81, 07-29 +127.51,
  07-30 +215.17 = +Rs526.49 (+0.53%). Equity 100,000 -> 100,765.89 over the whole test.**
- **07-28 was a DATA-OUTAGE day**, not a trading day: yfinance returned nothing for all 5 tickers all
  session (376 cycles, "Failed to fetch ... after 3 retries" / "No data ... skipping"), so the bot
  correctly did nothing. Good failure behavior; EXCLUDE from stats.
- **THE DECISIVE NUMBER — cost hurdle:** avg cost Rs 211/day => **~Rs 52,800/yr = ~53% of capital per
  year in costs at this trade frequency.** The strategy must gross >53%/yr just to break even. This is
  the quantified, live-data version of why the 55-day intraday lab showed ~2%/yr net.
- **DO NOT OVER-READ THE 3 GOOD DAYS.** Prior 4-day sample had daily net of +76, -327, -2, +425
  (stdev ~Rs309). Three consecutive positive days occurs ~12.5% of the time by chance alone with zero
  edge. This is encouraging mechanics, NOT evidence of profitability.
- **Churn only partly fixed:** trades/day 16 (07-27) -> 26 (07-29) -> 34 (07-30), i.e. creeping back to
  pre-fix levels (30-38). The cooldown fired only 2-4x/day, so it is not biting hard enough to cut
  frequency materially. That directly drives the 53%/yr cost hurdle above.
- **STATUS:** mechanics are now correct and honest (costs charged, no freezes, square-off working,
  graceful data-outage handling). The economics remain the problem, and are now quantified from live
  data rather than backtest. Need many more days before any profitability claim; the strong prior from
  55d intraday + 12y swing + signal tests is still "no edge".

**2026-08-01 — SL/TP AUDIT + POST-EXIT DRIFT ANALYSIS (user asked: should we widen SL/TP?):**
- **Configured: stop_loss_pct 0.05 (5%), take_profit_pct 0.15 (15%), trailing_stop 0.08 (8%).**
- **THEY HAVE NEVER FIRED. Not once.** Across 11 sessions / 131 exits: 110 consensus SELL,
  11 TIME-EXIT, 10 SQUARE-OFF, **0 STOP-LOSS, 0 TAKE-PROFIT, 0 TRAILING-STOP.** They are sized for
  SWING trading (5%/15% over weeks) while intraday moves are ~+/-0.3% (worst ever -1.84%). The 5% stop
  sits ~3x further away than the worst move ever seen; 15% is a multi-month move. They are decorative.
- **=> ANSWER TO USER: do NOT widen them. They should be SHRUNK to reachable levels or removed.**
  From 120 exits with matching 5m bars: remaining intraday upside after exit averaged +1.03%
  (90th pct +2.11%, max +5.13%). A +15% target was reachable **0/120** times; +1% in 39%; +0.5% in 56%.
- **POST-EXIT DRIFT (the real finding): the bot EXITS TOO EARLY.** After exits, price was higher
  59% (+15m), 64% (+30m), 60% (+60m), **68% (EOD)** of the time; avg +0.33/+0.35/+0.34/+0.47%.
- **CONTROL FOR MARKET DRIFT (important — done properly):** compared against every bar on the same
  tickers/days (2898-bar baseline). Our exits: +30m +0.35%, EOD +0.47%, maxup +1.03%.
  Random bar: +0.02%, +0.18%, +0.68%. **Excess = +0.33pp / +0.29pp / +0.35pp — so it is NOT just a
  rising market; the exits are genuinely premature.** Likely cause: mean-reversion (Bollinger/RSI)
  sells into continuing momentum.
- Scale: ~0.29% excess x ~Rs16k position x 120 exits ≈ Rs 5.5k left on the table over 7 days, vs
  +Rs526 actually earned. (Upper bound — cannot capture all of it, and EOD holding adds square-off risk.)
- **PROPOSED FIX (NOT yet tested):** the lever is the premature consensus SELL, not the SL/TP levels.
  Candidate: replace/soften the consensus SELL with a REACHABLE trailing stop (~0.3-0.5%, vs the
  useless 8%) so winners run. NOTE the corrected lab already found trail_0.5/0.8 only marginally
  helpful (+0.27pp) — but those variants ADDED a trail on top of the existing sells rather than
  REPLACING them. Test that distinction in the lab before promoting.
- **REGIME CAVEAT:** this 7-day window had positive baseline drift (+0.18% EOD on random bars), i.e. a
  rising market. In a falling market early exits would be an ADVANTAGE. Like everything else here, the
  finding may be regime-dependent — do not treat it as universal.

**2026-08-01 — HYPOTHESIS TESTED IN LAB: trailing stop REPLACING strategy sells. BEST RESULT SO FAR:**
Added `disable_strategy_sells` to `IntradayBacktester` + 5 `replace_*` variants in run_experiment.py.
55 days, 5 tickers x 3 strategies (15 combos), fresh data, costs both sides.

| variant | avg ret | profitable | trades | sharpe |
|---|---|---|---|---|
| **replace_trail_0.8** | **+1.33%** | **13/15 (87%)** | 54 | **0.86** |
| replace_trail_0.5 | +1.09% | 10/15 | 55 | 0.77 |
| replace_trail_1.2 | +0.87% | 11/15 | 52 | 0.48 |
| trail_0.8 (ADD) | +0.64% | 9/15 | 55 | 0.46 |
| **baseline** | **+0.41%** | 9/15 | 52 | 0.44 |
| replace_hold_eod | +0.41% | 9/15 | 42 | 0.30 |
| replace_trail_0.3 | +0.20% | 6/15 | 59 | 0.17 |

- **REPLACING beats ADDING at matched trail levels** (0.8%: +1.33 vs +0.64; 0.5%: +1.09 vs +0.59) —
  exactly what the post-exit drift analysis predicted. The sells themselves were the problem.
- **Improvement is BROAD, not one outlier:** 13/15 combos profitable vs 9/15; biggest gains on the
  WORST cases (ASIANPAINT/ma_crossover -6.2% -> -2.6%); Sharpe ~doubled (0.44 -> 0.86).
- **`replace_hold_eod` == baseline (+0.41%) with FEWEST trades (42)** — the key control: simply not
  selling does NOT help (gains handed back by the close). The TRAILING STOP is doing the real work,
  not merely cost savings from fewer trades.
- **COST STRESS (0.001/side, double): replace_trail_0.8 is the ONLY variant still positive (+0.24%)**;
  baseline -0.80%, trail_0.8 -0.64%, replace_hold_eod -0.31%. Gap vs baseline WIDENS to +1.04pp under
  stress => the edge is robust to the cost assumption, not an artifact of it.
- **ANNUALIZED REALITY CHECK:** +1.33%/55d ≈ **+6.3%/yr** (vs baseline ~+1.9%/yr); at doubled costs
  ≈ +1.1%/yr (vs baseline ~-3.6%/yr). So this roughly TRIPLES the return and survives stress — but is
  still **below a risk-free FD (~7%) and far below buy & hold (+13-17%/yr)**. It makes a losing system
  much less bad; it does NOT make it a winner.
- **PROMOTE CHECKLIST: steps 1-2 PASSED** (beats baseline; survives cost stress). Step 3 (promotion)
  pending user decision. **NOTE: live promotion is NOT config-only** — it needs a code change in
  `dhan_live_trader.py` to suppress the consensus SELL branch and enable a reachable trailing stop
  (currently 8% = inert; needs ~0.8%). Recommend paper validation before/after.

**2026-08-01 (Sat) — TIMEFRAME COMPARISON. 5m WINS; SEARCH CLOSED.**
Added `--interval` to run_experiment.py (load_bars/cache already keyed by interval). 55 days.

| interval | best result | best variant | trades | win% |
|---|---|---|---|---|
| **5m** | **+1.33%** | **replace_trail_0.8** | 54 | 49% |
| 15m | +0.77% | baseline | 23 | 57% |
| 30m | +0.53% | (all tied) | 13 | 63% |

- Larger candles gave **fewer trades and higher win rate** (54->23->13; 49%->57%->63%) — the hoped-for
  cost-hurdle relief — but **lower net returns**. 5m + replace_trail_0.8 is the best result found.
- **The exit policy is interval-dependent.** A 0.8% trail is LOOSE on 5m (~0.1% bars) but TIGHT on
  15m/30m, so it stopped out on normal noise there. Scaling it up at 15m (swept 1.2/1.5/2.0/2.5/3.0)
  helped (0.46% -> 0.64%) but **never beat 15m baseline (+0.77%)**.
- **Confound found and then RESOLVED:** at 15m/30m every variant returned identical trade counts,
  because max_hold_minutes=120 is only 8 bars (15m) / 4 bars (30m) — the time-exit fired before the
  trailing stop could matter. Re-ran 15m with the time-exit effectively off: **everything got WORSE**
  (baseline_longhold +0.51% vs baseline +0.77%; trail2.5_longhold +0.33%). So the 120-min time-exit is
  genuinely HELPING at 15m, and the trailing stop does not help there at all.
- **CONCLUSION: keep 5m + replace_trail_0.8. Monday 03 Aug config unchanged.**
- **SEARCH DELIBERATELY STOPPED HERE.** ~13 variants x 3 intervals have now been tested on ONE 55-day
  sample; every further comparison inflates the chance of a false positive. Continuing would be
  overfitting, not research. Next evidence must come from LIVE paper data, not more backtest variants.

**2026-08-01 — MULTI-TIMEFRAME FILTER (`lab/run_mtf.py`) — PREDICTION CONFIRMED, FAILS:**
User asked: use 5m entries but only trade when a higher timeframe (15m/60m) agrees on trend.
Prediction that it would FAIL was **pre-registered in the module docstring before running** (basis:
the signal lab found every trend filter had a NEGATIVE forward spread, |t|<1.4). Method: ONE config
per timeframe (close > SMA20 on resampled bars, `.shift(1)` so only CLOSED higher-TF bars are used —
no look-ahead), not a sweep. Exit policy held constant at the live setting (replace_trail_0.8).

| arm | avg ret | profitable | trades | win% | sharpe |
|---|---|---|---|---|---|
| **no_filter** | **+1.33%** | 13/15 | 54 | 49% | **0.86** |
| filter_60m | +0.80% | 11/15 | 29 | 49% | 0.70 |
| filter_15m | +0.06% | 10/15 | 28 | 45% | 0.01 |

- Filters roughly HALVED trades (54 -> ~29) but **cost return** (-0.54pp for 60m, -1.27pp for 15m) and
  did not improve win rate. The higher-TF trend removed good and bad trades indiscriminately —
  consistent with the signal lab's finding that trend has no predictive power on these names.
- Filters were "on" only 53-56% of bars, so this is a real halving of opportunity for no quality gain.
- **CONFIRMS: keep 5m + replace_trail_0.8, no HTF filter. Monday 03 Aug config unchanged.**

**OPEN QUESTION — VOLUME (user asked; NOT yet properly tested):** the signal lab's `volume_above_avg`
showed ~zero predictive power (20d spread -0.00%, t=-0.02) BUT that test ran on DAILY bars vs a 50-day
average. **That is a weak test for intraday purposes** — intraday volume has a strong U-shape (heavy at
open/close, thin midday), so a raw "above average" test mostly detects TIME OF DAY, not information.
A proper test needs time-of-day-adjusted measures: relative volume vs the same time slot on prior days,
volume surge vs trailing median, **VWAP deviation** (highest-ranked candidate), volume-price divergence.
Prior is still low (all price-derived signals have failed so far) but genuinely weaker than for trend
filters, because volume has NOT been tested properly at intraday resolution.

**2026-08-01 — VOLUME TESTED PROPERLY (`lab/run_volume.py`). NO EDGE. QUESTION CLOSED.**
Prediction pre-registered before running (expect no edge; VWAP the likeliest exception). 8 volume
signals x 5 tickers x horizons 5/15/30min on 55 days of 5m bars. All measures time-of-day adjusted or
self-normalising, so the intraday U-shape cannot leak in as fake signal. Forward windows never cross a
session boundary; effective N deflated by horizon.

Signals: relvol_tod_high/dry (vs same time slot on prior days), vol_surge_2x (vs trailing median),
above/below_vwap (session VWAP), up_on_high_vol, up_on_low_vol, down_on_high_vol.

**RESULT: not one cleared |t|>=2 with >=80% consistency. Strongest was up_on_low_vol @5min,
t=-1.12 — noise.** Spreads were tiny (|spread| <= 0.016% at 30min) and mostly NEGATIVE, i.e. high
volume was followed by slightly WORSE returns. `above_vwap` was negative at all three horizons
(-0.005/-0.007/-0.009%) — no momentum above VWAP; `below_vwap` was weakly positive (mild reversion)
but t<=0.62, also noise. VWAP, the one I flagged as most plausible, showed nothing.
**=> Volume does not predict the next candle on these names. Closed.

**2026-08-03 (Mon) — FIRST LIVE RUN OF NEW EXIT POLICY. MY BUG: WRONG TRAIL SHIPPED.**
- Trailing stop DID fire — **11 TRAILING-STOP exits** (vs 0 across 131 exits before), so the missing
  mark_entry/update_trailing_stop/clear_entry plumbing is genuinely fixed. Also 2 TIME-EXIT, 0 WARN
  gaps, 370 cycles, clean session.
- **BUT THE TRAIL WAS ~0.39%, NOT THE INTENDED 0.8%.** Trigger depths ranged -0.31% to -0.59%
  (avg -0.39%), and implied trail-vs-peak worked out to 0.29-0.57% — varying, i.e. the **ATR path**
  (`peak - 2.0*ATR`), not the fixed-pct path.
- **ROOT CAUSE (my error):** `cli.py` builds an explicit `RiskManager` for dhan-live reading the
  GLOBAL `risk:` block (`trailing_stop_atr_mult: 2.0`, `trailing_stop_pct: 0.08`) and passes it as
  `risk_manager=risk`. `DhanLiveTrader.__init__` does `risk_manager or RiskManager(...config...)`, so
  the passed one WINS and my new dhan_live_trading trailing settings were silently ignored. I added the
  config fields and verified them on the config object, but never checked the risk manager the CLI
  actually injects.
- **WHY IT MATTERS:** the lab ranked tight trails WORST (replace_trail_0.3 +0.20% vs replace_trail_0.8
  +1.33%). Monday effectively ran ~the 0.3-0.4% variant — an untested-in-live, lab-worst setting.
  Day result **-Rs374.02** (equity 100,765.89 -> 100,391.88). **This day does NOT test the validated
  policy and should be excluded from the before/after comparison.**
- **FIXES:** (a) cli.py dhan-live RiskManager now reads trailing settings from `dhan_cfg` (falling back
  to risk_cfg); (b) startup now prints the RISK MANAGER's actual values, incl. an explicit
  "ATR mode — pct IGNORED" warning and a "Trailing: DISABLED" warning, so a config/injection mismatch
  can never hide again. Verified startup now shows `Trailing: 0.80% below peak`.
- **Churn note:** only **13 real BUY fills** but **63 BUY skipped by re-entry cooldown**
  (SUNPHARMA 21, BAJFINANCE 21, ASIANPAINT 13). The 15m cooldown is now doing heavy lifting — worth
  watching whether it is over-blocking once the correct trail is in place.
- **USER MUST `git pull` BEFORE THE NEXT SESSION** to get the trail fix, then re-run. Tue 04 Aug is the
  first genuine test of replace_trail_0.8 in live paper.

**2026-08-07 — FULL WEEK OF THE NEW EXIT POLICY. IT IS NOT WORKING. (9 logs analysed)**
- **Trail fix CONFIRMED live from 08-04**: implied trail width exactly 0.800% on every trigger
  (08-03 was the buggy ATR day at 0.29-0.57%). So 08-04..08-07 are 4 VALID days of replace_trail_0.8.

| day | status | net | equity | trades | trail | timeX | sqoff |
|---|---|---|---|---|---|---|---|
| 08-03 | BUGGY ATR | -374.02 | 100,391.88 | 26 | 11 | 2 | 0 |
| 08-04 | valid | -326.84 | 100,065.03 | 16 | 3 | 4 | 1 |
| 08-05 | valid | -109.92 | 99,955.11 | 18 | 1 | 8 | 0 |
| 08-06 | valid | +55.06 | 100,010.18 | 12 | 0 | 5 | 1 |
| 08-07 | valid | -410.28 | 99,599.91 | 24 | 4 | 4 | 4 |

- **4 valid days: -Rs791.98 (-0.79%). 3 of 4 negative.** Week incl. buggy day: -Rs1,166.
- **ACCOUNT IS NOW UNDERWATER SINCE INCEPTION: Rs 99,599.91 vs Rs 100,000 start (-0.40%);
  -1.16% from the 2026-07-31 peak of Rs 100,765.89.**
- **NOT statistically significant**: mean -Rs198/day, prior daily stdev Rs309 => SE over 4 days Rs154,
  **t = -1.28**. Cannot conclude the policy is proven bad on 4 days. But see the mechanism finding,
  which does NOT depend on sample size:
- **MECHANISM IS BROKEN — the trailing stop rarely gets to act.** Exits on valid days:
  **TIME-EXIT 21, TRAILING-STOP 8, SQUARE-OFF 6** out of 35 BUY fills. Every time-exit fired at
  120-121m. Reason: the trail only arms once price exceeds entry (`high > entry` in check_sell), and
  these names rarely run +0.8% intraday (avg move ~0.3%). So winners seldom trigger it, and **losers
  have NO exit at all** — the 5% stop is unreachable and strategy sells are now disabled — they simply
  bleed for 120 minutes then time-exit. Removing strategy sells removed the only thing that was
  actually closing positions; the 120-min time-exit became the default exit.
- Time-exit P&L was a coin flip (11 negative / 10 positive, -1.18% to +0.46%) — i.e. exits are now
  effectively random with respect to price.
- **WHY THE LAB DIDN'T TRANSFER:** lab ran ONE strategy per ticker at 95% allocation; live runs a
  3-way CONSENSUS at 16% across 5 tickers, so entries are rarer and differently timed. The lab's
  +1.33% did not survive that difference.
- **OPTIONS (user decision pending):** (a) keep collecting ~2-3 weeks for significance; (b) revert
  `disable_strategy_sells: false` (one config line) back to the old policy; (c) fix the asymmetry —
  add a REACHABLE stop-loss (~1%) so losers are cut, since currently only winners have an exit path.
  Recommend (c) tested in the lab first, or (b) if the user wants to stop the bleeding now.

**2026-08-07 — USER CHOSE (b): REVERTED to the 07-27..07-30 configuration.**
- `config.yaml` dhan_live_trading: `disable_strategy_sells: false`, `trailing_stop_enabled: false`.
  Everything else unchanged (15m cooldown, 5 tickers, 16%/6 positions, 5m, 120m time-exit, costs on).
  This is EXACTLY the config that ran 07-27/29/30 for +Rs526 over 3 days — chosen deliberately so the
  next sessions are directly comparable to those days rather than forming a third configuration.
- Startup banner no longer prints an Exit policy / Trailing line, which is the visual confirmation
  the revert is live.
- **Honest scoreboard (both net of costs, neither statistically significant):**
  OLD +Rs526 over 3 days (+Rs175/day) vs NEW -Rs792 over 4 days (-Rs198/day).
- **Standing caveat to repeat if the user gets optimistic:** those 3 old-policy days were never proof
  either — 3 consecutive positive days occurs ~12.5% of the time by chance, and the prior daily stdev
  is ~Rs309. The reverted config is the better BET on the evidence, not a validated winner.
- Account since inception: Rs 99,599.91 vs Rs 100,000 start (-0.40%).
- NOT done (deliberately): option (c), a reachable ~1% stop-loss to cut losers. Still the most
  promising unexplored repair if the user later wants to revisit the winners-run idea. Would need
  lab testing first.

**Security note:** user has repeatedly pasted Dhan JWT access tokens into chat. They expire in 24h;
always tell them to regenerate rather than reuse, tokens go only in git-ignored `.env`, never commit
`.env`. Paper mode needs no credentials at all.

---

> Earlier sessions below.

---

## 🤖 Auto-Save Pattern

**When the chat gets too long and productivity is compromised:**
1. Create/update `CHAT_MEMORY.md` with all learnings, fixes, and state
2. Next session: say "read the memory file" to restore context instantly

---

## 🔑 Critical Lessons

### 1. Dhan API Response Wrapping
**All** Dhan API responses are wrapped as:
```json
{
  "status": "success" | "failure",
  "remarks": { "error_code": "DH-906", "error_type": "...", "error_message": "..." },
  "data": { ... }
}
```
**Always** extract `response["data"]` before reading fields. See `engine/dhan_live_trader.py` → `_unwrap()`.

### 2. Dhan Field Names Are camelCase
- `availabelBalance` (note: Dhan's API has a typo — "availabel" not "available")
- `utilizedAmount`, `sodLimit`, `withdrawableBalance`, `dhanClientId`

### 3. Dhan SDK Order Type Constants
- `dhan.MARKET`, `dhan.LIMIT`, `dhan.SLM` (Stop-Loss Market), `dhan.SL` (Stop-Loss Limit)
- `dhan.BUY`, `dhan.SELL`, `dhan.INTRA`, `dhan.CNC`, `dhan.NSE`, `dhan.DAY`
- There is NO `dhan.STOP_LOSS_MARKET` — use `dhan.SLM` instead

### 4. Dhan SL/TP: place_order() signature
```python
dhan.place_order(
    security_id, exchange_segment, transaction_type, quantity,
    order_type, product_type,
    price=0, trigger_price=0, disclosed_quantity=0, validity='DAY'
)
```
- STOP_LOSS_MARKET: `order_type=dhan.SLM, trigger_price=X, price=0`
- LIMIT sell (take-profit): `order_type=dhan.LIMIT, price=X`
- Cancel: `dhan.cancel_order(order_id)`

### 5. Binance Order Type Constants
```python
from binance.enums import (
    SIDE_BUY, SIDE_SELL,
    ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, ORDER_TYPE_STOP_LOSS_LIMIT,
    TIME_IN_FORCE_GTC
)
```
- STOP_LOSS_LIMIT: `type=ORDER_TYPE_STOP_LOSS_LIMIT, price=stop_limit, stopPrice=trigger`
- Cancel: `client.cancel_order(symbol=ticker, orderId=order_id)`

### 6. Token Expiry & load_dotenv
- Dhan access tokens expire every **24 hours**
- `load_dotenv(override=True)` required in cli.py line 33

### 7. Strategy Name Warning
Wrong: `rsi_mean_reversion` → Correct: `rsi_mean_revert`

---

## 🆕 Changes Made This Session (2026-06-01)

### 1. All 21 NSE Security IDs (was only 10)
`engine/dhan_live_trader.py` → `_KNOWN_SECURITY_IDS`:
```
RELIANCE:2885  TCS:11536  HDFCBANK:1333  INFY:1594  ICICIBANK:2179
BHARTIARTL:2718  ITC:1660  HINDUNILVR:1394  SBIN:3045  LT:8028
KOTAKBANK:1922  AXISBANK:5900  BAJFINANCE:317  MARUTI:10999
SUNPHARMA:3351  ASIANPAINT:236  NESTLEIND:17963  WIPRO:3787
TITAN:3506  M&M:2031  HCLTECH:7229
```

### 2. Broker-Level SL/TP Orders — Dhan (`engine/dhan_live_trader.py`)
- **New methods**: `submit_stop_loss()`, `submit_take_profit()`, `cancel_order()`, `cancel_sl_tp_orders()`, `_place_sl_tp_orders()`
- After every BUY fill: places `STOP_LOSS_MARKET` at -5% + `LIMIT` sell at +15%
- Before every SELL/exit: cancels SL/TP orders first
- Risk manager SL/TP monitoring remains as fallback
- Caveat: on restart, existing positions from `sync_portfolio()` won't get SL/TP orders (software monitoring still works)

### 3. Broker-Level SL/TP Orders — Crypto (`engine/crypto_live_trader.py`)
- Same pattern as Dhan: `submit_stop_loss()` (Binance STOP_LOSS_LIMIT), `submit_take_profit()` (LIMIT), cancel on exit
- Uses Binance LOT_SIZE/PRICE_FILTER for proper quantity/price rounding
- Trailing stop wired through (params added to config)

### 4. Crypto Multi-Strategy Refactor (`engine/crypto_live_trader.py`)
- `BinanceLiveTrader.__init__` now accepts `strategies: list[BaseStrategy]` (like Dhan)
- `run_once()` iterates all strategies per ticker, first-signal-wins pattern
- `CryptoLiveTraderConfig` added: `trailing_stop_enabled`, `trailing_stop_pct`, `trailing_stop_atr_mult`
- CLI `crypto-live --all` now creates one trader with all 5 strategies (not 5 separate)

### 5. Scheduler: Continuous Mode (`scheduler.py`)
- `run_dhan_trader()`: `once=True` → `once=False` (continuous loop, polls until Ctrl+C)
- Added missing Dhan config fields: `max_hold_minutes`, `min_profit_threshold_pct`, `min_volatility_pct`, `stop_buying_minutes`, `force_square_off_minutes`
- `run_crypto_live_trader()`: now uses all 5 strategies + trailing stop + 5m interval

### 6. Config Updates (`config.yaml`)
- `risk.take_profit_pct`: 0.10 → 0.15
- `scheduler.mode`: "dhan"

### 7. Position Checker (`scripts/check_positions.py`)
- Quick script to fetch live Dhan account balance, positions, holdings, unrealized P&L
- Run: `python scripts/check_positions.py`

---

## 🆕 Changes Made This Session (2026-07-02)

### 1. SL/TP Reconcile Loop (`engine/dhan_live_trader.py`, `engine/crypto_live_trader.py`)
New `reconcile_sl_tp(portfolio, tickers)` runs every cycle right after `sync_portfolio()`:
- **Dangling sibling fix**: if the broker executed the SL (or TP) between cycles,
  the surviving sibling order is cancelled, tracking is cleared, and the position
  is dropped locally (Dhan sends a Telegram notification too)
- **Restart protection**: positions with no tracked SL/TP first *adopt* matching
  pending SELL orders from the broker (`get_order_list()` / `get_open_orders()`),
  otherwise fresh SL/TP orders are placed around the average entry price
- Dead orders (CANCELLED / REJECTED / EXPIRED) are dropped from tracking; expired
  DAY orders on Dhan get re-placed the next cycle automatically
- Positions closed outside the bot (manual close) get leftover SL/TP cancelled

### 2. Binance OCO Orders (`engine/crypto_live_trader.py`)
- `_place_sl_tp_orders()` now prefers a single **OCO order** (`create_oco_order`):
  TP leg = LIMIT_MAKER, SL leg = STOP_LOSS_LIMIT
- Why: Binance spot **locks the quantity** for the first sell order, so the old
  separate SL-then-TP pattern made the TP fail with insufficient balance; OCO also
  makes the exchange auto-cancel the surviving leg (no dangling sibling)
- Falls back to separate SL + TP orders if OCO fails
- `cancel_sl_tp_orders()` cancels only one OCO leg (cancelling a leg cancels the list)

### 3. Bug Fix: Binance Portfolio Keying (`engine/crypto_live_trader.py`)
`sync_portfolio()` keyed positions by asset (`BTC`) while `run_once()` looked them
up by pair (`BTCUSDT`) — after a restart the bot never saw its own positions
(duplicate buys, SELL signals ignored). Now keyed by pair everywhere.

### 4. Scheduler Dual Mode (`scheduler.py`, `config.yaml`)
- `scheduler.mode: "both"` — crypto trader starts immediately in a background
  daemon thread (24/7); Dhan trader is scheduled daily at `run_time`
- Crypto thread crash is caught and logged, doesn't kill the scheduler

### 5. Intraday (Day-Trading) Backtest (`backtest/intraday_runner.py`, CLI `backtest-intraday`)
- `IntradayBacktester(PaperTrader)` — simulates the live Dhan intraday rules on 5m bars:
  no BUYs after 15:00 IST, forced square-off at 15:10 (never holds overnight),
  ATR volatility filter, time-exit for stale positions
- CLI: `python cli.py backtest-intraday` (defaults: Dhan tickers, all 5 strategies, 5m, ~55 days)
- **The 20-year backtest numbers are daily-bar (swing) results — they do NOT
  validate day trading.** yfinance caps 5m history at ~60 days, so intraday
  backtests are a short-window reality check only
- Also extracted `_build_strategy_list()` helper in cli.py (was duplicated 2x)

### 7. Forward Paper Trading (`engine/dhan_paper_trader.py`, `dhan-live --paper`)
- `DhanPaperTrader(DhanLiveTrader)`: same loop/strategies/risk/square-off but
  simulated fills, zero broker calls, no credentials needed
- Virtual portfolio persists to `state/dhan_paper_state.json` (restart-safe);
  inspect with `python cli.py paper-status`; `--capital` sets virtual cash (default 10k)
- Software SL/TP monitoring performs all exits (broker plumbing disabled)
- Also fixed: run_once price keying (base symbol, was .NS — stale position marks),
  and data/stocks.py now falls back to Yahoo's plain v8 chart API when yfinance's
  curl_cffi TLS impersonation is blocked by a proxy
- Account reality check (Jul 6): Rs 0 cash; holdings 8x ITC @278.40 + 1x HINDUNILVR
  @2086.50 (both in profit). Zero-cost paper testing chosen before adding funds

### 8. Real-Data Findings (57 trading days of 5m NSE data, Apr–Jul 2026)
- Per-strategy day trading: 62/105 combos profitable but avg only ~+1%/quarter.
  Winners = mean-reversion on high-beta non-IT names (AXISBANK+RSI +10.0%,
  ASIANPAINT+BB +11.4%, BAJFINANCE+BB +12.1%). **IT sector loses under every
  strategy intraday** (TCS avg −7.4%). MACD (daily-bar champion) is the worst
  strategy on 5m bars — rankings nearly invert between daily and intraday
- **Parallel day-trade engine (5-vote consensus) is defensive, not profitable,
  on real data**: contains losses (TCS −4% vs −19% standalone worst) but votes
  away the mean-reversion winners (AXISBANK −0.2% vs +10% standalone). An
  8-config sweep (entry_threshold 0.1–0.3 × trend filter on/off) found no
  meaningfully profitable setting (best avg +0.52%/57d) — don't tune-chase it;
  prefer standalone RSI/Bollinger on the winner tickers for intraday

---

## 🏗️ Architecture

### Data Flow
```
CLI (cli.py)
  ↓
DhanLiveTrader / BinanceLiveTrader
  ├── Account:   get_fund_limits() / get_account()
  ├── Orders:    place_order() — MARKET, SLM, LIMIT
  ├── SL/TP:     STOP_LOSS_MARKET + LIMIT placed after BUY, cancelled before SELL
  ├── Prices:    quote_data() / get_symbol_ticker()
  ├── History:   yfinance (Dhan) / get_historical_klines (Binance)
  └── Alerts:    TelegramNotifier (Bot API or Telethon user account)
```

### Trading Loop (Continuous Mode)
```
while True:
    if market closed: sleep(60), continue
    run_once():
        sync_portfolio()          # fetch positions from broker
        reconcile_sl_tp()         # cancel dangling siblings + re-protect after restart
        for ticker in tickers:
            for strat in strategies:
                if BUY signal:
                    submit_buy()              → MKT order
                    _place_sl_tp_orders()     → SLM + LIMIT orders
                    break                     # first strategy wins
                elif SELL signal:
                    cancel_sl_tp_orders()
                    submit_sell()             → MKT order
                    break
            check_sell()  # SL/TP/trailing-stop monitoring (fallback)
        sleep(poll_interval)
```

---

## 🚀 Quick Reference

```bash
# Paper trading (safe, local backtest)
python cli.py paper -t SBIN.NS -t ICICIBANK.NS -t BHARTIARTL.NS -s all

# Dhan live — single test cycle
python cli.py dhan-live --all --live --once

# Dhan live — CONTINUOUS (polls 60s, Mon-Fri 09:15-15:30 IST)
python cli.py dhan-live --all --live

# Crypto live — CONTINUOUS (24/7)
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT --all

# Scheduler — auto-launches Dhan at 09:15 IST daily
python cli.py schedule

# Check live positions & P&L
python scripts/check_positions.py

# Backtest all markets
python cli.py backtest
```

---

## ⚠️ Known Limitations

1. **Dhan sandbox requires static IP** → production + paper engine for testing
2. **Dhan Data API is paid** → using yfinance for historical data
3. **Tokens expire every 24hrs** → must regenerate from developer portal
4. **NSE equity only** — no F&O, no crypto via Dhan
5. **Integer share quantities only** — fractional qty truncated
6. **Binance avg entry price unknown** — restart SL/TP protection for crypto uses the *current* price as reference (Binance doesn't expose avg entry via the account endpoint)

> Fixed in 2026-07-02 session: ~~No SL/TP on bot restart~~, ~~dangling SL/TP on broker hit~~, ~~scheduler single mode only~~ — see reconcile loop + OCO + dual mode above.

---

## 📁 Key Files

| File | Purpose |
|---|---|
| `cli.py` | CLI entry point, all commands |
| `config.yaml` | Strategy params, risk, tickers, schedules |
| `engine/dhan_live_trader.py` | Dhan live trading (NSE) — SL/TP, multi-strategy |
| `engine/crypto_live_trader.py` | Binance live trading — SL/TP, multi-strategy |
| `engine/risk_manager.py` | Risk checks, SL/TP monitoring (fallback) |
| `engine/notifier.py` | Telegram notifications |
| `scheduler.py` | Daily scheduler — continuous mode for dhan/crypto |
| `scripts/check_positions.py` | Quick position/P&L checker |
| `.env` | API keys (Dhan, Binance, Alpaca, Telegram) |
| `data/dhan_securities.csv` | Full NSE security ID mapping |
