# 🚀 Trading Bot — Command Cheat Sheet

All commands assume you're in the project root (`tradingBot/`) with `.env` configured.

---

## 📊 Paper Trading & Backtesting (No Money, No API Keys)

```bash
# Paper trade all tickers + all strategies (stocks + crypto)
python cli.py paper -s all

# Paper trade specific stock tickers
python cli.py paper -t AAPL -t NVDA -t TSLA -s macd

# Paper trade crypto pairs (NOTE: CCXT uses slash format BTC/USDT)
python cli.py paper -c BTC/USDT -c ETH/USDT -c SOL/USDT -s bollinger_bands

# Paper trade with custom capital
python cli.py paper -s all --capital 50000
```

```bash
# Backtest ALL markets (USA 20yr + India 20yr + Canada 20yr + Crypto 4yr)
python cli.py backtest

# Backtest specific market
python cli.py backtest --usa
python cli.py backtest --india
python cli.py backtest --canada
python cli.py backtest --crypto

# Backtest single strategy across all markets
python cli.py backtest --strategy macd

# Backtest with CSV export
python cli.py backtest --csv ./reports
```

```bash
# Benchmark: rank all strategies by Sharpe/Sortino/Return
python cli.py benchmark                  # stocks + crypto
python cli.py benchmark --stocks         # stocks only
python cli.py benchmark --crypto         # crypto only
python cli.py benchmark --top 15         # show top 15
```

```bash
# Walk-forward optimization (prevents overfitting)
python cli.py walk-forward -t AAPL -s bollinger_bands
python cli.py walk-forward -t NVDA -s macd --metric sortino
python cli.py walk-forward -t TSLA -s momentum_breakout --csv reports/wf_tsla.csv
```

```bash
# Grid search: exhaustive parameter search
python cli.py grid-search -t AAPL -s bollinger_bands
python cli.py grid-search -t NVDA -s macd --metric return --top 5
python cli.py grid-search -t TSLA -s momentum_breakout --csv reports/gs_tsla.csv
```

```bash
# Generate candlestick chart with buy/sell markers + equity curve
python cli.py chart -t AAPL -s ma_crossover
python cli.py chart -t NVDA -s bollinger_bands -y 5
python cli.py chart -t TSLA -s momentum_breakout -o charts/tsla_mb.png
```

```bash
# View saved portfolio state
python cli.py portfolio

# Fetch market data
python cli.py data -t AAPL -y 10
```

---

## ⚡ Parallel Day Trading (5 strategies, one chart, one decision)

All 5 strategies run in parallel on every intraday bar of a single symbol.
Votes are weighted by each strategy's recent profitability on that exact
chart; the bot BUYs on weighted consensus, SELLs on consensus loss /
stop-loss / take-profit, and always squares off before the session close.

```bash
# Backtest 60 days of 5m bars on AAPL, save the parallel-strategy chart
python cli.py day-trade -t AAPL

# Different symbol / interval / history depth
python cli.py day-trade -t NVDA -i 15m -d 30
python cli.py day-trade -t RELIANCE.NS -i 5m

# Compare the combined engine vs each strategy standalone + buy & hold
python cli.py day-trade -t AAPL --compare

# What would the bot do RIGHT NOW? (run every few minutes during market hours)
python cli.py day-trade -t AAPL --signal --no-chart

# Backtest a local OHLCV CSV (offline)
python cli.py day-trade -t MYDATA --csv-file data/synthetic/uptrend_5m.csv

# Offline validation on synthetic regimes (also a regression test)
python scripts/validate_day_trade.py
python scripts/validate_day_trade.py --save-csv   # writes data/synthetic/*.csv
```

Tune thresholds, adaptive weighting, square-off, and intraday risk limits in
the `day_trading:` section of `config.yaml`.

---

## 🔴 Live Trading — India NSE (Dhan)

```bash
# Quick test: one cycle, all 5 strategies, intraday 5m bars
python cli.py dhan-live --all --once

# Continuous all-day trading (Mon-Fri 09:15–15:30 IST)
python cli.py dhan-live --all

# Single strategy, specific tickers
python cli.py dhan-live -s macd -t SBIN.NS -t ICICIBANK.NS --once

# Multiple strategies (repeatable -s flag)
python cli.py dhan-live -s macd -s bollinger_bands --once

# REAL money (CAUTION!)
python cli.py dhan-live --all --live --once

# Override intraday interval
python cli.py dhan-live --all --once --intraday --intraday-interval 15m
```

---

## 🪙 Live Trading — Crypto (Binance)

```bash
# All 5 strategies on 5m bars (testnet — FREE)
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT --all --once

# Continuous loop (catches breakouts 24/7)
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT --all

# Single strategy
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT -s macd --once

# Different interval
python cli.py crypto-live -t BTCUSDT -s bollinger_bands -i 1h --once

# REAL money (CAUTION!)
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT --all --live --once
```

---

## 🏦 Live Trading — US Stocks (Alpaca)

```bash
# Paper trading (free)
python cli.py live -t AAPL -t MSFT -t GOOGL -s macd --once

# Continuous loop
python cli.py live -t AAPL -t MSFT -t GOOGL -s bollinger_bands
```

---

## 🌍 Live Trading — Multi-Market (Interactive Brokers)

```bash
# US stocks only
python cli.py ibkr-live --market usa -s bollinger_bands --once

# India NSE only
python cli.py ibkr-live --market india -s macd --once

# Canada TSX only
python cli.py ibkr-live --market canada -s bollinger_bands --once

# ALL markets (US + India + Canada)
python cli.py ibkr-live --market all -s bollinger_bands

# Specific tickers
python cli.py ibkr-live -t NVDA -t AAPL -t MSFT --once
```

---

## ⚙️ Portfolio Management

```bash
# Check rebalancing (dry-run)
python cli.py rebalance

# Force rebalance even within threshold
python cli.py rebalance --force

# Execute rebalance orders via Alpaca
python cli.py rebalance --execute --force
```

---

## 🔄 Daily Scheduler

```bash
# Start scheduler (runs at 09:15 IST Mon-Fri)
python cli.py schedule
```

---

## 🎯 My Go-To Commands

```bash
# 1. Quick backtest to see which strategies work
python cli.py backtest

# 2. Run crypto intraday (testnet — free, 24/7)
python cli.py crypto-live -t BTCUSDT -t ETHUSDT -t SOLUSDT --all --once

# 3. Run NSE intraday (sandbox)
python cli.py dhan-live --all --once

# 4. Generate a chart to visualize trades
python cli.py chart -t RELIANCE.NS -s macd -y 5
```

---

## 🛠️ Setup Checklist

1. **Clone & install:** `pip install -r requirements.txt`
2. **Create `.env`:** Copy API keys (see README.md)
3. **Edit `config.yaml`:** Set tickers, capital, strategy params
4. **Test paper trading:** `python cli.py paper -s all`
5. **Test backtest:** `python cli.py backtest`
6. **Test live (testnet/sandbox):** `python cli.py crypto-live --all --once`
7. **Go live:** Add `--live` flag (CAUTION: real money!)
