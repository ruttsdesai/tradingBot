"""Quick position & P&L checker for Dhan live account."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(override=True)

from engine.dhan_live_trader import DhanLiveTrader, DhanLiveTraderConfig
from strategies.base import BaseStrategy, Signal, StrategyResult


class DummyStrategy(BaseStrategy):
    name = "Dummy"
    def prepare(self, df): pass
    def evaluate(self, df, idx):
        return StrategyResult(signal=Signal.HOLD, reason="n/a")


config = DhanLiveTraderConfig(
    client_id=os.environ.get("DHAN_CLIENT_ID", ""),
    access_token=os.environ.get("DHAN_ACCESS_TOKEN", ""),
    sandbox=False,
    initial_capital=100000,
    max_positions=10,
    max_allocation_pct=0.50,
    stop_loss_pct=0.05,
    take_profit_pct=0.15,
    poll_interval_seconds=60,
    intraday=True,
    intraday_interval="5m",
)

trader = DhanLiveTrader(config, strategies=[DummyStrategy()])

# --- Account ---
acct = trader.get_account_info()
print("=== Dhan Account (LIVE) ===")
print(f"Balance:       Rs {acct['equity']:,.2f}")
print(f"Available:     Rs {acct['cash']:,.2f}")
if acct.get("used_margin", 0) > 0:
    print(f"Used Margin:   Rs {acct['used_margin']:,.2f}")
print()

# --- Positions + Holdings ---
positions = trader.get_positions()
holdings = trader.get_holdings()

if not positions and not holdings:
    print("No open positions or holdings.")
else:
    all_items = []
    for p in positions:
        sym = p["symbol"]
        qty = p["qty"]
        entry = p["avg_entry_price"]
        ltp = p.get("ltp", 0)
        if qty > 0:
            try:
                fresh = trader.get_latest_price(sym + ".NS")
                if fresh > 0:
                    ltp = fresh
            except Exception:
                pass
            upl = (ltp - entry) * qty
            all_items.append(("POS", sym, qty, entry, ltp, upl))

    for h in holdings:
        sym = h["symbol"]
        qty = h["qty"]
        entry = h["avg_entry_price"]
        ltp = h.get("ltp", 0)
        if qty > 0:
            try:
                fresh = trader.get_latest_price(sym + ".NS")
                if fresh > 0:
                    ltp = fresh
            except Exception:
                pass
            upl = (ltp - entry) * qty
            all_items.append(("HLD", sym, qty, entry, ltp, upl))

    if all_items:
        header = f"{'Type':<5} {'Symbol':<16} {'Qty':>6} {'Entry':>10} {'LTP':>10} {'UPL':>12} {'UPL%':>8}"
        print(header)
        print("-" * 72)
        total_upl = 0.0
        total_val = 0.0
        for typ, sym, qty, entry, ltp, upl in all_items:
            pct = ((ltp - entry) / entry * 100) if entry > 0 else 0.0
            print(f"{typ:<5} {sym:<16} {qty:>6} {entry:>10.2f} {ltp:>10.2f} {upl:>+12.2f} {pct:>+7.2f}%")
            total_upl += upl
            total_val += qty * ltp
        print("-" * 72)
        print(f"{'':>5} {'TOTAL':<16} {'':>6} {'':>10} {'':>10} {total_upl:>+12.2f}")
        print(f"\nTotal Position Value: Rs {total_val:,.2f}")
        print(f"Total Unrealized P&L:  Rs {total_upl:+,.2f}")
