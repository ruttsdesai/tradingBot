"""
Collect crypto market data using CCXT.
Default: 4 years of daily OHLCV data from Binance.
"""

from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd


def fetch_crypto_data(
    symbol: str,
    years: int = 4,
    interval: str = "1d",
    exchange_id: str = "binance",
) -> pd.DataFrame:
    """
    Fetch historical crypto OHLCV data from a CCXT exchange.

    Args:
        symbol: Trading pair (e.g., 'BTC/USDT', 'ETH/USDT')
        years: Years of data (default 4 -> 2022-2026)
        interval: Candle interval -- '1d', '1h', '15m', '5m', '1m'
        exchange_id: CCXT exchange ID (binance, coinbase, bybit, etc.)

    Returns:
        DataFrame with columns: open, high, low, close, volume
        Index: DatetimeIndex (UTC)
    """
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({"enableRateLimit": True})

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=365 * years)

    # CCXT requires timestamps in milliseconds
    since = int(start.timestamp() * 1000)

    all_candles: list[list] = []
    while since < int(end.timestamp() * 1000):
        candles = exchange.fetch_ohlcv(
            symbol=symbol,
            timeframe=interval,
            since=since,
            limit=1000,  # max candles per request
        )

        if not candles:
            break

        all_candles.extend(candles)

        # Advance `since` past the last candle fetched
        since = candles[-1][0] + 1

    if not all_candles:
        raise ValueError(
            f"No data returned for {symbol} on {exchange_id}. "
            f"Date range: {start.date()} -> {end.date()}"
        )

    df = pd.DataFrame(
        all_candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df.index = df.index.tz_localize(None)

    # Drop duplicate rows (can happen with pagination overlap)
    df = df[~df.index.duplicated(keep="first")]

    return df


def fetch_multiple_crypto(
    symbols: list[str],
    years: int = 4,
    interval: str = "1d",
    exchange_id: str = "binance",
) -> dict[str, pd.DataFrame]:
    """
    Fetch data for multiple crypto pairs. Returns dict of {symbol: DataFrame}.
    """
    results: dict[str, pd.DataFrame] = {}
    for s in symbols:
        print(f"  Fetching {s} ... ", end="", flush=True)
        try:
            results[s] = fetch_crypto_data(
                s,
                years=years,
                interval=interval,
                exchange_id=exchange_id,
            )
            print(f"+ ({len(results[s])} candles)")
        except Exception as e:
            print(f"! {e}")
    return results


def get_default_crypto_data(years: int = 4) -> dict[str, pd.DataFrame]:
    """Fetch all default crypto tickers defined in config.yaml."""
    import yaml
    import os

    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    symbols = config["data"]["crypto"]["default_tickers"]
    return fetch_multiple_crypto(symbols, years=years)
