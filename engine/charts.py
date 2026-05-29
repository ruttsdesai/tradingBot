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
