"""
Consensus Lab — does requiring MORE strategies to agree actually help?

Why this exists
---------------
The live bot does not trade one strategy; it trades a VOTE across three
(RSI mean-reversion, Bollinger, MA crossover). Every other lab script runs
a single strategy at a time, so the consensus rule itself has never been
backtested — `lab/run_experiment.py` says so in its own docstring.

Three weeks of paper logs (2026-08-03..08-21) surfaced the reason to close
that gap. Of 73 live entries:

    1-of-3 agreement   67 entries   net Rs   +27   win 56%
    2-of-3 agreement    6 entries   net Rs  +314   win 83%

That looks like an edge, but it is a post-hoc slice of a tiny sample
(Welch t = +1.89 for the difference, which does not clear |t| > 2 even
before penalising it for being found by looking). It is a HYPOTHESIS, and
this script is the test.

Pre-registered prediction (written before running)
--------------------------------------------------
EXPECT NO EDGE. Every prior test in this repo -- swing, regime timing,
long/short, trend/momentum/volatility/volume signal power -- found nothing
clearing |t| >= 2, and the 2-of-3 cell had n=6. The specific prediction is
that min_agree=2 trades far less (agreement is rare), which mechanically
cuts the cost drag, and that any apparent improvement will come from
trading less rather than from picking better. The diagnostic that separates
the two is Rs-per-round-trip: if edge-per-trade is flat and only the trade
count falls, the rule is a cost filter, not a signal.

    python lab/run_consensus.py                 # 5m, 55 days, live basket
    python lab/run_consensus.py --cost-pct 0.001  # double costs
"""

import argparse
import json
import os
import statistics as st
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.intraday_runner import IntradayBacktester
from engine.risk_manager import RiskManager
from strategies.base import BaseStrategy, Signal, StrategyResult
from lab.run_experiment import LIVE_BASKET, RESULTS_DIR, load_bars, DEFAULT_COST_PCT


def _live_substrategies() -> list[BaseStrategy]:
    """The three strategies the live bot votes across, with CONFIG parameters.

    Bollinger is 10/1.5 here, not the library default 20/2.0 — matching
    config.yaml is the whole point, otherwise this tests a different bot.
    """
    from strategies.rsi_mean_revert import RSIMeanReversionStrategy
    from strategies.bollinger_bands import BollingerBandsStrategy
    from strategies.ma_crossover import MACrossoverStrategy
    return [
        RSIMeanReversionStrategy(rsi_period=14, oversold_threshold=30,
                                 overbought_threshold=70),
        BollingerBandsStrategy(period=10, num_std=1.5),
        MACrossoverStrategy(fast_period=20, slow_period=50),
    ]


class LiveConsensusStrategy(BaseStrategy):
    """Faithful mirror of `dhan_live_trader`'s vote, with a tunable threshold.

    The live rule is: count BUY votes and SELL votes across the sub-strategies,
    BUY when BUY > SELL, SELL when SELL > BUY, HOLD on a tie or all-HOLD.
    HOLD abstains — it does not veto. `min_agree` adds the threshold under
    test: a BUY additionally needs at least that many BUY votes.

    Note `strategies/ensemble.py` is deliberately NOT reused: it exits the
    moment buy-consensus is lost, which is a much tighter exit rule than the
    live bot's. Reusing it would confound the entry threshold with an exit
    change and the comparison would measure the wrong thing.
    """

    def __init__(self, min_agree: int = 1, name: str | None = None):
        super().__init__(name=name or f"Consensus(min={min_agree})")
        self.min_agree = min_agree
        self._subs = _live_substrategies()

    def prepare(self, df: pd.DataFrame) -> None:
        for s in self._subs:
            s.prepare(df)

    def clone(self) -> "LiveConsensusStrategy":
        return LiveConsensusStrategy(min_agree=self.min_agree, name=self.name)

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        price = float(df["close"].iloc[idx])
        buys, sells = [], []
        for s in self._subs:
            sig = s.evaluate(df, idx).signal
            if sig == Signal.BUY:
                buys.append(s.name)
            elif sig == Signal.SELL:
                sells.append(s.name)

        if len(buys) > len(sells) and len(buys) >= self.min_agree:
            return StrategyResult(Signal.BUY, price,
                                  f"Consensus({len(buys)}/3 BUY: {','.join(buys)})")
        if len(sells) > len(buys):
            return StrategyResult(Signal.SELL, price,
                                  f"Consensus({len(sells)}/3 SELL: {','.join(sells)})")
        return StrategyResult(Signal.HOLD, price,
                              f"Votes: BUY={len(buys)} SELL={len(sells)}")


