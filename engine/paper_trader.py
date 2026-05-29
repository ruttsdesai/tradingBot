"""
Paper Trading Engine -- simulates trade execution with fake money.

Walks through historical data candle-by-candle, evaluates strategies,
and executes simulated trades while tracking P&L in a Portfolio.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .portfolio import Portfolio, Side
from .risk_manager import RiskAction, RiskCheck, RiskManager
from strategies.base import BaseStrategy, Signal, StrategyResult

# Sentinel returned by _compute_sortino when there is no downside volatility.
# Must be > any realistic Sortino ratio so it ranks highest but is excluded from averages.
SORTINO_NO_DOWNSIDE = 999.0
SORTINO_SENTINEL_THRESHOLD = 900.0  # Values >= this are considered sentinels


def _compute_daily_returns(equity_curve: list[float]) -> np.ndarray:
    """Compute daily returns from an equity curve."""
    if len(equity_curve) < 2:
        return np.array([])
    arr = np.array(equity_curve)
    return (arr[1:] - arr[:-1]) / arr[:-1]


def _compute_sharpe(equity_curve: list[float], trading_days: int = 252) -> float:
    """Annualized Sharpe ratio (risk-free rate = 0)."""
    returns = _compute_daily_returns(equity_curve)
    if len(returns) < 2:
        return 0.0
    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1)
    if std_ret == 0:
        return 0.0
    return float(mean_ret / std_ret * math.sqrt(trading_days))


def _compute_sortino(equity_curve: list[float], trading_days: int = 252) -> float:
    """Annualized Sortino ratio (only penalizes downside vol).
    Returns a high sentinel (999) when there's no downside."""
    returns = _compute_daily_returns(equity_curve)
    if len(returns) < 2:
        return 0.0
    mean_ret = np.mean(returns)
    downside = returns[returns < 0]
    if len(downside) < 2:
        # No downside — return high sentinel (but cap to avoid infinity in rankings)
        return SORTINO_NO_DOWNSIDE if mean_ret > 0 else 0.0
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return 0.0
    return float(mean_ret / downside_std * math.sqrt(trading_days))


def avg_sortino_filtered(values: list[float]) -> float:
    """Average Sortino, filtering out the no-downside sentinel (999).

    Use this when computing strategy-level averages so a single all-upside
    run doesn't inflate the mean with a 999 sentinel.
    """
    filtered = [v for v in values if v < SORTINO_SENTINEL_THRESHOLD]
    return sum(filtered) / len(filtered) if filtered else 0.0


def _compute_max_drawdown_duration(equity_curve: list[float]) -> int:
    """Longest number of consecutive bars below the previous peak."""
    if len(equity_curve) < 2:
        return 0
    peak = equity_curve[0]
    max_duration = 0
    current_duration = 0
    for v in equity_curve[1:]:
        if v < peak:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            peak = v
            current_duration = 0
    return max_duration


