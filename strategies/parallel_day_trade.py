"""
Parallel Day-Trade Strategy — runs all 5 strategies in parallel on one chart.

On every intraday bar, all sub-strategies (MA crossover, RSI mean reversion,
MACD, Bollinger Bands, momentum breakout) are evaluated simultaneously on the
same symbol. Each strategy maintains a "stance" (bullish after its BUY event,
flat after its SELL event) and a virtual trade ledger that tracks how much
money it would have made on its own recent signals.

Votes are combined into a single consensus score in [-1, +1]:

    score = (sum of weights of bullish strategies
             - sum of weights of flat/bearish strategies) / total weight

where each strategy's weight adapts to its recent virtual performance —
strategies that have been making money on this chart get a louder voice,
strategies that have been losing get muted (never silenced below min_weight).

The combined decision:
    BUY   when flat and score >= entry_threshold (weighted majority bullish)
    SELL  when long and score <= exit_threshold (consensus lost)
    SELL  on the last bars of each session (mandatory intraday square-off)

Day-trading guards (only active on intraday data):
    - no new entries within `no_entry_last_bars` bars of the session close
    - force square-off on the last bar of every session (no overnight risk)
    - optional ATR volatility filter skips entries in dead, sideways markets

Per-bar history (stances, weights, score, decisions) is recorded so the whole
parallel decision process can be rendered on a single chart
(see engine.charts.plot_parallel_day_trade_chart).
"""

from collections import deque

import numpy as np
import pandas as pd

from .base import BaseStrategy, Signal, StrategyResult
from .ma_crossover import MACrossoverStrategy
from .rsi_mean_revert import RSIMeanReversionStrategy
from .macd import MACDStrategy
from .bollinger_bands import BollingerBandsStrategy
from .momentum_breakout import MomentumBreakoutStrategy


def build_intraday_strategies() -> list[BaseStrategy]:
    """Default sub-strategies with parameters tuned for 5m/15m intraday bars.

    Periods are shorter than the daily-chart defaults because intraday moves
    develop over dozens of bars, not dozens of days.
    """
    return [
        MACrossoverStrategy(fast_period=9, slow_period=21),
        RSIMeanReversionStrategy(rsi_period=14, oversold_threshold=30,
                                 overbought_threshold=70, stop_loss_pct=0.01),
        MACDStrategy(fast_period=12, slow_period=26, signal_period=9),
        BollingerBandsStrategy(period=20, num_std=2.0),
        MomentumBreakoutStrategy(lookback=30, exit_sma=10),
    ]