def run_threshold(min_agree: int, data: dict, cost_pct: float) -> dict:
    """Run the consensus strategy at one threshold across the whole basket."""
    per_ticker = []
    for ticker, df in data.items():
        if df is None or df.empty:
            continue
        risk = RiskManager(max_positions=1, max_allocation_pct=0.95,
                           max_daily_loss_pct=0.03, stop_loss_pct=0.05,
                           take_profit_pct=0.15)
        bt = IntradayBacktester(
            strategy=LiveConsensusStrategy(min_agree=min_agree),
            risk_manager=risk,
            initial_capital=100_000.0,
            commission_pct=cost_pct,
            max_hold_minutes=120,
            min_profit_threshold_pct=0.005,
            min_volatility_pct=0.0015,
        )
        try:
            res = bt.run(df, ticker=ticker)
        except Exception as e:                      # noqa: BLE001
            print(f"    [skip] {ticker}: {e}")
            continue
        per_ticker.append({
            "ticker": ticker,
            "return_pct": (res.portfolio.total_value - 100_000.0) / 1_000.0,
            "trades": res.total_trades,
            "win_rate": res.win_rate * 100,
            "sharpe": res.sharpe_ratio,
        })

    if not per_ticker:
        return {"min_agree": min_agree, "tickers": 0}

    rets = [t["return_pct"] for t in per_ticker]
    trades = sum(t["trades"] for t in per_ticker)
    # Rs per round trip on a Rs100k book — the number that separates
    # "better signal" from "merely fewer trades".
    rs_per_rt = (sum(rets) / 100 * 100_000) / (trades / 2) if trades else 0.0
    return {
        "min_agree": min_agree,
        "tickers": len(per_ticker),
        "avg_return_pct": sum(rets) / len(rets),
        "total_trades": trades,
        "profitable": sum(1 for r in rets if r > 0),
        "avg_win_rate": sum(t["win_rate"] for t in per_ticker) / len(per_ticker),
        "avg_sharpe": sum(t["sharpe"] for t in per_ticker) / len(per_ticker),
        "rs_per_round_trip": rs_per_rt,
        "spread_stdev": st.stdev(rets) if len(rets) > 1 else 0.0,
        "detail": sorted(per_ticker, key=lambda t: -t["return_pct"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Does requiring more agreement help?")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--days", type=int, default=55)
    ap.add_argument("--cost-pct", type=float, default=DEFAULT_COST_PCT)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    print(f"\nLoading {args.interval} bars ({args.days}d) for {len(LIVE_BASKET)} tickers...")
    data = {t: load_bars(t, args.days, args.interval, args.refresh) for t in LIVE_BASKET}
    have = {t: d for t, d in data.items() if d is not None and not d.empty}
    print(f"  {len(have)}/{len(LIVE_BASKET)} tickers loaded, "
          f"{sum(len(d) for d in have.values()):,} bars total")

    print(f"\nCost: {args.cost_pct:.4%}/side ({args.cost_pct*2:.3%} round trip)")
    print(f"{'min_agree':>10} {'avg ret%':>9} {'trades':>7} {'Rs/RT':>8} "
          f"{'win%':>6} {'sharpe':>7} {'profitable':>11}")
    print("  " + "-" * 68)

    results = []
    for k in (1, 2, 3):
        r = run_threshold(k, have, args.cost_pct)
        results.append(r)
        if not r.get("tickers"):
            continue
        print(f"{k:>10} {r['avg_return_pct']:>+9.2f} {r['total_trades']:>7} "
              f"{r['rs_per_round_trip']:>+8.2f} {r['avg_win_rate']:>6.1f} "
              f"{r['avg_sharpe']:>7.2f} {r['profitable']:>7}/{r['tickers']}")

    print("\nPer-ticker detail:")
    for r in results:
        if not r.get("tickers"):
            continue
        print(f"  min_agree={r['min_agree']}: " + "  ".join(
            f"{d['ticker'].replace('.NS',''):<11}{d['return_pct']:+6.2f}% "
            f"({d['trades']:>3}t)" for d in r["detail"]))

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = os.path.join(RESULTS_DIR, f"consensus_{stamp}.json")
    with open(path, "w") as fh:
        json.dump({"interval": args.interval, "days": args.days,
                   "cost_pct": args.cost_pct, "results": results}, fh, indent=2)
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
