"""
Monthly Trade Distribution — counts trades per month across all markets & strategies.

Re-runs the paper trader on the full backtest dataset (USA, India, Canada, Crypto)
and groups every BUY+SELL trade by YYYY-MM to show trading frequency over time.

Usage:  python monthly_trades.py
"""

import os
import sys
from collections import defaultdict
from datetime import datetime

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tabulate import tabulate


def load_config():
    import yaml
    from dotenv import load_dotenv
    import re

    load_dotenv(override=True)
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
    with open(config_path) as f:
        raw = f.read()

    def sub_env(match):
        var = match.group(1)
        return os.environ.get(var, "")

    raw = re.sub(r"\$\{([A-Z_]+)\}", sub_env, raw)
    return yaml.safe_load(raw)


def build_strategies(strat_cfg):
    """Build all 5 strategies from config."""
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.macd import MACDStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.momentum_breakout import MomentumBreakoutStrategy

    mc = strat_cfg["ma_crossover"]
    rsi = strat_cfg["rsi_mean_revert"]
    mb = strat_cfg["momentum_breakout"]
    bb = strat_cfg["bollinger_bands"]
    macd = strat_cfg["macd"]

    return [
        ("MA Crossover", MACrossoverStrategy(
            fast_period=mc["fast_period"], slow_period=mc["slow_period"])),
        ("RSI Mean Reversion", RSIMeanReversionStrategy(
            rsi_period=rsi["rsi_period"],
            oversold_threshold=rsi["oversold_threshold"],
            overbought_threshold=rsi["overbought_threshold"])),
        ("MACD", MACDStrategy(
            fast_period=macd["fast_period"], slow_period=macd["slow_period"],
            signal_period=macd["signal_period"])),
        ("Bollinger Bands", BollingerBandsStrategy(
            period=bb["period"], num_std=bb["num_std"])),
        ("Momentum Breakout", MomentumBreakoutStrategy(
            lookback=mb["lookback"], exit_sma=mb["exit_sma"])),
    ]


def run_market(label, tickers, years, capital, commission_pct, strategies, risk, data_fetcher):
    """Run paper trader on one market and return monthly trade counts.

    Returns: dict[market_name] -> dict[strategy_name] -> dict[YYYY-MM] -> count
    """
    from engine.paper_trader import PaperTrader

    print(f"\n  {'='*60}")
    print(f"  {label} — {years}yr, {len(tickers)} tickers, {len(strategies)} strategies")
    print(f"  {'='*60}")

    data = data_fetcher(tickers, years=years)
    data = {t: df for t, df in data.items() if not df.empty}
    if not data:
        print(f"  No data fetched — skipping.\n")
        return {}

    market_monthly: dict = {}  # strategy_name -> {YYYY-MM: count}

    for sname, strat in strategies:
        strat_monthly: dict = defaultdict(int)

        for ticker, df in data.items():
            s = strat.clone()
            trader = PaperTrader(
                s,
                risk_manager=risk,
                initial_capital=capital,
                commission_pct=commission_pct,
            )
            result = trader.run(df, ticker=ticker)

            # Count trades by month from trade history
            for trade in result.portfolio.trade_history:
                month_key = trade.timestamp.strftime("%Y-%m")
                strat_monthly[month_key] += 1

            print(f"    {sname:22s} | {ticker:12s} | "
                  f"{result.total_trades:4d} trades | "
                  f"Return: {result.portfolio.total_pnl_pct:+.2%} | "
                  f"Sharpe: {result.sharpe_ratio:.2f}")

        market_monthly[sname] = dict(strat_monthly)

    return {label: market_monthly}


