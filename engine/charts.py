"""
Chart Visualization Module.

Plots candlestick charts with buy/sell markers, equity curves, and drawdowns
for paper trading results. Exports to PNG files.

Usage:
    from engine.charts import plot_backtest_chart

    result = paper_trader.run(df, ticker="AAPL")
    plot_backtest_chart(df, result, save_path="charts/AAPL_ma_crossover.png")
"""

import os
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for CLI use
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from engine.portfolio import Side
from engine.paper_trader import PaperTraderResult


def plot_backtest_chart(
    df: pd.DataFrame,
    result: PaperTraderResult,
    save_path: Optional[str] = None,
    show: bool = False,
    figsize: tuple[int, int] = (18, 12),
    dpi: int = 150,
) -> str:
    """
    Plot a comprehensive backtest chart with 3 panels:
      1. Price chart with buy/sell markers
      2. Equity curve with drawdown shading
      3. Drawdown percentage over time

    Args:
        df: OHLCV DataFrame with datetime index
        result: PaperTraderResult from a completed run
        save_path: If provided, save to this file path
        show: If True, display the plot interactively
        figsize: Figure size in inches
        dpi: Dots per inch for saved image

    Returns:
        Path to saved file (or empty string if not saved)
    """
    trades = result.portfolio.trade_history
    equity = result.equity_curve if result.equity_curve else []
    dates = df.index

    # Build buy/sell markers
    buy_dates, buy_prices = [], []
    sell_dates, sell_prices = [], []
    for t in trades:
        if t.side == Side.BUY:
            buy_dates.append(t.timestamp)
            buy_prices.append(t.price)
        elif t.side == Side.SELL:
            sell_dates.append(t.timestamp)
            sell_prices.append(t.price)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=figsize,
                                         gridspec_kw={"height_ratios": [3, 2, 1]},
                                         sharex=True)

    # ── Panel 1: Price + Trade Markers ─────────────────────────────────
    _plot_price_chart(ax1, df, buy_dates, buy_prices, sell_dates, sell_prices, result.ticker)

    # ── Panel 2: Equity Curve ──────────────────────────────────────────
    _plot_equity_curve(ax2, equity, dates)

    # ── Panel 3: Drawdown ──────────────────────────────────────────────
    _plot_drawdown(ax3, equity)

    # ── Title & Layout ─────────────────────────────────────────────────
    fig.suptitle(
        f"{result.ticker} -- {result.strategy_name}\n"
        f"Return: {result.portfolio.total_pnl_pct:+.2%}  |  "
        f"Sharpe: {result.sharpe_ratio:.2f}  |  "
        f"Sortino: {result.sortino_ratio:.2f}  |  "
        f"Trades: {result.total_trades}  |  "
        f"Win Rate: {result.win_rate:.1%}  |  "
        f"Max DD: {result.portfolio.max_drawdown:+.2%}",
        fontsize=13, fontweight="bold", y=0.98,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.95])

    # ── Save / Show ────────────────────────────────────────────────────
    saved = ""
    if save_path:
        parent = os.path.dirname(save_path) or "."
        os.makedirs(parent, exist_ok=True)
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight", facecolor="white")
        saved = save_path

    if show:
        plt.show()
    else:
        plt.close(fig)

    return saved


def _plot_price_chart(
    ax: plt.Axes,
    df: pd.DataFrame,
    buy_dates: list,
    buy_prices: list,
    sell_dates: list,
    sell_prices: list,
    ticker: str,
) -> None:
    """Plot price line with buy/sell markers."""
    ax.plot(df.index, df["close"], color="#1f77b4", linewidth=0.8, alpha=0.9, label="Close")
    ax.fill_between(df.index, df["low"], df["high"], color="#1f77b4", alpha=0.08, label="Daily Range")

    # Moving averages for context
    sma50 = df["close"].rolling(50).mean()
    sma200 = df["close"].rolling(200).mean()
    ax.plot(df.index, sma50, color="#ff7f0e", linewidth=0.6, alpha=0.5, label="SMA 50")
    ax.plot(df.index, sma200, color="#9467bd", linewidth=0.6, alpha=0.5, label="SMA 200")

    # Buy markers (green up triangle)
    if buy_dates:
        ax.scatter(buy_dates, buy_prices, marker="^", color="#2ca02c",
                   s=60, zorder=5, edgecolors="white", linewidth=0.5, label="Buy")
    # Sell markers (red down triangle)
    if sell_dates:
        ax.scatter(sell_dates, sell_prices, marker="v", color="#d62728",
                   s=60, zorder=5, edgecolors="white", linewidth=0.5, label="Sell")

    ax.set_ylabel("Price ($)", fontsize=10)
    ax.set_title(f"{ticker} — Price Chart with Trade Markers", fontsize=12, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.0f}"))


