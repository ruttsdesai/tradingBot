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
