"""
Crypto Live Trading Engine -- executes real trades via Binance.

Mirrors the AlpacaLiveTrader interface but uses python-binance for
spot market orders, account sync, and historical kline data.

Requires python-binance:  pip install python-binance

Usage:
    from engine.crypto_live_trader import BinanceLiveTrader, CryptoLiveTraderConfig

    config = CryptoLiveTraderConfig(api_key="...", api_secret="...", testnet=True)
    trader = BinanceLiveTrader(config, strategy)
    trader.run(tickers=["BTCUSDT", "ETHUSDT"], once=True)
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from .portfolio import Portfolio, Position
from .risk_manager import RiskAction, RiskManager
from .paper_trader import compute_atr
from strategies.base import BaseStrategy, Signal, StrategyResult


@dataclass
class CryptoLiveTraderConfig:
    """Configuration for Binance live trading."""

    api_key: str
    api_secret: str
    testnet: bool = True  # Use Binance testnet by default
    initial_capital: float = 10_000.0
    max_positions: int = 5
    max_allocation_pct: float = 0.20
    max_daily_loss_pct: float = 0.03
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    poll_interval_seconds: int = 60
    interval: str = "1d"  # candle interval: 1d, 4h, 1h, 15m, 5m, 1m
    use_atr_sizing: bool = False
    position_risk_pct: float = 0.01
    atr_period: int = 14
    atr_multiplier: float = 2.0


class BinanceLiveTrader:
    """
    Live trading engine backed by Binance Spot.

    Fetches account balances, evaluates strategies against recent kline data,
    and submits real (or testnet) spot orders.

    Usage:
        config = CryptoLiveTraderConfig(
            api_key="...", api_secret="...", testnet=True,
        )
        strategy = MACrossoverStrategy(fast_period=20, slow_period=50)
        trader = BinanceLiveTrader(config, strategy)
        trader.run(tickers=["BTCUSDT", "ETHUSDT"], once=True)
    """

    def __init__(
        self,
        config: CryptoLiveTraderConfig,
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
        self._client = None
        self._portfolio: Optional[Portfolio] = None
        self._symbol_info_cache: dict[str, dict] = {}  # cached LOT_SIZE etc.

    def _init_client(self) -> None:
        """Lazily initialize Binance client."""
        if self._client is not None:
            return

        try:
            from binance.client import Client
        except ImportError:
            raise ImportError(
                "python-binance is required for crypto live trading. "
                "Install it with: pip install python-binance"
            )

        if self.config.testnet:
            self._client = Client(
                self.config.api_key,
                self.config.api_secret,
                testnet=True,
            )
        else:
            self._client = Client(self.config.api_key, self.config.api_secret)

    # ------------------------------------------------------------------
    # Account & Portfolio
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict:
        """Return key account fields from Binance."""
        self._init_client()
        account = self._client.get_account()
        balances = account.get("balances", [])
        total_btc_value = 0.0
        usdt_balance = 0.0
        for b in balances:
            asset = b["asset"]
            free = float(b["free"])
            locked = float(b["locked"])
            total = free + locked
            if total <= 0:
                continue
            if asset == "USDT":
                usdt_balance = total
            elif asset == "BTC":
                total_btc_value += total  # rough approximation

        return {
            "can_trade": account.get("canTrade", False),
            "balances_count": len([b for b in balances
                                   if float(b["free"]) + float(b["locked"]) > 0]),
            "usdt_balance": usdt_balance,
            "btc_balance": total_btc_value,
        }

    def get_balances(self) -> dict[str, dict]:
        """Return all non-zero balances. {asset: {free, locked}}."""
        self._init_client()
        account = self._client.get_account()
        result = {}
        for b in account.get("balances", []):
            free = float(b["free"])
            locked = float(b["locked"])
            if free + locked > 0:
                result[b["asset"]] = {"free": free, "locked": locked}
        return result

    def sync_portfolio(self) -> Portfolio:
        """Build a local Portfolio snapshot from live Binance balances + prices."""
        self._init_client()

        balances = self.get_balances()
        cash = balances.get("USDT", {}).get("free", 0.0)

        portfolio = Portfolio(
            initial_capital=self.config.initial_capital,
            current_cash=cash,
        )

        # Get current prices for all held assets
        held_assets = [a for a in balances if a != "USDT"]
        prices = {}
        for asset in held_assets:
            symbol = f"{asset}USDT"
            try:
                ticker = self._client.get_symbol_ticker(symbol=symbol)
                prices[asset] = float(ticker["price"])
            except Exception:
                prices[asset] = 0.0

        total_value = cash
        for asset, bal in balances.items():
            if asset == "USDT":
                continue
            qty = bal["free"] + bal["locked"]
            price = prices.get(asset, 0)
            if qty > 0 and price > 0:
                portfolio.update_price(asset, price)
                portfolio.positions[asset] = Position(
                    ticker=asset,
                    quantity=qty,
                    avg_entry_price=price,  # Binance doesn't expose avg entry easily
                    entry_date=datetime.now(),
                )
                total_value += qty * price

        portfolio.peak_value = max(total_value, portfolio.peak_value)
        return portfolio

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_latest_price(self, ticker: str) -> float:
        """Fetch the latest price for a trading pair (e.g., BTCUSDT)."""
        self._init_client()
        t = self._client.get_symbol_ticker(symbol=ticker)
        return float(t["price"])

    def get_historical_klines(
        self,
        tickers: list[str],
        days: int = 120,
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch daily klines for one or more tickers.
        Returns {ticker: DataFrame} with columns: open, high, low, close, volume.
        """
        self._init_client()
        from binance.client import Client

        interval_map = {
            "1d": Client.KLINE_INTERVAL_1DAY,
            "1h": Client.KLINE_INTERVAL_1HOUR,
            "4h": Client.KLINE_INTERVAL_4HOUR,
            "15m": Client.KLINE_INTERVAL_15MINUTE,
            "5m": Client.KLINE_INTERVAL_5MINUTE,
            "1m": Client.KLINE_INTERVAL_1MINUTE,
        }
        binance_interval = interval_map.get(interval, Client.KLINE_INTERVAL_1DAY)

        result: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            try:
                klines = self._client.get_historical_klines(
                    ticker,
                    binance_interval,
                    f"{days} day ago UTC",
                )
                if not klines:
                    result[ticker] = pd.DataFrame()
                    continue

                df = pd.DataFrame(
                    klines,
                    columns=[
                        "timestamp", "open", "high", "low", "close", "volume",
                        "close_time", "quote_asset_volume", "num_trades",
                        "taker_buy_base_vol", "taker_buy_quote_vol", "ignore",
                    ],
                )
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df[["open", "high", "low", "close", "volume"]]
                result[ticker] = df
            except Exception as e:
                print(f"  [BINANCE] Failed to fetch {ticker}: {e}")
                result[ticker] = pd.DataFrame()

        return result

    # ------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------

    def _get_symbol_info(self, ticker: str) -> dict:
        """Cached symbol info lookup for LOT_SIZE filters."""
        if ticker not in self._symbol_info_cache:
            self._symbol_info_cache[ticker] = self._client.get_symbol_info(ticker) or {}
        return self._symbol_info_cache[ticker]

    def submit_buy(self, ticker: str, quantity: float) -> dict | None:
        """
        Submit a market buy order on Binance.
        For pairs like BTCUSDT, quantity is in the base asset (BTC).
        """
        self._init_client()
        from binance.enums import SIDE_BUY, ORDER_TYPE_MARKET

        if quantity <= 0:
            return None

        try:
            info = self._get_symbol_info(ticker)
            step_size = 0.000001
            for f in info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                    break

            # Round quantity to valid step size
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            qty = round(quantity - (quantity % step_size), precision)
            if qty < step_size:
                return None

            order = self._client.create_order(
                symbol=ticker,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
            )
            return self._format_order(order, "BUY")
        except Exception as e:
            print(f"  [BINANCE] BUY failed for {ticker}: {e}")
            return None

    def submit_sell(self, ticker: str, quantity: float) -> dict | None:
        """Submit a market sell order on Binance."""
        self._init_client()
        from binance.enums import SIDE_SELL, ORDER_TYPE_MARKET

        if quantity <= 0:
            return None

        try:
            info = self._get_symbol_info(ticker)
            step_size = 0.000001
            for f in info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                    break

            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            qty = round(quantity - (quantity % step_size), precision)
            if qty < step_size:
                return None

            order = self._client.create_order(
                symbol=ticker,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
            )
            return self._format_order(order, "SELL")
        except Exception as e:
            print(f"  [BINANCE] SELL failed for {ticker}: {e}")
            return None

    def _format_order(self, order: dict, side: str) -> dict:
        """Normalize Binance order response to a standard dict."""
        fills = order.get("fills", [])
        avg_price = None
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            avg_price = total_cost / total_qty if total_qty > 0 else None

        return {
            "id": str(order.get("orderId", "")),
            "symbol": order.get("symbol", ""),
            "qty": float(order.get("executedQty", order.get("origQty", 0))),
            "side": side,
            "status": order.get("status", "UNKNOWN"),
            "filled_avg_price": avg_price,
        }

    # ------------------------------------------------------------------
    # Strategy Execution
    # ------------------------------------------------------------------

    def run_once(self, tickers: list[str]) -> dict:
        """
        Execute one evaluation cycle:
        1. Fetch recent kline data for each ticker
        2. Evaluate strategy at the last bar
        3. Submit buy/sell orders based on signals

        Returns a dict of {ticker: signal_name} for logging.
        """
        self._init_client()

        data = self.get_historical_klines(tickers, days=120, interval=self.config.interval)
        portfolio = self.sync_portfolio()
        self.risk_manager.set_daily_start(portfolio.total_value)

        signals = {}

        for ticker in tickers:
            df = data.get(ticker)
            if df is None or df.empty:
                print(f"  [BINANCE] No data for {ticker}, skipping")
                signals[ticker] = "NO_DATA"
                continue

            self.strategy.prepare(df)

            idx = len(df) - 1
            price = df["close"].iloc[idx]
            portfolio.update_price(ticker, price)

            # Pre-compute ATR if sizing enabled
            atr_val = None
            if self.config.use_atr_sizing:
                atr_arr = compute_atr(df, self.config.atr_period)
                atr_val = atr_arr[idx] if idx < len(atr_arr) and not pd.isna(atr_arr[idx]) else None

            result: StrategyResult = self.strategy.evaluate(df, idx)
            signals[ticker] = result.signal.name

            if result.signal == Signal.BUY:
                if ticker in portfolio.positions:
                    continue

                max_value = portfolio.total_value * self.risk_manager.max_allocation_pct
                if self.config.use_atr_sizing and atr_val and atr_val > 0:
                    risk_amount = portfolio.total_value * self.config.position_risk_pct
                    stop_distance = atr_val * self.config.atr_multiplier
                    quantity = min(risk_amount / stop_distance, max_value / price)
                else:
                    quantity = max_value / price

                risk = self.risk_manager.check_buy(portfolio, ticker, quantity, price)
                if not risk.allowed:
                    print(f"  [BINANCE] BUY blocked for {ticker}: {risk.reason}")
                    continue

                order = self.submit_buy(ticker, quantity)
                if order:
                    print(f"  [BINANCE] BUY  {ticker} x{order['qty']} ~${order.get('filled_avg_price', '?')}")

            elif result.signal == Signal.SELL:
                pos = portfolio.positions.get(ticker)
                if pos is None or pos.quantity <= 0:
                    continue

                order = self.submit_sell(ticker, pos.quantity)
                if order:
                    print(f"  [BINANCE] SELL {ticker} x{order['qty']} ~${order.get('filled_avg_price', '?')}")

            # Stop-loss / take-profit checks
            if ticker in portfolio.positions:
                risk = self.risk_manager.check_sell(portfolio, ticker, price)
                if risk.action in (RiskAction.STOP_LOSS, RiskAction.TAKE_PROFIT):
                    order = self.submit_sell(ticker, portfolio.positions[ticker].quantity)
                    if order:
                        print(f"  [BINANCE] {risk.action.name} {ticker}: {risk.reason}")

        return signals

    def run(self, tickers: list[str], once: bool = True) -> None:
        """
        Execute the live trading loop.

        Args:
            tickers: List of Binance trading pairs (e.g., ["BTCUSDT", "ETHUSDT"]).
            once: If True, run one cycle and return. If False, loop forever.
        """
        self._init_client()

        account = self.get_account_info()
        env = "TESTNET" if self.config.testnet else "LIVE"
        print(f"\n=== Binance Live Trader ({env}) ===")
        print(f"  USDT Balance: ${account['usdt_balance']:,.2f}")
        print(f"  Can Trade:    {account['can_trade']}")
        print(f"  Strategy:     {self.strategy.name}")
        print(f"  Tickers:      {', '.join(tickers)}")
        print()

        if once:
            print("  Running one evaluation cycle...")
            self.run_once(tickers)
            print("\n  Done.")
        else:
            print(f"  Polling every {self.config.poll_interval_seconds}s. Press Ctrl+C to stop.")
            try:
                while True:
                    self.run_once(tickers)
                    time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("\n  Crypto live trader stopped by user.")
