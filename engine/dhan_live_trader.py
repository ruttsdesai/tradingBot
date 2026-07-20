"""
Dhan Live Trading Engine — executes real/paper trades via Dhan (dhanhq SDK).

Uses dhanhq 2.x to connect to Dhan's trading API.
Supports NSE equity trading for Indian residents.

Mirrors the AlpacaLiveTrader / IBKRLiveTrader interface so the CLI can use
any broker with the same run_once() / run() contract.

Requirements:
    pip install dhanhq

Setup:
    1. Open a Dhan account at https://dhan.co
    2. Go to https://developer.dhanhq.co → generate API keys
       - client_id: your Dhan client ID
       - access_token: JWT token from developer portal
    3. For sandbox (paper): use https://sandbox.dhan.co/v2/ credentials
    4. Run: python cli.py dhan-live --strategy macd --once
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .portfolio import Portfolio, Position
from .risk_manager import RiskAction, RiskCheck, RiskManager
from .paper_trader import compute_atr
from .notifier import (
    TelegramNotifier,
    TelegramUserNotifier,
    format_buy_msg,
    format_sell_msg,
    format_sl_msg,
    format_tp_msg,
    format_session_start,
    format_daily_summary,
    format_error,
)
from strategies.base import BaseStrategy, Signal, StrategyResult


# ---------------------------------------------------------------------------
# Security ID mapping for NSE stocks
# Pre-populated with 10 India tickers (others resolved via fetch_security_list)
# Users can extend via fetch_security_list() at runtime.
# ---------------------------------------------------------------------------
_KNOWN_SECURITY_IDS: dict[str, str] = {
    # Format: "BASE_SYMBOL" -> "security_id"
    "RELIANCE": "2885",
    "TCS": "11536",
    "HDFCBANK": "1333",
    "INFY": "1594",
    "ICICIBANK": "2179",
    "BHARTIARTL": "2718",
    "ITC": "1660",
    "HINDUNILVR": "1394",
    "SBIN": "3045",
    "LT": "8028",
    "KOTAKBANK": "1922",
    "AXISBANK": "5900",
    "BAJFINANCE": "317",
    "MARUTI": "10999",
    "SUNPHARMA": "3351",
    "ASIANPAINT": "236",
    "NESTLEIND": "17963",
    "WIPRO": "3787",
    "TITAN": "3506",
    "M&M": "2031",
    "HCLTECH": "7229",
}


def strip_ns(ticker: str) -> str:
    """Strip .NS suffix from Indian ticker symbols.

    >>> strip_ns("SBIN.NS")
    'SBIN'
    >>> strip_ns("AAPL")
    'AAPL'
    """
    upper = ticker.upper()
    if upper.endswith(".NS"):
        return upper[:-3]
    return upper


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class DhanLiveTraderConfig:
    """Configuration for the Dhan live trading engine.

    NOTE: Dhan's sandbox API requires a static IP whitelisted in their
    developer portal. Without a static IP, sandbox calls will hang.
    We use the production API URL for both sandbox and live modes.

    For paper testing, use the built-in paper trading engine:
        python cli.py paper -s macd -t SBIN.NS ICICIBANK.NS BHARTIARTL.NS

    Args:
        client_id: Dhan client ID from developer portal.
        access_token: JWT access token from developer portal.
        sandbox: If True, disables SSL verification. Does NOT change API URL.
        disable_ssl: Force-disable SSL verification regardless of sandbox flag.
    """

    client_id: str = ""
    access_token: str = ""
    sandbox: bool = True
    sandbox_client_id: str = ""
    sandbox_access_token: str = ""
    disable_ssl: bool = False
    initial_capital: float = 100_000.0
    max_positions: int = 5
    max_allocation_pct: float = 0.20
    max_daily_loss_pct: float = 0.03
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    poll_interval_seconds: int = 60
    intraday: bool = False
    intraday_interval: str = "5m"
    max_hold_minutes: int = 120          # Max minutes to hold a position before forced exit
    min_profit_threshold_pct: float = 0.005  # Min profit (0.5%) required to keep a stale position
    min_volatility_pct: float = 0.005     # Skip BUYs if ATR/close < 0.5% (sideways market)
    stop_buying_minutes: int = 900         # 15:00 IST — stop opening new positions
    force_square_off_minutes: int = 910    # 15:10 IST — close all positions
    use_atr_sizing: bool = False
    position_risk_pct: float = 0.01
    atr_period: int = 14
    atr_multiplier: float = 2.0
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session: str = "dhan_trader"


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------

class DhanLiveTrader:
    """Live trading engine backed by Dhan via dhanhq SDK.

    Usage:
        from engine.dhan_live_trader import DhanLiveTrader, DhanLiveTraderConfig
        from strategies.macd import MACDStrategy

        config = DhanLiveTraderConfig(
            client_id="YOUR_CLIENT_ID",
            access_token="YOUR_ACCESS_TOKEN",
            sandbox=True,
        )
        strategy = MACDStrategy(fast_period=16, slow_period=34, signal_period=9)
        trader = DhanLiveTrader(config, strategy)
        trader.run(["SBIN.NS", "ICICIBANK.NS", "BHARTIARTL.NS"], once=True)
    """

    def __init__(
        self,
        config: DhanLiveTraderConfig,
        strategy: BaseStrategy = None,
        strategies: list[BaseStrategy] = None,
        risk_manager: Optional[RiskManager] = None,
    ):
        self.config = config
        # Support both single-strategy (backward compat) and multi-strategy
        if strategies:
            self.strategies = strategies
            self.strategy = strategies[0]  # First strategy for display/backward compat
        elif strategy:
            self.strategies = [strategy]
            self.strategy = strategy
        else:
            raise ValueError("At least one strategy (or strategies list) is required")
        self.risk_manager = risk_manager or RiskManager(
            max_positions=config.max_positions,
            max_allocation_pct=config.max_allocation_pct,
            max_daily_loss_pct=config.max_daily_loss_pct,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
        )
        self._dhan = None
        self._security_id_cache: dict[str, str] = dict(_KNOWN_SECURITY_IDS)
        self._notifier = None
        self._sl_orders: dict[str, str] = {}   # ticker (base) -> stop-loss order_id
        self._tp_orders: dict[str, str] = {}   # ticker (base) -> take-profit order_id
        self._cnc_holdings: set[str] = set()   # symbols held as CNC delivery (sell as CNC, not MIS)
        self._init_notifier()

    # ------------------------------------------------------------------
    # Notifier
    # ------------------------------------------------------------------

    def _init_notifier(self):
        """Lazily initialize the Telegram notifier if credentials are set.

        Tries backends in order:
          1. TelegramUserNotifier (Telethon — no BotFather needed)
             Requires TG_API_ID + TG_API_HASH from my.telegram.org/apps
          2. TelegramNotifier (Bot API — requires @BotFather bot token)
        """
        # Option 1: Telethon user account (no bot needed!)
        if self.config.tg_api_id and self.config.tg_api_hash:
            try:
                # Verify telethon is installed before committing
                import telethon  # noqa: F401
                self._notifier = TelegramUserNotifier(
                    api_id=int(self.config.tg_api_id),
                    api_hash=str(self.config.tg_api_hash),
                    session_file=self.config.tg_session,
                )
                if self._notifier.is_enabled:
                    print("  [NOTIFIER] Using Telegram user account (Telethon)")
                    return
            except ImportError:
                print("  [NOTIFIER] Telethon not installed, falling back to Bot API")
            except Exception as e:
                print(f"  [NOTIFIER] Telethon init failed: {e}")

        # Option 2: Bot API (fallback)
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            self._notifier = TelegramNotifier(
                bot_token=self.config.telegram_bot_token,
                chat_id=self.config.telegram_chat_id,
            )
            if self._notifier.is_enabled:
                print("  [NOTIFIER] Using Telegram Bot API")

    def _notify(self, message: str) -> None:
        """Send a notification if notifier is enabled."""
        if self._notifier and self._notifier.is_enabled:
            self._notifier.send(message)

    # ------------------------------------------------------------------
    # API response helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _field(d: dict, *keys, default=None):
        """Read the first present key from a dict — Dhan responses mix
        camelCase (v2 API) and snake_case (older SDK versions)."""
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return default

    @staticmethod
    def _unwrap(response):
        """Extract the 'data' field from a Dhan API response dict.

        Dhan wraps all responses as:
            {"status": "success"|"failure", "remarks": ..., "data": ...}

        This returns the value of the 'data' key, or the original response
        if no 'data' key is present (e.g. on error).
        """
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response

    # ------------------------------------------------------------------
    # Client init
    # ------------------------------------------------------------------

    def _init_client(self):
        """Lazily initialize the dhanhq client.

        In sandbox mode, we monkey-patch the dhanhq SDK to use
        sandbox.dhan.co/v2 (which does NOT enforce IP whitelist)
        and use separate sandbox credentials.

        In live mode, we use api.dhan.co/v2 (production) which
        requires IP whitelisting on Dhan's developer portal.
        """
        if self._dhan is not None:
            return self._dhan

        try:
            from dhanhq import DhanContext
            from dhanhq.dhanhq import dhanhq as DhanClient
        except ImportError:
            raise ImportError(
                "dhanhq is required for Dhan live trading. "
                "Install it with: pip install dhanhq"
            )

        if self.config.sandbox:
            # Swap the SDK's hardcoded production URL for sandbox URL.
            # Sandbox API does NOT enforce IP whitelist — ideal for
            # users who can't whitelist their IP on the production portal.
            import dhanhq.dhan_http
            old_url = dhanhq.dhan_http.DhanHTTP.API_BASE_URL
            dhanhq.dhan_http.DhanHTTP.API_BASE_URL = "https://sandbox.dhan.co/v2"

            client_id = self.config.sandbox_client_id or self.config.client_id
            access_token = self.config.sandbox_access_token or self.config.access_token
            disable_ssl = self.config.disable_ssl or True

            if not self.config.sandbox_client_id:
                print("  [DHAN] WARNING: No sandbox credentials set — falling back to production creds.")
                print("  [DHAN] Production tokens will fail on sandbox URL. Get sandbox keys at dhanhq.co -> DevPortal -> Sandbox.")

            print("  [DHAN] Sandbox mode — using sandbox.dhan.co/v2 (no IP whitelist required)")

            # Create client with sandbox URL
            context = DhanContext(
                client_id=str(client_id),
                access_token=access_token,
                disable_ssl=disable_ssl,
            )
            self._dhan = DhanClient(context)

            # Restore original URL so live mode still works if this module is reused
            dhanhq.dhan_http.DhanHTTP.API_BASE_URL = old_url
            return self._dhan

        else:
            # Production mode — use production API URL + production credentials
            client_id = self.config.client_id
            access_token = self.config.access_token
            disable_ssl = self.config.disable_ssl
            print("  [DHAN] LIVE mode — using api.dhan.co/v2 (IP whitelist required)")

            context = DhanContext(
                client_id=str(client_id),
                access_token=access_token,
                disable_ssl=disable_ssl,
            )
            self._dhan = DhanClient(context)
            return self._dhan

    # ------------------------------------------------------------------
    # Security ID helpers
    # ------------------------------------------------------------------

    def _lookup_security_id(self, ticker: str) -> str:
        """Get the Dhan security_id for a ticker symbol.

        First checks the in-memory cache, then tries fetch_security_list().

        Args:
            ticker: Ticker symbol with or without .NS suffix (e.g., "SBIN.NS" or "SBIN").

        Returns:
            Security ID string for the Dhan API.

        Raises:
            ValueError: If security_id cannot be found.
        """
        base = strip_ns(ticker)

        # Check cache
        if base in self._security_id_cache:
            return self._security_id_cache[base]

        # Try to look up from Dhan's security list
        dhan = self._init_client()
        try:
            df = dhan.fetch_security_list("compact", filename="dhan_securities.csv")
            if df is not None and not df.empty:
                # The DataFrame should have columns like 'SEM_SMST_SECURITY_ID'
                # and 'SEM_TRADING_SYMBOL' or 'SEM_CUSTOM_SYMBOL'
                for col_pattern in ["SEM_TRADING_SYMBOL", "SEM_CUSTOM_SYMBOL",
                                    "trading_symbol", "symbol"]:
                    if col_pattern in df.columns:
                        mask = df[col_pattern].astype(str).str.upper() == base.upper()
                        if mask.any():
                            row = df[mask].iloc[0]
                            for id_col in ["SEM_SMST_SECURITY_ID", "security_id"]:
                                if id_col in df.columns:
                                    sid = str(row[id_col])
                                    self._security_id_cache[base] = sid
                                    return sid
        except Exception as e:
            print(f"  [DHAN] Could not fetch security list: {e}")

        raise ValueError(
            f"Could not find security_id for {ticker}. "
            f"Add it to _KNOWN_SECURITY_IDS in engine/dhan_live_trader.py, "
            f"or run fetch_security_list() to populate the cache."
        )

    # ------------------------------------------------------------------
    # Account & Portfolio
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict:
        """Return key account fields from Dhan fund limits."""
        dhan = self._init_client()
        raw = dhan.get_fund_limits()
        funds = self._unwrap(raw)
        if not isinstance(funds, dict):
            return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0, "used_margin": 0.0, "raw": raw}

        # Dhan uses camelCase: availabelBalance (note the typo in their API), sodLimit, etc.
        available = float(funds.get("availabelBalance", 0.0))
        return {
            "equity": available,
            "cash": available,
            "buying_power": available,
            "used_margin": float(funds.get("utilizedAmount", 0.0)),
            "raw": funds,
        }

    def get_positions(self) -> list[dict]:
        """Return all current open positions from Dhan."""
        dhan = self._init_client()

        positions = []
        try:
            raw = dhan.get_positions()
            data = self._unwrap(raw)
            if isinstance(data, list):
                for p in data:
                    positions.append({
                        "symbol": self._field(p, "tradingSymbol", "trading_symbol", default=""),
                        "security_id": str(self._field(p, "securityId", "security_id", default="")),
                        "qty": int(self._field(p, "netQty", "net_qty", default=0)),
                        "avg_entry_price": float(self._field(p, "costPrice", "buyAvg", "average_price", default=0.0)),
                        "ltp": float(self._field(p, "lastTradedPrice", "last_price", default=0.0)),
                        "unrealized_pl": float(self._field(p, "unrealizedProfit", "unrealized_profit", default=0.0)),
                    })
        except Exception as e:
            print(f"  [DHAN] Error fetching positions: {e}")

        return positions

    def get_holdings(self) -> list[dict]:
        """Return all delivery holdings (T+1 positions from prior sessions)."""
        dhan = self._init_client()

        holdings = []
        try:
            raw = dhan.get_holdings()
            data = self._unwrap(raw)
            if isinstance(data, list):
                for h in data:
                    holdings.append({
                        "symbol": self._field(h, "tradingSymbol", "trading_symbol", default=""),
                        "security_id": str(self._field(h, "securityId", "security_id", default="")),
                        "qty": int(self._field(h, "totalQty", "total_qty", default=0)),
                        "avg_entry_price": float(self._field(h, "avgCostPrice", "average_price", default=0.0)),
                        "ltp": float(self._field(h, "lastTradedPrice", "last_price", default=0.0)),
                    })
        except Exception as e:
            print(f"  [DHAN] Error fetching holdings: {e}")

        return holdings

    def sync_portfolio(self) -> Portfolio:
        """Build a local Portfolio snapshot from the live Dhan account."""
        dhan = self._init_client()

        # Get account balance
        raw_funds = dhan.get_fund_limits()
        funds = self._unwrap(raw_funds)
        if not isinstance(funds, dict):
            funds = {}
        cash = float(funds.get("availabelBalance", self.config.initial_capital))

        portfolio = Portfolio(
            initial_capital=self.config.initial_capital,
            current_cash=cash,
        )

        # Get equity value from funds
        equity = float(funds.get("availabelBalance", cash))
        portfolio.peak_value = max(equity, portfolio.peak_value)

        # Combine positions + holdings
        all_positions: dict[str, dict] = {}
        self._cnc_holdings.clear()

        try:
            for p in self._unwrap(dhan.get_positions()) or []:
                if not isinstance(p, dict):
                    continue
                sym = self._field(p, "tradingSymbol", "trading_symbol", default="")
                qty = int(self._field(p, "netQty", "net_qty", default=0))
                if qty <= 0:
                    continue
                all_positions[sym] = {
                    "symbol": sym,
                    "qty": qty,
                    "avg_entry_price": float(self._field(p, "costPrice", "buyAvg", "average_price", default=0.0)),
                }
        except Exception:
            pass

        try:
            for h in self._unwrap(dhan.get_holdings()) or []:
                if not isinstance(h, dict):
                    continue
                sym = self._field(h, "tradingSymbol", "trading_symbol", default="")
                qty = int(self._field(h, "totalQty", "total_qty", default=0))
                if qty <= 0:
                    continue
                # Delivery holdings must be sold as CNC, never as intraday MIS
                self._cnc_holdings.add(sym)
                if sym in all_positions:
                    # Merge: add holdings qty to positions qty
                    all_positions[sym]["qty"] += qty
                else:
                    all_positions[sym] = {
                        "symbol": sym,
                        "qty": qty,
                        "avg_entry_price": float(self._field(h, "avgCostPrice", "average_price", default=0.0)),
                    }
        except Exception:
            pass

        # Try to get latest prices via quote_data
        price_map: dict[str, float] = {}
        if all_positions:
            exchange_groups: dict[str, list[str]] = {}
            sid_to_sym: dict[str, str] = {}   # reverse map built during group loop
            for sym, info in all_positions.items():
                try:
                    sid = self._lookup_security_id(sym)
                except ValueError:
                    continue
                segment = "NSE_EQ"  # We only trade NSE cash
                exchange_groups.setdefault(segment, []).append(sid)
                sid_to_sym[sid] = sym

            if exchange_groups:
                try:
                    raw_quotes = dhan.quote_data(securities=exchange_groups)
                    quotes = self._unwrap(raw_quotes)
                    if isinstance(quotes, dict):
                        for segment, entries in quotes.items():
                            if isinstance(entries, list):
                                for entry in entries:
                                    sid = str(entry.get("security_id", ""))
                                    ltp = float(entry.get("last_price", 0.0))
                                    if ltp > 0 and sid in sid_to_sym:
                                        price_map[sid_to_sym[sid]] = ltp
                except Exception:
                    pass

        # Build Position objects
        for sym, info in all_positions.items():
            price = price_map.get(sym, info["avg_entry_price"])
            portfolio.update_price(sym, price)
            portfolio.positions[sym] = Position(
                ticker=sym,
                quantity=info["qty"],
                avg_entry_price=info["avg_entry_price"],
                entry_date=datetime.now(),
            )

        return portfolio

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_latest_price(self, ticker: str) -> float:
        """Fetch the latest price for a ticker via quote_data."""
        dhan = self._init_client()
        try:
            sid = self._lookup_security_id(ticker)
            raw_quotes = dhan.quote_data(securities={"NSE_EQ": [sid]})
            quotes = self._unwrap(raw_quotes)
            if isinstance(quotes, dict):
                for entries in quotes.values():
                    if isinstance(entries, list) and entries:
                        ltp = float(entries[0].get("last_price", 0.0))
                        if ltp > 0:
                            return ltp
        except Exception:
            pass
        return 0.0

    def get_historical_bars(
        self,
        tickers: list[str],
        days: int = 120,
        timeframe: str = "1Day",
    ) -> dict[str, pd.DataFrame]:
        """Fetch bars for one or more tickers using yfinance.

        Dhan's Data API requires a separate paid subscription (DH-902 error).
        We use yfinance (free) for historical OHLC data and only use Dhan's
        API for account info and order execution.

        In intraday mode, fetches 5 days of N-minute bars (e.g. 5m, 15m).
        In daily mode, fetches N years of 1d bars.
        """
        from data.stocks import fetch_stock_data, fetch_intraday_data

        result: dict[str, pd.DataFrame] = {}

        if self.config.intraday:
            # Intraday: fetch 7 calendar days of N-minute bars (enough for
            # all strategy warmups — 7 days of 5m = ~525 bars). Use the
            # period-based intraday fetcher, which always includes today's
            # live bars (a date-range fetch drops the current session, so the
            # trader would otherwise evaluate on a frozen prior-day close).
            interval = self.config.intraday_interval

            for ticker in tickers:
                try:
                    df = fetch_intraday_data(ticker, interval=interval, days=7)
                    result[ticker] = df
                except Exception as e:
                    print(f"  [DHAN] Failed to fetch intraday bars for {ticker}: {e}")
                    result[ticker] = pd.DataFrame()
        else:
            # Daily: fetch N years of 1d bars
            years = max(1, days // 252)  # ~252 trading days per year
            for ticker in tickers:
                try:
                    df = fetch_stock_data(ticker, years=years)
                    result[ticker] = df
                except Exception as e:
                    print(f"  [DHAN] Failed to fetch bars for {ticker}: {e}")
                    result[ticker] = pd.DataFrame()

        return result

    # ------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------

    def _sell_product_type(self, ticker: str):
        """Product type for SELL-side orders on a symbol.

        Delivery (CNC) holdings must be sold as CNC — an INTRA sell against
        a holding opens a fresh intraday short instead of selling the shares.
        """
        dhan = self._init_client()
        if strip_ns(ticker) in self._cnc_holdings:
            return dhan.CNC
        return dhan.INTRA if self.config.intraday else dhan.CNC

    def submit_buy(self, ticker: str, quantity: float) -> dict | None:
        """Submit a MKT buy order via Dhan. Returns order dict or None."""
        dhan = self._init_client()

        # Dhan requires integer quantity (NSE has no fractional shares)
        qty = int(quantity)
        if qty <= 0:
            return None
        if abs(quantity - qty) > 0.01:
            print(f"  [DHAN] Truncated fractional quantity {quantity:.2f} -> {qty} for {ticker}")

        try:
            sid = self._lookup_security_id(ticker)

            product_type = dhan.INTRA if self.config.intraday else dhan.CNC

            response = dhan.place_order(
                security_id=sid,
                exchange_segment=dhan.NSE,
                transaction_type=dhan.BUY,
                quantity=qty,
                order_type=dhan.MARKET,
                product_type=product_type,
                price=0,            # 0 = market price for MARKET orders
                validity=dhan.DAY,
            )

            # Response format: {"status": "success"|"failure", "data": {...}}
            if isinstance(response, dict) and response.get("status") == "failure":
                err = response.get("remarks", {})
                err_msg = err.get("error_message", str(err)) if isinstance(err, dict) else str(err)
                print(f"  [DHAN] Buy order rejected for {ticker}: {err_msg}")
                return None

            order_data = self._unwrap(response)
            if not isinstance(order_data, dict):
                order_data = {}

            avg_price = float(order_data.get("averagePrice",
                                   order_data.get("average_price", 0.0)))
            if avg_price <= 0:
                print(f"  [DHAN] Buy order for {ticker} accepted but NOT filled (avg_price={avg_price}). Skipping.")
                return None

            order_id = order_data.get("orderId", order_data.get("order_id", ""))
            return {
                "id": str(order_id),
                "symbol": ticker,
                "qty": qty,
                "side": "BUY",
                "product": "INTRA" if self.config.intraday else "CNC",
                "status": order_data.get("orderStatus", order_data.get("order_status", "PENDING")),
                "filled_avg_price": avg_price,
            }

        except Exception as e:
            print(f"  [DHAN] Buy order failed for {ticker}: {e}")
            return None

    def submit_sell(self, ticker: str, quantity: float) -> dict | None:
        """Submit a MKT sell order via Dhan. Returns order dict or None."""
        dhan = self._init_client()

        qty = int(quantity)
        if qty <= 0:
            return None
        if abs(quantity - qty) > 0.01:
            print(f"  [DHAN] Truncated fractional quantity {quantity:.2f} -> {qty} for {ticker}")

        try:
            sid = self._lookup_security_id(ticker)

            product_type = self._sell_product_type(ticker)

            response = dhan.place_order(
                security_id=sid,
                exchange_segment=dhan.NSE,
                transaction_type=dhan.SELL,
                quantity=qty,
                order_type=dhan.MARKET,
                product_type=product_type,
                price=0,            # 0 = market price for MARKET orders
                validity=dhan.DAY,
            )

            # Response format: {"status": "success"|"failure", "data": {...}}
            if isinstance(response, dict) and response.get("status") == "failure":
                err = response.get("remarks", {})
                err_msg = err.get("error_message", str(err)) if isinstance(err, dict) else str(err)
                print(f"  [DHAN] Sell order rejected for {ticker}: {err_msg}")
                return None

            order_data = self._unwrap(response)
            if not isinstance(order_data, dict):
                order_data = {}

            avg_price = float(order_data.get("averagePrice",
                                   order_data.get("average_price", 0.0)))
            if avg_price <= 0:
                print(f"  [DHAN] Sell order for {ticker} accepted but NOT filled (avg_price={avg_price}). Skipping.")
                return None

            order_id = order_data.get("orderId", order_data.get("order_id", ""))
            return {
                "id": str(order_id),
                "symbol": ticker,
                "qty": qty,
                "side": "SELL",
                "product": "INTRA" if self.config.intraday else "CNC",
                "status": order_data.get("orderStatus", order_data.get("order_status", "PENDING")),
                "filled_avg_price": avg_price,
            }

        except Exception as e:
            print(f"  [DHAN] Sell order failed for {ticker}: {e}")
            return None

    # ------------------------------------------------------------------
    # Stop-Loss / Take-Profit Orders (broker-level)
    # ------------------------------------------------------------------

    def submit_stop_loss(self, ticker: str, quantity: float, trigger_price: float) -> dict | None:
        """Submit a STOP_LOSS_MARKET sell order to protect a long position.

        The order triggers a market sell when the price drops to trigger_price.
        Placed as a DAY order with the same product type (INTRA/CNC) as the parent.
        """
        dhan = self._init_client()
        qty = int(quantity)
        if qty <= 0:
            return None

        trigger = round(trigger_price, 2)
        try:
            sid = self._lookup_security_id(ticker)
            product_type = self._sell_product_type(ticker)

            response = dhan.place_order(
                security_id=sid,
                exchange_segment=dhan.NSE,
                transaction_type=dhan.SELL,
                quantity=qty,
                order_type=dhan.SLM,
                product_type=product_type,
                price=0,                # market execution when triggered
                trigger_price=trigger,
                validity=dhan.DAY,
            )

            if isinstance(response, dict) and response.get("status") == "failure":
                err = response.get("remarks", {})
                err_msg = err.get("error_message", str(err)) if isinstance(err, dict) else str(err)
                print(f"  [DHAN] Stop-loss order rejected for {ticker}: {err_msg}")
                return None

            order_data = self._unwrap(response)
            if not isinstance(order_data, dict):
                order_data = {}
            order_id = order_data.get("orderId", order_data.get("order_id", ""))
            print(f"  [DHAN] SL   {ticker} x{qty} STOP_LOSS_MARKET @ trigger={trigger:.2f}  [id={order_id}]")
            return {
                "id": str(order_id),
                "symbol": ticker,
                "qty": qty,
                "side": "SELL",
                "type": "SLM",
                "trigger_price": trigger,
            }
        except Exception as e:
            print(f"  [DHAN] Stop-loss order failed for {ticker}: {e}")
            return None

    def submit_take_profit(self, ticker: str, quantity: float, limit_price: float) -> dict | None:
        """Submit a LIMIT sell order to take profit at a target price.

        The order sells at limit_price or better. Placed as a DAY order.
        """
        dhan = self._init_client()
        qty = int(quantity)
        if qty <= 0:
            return None

        limit = round(limit_price, 2)
        try:
            sid = self._lookup_security_id(ticker)
            product_type = self._sell_product_type(ticker)

            response = dhan.place_order(
                security_id=sid,
                exchange_segment=dhan.NSE,
                transaction_type=dhan.SELL,
                quantity=qty,
                order_type=dhan.LIMIT,
                product_type=product_type,
                price=limit,            # limit sell price
                validity=dhan.DAY,
            )

            if isinstance(response, dict) and response.get("status") == "failure":
                err = response.get("remarks", {})
                err_msg = err.get("error_message", str(err)) if isinstance(err, dict) else str(err)
                print(f"  [DHAN] Take-profit order rejected for {ticker}: {err_msg}")
                return None

            order_data = self._unwrap(response)
            if not isinstance(order_data, dict):
                order_data = {}
            order_id = order_data.get("orderId", order_data.get("order_id", ""))
            print(f"  [DHAN] TP   {ticker} x{qty} LIMIT @ {limit:.2f}  [id={order_id}]")
            return {
                "id": str(order_id),
                "symbol": ticker,
                "qty": qty,
                "side": "SELL",
                "type": "LIMIT",
                "limit_price": limit,
            }
        except Exception as e:
            print(f"  [DHAN] Take-profit order failed for {ticker}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True on success."""
        if not order_id:
            return False
        dhan = self._init_client()
        try:
            dhan.cancel_order(order_id)
            return True
        except Exception as e:
            print(f"  [DHAN] Failed to cancel order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> str:
        """Fetch the current status of an order (e.g. TRADED, PENDING, CANCELLED).

        Returns "" if the order cannot be looked up.
        """
        if not order_id:
            return ""
        dhan = self._init_client()
        try:
            data = self._unwrap(dhan.get_order_by_id(order_id))
            if isinstance(data, list):
                data = data[0] if data else {}
            if isinstance(data, dict):
                return str(data.get("orderStatus", data.get("order_status", ""))).upper()
        except Exception as e:
            print(f"  [DHAN] Could not fetch status of order {order_id}: {e}")
        return ""

    def cancel_sl_tp_orders(self, ticker: str) -> None:
        """Cancel both stop-loss and take-profit orders for a ticker."""
        base = strip_ns(ticker)

        sl_id = self._sl_orders.pop(base, None)
        if sl_id:
            if self.cancel_order(sl_id):
                print(f"  [DHAN] Cancelled SL order for {ticker} [id={sl_id}]")

        tp_id = self._tp_orders.pop(base, None)
        if tp_id:
            if self.cancel_order(tp_id):
                print(f"  [DHAN] Cancelled TP order for {ticker} [id={tp_id}]")

    def _place_sl_tp_orders(self, ticker: str, base: str, quantity: int, entry_price: float) -> None:
        """Place both stop-loss and take-profit orders after a BUY fill.

        Args:
            ticker: Full ticker with .NS suffix (e.g., "SBIN.NS")
            base: Base symbol without suffix (e.g., "SBIN")
            quantity: Position quantity (int)
            entry_price: Filled entry price
        """
        sl_price = entry_price * (1.0 - self.config.stop_loss_pct)
        tp_price = entry_price * (1.0 + self.config.take_profit_pct)

        sl_order = self.submit_stop_loss(ticker, quantity, sl_price)
        if sl_order:
            self._sl_orders[base] = sl_order["id"]
        else:
            print(f"  [DHAN] WARNING: Failed to place SL for {ticker} — relying on polling-based monitoring")

        tp_order = self.submit_take_profit(ticker, quantity, tp_price)
        if tp_order:
            self._tp_orders[base] = tp_order["id"]
        else:
            print(f"  [DHAN] WARNING: Failed to place TP for {ticker} — relying on polling-based monitoring")

    # Order statuses that mean the order is no longer working at the broker
    _DEAD_ORDER_STATUSES = frozenset({"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"})
    _PENDING_ORDER_STATUSES = frozenset({"TRANSIT", "PENDING", "TRIGGER_PENDING", "PART_TRADED"})

    def reconcile_sl_tp(self, portfolio: Portfolio, tickers: list[str]) -> None:
        """Reconcile broker-level SL/TP orders with the current portfolio.

        Runs once per cycle, right after sync_portfolio():

        1. Dangling sibling cleanup — if the broker executed the SL (or TP)
           between cycles, cancel the surviving sibling order and drop the
           position locally so strategies don't act on a closed position.
        2. Restart protection — if an open position has no tracked SL/TP
           (e.g. the bot was restarted), adopt matching pending SELL orders
           from the broker, or place fresh SL/TP orders around the average
           entry price.
        """
        # ── 1. Detect executed / dead tracked orders ───────────────
        for base in sorted(set(self._sl_orders) | set(self._tp_orders)):
            sl_id = self._sl_orders.get(base)
            tp_id = self._tp_orders.get(base)
            sl_status = self.get_order_status(sl_id) if sl_id else ""
            tp_status = self.get_order_status(tp_id) if tp_id else ""

            if sl_status == "TRADED":
                print(f"  [DHAN] Broker executed STOP-LOSS for {base} [id={sl_id}] — cancelling sibling TP")
                self._sl_orders.pop(base, None)
                self._tp_orders.pop(base, None)
                if tp_id and tp_status != "TRADED" and tp_status not in self._DEAD_ORDER_STATUSES:
                    self.cancel_order(tp_id)
                portfolio.positions.pop(base, None)
                self._notify(format_sl_msg(base, f"Broker SL order executed [id={sl_id}]", "broker"))
            elif tp_status == "TRADED":
                print(f"  [DHAN] Broker executed TAKE-PROFIT for {base} [id={tp_id}] — cancelling sibling SL")
                self._sl_orders.pop(base, None)
                self._tp_orders.pop(base, None)
                if sl_id and sl_status not in self._DEAD_ORDER_STATUSES:
                    self.cancel_order(sl_id)
                portfolio.positions.pop(base, None)
                self._notify(format_tp_msg(base, f"Broker TP order executed [id={tp_id}]", "broker"))
            else:
                # Drop tracking for orders that died on their own
                # (DAY orders expire at market close, manual cancels, rejects)
                if sl_id and sl_status in self._DEAD_ORDER_STATUSES:
                    self._sl_orders.pop(base, None)
                if tp_id and tp_status in self._DEAD_ORDER_STATUSES:
                    self._tp_orders.pop(base, None)
                # Position vanished without either order trading — closed
                # outside the bot. Cancel whatever is still working.
                if base not in portfolio.positions and (base in self._sl_orders or base in self._tp_orders):
                    print(f"  [DHAN] Position {base} closed outside the bot — cancelling leftover SL/TP")
                    self.cancel_sl_tp_orders(base)

        # ── 2. Ensure every open position is protected ─────────────
        unprotected: list[tuple[str, str, Position]] = []
        for ticker in tickers:
            base = strip_ns(ticker)
            pos = portfolio.positions.get(base)
            if pos is None or pos.quantity <= 0:
                continue
            if base in self._sl_orders or base in self._tp_orders:
                continue
            unprotected.append((ticker, base, pos))

        if not unprotected:
            return

        # Fetch the broker's order book once so we can adopt pending SELL
        # orders that were placed before a restart instead of duplicating them.
        pending_by_sid: dict[str, list[tuple[str, str]]] = {}
        dhan = self._init_client()
        try:
            orders = self._unwrap(dhan.get_order_list())
            if isinstance(orders, list):
                for o in orders:
                    if not isinstance(o, dict):
                        continue
                    status = str(o.get("orderStatus", o.get("order_status", ""))).upper()
                    txn = str(o.get("transactionType", o.get("transaction_type", ""))).upper()
                    if txn != "SELL" or status not in self._PENDING_ORDER_STATUSES:
                        continue
                    sid = str(o.get("securityId", o.get("security_id", "")))
                    otype = str(o.get("orderType", o.get("order_type", ""))).upper()
                    oid = str(o.get("orderId", o.get("order_id", "")))
                    if sid and oid:
                        pending_by_sid.setdefault(sid, []).append((otype, oid))
        except Exception as e:
            print(f"  [DHAN] Could not fetch order list for SL/TP adoption: {e}")

        for ticker, base, pos in unprotected:
            try:
                sid = self._lookup_security_id(ticker)
            except ValueError:
                sid = ""
            for otype, oid in pending_by_sid.get(sid, []):
                if "STOP_LOSS" in otype or otype in ("SLM", "SL"):
                    if base not in self._sl_orders:
                        self._sl_orders[base] = oid
                        print(f"  [DHAN] Adopted existing SL order for {base} [id={oid}]")
                elif otype == "LIMIT" and base not in self._tp_orders:
                    self._tp_orders[base] = oid
                    print(f"  [DHAN] Adopted existing TP order for {base} [id={oid}]")
            if base in self._sl_orders or base in self._tp_orders:
                continue

            print(f"  [DHAN] Position {base} has no SL/TP protection — placing orders")
            self._place_sl_tp_orders(ticker, base, int(pos.quantity), pos.avg_entry_price)

    # ------------------------------------------------------------------
    # Strategy Execution
    # ------------------------------------------------------------------

    def run_once(self, tickers: list[str]) -> dict:
        """Execute one evaluation cycle.

        1. Fetch latest bars for each ticker
        2. Evaluate strategy at the last bar
        3. Submit buy/sell orders based on signals

        Returns {ticker: signal_name} for logging.
        """
        dhan = self._init_client()

        # Fetch recent bars (last 120 days for strategy warmup)
        data = self.get_historical_bars(tickers, days=120)

        # Sync portfolio from broker
        portfolio = self.sync_portfolio()
        self.risk_manager.set_daily_start(portfolio.total_value)

        # Reconcile broker SL/TP orders: cancel dangling siblings after a
        # broker-side SL/TP fill, and protect positions that have no orders
        # (e.g. after a bot restart).
        self.reconcile_sl_tp(portfolio, tickers)

        # ── Intraday auto-square-off gate ──────────────────────────
        # At 3:10 PM IST, force-close ALL open positions (20 min before
        # market close). MIS positions must be squared off by 3:20 PM.
        if self.config.intraday:
            now = self._ist_now()
            minutes = now.hour * 60 + now.minute
            if minutes >= self.config.force_square_off_minutes:
                to_close = [t for t in tickers
                            if strip_ns(t) in portfolio.positions
                            and strip_ns(t) not in self._cnc_holdings]
                if to_close:
                    print(f"  [DHAN] AUTO-SQUARE-OFF ({now.strftime('%H:%M')} IST): Closing all positions...")
                for ticker in to_close:
                    base = strip_ns(ticker)
                    if base in portfolio.positions:
                        pos = portfolio.positions[base]
                        self.cancel_sl_tp_orders(ticker)
                        order = self.submit_sell(ticker, pos.quantity)
                        if order:
                            exit_price = order.get("filled_avg_price") or portfolio.current_prices.get(base, pos.avg_entry_price)
                            pnl = (exit_price - pos.avg_entry_price) * pos.quantity
                            print(f"  [DHAN] SQUARE-OFF {ticker} x{pos.quantity} @ ~{order.get('filled_avg_price', 'MKT')} | P&L: Rs {pnl:+.2f}")
                            portfolio.current_cash += order["qty"] * exit_price
                            del portfolio.positions[base]
                        else:
                            print(f"  [DHAN] SQUARE-OFF {ticker} FAILED — position may remain open!")
                # Return early — no new evaluations after square-off
                return {}

        # ── Intraday stop-buying gate ──────────────────────────────
        # After 3:00 PM IST, don't open new positions (only 30 min left).
        stop_buying = False
        if self.config.intraday:
            now = self._ist_now()
            minutes = now.hour * 60 + now.minute
            stop_buying = minutes >= self.config.stop_buying_minutes

        signals: dict[str, str] = {}

        for ticker in tickers:
            df = data.get(ticker)
            if df is None or df.empty:
                print(f"  [DHAN] No data for {ticker}, skipping")
                signals[ticker] = "NO_DATA"
                continue

            # Evaluate ALL strategies for this ticker
            idx = len(df) - 1
            price = df["close"].iloc[idx]
            base = strip_ns(ticker)
            # Key prices by base symbol — positions are keyed the same way,
            # so total_value picks up live marks instead of stale entries
            portfolio.update_price(base, price)

            # Pre-compute ATR (used for sizing AND intraday volatility filter)
            atr_val = None
            if self.config.intraday or self.config.use_atr_sizing:
                atr_arr = compute_atr(df, self.config.atr_period)
                atr_val = (
                    atr_arr[idx]
                    if idx < len(atr_arr) and not pd.isna(atr_arr[idx])
                    else None
                )

            # Volatility filter for intraday — check once per ticker, not per strategy
            low_vol = False
            if self.config.intraday and atr_val and atr_val > 0 and price > 0:
                vol_pct = atr_val / price
                if vol_pct < self.config.min_volatility_pct:
                    low_vol = True
                    print(f"  [DHAN] Low volatility for {ticker}: ATR/close={vol_pct:.3%} < {self.config.min_volatility_pct:.2%} — skipping BUYs")

            # Track which strategies signaled this ticker
            per_strategy_signals: list[str] = []

            for strat in self.strategies:
                # Pre-compute indicators (each strategy has its own prepare)
                strat.prepare(df)
                result: StrategyResult = strat.evaluate(df, idx)
                per_strategy_signals.append(f"{strat.name}={result.signal.name}")

            signals[ticker] = " | ".join(per_strategy_signals)

            # Execute trades — iterate all strategies. Once any strategy
            # takes action (BUY or SELL), stop processing further strategies
            # for this ticker in this cycle to prevent churning (e.g. one
            # strategy buying and another immediately selling at same price).
            for strat in self.strategies:
                # Re-evaluate to get fresh result for this strategy
                result: StrategyResult = strat.evaluate(df, idx)

                if result.signal == Signal.BUY:
                    # Already holding?
                    if base in portfolio.positions:
                        continue

                    # Intraday gate: stop buying after 3:00 PM
                    if stop_buying:
                        print(f"  [DHAN] BUY skipped for {ticker} [{strat.name}]: Past 3:00 PM — no new positions")
                        continue

                    # Volatility filter: skip if market is dead (checked once per ticker)
                    if low_vol:
                        continue

                    # Size the position
                    max_value = portfolio.total_value * self.risk_manager.max_allocation_pct
                    if self.config.use_atr_sizing and atr_val and atr_val > 0:
                        risk_amount = portfolio.total_value * self.config.position_risk_pct
                        stop_distance = atr_val * self.config.atr_multiplier
                        quantity = min(risk_amount / stop_distance, max_value / price)
                    else:
                        quantity = max_value / price

                    risk = self.risk_manager.check_buy(portfolio, base, quantity, price)
                    if not risk.allowed:
                        print(f"  [DHAN] BUY blocked for {ticker} [{strat.name}]: {risk.reason}")
                        continue

                    order = self.submit_buy(ticker, quantity)
                    if order:
                        print(
                            f"  [DHAN] BUY  {ticker} x{order['qty']} "
                            f"@ ~{order.get('filled_avg_price', 'MKT')}  [{strat.name}]"
                        )
                        self._notify(
                            format_buy_msg(
                                ticker, order["qty"], order.get("filled_avg_price"),
                                strat.name, portfolio.total_value
                            )
                        )
                        # Mark position in portfolio so next strategy sees it,
                        # and debit cash so total_value stays invariant across
                        # the fill (otherwise the daily-loss limiter mis-fires).
                        entry_price = order.get("filled_avg_price") or price
                        portfolio.current_cash -= order["qty"] * entry_price
                        portfolio.positions[base] = Position(
                            ticker=base,
                            quantity=order["qty"],
                            avg_entry_price=entry_price,
                            entry_date=datetime.now(),
                        )
                        # Place broker-level stop-loss and take-profit orders
                        self._place_sl_tp_orders(ticker, base, order["qty"], entry_price)
                        # Stop processing further strategies — position just opened
                        break

                elif result.signal == Signal.SELL:
                    pos = portfolio.positions.get(base)
                    if pos is None or pos.quantity <= 0:
                        continue

                    # Cancel SL/TP orders before selling (they're no longer needed)
                    self.cancel_sl_tp_orders(ticker)

                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(
                            f"  [DHAN] SELL {ticker} x{order['qty']} "
                            f"@ ~{order.get('filled_avg_price', 'MKT')}  [{strat.name}]"
                        )
                        # Calculate P&L if possible
                        pnl = None
                        exit_price = order.get("filled_avg_price")
                        if exit_price and pos.avg_entry_price > 0:
                            pnl = (exit_price - pos.avg_entry_price) * order["qty"]
                        self._notify(
                            format_sell_msg(
                                ticker, order["qty"], order.get("filled_avg_price"),
                                strat.name, pnl
                            )
                        )
                        # Credit cash and remove from portfolio (keeps
                        # total_value invariant across the fill)
                        portfolio.current_cash += order["qty"] * (exit_price or price)
                        del portfolio.positions[base]
                        # Stop processing further strategies — position just closed
                        break

            # Check stop-loss / take-profit
            if base in portfolio.positions:
                risk = self.risk_manager.check_sell(portfolio, base, price)
                if risk.action == RiskAction.STOP_LOSS:
                    pos = portfolio.positions[base]
                    self.cancel_sl_tp_orders(ticker)
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [DHAN] STOP-LOSS {ticker}: {risk.reason}")
                        portfolio.current_cash += order["qty"] * (order.get("filled_avg_price") or price)
                        del portfolio.positions[base]
                        self._notify(
                            format_sl_msg(ticker, risk.reason, self.strategy.name)
                        )
                elif risk.action == RiskAction.TAKE_PROFIT:
                    pos = portfolio.positions[base]
                    self.cancel_sl_tp_orders(ticker)
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [DHAN] TAKE-PROFIT {ticker}: {risk.reason}")
                        portfolio.current_cash += order["qty"] * (order.get("filled_avg_price") or price)
                        del portfolio.positions[base]
                        self._notify(
                            format_tp_msg(ticker, risk.reason, self.strategy.name)
                        )
                elif risk.action == RiskAction.TRAILING_STOP:
                    pos = portfolio.positions[base]
                    self.cancel_sl_tp_orders(ticker)
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [DHAN] TRAILING-STOP {ticker}: {risk.reason}")
                        portfolio.current_cash += order["qty"] * (order.get("filled_avg_price") or price)
                        del portfolio.positions[base]
                        self._notify(
                            format_sell_msg(
                                ticker, order["qty"], order.get("filled_avg_price"),
                                f"TrailingStop",
                                (order.get("filled_avg_price", 0) - pos.avg_entry_price) * order["qty"]
                                if order.get("filled_avg_price") and pos.avg_entry_price > 0 else None
                            )
                        )

            # ── Time-based exit for stale intraday positions ─────────
            # If a position has been held too long with minimal profit,
            # exit to free up capital for better opportunities.
            if self.config.intraday and base in portfolio.positions and base not in self._cnc_holdings:
                pos = portfolio.positions[base]
                hold_minutes = (datetime.now() - pos.entry_date).total_seconds() / 60.0
                if hold_minutes >= self.config.max_hold_minutes:
                    pnl_pct = (price - pos.avg_entry_price) / pos.avg_entry_price if pos.avg_entry_price > 0 else 0.0
                    if pnl_pct < self.config.min_profit_threshold_pct:
                        self.cancel_sl_tp_orders(ticker)
                        order = self.submit_sell(ticker, pos.quantity)
                        if order:
                            pnl = (order.get("filled_avg_price", 0) - pos.avg_entry_price) * pos.quantity if order.get("filled_avg_price") and pos.avg_entry_price > 0 else None
                            print(f"  [DHAN] TIME-EXIT {ticker}: Held {hold_minutes:.0f}m, "
                                  f"P&L {pnl_pct:+.2%} < {self.config.min_profit_threshold_pct:.1%} threshold")
                            portfolio.current_cash += order["qty"] * (order.get("filled_avg_price") or price)
                            del portfolio.positions[base]
                            self._notify(
                                format_sell_msg(
                                    ticker, order["qty"], order.get("filled_avg_price"),
                                    f"TimeExit_{hold_minutes:.0f}m", pnl
                                )
                            )

        return signals

    # ------------------------------------------------------------------
    # Market hours
    # ------------------------------------------------------------------

    @staticmethod
    def _ist_now() -> datetime:
        """Return current datetime in Asia/Kolkata (IST)."""
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime.now(ist)

    @staticmethod
    def _is_market_open() -> bool:
        """Check if NSE is open right now (Mon-Fri, 09:15-15:30 IST)."""
        now = DhanLiveTrader._ist_now()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        minutes = now.hour * 60 + now.minute
        return 555 <= minutes < 930  # 09:15 = 555 min, 15:30 = 930 min

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, tickers: list[str], once: bool = True) -> None:
        """Execute the Dhan live trading loop.

        Args:
            tickers: List of ticker symbols to trade (e.g., ["SBIN.NS", "ICICIBANK.NS"]).
            once: If True, run one evaluation cycle and return.
                  If False, loop forever at poll_interval_seconds.
        """
        dhan = self._init_client()
        mode = getattr(self, "_mode_label", "") or ("SANDBOX" if self.config.sandbox else "LIVE")

        # Print account summary
        try:
            account = self.get_account_info()
            print(f"\n=== Dhan Live Trader ({mode}) ===")
            print(f"  Balance:      Rs {account['equity']:,.2f}")
            print(f"  Available:    Rs {account['cash']:,.2f}")
            if account.get("used_margin", 0) > 0:
                print(f"  Used Margin:  Rs {account['used_margin']:,.2f}")
            strategy_names = ', '.join(s.name for s in self.strategies)
            print(f"  Strategies:   {len(self.strategies)} ({strategy_names})")
            print(f"  Tickers:      {', '.join(tickers)}")
            if self.config.intraday:
                print(f"  Interval:     {self.config.intraday_interval} (INTRADAY — MIS orders)")
                print(f"  Auto-exit:    {self.config.max_hold_minutes}min stale | Stop-buy: 15:00 | Square-off: 15:10")
                print(f"  Vol filter:   ATR/close >= {self.config.min_volatility_pct:.1%}")
            print()
        except Exception as e:
            print(f"\n=== Dhan Live Trader ({mode}) === (balance unavailable: {e})\n")
            strategy_names = ', '.join(s.name for s in self.strategies)
            print(f"  Strategies: {len(self.strategies)} ({strategy_names})")
            print(f"  Tickers:   {', '.join(tickers)}")
            if self.config.intraday:
                print(f"  Interval:  {self.config.intraday_interval} (INTRADAY — MIS orders)")
                print(f"  Auto-exit:  {self.config.max_hold_minutes}min stale | Stop-buy: 15:00 | Square-off: 15:10")
            print()

        if once:
            print("  Running one evaluation cycle...")
            signals = self.run_once(tickers)
            if signals:
                print("\n  --- Strategy Signals ---")
                for ticker, sig in signals.items():
                    print(f"    {ticker}: {sig}")
            print("\n  Done.")
        else:
            import time

            if self.config.intraday:
                print(f"  Polling every {self.config.poll_interval_seconds}s during market hours "
                      f"(09:15-15:30 IST, Mon-Fri).")
            else:
                print(f"  Polling every {self.config.poll_interval_seconds}s. "
                      "Press Ctrl+C to stop.")

            # Send session-start notification
            strategy_names = ', '.join(s.name for s in self.strategies)
            self._notify(
                format_session_start(
                    mode, strategy_names, tickers,
                    self.get_account_info().get("equity", 0.0)
                )
            )

            waiting_logged = False
            market_was_open = False
            try:
                while True:
                    if not self._is_market_open():
                        now = self._ist_now()
                        # Market just closed after a trading session — send the
                        # end-of-day summary once, before going quiet.
                        if market_was_open:
                            try:
                                self._send_daily_summary()
                            except Exception as e:
                                print(f"  [summary] failed: {e}")
                            market_was_open = False
                        # Log the "waiting" line once per closed-market stretch,
                        # not every minute, so the console isn't flooded overnight.
                        if not waiting_logged:
                            print(f"  [{now.strftime('%H:%M')}] Market closed — waiting for 09:15 IST...")
                            waiting_logged = True
                        time.sleep(60)  # check every minute
                        continue
                    waiting_logged = False
                    market_was_open = True
                    signals = self.run_once(tickers)
                    # Heartbeat: continuous mode is otherwise silent when every
                    # strategy says HOLD, which looks like the bot has frozen.
                    now = self._ist_now()
                    notable = [f"{t}=[{s}]" for t, s in (signals or {}).items()
                               if s and ("BUY" in s or "SELL" in s)]
                    if notable:
                        print(f"  [{now.strftime('%H:%M')}] " + "  ".join(notable))
                    else:
                        n = len(signals or {})
                        print(f"  [{now.strftime('%H:%M')}] cycle ok — {n} ticker(s) evaluated, no entries (all HOLD)")
                    time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("\n  Dhan live trader stopped by user.")

    # ------------------------------------------------------------------
    # End-of-day summary
    # ------------------------------------------------------------------

    def _send_daily_summary(self) -> None:
        """Send an end-of-day summary via the notifier.

        Base implementation reports the account equity. DhanPaperTrader
        overrides this with full trade statistics from its virtual ledger.
        """
        acct = self.get_account_info()
        equity = acct.get("equity", 0.0)
        day = self._ist_now().strftime("%Y-%m-%d")
        msg = format_daily_summary(
            getattr(self, "_mode_label", "") or ("SANDBOX" if self.config.sandbox else "LIVE"),
            day, total_trades=0, closed_trades=0, wins=0, realized_pnl=0.0,
            equity=equity, initial_capital=self.config.initial_capital, open_positions=[],
        )
        print(f"  [SUMMARY] {day}: equity Rs {equity:,.2f}")
        self._notify(msg)
