"""
Walk-Forward Optimization Engine.

Splits historical data into rolling in-sample (training) and out-of-sample (testing)
windows, grid-searches strategy parameters on IS data, evaluates the best params on
OOS data, and aggregates the combined out-of-sample performance.

This is the gold standard for preventing overfitting — the OOS results are never
seen during optimization, giving a realistic estimate of live performance.

Usage:
    from data.stocks import fetch_stock_data
    from strategies.bollinger_bands import BollingerBandsStrategy
    from engine.walk_forward import WalkForwardOptimizer, WalkForwardConfig

    df = fetch_stock_data("AAPL", years=20)
    param_grid = {
        "period": [10, 20, 30],
        "num_std": [1.5, 2.0, 2.5],
    }
    config = WalkForwardConfig(train_years=10, test_years=2, step_years=2)
    optimizer = WalkForwardOptimizer(BollingerBandsStrategy, param_grid, config)
    summary = optimizer.run(df, initial_capital=100_000)
    print(summary.to_table())
"""

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Callable, Optional

import pandas as pd

from engine.paper_trader import PaperTrader, PaperTraderResult
from engine.risk_manager import RiskManager
from strategies.base import BaseStrategy


# ── Per-strategy parameter grids ────────────────────────────────────────────

STRATEGY_PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "ma_crossover": {
        "fast_period": [10, 15, 20, 25, 30],
        "slow_period": [40, 50, 60, 75, 100],
    },
    "rsi_mean_revert": {
        "rsi_period": [7, 10, 14, 21],
        "oversold_threshold": [20, 25, 30, 35],
        "overbought_threshold": [65, 70, 75, 80],
    },
    "macd": {
        "fast_period": [8, 12, 16],
        "slow_period": [21, 26, 34],
        "signal_period": [5, 7, 9, 12],
    },
    "bollinger_bands": {
        "period": [10, 15, 20, 25, 30],
        "num_std": [1.0, 1.5, 2.0, 2.5, 3.0],
    },
    "momentum_breakout": {
        "lookback": [10, 15, 20, 30, 40],
        "exit_sma": [5, 7, 10, 15, 20],
    },
}


# ── Helper: build strategy from name + params ───────────────────────────────

def _build_strategy(strategy_name: str, **params) -> BaseStrategy:
    """Instantiate a strategy by name with the given parameters."""
    if strategy_name == "ma_crossover":
        from strategies.ma_crossover import MACrossoverStrategy
        return MACrossoverStrategy(
            fast_period=params["fast_period"],
            slow_period=params["slow_period"],
        )
    elif strategy_name == "rsi_mean_revert":
        from strategies.rsi_mean_revert import RSIMeanReversionStrategy
        return RSIMeanReversionStrategy(
            rsi_period=params["rsi_period"],
            oversold_threshold=params["oversold_threshold"],
            overbought_threshold=params["overbought_threshold"],
        )
    elif strategy_name == "macd":
        from strategies.macd import MACDStrategy
        return MACDStrategy(
            fast_period=params["fast_period"],
            slow_period=params["slow_period"],
            signal_period=params["signal_period"],
        )
    elif strategy_name == "bollinger_bands":
        from strategies.bollinger_bands import BollingerBandsStrategy
        return BollingerBandsStrategy(
            period=params["period"],
            num_std=params["num_std"],
        )
    elif strategy_name == "momentum_breakout":
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        return MomentumBreakoutStrategy(
            lookback=params["lookback"],
            exit_sma=params["exit_sma"],
        )
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")


# ── Config / Window / Summary dataclasses ───────────────────────────────────

@dataclass
class WalkForwardConfig:
    """Configuration for walk-forward optimization."""
    train_years: int = 10          # In-sample training window length
    test_years: int = 2            # Out-of-sample testing window length
    step_years: int = 2            # How far to slide the window each iteration
    optimization_metric: str = "sharpe"  # Metric to optimize: "sharpe", "sortino", "return"
    min_trades: int = 5            # Minimum trades required for a valid IS result
    trading_days_per_year: int = 252


@dataclass
class WalkForwardWindow:
    """Results from a single walk-forward window."""
    window_num: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: dict[str, Any]
    is_return_pct: float          # In-sample return with best params
    is_sharpe: float               # In-sample Sharpe with best params
    is_trades: int                 # In-sample trade count
    oos_return_pct: float          # Out-of-sample return
    oos_sharpe: float              # Out-of-sample Sharpe
    oos_trades: int                # Out-of-sample trade count
    oos_win_rate: float            # Out-of-sample win rate
    oos_max_dd: float              # Out-of-sample max drawdown
    oos_sortino: float             # Out-of-sample Sortino


