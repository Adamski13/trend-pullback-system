"""
ILSS Daily Bias Computer

Computes a daily EWMAC-based trend forecast for each instrument.
Used in Phase 2 to filter SFPs — only take bull SFPs on bullish-trend days.

Logic mirrors TPS v2 forecast engine but vectorized across all dates,
returning a full time series so Phase 2 can merge bias onto SFP rows by date.

EWMAC pairs tested: (8,32), (16,64), (32,128).
Normalized by price-change EWM volatility (same as TPS v2).
"""

import numpy as np
import pandas as pd


# Carver forecast scalars for standard EWMAC pairs
# Source: "Systematic Trading" appendix — target expected abs forecast = 10
FORECAST_SCALARS = {
    (8,  32):   10.6,
    (16, 64):   7.5,
    (32, 128):  5.3,
}


def compute_daily_bias(
    daily_df: pd.DataFrame,
    ewmac_pairs: list | None = None,
    vol_lookback: int = 25,
    forecast_cap: float = 20.0,
    long_threshold: float = 5.0,
    sma_period: int = 200,
) -> pd.DataFrame:
    """
    Compute daily trend bias from a daily OHLCV DataFrame.

    Args:
        daily_df:       Daily OHLCV DataFrame (DatetimeIndex)
        ewmac_pairs:    List of (fast, slow) EMA pairs. Default: standard 3.
        vol_lookback:   EWM span for price-change vol normalisation.
        forecast_cap:   Cap/floor applied to individual and blended forecast.
        long_threshold: Forecast >= this value → bias = "bull".
        sma_period:     SMA period for secondary regime filter.

    Returns:
        DataFrame indexed by date with columns:
            forecast    — blended EWMAC forecast, clipped to ±cap
            bias        — "bull" / "bear" / "neutral"
            above_sma   — bool: close > sma_period SMA
            sma_200     — rolling SMA value
            <f>_<s>     — individual component forecasts (for diagnostics)
    """
    if ewmac_pairs is None:
        ewmac_pairs = [(8, 32), (16, 64), (32, 128)]

    prices = daily_df["Close"].copy()

    # Price-change vol (EWM std of daily price changes — same units as EWMAC raw)
    price_changes = prices.diff()
    price_vol = price_changes.ewm(span=vol_lookback, min_periods=vol_lookback).std()

    result = pd.DataFrame(index=prices.index)

    components = []
    for fast, slow in ewmac_pairs:
        ema_fast = prices.ewm(span=fast, min_periods=fast).mean()
        ema_slow = prices.ewm(span=slow, min_periods=slow).mean()
        raw      = ema_fast - ema_slow

        # Normalise by price-change vol; avoid division by zero
        risk_adj = raw / price_vol.replace(0, np.nan)

        scalar  = FORECAST_SCALARS.get((fast, slow), 10.0)
        scaled  = (risk_adj * scalar).clip(-forecast_cap, forecast_cap)

        col = f"{fast}_{slow}"
        result[col] = scaled
        components.append(col)

    # Equal-weight blend; FDM ≈ 1.0 for three correlated EWMAC variations
    result["forecast"] = result[components].mean(axis=1).clip(-forecast_cap, forecast_cap)

    # SMA regime
    result["sma_200"]   = prices.rolling(sma_period, min_periods=sma_period).mean()
    result["above_sma"] = prices > result["sma_200"]

    # Bias label
    result["bias"] = "neutral"
    result.loc[result["forecast"] >= long_threshold,  "bias"] = "bull"
    result.loc[result["forecast"] <= -long_threshold, "bias"] = "bear"

    return result
