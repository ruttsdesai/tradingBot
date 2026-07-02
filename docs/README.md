# 🤖 Multi-Strategy Trading Bot

A production-grade, multi-market, multi-strategy algorithmic trading bot supporting **stocks** (US, India NSE, Canada TSX) and **crypto** (Binance). Features 5 technical strategies, walk-forward optimization, ATR-based position sizing, correlation risk controls, live trading via Dhan/Alpaca/IBKR/Binance, and a CLI for everything.

---

## 📚 Table of Contents

- [Architecture](#-architecture)
- [Installation](#-installation)
- [Configuration](#-configuration)
  - [API Keys (.env)](#api-keys-env)
  - [Strategy Parameters](#strategy-parameters)
  - [Risk Management](#risk-management)
- [Commands](#-commands)
- [Strategies](#-strategies)
- [Markets & Brokers](#-markets--brokers)
- [Risk Management](#-risk-management-1)
- [Position Sizing](#-position-sizing)
- [Scheduler](#-scheduler)
- [Telegram Notifications](#-telegram-notifications)
- [File Structure](#-file-structure)
- [FAQ](#-faq)

---

## 🏗️ Architecture

```
                     ┌──────────────────────────┐
                     │         CLI (click)        │
                     │  paper | backtest | ...    │
                     └──────────┬───────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
   ┌─────▼─────┐         ┌─────▼─────┐          ┌─────▼─────┐
   │   Data     │         │ Strategies │          │  Engine   │
   │ stocks.py  │────────▶│  5 tech    │─────────▶│ paper     │
   │ crypto.py  │         │ strategies │          │ live      │
   └───────────┘         └───────────┘          │ backtest  │
                                                 │ charts    │
                                                 │ rebalance │
                                                 └───────────┘
```

### Layered Design

| Layer | Files | Responsibility |
|-------|-------|----------------|
| **Data** | `data/stocks.py`, `data/crypto.py` | Fetch OHLCV from yfinance, Binance, CCXT |
| **Strategies** | `strategies/*.py` | Generate BUY/SELL/HOLD signals |
| **Engine** | `engine/*.py` | Execute trades, manage risk, track portfolio |
| **CLI** | `cli.py` | Unified command-line interface |
| **Config** | `config.yaml`, `.env` | YAML config + env var substitution |
| **Scheduler** | `scheduler.py` | Daily cron-style execution at 09:15 IST |

---

## 📦 Installation

```bash
# 1. Clone the repo
git clone <repo-url>
cd tradingBot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create a .env file in the project root with your API keys
#    (see the "API Keys" section below for required variables)

# 4. Verify installation
python cli.py --help
```

---

## ⚙️ Configuration

### API Keys (.env)

Create a `.env` file with your API keys. Only fill in the ones you need:

```bash
# Binance (crypto testnet or live)
BINANCE_API_KEY=your_binance_key
BINANCE_SECRET=your_binance_secret

# Alpaca (US stocks)
ALPACA_API_KEY=PK...
ALPACA_API_SECRET=...

# Dhan (India NSE)
DHAN_CLIENT_ID=your_client_id
DHAN_ACCESS_TOKEN=your_access_token
DHAN_SANDBOX_CLIENT_ID=           # optional — for sandbox mode
DHAN_SANDBOX_ACCESS_TOKEN=        # optional — for sandbox mode

# Telegram (optional trade alerts)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TG_API_ID=
TG_API_HASH=
```

### Strategy Parameters

All defined in `config.yaml` under `strategies:`. Each strategy has its own parameter set:

| Strategy | Key Parameters | Default |
|----------|---------------|---------|
| **MA Crossover** | `fast_period`, `slow_period` | 20, 50 |
| **RSI Mean Rev** | `rsi_period`, `oversold_threshold`, `overbought_threshold` | 14, 30, 70 |
| **MACD** | `fast_period`, `slow_period`, `signal_period` | 12, 26, 12 |
| **Bollinger Bands** | `period`, `num_std` | 10, 1.5 |
| **Momentum Breakout** | `lookback`, `exit_sma` | 20, 10 |

### Risk Management

```yaml
risk:
  max_daily_loss_pct: 0.03       # stop trading if daily loss > 3%
  stop_loss_pct: 0.05            # hard stop-loss at 5% per position
  take_profit_pct: 0.10          # take profit at 10% per position
  trailing_stop_enabled: true    # lock in gains by trailing 8% below peak
  trailing_stop_pct: 0.08
  correlation_enabled: false     # limit exposure to highly-correlated assets
```

---

## 🚀 Commands

See **[COMMANDS.md](COMMANDS.md)** for a full grouped cheat sheet. Quick reference:

| Command | Purpose | Mode |
|---------|---------|------|
| `paper` | Paper trading simulation | Offline |
| `backtest` | Historical backtesting (USA/India/Canada/Crypto) | Offline |
| `benchmark` | Rank all strategies by metrics | Offline |
| `walk-forward` | Walk-forward optimization | Offline |
| `grid-search` | Exhaustive parameter search | Offline |
| `chart` | Candlestick chart with trade markers | Offline |
| `day-trade` | 5 strategies in parallel on one intraday chart → one buy/sell decision | Offline/Signal |
| `crypto-live` | Binance testnet/live trading | Live |
| `dhan-live` | Dhan NSE sandbox/live trading | Live |
| `live` | Alpaca US stock trading | Live |
| `ibkr-live` | Interactive Brokers multi-market | Live |
| `schedule` | Daily scheduler (09:15 IST) | Auto |
| `rebalance` | Portfolio rebalancing | Offline/Live |

---

## 📈 Strategies

### 1. MA Crossover (`ma_crossover`)
**Logic:** BUY when fast MA crosses above slow MA (golden cross). SELL when fast MA crosses below slow MA (death cross).
**Best for:** Trending markets, long-term holds.
**Default params:** 20/50 period.

### 2. RSI Mean Reversion (`rsi_mean_revert`)
**Logic:** BUY when RSI < oversold threshold (oversold bounce). SELL when RSI > overbought threshold (overbought reversal).
**Best for:** Sideways/range-bound markets.
**Default params:** 14 period, 30/70 thresholds.

### 3. MACD (`macd`)
**Logic:** BUY when MACD line crosses above signal line. SELL when MACD crosses below signal.
**Best for:** Momentum-driven moves, all market conditions.
**Default params:** 12/26/12 (fast/slow/signal).

### 4. Bollinger Bands (`bollinger_bands`)
**Logic:** BUY when price touches lower band (bounce). SELL when price touches upper band (reversal).
**Best for:** Mean-reverting markets with clear volatility.
**Default params:** 10 period, 1.5 std dev.

### 5. Momentum Breakout (`momentum_breakout`)
**Logic:** BUY when price breaks above N-day high. SELL when price crosses below exit SMA.
**Best for:** Strong trending markets, catching breakouts early.
**Default params:** 20 lookback, 10 exit SMA.

### Ensemble Mode
Combines votes from multiple strategies. BUY only when `min_buy_votes` strategies agree (default: 2 out of 5). Reduces false signals at the cost of fewer trades.

### Parallel Day-Trade Mode (`day-trade`)
All 5 strategies run **in parallel on every intraday bar of a single chart** (5m/15m/etc.). Each strategy keeps a virtual trade ledger on that exact chart; its vote is weighted by its recent virtual profitability (winners get up to 3× voice, losers are muted to 0.25×, never silenced). The bot BUYs on weighted consensus (`entry_threshold`), SELLs on consensus loss / 1% stop / 2.5% take-profit / 0.8% trailing stop, only enters above a 200-bar trend EMA, blocks entries in the last 30 minutes, and **always squares off before the session close** (zero overnight risk). Validate offline with `python scripts/validate_day_trade.py`.

> ⚠️ No strategy is guaranteed profitable. Backtest results (and the synthetic-regime validation) are not a promise of future returns — always paper trade before risking real money.

---

## 🌍 Markets & Brokers

| Market | Broker | Data Source | Live Trading | Intraday |
|--------|--------|-------------|--------------|----------|
| **US Stocks** | Alpaca | yfinance | ✅ Paper/Live | ❌ |
| **US Stocks** | IBKR | yfinance | ✅ Paper/Live | ❌ |
| **India NSE** | Dhan | yfinance | ✅ Sandbox/Live | ✅ 5m bars |
| **India NSE** | IBKR | yfinance | ✅ Paper/Live | ❌ |
| **Canada TSX** | IBKR | yfinance | ✅ Paper/Live | ❌ |
| **Crypto** | Binance | python-binance | ✅ Testnet/Live | ✅ 1m–1d |

### Dhan (India NSE) — Intraday Mode

When `intraday: true` (default in config), the bot:
- Uses **5-minute candles** instead of daily
- Applies **volatility filters** (skips tickers with ATR/close < 0.15%)
- **Stops buying** at 15:00 IST (30 min before close)
- **Force square-off** all MIS positions at 15:10 IST
- **Time-exits** positions held > 120 min with minimal profit (< 0.5%)

The Dhan trader supports **all 5 strategies simultaneously** via `--all` flag. Each strategy evaluates independently.

### Binance — 24/7 Crypto

Crypto markets never close. The bot polls every 60 seconds and catches breakouts any time of day or night. Testnet mode is **free** — no real funds used.

---

## 🛡️ Risk Management

All live traders use the same `RiskManager`:

| Control | Description |
|---------|-------------|
| **Max Positions** | Limit concurrent open positions (default: 5) |
| **Max Allocation** | Max % of portfolio per position (default: 20%) |
| **Daily Loss Limit** | Stop all trading if daily loss > 3% |
| **Stop Loss** | Hard exit at 5% loss per position |
| **Take Profit** | Auto-exit at 10% profit |
| **Trailing Stop** | Lock in gains — trail 8% below peak price |
| **Correlation Filter** | Limit exposure to correlated assets (e.g., all tech) |

---

## 📏 Position Sizing

Two modes in `config.yaml` → `position_sizing`:

### Fixed % Allocation (default: `use_atr_sizing: false`)
Each trade uses a fixed % of portfolio (e.g., 20% = $20K of $100K).

### ATR-Based Sizing (`use_atr_sizing: true`)
Position size = `(portfolio × risk_pct) / (ATR × multiplier)`. Larger positions in low-volatility assets, smaller in high-volatility. More risk-balanced.

---

## ⏰ Scheduler

The scheduler runs daily at **09:15 IST** (NSE market open). It:
1. Evaluates all configured strategies on all configured tickers
2. Places orders via the configured broker (Dhan sandbox by default; set `sandbox: false` in config for real orders)
3. Sends Telegram alerts for fills and exits

```bash
python cli.py schedule
```

Set `scheduler.mode` in config.yaml: `paper` | `live` | `crypto` | `dhan`.

---

## 📱 Telegram Notifications

Optional. When configured, the Dhan live trader sends real-time alerts:

- **Entry alerts:** ticker, strategy, price, quantity, SL/TP levels
- **Exit alerts:** ticker, exit reason (SL/TP/Trailing/Time/Square-off), P&L
- **Daily summary:** total P&L, win rate, number of trades

Set in `.env`:
```
TELEGRAM_BOT_TOKEN=123:abc
TELEGRAM_CHAT_ID=123456
```

---

## 📁 File Structure

```
tradingBot/
├── cli.py                  # CLI entry point (14 commands)
├── scheduler.py            # Daily cron-style scheduler
├── config.yaml             # All configuration (strategies, risk, brokers)
├── .env                    # API keys (git-ignored)
├── requirements.txt        # Python dependencies
├── COMMANDS.md             # Command cheat sheet
├── README.md               # This file
│
├── data/
│   ├── stocks.py           # Stock data (yfinance)
│   └── crypto.py           # Crypto data (CCXT/Binance)
│
├── strategies/
│   ├── base.py             # Base strategy + Signal enum
│   ├── ma_crossover.py     # MA Crossover
│   ├── rsi_mean_revert.py  # RSI Mean Reversion
│   ├── macd.py             # MACD
│   ├── bollinger_bands.py  # Bollinger Bands
│   ├── momentum_breakout.py # Momentum Breakout
│   └── ensemble.py         # Ensemble voting
│
├── engine/
│   ├── portfolio.py        # Portfolio tracking (positions, P&L)
│   ├── risk_manager.py     # Risk controls (SL, TP, trailing, correlation)
│   ├── paper_trader.py     # Paper trading simulation
│   ├── live_trader.py      # Alpaca live trader
│   ├── ibkr_live_trader.py # Interactive Brokers live trader
│   ├── dhan_live_trader.py # Dhan (India NSE) live trader
│   ├── crypto_live_trader.py # Binance live trader
│   ├── charts.py           # Chart generation (candlestick + equity)
│   ├── rebalancer.py       # Portfolio rebalancing
│   └── walk_forward.py     # Walk-forward optimization
│
├── backtest/
│   └── runner.py           # Backtest engine
│
├── reports/                # CSV exports from backtests
└── charts/                 # Saved chart PNGs
```

---

## ❓ FAQ

### Q: I just want to test strategies without real money. What do I run?
```bash
python cli.py paper -s all          # paper trade all stocks + crypto
python cli.py backtest              # backtest all markets
python cli.py benchmark             # rank strategies by performance
```

### Q: How do I run intraday trading on Indian stocks?
```bash
python cli.py dhan-live --all --once    # test one cycle
python cli.py dhan-live --all           # continuous loop (Mon-Fri)
```

### Q: How do I run crypto trading (24/7)?
```bash
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT --all --once
```

### Q: What's the difference between `--all` and `-s macd`?
- `-s macd` runs only the MACD strategy
- `--all` runs all 5 strategies (MA Crossover, RSI, MACD, Bollinger, Momentum)

### Q: How do I optimize strategy parameters?
```bash
python cli.py walk-forward -t SBIN.NS -s macd     # walk-forward
python cli.py grid-search -t AAPL -s bollinger_bands   # grid search
```

### Q: Does this use real money by default?
**No.** All live commands default to **testnet/sandbox/paper** mode. Add `--live` to use real money on Dhan, Binance, or Alpaca.

### Q: Can I run multiple strategies on the same ticker?
Yes — use `--all` or repeat `-s`:
```bash
python cli.py dhan-live -s macd -s bollinger_bands --once
```

### Q: What if I don't have API keys?
Paper trading, backtesting, benchmarking, charting, and grid search all work offline. Only live trading commands need API keys.

### Q: How do I add a new ticker?
Edit `config.yaml` → `data.stocks.default_tickers` (for backtests) or the broker-specific section (e.g., `dhan_live_trading.tickers`).

---

## 📝 License

MIT — Use at your own risk. Algorithmic trading involves financial risk. Always test thoroughly on paper/sandbox before using real money.
