# Chat Memory — tradingBot

> Updated: 2026-07-02
> Sessions: Dhan SL/TP, crypto multi-strategy, scheduler continuous, security IDs, live trading, SL/TP reconcile + OCO + dual scheduler

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
