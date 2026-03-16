"""
TPS v2 Data Loader
Downloads daily OHLCV data from yfinance (primary) with stooq and Binance fallbacks.
"""

import os
import requests
import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import date


DATA_DIR = Path("data")

# Map ticker symbols to stooq equivalents
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

# Binance symbol map: ticker → Binance trading pair
_BINANCE_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
}


def ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def _fetch_yfinance(symbol: str, start: str, end: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _fetch_stooq(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fallback: pandas_datareader stooq (no auth, no rate-limiting)."""
    try:
        from pandas_datareader import data as pdr
    except ImportError:
        raise ImportError("pandas_datareader not installed. Run: pip install pandas-datareader")
    stooq_sym = _STOOQ_MAP.get(symbol, symbol.lower() + ".us")
    df = pdr.DataReader(stooq_sym, "stooq", start=start, end=end)
    df = df.rename(columns=str.title)
    df.sort_index(inplace=True)
    return df


def _fetch_binance(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fallback for crypto: Binance public klines API, no auth required."""
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


def load_data(symbol: str, start_date: str, end_date: str,
              use_cache: bool = True) -> pd.DataFrame:
    """
    Load daily OHLCV data for a symbol.
    Tries yfinance first, then stooq, then Binance (crypto only).
    Results are cached to disk as parquet.

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
        DatetimeIndex
    """
    ensure_data_dir()
    end = end_date if end_date else date.today().isoformat()
    cache_path = DATA_DIR / f"{symbol.replace('-', '_')}_{start_date}_{end}.parquet"

    if use_cache and cache_path.exists():
        print(f"  Loading {symbol} from cache...")
        df = pd.read_parquet(cache_path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        return df

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
            print(f"  [{symbol}] stooq failed ({e}), trying Binance...")

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
    df = df[~df.index.duplicated(keep='first')]

    df.to_parquet(cache_path)
    print(f"  Cached {len(df)} bars for {symbol}")

    return df


def load_multiple(symbols: list, start_date: str, end_date: str,
                  use_cache: bool = True) -> dict:
    """
    Load data for multiple symbols.

    Returns:
        Dict mapping symbol -> DataFrame
    """
    data = {}
    for symbol in symbols:
        try:
            data[symbol] = load_data(symbol, start_date, end_date, use_cache)
        except Exception as e:
            print(f"  WARNING: Could not load {symbol}: {e}")
    return data
