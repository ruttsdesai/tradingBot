"""
IBKR Live Trading Engine — executes real/paper trades via Interactive Brokers.

Uses ib_insync to connect to TWS/IB Gateway.
Supports NSE (India), SMART (US), and TSE (Canada) markets in one account.

Mirrors the AlpacaLiveTrader interface so the CLI can use either broker
with the same run_once() / run() contract.

Requirements:
    pip install ib_insync

Setup:
    1. Install TWS or IB Gateway from https://www.interactivebrokers.com/
    2. In TWS: File → Global Configuration → API → Settings
       - Enable ActiveX and Socket Clients
       - Port: 7497 (paper) or 7496 (live)
    3. Log into TWS with your paper or live credentials
    4. Run: python cli.py ibkr-live --market usa --strategy bollinger_bands
"""

import asyncio

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .portfolio import Portfolio, Position
from .risk_manager import RiskAction, RiskCheck, RiskManager
from .paper_trader import compute_atr
from strategies.base import BaseStrategy, Signal, StrategyResult

# ---------------------------------------------------------------------------
# ib_insync / eventkit needs a running event loop on Python ≥ 3.10.
# Create one at module level so imports don't crash.
# ---------------------------------------------------------------------------
try:
    _loop = asyncio.get_running_loop()
except RuntimeError:
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)


# ---------------------------------------------------------------------------
# Exchange / contract helpers
# ---------------------------------------------------------------------------

EXCHANGE_MAP: dict[str, dict[str, str]] = {
    "NSE":  {"exchange": "NSE",    "currency": "INR"},
    "US":   {"exchange": "SMART",  "currency": "USD"},
    "TSX":  {"exchange": "TSE",    "currency": "CAD"},
}


def detect_market(ticker: str) -> str:
    """Infer the IBKR market from a ticker string.

    >>> detect_market("SBIN.NS")
    'NSE'
    >>> detect_market("AAPL")
    'US'
    >>> detect_market("RY.TO")
    'TSX'
    """
    upper = ticker.upper()
    if upper.endswith(".NS"):
        return "NSE"
    if upper.endswith(".TO"):
        return "TSX"
    return "US"


def strip_suffix(ticker: str) -> str:
    """Return the base symbol without market suffix, uppercased for IBKR.

    >>> strip_suffix("SBIN.NS")
    'SBIN'
    >>> strip_suffix("AAPL")
    'AAPL'
    >>> strip_suffix("RY.TO")
    'RY'
    >>> strip_suffix("sbin.ns")
    'SBIN'
    """
    for suffix in (".NS", ".TO"):
        if ticker.upper().endswith(suffix):
            return ticker[: -len(suffix)].upper()
    return ticker.upper()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class IBKRLiveTraderConfig:
    """Configuration for the IBKR live trading engine.

    Port defaults:
        7497 — TWS paper trading
        7496 — TWS live trading
        4002 — IB Gateway paper trading
        4001 — IB Gateway live trading
    """

    host: str = "127.0.0.1"
    port: int = 7497           # TWS paper by default
    client_id: int = 1
    initial_capital: float = 100_000.0
    max_positions: int = 5
    max_allocation_pct: float = 0.20
    max_daily_loss_pct: float = 0.03
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    poll_interval_seconds: int = 60
    use_atr_sizing: bool = False
    position_risk_pct: float = 0.01
    atr_period: int = 14
    atr_multiplier: float = 2.0


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------