class ParallelDayTradeStrategy(BaseStrategy):
    """
    Meta-strategy: adaptive performance-weighted voting across 5 strategies
    running in parallel on a single symbol.

    Parameters:
        entry_threshold: consensus score needed to open a long (default 0.2
            = weighted 60/40 bullish split across strategies)
        exit_threshold: consensus score at/below which a long is closed
            (default -0.2 = weighted majority has turned flat/bearish)
        perf_window: number of recent virtual trades used to score each
            strategy's performance (default 10)
        perf_sensitivity: how strongly recent avg virtual return moves a
            strategy's weight; weight = 1 + sensitivity * avg_return
            (default 100 -> +0.5% avg return doubles the base weight)
        min_weight / max_weight: clamp for adaptive weights (0.25 / 3.0)
        min_trades_for_weight: virtual trades required before a strategy's
            weight deviates from 1.0 (default 3)
        square_off: force-exit at the end of every intraday session (True)
        no_entry_last_bars: block new entries this many bars before the
            session close (default 6 = last 30 min on 5m bars)
        min_volatility_pct: skip entries when ATR/price is below this
            (0 = disabled)
        trend_filter_period: EMA period for the trend filter; entries are
            only allowed while price is above this EMA, which keeps the
            long-only bot out of persistent downtrends (0 = disabled)
        strategies: custom sub-strategy list (default: intraday-tuned 5)
    """

    def __init__(
        self,
        entry_threshold: float = 0.2,
        exit_threshold: float = -0.2,
        perf_window: int = 10,
        perf_sensitivity: float = 100.0,
        min_weight: float = 0.25,
        max_weight: float = 3.0,
        min_trades_for_weight: int = 3,
        square_off: bool = True,
        no_entry_last_bars: int = 6,
        min_volatility_pct: float = 0.0,
        atr_period: int = 14,
        trend_filter_period: int = 200,
        strategies: list[BaseStrategy] | None = None,
        name: str = "ParallelDayTrade",
    ):
        super().__init__(name=name)
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.perf_window = perf_window
        self.perf_sensitivity = perf_sensitivity
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.min_trades_for_weight = min_trades_for_weight
        self.square_off = square_off
        self.no_entry_last_bars = no_entry_last_bars
        self.min_volatility_pct = min_volatility_pct
        self.atr_period = atr_period
        self.trend_filter_period = trend_filter_period

        self._strategies = strategies if strategies is not None else build_intraday_strategies()

        # Per-strategy live state
        self._stance: dict[str, int] = {s.name: 0 for s in self._strategies}
        self._virtual_entry: dict[str, float | None] = {s.name: None for s in self._strategies}
        self._virtual_returns: dict[str, deque] = {
            s.name: deque(maxlen=perf_window) for s in self._strategies
        }
        self._virtual_trade_count: dict[str, int] = {s.name: 0 for s in self._strategies}

        self._in_position = False

        # Precomputed session/volatility arrays (set in prepare)
        self._intraday = False
        self._bars_to_session_end: np.ndarray | None = None
        self._atr_pct: pd.Series | None = None
        self._trend_ema: pd.Series | None = None

        # Per-bar history for charting: filled during evaluate()
        self.history: dict[str, list] = self._empty_history()

    # ── setup ──────────────────────────────────────────────────────────

    def _empty_history(self) -> dict[str, list]:
        h = {
            "timestamp": [],
            "score": [],
            "decision": [],          # Signal per bar (combined)
        }
        for s in self._strategies:
            h[f"stance:{s.name}"] = []
            h[f"weight:{s.name}"] = []
            h[f"event:{s.name}"] = []   # +1 buy event, -1 sell event, 0 none
        return h

    @property
    def strategy_names(self) -> list[str]:
        return [s.name for s in self._strategies]

    def prepare(self, df: pd.DataFrame) -> None:
        """Pre-compute sub-strategy indicators, session boundaries, and ATR."""
        for s in self._strategies:
            s.prepare(df)

        # Detect intraday data: more than one bar per calendar date
        dates = pd.Series(pd.to_datetime(df.index).date)
        self._intraday = len(df) > 1 and dates.nunique() < len(df)

        # bars_to_session_end[i] = bars remaining in i's session (0 = last bar)
        bars_to_end = np.zeros(len(df), dtype=int)
        if self._intraday:
            count = 0
            for i in range(len(df) - 1, -1, -1):
                is_last = (i == len(df) - 1) or (dates.iloc[i] != dates.iloc[i + 1])
                count = 0 if is_last else count + 1
                bars_to_end[i] = count
        self._bars_to_session_end = bars_to_end

        # ATR as % of price for the volatility filter
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()
        self._atr_pct = atr / close

        if self.trend_filter_period > 0:
            self._trend_ema = close.ewm(span=self.trend_filter_period, adjust=False).mean()

        self.history = self._empty_history()

    def clone(self) -> "ParallelDayTradeStrategy":
        return ParallelDayTradeStrategy(
            entry_threshold=self.entry_threshold,
            exit_threshold=self.exit_threshold,
            perf_window=self.perf_window,
            perf_sensitivity=self.perf_sensitivity,
            min_weight=self.min_weight,
            max_weight=self.max_weight,
            min_trades_for_weight=self.min_trades_for_weight,
            square_off=self.square_off,
            no_entry_last_bars=self.no_entry_last_bars,
            min_volatility_pct=self.min_volatility_pct,
            atr_period=self.atr_period,
            trend_filter_period=self.trend_filter_period,
            strategies=[s.clone() for s in self._strategies],
            name=self.name,
        )

    # ── adaptive weights ───────────────────────────────────────────────

    def current_weight(self, strategy_name: str) -> float:
        """Weight based on the strategy's recent virtual trade returns."""
        rets = self._virtual_returns[strategy_name]
        if len(rets) < self.min_trades_for_weight:
            return 1.0
        avg_ret = float(np.mean(rets))
        return float(np.clip(1.0 + self.perf_sensitivity * avg_ret,
                             self.min_weight, self.max_weight))

    def performance_report(self) -> list[dict]:
        """Per-strategy virtual performance summary (for CLI tables)."""
        rows = []
        for s in self._strategies:
            rets = list(self._virtual_returns[s.name])
            rows.append({
                "strategy": s.name,
                "virtual_trades": self._virtual_trade_count[s.name],
                "recent_avg_return": float(np.mean(rets)) if rets else 0.0,
                "recent_win_rate": (sum(1 for r in rets if r > 0) / len(rets)) if rets else 0.0,
                "weight": self.current_weight(s.name),
                "stance": "LONG" if self._stance[s.name] == 1 else "FLAT",
            })
        return rows

    # ── evaluation ─────────────────────────────────────────────────────

    def evaluate(self, df: pd.DataFrame, idx: int) -> StrategyResult:
        price = df["close"].iloc[idx]
        ts = df.index[idx]

        # 1. Evaluate every sub-strategy in parallel; update stances and
        #    virtual trade ledgers
        events: dict[str, int] = {}
        for s in self._strategies:
            res = s.evaluate(df, idx)
            event = 0
            if res.signal == Signal.BUY:
                if self._virtual_entry[s.name] is None:
                    self._virtual_entry[s.name] = price
                    event = 1
                self._stance[s.name] = 1
            elif res.signal == Signal.SELL:
                entry = self._virtual_entry[s.name]
                if entry is not None:
                    self._virtual_returns[s.name].append((price - entry) / entry)
                    self._virtual_trade_count[s.name] += 1
                    self._virtual_entry[s.name] = None
                    event = -1
                self._stance[s.name] = 0
            events[s.name] = event

        # Intraday: close all virtual positions at session end so overnight
        # gaps never pollute the performance ledger
        bars_left = int(self._bars_to_session_end[idx]) if self._bars_to_session_end is not None else 0
        if self._intraday and bars_left == 0:
            for s in self._strategies:
                entry = self._virtual_entry[s.name]
                if entry is not None:
                    self._virtual_returns[s.name].append((price - entry) / entry)
                    self._virtual_trade_count[s.name] += 1
                    self._virtual_entry[s.name] = None
                self._stance[s.name] = 0

        # 2. Weighted consensus score in [-1, +1]
        weights = {s.name: self.current_weight(s.name) for s in self._strategies}
        total_w = sum(weights.values())
        bull_w = sum(w for n, w in weights.items() if self._stance[n] == 1)
        score = (2.0 * bull_w - total_w) / total_w if total_w > 0 else 0.0

        # 3. Combined decision
        decision = Signal.HOLD
        reason = f"score={score:+.2f}"

        session_closing = self._intraday and self.square_off and bars_left == 0
        entry_window_closed = self._intraday and bars_left < self.no_entry_last_bars
        too_quiet = (
            self.min_volatility_pct > 0
            and self._atr_pct is not None
            and not np.isnan(self._atr_pct.iloc[idx])
            and self._atr_pct.iloc[idx] < self.min_volatility_pct
        )
        against_trend = (
            self._trend_ema is not None
            and idx >= self.trend_filter_period
            and price < self._trend_ema.iloc[idx]
        )

        if self._in_position and session_closing:
            decision = Signal.SELL
            reason = f"SQUARE-OFF at session close (score={score:+.2f})"
            self._in_position = False
        elif self._in_position and score <= self.exit_threshold:
            decision = Signal.SELL
            bulls = [n for n in weights if self._stance[n] == 1]
            reason = (f"CONSENSUS LOST: score={score:+.2f} <= {self.exit_threshold} "
                      f"(bullish: {', '.join(bulls) if bulls else 'none'})")
            self._in_position = False
        elif (not self._in_position and score >= self.entry_threshold
              and not entry_window_closed and not too_quiet and not against_trend):
            decision = Signal.BUY
            parts = [f"{n}(w={weights[n]:.2f})" for n in weights if self._stance[n] == 1]
            reason = f"CONSENSUS BUY: score={score:+.2f} [{' + '.join(parts)}]"
            self._in_position = True

        # 4. Record history for the parallel chart
        h = self.history
        h["timestamp"].append(ts)
        h["score"].append(score)
        h["decision"].append(decision)
        for s in self._strategies:
            h[f"stance:{s.name}"].append(self._stance[s.name])
            h[f"weight:{s.name}"].append(weights[s.name])
            h[f"event:{s.name}"].append(events[s.name])

        return StrategyResult(decision, price, reason)

    # ── position-state hooks (called by PaperTrader / live traders) ───

    def set_entry_price(self, price: float) -> None:
        for s in self._strategies:
            if hasattr(s, "set_entry_price"):
                s.set_entry_price(price)

    def clear_entry(self) -> None:
        # Risk manager exits (stop-loss / take-profit / trailing) close the
        # real position without a strategy SELL — resync so we can re-enter
        self._in_position = False
        for s in self._strategies:
            if hasattr(s, "clear_entry"):
                s.clear_entry()
