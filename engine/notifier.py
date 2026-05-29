"""
Notification module — sends trading alerts via Telegram.

Two backends:
  1. TelegramNotifier (Bot API) — requires @BotFather bot token + chat ID.
  2. TelegramUserNotifier (Telethon) — sends as your own user account
     to "Saved Messages". No bot needed! Requires API_ID + API_HASH
     from https://my.telegram.org/apps (first run prompts for phone number).

Usage:
    from engine.notifier import TelegramNotifier, TelegramUserNotifier

    # Option A: Bot API (if you have a bot)
    notifier = TelegramNotifier(bot_token="...", chat_id="...")

    # Option B: User account (no BotFather needed!)
    notifier = TelegramUserNotifier(api_id=123456, api_hash="abc123...")

    notifier.send("🚀 BUY SBIN.NS x10 @ ~₹850")
"""

import os
from datetime import datetime
from typing import Optional


class TelegramNotifier:
    """Fire-and-forget Telegram message sender via Bot API.

    Requires a bot token from @BotFather and a chat ID.

    Args:
        bot_token: Telegram bot token from @BotFather.
        chat_id: Telegram chat ID to send messages to.
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def send(self, message: str) -> bool:
        """Send a message via Telegram Bot API. Returns True if sent, False if disabled/failed.

        Non-blocking — exceptions are silently caught.
        """
        if not self._enabled:
            return False

        try:
            import requests
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message}
            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False


class TelegramUserNotifier:
    """Fire-and-forget Telegram message sender — as your own user account.

    Uses Telethon to authenticate as your Telegram user (not a bot) and
    sends messages to your own "Saved Messages" chat.

    This bypasses @BotFather entirely — you only need:
      - API_ID and API_HASH from https://my.telegram.org/apps

    First run prompts for your phone number + verification code,
    then saves a .session file so subsequent runs skip auth.

    Args:
        api_id: Telegram API ID from my.telegram.org.
        api_hash: Telegram API hash from my.telegram.org.
        session_file: Path to session file (default: dhan_trader.session).
    """

    def __init__(self, api_id: int = 0, api_hash: str = "",
                 session_file: str = "dhan_trader"):
        self.api_id = api_id
        self.api_hash = api_hash
        # Store session in the project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.session_file = os.path.join(project_root, session_file)
        self._enabled = bool(api_id and api_hash)
        self._client = None

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _get_client(self):
        """Lazily create and connect a Telethon client."""
        if self._client is not None:
            return self._client

        from telethon import TelegramClient
        self._client = TelegramClient(self.session_file, self.api_id, self.api_hash)
        return self._client

    async def _async_send(self, message: str) -> bool:
        """Async send message to Saved Messages."""
        client = self._get_client()
        await client.start()  # no-op if session is valid; prompts if first run
        await client.send_message("me", message)
        return True

    def send(self, message: str) -> bool:
        """Send a message to your Saved Messages via Telethon.

        First call will prompt for phone number + verification code
        in the terminal. Subsequent calls reuse the saved session.

        Returns True if sent, False if disabled/failed.
        """
        if not self._enabled:
            return False

        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._async_send(message))
            loop.close()
            return result
        except Exception as e:
            print(f"  [NOTIFIER] Telegram send failed: {e}")
            return False


# ---------------------------------------------------------------------------
# Message formatting helpers
# ---------------------------------------------------------------------------

def format_buy_msg(ticker: str, qty: int, price: Optional[float], strategy: str,
                   balance: float) -> str:
    """Format a BUY signal notification."""
    price_str = f"Rs {price:,.2f}" if price else "MKT"
    return (
        f"🚀 *BUY* {ticker} x{qty} @ ~{price_str}\\n"
        f"Strategy: {strategy}\\n"
        f"Balance: Rs {balance:,.2f}"
    )


def format_sell_msg(ticker: str, qty: int, price: Optional[float], strategy: str,
                    pnl: Optional[float] = None) -> str:
    """Format a SELL signal notification."""
    price_str = f"Rs {price:,.2f}" if price else "MKT"
    pnl_str = f" | P&L: Rs {pnl:+,.2f}" if pnl is not None else ""
    return (
        f"📉 *SELL* {ticker} x{qty} @ ~{price_str}{pnl_str}\\n"
        f"Strategy: {strategy}"
    )


def format_sl_msg(ticker: str, reason: str, strategy: str) -> str:
    """Format a STOP-LOSS notification."""
    return (
        f"🛑 *STOP-LOSS* {ticker}\\n"
        f"Reason: {reason}\\n"
        f"Strategy: {strategy}"
    )


def format_tp_msg(ticker: str, reason: str, strategy: str) -> str:
    """Format a TAKE-PROFIT notification."""
    return (
        f"✅ *TAKE-PROFIT* {ticker}\\n"
        f"Reason: {reason}\\n"
        f"Strategy: {strategy}"
    )


def format_session_start(mode: str, strategy: str, tickers: list[str],
                         balance: float) -> str:
    """Format a session-start notification."""
    ticker_str = ", ".join(tickers)
    return (
        f"🤖 *Dhan Live Trader Started*\\n"
        f"Mode: {mode}\\n"
        f"Strategy: {strategy}\\n"
        f"Tickers: {ticker_str}\\n"
        f"Balance: Rs {balance:,.2f}\\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )


def format_error(msg: str) -> str:
    """Format an error notification."""
    return f"⚠️ *Error*: {msg}"
