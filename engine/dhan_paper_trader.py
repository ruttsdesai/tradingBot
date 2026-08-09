"""
Dhan Forward Paper Trader — the dhan-live loop with simulated fills.

Runs the exact same evaluation cycle as DhanLiveTrader (live NSE prices via
yfinance, same strategies, risk manager, market-hours gating, square-off and
time-exit rules) but never touches the broker:

  - BUY/SELL fills are simulated at the last evaluated price
  - the virtual portfolio (cash, positions, trade log) persists to
    state/dhan_paper_state.json, so restarts and container recycling are safe
  - broker-level SL/TP plumbing is disabled; the risk manager's software
    stop-loss / take-profit / trailing-stop monitoring performs all exits

No Dhan credentials are required. Use for zero-cost forward testing:

    python cli.py dhan-live --paper --all
    python cli.py paper-status          # inspect the virtual portfolio
"""

import json
import os
from datetime import datetime

from .dhan_live_trader import DhanLiveTrader, DhanLiveTraderConfig, strip_ns
from .portfolio import Portfolio, Position

_DEFAULT_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "dhan_paper_state.json",
)


class DhanPaperTrader(DhanLiveTrader):
    """Forward paper-trading engine: live data, virtual fills, zero orders."""

    # Per-side cost applied to every simulated fill: brokerage + STT +
    # exchange txn + GST + stamp duty, plus a little slippage. ~0.05%/side
    # is a realistic Dhan intraday figure, i.e. ~0.1% round trip. Without
    # this, paper P&L is GROSS and flatters a high-frequency strategy —
    # which is exactly how the churn problem stayed hidden for days.
    COST_PCT_PER_SIDE = 0.0005

    # --- Dhan intraday (MIS) charges, for the size-aware cost model ---------
    # Brokerage is min(Rs20, 0.03%) PER ORDER, so it CAPS above ~Rs66,667 of
    # notional and stops scaling with size. Everything else stays proportional.
    # Net effect: round-trip cost falls from ~0.106% at Rs16k positions to
    # ~0.059% at Rs200k — a 45% cut in drag from position size alone. The flat
    # COST_PCT_PER_SIDE above happens to be accurate at the CURRENT Rs16k size
    # (0.053%/side), which is why it stays the default: switching models
    # mid-experiment would break comparability with the pooled 07-27..07-30 days.
    BROKERAGE_PCT = 0.0003        # 0.03% per order...
    BROKERAGE_CAP = 20.0          # ...capped at Rs20 per order
    STT_PCT_SELL = 0.00025        # 0.025%, sell side only
    EXCHANGE_PCT = 0.0000297      # both sides
    GST_PCT = 0.18                # on brokerage + exchange charges
    STAMP_PCT_BUY = 0.00003       # 0.003%, buy side only

    def _side_cost(self, notional: float, side: str) -> float:
        """Cost in rupees for one leg of `notional` value.

        Uses the real Dhan charge stack when `size_aware_costs` is on, else the
        flat COST_PCT_PER_SIDE. Returns rupees, not a percentage, because the
        brokerage cap makes the percentage size-dependent.
        """
        if not getattr(self, "_size_aware_costs", False):
            return notional * self.COST_PCT_PER_SIDE
        brokerage = min(self.BROKERAGE_CAP, notional * self.BROKERAGE_PCT)
        exchange = notional * self.EXCHANGE_PCT
        gst = (brokerage + exchange) * self.GST_PCT
        extra = (notional * self.STT_PCT_SELL if side == "SELL"
                 else notional * self.STAMP_PCT_BUY)
        return brokerage + exchange + gst + extra

    def __init__(self, *args, state_file: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._mode_label = "PAPER"
        self._size_aware_costs = getattr(self.config, "size_aware_costs", False)
        self._state_file = state_file or _DEFAULT_STATE_FILE
        self._last_price: dict[str, float] = {}      # base symbol -> latest evaluated price
        self._paper_cash: float = self.config.initial_capital
        self._paper_positions: dict[str, dict] = {}  # base -> {qty, avg, entry}
        self._trades: list[dict] = []
        self._load_state()
        # Write state immediately so `paper-status` shows the live portfolio
        # from startup, even before the first trade fires.
        self._save_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file) as f:
                d = json.load(f)
            self._paper_cash = float(d.get("cash", self._paper_cash))
            self._paper_positions = d.get("positions", {})
            self._trades = d.get("trades", [])
            print(f"  [PAPER] Restored state: Rs {self._paper_cash:,.2f} cash, "
                  f"{len(self._paper_positions)} position(s), {len(self._trades)} past trade(s)")
        except Exception as e:
            print(f"  [PAPER] Could not load state file ({e}) — starting fresh")

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        realized = sum(t.get("pnl", 0.0) for t in self._trades if t["side"] == "SELL")
        with open(self._state_file, "w") as f:
            json.dump({
                "last_updated": datetime.now().isoformat(timespec="seconds"),
                "initial_capital": self.config.initial_capital,
                "cash": round(self._paper_cash, 2),
                "realized_pnl": round(realized, 2),
                "positions": self._paper_positions,
                "trades": self._trades[-1000:],
            }, f, indent=2)

    # ------------------------------------------------------------------
    # Broker surface — everything virtual
    # ------------------------------------------------------------------

    def _init_client(self):
        return None  # never talk to Dhan in paper mode

    def get_account_info(self) -> dict:
        pos_value = sum(
            p["qty"] * self._last_price.get(b, p["avg"])
            for b, p in self._paper_positions.items()
        )
        equity = self._paper_cash + pos_value
        return {"equity": equity, "cash": self._paper_cash,
                "buying_power": self._paper_cash, "used_margin": 0.0}

    def sync_portfolio(self) -> Portfolio:
        portfolio = Portfolio(
            initial_capital=self.config.initial_capital,
            current_cash=self._paper_cash,
        )
        for base, info in self._paper_positions.items():
            try:
                entry = datetime.fromisoformat(info.get("entry", ""))
            except ValueError:
                entry = datetime.now()
            portfolio.positions[base] = Position(
                ticker=base, quantity=info["qty"],
                avg_entry_price=info["avg"], entry_date=entry,
            )
            portfolio.update_price(base, self._last_price.get(base, info["avg"]))

        # Mirror every price update run_once() makes, so simulated fills
        # always execute at the most recent evaluated price.
        original_update = portfolio.update_price

        def _mirroring_update(ticker: str, price: float) -> None:
            original_update(ticker, price)
            self._last_price[strip_ns(ticker)] = float(price)

        portfolio.update_price = _mirroring_update
        return portfolio

    def submit_buy(self, ticker: str, quantity: float) -> dict | None:
        base = strip_ns(ticker)
        price = self._last_price.get(base, 0.0)
        if price <= 0:
            return None
        qty = int(quantity)
        if qty <= 0:
            return None
        # Charge realistic costs so paper P&L is NET, not gross. The fee depends
        # on the ORDER's notional (brokerage caps), so compute it from the whole
        # order and fold it back into a per-share basis.
        def _buy_total(q):
            notional = q * price
            return notional + self._side_cost(notional, "BUY")

        if _buy_total(qty) > self._paper_cash:
            while qty > 0 and _buy_total(qty) > self._paper_cash:
                qty -= 1
            if qty <= 0:
                print(f"  [PAPER] BUY skipped for {ticker}: Rs {price:,.2f}/share exceeds "
                      f"cash Rs {self._paper_cash:,.2f}")
                return None
        cost = _buy_total(qty)
        unit_cost = cost / qty

        self._paper_cash -= cost
        # Store the cost-inclusive unit price as the basis, so a later sell's
        # P&L is a true NET round-trip number (both sides' costs included).
        pos = self._paper_positions.get(base)
        if pos:
            total = pos["qty"] + qty
            pos["avg"] = (pos["avg"] * pos["qty"] + unit_cost * qty) / total
            pos["qty"] = total
        else:
            self._paper_positions[base] = {
                "qty": qty, "avg": unit_cost,
                "entry": datetime.now().isoformat(timespec="seconds"),
            }
        self._trades.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "side": "BUY", "symbol": base, "qty": qty, "price": round(price, 2),
        })
        self._save_state()
        return {"id": f"paper-{len(self._trades)}", "symbol": ticker, "qty": qty,
                "side": "BUY", "product": "PAPER", "status": "FILLED",
                "filled_avg_price": price}

    def submit_sell(self, ticker: str, quantity: float) -> dict | None:
        base = strip_ns(ticker)
        pos = self._paper_positions.get(base)
        if pos is None or pos["qty"] <= 0:
            return None
        price = self._last_price.get(base, pos["avg"])
        if price <= 0:
            return None
        qty = min(int(quantity), pos["qty"])
        if qty <= 0:
            return None

        # Net of costs on the way out too; pos["avg"] already carries the
        # entry-side cost, so pnl here is a true net round-trip figure.
        gross = qty * price
        proceeds = gross - self._side_cost(gross, "SELL")
        pnl = proceeds - (pos["avg"] * qty)
        self._paper_cash += proceeds
        pos["qty"] -= qty
        if pos["qty"] <= 0:
            del self._paper_positions[base]
        self._trades.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "side": "SELL", "symbol": base, "qty": qty,
            "price": round(price, 2), "pnl": round(pnl, 2),
        })
        self._save_state()
        return {"id": f"paper-{len(self._trades)}", "symbol": ticker, "qty": qty,
                "side": "SELL", "product": "PAPER", "status": "FILLED",
                "filled_avg_price": price}

    # ------------------------------------------------------------------
    # Broker-level SL/TP plumbing is disabled in paper mode — the risk
    # manager's software monitoring in run_once() performs all exits.
    # ------------------------------------------------------------------

    def reconcile_sl_tp(self, portfolio: Portfolio, tickers: list[str]) -> None:
        return

    def _place_sl_tp_orders(self, ticker: str, base: str, quantity: int, entry_price: float) -> None:
        sl = entry_price * (1.0 - self.config.stop_loss_pct)
        tp = entry_price * (1.0 + self.config.take_profit_pct)
        print(f"  [PAPER] Virtual SL/TP for {base}: stop Rs {sl:,.2f} / target Rs {tp:,.2f} "
              f"(software-monitored)")

    def cancel_sl_tp_orders(self, ticker: str) -> None:
        return

    def get_order_status(self, order_id: str) -> str:
        return ""

    # ------------------------------------------------------------------
    # End-of-day summary (full stats from the virtual ledger)
    # ------------------------------------------------------------------

    def _send_daily_summary(self) -> None:
        from .notifier import format_daily_summary

        day = self._ist_now().strftime("%Y-%m-%d")
        # Only count trades executed today, so each session's summary is
        # about that day rather than the whole test period.
        todays = [t for t in self._trades if str(t.get("ts", "")).startswith(day)]
        sells = [t for t in todays if t["side"] == "SELL"]
        wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
        realized_today = sum(t.get("pnl", 0.0) for t in sells)

        pos_value = sum(p["qty"] * self._last_price.get(b, p["avg"])
                        for b, p in self._paper_positions.items())
        equity = self._paper_cash + pos_value
        open_pos = list(self._paper_positions.keys())

        print(f"  [SUMMARY] {day}: {len(todays)} trades, "
              f"realized Rs {realized_today:+,.2f}, equity Rs {equity:,.2f}")
        self._notify(format_daily_summary(
            "PAPER", day, total_trades=len(todays), closed_trades=len(sells),
            wins=wins, realized_pnl=realized_today, equity=equity,
            initial_capital=self.config.initial_capital, open_positions=open_pos,
        ))
