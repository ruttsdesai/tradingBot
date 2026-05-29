"""
MACD (Moving Average Convergence Divergence) Strategy.

Buy when the MACD line crosses ABOVE the signal line (bullish).
Sell when the MACD line crosses BELOW the signal line (bearish).

MACD Line = fast_ema - slow_ema
Signal Line = EMA of MACD Line
"""

import pandas as pd
from .base import BaseStrategy, Signal, StrategyResult


class MACDStrategy(BaseStrategy):
    """
    Classic MACD crossover strategy.

    Parameters:
        fast_period: Fast EMA period (default 12)
        slow_period: Slow EMA period (default 26)
        signal_period: Signal line EMA period (default 9)
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        name: str = "MACD",
    ):
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

        if slow_period <= fast_period:
            raise ValueError(
                f"slow_period ({slow_period}) must be > fast_period ({fast_period})"
            )

        self._macd_line: pd.Series | None = None
        self._signal_line: pd.Series | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute MACD and signal lines once on the full dataset."""
        close = df["close"]
        fast_ema = close.ewm(span=self.fast_period, adjust=False).mean()
        slow_ema = close.ewm(span=self.slow_period, adjust=False).mean()
        self._macd_line = fast_ema - slow_ema
        self._signal_line = self._macd_line.ewm(
            span=self.signal_period, adjust=False
        ).mean()

    def clone(self) -> "MACDStrategy":
        """Return a fresh copy with the same MACD parameters."""
        clone = MACDStrategy(
            fast_period=self.fast_period,
            slow_period=self.slow_period,
            signal_period=self.signal_period,
            name=self.name,
        )
        if self._macd_line is not None:
            clone._macd_line = self._macd_line
            clone._signal_line = self._signal_line
        return clone

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        price = df["close"].iloc[idx]

        # Need enough bars for slow EMA + signal EMA
        min_bars = self.slow_period + self.signal_period
        if idx < min_bars:
            return StrategyResult(Signal.HOLD, price, "Warmup")

        macd_now = self._macd_line.iloc[idx]
        sig_now = self._signal_line.iloc[idx]
        macd_prev = self._macd_line.iloc[idx - 1]
        sig_prev = self._signal_line.iloc[idx - 1]

        # Bullish crossover: MACD crosses ABOVE signal
        if macd_prev <= sig_prev and macd_now > sig_now:
            return StrategyResult(
                Signal.BUY,
                price,
                f"MACD cross UP: MACD={macd_now:.4f} > Signal={sig_now:.4f}",
            )

        # Bearish crossover: MACD crosses BELOW signal
        if macd_prev >= sig_prev and macd_now < sig_now:
            return StrategyResult(
                Signal.SELL,
                price,
                f"MACD cross DOWN: MACD={macd_now:.4f} < Signal={sig_now:.4f}",
            )

        return StrategyResult(Signal.HOLD, price, "")