def _plot_equity_curve(ax: plt.Axes, equity: list[float], dates: pd.DatetimeIndex) -> None:
    """Plot equity curve with watermark stats."""
    if len(equity) < 2:
        ax.text(0.5, 0.5, "No equity data", transform=ax.transAxes, ha="center")
        return

    # Align equity curve with dates (equity starts one bar behind dates due to insert)
    eq_dates = dates[:len(equity)] if len(equity) <= len(dates) else dates
    if len(eq_dates) < len(equity):
        eq_dates = dates

    # Trim to match
    n = min(len(eq_dates), len(equity))
    eq_dates = eq_dates[:n]
    eq_vals = equity[:n]

    initial = eq_vals[0] if eq_vals else 100_000

    # Compute drawdown for shading
    peak = np.maximum.accumulate(np.array(eq_vals))
    dd = (np.array(eq_vals) - peak) / peak
    in_dd = dd < 0

    ax.plot(eq_dates, eq_vals, color="#2ca02c", linewidth=1.2, label="Equity")
    ax.axhline(y=initial, color="gray", linestyle="--", linewidth=0.5, alpha=0.5, label="Initial")

    # Shade drawdown periods in red
    ax.fill_between(eq_dates, eq_vals, peak[:n],
                    where=in_dd, color="#d62728", alpha=0.15, label="Drawdown")

    ax.set_ylabel("Portfolio Value ($)", fontsize=10)
    ax.set_title("Equity Curve", fontsize=12, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.0f}"))


def _plot_drawdown(ax: plt.Axes, equity: list[float]) -> None:
    """Plot drawdown percentage over time."""
    if len(equity) < 2:
        ax.text(0.5, 0.5, "No drawdown data", transform=ax.transAxes, ha="center")
        return

    peak = np.maximum.accumulate(np.array(equity))
    dd = (np.array(equity) - peak) / peak

    ax.fill_between(range(len(dd)), dd, 0, color="#d62728", alpha=0.4)
    ax.plot(range(len(dd)), dd, color="#d62728", linewidth=0.8)
    ax.axhline(y=0, color="gray", linewidth=0.5)

    # Label max drawdown
    max_dd_idx = np.argmin(dd)
    max_dd_val = dd[max_dd_idx]
    ax.annotate(
        f"Max DD: {max_dd_val:.1%}",
        xy=(max_dd_idx, max_dd_val),
        xytext=(max_dd_idx + len(dd) * 0.1, max_dd_val - 0.03),
        arrowprops={"arrowstyle": "->", "color": "#d62728"},
        fontsize=9, color="#d62728",
    )

    ax.set_ylabel("Drawdown %", fontsize=10)
    ax.set_xlabel("Bar Index", fontsize=10)
    ax.set_title("Drawdown", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))


