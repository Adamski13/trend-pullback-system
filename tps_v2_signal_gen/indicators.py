"""
TPS v2 Indicators — self-contained module.
EWMAC signals, volatility estimation, regime filter.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, min_periods=span).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def instrument_volatility(price: pd.Series, lookback: int = 25) -> pd.Series:
    """Annualized vol from exponentially weighted std of daily returns."""
    returns  = price.pct_change()
    vol_daily = returns.ewm(span=lookback, min_periods=max(lookback, 10)).std()
    return vol_daily * np.sqrt(256)


def price_volatility(price: pd.Series, lookback: int = 25) -> pd.Series:
    """EW std of daily *price changes* — used to normalize raw EWMAC."""
    changes = price.diff()
    return changes.ewm(span=lookback, min_periods=max(lookback, 10)).std()


def ewmac_forecast(price: pd.Series, fast: int, slow: int,
                   scalar: float, vol_lookback: int = 25) -> pd.Series:
    """Single EWMAC variation: normalized and scaled to mean |10|."""
    raw  = ema(price, fast) - ema(price, slow)
    pvol = price_volatility(price, vol_lookback).replace(0, np.nan)
    return (raw / pvol) * scalar


def combined_forecast(price: pd.Series, variations: list,
                      forecast_scalars: dict, forecast_weights: dict,
                      forecast_div_multiplier: float,
                      forecast_cap: float = 20.0,
                      forecast_floor: float = 0.0,
                      vol_lookback: int = 25) -> pd.Series:
    """Blend EWMAC variations → apply FDM → cap/floor."""
    blended = pd.Series(0.0, index=price.index)
    for fast, slow in variations:
        key     = f"{fast}_{slow}"
        fc      = ewmac_forecast(price, fast, slow, forecast_scalars[key], vol_lookback)
        blended += forecast_weights[key] * fc.fillna(0)
    blended *= forecast_div_multiplier
    return blended.clip(lower=forecast_floor, upper=forecast_cap)


def regime_filter(price: pd.Series, sma_period: int = 200) -> pd.Series:
    """1 if price > SMA(sma_period), else 0."""
    return (price > sma(price, sma_period)).astype(float)
