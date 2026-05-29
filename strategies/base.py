"""
Abstract base class and signal enum for all trading strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto

import pandas as pd


class Signal(Enum):
    """Trading signal emitted by a strategy on each bar."""
    BUY = auto()
    SELL = auto()
    HOLD = auto()


@dataclass
class StrategyResult:
    """Output of a strategy evaluation for a single bar."""
    signal: Signal
    price: float
    reason: str = ""


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement `evaluate()` which receives the full OHLCV
    DataFrame and the current index position, and returns a Signal.

    Usage:
        class MyStrategy(BaseStrategy):
            def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
                ...
    """

    def __init__(self, name: str = "BaseStrategy"):
        self.name = name
        self._last_signal: Signal = Signal.HOLD

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        """
        Evaluate the strategy at a specific bar index.

        Args:
            df: Full OHLCV DataFrame (columns: open, high, low, close, volume)
            idx: Current bar index to evaluate

        Returns:
            StrategyResult with the trade signal for this bar
        """
        ...

    def prepare(self, df: pd.DataFrame) -> None:
        """
        Pre-compute any indicators once before running the strategy.
        Called by PaperTrader before the main bar-by-bar loop.
        Override in subclasses to cache expensive computations.
        """
        pass

    def clone(self) -> "BaseStrategy":
        """
        Return a fresh copy of this strategy with the same parameters.
        Override in subclasses to copy specific fields.
        """
        return type(self)(name=self.name)

    @property
    def last_signal(self) -> Signal:
        return self._last_signal

    @last_signal.setter
    def last_signal(self, value: Signal) -> None:
        self._last_signal = value

    def __repr__(self) -> str:
        return f"{self.name}()"
