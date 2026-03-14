"""
Data loader: fetch and cache OHLCV data via yfinance.
"""

import os
import pandas as pd
import yfinance as yf
from datetime import date


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _cache_path(symbol: str, start: str, end: str) -> str:
    end_tag = end if end else "today"
    fname = f"{symbol}_{start}_{end_tag}.parquet".replace("/", "-")
    return os.path.join(DATA_DIR, fname)


def load_data(symbol: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """
    Return a DataFrame with columns: Open, High, Low, Close, Volume.
    Signals are generated on Close; orders execute at next Open.
    Data is cached to disk as parquet to avoid repeated downloads.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    end = end_date if end_date else date.today().isoformat()
    cache = _cache_path(symbol, start_date, end)

    if os.path.exists(cache):
        df = pd.read_parquet(cache)
    else:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_date, end=end, interval="1d", auto_adjust=True)
        if df.empty:
            raise ValueError(f"No data returned for {symbol}")
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        df.to_parquet(cache)

    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.sort_index(inplace=True)
    return df
