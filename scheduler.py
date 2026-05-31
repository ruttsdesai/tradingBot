"""
Daily scheduler -- runs paper or live trading at a configured time every day.

Uses the `schedule` library for cron-like scheduling.

Modes (set in config.yaml -> scheduler -> mode):
  - "paper"  : Runs PaperTrader and saves portfolio_state.json
  - "live"   : Runs AlpacaLiveTrader with real/paper brokerage orders
  - "crypto" : Runs BinanceLiveTrader with real/testnet Binance orders
  - "dhan"   : Runs DhanLiveTrader for India NSE (uses config.yaml dhan_live_trading section)
"""

import datetime
import json
import os
import time

import schedule


def _build_strategy(config: dict):
    """Build a MA Crossover strategy from config."""
    from strategies.ma_crossover import MACrossoverStrategy
    sc = config["strategies"]["ma_crossover"]
    return MACrossoverStrategy(fast_period=sc["fast_period"], slow_period=sc["slow_period"])


def _build_risk_manager(config: dict):
    """Build a RiskManager from config."""
    from engine.risk_manager import RiskManager
    risk_cfg = config["risk"]
    return RiskManager(
        max_positions=config["paper_trading"]["max_positions"],
        max_allocation_pct=config["paper_trading"]["max_allocation_pct"],
        max_daily_loss_pct=risk_cfg["max_daily_loss_pct"],
        stop_loss_pct=risk_cfg["stop_loss_pct"],
        take_profit_pct=risk_cfg["take_profit_pct"],
    )


def run_paper_trader(config: dict) -> None:
    """Run the paper trader on stocks + crypto and save portfolio state."""
    from data.stocks import fetch_stock_data
    from data.crypto import fetch_crypto_data
    from engine.paper_trader import PaperTrader

    now = datetime.datetime.now()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Running daily paper trade...")

    capital = config["paper_trading"]["initial_capital"]
    risk = _build_risk_manager(config)
    strategy = _build_strategy(config)
    sizing_cfg = config.get("position_sizing", {})

    stock_tickers = config["data"]["stocks"]["default_tickers"][:3]
    crypto_tickers = config["data"]["crypto"]["default_tickers"][:2]
    stock_years = config["data"]["stocks"]["lookback_years"]
    crypto_years = config["data"]["crypto"]["lookback_years"]

    all_results = []

    for t in stock_tickers:
        try:
            df = fetch_stock_data(t, years=stock_years)
            trader = PaperTrader(strategy, risk_manager=risk, initial_capital=capital,
                                 use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
                                 position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
                                 atr_period=sizing_cfg.get("atr_period", 14),
                                 atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0))
            result = trader.run(df, ticker=t)
            all_results.append(result)
            print(f"  {t}: {result.portfolio.total_pnl_pct:+.2%} | "
                  f"${result.portfolio.total_pnl:+,.0f} | "
                  f"Win Rate: {result.win_rate:.1%}")
        except Exception as e:
            print(f"  {t}: ! {e}")

    for sym in crypto_tickers:
        try:
            df = fetch_crypto_data(sym, years=crypto_years)
            trader = PaperTrader(strategy, risk_manager=risk, initial_capital=capital,
                                 use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
                                 position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
                                 atr_period=sizing_cfg.get("atr_period", 14),
                                 atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0))
            result = trader.run(df, ticker=sym)
            all_results.append(result)
            print(f"  {sym}: {result.portfolio.total_pnl_pct:+.2%} | "
                  f"${result.portfolio.total_pnl:+,.0f} | "
                  f"Win Rate: {result.win_rate:.1%}")
        except Exception as e:
            print(f"  {sym}: ! {e}")

    # Save portfolio state to disk
    state_file = os.path.join(os.path.dirname(__file__), "state", "portfolio_state.json")
    if all_results:
        best = max(all_results, key=lambda r: r.portfolio.total_pnl_pct)
        state = {
            "last_updated": datetime.datetime.now().isoformat(),
            "cash": best.portfolio.current_cash,
            "total_value": best.portfolio.total_value,
            "total_pnl": best.portfolio.total_pnl,
            "total_pnl_pct": best.portfolio.total_pnl_pct,
            "positions": {
                t: {
                    "quantity": p.quantity,
                    "avg_entry": p.avg_entry_price,
                    "current_price": best.portfolio.current_prices.get(t, p.avg_entry_price),
                }
                for t, p in best.portfolio.positions.items()
            },
        }
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    print(f"  + Daily paper run complete. {len(all_results)} tickers processed.\n")


