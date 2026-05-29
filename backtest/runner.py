"""
Backtest Runner -- tests strategies against historical data.

Runs a strategy across multiple tickers and years of data,
then prints a consolidated performance report with key metrics.
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from engine.paper_trader import (
    PaperTrader, PaperTraderResult, avg_sortino_filtered
)
from engine.risk_manager import RiskManager
from strategies.base import BaseStrategy


@dataclass
class BacktestSummary:
    """Aggregate performance across all tickers."""
    ticker_results: list[PaperTraderResult] = field(default_factory=list)
    total_trades: int = 0
    avg_win_rate: float = 0.0
    avg_return_pct: float = 0.0
    avg_sharpe: float = 0.0
    avg_sortino: float = 0.0
    best_return_pct: float = 0.0
    worst_return_pct: float = 0.0
    best_ticker: str = ""
    worst_ticker: str = ""
    strategy_name: str = ""
    market_label: str = ""

    def to_csv_rows(self) -> list[dict]:
        """Return a list of dicts suitable for csv.DictWriter.

        One row per ticker with all strategy-level metadata repeated.
        """
        rows = []
        for r in self.ticker_results:
            rows.append({
                "Market": self.market_label,
                "Strategy": self.strategy_name,
                "Ticker": r.ticker,
                "Trades": r.total_trades,
                "Win Rate": round(r.win_rate, 4),
                "Return": round(r.portfolio.total_pnl_pct, 4),
                "P&L": round(r.portfolio.total_pnl, 2),
                "Max DD": round(r.portfolio.max_drawdown, 4),
                "Wins": r.win_trades,
                "Losses": r.loss_trades,
                "Sharpe": round(r.sharpe_ratio, 2),
                "Sortino": round(r.sortino_ratio, 2),
                "DD Days": r.max_drawdown_duration,
                "Start Date": str(r.start_date.date()),
                "End Date": str(r.end_date.date()),
            })
        return rows

    def to_table(self) -> str:
        """Return a formatted table of results."""
        from tabulate import tabulate

        rows = []
        for r in self.ticker_results:
            rows.append([
                r.ticker,
                r.total_trades,
                f"{r.win_rate:.1%}",
                f"{r.portfolio.total_pnl_pct:+.2%}",
                f"${r.portfolio.total_pnl:+,.0f}",
                f"{r.portfolio.max_drawdown:+.2%}",
                r.win_trades,
                r.loss_trades,
                f"{r.sharpe_ratio:.2f}",
                f"{r.sortino_ratio:.2f}",
                str(r.max_drawdown_duration),
            ])

        headers = [
            "Ticker", "Trades", "Win Rate", "Return", "P&L",
            "Max DD", "Wins", "Losses", "Sharpe", "Sortino", "DD Days"
        ]

        result = tabulate(rows, headers=headers, tablefmt="grid", stralign="right")

        # Add summary footer
        result += f"\n\n"
        result += f"Total Trades: {self.total_trades}  |  "
        result += f"Avg Win Rate: {self.avg_win_rate:.1%}  |  "
        result += f"Avg Return: {self.avg_return_pct:+.2%}  |  "
        result += f"Avg Sharpe: {self.avg_sharpe:.2f}\n"
        result += f"Avg Sortino: {self.avg_sortino:.2f}  |  "
        result += f"Best: {self.best_ticker} ({self.best_return_pct:+.2%})  |  "
        result += f"Worst: {self.worst_ticker} ({self.worst_return_pct:+.2%})"

        return result


class BacktestRunner:
    """
    Runs a strategy across multiple tickers and years of historical data.

    Usage:
        strategy = MACrossoverStrategy(fast_period=20, slow_period=50)
        runner = BacktestRunner(strategy, initial_capital=100000)
        summary = runner.run(data_dict, years=10)
        print(summary.to_table())
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.001,
        risk_manager: Optional[RiskManager] = None,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.risk_manager = risk_manager

    def run(
        self,
        data: dict[str, pd.DataFrame],
        market_label: str = "",
    ) -> BacktestSummary:
        """Run backtest across all tickers and return aggregated summary."""
        summary = BacktestSummary(
            strategy_name=self.strategy.name,
            market_label=market_label,
        )

        for ticker, df in data.items():
            if df.empty:
                print(f"  {ticker}: Skipped (no data)")
                continue

            # Clone strategy so each ticker gets a fresh instance (no state bleed)
            strat = self.strategy.clone()

            trader = PaperTrader(
                strategy=strat,
                risk_manager=self.risk_manager or RiskManager(),
                initial_capital=self.initial_capital,
                commission_pct=self.commission_pct,
            )

            result = trader.run(df, ticker=ticker)
            summary.ticker_results.append(result)
            print(f"  {ticker}: {result.total_trades} trades | "
                  f"Win Rate: {result.win_rate:.1%} | "
                  f"Return: {result.portfolio.total_pnl_pct:+.2%} | "
                  f"P&L: ${result.portfolio.total_pnl:+,.0f}")

        self._compute_aggregate(summary)
        return summary

    def _compute_aggregate(self, summary: BacktestSummary) -> None:
        """Compute aggregate statistics from ticker results."""
        if not summary.ticker_results:
            return

        summary.total_trades = sum(r.total_trades for r in summary.ticker_results)
        summary.avg_win_rate = (
            sum(r.win_rate for r in summary.ticker_results) / len(summary.ticker_results)
            if summary.ticker_results else 0.0
        )
        summary.avg_sharpe = (
            sum(r.sharpe_ratio for r in summary.ticker_results) / len(summary.ticker_results)
            if summary.ticker_results else 0.0
        )
        # Filter Sortino sentinel (999) from averages so one all-upside ticker
        # doesn't inflate the mean
        sortino_vals = [r.sortino_ratio for r in summary.ticker_results]
        summary.avg_sortino = avg_sortino_filtered(sortino_vals)
        returns = [r.portfolio.total_pnl_pct for r in summary.ticker_results]
        summary.avg_return_pct = sum(returns) / len(returns) if returns else 0.0
        summary.best_return_pct = max(returns) if returns else 0.0
        summary.worst_return_pct = min(returns) if returns else 0.0

        best_idx = returns.index(summary.best_return_pct) if returns else -1
        worst_idx = returns.index(summary.worst_return_pct) if returns else -1
        summary.best_ticker = summary.ticker_results[best_idx].ticker if best_idx >= 0 else "-"
        summary.worst_ticker = summary.ticker_results[worst_idx].ticker if worst_idx >= 0 else "-"
