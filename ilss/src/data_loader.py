"""
ILSS Data Loader

Downloads historical intraday candles from OANDA v20 API with pagination.
Stores as parquet files in data/.

OANDA limit: 5,000 candles per request.
15-min over 5 years ≈ 125,000 candles per instrument → ~25 requests each.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent.parent / "data"

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL     = "https://api-fxtrade.oanda.com"

GRANULARITY_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D": 1440,
}


def _base_url() -> str:
    env = os.environ.get("OANDA_ENV", "practice").lower()
    return LIVE_URL if env == "live" else PRACTICE_URL


def _headers() -> dict:
    token = os.environ.get("OANDA_TOKEN")
    if not token:
        raise EnvironmentError("OANDA_TOKEN environment variable not set")
    return {
        "Authorization":          f"Bearer {token}",
        "Accept-Datetime-Format": "RFC3339",
    }


def _cache_path(symbol: str, granularity: str,
                start: str, end: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    safe = symbol.replace("/", "_")
    return DATA_DIR / f"{safe}_{granularity}_{start}_{end}.parquet"


def fetch_candles_page(symbol: str, granularity: str,
                       from_dt: datetime, to_dt: datetime,
                       count: int = 5000) -> list[dict]:
    """
    Fetch one page of candles (up to 5000) from OANDA.
    Returns list of dicts with keys: time, Open, High, Low, Close, Volume.
    Only complete candles are included.
    """
    params = {
        "granularity": granularity,
        "price":       "M",
        "from":        from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to":          to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count":       count,
    }
    url  = f"{_base_url()}/v3/instruments/{symbol}/candles"
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()

    rows = []
    for c in resp.json().get("candles", []):
        if not c.get("complete", False):
            continue
        rows.append({
            "time":   pd.to_datetime(c["time"]).tz_localize(None),
            "Open":   float(c["mid"]["o"]),
            "High":   float(c["mid"]["h"]),
            "Low":    float(c["mid"]["l"]),
            "Close":  float(c["mid"]["c"]),
            "Volume": int(c["volume"]),
        })
    return rows


def download_instrument(symbol: str, granularity: str = "M15",
                        start_date: str = "2020-01-01",
                        end_date:   str = "2025-12-31",
                        use_cache:  bool = True) -> pd.DataFrame:
    """
    Download full history for one instrument via paginated OANDA requests.

    Args:
        symbol:      OANDA instrument code (e.g. NAS100_USD)
        granularity: M1, M5, M15, M30, H1, H4, D
        start_date:  ISO date string
        end_date:    ISO date string
        use_cache:   Load from parquet if exists

    Returns:
        DataFrame with DatetimeIndex and OHLCV columns, sorted ascending.
    """
    cache = _cache_path(symbol, granularity, start_date, end_date)

    if use_cache and cache.exists():
        print(f"  [{symbol}] loading from cache ({cache.name})")
        df = pd.read_parquet(cache)
        df.index = pd.to_datetime(df.index)
        return df

    minutes_per_bar = GRANULARITY_MINUTES.get(granularity, 15)
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

    # How many bars fit in 5000 candles?
    window_minutes = 5000 * minutes_per_bar
    cursor = start_dt
    all_rows = []
    page = 0

    print(f"  [{symbol}] downloading {granularity} {start_date}→{end_date}...", end="", flush=True)

    while cursor < end_dt:
        page_end = min(
            cursor + pd.Timedelta(minutes=window_minutes),
            end_dt,
        )
        rows = fetch_candles_page(symbol, granularity, cursor, page_end)
        all_rows.extend(rows)

        if not rows:
            # No data in this window (e.g. weekend gap) — advance the cursor
            cursor = page_end
        else:
            # Advance past the last bar we received
            last_time = rows[-1]["time"]
            cursor = last_time + pd.Timedelta(minutes=minutes_per_bar)

        page += 1
        print(".", end="", flush=True)
        time.sleep(0.1)   # be polite to the API

    print(f" {len(all_rows)} bars")

    if not all_rows:
        raise ValueError(f"No data returned for {symbol} {granularity}")

    df = pd.DataFrame(all_rows).set_index("time")
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)

    df.to_parquet(cache)
    print(f"  [{symbol}] cached → {cache.name}")

    return df


def download_all(symbols: list[str], granularity: str = "M15",
                 start_date: str = "2020-01-01",
                 end_date:   str = "2025-12-31",
                 use_cache:  bool = True) -> dict[str, pd.DataFrame]:
    """
    Download data for a list of instruments.
    Returns dict: symbol → DataFrame.
    """
    data = {}
    for symbol in symbols:
        try:
            data[symbol] = download_instrument(
                symbol, granularity, start_date, end_date, use_cache
            )
        except Exception as e:
            print(f"  [{symbol}] FAILED: {e}")
    return data


def load_cached(symbol: str, granularity: str = "M15",
                start_date: str = "2020-01-01",
                end_date:   str = "2025-12-31") -> pd.DataFrame | None:
    """Load from cache only — returns None if not cached."""
    cache = _cache_path(symbol, granularity, start_date, end_date)
    if not cache.exists():
        return None
    df = pd.read_parquet(cache)
    df.index = pd.to_datetime(df.index)
    return df