def plot_parallel_day_trade_chart(
    df: pd.DataFrame,
    result: PaperTraderResult,
    strategy,
    save_path: Optional[str] = None,
    figsize: tuple[int, int] = (20, 16),
    dpi: int = 150,
) -> str:
    """
    Render the entire parallel multi-strategy decision process on ONE chart:

      1. Price with combined BUY/SELL decisions and in-position shading
      2. Strategy lanes -- each of the 5 strategies' live stance (bullish
         segments) and buy/sell events, stacked in parallel
      3. Weighted consensus score with entry/exit thresholds
      4. Adaptive strategy weights over time
      5. Intraday equity curve

    Args:
        df: OHLCV DataFrame the backtest ran on
        result: PaperTraderResult from the run
        strategy: ParallelDayTradeStrategy instance (provides .history)
        save_path: output PNG path

    Returns:
        Path to saved file
    """
    h = strategy.history
    names = strategy.strategy_names
    ts = pd.to_datetime(pd.Series(h["timestamp"]))
    x = np.arange(len(ts))  # bar index axis avoids overnight-gap distortion

    lane_colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b"]

    fig, axes = plt.subplots(
        5, 1, figsize=figsize, sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.6, 1.1, 1.1, 1.4]},
    )
    ax_price, ax_lanes, ax_score, ax_w, ax_eq = axes

    # ── Panel 1: price + combined decisions ────────────────────────────
    close = df["close"].values[:len(x)]
    ax_price.plot(x, close, color="#1f77b4", linewidth=0.9, label="Close")

    decisions = h["decision"]
    from strategies.base import Signal as _Sig
    buy_x = [i for i, d in enumerate(decisions) if d == _Sig.BUY]
    sell_x = [i for i, d in enumerate(decisions) if d == _Sig.SELL]
    if buy_x:
        ax_price.scatter(buy_x, close[buy_x], marker="^", color="#2ca02c", s=90,
                         zorder=5, edgecolors="white", linewidth=0.6, label="Combined BUY")
    if sell_x:
        ax_price.scatter(sell_x, close[sell_x], marker="v", color="#d62728", s=90,
                         zorder=5, edgecolors="white", linewidth=0.6, label="Combined SELL")

    # Shade bars where the combined bot was long
    in_pos = np.zeros(len(x), dtype=bool)
    holding = False
    for i, d in enumerate(decisions):
        if d == _Sig.BUY:
            holding = True
        elif d == _Sig.SELL:
            holding = False
        in_pos[i] = holding
    ax_price.fill_between(x, close.min(), close.max(), where=in_pos,
                          color="#2ca02c", alpha=0.06, label="In position")

    # Session boundaries (vertical lines at date changes)
    dates = ts.dt.date.values
    for i in range(1, len(dates)):
        if dates[i] != dates[i - 1]:
            for ax in axes:
                ax.axvline(i, color="gray", linewidth=0.4, alpha=0.25)

    ax_price.set_ylabel("Price", fontsize=10)
    ax_price.set_title(
        f"{result.ticker} — 5 Strategies in Parallel, One Combined Decision",
        fontsize=12, fontweight="bold")
    ax_price.legend(loc="upper left", fontsize=8, ncol=4)
    ax_price.grid(True, alpha=0.3)

    # ── Panel 2: parallel strategy lanes ───────────────────────────────
    for row, (name, color) in enumerate(zip(names, lane_colors)):
        y = row
        stance = np.array(h[f"stance:{name}"], dtype=float)
        events = np.array(h[f"event:{name}"])
        # Bullish stance = thick colored segment on the lane
        ax_lanes.fill_between(x, y - 0.28, y + 0.28, where=stance > 0,
                              color=color, alpha=0.55, linewidth=0)
        # Baseline
        ax_lanes.axhline(y, color=color, linewidth=0.4, alpha=0.35)
        # Buy/sell events as ticks
        ev_buy = np.where(events == 1)[0]
        ev_sell = np.where(events == -1)[0]
        if len(ev_buy):
            ax_lanes.scatter(ev_buy, np.full(len(ev_buy), y + 0.32), marker="^",
                             color="#2ca02c", s=14, zorder=5)
        if len(ev_sell):
            ax_lanes.scatter(ev_sell, np.full(len(ev_sell), y - 0.32), marker="v",
                             color="#d62728", s=14, zorder=5)

    ax_lanes.set_yticks(range(len(names)))
    ax_lanes.set_yticklabels(names, fontsize=8)
    ax_lanes.set_ylim(-0.7, len(names) - 0.3)
    ax_lanes.invert_yaxis()
    ax_lanes.set_ylabel("Strategy lanes", fontsize=10)
    ax_lanes.set_title("Per-strategy stance (colored = bullish) and signals",
                       fontsize=10, fontweight="bold")
    ax_lanes.grid(True, axis="x", alpha=0.2)

    # ── Panel 3: consensus score ───────────────────────────────────────
    score = np.array(h["score"])
    ax_score.plot(x, score, color="#333333", linewidth=0.9, label="Consensus score")
    ax_score.axhline(strategy.entry_threshold, color="#2ca02c", linestyle="--",
                     linewidth=0.8, label=f"Entry ≥ {strategy.entry_threshold:+.2f}")
    ax_score.axhline(strategy.exit_threshold, color="#d62728", linestyle="--",
                     linewidth=0.8, label=f"Exit ≤ {strategy.exit_threshold:+.2f}")
    ax_score.fill_between(x, score, 0, where=score > 0, color="#2ca02c", alpha=0.15)
    ax_score.fill_between(x, score, 0, where=score < 0, color="#d62728", alpha=0.15)
    ax_score.set_ylim(-1.05, 1.05)
    ax_score.set_ylabel("Score", fontsize=10)
    ax_score.set_title("Weighted consensus score", fontsize=10, fontweight="bold")
    ax_score.legend(loc="upper left", fontsize=8, ncol=3)
    ax_score.grid(True, alpha=0.3)

    # ── Panel 4: adaptive weights ──────────────────────────────────────
    for name, color in zip(names, lane_colors):
        ax_w.plot(x, h[f"weight:{name}"], color=color, linewidth=0.9, label=name)
    ax_w.axhline(1.0, color="gray", linestyle="--", linewidth=0.6, alpha=0.6)
    ax_w.set_ylabel("Weight", fontsize=10)
    ax_w.set_title("Adaptive strategy weights (recent virtual P&L)",
                   fontsize=10, fontweight="bold")
    ax_w.legend(loc="upper left", fontsize=7, ncol=5)
    ax_w.grid(True, alpha=0.3)

    # ── Panel 5: equity curve ──────────────────────────────────────────
    equity = result.bar_equity if result.bar_equity else result.equity_curve
    if len(equity) >= 2:
        n = min(len(x), len(equity))
        eq = np.array(equity[:n])
        ax_eq.plot(x[:n], eq, color="#2ca02c", linewidth=1.1, label="Equity")
        ax_eq.axhline(eq[0], color="gray", linestyle="--", linewidth=0.6,
                      alpha=0.6, label="Initial")
        peak = np.maximum.accumulate(eq)
        ax_eq.fill_between(x[:n], eq, peak, where=eq < peak,
                           color="#d62728", alpha=0.15, label="Drawdown")
        ax_eq.legend(loc="upper left", fontsize=8, ncol=3)
        ax_eq.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.0f}"))
    else:
        ax_eq.text(0.5, 0.5, "No equity data", transform=ax_eq.transAxes, ha="center")
    ax_eq.set_ylabel("Equity", fontsize=10)
    ax_eq.set_title("Portfolio equity (per bar)", fontsize=10, fontweight="bold")
    ax_eq.grid(True, alpha=0.3)

    # X-axis: label with dates at session starts
    tick_idx = [0] + [i for i in range(1, len(dates)) if dates[i] != dates[i - 1]]
    step = max(1, len(tick_idx) // 12)  # at most ~12 labels
    tick_idx = tick_idx[::step]
    ax_eq.set_xticks(tick_idx)
    ax_eq.set_xticklabels([str(dates[i]) for i in tick_idx], rotation=45,
                          ha="right", fontsize=8)
    ax_eq.set_xlabel("Session", fontsize=10)

    fig.suptitle(
        f"{result.ticker} — Parallel Day-Trade Bot\n"
        f"Return: {result.portfolio.total_pnl_pct:+.2%}  |  "
        f"Trades: {result.total_trades}  |  "
        f"Win Rate: {result.win_rate:.1%}  |  "
        f"Sharpe: {result.sharpe_ratio:.2f}  |  "
        f"Max DD: {result.portfolio.max_drawdown:+.2%}",
        fontsize=13, fontweight="bold", y=0.995,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.965])

    saved = ""
    if save_path:
        parent = os.path.dirname(save_path) or "."
        os.makedirs(parent, exist_ok=True)
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight", facecolor="white")
        saved = save_path
    plt.close(fig)
    return saved