def run_live_trader(config: dict) -> None:
    """Run the live (Alpaca) trader for stocks."""
    from engine.live_trader import AlpacaLiveTrader, LiveTraderConfig

    now = datetime.datetime.now()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Running daily live trade...")

    live_cfg = config.get("live_trading", {})
    api_cfg = config["api"]
    sizing_cfg = config.get("position_sizing", {})

    alpaca_key = api_cfg.get("alpaca_api_key", "")
    alpaca_secret = api_cfg.get("alpaca_api_secret", "")

    if not alpaca_key or not alpaca_secret or alpaca_key.startswith("${"):
        print("  ! ALPACA_API_KEY / ALPACA_API_SECRET not set. Skipping live run.")
        return

    strategy = _build_strategy(config)
    tickers = live_cfg.get("tickers", ["AAPL", "MSFT", "GOOGL"])

    trader_config = LiveTraderConfig(
        api_key=alpaca_key,
        api_secret=alpaca_secret,
        paper=live_cfg.get("paper", True),
        initial_capital=live_cfg.get("initial_capital", 100_000),
        max_positions=live_cfg.get("max_positions", 5),
        max_allocation_pct=live_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=config["risk"]["max_daily_loss_pct"],
        stop_loss_pct=config["risk"]["stop_loss_pct"],
        take_profit_pct=config["risk"]["take_profit_pct"],
        poll_interval_seconds=live_cfg.get("poll_interval_seconds", 60),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
    )

    trader = AlpacaLiveTrader(trader_config, strategy)
    trader.run(tickers, once=True)
    print("  + Daily live run complete.\n")


