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
    format_error,
)
from strategies.base import BaseStrategy, Signal, StrategyResult


# ---------------------------------------------------------------------------
# Security ID mapping for NSE stocks
# Pre-populated with our 10 India tickers.
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
    take_profit_pct: float = 0.10
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
                        "symbol": p.get("trading_symbol", ""),
                        "security_id": str(p.get("security_id", "")),
                        "qty": int(p.get("net_qty", 0)),
                        "avg_entry_price": float(p.get("average_price", 0.0)),
                        "ltp": float(p.get("last_price", 0.0)),
                        "unrealized_pl": float(p.get("unrealized_profit", 0.0)),
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
                        "symbol": h.get("trading_symbol", ""),
                        "security_id": str(h.get("security_id", "")),
                        "qty": int(h.get("total_qty", 0)),
                        "avg_entry_price": float(h.get("average_price", 0.0)),
                        "ltp": float(h.get("last_price", 0.0)),
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

        try:
            for p in self._unwrap(dhan.get_positions()) or []:
                if not isinstance(p, dict):
                    continue
                sym = p.get("trading_symbol", "")
                qty = int(p.get("net_qty", 0))
                if qty <= 0:
                    continue
                all_positions[sym] = {
                    "symbol": sym,
                    "qty": qty,
                    "avg_entry_price": float(p.get("average_price", 0.0)),
                }
        except Exception:
            pass

        try:
            for h in self._unwrap(dhan.get_holdings()) or []:
                if not isinstance(h, dict):
                    continue
                sym = h.get("trading_symbol", "")
                qty = int(h.get("total_qty", 0))
                if qty <= 0:
                    continue
                if sym in all_positions:
                    # Merge: add holdings qty to positions qty
                    all_positions[sym]["qty"] += qty
                else:
                    all_positions[sym] = {
                        "symbol": sym,
                        "qty": qty,
                        "avg_entry_price": float(h.get("average_price", 0.0)),
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
        from data.stocks import fetch_stock_data

        result: dict[str, pd.DataFrame] = {}

        if self.config.intraday:
            # Intraday: fetch 7 calendar days of N-minute bars (enough for
            # all strategy warmups — 7 days of 5m = ~525 bars). yfinance
            # limits 1m=7d, 5m/15m/30m=60d, 1h=730d. We use 7d for all
            # to stay well within limits and keep DataFrames fast.
            interval = self.config.intraday_interval
            fetch_years = 7.0 / 365.0  # 7 calendar days

            for ticker in tickers:
                try:
                    df = fetch_stock_data(ticker, years=fetch_years, interval=interval)
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

            product_type = dhan.INTRA if self.config.intraday else dhan.CNC

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

        # ── Intraday auto-square-off gate ──────────────────────────
        # At 3:10 PM IST, force-close ALL open positions (20 min before
        # market close). MIS positions must be squared off by 3:20 PM.
        if self.config.intraday:
            now = self._ist_now()
            minutes = now.hour * 60 + now.minute
            if minutes >= self.config.force_square_off_minutes:
                print(f"  [DHAN] AUTO-SQUARE-OFF ({now.strftime('%H:%M')} IST): Closing all positions...")
                for ticker in list(tickers):
                    base = strip_ns(ticker)
                    if base in portfolio.positions:
                        pos = portfolio.positions[base]
                        order = self.submit_sell(ticker, pos.quantity)
                        if order:
                            pnl = (order.get("filled_avg_price", 0) - pos.avg_entry_price) * pos.quantity
                            print(f"  [DHAN] SQUARE-OFF {ticker} x{pos.quantity} @ ~{order.get('filled_avg_price', 'MKT')} | P&L: Rs {pnl:+.2f}")
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
            portfolio.update_price(ticker, price)

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
                    print(f"  [DHAN] Low volatility for {ticker}: ATR/close={vol_pct:.3%} < {self.config.min_volatility_pct:.1%} — skipping BUYs")

            # Track which strategies signaled this ticker
            per_strategy_signals: list[str] = []
            base = strip_ns(ticker)

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
                        # Mark position in portfolio so next strategy sees it
                        portfolio.positions[base] = Position(
                            ticker=base,
                            quantity=order["qty"],
                            avg_entry_price=order.get("filled_avg_price") or price,
                            entry_date=datetime.now(),
                        )
                        # Stop processing further strategies — position just opened
                        break

                elif result.signal == Signal.SELL:
                    pos = portfolio.positions.get(base)
                    if pos is None or pos.quantity <= 0:
                        continue

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
                        # Remove from portfolio
                        del portfolio.positions[base]
                        # Stop processing further strategies — position just closed
                        break

            # Check stop-loss / take-profit
            if base in portfolio.positions:
                risk = self.risk_manager.check_sell(portfolio, base, price)
                if risk.action == RiskAction.STOP_LOSS:
                    pos = portfolio.positions[base]
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [DHAN] STOP-LOSS {ticker}: {risk.reason}")
                        del portfolio.positions[base]
                        self._notify(
                            format_sl_msg(ticker, risk.reason, self.strategy.name)
                        )
                elif risk.action == RiskAction.TAKE_PROFIT:
                    pos = portfolio.positions[base]
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [DHAN] TAKE-PROFIT {ticker}: {risk.reason}")
                        del portfolio.positions[base]
                        self._notify(
                            format_tp_msg(ticker, risk.reason, self.strategy.name)
                        )
                elif risk.action == RiskAction.TRAILING_STOP:
                    pos = portfolio.positions[base]
                    order = self.submit_sell(ticker, pos.quantity)
                    if order:
                        print(f"  [DHAN] TRAILING-STOP {ticker}: {risk.reason}")
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
            if self.config.intraday and base in portfolio.positions:
                pos = portfolio.positions[base]
                hold_minutes = (datetime.now() - pos.entry_date).total_seconds() / 60.0
                if hold_minutes >= self.config.max_hold_minutes:
                    pnl_pct = (price - pos.avg_entry_price) / pos.avg_entry_price if pos.avg_entry_price > 0 else 0.0
                    if pnl_pct < self.config.min_profit_threshold_pct:
                        order = self.submit_sell(ticker, pos.quantity)
                        if order:
                            pnl = (order.get("filled_avg_price", 0) - pos.avg_entry_price) * pos.quantity if order.get("filled_avg_price") and pos.avg_entry_price > 0 else None
                            print(f"  [DHAN] TIME-EXIT {ticker}: Held {hold_minutes:.0f}m, "
                                  f"P&L {pnl_pct:+.2%} < {self.config.min_profit_threshold_pct:.1%} threshold")
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
        mode = "SANDBOX" if self.config.sandbox else "LIVE"

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

            try:
                while True:
                    if not self._is_market_open():
                        now = self._ist_now()
                        print(f"  [{now.strftime('%H:%M')}] Market closed — waiting...")
                        time.sleep(60)  # check every minute
                        continue
                    self.run_once(tickers)
                    time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("\n  Dhan live trader stopped by user.")
