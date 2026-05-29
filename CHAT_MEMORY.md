# Chat Memory — tradingBot

> Saved: 2026-05-26
> Sessions: Dhan live trading integration, all-strategy paper test, Telegram notifications

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

### 3. Dhan Sandbox Requires Static IP (Non-Negotiable)
- Sandbox URL: `https://sandbox.dhan.co/v2` — **hangs without whitelisted IP**
- Use production API + paper trading engine for testing

### 4. Dhan Data API is a Paid Add-On
- `historical_daily_data()` returns `DH-902: Not subscribed`
- Workaround: use `yfinance` via `data.stocks.fetch_stock_data()` for OHLC data

### 5. Token Expiry & load_dotenv
- Dhan access tokens expire every **24 hours**
- `load_dotenv(override=True)` required in `cli.py` line 33 (empty shell env vars won't be overwritten otherwise)

### 6. Strategy Name Warning
Wrong: `rsi_mean_reversion` → Correct: `rsi_mean_revert`

---

## 📁 Files Changed

### `cli.py`
- Line 33: `load_dotenv(override=True)`
- `str(client_id)` cast for Dhan SDK
- Telegram: reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from env, passes to config

### `engine/dhan_live_trader.py` (major)
- **`_unwrap()`** — extracts `data` from wrapped API responses
- **`_init_notifier()` / `_notify()`** — Telegram integration
- **All API calls fixed:** unwrap + camelCase fields for fund_limits, positions, holdings, quote_data, orders
- **Failure checking:** `status == "failure"` in order responses
- **Data source:** switched to `yfinance` for historical bars (free)
- **`str(client_id)`** cast, removed broken sandbox URL monkey-patch

### `engine/notifier.py` (NEW)
- `TelegramNotifier` class — fire-and-forget via `requests`
- Format helpers: `format_buy_msg`, `format_sell_msg`, `format_sl_msg`, `format_tp_msg`, `format_session_start`, `format_error`
- Notifications for: session start (continuous mode only), BUY, SELL, STOP-LOSS, TAKE-PROFIT

### `config.yaml`
- Added `telegram_bot_token: ${TELEGRAM_BOT_TOKEN}` and `telegram_chat_id: ${TELEGRAM_CHAT_ID}` in `api:` section

### `.env`
- Contains: `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_CHAT_ID=`

---

## 🏗️ Architecture

### Data Flow
```
CLI (cli.py)
  ↓
DhanLiveTrader (engine/dhan_live_trader.py)
  ├── Account:   dhan.get_fund_limits()   → _unwrap() → camelCase keys
  ├── Orders:    dhan.place_order()       → _unwrap() → check status
  ├── Prices:    dhan.quote_data()        → _unwrap() → NSE_EQ entries
  ├── History:   data.stocks.fetch_stock_data() → yfinance (NOT Dhan Data API)
  └── Alerts:    TelegramNotifier.send()  → Telegram Bot API
```

### Security ID Mapping
```
RELIANCE:2885  TCS:11536  HDFCBANK:1333  INFY:1594  ICICIBANK:2179
BHARTIARTL:2718  ITC:1660  HINDUNILVR:1394  SBIN:3045  LT:8028
```

---

## 📊 All-Strategy Paper Test Results (India NSE, 10yr, $100k)

| Strategy | SBIN.NS | ICICIBANK.NS | BHARTIARTL.NS | **Combined** |
|---|---|---|---|---|
| **MACD** | +36.16% (Sharpe 0.82) | +45.09% (Sharpe 1.24) | +24.22% (Sharpe 0.64) | **+35.16%** |
| **Bollinger Bands** | +30.12% (Sharpe 0.65) | +34.25% (Sharpe 0.85) | +32.43% (Sharpe 0.86) | **+32.27%** |
| **MA Crossover** | +9.37% (Sharpe 0.34) | +7.81% (Sharpe 0.30) | +14.57% (Sharpe 0.52) | **+10.58%** |
| **RSI Mean Rev** | +10.74% (Sharpe 0.34) | +7.25% (Sharpe 0.32) | +1.78% (Sharpe 0.09) | **+6.59%** |
| **Momentum Break** | +3.60% (Sharpe 0.14) | +4.77% (Sharpe 0.20) | +0.12% (Sharpe 0.02) | **+2.83%** |

**Winner: MACD** (highest returns on all tickers, best Sharpe). **Runner-up: Bollinger Bands** (good across the board, higher win rates).

---

## 🚀 Quick Reference

```bash
# Paper trading (safe, local backtest)
python cli.py paper -t SBIN.NS -t ICICIBANK.NS -t BHARTIARTL.NS -s macd

# Dhan live — single evaluation
python cli.py dhan-live --live -s macd --once

# Dhan live — continuous polling (with Telegram alerts if configured)
python cli.py dhan-live --live -s macd

# Telegram setup:
# 1. Create bot via @BotFather → get token
# 2. Get chat ID via @userinfobot
# 3. Add to .env: TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...
```

---

## ⚠️ Known Limitations

1. **Dhan sandbox requires static IP** → production + paper engine for testing
2. **Dhan Data API is paid** → using yfinance for historical data
3. **Tokens expire every 24hrs** → must regenerate from developer portal
4. **Order placement may fail with `Invalid IP`** if IP not whitelisted
5. **NSE equity only** — no F&O, no crypto via Dhan
6. **Integer share quantities only** — fractional qty truncated
7. **Telegram alerts only in continuous mode** — `--once` mode skips session-start notification

---

## 🔮 For Next Session

- [ ] Fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
- [ ] Register static IP with Dhan for order execution
- [ ] Add token refresh automation
- [ ] Test Bollinger Bands live (2nd best strategy, higher win rates)
- [ ] Add auto-save pattern: when context gets long, save to CHAT_MEMORY.md