def plot_correlation_heatmap(
    matrix: dict[str, dict[str, float]],
    save_path: str = "",
    figsize: tuple[int, int] = (10, 8),
) -> str:
    """
    Plot a correlation heatmap for a set of tickers.

    Args:
        matrix: {ticker: {ticker: correlation}} from RiskManager.get_correlation_matrix()
        save_path: File path to save (if empty, auto-saves to charts/correlation.png)

    Returns:
        Path to saved file
    """
    tickers = list(matrix.keys())
    n = len(tickers)
    if n < 2:
        return ""

    data = np.zeros((n, n))
    for i, t1 in enumerate(tickers):
        for j, t2 in enumerate(tickers):
            data[i, j] = matrix.get(t1, {}).get(t2, 0.0)

    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(data, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(tickers, rotation=45, ha="right", fontsize=9)
    ax.set_yticklabels(tickers, fontsize=9)
    ax.set_title("Portfolio Correlation Matrix", fontsize=13, fontweight="bold")

    # Annotate cells
    for i in range(n):
        for j in range(n):
            color = "white" if abs(data[i, j]) > 0.6 else "black"
            ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center",
                    fontsize=8, color=color)

    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Pearson r", fontsize=9)

    if not save_path:
        os.makedirs("charts", exist_ok=True)
        save_path = "charts/correlation.png"

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    return save_path
