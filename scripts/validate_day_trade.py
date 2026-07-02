"""
Offline validation for the Parallel Day-Trade bot.

Generates realistic synthetic 5-minute intraday data across several market
regimes (uptrend, downtrend, range-bound, choppy, volatile with gaps), runs
the ParallelDayTradeStrategy through the PaperTrader with the same intraday
risk limits as `cli.py day-trade`, and checks the core invariants:

  * the bot never holds a position overnight (square-off works)
  * the combined engine's worst regime loses less than the worst
    standalone strategy (diversification works)
  * results are reported per regime, net of commission

Run:  python scripts/validate_day_trade.py [--seed 7] [--save-csv]

This uses no network access, so it also serves as a quick regression test
after changing strategy or engine code.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.paper_trader import PaperTrader
from engine.risk_manager import RiskManager
from engine.portfolio import Side
from strategies.parallel_day_trade import (
    ParallelDayTradeStrategy, build_intraday_strategies,
)

BARS_PER_SESSION = 78   # 6.5h US session on 5m bars
SESSIONS = 60


def make_session_index(n_sessions: int = SESSIONS) -> pd.DatetimeIndex:
    """Business-day sessions of 5m bars, 09:30–16:00."""
    days = pd.bdate_range("2026-03-02", periods=n_sessions)
    stamps = []
    for d in days:
        start = d + pd.Timedelta(hours=9, minutes=30)
        stamps.extend(start + pd.Timedelta(minutes=5 * i) for i in range(BARS_PER_SESSION))
    return pd.DatetimeIndex(stamps)


def synth_ohlcv(index: pd.DatetimeIndex, rng: np.random.Generator,
                drift_per_bar: float = 0.0, vol_per_bar: float = 0.0012,
                mean_revert: float = 0.0, gap_vol: float = 0.004) -> pd.DataFrame:
    """Synthetic OHLCV: geometric walk with optional drift, OU mean reversion,
    and overnight gaps between sessions."""
    n = len(index)
    log_price = np.zeros(n)
    log_price[0] = np.log(100.0)
    anchor = log_price[0]

    dates = index.date
    for i in range(1, n):
        shock = rng.normal(0, vol_per_bar)
        if dates[i] != dates[i - 1]:  # overnight gap
            shock += rng.normal(0, gap_vol)
        pull = mean_revert * (anchor - log_price[i - 1])
        log_price[i] = log_price[i - 1] + drift_per_bar + pull + shock

    close = np.exp(log_price)
    spread = np.abs(rng.normal(0, vol_per_bar, n)) * close
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.integers(10_000, 200_000, n).astype(float)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def build_regimes(seed: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = make_session_index()
    return {
        "uptrend": synth_ohlcv(idx, rng, drift_per_bar=+0.00006),
        "downtrend": synth_ohlcv(idx, rng, drift_per_bar=-0.00006),
        "range_bound": synth_ohlcv(idx, rng, mean_revert=0.004),
        "choppy_lowvol": synth_ohlcv(idx, rng, vol_per_bar=0.0005, mean_revert=0.001),
        "volatile_gaps": synth_ohlcv(idx, rng, vol_per_bar=0.0022, gap_vol=0.010),
    }


def day_trade_risk_manager() -> RiskManager:
    """Mirror of the config.yaml day_trading.risk defaults."""
    return RiskManager(
        max_positions=1, max_allocation_pct=0.95, max_daily_loss_pct=0.02,
        stop_loss_pct=0.01, take_profit_pct=0.025,
        trailing_stop_enabled=True, trailing_stop_pct=0.008,
    )


def check_no_overnight_holds(result, df: pd.DataFrame) -> bool:
    """Verify every position opened is closed within the same session."""
    open_qty = 0.0
    last_date = None
    for t in result.portfolio.trade_history:
        if last_date is not None and t.timestamp.date() != last_date and open_qty > 1e-9:
            return False
        open_qty += t.quantity if t.side == Side.BUY else -t.quantity
        last_date = t.timestamp.date()
    return True


def run_engine(df: pd.DataFrame, strategy, capital=100_000.0, commission=0.0005):
    trader = PaperTrader(
        strategy=strategy,
        risk_manager=day_trade_risk_manager(),
        initial_capital=capital,
        commission_pct=commission,
        record_bar_equity=True,
    )
    return trader.run(df.copy(), ticker="SYNTH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--save-csv", action="store_true",
                        help="Save synthetic regimes to data/synthetic/*.csv "
                             "for use with `cli.py day-trade --csv-file`")
    args = parser.parse_args()

    regimes = build_regimes(args.seed)

    if args.save_csv:
        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic")
        os.makedirs(out_dir, exist_ok=True)
        for name, df in regimes.items():
            path = os.path.join(out_dir, f"{name}_5m.csv")
            df.to_csv(path)
            print(f"  saved {path}")

    print(f"\n{'Regime':<15} {'B&H':>8} {'Combined':>9} {'Trades':>7} "
          f"{'WinRate':>8} {'MaxDD':>8} {'Overnight':>10}  worst standalone")
    print("-" * 100)

    combined_returns, failures = [], []
    for name, df in regimes.items():
        strategy = ParallelDayTradeStrategy()
        result = run_engine(df, strategy)
        ret = result.portfolio.total_pnl_pct
        combined_returns.append(ret)
        bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1.0

        no_overnight = check_no_overnight_holds(result, df)
        if not no_overnight:
            failures.append(f"{name}: position held overnight!")
        if len(strategy.history["score"]) != len(df):
            failures.append(f"{name}: history length mismatch")

        solo_rets = {}
        for solo in build_intraday_strategies():
            r = run_engine(df, solo)
            solo_rets[solo.name] = r.portfolio.total_pnl_pct
        worst_name = min(solo_rets, key=solo_rets.get)

        print(f"{name:<15} {bh:>+8.2%} {ret:>+9.2%} {result.total_trades:>7} "
              f"{result.win_rate:>8.1%} {result.portfolio.max_drawdown:>+8.2%} "
              f"{'OK' if no_overnight else 'FAIL':>10}  "
              f"{worst_name} {solo_rets[worst_name]:+.2%}")

        if ret < solo_rets[worst_name]:
            failures.append(
                f"{name}: combined ({ret:+.2%}) underperformed the worst "
                f"standalone strategy ({worst_name} {solo_rets[worst_name]:+.2%})"
            )

    avg = sum(combined_returns) / len(combined_returns)
    print("-" * 100)
    print(f"Average combined return across regimes: {avg:+.2%}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll invariants passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
