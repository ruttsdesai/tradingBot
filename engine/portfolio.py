"""
Portfolio tracker for paper trading and backtesting.

Tracks cash balance, open positions, trade history, and computes P&L metrics.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Trade:
    """A single executed trade."""
    timestamp: datetime
    ticker: str
    side: Side
    quantity: float
    price: float
    value: float = field(init=False)
    reason: str = ""

    def __post_init__(self):
        self.value = round(self.quantity * self.price, 2)


@dataclass
class Position:
    """An open position in a single asset."""
    ticker: str
    quantity: float
    avg_entry_price: float
    entry_date: datetime

    @property
    def cost_basis(self) -> float:
        return round(self.quantity * self.avg_entry_price, 2)

    def unrealized_pnl(self, current_price: float) -> float:
        return round(self.quantity * (current_price - self.avg_entry_price), 2)

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.avg_entry_price == 0:
            return 0.0
        return (current_price - self.avg_entry_price) / self.avg_entry_price


@dataclass
class Portfolio:
    """
    Tracks the state of a paper/live trading portfolio.

    - Cash balance
    - Open positions (dict of ticker -> Position)
    - Trade history (list of Trades)
    - Daily P&L tracking
    """

    initial_capital: float
    current_cash: float

    positions: dict[str, Position] = field(default_factory=dict)
    trade_history: list[Trade] = field(default_factory=list)
    daily_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_value: float = 0.0
    current_prices: dict[str, float] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        """Total portfolio value = cash + value of all open positions."""
        pos_value = sum(
            p.quantity * self.current_prices.get(t, p.avg_entry_price)
            for t, p in self.positions.items()
        )
        return round(self.current_cash + pos_value, 2)

    @property
    def total_pnl(self) -> float:
        """Total P&L since inception."""
        return round(self.total_value - self.initial_capital, 2)

    @property
    def total_pnl_pct(self) -> float:
        """Total return percentage since inception."""
        if self.initial_capital == 0:
            return 0.0
        return self.total_pnl / self.initial_capital

    def update_price(self, ticker: str, price: float) -> None:
        """Update the current market price for a ticker."""
        self.current_prices[ticker] = price

    def update_drawdown(self) -> None:
        """Update peak value and max drawdown."""
        current = self.total_value
        if current > self.peak_value:
            self.peak_value = current
        if self.peak_value > 0:
            dd = (current - self.peak_value) / self.peak_value
            self.max_drawdown = min(self.max_drawdown, dd)

    def can_buy(self, quantity: float, price: float) -> bool:
        """Check if we have enough cash to buy."""
        return self.current_cash >= (quantity * price)

    def buy(
        self,
        ticker: str,
        quantity: float,
        price: float,
        timestamp: datetime,
        reason: str = "",
    ) -> Trade | None:
        """
        Execute a buy. Returns the Trade or None if insufficient cash.
        """
        cost = round(quantity * price, 2)
        if cost > self.current_cash:
            return None

        trade = Trade(timestamp, ticker, Side.BUY, quantity, price, reason=reason)
        self.trade_history.append(trade)
        self.current_cash = round(self.current_cash - cost, 2)

        # Update or create position
        if ticker in self.positions:
            pos = self.positions[ticker]
            total_qty = pos.quantity + quantity
            total_cost = pos.cost_basis + cost
            pos.quantity = total_qty
            pos.avg_entry_price = round(total_cost / total_qty, 4) if total_qty > 0 else 0
        else:
            self.positions[ticker] = Position(
                ticker=ticker,
                quantity=quantity,
                avg_entry_price=price,
                entry_date=timestamp,
            )

        self.update_price(ticker, price)
        return trade

    def sell(
        self,
        ticker: str,
        quantity: float,
        price: float,
        timestamp: datetime,
        reason: str = "",
    ) -> Trade | None:
        """
        Execute a sell. Returns the Trade or None if we don't hold enough.
        """
        if ticker not in self.positions:
            return None

        pos = self.positions[ticker]
        if quantity > pos.quantity:
            return None

        trade = Trade(timestamp, ticker, Side.SELL, quantity, price, reason=reason)
        self.trade_history.append(trade)
        self.current_cash = round(self.current_cash + (quantity * price), 2)

        # Reduce or remove position
        pos.quantity -= quantity
        if pos.quantity <= 0:
            del self.positions[ticker]
            self.current_prices.pop(ticker, None)

        return trade

    def summary(self) -> str:
        """Return a human-readable portfolio summary."""
        lines = [
            f"=== Portfolio Summary ===",
            f"Cash:        ${self.current_cash:,.2f}",
            f"Positions:   {len(self.positions)} open",
            f"Total Value: ${self.total_value:,.2f}",
            f"Total P&L:   ${self.total_pnl:,.2f} ({self.total_pnl_pct:+.2%})",
            f"Max Drawdown:{self.max_drawdown:+.2%}",
        ]

        if self.positions:
            lines.append("")
            lines.append(f"{'Ticker':<10} {'Qty':>8} {'Entry':>10} {'Price':>10} {'P&L':>12}")
            lines.append("-" * 52)
            for t, p in self.positions.items():
                cp = self.current_prices.get(t, p.avg_entry_price)
                pnl = p.unrealized_pnl(cp)
                pnl_pct = p.unrealized_pnl_pct(cp)
                lines.append(
                    f"{t:<10} {p.quantity:>8.4f} ${p.avg_entry_price:>9.2f} "
                    f"${cp:>9.2f} ${pnl:>+10.2f} ({pnl_pct:+.2%})"
                )

        return "\n".join(lines)
