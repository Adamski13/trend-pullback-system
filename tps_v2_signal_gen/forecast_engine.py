"""
TPS v2 Forecast Engine
Computes EWMAC forecasts from price data.
Same logic as the backtester — can be used with any data source.
"""

import numpy as np
import pandas as pd


def compute_forecast(prices: pd.Series, config: dict) -> dict:
    """
    Compute the TPS v2 forecast for a single instrument.
    
    Args:
        prices: Series of daily closing prices (DatetimeIndex)
        config: dict with ewmac, regime, forecast, sizing keys
    
    Returns:
        dict with:
            forecast: float (0-20)
            components: dict of individual EWMAC forecasts
            regime: bool (True = bullish)
            inst_vol: float (annualized)
            sma_200: float
            price: float (latest close)
    """
    if len(prices) < 250:
        return {
            'forecast': 0.0, 'components': {}, 'regime': False,
            'inst_vol': 0.0, 'sma_200': 0.0, 'price': prices.iloc[-1] if len(prices) > 0 else 0.0
        }
    
    ewmac_cfg = config['ewmac']
    vol_lookback = config['sizing']['vol_lookback_days']
    regime_period = config['regime']['sma_period']
    fc_cap = config['forecast']['cap']
    fc_floor = config['forecast']['floor']
    
    # ─── Volatility (price-change vol for signal normalization) ───────
    price_changes = prices.diff()
    price_vol = price_changes.ewm(span=vol_lookback, min_periods=vol_lookback).std()
    
    # ─── Instrument vol (return-based, annualized, for sizing) ────────
    returns = prices.pct_change()
    daily_vol = returns.ewm(span=vol_lookback, min_periods=vol_lookback).std()
    inst_vol = daily_vol.iloc[-1] * np.sqrt(256)
    
    # ─── EWMAC components ────────────────────────────────────────────
    components = {}
    for fast, slow in ewmac_cfg['variations']:
        key = f"{fast}_{slow}"
        ema_fast = prices.ewm(span=fast, min_periods=fast).mean()
        ema_slow = prices.ewm(span=slow, min_periods=slow).mean()
        raw = ema_fast - ema_slow
        
        pv = price_vol.iloc[-1]
        if pv > 0 and not np.isnan(pv):
            risk_adj = raw.iloc[-1] / pv
        else:
            risk_adj = 0.0
        
        scalar = ewmac_cfg['forecast_scalars'][key]
        scaled = risk_adj * scalar
        components[key] = scaled
    
    # ─── Blend ────────────────────────────────────────────────────────
    blended = 0.0
    for key, fc in components.items():
        weight = ewmac_cfg['forecast_weights'][key]
        blended += weight * fc
    
    blended *= ewmac_cfg['forecast_div_multiplier']
    
    # ─── Regime ───────────────────────────────────────────────────────
    sma_200 = prices.rolling(regime_period).mean().iloc[-1]
    regime_bull = prices.iloc[-1] > sma_200
    
    # ─── Final forecast ──────────────────────────────────────────────
    if config['regime']['enabled'] and not regime_bull:
        forecast = 0.0
    else:
        forecast = max(fc_floor, min(blended, fc_cap))
    
    return {
        'forecast': round(forecast, 2),
        'components': {k: round(v, 2) for k, v in components.items()},
        'regime': regime_bull,
        'inst_vol': round(inst_vol, 4),
        'sma_200': round(sma_200, 2),
        'price': round(prices.iloc[-1], 2),
    }


def compute_target_position(forecast: float, inst_vol: float, price: float,
                             capital: float, config: dict,
                             instrument: str) -> float:
    """
    Carver-style position sizing.
    
    Returns target position in units (not lots).
    """
    if inst_vol <= 0 or price <= 0 or forecast <= 0:
        return 0.0
    
    vol_target = config['sizing']['vol_target_pct']
    weight = config['sizing']['instrument_weights'].get(instrument, 0.333)
    idm = config['sizing']['instrument_div_multiplier']
    
    numerator = capital * vol_target * weight * idm * (forecast / 10.0)
    denominator = inst_vol * price
    
    return numerator / denominator


def should_rebalance(target: float, current: float, avg_position: float,
                     buffer_fraction: float) -> bool:
    """Check if the position change exceeds the buffer threshold."""
    if avg_position <= 0:
        return target != current
    threshold = buffer_fraction * avg_position
    return abs(target - current) >= threshold