def compute_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Compute Average True Range (ATR) for volatility-based position sizing.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR = Wilder-smoothed moving average of True Range.

    Returns a numpy array of same length as df; early bars are NaN until warmup.
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    tr = np.zeros(len(df))
    tr[0] = high[0] - low[0]
    for i in range(1, len(df)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    # Wilder smoothing: ATR_t = (ATR_{t-1} * (period-1) + TR_t) / period
    atr = np.full(len(df), np.nan)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(df)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


@dataclass
class PaperTraderResult:
    """Full result of a paper trading run."""
    portfolio: Portfolio
    ticker: str
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    start_date: datetime
    end_date: datetime
    strategy_name: str
    equity_curve: list[float] = field(default_factory=list)

    @property
    def sharpe_ratio(self) -> float:
        return _compute_sharpe(self.equity_curve)

    @property
    def sortino_ratio(self) -> float:
        return _compute_sortino(self.equity_curve)

    @property
    def max_drawdown_duration(self) -> int:
        return _compute_max_drawdown_duration(self.equity_curve)

    def summary(self) -> str:
        lines = [
            self.portfolio.summary(),
            "",
            f"=== Trade Stats ===",
            f"Ticker:      {self.ticker}",
            f"Strategy:    {self.strategy_name}",
            f"Period:      {self.start_date.date()} -> {self.end_date.date()}",
            f"Total Trades:{self.total_trades}",
            f"Wins:        {self.win_trades}",
            f"Losses:      {self.loss_trades}",
            f"Win Rate:    {self.win_rate:.1%}",
            f"Sharpe:      {self.sharpe_ratio:.2f}",
            f"Sortino:     {self.sortino_ratio:.2f}",
            f"Max DD Days: {self.max_drawdown_duration}",
        ]
        return "\n".join(lines)


class PaperTrader:
    """
    Simulated trading engine.

    Loops through OHLCV data bar-by-bar, calls strategy.evaluate() at each bar,
    and simulates buy/sell execution.

    Usage:
        df = fetch_stock_data("AAPL", years=10)
        strategy = MACrossoverStrategy(fast_period=20, slow_period=50)
        trader = PaperTrader(strategy, initial_capital=100000)
        result = trader.run(df, ticker="AAPL")
        print(result.summary())
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: Optional[RiskManager] = None,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.001,
        use_atr_sizing: bool = False,
        position_risk_pct: float = 0.01,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
    ):
        self.strategy = strategy
        self.risk_manager = risk_manager or RiskManager()
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.use_atr_sizing = use_atr_sizing
        self.position_risk_pct = position_risk_pct
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self._atr: np.ndarray | None = None

    def run(
        self,
        df: pd.DataFrame,
        ticker: str = "UNKNOWN",
    ) -> PaperTraderResult:
        """
        Run the strategy against OHLCV data, simulating trades.

        Args:
            df: DataFrame with columns: open, high, low, close, volume
            ticker: Symbol being traded

        Returns:
            PaperTraderResult with portfolio state and trade statistics
        """
        portfolio = Portfolio(initial_capital=self.initial_capital, current_cash=self.initial_capital)
        self.risk_manager.set_daily_start(portfolio.total_value)

        # Give strategy a chance to pre-compute expensive indicators once
        self.strategy.prepare(df)

        # Pre-compute ATR for volatility-based position sizing
        if self.use_atr_sizing:
            self._atr = compute_atr(df, self.atr_period)

        # Track daily equity curve for Sharpe/Sortino/DD-duration
        equity_curve: list[float] = []
        prev_date: Optional[datetime] = None
        last_recorded_date: Optional[datetime] = None

        # Walk through each candle
        for idx in range(len(df)):
            current_date = df.index[idx]
            price = df["close"].iloc[idx]
            portfolio.update_price(ticker, price)

            # Reset daily loss tracker at start of each new trading day
            if prev_date is None or current_date.date() != prev_date.date():
                self.risk_manager.set_daily_start(portfolio.total_value)
                # Record portfolio value once per day (end-of-day snapshot)
                if last_recorded_date is not None:
                    equity_curve.append(portfolio.total_value)
                last_recorded_date = current_date
            prev_date = current_date

            # Feed price to risk manager for correlation tracking
            self.risk_manager.record_price(ticker, price)

            # Update trailing stop level (highest price since entry)
            self.risk_manager.update_trailing_stop(ticker, price)

            # Evaluate strategy at this bar
            result: StrategyResult = self.strategy.evaluate(df, idx)

            # Track whether we already exited this ticker on this bar
            already_sold = False

            if result.signal == Signal.BUY:
                self._handle_buy(portfolio, ticker, price, current_date, result.reason, idx=idx)
            elif result.signal == Signal.SELL:
                self._handle_sell(portfolio, ticker, price, current_date, result.reason)
                already_sold = True

            # Check stop-loss / take-profit / trailing-stop only if we haven't already sold
            if not already_sold:
                atr_val = float(self._atr[idx]) if self._atr is not None and 0 <= idx < len(self._atr) and not np.isnan(self._atr[idx]) else 0.0
                risk = self.risk_manager.check_sell(portfolio, ticker, price, atr_value=atr_val)
                if risk.action in (RiskAction.STOP_LOSS, RiskAction.TRAILING_STOP, RiskAction.TAKE_PROFIT):
                    self._handle_sell(portfolio, ticker, price, current_date, risk.reason)

            portfolio.update_drawdown()

        # Prepend initial capital so the curve captures the first day's return
        if equity_curve:
            equity_curve.insert(0, self.initial_capital)

        # Close any remaining positions at final price
        final_price = df["close"].iloc[-1]
        final_date = df.index[-1]
        for t in list(portfolio.positions.keys()):
            self._handle_sell(portfolio, t, final_price, final_date, "Close at end")

        # Record the final day's portfolio value
        equity_curve.append(portfolio.total_value)

        # Compute trade stats
        return self._build_result(portfolio, ticker, df, equity_curve)

    def _handle_buy(
        self,
        portfolio: Portfolio,
        ticker: str,
        price: float,
        date: datetime,
        reason: str,
        idx: int = -1,
    ) -> None:
        """Attempt to execute a simulated buy."""
        max_alloc = portfolio.total_value * self.risk_manager.max_allocation_pct

        # If we already hold this ticker, buy up to allocation limit
        pos = portfolio.positions.get(ticker)
        current_value = (pos.cost_basis) if pos else 0.0
        remaining = max_alloc - current_value
        if remaining <= 0:
            return

        # ATR-based position sizing: size by risk, not by fixed allocation
        if self.use_atr_sizing and self._atr is not None and 0 <= idx < len(self._atr):
            atr_val = self._atr[idx]
            if not np.isnan(atr_val) and atr_val > 0:
                risk_amount = portfolio.total_value * self.position_risk_pct
                stop_distance = atr_val * self.atr_multiplier
                atr_quantity = risk_amount / stop_distance if stop_distance > 0 else 0
                # Cap by max allocation as safety net
                max_quantity = max_alloc / price
                quantity = min(atr_quantity, max_quantity)
            else:
                quantity = remaining / price
        else:
            quantity = remaining / price

        cost = quantity * price + (quantity * price * self.commission_pct)

        if cost > portfolio.current_cash:
            # Adjust quantity to fit cash
            quantity = portfolio.current_cash / (price * (1 + self.commission_pct))
            if quantity <= 0:
                return

        # Risk check
        risk: RiskCheck = self.risk_manager.check_buy(portfolio, ticker, quantity, price)
        if not risk.allowed:
            return

        trade = portfolio.buy(ticker, quantity, price, date, reason)
        if trade:
            # Update strategy state for stop-loss tracking
            if hasattr(self.strategy, "set_entry_price"):
                self.strategy.set_entry_price(price)
            # Initialize trailing stop tracking
            self.risk_manager.mark_entry(ticker, price)

    def _handle_sell(
        self,
        portfolio: Portfolio,
        ticker: str,
        price: float,
        date: datetime,
        reason: str,
    ) -> None:
        """Attempt to execute a simulated sell."""
        pos = portfolio.positions.get(ticker)
        if pos is None or pos.quantity <= 0:
            return

        trade = portfolio.sell(ticker, pos.quantity, price, date, reason)
        if trade:
            if hasattr(self.strategy, "clear_entry"):
                self.strategy.clear_entry()
            # Clear trailing stop tracking
            self.risk_manager.clear_entry(ticker)

    def _build_result(
        self,
        portfolio: Portfolio,
        ticker: str,
        df: pd.DataFrame,
        equity_curve: list[float] | None = None,
    ) -> PaperTraderResult:
        """Compute trade statistics and build the result object."""
        trades = portfolio.trade_history
        total_trades = len(trades)

        # Count wins/losses by pairing buys -> sells
        buys: list[dict] = []
        completed_trades: list[tuple[float, float]] = []  # (entry, exit)

        for t in trades:
            if t.side == Side.BUY:
                buys.append({"qty": t.quantity, "price": t.price})
            elif t.side == Side.SELL and buys:
                # FIFO matching
                sell_qty = t.quantity
                while buys and sell_qty > 0:
                    b = buys[0]
                    if b["qty"] <= sell_qty:
                        completed_trades.append((b["price"], t.price))
                        sell_qty -= b["qty"]
                        buys.pop(0)
                    else:
                        completed_trades.append((b["price"], t.price))
                        b["qty"] -= sell_qty
                        sell_qty = 0

        wins = sum(1 for entry, exit in completed_trades if exit > entry)
        losses = len(completed_trades) - wins
        win_rate = wins / len(completed_trades) if completed_trades else 0.0

        return PaperTraderResult(
            portfolio=portfolio,
            ticker=ticker,
            total_trades=total_trades,
            win_trades=wins,
            loss_trades=losses,
            win_rate=win_rate,
            start_date=df.index[0],
            end_date=df.index[-1],
            strategy_name=self.strategy.name,
            equity_curve=equity_curve or [],
        )
