"""
ILSS SFP Detector

Detects Swing Failure Patterns (liquidity sweeps) at key levels.

A bullish SFP is defined as:
  1. Candle low sweeps BELOW a key level (low < level)
  2. Candle closes BACK ABOVE the level (close > level)
  3. Sweep depth is meaningful: (level - low) > min_depth * ATR
  4. Sweep depth is not excessive: (level - low) < max_depth * ATR

Each detected SFP is returned as a dict with full metadata for
backtesting and analysis.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict


@dataclass
class SFP:
    """A single detected Swing Failure Pattern."""
    # Identity
    time:          pd.Timestamp   # bar timestamp
    symbol:        str
    direction:     str            # "bull" or "bear"

    # Level swept
    level_type:    str            # "prev_day_low", "prev_day_high", "asian_low", "asian_high"
    level_price:   float

    # Bar data
    open:          float
    high:          float
    low:           float
    close:         float
    atr:           float

    # Sweep geometry
    sweep_depth:   float          # how far price went through the level
    sweep_depth_r: float          # sweep_depth / ATR

    # Session context
    session:       str

    # Entry / stop
    entry_price:   float          # close of confirmation bar
    stop_price:    float          # wick extreme + buffer

    # Risk
    stop_distance: float          # |entry - stop|
    stop_distance_r: float        # stop_distance / ATR


def detect_sfps(
    df: pd.DataFrame,
    symbol: str,
    min_sweep_atr: float = 0.25,
    max_sweep_atr: float = 1.5,
    stop_buffer_atr: float = 0.25,
    active_sessions: list[str] | None = None,
) -> pd.DataFrame:
    """
    Scan a prepared OHLCV DataFrame for bullish SFPs at PDL and Asian Low.

    Args:
        df:               Prepared DataFrame (output of session_labels.prepare())
                          Must have columns: Open, High, Low, Close, atr,
                          session, prev_day_low, prev_day_high,
                          asian_low, asian_high
        symbol:           Instrument name (for labelling)
        min_sweep_atr:    Minimum sweep depth as multiple of ATR
        max_sweep_atr:    Maximum sweep depth as multiple of ATR
        stop_buffer_atr:  Extra buffer beyond wick for stop placement
        active_sessions:  Only detect SFPs in these sessions.
                          None = all sessions.

    Returns:
        DataFrame of SFP events, one row per detection.
        Empty DataFrame if none found.
    """
    required = {"Open", "High", "Low", "Close", "atr",
                "session", "prev_day_low", "prev_day_high",
                "asian_low", "asian_high"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing columns: {missing}")

    records = []

    # Level definitions: (column_name, level_type, sfp_direction)
    level_defs = [
        ("prev_day_low",  "prev_day_low",  "bull"),   # sweep below PDL → bull
        ("prev_day_high", "prev_day_high", "bear"),   # sweep above PDH → bear
        ("asian_low",     "asian_low",     "bull"),   # sweep below Asian low → bull
        ("asian_high",    "asian_high",    "bear"),   # sweep above Asian high → bear
    ]

    for i in range(len(df)):
        row = df.iloc[i]

        # Session filter
        if active_sessions and row["session"] not in active_sessions:
            continue

        atr = row["atr"]
        if pd.isna(atr) or atr <= 0:
            continue

        for col, level_type, direction in level_defs:
            level = row[col]
            if pd.isna(level):
                continue

            if direction == "bull":
                # Wick sweeps below level, candle closes above
                if row["Low"] >= level:
                    continue                              # no sweep
                if row["Close"] <= level:
                    continue                              # didn't close back above
                sweep_depth = level - row["Low"]

            else:  # bear
                # Wick sweeps above level, candle closes below
                if row["High"] <= level:
                    continue
                if row["Close"] >= level:
                    continue
                sweep_depth = row["High"] - level

            sweep_r = sweep_depth / atr

            # Depth filters
            if sweep_r < min_sweep_atr:
                continue
            if sweep_r > max_sweep_atr:
                continue

            # Entry and stop
            entry = row["Close"]
            if direction == "bull":
                stop  = row["Low"] - stop_buffer_atr * atr
            else:
                stop  = row["High"] + stop_buffer_atr * atr

            stop_dist   = abs(entry - stop)
            stop_dist_r = stop_dist / atr if atr > 0 else np.nan

            records.append(SFP(
                time=df.index[i],
                symbol=symbol,
                direction=direction,
                level_type=level_type,
                level_price=level,
                open=row["Open"],
                high=row["High"],
                low=row["Low"],
                close=row["Close"],
                atr=atr,
                sweep_depth=sweep_depth,
                sweep_depth_r=sweep_r,
                session=row["session"],
                entry_price=entry,
                stop_price=stop,
                stop_distance=stop_dist,
                stop_distance_r=stop_dist_r,
            ))

    if not records:
        return pd.DataFrame()

    result = pd.DataFrame([asdict(r) for r in records])
    result["time"] = pd.to_datetime(result["time"])
    result = result.set_index("time")
    return result


def sfp_summary(sfps: pd.DataFrame, symbol: str = "") -> dict:
    """
    Print and return summary statistics for a set of detected SFPs.
    Useful for Phase 1 (raw SFP statistics).
    """
    if sfps.empty:
        print(f"  {symbol}: no SFPs detected")
        return {}

    bull = sfps[sfps["direction"] == "bull"]
    bear = sfps[sfps["direction"] == "bear"]

    by_level = sfps.groupby("level_type").size().to_dict()
    by_session = sfps.groupby("session").size().to_dict()

    summary = {
        "symbol":           symbol,
        "total":            len(sfps),
        "bull":             len(bull),
        "bear":             len(bear),
        "by_level":         by_level,
        "by_session":       by_session,
        "avg_sweep_r":      round(sfps["sweep_depth_r"].mean(), 3),
        "avg_stop_dist_r":  round(sfps["stop_distance_r"].mean(), 3),
        "per_year":         round(len(sfps) / 6, 1),   # 2020–2025 = ~6 years
        "per_week":         round(len(sfps) / (6 * 52), 2),
    }

    print(f"\n  {symbol or 'ALL'}")
    print(f"    Total SFPs:    {summary['total']:>6,}  ({summary['per_year']:.0f}/yr, {summary['per_week']:.1f}/wk)")
    print(f"    Bull / Bear:   {summary['bull']:>6,} / {summary['bear']:<6,}")
    print(f"    By level:      {by_level}")
    print(f"    By session:    {by_session}")
    print(f"    Avg sweep:     {summary['avg_sweep_r']:.2f}× ATR")
    print(f"    Avg stop dist: {summary['avg_stop_dist_r']:.2f}× ATR")

    return summary
