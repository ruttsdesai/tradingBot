"""
Portfolio Rebalancer -- periodically realigns holdings to target weights.

Sells overweight positions and buys underweight ones when drift exceeds
a configurable threshold. Supports both paper (simulated) and live
(Alpaca) execution modes.

Usage:
    from engine.rebalancer import Rebalancer, RebalanceConfig

    config = RebalanceConfig(target_weights={"AAPL": 0.25, "MSFT": 0.25, "GOOGL": 0.25, "SPY": 0.25})
    rebal = Rebalancer(config, portfolio, paper_trader_or_live_trader)
    orders = rebal.compute_rebalance_orders(current_prices)
    rebal.execute_rebalance(orders)
"""

from dataclasses import dataclass, field
from typing import Optional

from .portfolio import Portfolio, Position


@dataclass
class RebalanceConfig:
    """Configuration for the portfolio rebalancer."""

    # Target allocations: {"AAPL": 0.25, "MSFT": 0.25, ...}
    target_weights: dict[str, float] = field(default_factory=dict)

    # Trigger rebalance when any position's actual weight drifts more than this
    drift_threshold_pct: float = 0.05  # 5% absolute drift triggers rebalance

    # Minimum trade size to avoid dust / tiny orders
    min_trade_value: float = 100.0  # don't trade if the order is < $100

    # Whether to actually place orders (False = dry-run / report only)
    execute: bool = True

    # Rebalance frequency: how often to check (in days, 0 = every run)
    check_interval_days: int = 7


@dataclass
class RebalanceOrder:
    """A single rebalance order (buy or sell)."""

    ticker: str
    action: str  # "BUY" or "SELL"
    current_weight: float
    target_weight: float
    current_value: float
    target_value: float
    delta_value: float   # positive = buy, negative = sell
    quantity: float


class Rebalancer:
    """
    Portfolio Rebalancer -- checks current allocations against target weights
    and generates buy/sell orders to realign.

    Supports pluggable execution: pass a PaperTrader for simulation or an
    AlpacaLiveTrader / BinanceLiveTrader for live execution.
    """

    def __init__(
        self,
        config: RebalanceConfig,
        portfolio: Portfolio,
        executor=None,  # PaperTrader | AlpacaLiveTrader | BinanceLiveTrader (duck-typed)
    ):
        self.config = config
        self.portfolio = portfolio
        self.executor = executor

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def needs_rebalance(self, current_prices: dict[str, float]) -> bool:
        """Return True if any position's drift exceeds the threshold."""
        if not self.config.target_weights:
            return False

        total_value = self._compute_total_value(current_prices)
        if total_value <= 0:
            return False

        for ticker, target in self.config.target_weights.items():
            current = self._current_weight(ticker, current_prices, total_value)
            drift = abs(current - target)
            if drift > self.config.drift_threshold_pct:
                return True
        return False

    # ------------------------------------------------------------------
    # Order computation
    # ------------------------------------------------------------------

    def compute_rebalance_orders(
        self,
        current_prices: dict[str, float],
    ) -> list[RebalanceOrder]:
        """
        Compute the buy/sell orders needed to realign to target weights.

        Returns a list of RebalanceOrder objects (sorted: sells first, then buys).
        """
        orders: list[RebalanceOrder] = []
        if not self.config.target_weights:
            return orders

        total_value = self._compute_total_value(current_prices)
        if total_value <= 0:
            return orders

        for ticker, target_weight in self.config.target_weights.items():
            price = current_prices.get(ticker, 0.0)
            if price <= 0:
                continue

            current_weight = self._current_weight(ticker, current_prices, total_value)
            target_value = total_value * target_weight
            current_value = self._position_value(ticker, current_prices)

            delta_value = target_value - current_value

            # Skip tiny adjustments
            if abs(delta_value) < self.config.min_trade_value:
                continue

            quantity = abs(delta_value) / price
            action = "BUY" if delta_value > 0 else "SELL"

            orders.append(RebalanceOrder(
                ticker=ticker,
                action=action,
                current_weight=current_weight,
                target_weight=target_weight,
                current_value=current_value,
                target_value=target_value,
                delta_value=delta_value,
                quantity=round(quantity, 6),
            ))

        # Sells first (to free up cash), then buys
        orders.sort(key=lambda o: 0 if o.action == "SELL" else 1)
        return orders

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_rebalance(self, orders: list[RebalanceOrder]) -> list[dict]:
        """
        Execute rebalance orders through the executor.

        Each order is submitted as a buy/sell via the executor's submit_buy /
        submit_sell methods (for live) or _handle_buy / _handle_sell (for paper).
        """
        results = []
        if not self.config.execute:
            for o in orders:
                results.append({"ticker": o.ticker, "action": o.action,
                                "quantity": o.quantity, "status": "DRY_RUN"})
            return results

        for order in orders:
            try:
                if self.executor is None:
                    results.append({"ticker": order.ticker, "action": order.action,
                                    "quantity": order.quantity, "status": "NO_EXECUTOR"})
                    continue

                if order.action == "BUY":
                    if hasattr(self.executor, "submit_buy"):
                        r = self.executor.submit_buy(order.ticker, order.quantity)
                        results.append({"ticker": order.ticker, "action": "BUY",
                                        "quantity": order.quantity, "status": "SUBMITTED",
                                        "order": r})
                    else:
                        results.append({"ticker": order.ticker, "action": "BUY",
                                        "quantity": order.quantity, "status": "SKIPPED"})
                else:  # SELL
                    if hasattr(self.executor, "submit_sell"):
                        r = self.executor.submit_sell(order.ticker, order.quantity)
                        results.append({"ticker": order.ticker, "action": "SELL",
                                        "quantity": order.quantity, "status": "SUBMITTED",
                                        "order": r})
                    else:
                        results.append({"ticker": order.ticker, "action": "SELL",
                                        "quantity": order.quantity, "status": "SKIPPED"})
            except Exception as e:
                results.append({"ticker": order.ticker, "action": order.action,
                                "quantity": order.quantity, "status": "ERROR",
                                "error": str(e)})
        return results

    def run(
        self,
        current_prices: dict[str, float],
        force: bool = False,
    ) -> tuple[list[RebalanceOrder], list[dict]]:
        """
        Full rebalance cycle: check drift, compute orders, execute.

        Returns (orders, execution_results).
        """
        if not force and not self.needs_rebalance(current_prices):
            return [], []

        orders = self.compute_rebalance_orders(current_prices)
        results = self.execute_rebalance(orders)
        return orders, results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_total_value(self, prices: dict[str, float]) -> float:
        """Total portfolio value = cash + sum of position mark-to-market values."""
        total = self.portfolio.current_cash
        for ticker, pos in self.portfolio.positions.items():
            price = prices.get(ticker, pos.avg_entry_price)
            total += pos.quantity * price
        return total

    def _position_value(self, ticker: str, prices: dict[str, float]) -> float:
        """Mark-to-market value of a single position."""
        pos = self.portfolio.positions.get(ticker)
        if pos is None:
            price = prices.get(ticker, 0)
            return 0  # not held; value is $0 (target might want us to buy in)
        price = prices.get(ticker, pos.avg_entry_price)
        return pos.quantity * price

    def _current_weight(self, ticker: str, prices: dict[str, float],
                        total_value: float) -> float:
        """Current allocation weight of a ticker in the portfolio."""
        if total_value <= 0:
            return 0.0
        return self._position_value(ticker, prices) / total_value
