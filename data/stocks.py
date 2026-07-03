"""
Collect stock market data using yfinance.
Default: 10 years of daily OHLCV data with automatic retry on failure.
"""

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf


def fetch_stock_data(
    ticker: str,
    years: int = 10,
    interval: str = "1d",
    max_retries: int = 3,
) -> pd.DataFrame:
    """
    Fetch historical stock data from Yahoo Finance.

    Args:
        ticker: Stock symbol (e.g., 'AAPL', 'MSFT')
        years: Years of data to fetch (default 10 -> 2016-2026)
        interval: Candle interval -- '1d', '1h', '15m', '5m', '1m'
        max_retries: Retry count on network failure

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
        Index: DatetimeIndex (UTC)
    """
    end = datetime.now()
    start = end - timedelta(days=365 * years)

    stock = yf.Ticker(ticker)

    # Fetch with retries on empty results
    df = pd.DataFrame()
    for attempt in range(max_retries):
        df = stock.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
        )
        if not df.empty:
            break

    if df.empty:
        raise ValueError(
            f"Failed to fetch data for {ticker} after {max_retries} retries. "
            f"Date range: {start.date()} -> {end.date()}"
        )

    # Normalize column names (yfinance may return multi-level columns)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Standardize to lowercase
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index).tz_localize(None)

    return df


# Yahoo Finance caps how far back each intraday interval goes
INTRADAY_MAX_DAYS = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "1h": 730}


def fetch_intraday_data(
    ticker: str,
    interval: str = "5m",
    days: int | None = None,
    max_retries: int = 3,
) -> pd.DataFrame:
    """
    Fetch intraday OHLCV bars from Yahoo Finance for day trading.

    Args:
        ticker: Stock symbol (e.g., 'AAPL', 'RELIANCE.NS')
        interval: Candle interval -- '1m', '2m', '5m', '15m', '30m', '1h'
        days: Days of history (default/cap: Yahoo's max for the interval,
              e.g. 60 for 5m, 7 for 1m)

    Returns:
        DataFrame with columns: open, high, low, close, volume
        Index: DatetimeIndex in the exchange's local time (tz-naive), so
        grouping by calendar date yields trading sessions.
    """
    if interval not in INTRADAY_MAX_DAYS:
        raise ValueError(f"Unsupported intraday interval: {interval}. "
                         f"Choose from {list(INTRADAY_MAX_DAYS)}")

    cap = INTRADAY_MAX_DAYS[interval]
    if days is None:
        days = cap
    elif days > cap:
        print(f"  Note: Yahoo caps {interval} data at {cap} days; using {cap}.")
        days = cap

    stock = yf.Ticker(ticker)
    df = pd.DataFrame()
    for attempt in range(max_retries):
        df = stock.history(period=f"{days}d", interval=interval)
        if not df.empty:
            break

    if df.empty:
        raise ValueError(
            f"Failed to fetch {interval} data for {ticker} after {max_retries} retries."
        )

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def fetch_multiple_stocks(
    tickers: list[str],
    years: int = 10,
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    Fetch data for multiple stocks. Returns a dict of {ticker: DataFrame}.
    Prints progress for each ticker.
    """
    results: dict[str, pd.DataFrame] = {}
    for t in tickers:
        print(f"  Fetching {t} ... ", end="", flush=True)
        try:
            results[t] = fetch_stock_data(t, years=years, interval=interval)
            print(f"+ ({len(results[t])} candles)")
        except Exception as e:
            print(f"! {e}")
    return results


def get_default_stock_data(years: int = 10) -> dict[str, pd.DataFrame]:
    """Fetch all default stock tickers defined in config.yaml."""
    import yaml  # lazy import -- only needed here
    import os

    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    tickers = config["data"]["stocks"]["default_tickers"]
    return fetch_multiple_stocks(tickers, years=years)
