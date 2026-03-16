"""
Data loader: fetch and cache OHLCV data.

Primary source: yfinance (Yahoo Finance).
Fallback source: stooq via pandas_datareader (no auth required).
Data is cached to disk as parquet to avoid repeated downloads.
"""

import os
import requests
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
    "USO": "uso.us",
    "SLV": "slv.us",
    "EWG": "ewg.us",
    "EWJ": "ewj.us",
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


# Binance symbol map: ticker → Binance trading pair
_BINANCE_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
}


def _fetch_binance(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Fallback for crypto: Binance public klines API, no auth required.
    Paginates automatically to cover the full date range.
    """
    pair = _BINANCE_MAP.get(symbol)
    if not pair:
        raise ValueError(f"No Binance mapping for {symbol}")

    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    url = "https://api.binance.com/api/v3/klines"
    rows = []

    while start_ms < end_ms:
        params = {
            "symbol": pair,
            "interval": "1d",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        # Each row: [open_time, open, high, low, close, volume, ...]
        last_open_time = batch[-1][0]
        if last_open_time <= start_ms:
            break
        start_ms = last_open_time + 1

    if not rows:
        raise ValueError(f"Binance returned no data for {pair}")

    df = pd.DataFrame(rows, columns=[
        "ts", "Open", "High", "Low", "Close", "Volume",
        "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore"
    ])
    df = df[["ts", "Open", "High", "Low", "Close", "Volume"]].copy()
    df["Date"] = pd.to_datetime(df["ts"], unit="ms").dt.normalize()
    df = df.drop_duplicates("Date").set_index("Date").drop(columns="ts")
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = df[col].astype(float)
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
                print(f"  [{symbol}] stooq failed ({e}), trying CoinGecko...")

        # ── Fallback: Binance (crypto only) ───────────────────────────────────
        if df.empty and symbol in _BINANCE_MAP:
            try:
                df = _fetch_binance(symbol, start_date, end)
                if not df.empty:
                    print(f"  [{symbol}] fetched via Binance")
            except Exception as e:
                print(f"  [{symbol}] Binance failed ({e})")

        if df.empty:
            raise ValueError(f"No data returned for {symbol} from any source.")

        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[keep].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        df.to_parquet(cache)

    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.sort_index(inplace=True)
    return df
