"""
RSI Mean Reversion Strategy.

Buy when RSI drops below oversold threshold (asset is "cheap").
Sell when RSI rises above overbought threshold (asset is "expensive").
Also sells if a stop-loss is hit on an existing position.
"""

import pandas as pd
from .base import BaseStrategy, Signal, StrategyResult


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute Relative Strength Index (RSI) using Wilder's smoothing.

    RSI = 100 - (100 / (1 + RS))
    where RS = avg_gain / avg_loss over `period` bars.

    Uses the standard Wilder smoothing formula:
        avg_gain = (prev_avg_gain * (period - 1) + current_gain) / period
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Initial SMA for the first `period` bars
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # Wilder's recursive smoothing for subsequent bars
    avg_gain = avg_gain.copy()
    avg_loss = avg_loss.copy()
    for i in range(period, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


class RSIMeanReversionStrategy(BaseStrategy):
    """
    RSI-based mean reversion strategy.

    Parameters:
        rsi_period: RSI lookback window (default 14)
        oversold_threshold: RSI level that triggers a BUY (default 30)
        overbought_threshold: RSI level that triggers a SELL (default 70)
        stop_loss_pct: Stop-loss as decimal (0.05 = 5% below entry)
    """

    def __init__(
        self,
        rsi_period: int = 14,
        oversold_threshold: float = 30.0,
        overbought_threshold: float = 70.0,
        stop_loss_pct: float = 0.05,
        name: str = "RSI_MeanReversion",
    ):
        super().__init__(name=name)
        self.rsi_period = rsi_period
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
        self.stop_loss_pct = stop_loss_pct
        self._entry_price: float | None = None
        self._rsi_series: pd.Series | None = None

    def clone(self) -> "RSIMeanReversionStrategy":
        """Return a fresh copy with the same RSI parameters."""
        clone = RSIMeanReversionStrategy(
            rsi_period=self.rsi_period,
            oversold_threshold=self.oversold_threshold,
            overbought_threshold=self.overbought_threshold,
            stop_loss_pct=self.stop_loss_pct,
            name=self.name,
        )
        # Carry over pre-computed RSI if available (avoids recompute after clone)
        if hasattr(self, "_rsi_series"):
            clone._rsi_series = self._rsi_series
        return clone

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute RSI once on the full dataset so evaluate() is O(1)."""
        self._rsi_series = compute_rsi(df["close"], self.rsi_period)

    def set_entry_price(self, price: float) -> None:
        """Record entry price for stop-loss tracking."""
        self._entry_price = price

    def clear_entry(self) -> None:
        """Clear entry price when position is exited."""
        self._entry_price = None

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        close = df["close"]
        price = close.iloc[idx]

        if idx < self.rsi_period:
            return StrategyResult(Signal.HOLD, price, "Warmup")

        # Look up pre-computed RSI value (O(1)) or fall back to on-the-fly compute
        if self._rsi_series is not None:
            rsi_value = self._rsi_series.iloc[idx]
        else:
            rsi_series = compute_rsi(close.iloc[: idx + 1], self.rsi_period)
            rsi_value = rsi_series.iloc[-1]

        # Stop-loss check (only if we're in a position)
        if self._entry_price is not None:
            loss_pct = (price - self._entry_price) / self._entry_price
            if loss_pct <= -self.stop_loss_pct:
                self.clear_entry()
                return StrategyResult(
                    Signal.SELL,
                    price,
                    f"Stop-loss: RSI={rsi_value:.1f} | Loss={loss_pct:.2%}",
                )

        # RSI oversold -> buy signal
        if rsi_value < self.oversold_threshold:
            self._entry_price = price
            return StrategyResult(
                Signal.BUY,
                price,
                f"Oversold: RSI={rsi_value:.1f}",
            )

        # RSI overbought -> sell signal
        if rsi_value > self.overbought_threshold:
            self.clear_entry()
            return StrategyResult(
                Signal.SELL,
                price,
                f"Overbought: RSI={rsi_value:.1f}",
            )

        return StrategyResult(Signal.HOLD, price, f"RSI={rsi_value:.1f}")