def run_dhan_trader(config: dict) -> None:
    """Run the Dhan live trader for India NSE."""
    from engine.dhan_live_trader import DhanLiveTrader, DhanLiveTraderConfig
    from engine.risk_manager import RiskManager

    now = datetime.datetime.now()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Running daily Dhan live trade...")

    dhan_cfg = config.get("dhan_live_trading", {})
    api_cfg = config.get("api", {})
    risk_cfg = config["risk"]
    sizing_cfg = config.get("position_sizing", {})

    client_id = api_cfg.get("dhan_client_id", "")
    access_token = api_cfg.get("dhan_access_token", "")

    if not client_id or not access_token or str(client_id).startswith("${"):
        print("  ! DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set. Skipping Dhan run.")
        return

    # Build strategies list from config
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy

    strat_cfg = config["strategies"]
    scheduler_strats = dhan_cfg.get("scheduler_strategies", ["macd", "bollinger_bands", "ma_crossover", "rsi_mean_revert"])

    strategies_list = []
    for s in scheduler_strats:
        if s == "ma_crossover":
            mc = strat_cfg["ma_crossover"]
            strategies_list.append(MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"]))
        elif s == "rsi_mean_revert":
            rsi = strat_cfg["rsi_mean_revert"]
            strategies_list.append(RSIMeanReversionStrategy(
                rsi_period=rsi["rsi_period"],
                oversold_threshold=rsi["oversold_threshold"],
                overbought_threshold=rsi["overbought_threshold"],
            ))
        elif s == "macd":
            mc = strat_cfg["macd"]
            strategies_list.append(MACDStrategy(
                fast_period=mc["fast_period"], slow_period=mc["slow_period"],
                signal_period=mc["signal_period"],
            ))
        elif s == "bollinger_bands":
            bb = strat_cfg["bollinger_bands"]
            strategies_list.append(BollingerBandsStrategy(period=bb["period"], num_std=bb["num_std"]))
        elif s == "momentum_breakout":
            mb = strat_cfg["momentum_breakout"]
            strategies_list.append(MomentumBreakoutStrategy(lookback=mb["lookback"], exit_sma=mb["exit_sma"]))

    tickers = dhan_cfg.get("tickers", ["ICICIBANK.NS", "SBIN.NS", "BHARTIARTL.NS"])
    sandbox = dhan_cfg.get("sandbox", True)

    risk = RiskManager(
        max_positions=dhan_cfg.get("max_positions", 5),
        max_allocation_pct=dhan_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=risk_cfg["max_daily_loss_pct"],
        stop_loss_pct=risk_cfg["stop_loss_pct"],
        take_profit_pct=risk_cfg["take_profit_pct"],
        trailing_stop_enabled=risk_cfg.get("trailing_stop_enabled", False),
        trailing_stop_pct=risk_cfg.get("trailing_stop_pct", 0.08),
        trailing_stop_atr_mult=risk_cfg.get("trailing_stop_atr_mult", 2.0),
        correlation_enabled=risk_cfg.get("correlation_enabled", False),
        correlation_threshold=risk_cfg.get("correlation_threshold", 0.70),
        max_cluster_allocation_pct=risk_cfg.get("max_cluster_allocation_pct", 0.40),
    )

    sandbox_client_id = api_cfg.get("dhan_sandbox_client_id", "")
    sandbox_access_token = api_cfg.get("dhan_sandbox_access_token", "")

    trader_config = DhanLiveTraderConfig(
        client_id=client_id,
        access_token=access_token,
        sandbox=sandbox,
        sandbox_client_id=sandbox_client_id,
        sandbox_access_token=sandbox_access_token,
        disable_ssl=dhan_cfg.get("disable_ssl", False),
        initial_capital=dhan_cfg.get("initial_capital", 100_000),
        max_positions=dhan_cfg.get("max_positions", 5),
        max_allocation_pct=dhan_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=risk_cfg["max_daily_loss_pct"],
        stop_loss_pct=risk_cfg["stop_loss_pct"],
        take_profit_pct=risk_cfg["take_profit_pct"],
        poll_interval_seconds=dhan_cfg.get("poll_interval_seconds", 60),
        intraday=dhan_cfg.get("intraday", False),
        intraday_interval=dhan_cfg.get("intraday_interval", "5m"),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
        telegram_bot_token=str(api_cfg.get("telegram_bot_token", "")),
        telegram_chat_id=str(api_cfg.get("telegram_chat_id", "")),
        tg_api_id=int(str(api_cfg.get("tg_api_id", "0") or "0")),
        tg_api_hash=str(api_cfg.get("tg_api_hash", "")),
    )

    trader = DhanLiveTrader(trader_config, strategies=strategies_list, risk_manager=risk)
    trader.run(tickers, once=True)
    print("  + Daily Dhan live run complete.\n")


def run_crypto_live_trader(config: dict) -> None:
    """Run the crypto live (Binance) trader."""
    from engine.crypto_live_trader import BinanceLiveTrader, CryptoLiveTraderConfig

    now = datetime.datetime.now()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] Running daily crypto live trade...")

    crypto_live_cfg = config.get("crypto_live_trading", {})
    api_cfg = config["api"]
    sizing_cfg = config.get("position_sizing", {})

    binance_key = api_cfg.get("binance_api_key", "")
    binance_secret = api_cfg.get("binance_secret", "")

    if not binance_key or not binance_secret or binance_key.startswith("${"):
        print("  ! BINANCE_API_KEY / BINANCE_SECRET not set. Skipping crypto live run.")
        return

    strategy = _build_strategy(config)
    tickers = crypto_live_cfg.get("tickers", ["BTCUSDT", "ETHUSDT"])

    trader_config = CryptoLiveTraderConfig(
        api_key=binance_key,
        api_secret=binance_secret,
        testnet=crypto_live_cfg.get("testnet", True),
        initial_capital=crypto_live_cfg.get("initial_capital", 10_000),
        max_positions=crypto_live_cfg.get("max_positions", 5),
        max_allocation_pct=crypto_live_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=config["risk"]["max_daily_loss_pct"],
        stop_loss_pct=config["risk"]["stop_loss_pct"],
        take_profit_pct=config["risk"]["take_profit_pct"],
        poll_interval_seconds=crypto_live_cfg.get("poll_interval_seconds", 60),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
    )

    trader = BinanceLiveTrader(trader_config, strategy)
    trader.run(tickers, once=True)
    print("  + Daily crypto live run complete.\n")


# Map mode name -> handler function
_SCHEDULER_MODES = {
    "paper": run_paper_trader,
    "live": run_live_trader,
    "crypto": run_crypto_live_trader,
    "dhan": run_dhan_trader,
}


def start_scheduler(config: dict) -> None:
    """Start the daily scheduler loop."""
    run_time = config["scheduler"]["run_time"]
    mode = config["scheduler"].get("mode", "paper")

    handler = _SCHEDULER_MODES.get(mode)
    if handler is None:
        print(f"Unknown scheduler mode: '{mode}'. Valid: {list(_SCHEDULER_MODES.keys())}")
        print(f"Falling back to 'paper' mode.")
        handler = run_paper_trader

    schedule.every().day.at(run_time).do(handler, config=config)

    print(f"Scheduler started. Mode: {mode}. Will run daily at {run_time}.")
    print("Waiting for next scheduled run...")

    while True:
        schedule.run_pending()
        time.sleep(60)  # check every minute