@dataclass
class WalkForwardSummary:
    """Aggregate results from all walk-forward windows."""
    strategy_name: str
    ticker: str
    total_years: float
    windows: list[WalkForwardWindow] = field(default_factory=list)

    @property
    def num_windows(self) -> int:
        return len(self.windows)

    @property
    def combined_oos_return_pct(self) -> float:
        """Geometric chained return across all OOS windows."""
        if not self.windows:
            return 0.0
        cumulative = 1.0
        for w in self.windows:
            cumulative *= (1.0 + w.oos_return_pct)
        return cumulative - 1.0

    @property
    def avg_oos_sharpe(self) -> float:
        if not self.windows:
            return 0.0
        return sum(w.oos_sharpe for w in self.windows) / len(self.windows)

    @property
    def avg_oos_sortino(self) -> float:
        from engine.paper_trader import avg_sortino_filtered
        if not self.windows:
            return 0.0
        return avg_sortino_filtered([w.oos_sortino for w in self.windows])

    @property
    def avg_oos_win_rate(self) -> float:
        if not self.windows:
            return 0.0
        # Weight by trade count
        total_wins = sum(w.oos_win_rate * w.oos_trades for w in self.windows)
        total_trades = sum(w.oos_trades for w in self.windows)
        return total_wins / total_trades if total_trades > 0 else 0.0

    @property
    def total_oos_trades(self) -> int:
        return sum(w.oos_trades for w in self.windows)

    @property
    def avg_oos_max_dd(self) -> float:
        if not self.windows:
            return 0.0
        return sum(w.oos_max_dd for w in self.windows) / len(self.windows)

    @property
    def most_common_params(self) -> dict[str, Any]:
        """Return the most frequently selected best parameters across windows."""
        if not self.windows:
            return {}
        from collections import Counter
        # Serialize params dicts to tuples for counting
        param_tuples = [tuple(sorted(w.best_params.items())) for w in self.windows]
        most_common_tuple = Counter(param_tuples).most_common(1)[0][0]
        return dict(most_common_tuple)

    @property
    def param_stability_pct(self) -> float:
        """Percentage of windows that chose the most common param set."""
        if not self.windows:
            return 0.0
        best = self.most_common_params
        matches = sum(
            1 for w in self.windows
            if all(w.best_params.get(k) == v for k, v in best.items())
        )
        return matches / len(self.windows)

    def to_table(self) -> str:
        """Return a formatted table of walk-forward results."""
        from tabulate import tabulate

        rows = []
        for w in self.windows:
            params_str = ", ".join(f"{k}={v}" for k, v in w.best_params.items())
            rows.append([
                w.window_num,
                f"{w.train_start} -> {w.train_end}",
                f"{w.test_start} -> {w.test_end}",
                params_str,
                f"{w.is_return_pct:+.2%}",
                f"{w.is_sharpe:.2f}",
                w.is_trades,
                f"{w.oos_return_pct:+.2%}",
                f"{w.oos_sharpe:.2f}",
                f"{w.oos_win_rate:.1%}",
                f"{w.oos_max_dd:+.2%}",
                w.oos_trades,
            ])

        headers = [
            "W#", "Train Period", "Test Period", "Best Params",
            "IS Ret", "IS Sharpe", "IS Tr",
            "OOS Ret", "OOS Sharpe", "OOS Win%", "OOS MaxDD", "OOS Tr",
        ]

        result = tabulate(rows, headers=headers, tablefmt="grid", stralign="right")
        result += "\n\n"

        if self.windows:
            mc = self.most_common_params
            params_str = ", ".join(f"{k}={v}" for k, v in mc.items())
        result += (
            f"Windows: {self.num_windows}  |  "
            f"Combined OOS Return: {self.combined_oos_return_pct:+.2%}  |  "
            f"Avg OOS Sharpe: {self.avg_oos_sharpe:.2f}  |  "
            f"Avg OOS Sortino: {self.avg_oos_sortino:.2f}\n"
            f"Total OOS Trades: {self.total_oos_trades}  |  "
            f"Avg Win Rate: {self.avg_oos_win_rate:.1%}  |  "
            f"Avg Max DD: {self.avg_oos_max_dd:+.2%}\n"
            f"Best Params: {params_str}  |  "
            f"Stability: {self.param_stability_pct:.0%} of windows chose these params"
        )

        return result

    def to_csv_rows(self) -> list[dict]:
        """Return one row per window, suitable for csv.DictWriter."""
        rows = []
        for w in self.windows:
            params_str = ", ".join(f"{k}={v}" for k, v in w.best_params.items())
            rows.append({
                "Window": w.window_num,
                "Train Start": w.train_start,
                "Train End": w.train_end,
                "Test Start": w.test_start,
                "Test End": w.test_end,
                "Best Params": params_str,
                "IS Return": round(w.is_return_pct, 4),
                "IS Sharpe": round(w.is_sharpe, 2),
                "IS Trades": w.is_trades,
                "OOS Return": round(w.oos_return_pct, 4),
                "OOS Sharpe": round(w.oos_sharpe, 2),
                "OOS Sortino": round(w.oos_sortino, 2),
                "OOS Win Rate": round(w.oos_win_rate, 4),
                "OOS Max DD": round(w.oos_max_dd, 4),
                "OOS Trades": w.oos_trades,
            })
        return rows


