"""
Core strategy logic: signal generation and position sizing.

Generates a signal DataFrame from OHLCV + indicators.
Signals are on bar close; the backtester executes at next bar open.
"""

import numpy as np
import pandas as pd


def calc_position_size(
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    point_value: float = 1.0,
    min_qty: float = 1e-8,
) -> float:
    """
    Returns position size (units) risking risk_pct of equity.
    Returns 0.0 if stop_distance is zero or size rounds below min_qty.
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0.0
    risk_dollars = equity * risk_pct
    size = risk_dollars / (stop_distance * point_value)
    return size if size >= min_qty else 0.0


def generate_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Bar-by-bar signal scan that mirrors the Pine Script logic exactly.

    Returns df with extra columns:
        regime_bull, regime_bear,
        long_signal  (True = enter/add long at NEXT bar open),
        short_signal (True = enter/add short at NEXT bar open),
        signal_stop  (stop price for the incoming layer),
        pullback_low_at_signal  (for long),
        pullback_high_at_signal (for short).

    NOTE: This function only produces *when* and *at what stop* to trade.
    Actual P&L simulation lives in backtester.py.
    """
    s = cfg["strategy"]
    allow_shorts = s.get("allow_shorts", False)

    close = df["Close"].values
    low = df["Low"].values
    high = df["High"].values
    sma200 = df["sma200"].values
    ema21 = df["ema21"].values
    atr14 = df["atr14"].values
    n = len(df)

    long_signal = np.zeros(n, dtype=bool)
    short_signal = np.zeros(n, dtype=bool)
    signal_stop_long = np.full(n, np.nan)
    signal_stop_short = np.full(n, np.nan)

    was_below_ema_long = False
    pullback_low = np.inf
    was_above_ema_short = False
    pullback_high = -np.inf

    for i in range(n):
        # Skip bars where indicators are not yet available
        if np.isnan(sma200[i]) or np.isnan(ema21[i]) or np.isnan(atr14[i]):
            was_below_ema_long = False
            pullback_low = np.inf
            was_above_ema_short = False
            pullback_high = -np.inf
            continue

        regime_bull = close[i] > sma200[i]
        regime_bear = close[i] < sma200[i]

        # ── LONG SIDE ──────────────────────────────────────────────────────────
        if regime_bull:
            if close[i] < ema21[i]:
                was_below_ema_long = True
                pullback_low = min(pullback_low, low[i])

            if was_below_ema_long and close[i] > ema21[i] and pullback_low < np.inf:
                stop = pullback_low - s["stop_buffer_atr_mult"] * atr14[i]
                long_signal[i] = True
                signal_stop_long[i] = stop
                # Reset: fresh pullback required for next add
                was_below_ema_long = False
                pullback_low = np.inf
        else:
            # Regime flipped — reset long pullback tracking
            was_below_ema_long = False
            pullback_low = np.inf

        # ── SHORT SIDE ─────────────────────────────────────────────────────────
        if allow_shorts:
            if regime_bear:
                if close[i] > ema21[i]:
                    was_above_ema_short = True
                    pullback_high = max(pullback_high, high[i])

                if was_above_ema_short and close[i] < ema21[i] and pullback_high > -np.inf:
                    stop = pullback_high + s["stop_buffer_atr_mult"] * atr14[i]
                    short_signal[i] = True
                    signal_stop_short[i] = stop
                    was_above_ema_short = False
                    pullback_high = -np.inf
            else:
                was_above_ema_short = False
                pullback_high = -np.inf

    result = df.copy()
    result["regime_bull"] = close > sma200
    result["regime_bear"] = close < sma200
    result["long_signal"] = long_signal
    result["short_signal"] = short_signal
    result["signal_stop_long"] = signal_stop_long
    result["signal_stop_short"] = signal_stop_short
    return result
