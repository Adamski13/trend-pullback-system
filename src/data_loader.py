"""
Data loader: fetch and cache OHLCV data.

Primary source: yfinance (Yahoo Finance).
Fallback source: stooq via pandas_datareader (no auth required).
Data is cached to disk as parquet to avoid repeated downloads.
"""

import os
import pandas as pd
import yfinance as yf
from datetime import date


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Map ticker symbols to stooq equivalents for the fallback path
_STOOQ_MAP = {
    "QQQ": "qqq.us",
    "SPY": "spy.us",
    "GLD": "gld.us",
    "BTC-USD": "btc-usd.cr",
    "^GDAXI": "^dax",
}


def _cache_path(symbol: str, start: str, end: str) -> str:
    end_tag = end if end else "today"
    fname = f"{symbol}_{start}_{end_tag}.parquet".replace("/", "-").replace("^", "")
    return os.path.join(DATA_DIR, fname)


def _fetch_yfinance(symbol: str, start: str, end: str) -> pd.DataFrame:
    # yfinance 1.x manages its own curl_cffi session; do not pass a custom session
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)

    # yfinance 1.x may return multi-level columns when using download()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def _fetch_stooq(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fallback: pandas_datareader stooq (no auth, no rate-limiting)."""
    try:
        from pandas_datareader import data as pdr
    except ImportError:
        raise ImportError(
            "pandas_datareader not installed. Run: pip install pandas-datareader"
        )
    stooq_sym = _STOOQ_MAP.get(symbol, symbol.lower() + ".us")
    df = pdr.DataReader(stooq_sym, "stooq", start=start, end=end)
    df = df.rename(columns=str.title)  # Open/High/Low/Close/Volume
    df.sort_index(inplace=True)
    return df


def load_data(symbol: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """
    Return a DataFrame with columns: Open, High, Low, Close, Volume.
    Tries yfinance first; falls back to stooq on failure.
    Results are cached to disk as parquet.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    end = end_date if end_date else date.today().isoformat()
    cache = _cache_path(symbol, start_date, end)

    if os.path.exists(cache):
        df = pd.read_parquet(cache)
    else:
        df = pd.DataFrame()

        # ── Try yfinance ──────────────────────────────────────────────────────
        try:
            df = _fetch_yfinance(symbol, start_date, end)
            if not df.empty:
                print(f"  [{symbol}] fetched via yfinance")
        except Exception as e:
            print(f"  [{symbol}] yfinance failed ({e}), trying stooq...")

        # ── Fallback: stooq ───────────────────────────────────────────────────
        if df.empty:
            try:
                df = _fetch_stooq(symbol, start_date, end)
                if not df.empty:
                    print(f"  [{symbol}] fetched via stooq")
            except Exception as e:
                print(f"  [{symbol}] stooq failed ({e})")

        if df.empty:
            raise ValueError(
                f"No data returned for {symbol}. "
                "Yahoo Finance may be rate-limiting. Try again in a few minutes, "
                "or install pandas-datareader for the stooq fallback: "
                "pip install pandas-datareader"
            )

        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[keep].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        df.to_parquet(cache)

    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.sort_index(inplace=True)
    return df
