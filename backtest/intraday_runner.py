"""
Intraday (day-trading) backtester — simulates the live Dhan intraday rules
on 5-minute (or other intraday) bars.

Unlike the daily-bar PaperTrader, this engine enforces the constraints the
live intraday trader operates under:

  - No new BUYs after `stop_buying_minutes` (default 15:00 IST)
  - Forced square-off of all positions at `force_square_off_minutes`
    (default 15:10 IST) — no overnight holds, ever
  - Volatility filter: skip BUYs when ATR/close < `min_volatility_pct`
  - Time-exit: close positions held >= `max_hold_minutes` with profit
    below `min_profit_threshold_pct`

Data note: yfinance serves at most ~60 calendar days of 5m bars, so intraday
backtests cover weeks, not years. Treat results as a smoke test of the
day-trading configuration, not a 20-year validation.
"""

from typing import Optional

import numpy as np
import pandas as pd

from engine.paper_trader import PaperTrader, PaperTraderResult, compute_atr
from engine.portfolio import Portfolio
from engine.risk_manager import RiskAction, RiskManager
from strategies.base import BaseStrategy, Signal, StrategyResult


class IntradayBacktester(PaperTrader):
    """Bar-by-bar simulator with NSE day-trading rules.

    Expects a DataFrame of intraday bars whose index carries the exchange
    local time (yfinance returns Asia/Kolkata timestamps for .NS tickers).
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: Optional[RiskManager] = None,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.001,
        stop_buying_minutes: int = 900,        # 15:00 IST
        force_square_off_minutes: int = 910,   # 15:10 IST
        max_hold_minutes: int = 120,
        min_profit_threshold_pct: float = 0.005,
        min_volatility_pct: float = 0.005,
        **kwargs,
    ):
        super().__init__(
            strategy,
            risk_manager=risk_manager,
            initial_capital=initial_capital,
            commission_pct=commission_pct,
            **kwargs,
        )
        self.stop_buying_minutes = stop_buying_minutes
        self.force_square_off_minutes = force_square_off_minutes
        self.max_hold_minutes = max_hold_minutes
        self.min_profit_threshold_pct = min_profit_threshold_pct
        self.min_volatility_pct = min_volatility_pct

    def run(self, df: pd.DataFrame, ticker: str = "UNKNOWN") -> PaperTraderResult:
        portfolio = Portfolio(
            initial_capital=self.initial_capital,
            current_cash=self.initial_capital,
        )
        self.risk_manager.set_daily_start(portfolio.total_value)

        self.strategy.prepare(df)
        atr = compute_atr(df, self.atr_period)
        self._atr = atr  # used by _handle_buy for ATR sizing

        equity_curve: list[float] = []
        prev_ts = None

        for idx in range(len(df)):
            ts = df.index[idx]
            price = float(df["close"].iloc[idx])

            # New trading day: record EOD equity, reset daily loss tracker,
            # and guard against overnight holds if the previous day's data
            # ended before the square-off bar.
            if prev_ts is not None and ts.date() != prev_ts.date():
                if ticker in portfolio.positions:
                    prev_close = float(df["close"].iloc[idx - 1])
                    self._handle_sell(portfolio, ticker, prev_close, prev_ts,
                                      "EOD square-off (missing 15:10 bar)")
                equity_curve.append(portfolio.total_value)
                self.risk_manager.set_daily_start(portfolio.total_value)
            prev_ts = ts

            portfolio.update_price(ticker, price)
            self.risk_manager.record_price(ticker, price)
            self.risk_manager.update_trailing_stop(ticker, price)

            minutes = ts.hour * 60 + ts.minute

            # Forced square-off — close everything, no trading past this point
            if minutes >= self.force_square_off_minutes:
                if ticker in portfolio.positions:
                    self._handle_sell(portfolio, ticker, price, ts, "15:10 square-off")
                portfolio.update_drawdown()
                continue

            atr_val = (
                float(atr[idx])
                if 0 <= idx < len(atr) and not np.isnan(atr[idx])
                else 0.0
            )

            result: StrategyResult = self.strategy.evaluate(df, idx)
            already_sold = False

            if result.signal == Signal.BUY:
                if minutes >= self.stop_buying_minutes:
                    pass  # too late in the day to open a position
                elif atr_val > 0 and price > 0 and atr_val / price < self.min_volatility_pct:
                    pass  # dead market — not worth the commissions
                else:
                    self._handle_buy(portfolio, ticker, price, ts, result.reason, idx=idx)
            elif result.signal == Signal.SELL:
                self._handle_sell(portfolio, ticker, price, ts, result.reason)
                already_sold = True

            # Stop-loss / take-profit / trailing-stop
            if not already_sold and ticker in portfolio.positions:
                risk = self.risk_manager.check_sell(portfolio, ticker, price, atr_value=atr_val)
                if risk.action in (RiskAction.STOP_LOSS, RiskAction.TRAILING_STOP, RiskAction.TAKE_PROFIT):
                    self._handle_sell(portfolio, ticker, price, ts, risk.reason)
                    already_sold = True

            # Time-exit stale positions with negligible profit
            if not already_sold and ticker in portfolio.positions:
                pos = portfolio.positions[ticker]
                held_minutes = (ts - pos.entry_date).total_seconds() / 60.0
                pnl_pct = (
                    (price - pos.avg_entry_price) / pos.avg_entry_price
                    if pos.avg_entry_price > 0 else 0.0
                )
                if held_minutes >= self.max_hold_minutes and pnl_pct < self.min_profit_threshold_pct:
                    self._handle_sell(portfolio, ticker, price, ts,
                                      f"Time-exit after {held_minutes:.0f}m at {pnl_pct:+.2%}")

            portfolio.update_drawdown()

        # Close anything still open at the final bar (shouldn't happen if the
        # data includes the square-off bar, but never leave a phantom hold)
        if ticker in portfolio.positions:
            self._handle_sell(portfolio, ticker, float(df["close"].iloc[-1]),
                              df.index[-1], "Close at end")

        if equity_curve:
            equity_curve.insert(0, self.initial_capital)
        equity_curve.append(portfolio.total_value)

        return self._build_result(portfolio, ticker, df, equity_curve)
