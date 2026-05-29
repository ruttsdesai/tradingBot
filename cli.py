"""
CLI for the Trading Bot -- paper trade, backtest, and view portfolio.

Usage:
    python cli.py paper          # Run paper trader on default tickers
    python cli.py backtest       # Backtest all strategies on historical data
    python cli.py walk-forward   # Walk-forward optimization (prevents overfitting)
    python cli.py portfolio      # View current portfolio state
    python cli.py data           # Fetch and display market data
    python cli.py schedule       # Start the daily scheduler
    python cli.py rebalance      # Check & execute portfolio rebalancing
    python cli.py crypto-live    # Run live crypto trading on Binance
    python cli.py live           # Run live stock trading on Alpaca
    python cli.py ibkr-live      # Run live stock trading on Interactive Brokers
    python cli.py dhan-live      # Run live stock trading on Dhan (India NSE)
"""

import os
import sys
from datetime import datetime

import click

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_config():
    """Load YAML config with environment variable substitution."""
    import yaml
    from dotenv import load_dotenv

    load_dotenv(override=True)

    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        raw = f.read()

    # Substitute ${ENV_VAR} with os.environ values
    import re
    def sub_env(match):
        var = match.group(1)
        return os.environ.get(var, "")

    raw = re.sub(r"\$\{([A-Z_]+)\}", sub_env, raw)
    return yaml.safe_load(raw)


CONFIG = load_config()


@click.group()
def cli():
    """Trading Bot -- AI-powered paper trading and backtesting."""
    pass


@cli.command()
@click.option("--ticker", "-t", multiple=True, help="Stock ticker(s) to paper trade")
@click.option("--crypto", "-c", multiple=True, help="Crypto pair(s) to paper trade")
@click.option("--strategy", "-s", default="ma_crossover",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout", "ensemble", "all"]),
              help="Strategy to use")
@click.option("--capital", default=None, type=float, help="Initial capital (default from config)")
def paper(ticker, crypto, strategy, capital):
    """Run paper trading simulation with fake money."""
    from data.stocks import fetch_stock_data
    from data.crypto import fetch_crypto_data
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from engine.paper_trader import PaperTrader
    from engine.risk_manager import RiskManager

    click.echo("\n=== Paper Trading Simulation ===\n")

    capital = capital or CONFIG["paper_trading"]["initial_capital"]
    stock_years = CONFIG["data"]["stocks"]["lookback_years"]
    crypto_years = CONFIG["data"]["crypto"]["lookback_years"]

    risk_cfg = CONFIG["risk"]
    sizing_cfg = CONFIG.get("position_sizing", {})
    risk = RiskManager(
        max_positions=CONFIG["paper_trading"]["max_positions"],
        max_allocation_pct=CONFIG["paper_trading"]["max_allocation_pct"],
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

    strategies = []
    strat_cfg = CONFIG["strategies"]
    if strategy in ("ma_crossover", "all"):
        mc = strat_cfg["ma_crossover"]
        strategies.append(MACrossoverStrategy(
            fast_period=mc["fast_period"],
            slow_period=mc["slow_period"],
        ))
    if strategy in ("rsi_mean_revert", "all"):
        rsi = strat_cfg["rsi_mean_revert"]
        strategies.append(RSIMeanReversionStrategy(
            rsi_period=rsi["rsi_period"],
            oversold_threshold=rsi["oversold_threshold"],
            overbought_threshold=rsi["overbought_threshold"],
        ))
    if strategy in ("macd", "all"):
        mc = strat_cfg["macd"]
        strategies.append(MACDStrategy(
            fast_period=mc["fast_period"],
            slow_period=mc["slow_period"],
            signal_period=mc["signal_period"],
        ))
    if strategy in ("bollinger_bands", "all"):
        bb = strat_cfg["bollinger_bands"]
        strategies.append(BollingerBandsStrategy(
            period=bb["period"],
            num_std=bb["num_std"],
        ))
    if strategy in ("momentum_breakout", "all"):
        mb = strat_cfg["momentum_breakout"]
        strategies.append(MomentumBreakoutStrategy(
            lookback=mb["lookback"],
            exit_sma=mb["exit_sma"],
        ))
    if strategy in ("ensemble", "all"):
        from strategies.ensemble import EnsembleStrategy
        ens_cfg = CONFIG.get("ensemble", {})
        strategies.append(EnsembleStrategy(
            min_buy_votes=ens_cfg.get("min_buy_votes", 3),
        ))

    # Resolve tickers
    stock_tickers = list(ticker) if ticker else CONFIG["data"]["stocks"]["default_tickers"][:3]
    crypto_tickers = list(crypto) if crypto else CONFIG["data"]["crypto"]["default_tickers"][:2]

    all_results = []
    for t in stock_tickers:
        click.echo(f"  Fetching {t} ({stock_years}yr stock) ... ", nl=False)
        try:
            df = fetch_stock_data(t, years=stock_years)
            click.echo(f"+ {len(df)} candles")
        except Exception as e:
            click.echo(f"! {e}")
            continue

        for s in strategies:
            trader = PaperTrader(s, risk_manager=risk, initial_capital=capital,
                                 use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
                                 position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
                                 atr_period=sizing_cfg.get("atr_period", 14),
                                 atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0))
            result = trader.run(df, ticker=t)
            click.echo(result.summary())
            click.echo()
            all_results.append(result)

    for sym in crypto_tickers:
        click.echo(f"  Fetching {sym} ({crypto_years}yr crypto) ... ", nl=False)
        try:
            df = fetch_crypto_data(sym, years=crypto_years)
            click.echo(f"+ {len(df)} candles")
        except Exception as e:
            click.echo(f"! {e}")
            continue

        for s in strategies:
            trader = PaperTrader(s, risk_manager=risk, initial_capital=capital,
                                 use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
                                 position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
                                 atr_period=sizing_cfg.get("atr_period", 14),
                                 atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0))
            result = trader.run(df, ticker=sym)
            click.echo(result.summary())
            click.echo()
            all_results.append(result)

    # Save all results so `python cli.py portfolio` shows the full picture
    if all_results:
        _save_portfolio_state(all_results)
    else:
        click.echo("No trades executed -- nothing to save.")


def _save_portfolio_state(results: list) -> None:
    """Save all PaperTraderResults so the portfolio command shows a full summary."""
    import json

    entries = []
    combined_pnl = 0.0
    combined_cash = 0.0
    combined_value = 0.0

    for result in results:
        p = result.portfolio
        # Include per-position quantities so the rebalancer can reconstruct holdings
        pos_quantities = {t: round(pos.quantity, 6) for t, pos in p.positions.items()}
        entries.append({
            "ticker": result.ticker,
            "strategy": result.strategy_name,
            "cash": round(p.current_cash, 2),
            "total_value": p.total_value,
            "total_pnl": p.total_pnl,
            "total_pnl_pct": round(p.total_pnl_pct, 4),
            "sharpe": round(result.sharpe_ratio, 2),
            "sortino": round(result.sortino_ratio, 2),
            "trades": result.total_trades,
            "win_rate": round(result.win_rate, 4),
            "max_dd": round(p.max_drawdown, 4),
            "positions": len(p.positions),
            "position_quantities": pos_quantities,  # {"AAPL": 10.5, "MSFT": 5.0, ...}
        })
        combined_pnl += p.total_pnl
        combined_cash += p.current_cash
        combined_value += p.total_value

    state_file = os.path.join(os.path.dirname(__file__), "portfolio_state.json")
    state = {
        "runs": len(entries),
        "combined_pnl": round(combined_pnl, 2),
        "combined_value": round(combined_value, 2),
        "combined_cash": round(combined_cash, 2),
        "entries": entries,
        "last_updated": datetime.now().isoformat(),
    }
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2, default=str)
    click.echo(f"Portfolio state saved to {state_file} ({len(entries)} runs)")


