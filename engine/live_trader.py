"""
Live Trading Engine -- executes real trades via Alpaca Markets.

Mirrors the PaperTrader interface but submits actual orders to a brokerage.
Requires alpaca-py:  pip install alpaca-py
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .portfolio import Portfolio, Position, Side
from .risk_manager import RiskAction, RiskCheck, RiskManager
from .paper_trader import compute_atr
from strategies.base import BaseStrategy, Signal, StrategyResult


@dataclass
class LiveTraderConfig:
    """Configuration for the live trading engine."""

    api_key: str
    api_secret: str
    paper: bool = True  # Use Alpaca paper trading by default
    initial_capital: float = 100_000.0
    max_positions: int = 5
    max_allocation_pct: float = 0.20
    max_daily_loss_pct: float = 0.03
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    poll_interval_seconds: int = 60  # How often to re-evaluate strategies
    use_atr_sizing: bool = False
    position_risk_pct: float = 0.01
    atr_period: int = 14
    atr_multiplier: float = 2.0


class AlpacaLiveTrader:
    """
    Live trading engine backed by Alpaca Markets.

    Fetches account state, evaluates strategies against recent price data,
    and submits real (or paper) orders.

    Usage:
        config = LiveTraderConfig(
            api_key="PK...", api_secret="...", paper=True,
        )
        strategy = MACrossoverStrategy(fast_period=20, slow_period=50)
        trader = AlpacaLiveTrader(config, strategy)
        trader.run(tickers=["AAPL", "MSFT"], once=True)
    """

    def __init__(
        self,
        config: LiveTraderConfig,
        strategy: BaseStrategy,
        risk_manager: Optional[RiskManager] = None,
    ):
        self.config = config
        self.strategy = strategy
        self.risk_manager = risk_manager or RiskManager(
            max_positions=config.max_positions,
            max_allocation_pct=config.max_allocation_pct,
            max_daily_loss_pct=config.max_daily_loss_pct,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
        )
        self._trading_client = None
        self._data_client = None
        self._portfolio: Optional[Portfolio] = None

    def _init_clients(self) -> None:
        """Lazily initialize Alpaca clients (so import errors surface only when used)."""
        if self._trading_client is not None:
            return

        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
        except ImportError:
            raise ImportError(
                "alpaca-py is required for live trading. "
                "Install it with: pip install alpaca-py"
            )

        self._trading_client = TradingClient(
            self.config.api_key,
            self.config.api_secret,
            paper=self.config.paper,
        )
        self._data_client = StockHistoricalDataClient(
            self.config.api_key,
            self.config.api_secret,
        )

    # ------------------------------------------------------------------
    # Account & Portfolio
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict:
        """Return key account fields (equity, buying_power, cash, etc.)."""
        self._init_clients()
        account = self._trading_client.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "long_market_value": float(account.long_market_value),
            "portfolio_value": float(account.portfolio_value),
            "status": account.status,
        }

    def get_positions(self) -> list[dict]:
        """Return all current open positions."""
        self._init_clients()
        positions = self._trading_client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def sync_portfolio(self) -> Portfolio:
        """Build a local Portfolio snapshot from the live Alpaca account."""
        self._init_clients()
        account = self._trading_client.get_account()
        cash = float(account.cash)
        equity = float(account.equity)

        portfolio = Portfolio(
            initial_capital=self.config.initial_capital,
            current_cash=cash,
        )
        portfolio.peak_value = max(equity, portfolio.peak_value)

        positions = self._trading_client.get_all_positions()
        for p in positions:
            ticker = p.symbol
            qty = float(p.qty)
            entry = float(p.avg_entry_price)
            price = float(p.current_price)

            portfolio.update_price(ticker, price)
            portfolio.positions[ticker] = Position(
                ticker=ticker,
                quantity=qty,
                avg_entry_price=entry,
                entry_date=datetime.now(),
            )

        return portfolio

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_latest_price(self, ticker: str) -> float:
        """Fetch the latest ask price for a ticker."""
        self._init_clients()
        from alpaca.data.requests import StockLatestQuoteRequest

        quote = self._data_client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=ticker)
        )
        return float(quote[ticker].ask_price)

    def get_historical_bars(
        self,
        tickers: list[str],
        days: int = 60,
        timeframe: str = "1Day",
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily bars for one or more tickers. Returns {ticker: DataFrame}."""
        self._init_clients()
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        tf_map = {"1Day": TimeFrame.Day, "1Hour": TimeFrame.Hour, "1Min": TimeFrame.Minute}
        tf = tf_map.get(timeframe, TimeFrame.Day)

        end = datetime.now()
        start = end - timedelta(days=days)

        request = StockBarsRequest(
            symbol_or_symbols=tickers,
            timeframe=tf,
            start=start,
            end=end,
            limit=1000,
        )
        bars = self._data_client.get_stock_bars(request)

        result: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            if ticker in bars:
                df = bars[ticker].df
                if not df.empty:
                    # Flatten multi-level columns if present
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.droplevel(1)
                    result[ticker] = df
            if ticker not in result:
                result[ticker] = pd.DataFrame()
        return result

    # ------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------

    def submit_buy(self, ticker: str, quantity: float) -> dict | None:
        """Submit a market buy order. Returns order dict or None on failure."""
        self._init_clients()
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        if quantity <= 0:
            return None

        try:
            order = MarketOrderRequest(
                symbol=ticker,
                qty=round(quantity, 6),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            result = self._trading_client.submit_order(order)
            return {
                "id": str(result.id),
                "symbol": result.symbol,
                "qty": float(result.qty),
                "side": "BUY",
                "status": result.status,
                "filled_avg_price": (
                    float(result.filled_avg_price) if result.filled_avg_price else None
                ),
            }
        except Exception as e:
            print(f"  [LIVE] Buy order failed for {ticker}: {e}")
            return None

    def submit_sell(self, ticker: str, quantity: float) -> dict | None:
        """Submit a market sell order. Returns order dict or None on failure."""
        self._init_clients()
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        if quantity <= 0:
            return None

        try:
            order = MarketOrderRequest(
                symbol=ticker,
                qty=round(quantity, 6),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            result = self._trading_client.submit_order(order)
            return {
                "id": str(result.id),
                "symbol": result.symbol,
                "qty": float(result.qty),
                "side": "SELL",
                "status": result.status,
                "filled_avg_price": (
                    float(result.filled_avg_price) if result.filled_avg_price else None
                ),
            }
        except Exception as e:
            print(f"  [LIVE] Sell order failed for {ticker}: {e}")
            return None

    # ------------------------------------------------------------------
    # Strategy Execution
    # ------------------------------------------------------------------

    def run_once(self, tickers: list[str]) -> dict:
        """
        Execute one evaluation cycle:
        1. Fetch latest bars for each ticker
        2. Evaluate strategy at the last bar
        3. Submit buy/sell orders based on signals

        Returns a dict of {ticker: signal_name} for logging.
        """
        self._init_clients()

        # Fetch recent bars (last 120 days to give strategies enough warmup)
        data = self.get_historical_bars(tickers, days=120)

        # Sync current positions from broker
        portfolio = self.sync_portfolio()
        self.risk_manager.set_daily_start(portfolio.total_value)

        signals = {}

        for ticker in tickers:
            df = data.get(ticker)
            if df is None or df.empty:
                print(f"  [LIVE] No data for {ticker}, skipping")
                signals[ticker] = "NO_DATA"
                continue

            # Pre-compute indicators
            self.strategy.prepare(df)

            # Evaluate at the last bar
            idx = len(df) - 1
            price = df["close"].iloc[idx]
            portfolio.update_price(ticker, price)

            # Pre-compute ATR for this ticker if sizing is enabled
            atr_val = None
            if self.config.use_atr_sizing:
                atr_arr = compute_atr(df, self.config.atr_period)
                atr_val = atr_arr[idx] if idx < len(atr_arr) and not pd.isna(atr_arr[idx]) else None

            result: StrategyResult = self.strategy.evaluate(df, idx)
            signals[ticker] = result.signal.name

            if result.signal == Signal.BUY:
                # Check if we already hold this ticker
                if ticker in portfolio.positions:
                    continue

                # Size the position — ATR-based or fixed allocation
                max_value = portfolio.total_value * self.risk_manager.max_allocation_pct
                if self.config.use_atr_sizing and atr_val and atr_val > 0:
                    risk_amount = portfolio.total_value * self.config.position_risk_pct
                    stop_distance = atr_val * self.config.atr_multiplier
                    quantity = min(risk_amount / stop_distance, max_value / price)
                else:
                    quantity = max_value / price

                risk = self.risk_manager.check_buy(portfolio, ticker, quantity, price)
                if not risk.allowed:
                    print(f"  [LIVE] BUY blocked for {ticker}: {risk.reason}")
                    continue

                order = self.submit_buy(ticker, quantity)
                if order:
                    print(f"  [LIVE] BUY  {ticker} x{order['qty']} ~${order.get('filled_avg_price', '?')}")

            elif result.signal == Signal.SELL:
                pos = portfolio.positions.get(ticker)
                if pos is None or pos.quantity <= 0:
                    continue

                order = self.submit_sell(ticker, pos.quantity)
                if order:
                    print(f"  [LIVE] SELL {ticker} x{order['qty']} ~${order.get('filled_avg_price', '?')}")

            # Check stop-loss / take-profit
            if ticker in portfolio.positions:
                risk = self.risk_manager.check_sell(portfolio, ticker, price)
                if risk.action == RiskAction.STOP_LOSS:
                    order = self.submit_sell(ticker, portfolio.positions[ticker].quantity)
                    if order:
                        print(f"  [LIVE] STOP-LOSS {ticker}: {risk.reason}")
                elif risk.action == RiskAction.TAKE_PROFIT:
                    order = self.submit_sell(ticker, portfolio.positions[ticker].quantity)
                    if order:
                        print(f"  [LIVE] TAKE-PROFIT {ticker}: {risk.reason}")

        return signals

    def run(self, tickers: list[str], once: bool = True) -> None:
        """
        Execute the live trading loop.

        Args:
            tickers: List of ticker symbols to trade.
            once: If True, run one evaluation cycle and return.
                  If False, loop forever at poll_interval_seconds.
        """
        self._init_clients()

        # Print account summary
        account = self.get_account_info()
        print(f"\n=== Live Trader ({'PAPER' if self.config.paper else 'LIVE'}) ===")
        print(f"  Equity:    ${account['equity']:,.2f}")
        print(f"  Cash:      ${account['cash']:,.2f}")
        print(f"  Status:    {account['status']}")
        print(f"  Strategy:  {self.strategy.name}")
        print(f"  Tickers:   {', '.join(tickers)}")
        print()

        if once:
            print("  Running one evaluation cycle...")
            self.run_once(tickers)
            print("\n  Done.")
        else:
            import time
            print(f"  Polling every {self.config.poll_interval_seconds}s. Press Ctrl+C to stop.")
            try:
                while True:
                    self.run_once(tickers)
                    time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("\n  Live trader stopped by user.")
