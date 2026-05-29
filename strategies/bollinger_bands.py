"""
Bollinger Bands Mean Reversion Strategy.

Buy when price touches or crosses below the lower band (oversold).
Sell when price touches or crosses above the upper band (overbought).
Bands are computed as SMA +/- (num_std * standard deviation).
"""

import pandas as pd
from .base import BaseStrategy, Signal, StrategyResult


class BollingerBandsStrategy(BaseStrategy):
    """
    Bollinger Bands mean reversion strategy.

    Parameters:
        period: SMA lookback window (default 20)
        num_std: Number of standard deviations for bands (default 2.0)
    """

    def __init__(
        self,
        period: int = 20,
        num_std: float = 2.0,
        name: str = "BollingerBands",
    ):
        super().__init__(name=name)
        self.period = period
        self.num_std = num_std
        self._sma: pd.Series | None = None
        self._upper: pd.Series | None = None
        self._lower: pd.Series | None = None

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute Bollinger Bands once on the full dataset."""
        close = df["close"]
        self._sma = close.rolling(window=self.period, min_periods=self.period).mean()
        std = close.rolling(window=self.period, min_periods=self.period).std()
        self._upper = self._sma + self.num_std * std
        self._lower = self._sma - self.num_std * std

    def clone(self) -> "BollingerBandsStrategy":
        """Return a fresh copy with the same BB parameters."""
        clone = BollingerBandsStrategy(
            period=self.period,
            num_std=self.num_std,
            name=self.name,
        )
        if self._sma is not None:
            clone._sma = self._sma
            clone._upper = self._upper
            clone._lower = self._lower
        return clone

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        close = df["close"]
        price = close.iloc[idx]

        if idx < self.period:
            return StrategyResult(Signal.HOLD, price, "Warmup")

        lower = self._lower.iloc[idx]
        upper = self._upper.iloc[idx]
        sma = self._sma.iloc[idx]

        # Price at or below lower band -> oversold, buy
        if price <= lower:
            return StrategyResult(
                Signal.BUY,
                price,
                f"BB buy: close=${price:.2f} <= lower=${lower:.2f} (SMA=${sma:.2f})",
            )

        # Price at or above upper band -> overbought, sell
        if price >= upper:
            return StrategyResult(
                Signal.SELL,
                price,
                f"BB sell: close=${price:.2f} >= upper=${upper:.2f} (SMA=${sma:.2f})",
            )

        return StrategyResult(Signal.HOLD, price, "")
