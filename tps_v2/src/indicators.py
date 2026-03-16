"""
TPS v2 Indicators Module
Implements EWMAC signals and volatility estimation following Carver's framework.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=span, min_periods=span).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period, min_periods=period).mean()


def daily_returns(price: pd.Series) -> pd.Series:
    """Percentage daily returns."""
    return price.pct_change()


def instrument_volatility(price: pd.Series, lookback: int = 25) -> pd.Series:
    """
    Estimate annualized instrument volatility using exponentially weighted
    standard deviation of daily returns.
    
    Carver uses ~25-day half-life. Annualize by sqrt(256).
    
    Returns:
        Annualized volatility as a fraction (e.g., 0.20 = 20%)
    """
    returns = daily_returns(price)
    vol_daily = returns.ewm(span=lookback, min_periods=max(lookback, 10)).std()
    vol_annual = vol_daily * np.sqrt(256)
    return vol_annual


def price_volatility(price: pd.Series, lookback: int = 25) -> pd.Series:
    """
    Volatility of daily price CHANGES (not returns).
    Used to normalize raw EWMAC signal.
    
    This is the standard deviation of (price_t - price_{t-1}),
    which is needed to make the EWMAC signal comparable across instruments.
    """
    price_changes = price.diff()
    vol = price_changes.ewm(span=lookback, min_periods=max(lookback, 10)).std()
    return vol


def ewmac_raw(price: pd.Series, fast_span: int, slow_span: int) -> pd.Series:
    """
    Raw EWMAC signal: EMA(fast) - EMA(slow)
    
    Positive = bullish (fast above slow = uptrend)
    Negative = bearish (fast below slow = downtrend)
    """
    fast_ema = ema(price, fast_span)
    slow_ema = ema(price, slow_span)
    return fast_ema - slow_ema


def ewmac_forecast(price: pd.Series, fast_span: int, slow_span: int,
                    forecast_scalar: float, vol_lookback: int = 25) -> pd.Series:
    """
    Scaled EWMAC forecast.
    
    Steps:
    1. Raw signal = EMA(fast) - EMA(slow)
    2. Risk-adjust by dividing by price volatility (std of daily price changes)
    3. Multiply by forecast scalar so average |forecast| = 10
    
    Args:
        price: Daily closing prices
        fast_span: Fast EMA period
        slow_span: Slow EMA period
        forecast_scalar: Pre-calculated scalar for this speed variation
        vol_lookback: Lookback for volatility estimation
    
    Returns:
        Scaled forecast series (uncapped)
    """
    raw = ewmac_raw(price, fast_span, slow_span)
    vol = price_volatility(price, vol_lookback)
    
    # Avoid division by zero
    vol = vol.replace(0, np.nan)
    
    # Risk-adjusted forecast
    risk_adjusted = raw / vol
    
    # Scale so average absolute value ≈ 10
    scaled = risk_adjusted * forecast_scalar
    
    return scaled


def combined_forecast(price: pd.Series, variations: list, forecast_scalars: dict,
                      forecast_weights: dict, forecast_div_multiplier: float,
                      forecast_cap: float = 20.0, forecast_floor: float = 0.0,
                      vol_lookback: int = 25) -> pd.Series:
    """
    Combine multiple EWMAC variations into a single forecast.
    
    Steps:
    1. Calculate scaled forecast for each variation
    2. Weighted average
    3. Apply Forecast Diversification Multiplier (FDM)
    4. Cap at ±20 (or floor at 0 for long-only)
    
    Args:
        price: Daily closing prices
        variations: List of (fast, slow) tuples
        forecast_scalars: Dict mapping "fast_slow" to scalar
        forecast_weights: Dict mapping "fast_slow" to weight
        forecast_div_multiplier: FDM (typically 1.1-1.5)
        forecast_cap: Maximum forecast value
        forecast_floor: Minimum forecast value (0 for long-only)
        vol_lookback: Lookback for volatility estimation
    
    Returns:
        Combined, capped forecast series
    """
    forecasts = {}
    
    for fast, slow in variations:
        key = f"{fast}_{slow}"
        scalar = forecast_scalars[key]
        fc = ewmac_forecast(price, fast, slow, scalar, vol_lookback)
        forecasts[key] = fc
    
    # Weighted combination
    combined = pd.Series(0.0, index=price.index)
    for key, fc in forecasts.items():
        weight = forecast_weights[key]
        combined += weight * fc.fillna(0)
    
    # Apply FDM
    combined *= forecast_div_multiplier
    
    # Cap and floor
    combined = combined.clip(lower=forecast_floor, upper=forecast_cap)
    
    return combined


def regime_filter(price: pd.Series, sma_period: int = 200) -> pd.Series:
    """
    Binary regime filter: 1 if price > SMA, 0 otherwise.
    Used to floor forecasts at 0 when regime is bearish.
    """
    regime_ma = sma(price, sma_period)
    return (price > regime_ma).astype(float)
