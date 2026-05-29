"""
Moving Average Crossover Strategy.

Buy when fast MA crosses ABOVE slow MA (golden cross).
Sell when fast MA crosses BELOW slow MA (death cross).
"""

import pandas as pd
from .base import BaseStrategy, Signal, StrategyResult


class MACrossoverStrategy(BaseStrategy):
    """
    Classic dual moving average crossover.

    Parameters:
        fast_period: Short-term MA window (default 20)
        slow_period: Long-term MA window (default 50)
    """

    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        name: str = "MA_Crossover",
    ):
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period

        if slow_period <= fast_period:
            raise ValueError(
                f"slow_period ({slow_period}) must be > fast_period ({fast_period})"
            )

    def clone(self) -> "MACrossoverStrategy":
        """Return a fresh copy with the same MA parameters."""
        return MACrossoverStrategy(
            fast_period=self.fast_period,
            slow_period=self.slow_period,
            name=self.name,
        )

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        close = df["close"]

        # Need enough bars to compute both MAs
        if idx < self.slow_period:
            return StrategyResult(Signal.HOLD, close.iloc[idx], "Warmup")

        price = close.iloc[idx]

        # Current bar MAs
        fast_now = close.iloc[idx - self.fast_period + 1 : idx + 1].mean()
        slow_now = close.iloc[idx - self.slow_period + 1 : idx + 1].mean()

        # Previous bar MAs (for crossover detection)
        fast_prev = close.iloc[idx - self.fast_period : idx].mean()
        slow_prev = close.iloc[idx - self.slow_period : idx].mean()

        # Golden cross: fast crosses ABOVE slow
        if fast_prev <= slow_prev and fast_now > slow_now:
            return StrategyResult(
                Signal.BUY,
                price,
                f"Golden cross: {self.fast_period}MA > {self.slow_period}MA",
            )

        # Death cross: fast crosses BELOW slow
        if fast_prev >= slow_prev and fast_now < slow_now:
            return StrategyResult(
                Signal.SELL,
                price,
                f"Death cross: {self.fast_period}MA < {self.slow_period}MA",
            )

        return StrategyResult(Signal.HOLD, price, "")
