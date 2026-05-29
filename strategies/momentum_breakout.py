"""
Momentum Breakout Strategy.

Buy when price breaks above the highest high of the last N bars.
Sell when price drops below a trailing SMA (momentum faded).
"""

import pandas as pd
from .base import BaseStrategy, Signal, StrategyResult


class MomentumBreakoutStrategy(BaseStrategy):
    """
    Momentum breakout strategy -- buys breakouts, sells when trend weakens.

    Parameters:
        lookback: Bars to look back for breakout level (default 20)
        exit_sma: SMA period for exit signal (default 10)
    """

    def __init__(
        self,
        lookback: int = 20,
        exit_sma: int = 10,
        name: str = "MomentumBreakout",
    ):
        super().__init__(name=name)
        self.lookback = lookback
        self.exit_sma = exit_sma
        self._rolling_high: pd.Series | None = None
        self._sma: pd.Series | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute rolling highs and exit SMA once."""
        self._rolling_high = df["high"].rolling(
            window=self.lookback, min_periods=self.lookback
        ).max()
        self._sma = df["close"].rolling(
            window=self.exit_sma, min_periods=self.exit_sma
        ).mean()

    def clone(self) -> "MomentumBreakoutStrategy":
        """Return a fresh copy with the same parameters."""
        clone = MomentumBreakoutStrategy(
            lookback=self.lookback,
            exit_sma=self.exit_sma,
            name=self.name,
        )
        if self._rolling_high is not None:
            clone._rolling_high = self._rolling_high
            clone._sma = self._sma
        return clone

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        close = df["close"]
        price = close.iloc[idx]

        if idx < self.lookback:
            return StrategyResult(Signal.HOLD, price, "Warmup")

        prev_high = self._rolling_high.iloc[idx - 1]
        curr_sma = self._sma.iloc[idx]

        # Breakout: price crosses above previous N-bar high
        if price > prev_high:
            return StrategyResult(
                Signal.BUY,
                price,
                f"Breakout: close=${price:.2f} > {self.lookback}d high=${prev_high:.2f}",
            )

        # Exit: price drops below SMA (momentum faded)
        if price < curr_sma:
            return StrategyResult(
                Signal.SELL,
                price,
                f"Exit: close=${price:.2f} < SMA{self.exit_sma}=${curr_sma:.2f}",
            )

        return StrategyResult(Signal.HOLD, price, "")
