"""
Indicator calculations: SMA, EMA, ATR.
All operate on pandas Series and return pandas Series.
"""

import pandas as pd
import numpy as np


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average (Wilder / standard, adjust=False)."""
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """
    Average True Range (Wilder smoothing = RMA).
    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing (same as Pine Script ta.atr)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Add SMA200, EMA21, ATR14 columns to df in-place and return it.
    Column names are derived from config lengths.
    """
    s = cfg["strategy"]
    df = df.copy()
    df["sma200"] = sma(df["Close"], s["regime_ma_length"])
    df["ema21"] = ema(df["Close"], s["ema_length"])
    df["atr14"] = atr(df["High"], df["Low"], df["Close"], s["atr_length"])
    return df