def _run_backtest_market(label: str, tickers: list[str], years: int, capital: float,
                          commission_pct: float, strategy: str, risk,
                          strat_cfg: dict, csv_dir: str = "") -> None:
    """Run backtest for a single market. Shared by all market flags.

    If csv_dir is provided, writes one CSV per strategy to backtest_{label}_{strategy}.csv.
    """
    from data.stocks import fetch_multiple_stocks
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from backtest.runner import BacktestRunner

    click.echo(f"\n*** Backtesting {label} ({years}yr, {len(tickers)} tickers) ***\n")
    data = fetch_multiple_stocks(tickers, years=years)

    # Filter out tickers that failed to fetch
    data = {t: df for t, df in data.items() if not df.empty}
    if not data:
        click.echo("  No data fetched -- skipping.\n")
        return

    strategies_to_run = []
    if strategy in ("ma_crossover", "all"):
        mc = strat_cfg["ma_crossover"]
        strategies_to_run.append(("MA Crossover", MACrossoverStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"])))
    if strategy in ("rsi_mean_revert", "all"):
        rsi_cfg = strat_cfg["rsi_mean_revert"]
        strategies_to_run.append(("RSI Mean Reversion", RSIMeanReversionStrategy(
            rsi_period=rsi_cfg["rsi_period"],
            oversold_threshold=rsi_cfg["oversold_threshold"],
            overbought_threshold=rsi_cfg["overbought_threshold"])))
    if strategy in ("macd", "all"):
        mc = strat_cfg["macd"]
        strategies_to_run.append(("MACD", MACDStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"],
            signal_period=mc["signal_period"])))
    if strategy in ("bollinger_bands", "all"):
        bb = strat_cfg["bollinger_bands"]
        strategies_to_run.append(("Bollinger Bands", BollingerBandsStrategy(
            period=bb["period"], num_std=bb["num_std"])))
    if strategy in ("momentum_breakout", "all"):
        mb = strat_cfg["momentum_breakout"]
        strategies_to_run.append(("Momentum Breakout", MomentumBreakoutStrategy(
            lookback=mb["lookback"], exit_sma=mb["exit_sma"])))

    for sname, s in strategies_to_run:
        runner = BacktestRunner(s, capital, commission_pct, risk)
        click.echo(f"\n  -- {sname} --")
        summary = runner.run(data, market_label=label)
        click.echo(f"\n{summary.to_table()}")

        # Export to CSV if requested
        if csv_dir:
            import csv
            os.makedirs(csv_dir, exist_ok=True)
            safe_label = label.replace(" ", "_").replace("'", "")
            safe_strat = sname.replace(" ", "_").lower()
            csv_path = os.path.join(csv_dir, f"backtest_{safe_label}_{safe_strat}.csv")
            rows = summary.to_csv_rows()
            if rows:
                with open(csv_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                click.echo(f"  [CSV] Exported {len(rows)} rows -> {csv_path}")


@cli.command()
@click.option("--usa", is_flag=True, help="Backtest USA stocks (20yr)")
@click.option("--india", is_flag=True, help="Backtest Indian NSE stocks (20yr)")
@click.option("--canada", is_flag=True, help="Backtest Canadian TSX stocks (20yr)")
@click.option("--stocks", "-s", "usa_short", is_flag=True, help="Backtest USA stocks only (10yr, legacy)")
@click.option("--crypto", "-c", is_flag=True, help="Backtest crypto only")
@click.option("--strategy", "-st", default="all",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout", "all"]),
              help="Strategy to backtest")
@click.option("--years", "-y", default=None, type=int, help="Override lookback years")
@click.option("--csv", "csv_dir", is_flag=False, default="",
              help="Export results to CSV files in this directory (e.g. --csv ./reports)")
def backtest(usa, india, canada, usa_short, crypto, strategy, years, csv_dir):
    """Backtest strategies on USA, India, Canada, and Crypto markets.

    Examples:
      python cli.py backtest --usa              # USA stocks 20yr
      python cli.py backtest --india             # India NSE 20yr
      python cli.py backtest --canada            # Canada TSX 20yr
      python cli.py backtest --usa --india --canada  # All stock markets 20yr
      python cli.py backtest                     # All markets (USA 20yr + India 20yr + Canada 20yr + Crypto 4yr)
      python cli.py backtest --stocks            # USA stocks 10yr (legacy quick mode)
    """
    from data.crypto import fetch_multiple_crypto
    from backtest.runner import BacktestRunner
    from engine.risk_manager import RiskManager
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy

    bg_cfg = CONFIG["backtest"]
    risk_cfg = CONFIG["risk"]
    strat_cfg = CONFIG["strategies"]
    data_cfg = CONFIG["data"]

    risk = RiskManager(
        max_positions=CONFIG["paper_trading"]["max_positions"],
        max_allocation_pct=CONFIG["paper_trading"]["max_allocation_pct"],
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

    # Default capital and commission (same for all stock markets)
    stock_capital = bg_cfg["stocks"]["initial_capital"]
    stock_comm = bg_cfg["stocks"]["commission_pct"]

    # Determine what to run
    any_market_flag = usa or india or canada or usa_short or crypto

    # USA: 20yr if explicitly --usa, or if no market flags at all (full run)
    # usa_short: legacy --stocks flag, uses 10yr config
    run_usa_20yr = usa or (not any_market_flag)
    run_usa_10yr = usa_short
    run_india = india or (not any_market_flag)
    run_canada = canada or (not any_market_flag)
    run_crypto = crypto or (not any_market_flag)

    if run_usa_10yr:
        usa_years = years or bg_cfg["stocks"]["years"]
        tickers = data_cfg["stocks"]["default_tickers"]
        _run_backtest_market("USA Stocks", tickers, usa_years, stock_capital,
                             stock_comm, strategy, risk, strat_cfg, csv_dir)

    if run_usa_20yr and not run_usa_10yr:
        usa_years = years or 20
        tickers = data_cfg["stocks"]["default_tickers"]
        _run_backtest_market("USA Stocks", tickers, usa_years, stock_capital,
                             stock_comm, strategy, risk, strat_cfg, csv_dir)

    if run_india:
        india_years = years or data_cfg.get("india", {}).get("lookback_years", 20)
        tickers = data_cfg.get("india", {}).get("default_tickers", [])
        if tickers:
            _run_backtest_market("India NSE", tickers, india_years, stock_capital,
                                 stock_comm, strategy, risk, strat_cfg, csv_dir)
        else:
            click.echo("  No India tickers configured.\n")

    if run_canada:
        canada_years = years or data_cfg.get("canada", {}).get("lookback_years", 20)
        tickers = data_cfg.get("canada", {}).get("default_tickers", [])
        if tickers:
            _run_backtest_market("Canada TSX", tickers, canada_years, stock_capital,
                                 stock_comm, strategy, risk, strat_cfg, csv_dir)
        else:
            click.echo("  No Canada tickers configured.\n")

    if run_crypto:
        crypto_years = years or bg_cfg["crypto"]["years"]
        crypto_capital = bg_cfg["crypto"]["initial_capital"]
        crypto_comm = bg_cfg["crypto"]["commission_pct"]
        symbols = data_cfg["crypto"]["default_tickers"]

        click.echo(f"\n*** Backtesting CRYPTO ({crypto_years}yr, {len(symbols)} pairs) ***\n")
        data = fetch_multiple_crypto(symbols, years=crypto_years)
        data = {t: df for t, df in data.items() if not df.empty}

        if not data:
            click.echo("  No crypto data fetched.\n")
        else:
            # Build crypto strategies (same pattern as _run_backtest_market)
            crypto_strategies = []
            if strategy in ("ma_crossover", "all"):
                mc = strat_cfg["ma_crossover"]
                crypto_strategies.append(("MA Crossover", MACrossoverStrategy(
                    fast_period=mc["fast_period"], slow_period=mc["slow_period"])))
            if strategy in ("rsi_mean_revert", "all"):
                rsi_cfg = strat_cfg["rsi_mean_revert"]
                crypto_strategies.append(("RSI Mean Reversion", RSIMeanReversionStrategy(
                    rsi_period=rsi_cfg["rsi_period"],
                    oversold_threshold=rsi_cfg["oversold_threshold"],
                    overbought_threshold=rsi_cfg["overbought_threshold"])))
            if strategy in ("macd", "all"):
                mc = strat_cfg["macd"]
                crypto_strategies.append(("MACD", MACDStrategy(
                    fast_period=mc["fast_period"], slow_period=mc["slow_period"],
                    signal_period=mc["signal_period"])))
            if strategy in ("bollinger_bands", "all"):
                bb = strat_cfg["bollinger_bands"]
                crypto_strategies.append(("Bollinger Bands", BollingerBandsStrategy(
                    period=bb["period"], num_std=bb["num_std"])))
            if strategy in ("momentum_breakout", "all"):
                mb = strat_cfg["momentum_breakout"]
                crypto_strategies.append(("Momentum Breakout", MomentumBreakoutStrategy(
                    lookback=mb["lookback"], exit_sma=mb["exit_sma"])))

            for sname, s in crypto_strategies:
                runner = BacktestRunner(s, crypto_capital, crypto_comm, risk)
                click.echo(f"\n  -- {sname} --")
                summary = runner.run(data, market_label="Crypto")
                click.echo(f"\n{summary.to_table()}")

                # Export to CSV if requested
                if csv_dir:
                    import csv
                    os.makedirs(csv_dir, exist_ok=True)
                    safe_strat = sname.replace(" ", "_").lower()
                    csv_path = os.path.join(csv_dir, f"backtest_Crypto_{safe_strat}.csv")
                    rows = summary.to_csv_rows()
                    if rows:
                        with open(csv_path, "w", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                            writer.writeheader()
                            writer.writerows(rows)
                        click.echo(f"  [CSV] Exported {len(rows)} rows -> {csv_path}")


@cli.command()
@click.option("--ticker", "-t", default="AAPL", help="Ticker to optimize")
@click.option("--strategy", "-s", default="bollinger_bands",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout"]),
              help="Strategy to optimize")
@click.option("--train-years", default=10, type=int, help="In-sample training window (years)")
@click.option("--test-years", default=2, type=int, help="Out-of-sample test window (years)")
@click.option("--step-years", default=2, type=int, help="Years to slide window each iteration")
@click.option("--metric", "opt_metric", default="sharpe",
              type=click.Choice(["sharpe", "sortino", "return"]),
              help="Metric to optimize (default: sharpe)")
@click.option("--years", "-y", default=20, type=int, help="Total years of historical data")
@click.option("--capital", default=100000.0, type=float, help="Initial capital per run")
@click.option("--csv", "csv_path", default="", help="Export results to CSV file")
def walk_forward(ticker, strategy, train_years, test_years, step_years,
                 opt_metric, years, capital, csv_path):
    """Run walk-forward optimization to find robust parameters.

    Splits data into rolling in-sample/out-of-sample windows,
    grid-searches strategy parameters on IS data,
    and tests the best params on unseen OOS data.

    The combined OOS results give an unbiased estimate of real performance.

    Examples:
      python cli.py walk-forward --ticker AAPL --strategy bollinger_bands
      python cli.py walk-forward --ticker NVDA --strategy macd --metric sortino
      python cli.py walk-forward --ticker TSLA --strategy momentum_breakout --csv reports/wf_tsla.csv
    """
    from data.stocks import fetch_stock_data
    from engine.walk_forward import (
        WalkForwardOptimizer, WalkForwardConfig, STRATEGY_PARAM_GRIDS,
    )
    from engine.risk_manager import RiskManager

    risk_cfg = CONFIG["risk"]
    risk = RiskManager(
        max_positions=CONFIG["paper_trading"]["max_positions"],
        max_allocation_pct=CONFIG["paper_trading"]["max_allocation_pct"],
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

    # Fetch data
    click.echo(f"\n  Fetching {ticker} ({years}yr)... ", nl=False)
    try:
        df = fetch_stock_data(ticker, years=years)
        click.echo(f"{len(df)} candles ({df.index[0].date()} -> {df.index[-1].date()})")
    except Exception as e:
        click.echo(f"ERROR: {e}")
        return

    param_grid = STRATEGY_PARAM_GRIDS.get(strategy, {})
    if not param_grid:
        click.echo(f"ERROR: No parameter grid defined for strategy '{strategy}'")
        return

    config = WalkForwardConfig(
        train_years=train_years,
        test_years=test_years,
        step_years=step_years,
        optimization_metric=opt_metric,
    )

    optimizer = WalkForwardOptimizer(
        strategy_name=strategy,
        param_grid=param_grid,
        config=config,
        initial_capital=capital,
        risk_manager=risk,
        verbose=True,
    )

    summary = optimizer.run(df, ticker=ticker)
    click.echo(summary.to_table())

    # Export to CSV if requested
    if csv_path:
        import csv
        rows = summary.to_csv_rows()
        if rows:
            parent = os.path.dirname(csv_path) or "."
            os.makedirs(parent, exist_ok=True)
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            click.echo(f"  [CSV] Exported {len(rows)} rows -> {csv_path}")

    click.echo()


@cli.command()
@click.option("--ticker", "-t", default="AAPL", help="Ticker to chart")
@click.option("--strategy", "-s", default="ma_crossover",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout", "ensemble"]),
              help="Strategy to run")
@click.option("--years", "-y", default=10, type=int, help="Years of historical data")
@click.option("--capital", default=100000.0, type=float, help="Initial capital")
@click.option("--output", "-o", default="", help="Output PNG path (default: charts/{ticker}_{strategy}.png)")
def chart(ticker, strategy, years, capital, output):
    """Generate candlestick chart with buy/sell markers and equity curve.

    Runs a paper trade on the ticker+strategy and generates a 3-panel chart:
      1. Price chart with buy/sell markers + SMA 50/200
      2. Equity curve with drawdown shading
      3. Drawdown percentage over time

    Examples:
      python cli.py chart --ticker AAPL --strategy ma_crossover
      python cli.py chart --ticker NVDA --strategy bollinger_bands --years 5
      python cli.py chart --ticker TSLA --strategy momentum_breakout -o charts/tsla_mb.png
    """
    from data.stocks import fetch_stock_data
    from engine.paper_trader import PaperTrader
    from engine.risk_manager import RiskManager
    from engine.charts import plot_backtest_chart
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from strategies.ensemble import EnsembleStrategy

    risk_cfg = CONFIG["risk"]
    sizing_cfg = CONFIG.get("position_sizing", {})
    risk = RiskManager(
        max_positions=CONFIG["paper_trading"]["max_positions"],
        max_allocation_pct=CONFIG["paper_trading"]["max_allocation_pct"],
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

    click.echo(f"\n  Fetching {ticker} ({years}yr)... ", nl=False)
    try:
        df = fetch_stock_data(ticker, years=years)
        click.echo(f"{len(df)} candles ({df.index[0].date()} -> {df.index[-1].date()})")
    except Exception as e:
        click.echo(f"ERROR: {e}")
        return

    # Build strategy
    bg_cfg = CONFIG["backtest"]
    strat_cfg = CONFIG["strategies"]
    if strategy == "ma_crossover":
        mc = strat_cfg["ma_crossover"]
        strat = MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"])
    elif strategy == "rsi_mean_revert":
        rsi = strat_cfg["rsi_mean_revert"]
        strat = RSIMeanReversionStrategy(
            rsi_period=rsi["rsi_period"],
            oversold_threshold=rsi["oversold_threshold"],
            overbought_threshold=rsi["overbought_threshold"])
    elif strategy == "macd":
        mc = strat_cfg["macd"]
        strat = MACDStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"],
            signal_period=mc["signal_period"])
    elif strategy == "bollinger_bands":
        bb = strat_cfg["bollinger_bands"]
        strat = BollingerBandsStrategy(period=bb["period"], num_std=bb["num_std"])
    elif strategy == "momentum_breakout":
        mb = strat_cfg["momentum_breakout"]
        strat = MomentumBreakoutStrategy(lookback=mb["lookback"], exit_sma=mb["exit_sma"])
    elif strategy == "ensemble":
        ens_cfg = CONFIG.get("ensemble", {})
        strat = EnsembleStrategy(min_buy_votes=ens_cfg.get("min_buy_votes", 3))

    trader = PaperTrader(strat, risk_manager=risk, initial_capital=capital,
                         commission_pct=bg_cfg["stocks"]["commission_pct"],
                         use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
                         position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
                         atr_period=sizing_cfg.get("atr_period", 14),
                         atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0))
    result = trader.run(df, ticker=ticker)
    click.echo(result.summary())

    # Generate chart
    if not output:
        chart_dir = CONFIG.get("charts", {}).get("output_dir", "charts")
        os.makedirs(chart_dir, exist_ok=True)
        output = os.path.join(chart_dir, f"{ticker}_{strategy}.png")

    chart_dpi = CONFIG.get("charts", {}).get("dpi", 150)
    saved = plot_backtest_chart(df, result, save_path=output, dpi=chart_dpi)
    click.echo(f"\n  [Chart] Saved -> {saved}")
    click.echo()


@cli.command()
@click.option("--ticker", "-t", default="AAPL", help="Ticker to grid-search")
@click.option("--strategy", "-s", default="bollinger_bands",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout"]),
              help="Strategy to optimize")
@click.option("--metric", default="sharpe",
              type=click.Choice(["sharpe", "sortino", "return"]),
              help="Ranking metric")
@click.option("--top", "-n", default=10, type=int, help="Show top N results")
@click.option("--years", "-y", default=10, type=int, help="Years of historical data")
@click.option("--capital", default=100000.0, type=float, help="Initial capital")
@click.option("--csv", "csv_path", default="", help="Export results to CSV")
def grid_search(ticker, strategy, metric, top, years, capital, csv_path):
    """Exhaustive parameter grid search -- rank all combos by Sharpe/Return/Sortino.

    Tests every combination in the strategy's parameter grid on historical data
    and ranks results to find the optimal parameter set.

    Examples:
      python cli.py grid-search --ticker AAPL --strategy bollinger_bands
      python cli.py grid-search --ticker NVDA --strategy macd --metric return --top 5
      python cli.py grid-search --ticker TSLA --strategy momentum_breakout --csv reports/gs_tsla.csv
    """
    from data.stocks import fetch_stock_data
    from engine.paper_trader import PaperTrader
    from engine.risk_manager import RiskManager
    from engine.walk_forward import STRATEGY_PARAM_GRIDS, _build_strategy
    from itertools import product
    from tabulate import tabulate

    risk_cfg = CONFIG["risk"]
    sizing_cfg = CONFIG.get("position_sizing", {})
    risk = RiskManager(
        max_positions=CONFIG["paper_trading"]["max_positions"],
        max_allocation_pct=CONFIG["paper_trading"]["max_allocation_pct"],
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

    param_grid = STRATEGY_PARAM_GRIDS.get(strategy, {})
    if not param_grid:
        click.echo(f"ERROR: No parameter grid defined for strategy '{strategy}'")
        return

    # Generate all parameter combinations
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = [dict(zip(keys, combo)) for combo in product(*values)]

    click.echo(f"\n  Grid Search: {strategy} on {ticker}")
    click.echo(f"  Parameter grid: {len(combos)} combinations ({' x '.join(f'{len(v)}' for v in values)})")
    click.echo(f"  Ranking by: {metric}")
    click.echo(f"  Fetching {ticker} ({years}yr)... ", nl=False)

    try:
        df = fetch_stock_data(ticker, years=years)
        click.echo(f"{len(df)} candles\n")
    except Exception as e:
        click.echo(f"ERROR: {e}")
        return

    # Run all combos
    results: list[tuple[dict, float, PaperTraderResult]] = []
    for i, params in enumerate(combos, 1):
        strat = _build_strategy(strategy, **params)
        trader = PaperTrader(strat, risk_manager=risk, initial_capital=capital,
                             use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
                             position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
                             atr_period=sizing_cfg.get("atr_period", 14),
                             atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0))
        result = trader.run(df, ticker=ticker)

        if metric == "sharpe":
            score = result.sharpe_ratio
        elif metric == "sortino":
            score = result.sortino_ratio
        elif metric == "return":
            score = result.portfolio.total_pnl_pct
        else:
            score = result.sharpe_ratio

        results.append((params, score, result))
        click.echo(f"  [{i}/{len(combos)}] {_params_str(params):40s} "
                   f"{metric.upper()}={score:.3f}  Ret={result.portfolio.total_pnl_pct:+.2%}  "
                   f"Trades={result.total_trades}  WR={result.win_rate:.1%}")

    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)

    # Display top N
    click.echo(f"\n=== Top {top} by {metric.upper()} ===\n")
    rows = []
    for rank, (params, score, r) in enumerate(results[:top], 1):
        rows.append([
            rank,
            _params_str(params),
            f"{score:.3f}",
            f"{r.portfolio.total_pnl_pct:+.2%}",
            f"${r.portfolio.total_pnl:+,.0f}",
            f"{r.sharpe_ratio:.2f}",
            f"{r.sortino_ratio:.2f}",
            f"{r.portfolio.max_drawdown:+.2%}",
            f"{r.win_rate:.1%}",
            r.total_trades,
        ])
    click.echo(tabulate(rows, headers=["Rank", "Params", metric.upper(), "Return", "P&L",
                                        "Sharpe", "Sortino", "Max DD", "Win Rate", "Trades"],
                        tablefmt="grid", stralign="right"))

    # CSV export
    if csv_path:
        import csv
        parent = os.path.dirname(csv_path) or "."
        os.makedirs(parent, exist_ok=True)
        fieldnames = ["Rank", "Params", metric.upper(), "Return", "P&L",
                      "Sharpe", "Sortino", "Max DD", "Win Rate", "Trades"]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for rank, (params, score, r) in enumerate(results, 1):
                writer.writerow({
                    "Rank": rank,
                    "Params": _params_str(params),
                    metric.upper(): round(score, 3),
                    "Return": round(r.portfolio.total_pnl_pct, 4),
                    "P&L": round(r.portfolio.total_pnl, 2),
                    "Sharpe": round(r.sharpe_ratio, 2),
                    "Sortino": round(r.sortino_ratio, 2),
                    "Max DD": round(r.portfolio.max_drawdown, 4),
                    "Win Rate": round(r.win_rate, 4),
                    "Trades": r.total_trades,
                })
        click.echo(f"\n  [CSV] Exported {len(results)} rows -> {csv_path}")

    click.echo()


def _params_str(params: dict) -> str:
    """Format params dict as a compact string: 'period=20, std=2.0'."""
    return ", ".join(f"{k}={v}" for k, v in params.items())


@cli.command()
@click.option("--stocks", "-s", is_flag=True, help="Rank stocks only")
@click.option("--crypto", "-c", is_flag=True, help="Rank crypto only")
@click.option("--top", "-n", default=10, help="Show top N results")
def benchmark(stocks, crypto, top):
    """Rank all strategies by Sharpe, Sortino, return, and win rate."""
    from data.stocks import fetch_multiple_stocks
    from data.crypto import fetch_multiple_crypto
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from engine.paper_trader import PaperTrader
    from engine.risk_manager import RiskManager
    from tabulate import tabulate

    bg_cfg = CONFIG["backtest"]
    risk_cfg = CONFIG["risk"]
    strat_cfg = CONFIG["strategies"]

    risk = RiskManager(
        max_positions=CONFIG["paper_trading"]["max_positions"],
        max_allocation_pct=CONFIG["paper_trading"]["max_allocation_pct"],
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

    # Build all strategies
    all_strategies = [
        ("MA Crossover", MACrossoverStrategy(
            fast_period=strat_cfg["ma_crossover"]["fast_period"],
            slow_period=strat_cfg["ma_crossover"]["slow_period"],
        )),
        ("RSI Mean Rev", RSIMeanReversionStrategy(
            rsi_period=strat_cfg["rsi_mean_revert"]["rsi_period"],
            oversold_threshold=strat_cfg["rsi_mean_revert"]["oversold_threshold"],
            overbought_threshold=strat_cfg["rsi_mean_revert"]["overbought_threshold"],
        )),
        ("MACD", MACDStrategy(
            fast_period=strat_cfg["macd"]["fast_period"],
            slow_period=strat_cfg["macd"]["slow_period"],
            signal_period=strat_cfg["macd"]["signal_period"],
        )),
        ("Bollinger", BollingerBandsStrategy(
            period=strat_cfg["bollinger_bands"]["period"],
            num_std=strat_cfg["bollinger_bands"]["num_std"],
        )),
        ("Momentum", MomentumBreakoutStrategy(
            lookback=strat_cfg["momentum_breakout"]["lookback"],
            exit_sma=strat_cfg["momentum_breakout"]["exit_sma"],
        )),
    ]

    all_results: list[tuple[str, str, object]] = []  # (strategy_name, ticker, PaperTraderResult)

    run_stocks = not crypto
    run_crypto = not stocks

    if run_stocks:
        stock_tickers = CONFIG["data"]["stocks"]["default_tickers"]
        stock_years = bg_cfg["stocks"]["years"]
        stock_capital = bg_cfg["stocks"]["initial_capital"]
        stock_comm = bg_cfg["stocks"]["commission_pct"]

        click.echo(f"\n*** Benchmarking STOCKS ({len(stock_tickers)} tickers x {len(all_strategies)} strategies) ***\n")
        data = fetch_multiple_stocks(stock_tickers, years=stock_years)

        for sname, strat in all_strategies:
            click.echo(f"  {sname} ... ", nl=False)
            for ticker, df in data.items():
                if df.empty:
                    continue
                trader = PaperTrader(strat.clone(), risk_manager=risk,
                                     initial_capital=stock_capital, commission_pct=stock_comm)
                result = trader.run(df, ticker=ticker)
                all_results.append((sname, ticker, result))
            click.echo(f"{len(data)} tickers done")

    if run_crypto:
        crypto_tickers = CONFIG["data"]["crypto"]["default_tickers"]
        crypto_years = bg_cfg["crypto"]["years"]
        crypto_capital = bg_cfg["crypto"]["initial_capital"]
        crypto_comm = bg_cfg["crypto"]["commission_pct"]

        click.echo(f"\n*** Benchmarking CRYPTO ({len(crypto_tickers)} pairs x {len(all_strategies)} strategies) ***\n")
        data = fetch_multiple_crypto(crypto_tickers, years=crypto_years)

        for sname, strat in all_strategies:
            click.echo(f"  {sname} ... ", nl=False)
            for ticker, df in data.items():
                if df.empty:
                    continue
                trader = PaperTrader(strat.clone(), risk_manager=risk,
                                     initial_capital=crypto_capital, commission_pct=crypto_comm)
                result = trader.run(df, ticker=ticker)
                all_results.append((sname, ticker, result))
            click.echo(f"{len(data)} tickers done")

    if not all_results:
        click.echo("No results to display.")
        return

    # Rank by Sharpe
    click.echo(f"\n=== Top {top} by Sharpe Ratio ===\n")
    by_sharpe = sorted(all_results, key=lambda x: x[2].sharpe_ratio, reverse=True)[:top]
    rows = [[s, t, f"{r.sharpe_ratio:.2f}", f"{r.sortino_ratio:.2f}",
              f"{r.portfolio.total_pnl_pct:+.2%}", f"{r.portfolio.max_drawdown:+.2%}",
              f"{r.win_rate:.1%}", r.total_trades, r.max_drawdown_duration]
             for s, t, r in by_sharpe]
    click.echo(tabulate(rows, headers=["Strategy", "Ticker", "Sharpe", "Sortino",
                                        "Return", "Max DD", "Win Rate", "Trades", "DD Days"],
                        tablefmt="grid", stralign="right"))

    # Rank by Sortino
    click.echo(f"\n=== Top {top} by Sortino Ratio ===\n")
    by_sortino = sorted(all_results, key=lambda x: x[2].sortino_ratio, reverse=True)[:top]
    rows = [[s, t, f"{r.sortino_ratio:.2f}", f"{r.sharpe_ratio:.2f}",
              f"{r.portfolio.total_pnl_pct:+.2%}", f"{r.portfolio.max_drawdown:+.2%}",
              f"{r.win_rate:.1%}", r.total_trades, r.max_drawdown_duration]
             for s, t, r in by_sortino]
    click.echo(tabulate(rows, headers=["Strategy", "Ticker", "Sortino", "Sharpe",
                                        "Return", "Max DD", "Win Rate", "Trades", "DD Days"],
                        tablefmt="grid", stralign="right"))

    # Rank by Return
    click.echo(f"\n=== Top {top} by Return ===\n")
    by_return = sorted(all_results, key=lambda x: x[2].portfolio.total_pnl_pct, reverse=True)[:top]
    rows = [[s, t, f"{r.portfolio.total_pnl_pct:+.2%}", f"${r.portfolio.total_pnl:+,.0f}",
              f"{r.sharpe_ratio:.2f}", f"{r.portfolio.max_drawdown:+.2%}",
              f"{r.win_rate:.1%}", r.total_trades]
             for s, t, r in by_return]
    click.echo(tabulate(rows, headers=["Strategy", "Ticker", "Return", "P&L",
                                        "Sharpe", "Max DD", "Win Rate", "Trades"],
                        tablefmt="grid", stralign="right"))

    # Strategy-level averages (filter Sortino 999 sentinel from averages)
    click.echo(f"\n=== Strategy Averages (across all tickers) ===\n")
    from collections import defaultdict
    strat_agg = defaultdict(lambda: {"sharpe": [], "sortino": [], "return": [],
                                       "dd": [], "wr": [], "trades": 0, "dds": []})
    for sname, _, r in all_results:
        strat_agg[sname]["sharpe"].append(r.sharpe_ratio)
        strat_agg[sname]["sortino"].append(r.sortino_ratio)
        strat_agg[sname]["return"].append(r.portfolio.total_pnl_pct)
        strat_agg[sname]["dd"].append(r.portfolio.max_drawdown)
        strat_agg[sname]["wr"].append(r.win_rate)
        strat_agg[sname]["trades"] += r.total_trades
        strat_agg[sname]["dds"].append(r.max_drawdown_duration)

    from engine.paper_trader import avg_sortino_filtered

    agg_rows = []
    for sname in [s[0] for s in all_strategies]:
        a = strat_agg[sname]
        n = len(a["return"])
        agg_rows.append([
            sname, n,
            f"{sum(a['sharpe'])/n:.2f}",
            f"{avg_sortino_filtered(a['sortino']):.2f}",
            f"{sum(a['return'])/n:+.2%}",
            f"{sum(a['dd'])/n:+.2%}",
            f"{sum(a['wr'])/n:.1%}",
            a["trades"],
            f"{sum(a['dds'])/n:.0f}",
        ])
    click.echo(tabulate(agg_rows, headers=["Strategy", "Runs", "Avg Sharpe", "Avg Sortino",
                                            "Avg Return", "Avg Max DD", "Avg Win Rate",
                                            "Total Trades", "Avg DD Days"],
                        tablefmt="grid", stralign="right"))

    click.echo()


@cli.command()
def portfolio():
    """View current portfolio state (from saved state file)."""
    import json

    state_file = os.path.join(os.path.dirname(__file__), "portfolio_state.json")

    if not os.path.exists(state_file):
        click.echo("No saved portfolio state found. Run `paper` or `schedule` first.")
        return

    with open(state_file) as f:
        data = json.load(f)

    entries = data.get("entries", [])

    click.echo(f"\n=== Saved Portfolio ({data['runs']} runs) ===")
    click.echo(f"  Combined P&L:   ${data['combined_pnl']:+,.2f}")
    click.echo(f"  Combined Value: ${data['combined_value']:,.2f}")
    click.echo(f"  Last updated:   {data.get('last_updated', 'unknown')}")

    if entries:
        from tabulate import tabulate
        click.echo()
        rows = [[e["ticker"], e["strategy"], f"${e['total_pnl']:+,.0f}",
                  f"{e['total_pnl_pct']:+.2%}", f"{e['sharpe']:.2f}",
                  f"{e['sortino']:.2f}", f"{e['win_rate']:.1%}", e["trades"]]
                 for e in entries]
        click.echo(tabulate(rows, headers=["Ticker", "Strategy", "P&L", "Return",
                                            "Sharpe", "Sortino", "Win Rate", "Trades"],
                            tablefmt="grid", stralign="right"))


@cli.command()
@click.option("--ticker", "-t", default="AAPL", help="Ticker to fetch data for")
@click.option("--years", "-y", default=10, help="Years of historical data")
def data(ticker, years):
    """Fetch and display market data for a ticker."""
    from data.stocks import fetch_stock_data

    click.echo(f"\n  Fetching {ticker} ({years} years) ... ", nl=False)
    try:
        df = fetch_stock_data(ticker, years=years)
        click.echo(f"+ {len(df)} candles")
    except Exception as e:
        click.echo(f"! {e}")
        return

    click.echo(f"\n  -- {ticker} Summary --")
    click.echo(f"  Period:   {df.index[0].date()} -> {df.index[-1].date()}")
    click.echo(f"  Candles:  {len(df)}")
    click.echo(f"  Open:     ${df['open'].iloc[0]:.2f} -> ${df['open'].iloc[-1]:.2f}")
    click.echo(f"  High:     ${df['high'].max():.2f}")
    click.echo(f"  Low:      ${df['low'].min():.2f}")
    click.echo(f"  Close:    ${df['close'].iloc[-1]:.2f}")
    click.echo(f"  Range:    ${df['close'].min():.2f} -- ${df['close'].max():.2f}")
    click.echo(f"  Volume Avg: {df['volume'].mean():,.0f}")


@cli.command()
@click.option("--ticker", "-t", multiple=True, help="Ticker(s) to trade live")
@click.option("--strategy", "-s", default="ma_crossover",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout"]),
              help="Strategy to use")
@click.option("--once", is_flag=True, help="Run one cycle then exit (default: loop)")
def live(ticker, strategy, once):
    """Run live trading via Alpaca (PAPER by default)."""
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from engine.live_trader import AlpacaLiveTrader, LiveTraderConfig
    from engine.risk_manager import RiskManager

    live_cfg = CONFIG["live_trading"]
    api_cfg = CONFIG["api"]

    alpaca_key = api_cfg.get("alpaca_api_key", "")
    alpaca_secret = api_cfg.get("alpaca_api_secret", "")

    if not alpaca_key or not alpaca_secret or alpaca_key.startswith("${"):
        click.echo("ERROR: ALPACA_API_KEY and ALPACA_API_SECRET must be set in .env file.")
        click.echo("  1. Get keys at https://app.alpaca.markets/")
        click.echo("  2. Add to .env: ALPACA_API_KEY=PK...")
        click.echo("                   ALPACA_API_SECRET=...")
        return

    tickers = list(ticker) if ticker else live_cfg.get("tickers", ["AAPL", "MSFT", "GOOGL"])

    strat_cfg = CONFIG["strategies"]
    sizing_cfg = CONFIG.get("position_sizing", {})
    if strategy == "ma_crossover":
        mc = strat_cfg["ma_crossover"]
        strat = MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"])
    elif strategy == "rsi_mean_revert":
        rsi = strat_cfg["rsi_mean_revert"]
        strat = RSIMeanReversionStrategy(
            rsi_period=rsi["rsi_period"],
            oversold_threshold=rsi["oversold_threshold"],
            overbought_threshold=rsi["overbought_threshold"],
        )
    elif strategy == "macd":
        mc = strat_cfg["macd"]
        strat = MACDStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"],
            signal_period=mc["signal_period"],
        )
    elif strategy == "bollinger_bands":
        bb = strat_cfg["bollinger_bands"]
        strat = BollingerBandsStrategy(period=bb["period"], num_std=bb["num_std"])
    elif strategy == "momentum_breakout":
        mb = strat_cfg["momentum_breakout"]
        strat = MomentumBreakoutStrategy(lookback=mb["lookback"], exit_sma=mb["exit_sma"])
    else:
        click.echo(f"Unknown strategy: {strategy}")
        return

    config = LiveTraderConfig(
        api_key=alpaca_key,
        api_secret=alpaca_secret,
        paper=live_cfg.get("paper", True),
        initial_capital=live_cfg.get("initial_capital", 100_000),
        max_positions=live_cfg.get("max_positions", 5),
        max_allocation_pct=live_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=CONFIG["risk"]["max_daily_loss_pct"],
        stop_loss_pct=CONFIG["risk"]["stop_loss_pct"],
        take_profit_pct=CONFIG["risk"]["take_profit_pct"],
        poll_interval_seconds=live_cfg.get("poll_interval_seconds", 60),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
    )

    trader = AlpacaLiveTrader(config, strat)
    trader.run(tickers, once=once)


@cli.command()
def schedule():
    """Start the daily trading scheduler (runs forever)."""
    from scheduler import start_scheduler
    mode = CONFIG["scheduler"].get("mode", "paper")
    click.echo("Starting daily trading scheduler...")
    click.echo(f"   Mode:      {mode}")
    click.echo(f"   Run time:  {CONFIG['scheduler']['run_time']} {CONFIG['scheduler']['timezone']}")
    click.echo("   Press Ctrl+C to stop.")
    start_scheduler(CONFIG)


@cli.command()
@click.option("--execute", "-x", is_flag=True, help="Execute orders via Alpaca (requires API keys)")
@click.option("--force", is_flag=True, help="Rebalance even if drift < threshold")
def rebalance(execute, force):
    """Check portfolio allocations vs target weights and rebalance."""
    from engine.rebalancer import Rebalancer, RebalanceConfig
    from strategies.ma_crossover import MACrossoverStrategy

    rb_cfg = CONFIG.get("rebalancing", {})
    if not rb_cfg.get("enabled", False):
        click.echo("Rebalancing is disabled in config.yaml (rebalancing.enabled: false).")
        return

    target_weights = rb_cfg.get("target_weights", {})
    if not target_weights:
        click.echo("No target_weights configured in config.yaml.")
        return

    # Load saved portfolio state if available (from paper runs or scheduler)
    import json
    state_file = os.path.join(os.path.dirname(__file__), "portfolio_state.json")
    from engine.portfolio import Portfolio, Position
    from datetime import datetime

    portfolio = Portfolio(initial_capital=CONFIG["paper_trading"]["initial_capital"],
                          current_cash=CONFIG["paper_trading"]["initial_capital"])

    saved_quantities = {}
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
        portfolio.current_cash = state.get("cash", portfolio.current_cash)
        for entry in state.get("entries", []):
            t = entry.get("ticker", "")
            # Use the new position_quantities field if present, else fall back to 0
            pos_qty = entry.get("position_quantities", {})
            qty = pos_qty.get(t, 0) if isinstance(pos_qty, dict) else 0
            if t and t in target_weights:
                saved_quantities[t] = saved_quantities.get(t, 0) + qty

    # Fetch current prices for target tickers
    from data.stocks import fetch_stock_data
    current_prices = {}
    years = CONFIG["data"]["stocks"]["lookback_years"]
    for ticker in target_weights:
        try:
            df = fetch_stock_data(ticker, years=years)
            current_prices[ticker] = float(df["close"].iloc[-1])
            portfolio.update_price(ticker, current_prices[ticker])
            # Create position from saved quantities (or 0 if not held)
            qty = saved_quantities.get(ticker, 0)
            portfolio.positions[ticker] = Position(
                ticker=ticker, quantity=qty, avg_entry_price=current_prices[ticker],
                entry_date=datetime.now(),
            )
        except Exception as e:
            click.echo(f"  ! {ticker}: {e}")

    # Choose executor
    executor = None
    if execute:
        api_cfg = CONFIG["api"]
        alpaca_key = api_cfg.get("alpaca_api_key", "")
        alpaca_secret = api_cfg.get("alpaca_api_secret", "")
        if not alpaca_key or not alpaca_secret or alpaca_key.startswith("${"):
            click.echo("ERROR: --execute requires ALPACA_API_KEY/ALPACA_API_SECRET in .env")
            return
        from engine.live_trader import AlpacaLiveTrader, LiveTraderConfig
        mc = CONFIG["strategies"]["ma_crossover"]
        strat = MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"])
        live_cfg = CONFIG.get("live_trading", {})
        executor = AlpacaLiveTrader(LiveTraderConfig(
            api_key=alpaca_key, api_secret=alpaca_secret,
            paper=live_cfg.get("paper", True),
            initial_capital=live_cfg.get("initial_capital", 100_000),
            max_positions=live_cfg.get("max_positions", 5),
            max_allocation_pct=live_cfg.get("max_allocation_pct", 0.20),
        ), strat)
        # Sync portfolio from the broker so we rebalance real positions
        click.echo("  Syncing portfolio from Alpaca...")
        portfolio = executor.sync_portfolio()
        # Refresh prices after sync
        for ticker in target_weights:
            if ticker in portfolio.positions:
                try:
                    current_prices[ticker] = executor.get_latest_price(ticker)
                except Exception:
                    pass  # keep the historical close price as fallback

    click.echo(f"\n=== Portfolio Rebalancer ===")
    click.echo(f"  Drift threshold: {rb_cfg['drift_threshold_pct']:.0%}")
    click.echo(f"  Min trade value: ${rb_cfg['min_trade_value']:,.0f}")
    click.echo(f"  Execute:         {'YES (Alpaca)' if executor else 'DRY-RUN'}")

    rebalance_config = RebalanceConfig(
        target_weights=target_weights,
        drift_threshold_pct=rb_cfg.get("drift_threshold_pct", 0.05),
        min_trade_value=rb_cfg.get("min_trade_value", 100.0),
        execute=executor is not None,
    )
    rebal = Rebalancer(rebalance_config, portfolio, executor=executor)

    # Show current allocations
    total_val = rebal._compute_total_value(current_prices)
    click.echo(f"\n  Portfolio Value: ${total_val:,.2f}")
    click.echo(f"  Current Allocations:")
    for ticker in target_weights:
        w = rebal._current_weight(ticker, current_prices, total_val)
        click.echo(f"    {ticker:6s}: {w:7.2%}  (target: {target_weights[ticker]:.0%})")

    if not force and not rebal.needs_rebalance(current_prices):
        click.echo("\n  ✓ All positions within drift limits. No rebalance needed.")
        return

    orders = rebal.compute_rebalance_orders(current_prices)
    if not orders:
        click.echo("\n  No rebalance orders computed (all deltas below min_trade_value).")
        return

    click.echo(f"\n  === {len(orders)} Rebalance Orders ===")
    from tabulate import tabulate
    rows = [[o.ticker, o.action, f"{o.current_weight:.2%}", f"{o.target_weight:.2%}",
              f"${o.delta_value:+,.0f}", f"{o.quantity:,.4f}"]
             for o in orders]
    click.echo(tabulate(rows, headers=["Ticker", "Action", "Current", "Target",
                                        "Delta $", "Quantity"],
                        tablefmt="grid", stralign="right"))

    results = rebal.execute_rebalance(orders)
    if executor:
        submitted = [r for r in results if r["status"] == "SUBMITTED"]
        click.echo(f"\n  Done: {len(submitted)} orders submitted to Alpaca.")
    else:
        click.echo(f"\n  Dry-run complete. Use --execute to submit orders via Alpaca.")
    click.echo()


@cli.command("ibkr-live")
@click.option("--ticker", "-t", multiple=True, help="Ticker(s) to trade (e.g., NVDA, SBIN.NS, RY.TO)")
@click.option("--market", "-m", default="usa",
              type=click.Choice(["usa", "india", "canada", "all"]),
              help="Market to trade: usa, india, canada, or all")
@click.option("--strategy", "-s", default="bollinger_bands",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout"]),
              help="Strategy to use")
@click.option("--once", is_flag=True, help="Run one cycle then exit (default: loop)")
@click.option("--port", "-p", default=7497, type=int,
              help="IBKR port (7497=TWS paper, 7496=TWS live, 4002=Gateway paper)")
def ibkr_live(ticker, market, strategy, once, port):
    """Run live trading via Interactive Brokers (PAPER by default).

    Requires TWS or IB Gateway running with API connections enabled.
    Supports NSE (.NS), US (no suffix), and TSX (.TO) tickers in one account.

    Examples:
      python cli.py ibkr-live --market usa --strategy bollinger_bands
      python cli.py ibkr-live --market india --strategy macd --once
      python cli.py ibkr-live --market all --strategy bollinger_bands
      python cli.py ibkr-live -t NVDA -t AAPL -t MSFT --once
    """
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from engine.ibkr_live_trader import IBKRLiveTrader, IBKRLiveTraderConfig
    from engine.risk_manager import RiskManager

    ibkr_cfg = CONFIG.get("ibkr_live_trading", {})
    risk_cfg = CONFIG["risk"]
    strat_cfg = CONFIG["strategies"]
    sizing_cfg = CONFIG.get("position_sizing", {})

    # Resolve tickers
    if ticker:
        tickers = list(ticker)
    else:
        market_tickers = ibkr_cfg.get("tickers", {})
        tickers = []
        if market in ("usa", "all"):
            tickers.extend(market_tickers.get("usa", ["AAPL", "MSFT", "NVDA", "QQQ", "SPY"]))
        if market in ("india", "all"):
            tickers.extend(market_tickers.get("india", ["SBIN.NS", "ICICIBANK.NS", "BHARTIARTL.NS"]))
        if market in ("canada", "all"):
            tickers.extend(market_tickers.get("canada", ["RY.TO", "TD.TO", "SHOP.TO"]))

    if not tickers:
        click.echo("ERROR: No tickers specified. Use --ticker or --market.")
        return

    # Build strategy
    if strategy == "ma_crossover":
        mc = strat_cfg["ma_crossover"]
        strat = MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"])
    elif strategy == "rsi_mean_revert":
        rsi = strat_cfg["rsi_mean_revert"]
        strat = RSIMeanReversionStrategy(
            rsi_period=rsi["rsi_period"],
            oversold_threshold=rsi["oversold_threshold"],
            overbought_threshold=rsi["overbought_threshold"],
        )
    elif strategy == "macd":
        mc = strat_cfg["macd"]
        strat = MACDStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"],
            signal_period=mc["signal_period"],
        )
    elif strategy == "bollinger_bands":
        bb = strat_cfg["bollinger_bands"]
        strat = BollingerBandsStrategy(period=bb["period"], num_std=bb["num_std"])
    elif strategy == "momentum_breakout":
        mb = strat_cfg["momentum_breakout"]
        strat = MomentumBreakoutStrategy(lookback=mb["lookback"], exit_sma=mb["exit_sma"])
    else:
        click.echo(f"Unknown strategy: {strategy}")
        return

    risk = RiskManager(
        max_positions=ibkr_cfg.get("max_positions", 5),
        max_allocation_pct=ibkr_cfg.get("max_allocation_pct", 0.20),
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

    config = IBKRLiveTraderConfig(
        host=ibkr_cfg.get("host", "127.0.0.1"),
        port=port,
        client_id=ibkr_cfg.get("client_id", 1),
        initial_capital=ibkr_cfg.get("initial_capital", 100_000),
        max_positions=ibkr_cfg.get("max_positions", 5),
        max_allocation_pct=ibkr_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=risk_cfg["max_daily_loss_pct"],
        stop_loss_pct=risk_cfg["stop_loss_pct"],
        take_profit_pct=risk_cfg["take_profit_pct"],
        poll_interval_seconds=ibkr_cfg.get("poll_interval_seconds", 60),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
    )

    click.echo(f"\nConnecting to IBKR at {config.host}:{config.port}...")
    try:
        trader = IBKRLiveTrader(config, strat, risk_manager=risk)
        trader.run(tickers, once=once)
    except ConnectionError as e:
        click.echo(f"\nERROR: {e}")
        click.echo("\nMake sure TWS or IB Gateway is running:")
        click.echo("  1. Launch TWS (Trader Workstation) or IB Gateway")
        click.echo("  2. Log in with your paper or live credentials")
        click.echo("  3. File → Global Configuration → API → Settings")
        click.echo("  4. ☑ Enable ActiveX and Socket Clients")
        click.echo(f"  5. Verify port: {config.port}")
    except ImportError as e:
        click.echo(f"\nERROR: {e}")


@cli.command("dhan-live")
@click.option("--ticker", "-t", multiple=True, help="NSE ticker(s) to trade (e.g., SBIN.NS, ICICIBANK.NS)")
@click.option("--strategy", "-s", "strategies", multiple=True,
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout"]),
              help="Strategy to use (repeatable, e.g. -s macd -s bollinger_bands)")
@click.option("--all", "all_strategies", is_flag=True, help="Run all 5 strategies (MACD, Bollinger, RSI, MA Crossover, Momentum Breakout)")
@click.option("--once", is_flag=True, help="Run one cycle then exit (default: loop)")
@click.option("--live", "live_mode", is_flag=True, help="Use Dhan LIVE (default: sandbox)")
@click.option("--intraday", is_flag=True, help="Use intraday bars (5m) instead of daily — catches breakouts during the day")
@click.option("--intraday-interval", default=None,
              type=click.Choice(["1m", "5m", "15m", "30m", "1h"]),
              help="Intraday candle interval (default: 5m or config value)")
def dhan_live(ticker, strategies, all_strategies, once, live_mode, intraday, intraday_interval):
    """Run live trading via Dhan (SANDBOX by default).

    Requires a Dhan account and API credentials in .env:
        DHAN_CLIENT_ID=...
        DHAN_ACCESS_TOKEN=...

    Examples:
      python cli.py dhan-live --strategy macd --once
      python cli.py dhan-live --strategy bollinger_bands
      python cli.py dhan-live -t SBIN.NS -t ICICIBANK.NS -t BHARTIARTL.NS --once
      python cli.py dhan-live --live            # REAL money (use with caution!)
    """
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from engine.dhan_live_trader import DhanLiveTrader, DhanLiveTraderConfig
    from engine.risk_manager import RiskManager

    dhan_cfg = CONFIG.get("dhan_live_trading", {})
    api_cfg = CONFIG.get("api", {})
    risk_cfg = CONFIG["risk"]
    strat_cfg = CONFIG["strategies"]
    sizing_cfg = CONFIG.get("position_sizing", {})

    # Get credentials from config / env
    client_id = api_cfg.get("dhan_client_id", "")
    access_token = api_cfg.get("dhan_access_token", "")
    sandbox_client_id = api_cfg.get("dhan_sandbox_client_id", "")
    sandbox_access_token = api_cfg.get("dhan_sandbox_access_token", "")
    is_sandbox = not live_mode

    # Telegram notifications (optional)
    telegram_bot_token = api_cfg.get("telegram_bot_token", "")
    telegram_chat_id = api_cfg.get("telegram_chat_id", "")
    tg_api_id = api_cfg.get("tg_api_id", 0)
    tg_api_hash = api_cfg.get("tg_api_hash", "")

    # Validate credentials based on mode
    if is_sandbox:
        # Sandbox: use sandbox creds (preferred) or fall back to production creds
        if not sandbox_client_id and not sandbox_access_token:
            print("  [WARN] No DHAN_SANDBOX credentials set — falling back to production creds.")
            print("  [WARN] Production tokens may not work on sandbox URL (DH-906 Invalid Token).")
            print("  [WARN] Get sandbox keys at https://dhanhq.co -> DevPortal -> Sandbox tab.")
        # The trader's _init_client() handles `or` fallback internally

    if not is_sandbox and (not client_id or not access_token or str(client_id).startswith("${")):
        click.echo("ERROR: DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN must be set in .env file.")
        click.echo("  1. Open a Dhan account at https://dhan.co")
        click.echo("  2. Get API keys at https://developer.dhanhq.co")
        click.echo("  3. Add to .env: DHAN_CLIENT_ID=...")
        click.echo("                   DHAN_ACCESS_TOKEN=...")
        return

    # Resolve tickers
    if ticker:
        tickers = list(ticker)
    else:
        tickers = dhan_cfg.get("tickers", ["SBIN.NS", "ICICIBANK.NS", "BHARTIARTL.NS"])

    if not tickers:
        click.echo("ERROR: No tickers specified. Use --ticker or configure dhan_live_trading.tickers in config.yaml.")
        return

    # Build strategy list (support multiple -s flags or --all)
    requested = list(strategies) if strategies else []
    if all_strategies:
        requested = ["ma_crossover", "rsi_mean_revert", "macd", "bollinger_bands", "momentum_breakout"]
    if not requested:
        requested = ["macd"]

    strat_list: list = []
    for s in requested:
        if s == "ma_crossover":
            mc = strat_cfg["ma_crossover"]
            strat_list.append(MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"]))
        elif s == "rsi_mean_revert":
            rsi = strat_cfg["rsi_mean_revert"]
            strat_list.append(RSIMeanReversionStrategy(
                rsi_period=rsi["rsi_period"],
                oversold_threshold=rsi["oversold_threshold"],
                overbought_threshold=rsi["overbought_threshold"],
            ))
        elif s == "macd":
            mc = strat_cfg["macd"]
            strat_list.append(MACDStrategy(
                fast_period=mc["fast_period"], slow_period=mc["slow_period"],
                signal_period=mc["signal_period"],
            ))
        elif s == "bollinger_bands":
            bb = strat_cfg["bollinger_bands"]
            strat_list.append(BollingerBandsStrategy(period=bb["period"], num_std=bb["num_std"]))
        elif s == "momentum_breakout":
            mb = strat_cfg["momentum_breakout"]
            strat_list.append(MomentumBreakoutStrategy(lookback=mb["lookback"], exit_sma=mb["exit_sma"]))
        else:
            click.echo(f"Unknown strategy: {s}")
            return

    if not strat_list:
        click.echo("ERROR: No valid strategies specified.")
        return

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

    config = DhanLiveTraderConfig(
        client_id=client_id,
        access_token=access_token,
        sandbox=is_sandbox,
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
        intraday=intraday or dhan_cfg.get("intraday", False),
        intraday_interval=intraday_interval or dhan_cfg.get("intraday_interval", "5m"),
        max_hold_minutes=dhan_cfg.get("max_hold_minutes", 120),
        min_profit_threshold_pct=dhan_cfg.get("min_profit_threshold_pct", 0.005),
        min_volatility_pct=dhan_cfg.get("min_volatility_pct", 0.005),
        stop_buying_minutes=dhan_cfg.get("stop_buying_minutes", 900),
        force_square_off_minutes=dhan_cfg.get("force_square_off_minutes", 910),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        tg_api_id=tg_api_id,
        tg_api_hash=tg_api_hash,
    )

    mode = "LIVE" if live_mode else "SANDBOX"
    effective_intraday = intraday or dhan_cfg.get("intraday", False)
    effective_interval = intraday_interval or dhan_cfg.get("intraday_interval", "5m")
    interval_tag = f" [{effective_interval} intraday]" if effective_intraday else ""
    strategy_names = ', '.join(s.name for s in strat_list)
    click.echo(f"\nDhan Live Trader -- {mode} mode{interval_tag}")
    click.echo(f"  Tickers:    {', '.join(tickers)}")
    click.echo(f"  Strategies: {len(strat_list)} ({strategy_names})")
    click.echo()

    try:
        trader = DhanLiveTrader(config, strategies=strat_list, risk_manager=risk)
        trader.run(tickers, once=once)
    except ImportError as e:
        click.echo(f"\nERROR: {e}")
    except Exception as e:
        click.echo(f"\nERROR: {e}")


@cli.command()
@click.option("--ticker", "-t", multiple=True, help="Binance trading pair(s) (e.g., BTCUSDT)")
@click.option("--strategy", "-s", default="ma_crossover",
              type=click.Choice(["ma_crossover", "rsi_mean_revert", "macd",
                                 "bollinger_bands", "momentum_breakout"]),
              help="Strategy to use")
@click.option("--once", is_flag=True, help="Run one cycle then exit (default: loop)")
@click.option("--live", "live_mode", is_flag=True, help="Use Binance LIVE (default: testnet)")
def crypto_live(ticker, strategy, once, live_mode):
    """Run live crypto trading via Binance (TESTNET by default)."""
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy
    from engine.crypto_live_trader import BinanceLiveTrader, CryptoLiveTraderConfig

    crypto_cfg = CONFIG.get("crypto_live_trading", {})
    api_cfg = CONFIG["api"]

    binance_key = api_cfg.get("binance_api_key", "")
    binance_secret = api_cfg.get("binance_secret", "")

    if not binance_key or not binance_secret or binance_key.startswith("${"):
        click.echo("ERROR: BINANCE_API_KEY and BINANCE_SECRET must be set in .env file.")
        click.echo("  1. Get keys at https://www.binance.com/en/support/faq/how-to-create-api-keys-on-binance-360002502072")
        click.echo("  2. Add to .env: BINANCE_API_KEY=...")
        click.echo("                   BINANCE_SECRET=...")
        return

    tickers = list(ticker) if ticker else crypto_cfg.get("tickers", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    strat_cfg = CONFIG["strategies"]
    sizing_cfg = CONFIG.get("position_sizing", {})
    if strategy == "ma_crossover":
        mc = strat_cfg["ma_crossover"]
        strat = MACrossoverStrategy(fast_period=mc["fast_period"], slow_period=mc["slow_period"])
    elif strategy == "rsi_mean_revert":
        rsi = strat_cfg["rsi_mean_revert"]
        strat = RSIMeanReversionStrategy(
            rsi_period=rsi["rsi_period"],
            oversold_threshold=rsi["oversold_threshold"],
            overbought_threshold=rsi["overbought_threshold"],
        )
    elif strategy == "macd":
        mc = strat_cfg["macd"]
        strat = MACDStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"],
            signal_period=mc["signal_period"],
        )
    elif strategy == "bollinger_bands":
        bb = strat_cfg["bollinger_bands"]
        strat = BollingerBandsStrategy(period=bb["period"], num_std=bb["num_std"])
    elif strategy == "momentum_breakout":
        mb = strat_cfg["momentum_breakout"]
        strat = MomentumBreakoutStrategy(lookback=mb["lookback"], exit_sma=mb["exit_sma"])
    else:
        click.echo(f"Unknown strategy: {strategy}")
        return

    config = CryptoLiveTraderConfig(
        api_key=binance_key,
        api_secret=binance_secret,
        testnet=not live_mode,
        initial_capital=crypto_cfg.get("initial_capital", 10_000),
        max_positions=crypto_cfg.get("max_positions", 5),
        max_allocation_pct=crypto_cfg.get("max_allocation_pct", 0.20),
        max_daily_loss_pct=CONFIG["risk"]["max_daily_loss_pct"],
        stop_loss_pct=CONFIG["risk"]["stop_loss_pct"],
        take_profit_pct=CONFIG["risk"]["take_profit_pct"],
        poll_interval_seconds=crypto_cfg.get("poll_interval_seconds", 60),
        use_atr_sizing=sizing_cfg.get("use_atr_sizing", False),
        position_risk_pct=sizing_cfg.get("position_risk_pct", 0.01),
        atr_period=sizing_cfg.get("atr_period", 14),
        atr_multiplier=sizing_cfg.get("atr_multiplier", 2.0),
    )

    trader = BinanceLiveTrader(config, strat)
    trader.run(tickers, once=once)


if __name__ == "__main__":
    cli()
