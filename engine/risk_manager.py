"""
Risk manager -- enforces position sizing, stop-losses, daily loss limits,
trailing stops, and correlation-based exposure limits.

Works alongside the paper trader and portfolio to prevent catastrophic losses.
"""

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .portfolio import Portfolio


class RiskAction(Enum):
    ALLOW = "ALLOW"
    BLOCK_POSITION_LIMIT = "BLOCK_POSITION_LIMIT"
    BLOCK_ALLOCATION = "BLOCK_ALLOCATION"
    BLOCK_CLUSTER = "BLOCK_CLUSTER"
    BLOCK_DAILY_LOSS = "BLOCK_DAILY_LOSS"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"


@dataclass
class RiskCheck:
    """Result of a risk check."""
    action: RiskAction
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == RiskAction.ALLOW


@dataclass
class RiskManager:
    """
    Enforces risk limits for paper/live trading.

    Parameters:
        max_positions: Maximum number of concurrent open positions
        max_allocation_pct: Max % of portfolio per position (0.20 = 20%)
        max_daily_loss_pct: Stop all trading if daily loss exceeds this (0.03 = 3%)
        stop_loss_pct: Close position if loss exceeds this (0.05 = 5%)
        take_profit_pct: Close position if gain exceeds this (0.10 = 10%)
        trailing_stop_enabled: Use trailing stop that locks in gains
        trailing_stop_pct: Trail stop at X% below highest price since entry
        trailing_stop_atr_mult: Alternative: trail at ATR * multiplier below peak
        correlation_threshold: Pearson r above which positions are clustered
        max_cluster_allocation_pct: Max total allocation to a correlated cluster
    """

    max_positions: int = 5
    max_allocation_pct: float = 0.20
    max_daily_loss_pct: float = 0.03
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10

    # Trailing stop
    trailing_stop_enabled: bool = False
    trailing_stop_pct: float = 0.08         # 8% trail (only used if atr_mult is 0)
    trailing_stop_atr_mult: float = 0.0      # If > 0, trails at ATR * mult below peak

    # Correlation limits
    correlation_threshold: float = 0.70      # Pearson r threshold for clustering
    max_cluster_allocation_pct: float = 0.40  # Max total allocation to any cluster
    correlation_enabled: bool = False        # Enable correlation checks

    _daily_start_value: float = 0.0
    _highest_since_entry: dict[str, float] = field(default_factory=dict)
    _entry_prices: dict[str, float] = field(default_factory=dict)
    _price_history: dict[str, list[float]] = field(default_factory=dict)

    def set_daily_start(self, portfolio_value: float) -> None:
        """Record portfolio value at start of trading day."""
        self._daily_start_value = portfolio_value

    def record_price(self, ticker: str, price: float) -> None:
        """Record a price point for correlation computation."""
        if ticker not in self._price_history:
            self._price_history[ticker] = []
        self._price_history[ticker].append(price)
        # Keep only last 60 bars (rolling correlation window)
        if len(self._price_history[ticker]) > 60:
            self._price_history[ticker] = self._price_history[ticker][-60:]

    def mark_entry(self, ticker: str, price: float) -> None:
        """Record entry price and initialize trailing stop tracking."""
        self._entry_prices[ticker] = price
        self._highest_since_entry[ticker] = price

    def clear_entry(self, ticker: str) -> None:
        """Clear trailing stop tracking when position is closed."""
        self._entry_prices.pop(ticker, None)
        self._highest_since_entry.pop(ticker, None)

    def update_trailing_stop(self, ticker: str, price: float) -> None:
        """Update highest price seen since entry for trailing stop."""
        if ticker in self._highest_since_entry:
            self._highest_since_entry[ticker] = max(
                self._highest_since_entry[ticker], price
            )

    def check_buy(
        self,
        portfolio: Portfolio,
        ticker: str,
        quantity: float,
        price: float,
    ) -> RiskCheck:
        """Check if a BUY is allowed under current risk constraints."""
        cost = quantity * price

        # 1. Max positions check
        if (
            ticker not in portfolio.positions
            and len(portfolio.positions) >= self.max_positions
        ):
            return RiskCheck(
                RiskAction.BLOCK_POSITION_LIMIT,
                f"Max {self.max_positions} positions reached",
            )

        # 2. Max allocation per position
        max_alloc = portfolio.total_value * self.max_allocation_pct
        current_pos = portfolio.positions.get(ticker)
        current_value = (current_pos.quantity * price) if current_pos else 0.0
        if (current_value + cost) > max_alloc:
            return RiskCheck(
                RiskAction.BLOCK_ALLOCATION,
                f"Position would exceed {self.max_allocation_pct:.0%} allocation "
                f"(${cost:,.0f} > ${max_alloc:,.0f} max)",
            )

        # 3. Daily loss limit
        if self._daily_start_value > 0:
            daily_loss_pct = (
                portfolio.total_value - self._daily_start_value
            ) / self._daily_start_value
            if daily_loss_pct <= -self.max_daily_loss_pct:
                return RiskCheck(
                    RiskAction.BLOCK_DAILY_LOSS,
                    f"Daily loss limit hit: {daily_loss_pct:+.2%}",
                )

        # 4. Correlation cluster limit
        if self.correlation_enabled and self._price_history:
            cluster_risk = self._check_cluster_exposure(portfolio, ticker, cost, price)
            if not cluster_risk.allowed:
                return cluster_risk

        return RiskCheck(RiskAction.ALLOW)

    def check_sell(
        self,
        portfolio: Portfolio,
        ticker: str,
        price: float,
        atr_value: float = 0.0,
    ) -> RiskCheck:
        """
        Check stop-loss, trailing stop, and take-profit for an open position.
        Returns STOP_LOSS / TRAILING_STOP / TAKE_PROFIT if trigger hit, ALLOW otherwise.
        """
        pos = portfolio.positions.get(ticker)
        if pos is None:
            return RiskCheck(RiskAction.ALLOW)

        pnl_pct = (price - pos.avg_entry_price) / pos.avg_entry_price

        # 1. Fixed stop-loss (always active)
        if pnl_pct <= -self.stop_loss_pct:
            return RiskCheck(
                RiskAction.STOP_LOSS,
                f"Stop-loss triggered at {pnl_pct:+.2%}",
            )

        # 2. Trailing stop (only when in profit)
        if self.trailing_stop_enabled and ticker in self._highest_since_entry:
            entry = self._entry_prices.get(ticker, pos.avg_entry_price)
            high = self._highest_since_entry[ticker]

            if self.trailing_stop_atr_mult > 0 and atr_value > 0:
                # ATR-based trailing stop
                trail_price = high - (atr_value * self.trailing_stop_atr_mult)
            else:
                # Percentage-based trailing stop
                trail_price = high * (1.0 - self.trailing_stop_pct)

            if price <= trail_price and high > entry:
                drop_pct = (price - high) / high
                return RiskCheck(
                    RiskAction.TRAILING_STOP,
                    f"Trailing stop: ${price:.2f} <= trail ${trail_price:.2f} "
                    f"(peak ${high:.2f}, {drop_pct:+.2%})",
                )

        # 3. Take-profit
        if pnl_pct >= self.take_profit_pct:
            return RiskCheck(
                RiskAction.TAKE_PROFIT,
                f"Take-profit triggered at {pnl_pct:+.2%}",
            )

        return RiskCheck(RiskAction.ALLOW)

    def _check_cluster_exposure(
        self,
        portfolio: Portfolio,
        new_ticker: str,
        new_cost: float,
        new_price: float,
    ) -> RiskCheck:
        """Check if adding this position would over-concentrate a correlated cluster."""
        if len(portfolio.positions) == 0:
            return RiskCheck(RiskAction.ALLOW)

        # Compute rolling correlations between new_ticker and existing positions
        new_prices = self._price_history.get(new_ticker, [])
        if len(new_prices) < 20:
            return RiskCheck(RiskAction.ALLOW)  # Not enough data

        new_returns = np.diff(new_prices[-60:]) / new_prices[-61:-1]

        cluster_value = new_cost
        cluster_max = portfolio.total_value * self.max_cluster_allocation_pct

        for held_ticker, pos in portfolio.positions.items():
            held_prices = self._price_history.get(held_ticker, [])
            if len(held_prices) < 20:
                cluster_value += pos.cost_basis
                continue

            held_returns = np.diff(held_prices[-60:]) / held_prices[-61:-1]

            # Compute Pearson correlation on overlapping returns
            min_len = min(len(new_returns), len(held_returns))
            if min_len < 10:
                continue

            corr = np.corrcoef(new_returns[:min_len], held_returns[:min_len])[0, 1]

            if abs(corr) >= self.correlation_threshold:
                cluster_value += pos.cost_basis

        if cluster_value > cluster_max:
            return RiskCheck(
                RiskAction.BLOCK_CLUSTER,
                f"Correlation cluster limit: ${cluster_value:,.0f} > ${cluster_max:,.0f} "
                f"({self.max_cluster_allocation_pct:.0%} max)",
            )

        return RiskCheck(RiskAction.ALLOW)

    def get_correlation_matrix(self) -> dict[str, dict[str, float]]:
        """Return the current correlation matrix for all tracked tickers."""
        tickers = list(self._price_history.keys())
        matrix = {}
        for t1 in tickers:
            matrix[t1] = {}
            p1 = np.diff(self._price_history[t1][-60:]) / np.array(self._price_history[t1][-61:-1])
            for t2 in tickers:
                if t1 == t2:
                    matrix[t1][t2] = 1.0
                    continue
                p2 = np.diff(self._price_history[t2][-60:]) / np.array(self._price_history[t2][-61:-1])
                min_len = min(len(p1), len(p2))
                if min_len < 10:
                    matrix[t1][t2] = 0.0
                else:
                    matrix[t1][t2] = float(np.corrcoef(p1[:min_len], p2[:min_len])[0, 1])
        return matrix