# ── Walk-Forward Optimizer ──────────────────────────────────────────────────

class WalkForwardOptimizer:
    """Walk-forward optimization of strategy parameters.

    Splits data into rolling in-sample / out-of-sample windows.
    For each window, grid-searches all param combinations on IS data,
    selects the best by the configured metric, and evaluates on OOS data.
    The combined OOS results give an unbiased estimate of live performance.

    Usage:
        df = fetch_stock_data("AAPL", years=20)
        param_grid = {"period": [10, 20, 30], "num_std": [1.5, 2.0, 2.5]}
        config = WalkForwardConfig(train_years=10, test_years=2, step_years=2)
        optimizer = WalkForwardOptimizer(BollingerBandsStrategy, param_grid, config)
        summary = optimizer.run(df)
        print(summary.to_table())
    """

    def __init__(
        self,
        strategy_name: str,
        param_grid: dict[str, list[Any]],
        config: WalkForwardConfig,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.001,
        risk_manager: RiskManager | None = None,
        verbose: bool = True,
    ):
        self.strategy_name = strategy_name
        self.param_grid = param_grid
        self.config = config
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.risk_manager = risk_manager or RiskManager()
        self.verbose = verbose

        # Pre-compute all parameter combinations
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        self._param_combos = [dict(zip(keys, combo)) for combo in product(*values)]

    def run(
        self,
        df: pd.DataFrame,
        ticker: str = "UNKNOWN",
    ) -> WalkForwardSummary:
        """Run walk-forward optimization across all windows.

        Args:
            df: OHLCV DataFrame with datetime index
            ticker: Ticker symbol for display

        Returns:
            WalkForwardSummary with per-window results and aggregated stats
        """
        if df.empty:
            raise ValueError("DataFrame is empty")

        td_per_year = pd.Timedelta(days=self.config.trading_days_per_year)
        data_start = df.index[0]
        data_end = df.index[-1]
        total_years = (data_end - data_start).days / 365.25

        if self.verbose:
            print(f"\n{'='*70}")
            print(f"  Walk-Forward Optimization: {self.strategy_name} on {ticker}")
            print(f"  Data: {data_start.date()} -> {data_end.date()} ({total_years:.1f}yr)")
            print(f"  Config: {self.config.train_years}yr train, {self.config.test_years}yr test, "
                  f"{self.config.step_years}yr step")
            print(f"  Param grid: {len(self._param_combos)} combinations")
            print(f"  Optimizing for: {self.config.optimization_metric}")
            print(f"{'='*70}\n")

        windows: list[WalkForwardWindow] = []
        window_num = 0
        current_start = data_start

        while True:
            window_num += 1
            train_start = current_start
            train_end = train_start + pd.Timedelta(days=self.config.train_years * 365)
            test_end = train_end + pd.Timedelta(days=self.config.test_years * 365)

            if test_end > data_end:
                # Not enough data for a full OOS window — stop
                break
            if train_end > data_end:
                # Not enough data for a full IS window — stop
                break

            # Slice data
            train_df = df[train_start:train_end].copy()
            test_df = df[train_end:test_end].copy()

            if len(train_df) < self.config.min_trades * 5 or len(test_df) < self.config.min_trades * 5:
                if self.verbose:
                    print(f"  Window {window_num}: {train_start.date()}->{test_end.date()} "
                          f"-- too few bars, skipping")
                current_start += pd.Timedelta(days=self.config.step_years * 365)
                continue

            # Grid search on in-sample data
            best_params, best_score, best_return, best_trades, best_sharpe = (
                self._grid_search(train_df, ticker)
            )

            if best_params is None:
                if self.verbose:
                    print(f"  Window {window_num}: {train_start.date()}->{test_end.date()} "
                          f"-- no valid IS results, skipping")
                current_start += pd.Timedelta(days=self.config.step_years * 365)
                continue

            # Evaluate best params on out-of-sample data
            oos_result = self._evaluate_params(best_params, test_df, ticker)

            window = WalkForwardWindow(
                window_num=window_num,
                train_start=str(train_start.date()),
                train_end=str(train_end.date()),
                test_start=str(train_end.date()),
                test_end=str(test_end.date()),
                best_params=best_params,
                is_return_pct=best_return,
                is_sharpe=best_sharpe,
                is_trades=best_trades,
                oos_return_pct=oos_result.portfolio.total_pnl_pct,
                oos_sharpe=oos_result.sharpe_ratio,
                oos_trades=oos_result.total_trades,
                oos_win_rate=oos_result.win_rate,
                oos_max_dd=oos_result.portfolio.max_drawdown,
                oos_sortino=oos_result.sortino_ratio,
            )
            windows.append(window)

            if self.verbose:
                params_str = ", ".join(f"{k}={v}" for k, v in best_params.items())
            print(
                f"  W{window_num}: [{train_start.date()}->{train_end.date()}] "
                f"IS: {best_return:+.2%} (Sharpe {best_sharpe:.2f})  |  "
                f"[{train_end.date()}->{test_end.date()}] "
                f"OOS: {oos_result.portfolio.total_pnl_pct:+.2%} "
                f"(Sharpe {oos_result.sharpe_ratio:.2f}, "
                f"WR {oos_result.win_rate:.1%})  |  "
                f"Params: {params_str}"
            )

            # Slide the window forward
            current_start += pd.Timedelta(days=self.config.step_years * 365)

        if self.verbose and not windows:
            print("  No valid windows found -- data may be too short for the configured windows.")
            print("  Try reducing train_years or test_years, or use a longer dataset.")

        return WalkForwardSummary(
            strategy_name=self.strategy_name,
            ticker=ticker,
            total_years=total_years,
            windows=windows,
        )

    def _grid_search(
        self, df: pd.DataFrame, ticker: str
    ) -> tuple[dict[str, Any] | None, float, float, int, float]:
        """Grid search all parameter combinations on in-sample data.

        Returns (best_params, best_score, best_return, best_trades, best_sharpe)
        or (None, 0, 0, 0, 0) if no valid results.
        """
        best_params = None
        best_score = -float("inf")
        best_return = 0.0
        best_sharpe = 0.0
        best_trades = 0

        metric = self.config.optimization_metric

        for params in self._param_combos:
            result = self._evaluate_params(params, df, ticker)

            # Skip parameter sets with too few trades
            if result.total_trades < self.config.min_trades:
                continue

            # Compute optimization score
            if metric == "sharpe":
                score = result.sharpe_ratio
            elif metric == "sortino":
                score = result.sortino_ratio
            elif metric == "return":
                score = result.portfolio.total_pnl_pct
            else:
                raise ValueError(f"Unknown optimization metric: {metric}")

            if score > best_score:
                best_score = score
                best_params = dict(params)
                best_return = result.portfolio.total_pnl_pct
                best_sharpe = result.sharpe_ratio
                best_trades = result.total_trades

        return best_params, best_score, best_return, best_trades, best_sharpe

    def _evaluate_params(
        self, params: dict[str, Any], df: pd.DataFrame, ticker: str
    ) -> PaperTraderResult:
        """Evaluate a single parameter set on a DataFrame slice."""
        strat = _build_strategy(self.strategy_name, **params)
        trader = PaperTrader(
            strategy=strat,
            risk_manager=self.risk_manager,
            initial_capital=self.initial_capital,
            commission_pct=self.commission_pct,
        )
        return trader.run(df, ticker=ticker)
