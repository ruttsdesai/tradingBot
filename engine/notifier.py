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

    async def _async_send(self, message: str) -> bool:
        """Connect a fresh client, send to Saved Messages, disconnect cleanly.

        A brand-new client per call keeps each send self-contained: it never
        reuses a client bound to an already-closed event loop (which broke the
        2nd+ send of a session), and disconnecting before the loop closes stops
        Telethon's background tasks from spewing 'Event loop is closed' noise on
        teardown (notably on Python 3.14).
        """
        from telethon import TelegramClient

        client = TelegramClient(self.session_file, self.api_id, self.api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                # No valid session yet — do the interactive login. This only
                # happens during `telegram-setup`; runtime sends skip it.
                await client.start()
            await client.send_message("me", message)
            return True
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    def send(self, message: str) -> bool:
        """Send a message to your Saved Messages via Telethon.

        First call (during telegram-setup) prompts for phone number +
        verification code in the terminal. Later calls reuse the saved session.

        Returns True if sent, False if disabled/failed.
        """
        if not self._enabled:
            return False

        loop = None
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self._async_send(message))
        except Exception as e:
            print(f"  [NOTIFIER] Telegram send failed: {e}")
            return False
        finally:
            if loop is not None:
                # Let disconnect()'s cancelled tasks settle before closing the
                # loop, so teardown stays quiet.
                try:
                    loop.run_until_complete(asyncio.sleep(0))
                except Exception:
                    pass
                loop.close()


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


def format_daily_summary(mode: str, day: str, total_trades: int, closed_trades: int,
                         wins: int, realized_pnl: float, equity: float,
                         initial_capital: float, open_positions: list[str]) -> str:
    """Format an end-of-day summary notification."""
    win_rate = (wins / closed_trades * 100) if closed_trades else 0.0
    total_ret = ((equity - initial_capital) / initial_capital * 100) if initial_capital else 0.0
    pnl_emoji = "🟢" if realized_pnl > 0 else ("🔴" if realized_pnl < 0 else "⚪")
    pos_str = ", ".join(open_positions) if open_positions else "none (flat)"
    return (
        f"{pnl_emoji} *Day Summary ({mode})* — {day}\\n"
        f"Realized P&L: Rs {realized_pnl:+,.2f}\\n"
        f"Trades: {total_trades} ({closed_trades} closed, {win_rate:.0f}% win)\\n"
        f"Equity: Rs {equity:,.2f} ({total_ret:+.2f}% since start)\\n"
        f"Open overnight: {pos_str}"
    )
