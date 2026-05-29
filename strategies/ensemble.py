"""
Ensemble Voting Strategy — trades only when multiple strategies agree.

Wraps all 5 base strategies and on each bar evaluates all of them.
A BUY signal is emitted only when `min_votes` or more strategies agree.
A SELL is emitted when any strategy says sell OR when fewer than
`min_votes` strategies agree to stay in (exit on loss of consensus).

This filters out false signals and improves win rate at the cost of
fewer total trades.
"""

import pandas as pd

from .base import BaseStrategy, Signal, StrategyResult
from .ma_crossover import MACrossoverStrategy
from .rsi_mean_revert import RSIMeanReversionStrategy
from .macd import MACDStrategy
from .bollinger_bands import BollingerBandsStrategy
from .momentum_breakout import MomentumBreakoutStrategy


class EnsembleStrategy(BaseStrategy):
    """
    Meta-strategy that votes across multiple sub-strategies.

    Parameters:
        min_buy_votes: Minimum strategies that must say BUY for ensemble to buy (default 3)
        strategies: List of (name, strategy) tuples. If empty, builds defaults.
    """

    def __init__(
        self,
        min_buy_votes: int = 2,
        strategies: list[BaseStrategy] | None = None,
        name: str = "Ensemble",
    ):
        super().__init__(name=name)
        self.min_buy_votes = min_buy_votes

        if strategies is not None:
            self._strategies = strategies
        else:
            self._strategies = self._build_defaults()

        self._in_position: bool = False

    def _build_defaults(self) -> list[BaseStrategy]:
        """Build the 5 default strategies with standard parameters."""
        return [
            MACrossoverStrategy(fast_period=20, slow_period=50),
            RSIMeanReversionStrategy(rsi_period=14, oversold_threshold=30, overbought_threshold=70),
            MACDStrategy(fast_period=12, slow_period=26, signal_period=9),
            BollingerBandsStrategy(period=20, num_std=2.0),
            MomentumBreakoutStrategy(lookback=20, exit_sma=10),
        ]

    @property
    def strategy_names(self) -> list[str]:
        return [s.name for s in self._strategies]

    @property
    def num_strategies(self) -> int:
        return len(self._strategies)

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute indicators for all sub-strategies."""
        for s in self._strategies:
            s.prepare(df)

    def clone(self) -> "EnsembleStrategy":
        """Return a fresh copy with cloned sub-strategies."""
        cloned_strats = [s.clone() for s in self._strategies]
        clone = EnsembleStrategy(
            min_buy_votes=self.min_buy_votes,
            strategies=cloned_strats,
            name=self.name,
        )
        clone._in_position = self._in_position
        return clone

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        """Vote across all sub-strategies and emit consensus signal."""
        price = df["close"].iloc[idx]

        # Collect votes from all sub-strategies
        buy_votes = 0
        sell_votes = 0
        buy_reasons: list[str] = []
        sell_reasons: list[str] = []

        for s in self._strategies:
            result = s.evaluate(df, idx)
            if result.signal == Signal.BUY:
                buy_votes += 1
                if result.reason:
                    buy_reasons.append(f"{s.name}:{result.reason}")
            elif result.signal == Signal.SELL:
                sell_votes += 1
                if result.reason:
                    sell_reasons.append(f"{s.name}:{result.reason}")

        # Buy: min_buy_votes strategies agree
        if buy_votes >= self.min_buy_votes and not self._in_position:
            self._in_position = True
            details = " | ".join(buy_reasons[:3])
            return StrategyResult(
                Signal.BUY, price,
                f"ENSEMBLE BUY [{buy_votes}/{self.num_strategies}]: {details}",
            )

        # Sell: any strategy says sell OR consensus lost while in position
        if sell_votes >= 1 and self._in_position:
            self._in_position = False
            details = " | ".join(sell_reasons[:3]) if sell_reasons else "Exit"
            return StrategyResult(
                Signal.SELL, price,
                f"ENSEMBLE SELL [{sell_votes}/{self.num_strategies}]: {details}",
            )

        # If we're in a position but buy votes dropped below threshold (consensus lost),
        # exit to preserve gains
        if self._in_position and buy_votes < self.min_buy_votes:
            self._in_position = False
            return StrategyResult(
                Signal.SELL, price,
                f"ENSEMBLE EXIT: consensus lost [{buy_votes}/{self.num_strategies} < {self.min_buy_votes}]",
            )

        return StrategyResult(
            Signal.HOLD, price,
            f"Votes: BUY={buy_votes} SELL={sell_votes}/{self.num_strategies}",
        )

    def set_entry_price(self, price: float) -> None:
        """Propagate entry price to sub-strategies that track it."""
        for s in self._strategies:
            if hasattr(s, "set_entry_price"):
                s.set_entry_price(price)

    def clear_entry(self) -> None:
        """Propagate clear to sub-strategies."""
        for s in self._strategies:
            if hasattr(s, "clear_entry"):
                s.clear_entry()