def build_risk_manager(config):
    from engine.risk_manager import RiskManager
    risk_cfg = config["risk"]
    return RiskManager(
        max_positions=config["paper_trading"]["max_positions"],
        max_allocation_pct=config["paper_trading"]["max_allocation_pct"],
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


def print_monthly_tables(all_data: dict):
    """Print per-market, per-strategy monthly trade tables."""
    # all_data: {market_label: {strategy_name: {YYYY-MM: count}}}

    for market_label in sorted(all_data.keys()):
        strategies = all_data[market_label]

        # Collect all months across all strategies for this market
        all_months = set()
        for monthly in strategies.values():
            all_months.update(monthly.keys())
        sorted_months = sorted(all_months)

        if not sorted_months:
            print(f"\n  {market_label}: No trades")
            continue

        print(f"\n{'='*120}")
        print(f"  {market_label} — Trades Per Month (BUY + SELL legs; 1 round-trip = 2 trades)")
        print(f"{'='*120}")

        # Build table: rows = months, columns = strategies
        strategy_names = list(strategies.keys())
        headers = ["Month"] + strategy_names + ["Total"]
        rows = []
        grand_total = 0

        for month in sorted_months:
            row = [month]
            month_total = 0
            for sname in strategy_names:
                count = strategies[sname].get(month, 0)
                row.append(count)
                month_total += count
            row.append(month_total)
            grand_total += month_total
            rows.append(row)

        # Add totals row
        totals_row = ["TOTAL"]
        for sname in strategy_names:
            totals_row.append(sum(strategies[sname].get(m, 0) for m in sorted_months))
        totals_row.append(grand_total)
        rows.append(totals_row)

        print(tabulate(rows, headers=headers, tablefmt="grid", stralign="right"))

        # Per-strategy summary
        print(f"\n  Per-Strategy Totals for {market_label}:")
        summary_rows = []
        for sname in strategy_names:
            total = sum(strategies[sname].get(m, 0) for m in sorted_months)
            active_months = len([m for m in sorted_months if strategies[sname].get(m, 0) > 0])
            avg_per_month = total / len(sorted_months) if sorted_months else 0
            summary_rows.append([sname, total, active_months, f"{avg_per_month:.1f}"])
        print(tabulate(summary_rows,
                       headers=["Strategy", "Total Trades", "Active Months", "Avg/Month"],
                       tablefmt="grid", stralign="right"))

    # Cross-market summary
    print(f"\n{'='*120}")
    print(f"  Cross-Market Summary — Average Trades Per Month")
    print(f"{'='*120}\n")

    cross_rows = []
    for market_label in sorted(all_data.keys()):
        strategies = all_data[market_label]
        all_months = set()
        for monthly in strategies.values():
            all_months.update(monthly.keys())
        num_months = len(all_months)

        for sname in sorted(strategies.keys()):
            total = sum(strategies[sname].values())
            avg = total / num_months if num_months else 0
            cross_rows.append([market_label, sname, total, num_months, f"{avg:.1f}"])

    print(tabulate(cross_rows,
                   headers=["Market", "Strategy", "Total Trades", "Months", "Avg/Month"],
                   tablefmt="grid", stralign="right"))
    print()


def main():
    config = load_config()
    strat_cfg = config["strategies"]
    bg_cfg = config["backtest"]
    data_cfg = config["data"]
    risk = build_risk_manager(config)

    strategies = build_strategies(strat_cfg)
    print(f"\n{'='*120}")
    print(f"  MONTHLY TRADE DISTRIBUTION — All Markets × All Strategies")
    print(f"  Strategies: {', '.join(s[0] for s in strategies)}")
    print(f"{'='*120}")

    all_data: dict = {}

    # --- USA Stocks (20yr) ---
    from data.stocks import fetch_multiple_stocks
    usa_tickers = data_cfg["stocks"]["default_tickers"]
    usa_years = 20
    result = run_market(
        "USA Stocks", usa_tickers, usa_years,
        bg_cfg["stocks"]["initial_capital"],
        bg_cfg["stocks"]["commission_pct"],
        strategies, risk, fetch_multiple_stocks,
    )
    all_data.update(result)

    # --- India NSE ---
    india_tickers = data_cfg.get("india", {}).get("default_tickers", [])
    if india_tickers:
        india_years = data_cfg.get("india", {}).get("lookback_years", 20)
        result = run_market(
            "India NSE", india_tickers, india_years,
            bg_cfg["stocks"]["initial_capital"],
            bg_cfg["stocks"]["commission_pct"],
            strategies, risk, fetch_multiple_stocks,
        )
        all_data.update(result)

    # --- Canada TSX ---
    canada_tickers = data_cfg.get("canada", {}).get("default_tickers", [])
    if canada_tickers:
        canada_years = data_cfg.get("canada", {}).get("lookback_years", 20)
        result = run_market(
            "Canada TSX", canada_tickers, canada_years,
            bg_cfg["stocks"]["initial_capital"],
            bg_cfg["stocks"]["commission_pct"],
            strategies, risk, fetch_multiple_stocks,
        )
        all_data.update(result)

    # --- Crypto ---
    from data.crypto import fetch_multiple_crypto
    crypto_tickers = data_cfg["crypto"]["default_tickers"]
    crypto_years = bg_cfg["crypto"]["years"]
    result = run_market(
        "Crypto", crypto_tickers, crypto_years,
        bg_cfg["crypto"]["initial_capital"],
        bg_cfg["crypto"]["commission_pct"],
        strategies, risk, fetch_multiple_crypto,
    )
    all_data.update(result)

    # Print all tables
    print_monthly_tables(all_data)


if __name__ == "__main__":
    main()