class IBKRLiveTrader:
    """Live trading engine backed by Interactive Brokers via ib_insync.

    Usage:
        from engine.ibkr_live_trader import IBKRLiveTrader, IBKRLiveTraderConfig
        from strategies.bollinger_bands import BollingerBandsStrategy

        config = IBKRLiveTraderConfig(port=7497)  # paper
        strategy = BollingerBandsStrategy(period=10, num_std=1.5)
        trader = IBKRLiveTrader(config, strategy)
        trader.run(["NVDA", "MSFT", "QQQ"], once=True)
    """

    def __init__(
        self,
        config: IBKRLiveTraderConfig,
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
        self._ib: Optional["IB"] = None  # type: ignore[name-defined]  # noqa: F821

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> "IB":  # type: ignore[name-defined]  # noqa: F821
        """Connect to TWS/IB Gateway. Returns the IB instance."""
        if self._ib is not None and self._ib.isConnected():
            return self._ib

        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError(
                "ib_insync is required for IBKR live trading. "
                "Install it with: pip install ib_insync"
            )

        self._ib = IB()
        try:
            self._ib.connect(self.config.host, self.config.port,
                             clientId=self.config.client_id, timeout=10)
        except ConnectionRefusedError:
            raise ConnectionError(
                f"Cannot connect to TWS/Gateway at {self.config.host}:{self.config.port}. "
                "Make sure TWS or IB Gateway is running and API connections are enabled.\n"
                "  TWS -> File -> Global Configuration -> API -> Settings\n"
                "  [x] Enable ActiveX and Socket Clients\n"
                f"  Port: {self.config.port}"
            )

        return self._ib

    def disconnect(self) -> None:
        """Gracefully disconnect from TWS/Gateway."""
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
            self._ib = None

    # ------------------------------------------------------------------
    # Contract helpers
    # ------------------------------------------------------------------

    def _make_contract(self, ticker: str) -> "Contract":  # type: ignore[name-defined]  # noqa: F821
        """Build an IBKR Stock contract, qualifying it against the server."""
        from ib_insync import Stock

        market = detect_market(ticker)
        info = EXCHANGE_MAP[market]
        base = strip_suffix(ticker)

        contract = Stock(base, info["exchange"], info["currency"])
        ib = self._connect()
        # qualifyContracts mutates the contract with full conId etc.
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {ticker} ({contract})")
        return qualified[0]

    # ------------------------------------------------------------------
    # Account & Portfolio
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict:
        """Return key account fields (NetLiquidation, Cash, etc.)."""
        ib = self._connect()
        summary = ib.accountSummary()

        def _get_float(tag: str, default: float = 0.0) -> float:
            for item in summary:
                if item.tag == tag:
                    try:
                        return float(item.value)
                    except (ValueError, TypeError):
                        return default
            return default

        return {
            "equity": _get_float("NetLiquidation"),
            "cash": _get_float("AvailableFunds"),
            "buying_power": _get_float("BuyingPower"),
            "gross_position_value": _get_float("GrossPositionValue"),
        }

    def get_positions(self) -> list[dict]:
        """Return all current open positions from IBKR."""
        ib = self._connect()
        positions = ib.positions()
        return [
            {
                "symbol": p.contract.localSymbol or p.contract.symbol,
                "qty": float(p.position),
                "avg_entry_price": float(p.avgCost),
                "market_value": float(p.position) * float(p.avgCost),
                "unrealized_pl": float(p.unrealizedPNL),
                "unrealized_plpc": (
                    float(p.unrealizedPNL) / (float(p.position) * float(p.avgCost)) * 100
                    if float(p.avgCost) > 0 and float(p.position) > 0 else 0.0
                ),
            }
            for p in positions
        ]

    def sync_portfolio(self) -> Portfolio:
        """Build a local Portfolio snapshot from the live IBKR account."""
        ib = self._connect()
        summary = ib.accountSummary()

        def _get(tag: str) -> float:
            for item in summary:
                if item.tag == tag:
                    return float(item.value)
            return 0.0

        equity = _get("NetLiquidation")
        cash = _get("AvailableFunds")

        portfolio = Portfolio(
            initial_capital=self.config.initial_capital,
            current_cash=cash,
        )
        portfolio.peak_value = max(equity, portfolio.peak_value)

        positions = ib.positions()
        for p in positions:
            sym = p.contract.localSymbol or p.contract.symbol
            qty = float(p.position)
            entry = float(p.avgCost)

            # Try to get current price via snapshot; fall back to avgCost
            try:
                contract = p.contract
                ib.qualifyContracts(contract)
                ticker_data = ib.reqMktData(contract, '', True, False)
                price = ticker_data.last if ticker_data.last and ticker_data.last > 0 else entry
                ib.cancelMktData(contract)
            except Exception:
                price = entry

            portfolio.update_price(sym, price)
            portfolio.positions[sym] = Position(
                ticker=sym,
                quantity=qty,
                avg_entry_price=entry,
                entry_date=datetime.now(),
            )

        return portfolio

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_latest_price(self, ticker: str) -> float:
        """Fetch the latest price for a ticker via a snapshot quote."""
        ib = self._connect()
        contract = self._make_contract(ticker)
        ticker_data = ib.reqMktData(contract, '', False, False)
        ib.sleep(0.5)

        price = ticker_data.last if ticker_data.last and ticker_data.last > 0 else 0.0
        ib.cancelMktData(contract)

        if price <= 0:
            # Fallback: use close from latest historical bar
            bars = ib.reqHistoricalData(
                contract, endDateTime='', durationStr='1 D',
                barSizeSetting='1 day', whatToShow='TRADES',
                useRTH=True, formatDate=1,
            )
            if bars:
                price = float(bars[-1].close)
        return price

    def get_historical_bars(
        self,
        tickers: list[str],
        days: int = 120,
        timeframe: str = "1Day",
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily bars for one or more tickers. Returns {ticker: DataFrame}.

        Uses IBKR reqHistoricalData — note that pacing violations can occur
        if you request too many tickers in quick succession.  A small sleep
        is inserted between requests to stay friendly.
        """
        ib = self._connect()

        # Map timeframe to IBKR bar size
        tf_map = {
            "1Day": "1 day",
            "1Hour": "1 hour",
            "1Min": "1 min",
        }
        bar_size = tf_map.get(timeframe, "1 day")

        result: dict[str, pd.DataFrame] = {}
        from ib_insync import util

        for ticker in tickers:
            try:
                contract = self._make_contract(ticker)
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime='',
                    durationStr=f'{days} D',
                    barSizeSetting=bar_size,
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1,
                )
                if bars:
                    df = util.df(bars)
                    if not df.empty:
                        # Set datetime index from 'date' column
                        if 'date' in df.columns:
                            df['date'] = pd.to_datetime(df['date'])
                            df.set_index('date', inplace=True)
                        result[ticker] = df
                    else:
                        result[ticker] = pd.DataFrame()
                else:
                    result[ticker] = pd.DataFrame()

                # Pacing: stay friendly to IBKR's rate limits
                ib.sleep(0.3)

            except Exception as e:
                print(f"  [IBKR] Failed to fetch bars for {ticker}: {e}")
                result[ticker] = pd.DataFrame()

        return result

    # ------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------

    def submit_buy(self, ticker: str, quantity: float) -> dict | None:
        """Submit a MKT buy order. Returns order dict or None on failure."""
        ib = self._connect()
        from ib_insync import MarketOrder

        if quantity <= 0:
            return None

        try:
            contract = self._make_contract(ticker)
            order = MarketOrder('BUY', round(quantity, 6))
            trade = ib.placeOrder(contract, order)

            # Wait briefly for fill confirmation
            ib.sleep(1.0)

            return {
                "id": str(trade.order.orderId),
                "symbol": ticker,
                "qty": trade.order.totalQuantity,
                "side": "BUY",
                "status": trade.orderStatus.status,
                "filled_avg_price": (
                    float(trade.orderStatus.avgFillPrice)
                    if trade.orderStatus.avgFillPrice > 0
                    else None
                ),
            }
        except Exception as e:
            print(f"  [IBKR] Buy order failed for {ticker}: {e}")
            return None

    def submit_sell(self, ticker: str, quantity: float) -> dict | None:
        """Submit a MKT sell order. Returns order dict or None on failure."""
        ib = self._connect()
        from ib_insync import MarketOrder

        if quantity <= 0:
            return None

        try:
            contract = self._make_contract(ticker)
            order = MarketOrder('SELL', round(quantity, 6))
            trade = ib.placeOrder(contract, order)

            ib.sleep(1.0)

            return {
                "id": str(trade.order.orderId),
                "symbol": ticker,
                "qty": trade.order.totalQuantity,
                "side": "SELL",
                "status": trade.orderStatus.status,
                "filled_avg_price": (
                    float(trade.orderStatus.avgFillPrice)
                    if trade.orderStatus.avgFillPrice > 0
                    else None
                ),
            }
        except Exception as e:
            print(f"  [IBKR] Sell order failed for {ticker}: {e}")
            return None

    # ------------------------------------------------------------------
    # Strategy Execution
    # ------------------------------------------------------------------

    def run_once(self, tickers: list[str]) -> dict:
        """Execute one evaluation cycle.

        1. Fetch latest bars for each ticker
        2. Evaluate strategy at the last bar
        3. Submit buy / sell orders based on signals

        Returns a dict of {ticker: signal_name} for logging.
        """
        ib = self._connect()

        # Fetch recent bars (last 120 days to give strategies enough warmup)
        data = self.get_historical_bars(tickers, days=120)

        # Sync current positions from broker
        portfolio = self.sync_portfolio()
        self.risk_manager.set_daily_start(portfolio.total_value)

        signals: dict[str, str] = {}

        for ticker in tickers:
            df = data.get(ticker)
            if df is None or df.empty:
                print(f"  [IBKR] No data for {ticker}, skipping")
                signals[ticker] = "NO_DATA"
                continue

            # Pre-compute indicators
            self.strategy.prepare(df)

            # Evaluate at the last bar
            idx = len(df) - 1
            price = df["close"].iloc[idx]
            portfolio.update_price(ticker, price)

            # Pre-compute ATR if sizing is enabled
            atr_val = None
            if self.config.use_atr_sizing:
                atr_arr = compute_atr(df, self.config.atr_period)
                atr_val = (
                    atr_arr[idx]
                    if idx < len(atr_arr) and not pd.isna(atr_arr[idx])
                    else None
                )

            result: StrategyResult = self.strategy.evaluate(df, idx)
            signals[ticker] = result.signal.name

            # Strip suffix for portfolio matching (positions use base symbol)
            base_ticker = strip_suffix(ticker)

            if result.signal == Signal.BUY:
                # Already holding this ticker?
                if base_ticker in portfolio.positions:
                    continue

                # Size the position
                max_value = portfolio.total_value * self.risk_manager.max_allocation_pct
                if self.config.use_atr_sizing and atr_val and atr_val > 0:
                    risk_amount = portfolio.total_value * self.config.position_risk_pct
                    stop_distance = atr_val * self.config.atr_multiplier
                    quantity = min(risk_amount / stop_distance, max_value / price)
                else:
                    quantity = max_value / price

                risk = self.risk_manager.check_buy(portfolio, base_ticker, quantity, price)
                if not risk.allowed:
                    print(f"  [IBKR] BUY blocked for {ticker}: {risk.reason}")
                    continue

                order = self.submit_buy(ticker, quantity)
                if order:
                    print(
                        f"  [IBKR] BUY  {ticker} x{order['qty']} "
                        f"~${order.get('filled_avg_price', '?')}"
                    )

            elif result.signal == Signal.SELL:
                pos = portfolio.positions.get(base_ticker)
                if pos is None or pos.quantity <= 0:
                    continue

                order = self.submit_sell(ticker, pos.quantity)
                if order:
                    print(
                        f"  [IBKR] SELL {ticker} x{order['qty']} "
                        f"~${order.get('filled_avg_price', '?')}"
                    )

            # Check stop-loss / take-profit
            if base_ticker in portfolio.positions:
                risk = self.risk_manager.check_sell(portfolio, base_ticker, price)
                if risk.action == RiskAction.STOP_LOSS:
                    pos = portfolio.positions[base_ticker]
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [IBKR] STOP-LOSS {ticker}: {risk.reason}")
                elif risk.action == RiskAction.TAKE_PROFIT:
                    pos = portfolio.positions[base_ticker]
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [IBKR] TAKE-PROFIT {ticker}: {risk.reason}")

        return signals

    def run(self, tickers: list[str], once: bool = True) -> None:
        """Execute the IBKR live trading loop.

        Args:
            tickers: List of ticker symbols to trade.
            once: If True, run one evaluation cycle and return.
                  If False, loop forever at poll_interval_seconds.
        """
        ib = self._connect()

        # Print account summary
        try:
            account = self.get_account_info()
            print(f"\n=== IBKR Live Trader (PAPER port {self.config.port}) ===")
            print(f"  Equity:        ${account['equity']:,.2f}")
            print(f"  Cash:          ${account['cash']:,.2f}")
            print(f"  Buying Power:  ${account['buying_power']:,.2f}")
            print(f"  Strategy:      {self.strategy.name}")
            print(f"  Tickers:       {', '.join(tickers)}")
            print()
        except Exception as e:
            print(f"\n=== IBKR Live Trader === (account summary unavailable: {e})\n")
            print(f"  Strategy:  {self.strategy.name}")
            print(f"  Tickers:   {', '.join(tickers)}")
            print()

        if once:
            print("  Running one evaluation cycle...")
            self.run_once(tickers)
            print("\n  Done.")
            self.disconnect()
        else:
            import time

            print(f"  Polling every {self.config.poll_interval_seconds}s. "
                  "Press Ctrl+C to stop.")
            try:
                while True:
                    self.run_once(tickers)
                    time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("\n  IBKR live trader stopped by user.")
                self.disconnect()
