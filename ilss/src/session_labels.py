"""
ILSS Session Labeller

Assigns session labels and computes key levels (PDH/PDL, Asian H/L)
for each bar in a 15-min OHLCV DataFrame.

All times are UTC throughout.
"""

import pandas as pd
import numpy as np

# Session boundaries in UTC (hour, minute)
SESSIONS = {
    "asian":         (0,  0,   7,  0),   # 00:00–07:00
    "london_open":   (7,  0,   9,  0),   # 07:00–09:00
    "london":        (9,  0,  12,  0),   # 09:00–12:00
    "ny_open":       (12, 0,  15,  0),   # 12:00–15:00
    "ny_afternoon":  (15, 0,  19,  0),   # 15:00–19:00
    "ny_close":      (19, 0,  21,  0),   # 19:00–21:00
    "off_hours":     (21, 0,  24,  0),   # 21:00–00:00
}


def _time_in_minutes(dt: pd.Timestamp) -> int:
    return dt.hour * 60 + dt.minute


def assign_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a 'session' column to a OHLCV DataFrame.
    Index must be UTC DatetimeIndex.
    """
    df = df.copy()
    mins = df.index.hour * 60 + df.index.minute

    conditions = [
        (mins >= 0)   & (mins < 420),    # 00:00–07:00  asian
        (mins >= 420) & (mins < 540),    # 07:00–09:00  london_open
        (mins >= 540) & (mins < 720),    # 09:00–12:00  london
        (mins >= 720) & (mins < 900),    # 12:00–15:00  ny_open
        (mins >= 900) & (mins < 1140),   # 15:00–19:00  ny_afternoon
        (mins >= 1140)& (mins < 1260),   # 19:00–21:00  ny_close
        (mins >= 1260),                  # 21:00+       off_hours
    ]
    labels = [
        "asian", "london_open", "london",
        "ny_open", "ny_afternoon", "ny_close", "off_hours",
    ]
    df["session"] = np.select(conditions, labels, default="off_hours")
    return df


def compute_daily_levels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add previous-day high/low columns to the DataFrame.

    PDH/PDL are defined as the high/low of the CALENDAR day (UTC 00:00–00:00).
    Each bar gets the prior day's high and low — these become the key levels
    for that day's trading.
    """
    df = df.copy()
    df.index.name = None            # avoid "date is both index and column" error
    df["_time"] = df.index          # save DatetimeIndex before merge resets it
    df["date"]  = df.index.normalize()

    daily = (
        df.groupby("date")
        .agg(day_high=("High", "max"), day_low=("Low", "min"))
        .reset_index()
    )
    daily["prev_day_high"] = daily["day_high"].shift(1)
    daily["prev_day_low"]  = daily["day_low"].shift(1)

    df = df.merge(
        daily[["date", "prev_day_high", "prev_day_low"]],
        on="date", how="left"
    )
    df = df.set_index("_time")
    df.index.name = None
    df.drop(columns="date", inplace=True)
    return df


def compute_asian_levels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add asian_high and asian_low columns.

    Asian session = 00:00–07:00 UTC.
    Each bar in London Open / NY / etc gets the high and low from
    THAT DAY's Asian session (00:00–07:00 UTC same calendar day).

    Bars within the Asian session itself get NaN (the session isn't complete yet).
    """
    df = df.copy()
    df.index.name = None            # avoid "date is both index and column" error
    df["_time"] = df.index
    df["date"]  = df.index.normalize()

    asian_bars = df[df["session"] == "asian"]
    asian_levels = (
        asian_bars.groupby("date")
        .agg(asian_high=("High", "max"), asian_low=("Low", "min"))
        .reset_index()
    )

    df = df.merge(asian_levels, on="date", how="left")
    df = df.set_index("_time")
    df.index.name = None

    in_asian = df["session"] == "asian"
    df.loc[in_asian, "asian_high"] = np.nan
    df.loc[in_asian, "asian_low"]  = np.nan

    df.drop(columns="date", inplace=True)
    return df


def compute_weekly_levels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add prev_week_high and prev_week_low columns.

    Week = ISO calendar week (Mon–Fri for FX).
    Each bar gets the prior complete week's high and low.
    Uses ISO year+week as key to handle year-boundary weeks correctly.
    """
    df = df.copy()
    df.index.name = None
    df["_time"] = df.index

    iso          = df.index.isocalendar()
    df["_wkey"]  = (iso["year"].astype(str) + "_"
                    + iso["week"].astype(str).str.zfill(2))

    weekly = (
        df.groupby("_wkey")
        .agg(week_high=("High", "max"), week_low=("Low", "min"))
        .reset_index()
        .sort_values("_wkey")
    )
    weekly["prev_week_high"] = weekly["week_high"].shift(1)
    weekly["prev_week_low"]  = weekly["week_low"].shift(1)

    df = df.merge(weekly[["_wkey", "prev_week_high", "prev_week_low"]],
                  on="_wkey", how="left")
    df = df.set_index("_time")
    df.index.name = None
    df.drop(columns="_wkey", inplace=True)
    return df


def compute_intraday_session_levels(df: pd.DataFrame, source_session: str) -> pd.DataFrame:
    """
    Compute H/L for a named session and make it available to bars in later
    sessions on the same calendar day.

    Adds columns: {source_session}_high, {source_session}_low
    Bars within or before the source session get NaN (level not yet complete).

    Session order (UTC):
        asian → london_open → london → ny_open → ny_afternoon → ny_close → off_hours
    """
    SESSION_ORDER = ["asian", "london_open", "london", "ny_open",
                     "ny_afternoon", "ny_close", "off_hours"]
    if source_session not in SESSION_ORDER:
        raise ValueError(f"Unknown session: {source_session}")

    df = df.copy()
    df.index.name = None
    df["_time"] = df.index
    df["date"]  = df.index.normalize()

    col_h = f"{source_session}_high"
    col_l = f"{source_session}_low"

    src_bars = df[df["session"] == source_session]
    if src_bars.empty:
        df[col_h] = np.nan
        df[col_l] = np.nan
    else:
        src_levels = (
            src_bars.groupby("date")
            .agg(**{col_h: ("High", "max"), col_l: ("Low", "min")})
            .reset_index()
        )
        df = df.merge(src_levels, on="date", how="left")

    df = df.set_index("_time")
    df.index.name = None

    # Null out bars in or before the source session (level not yet settled)
    src_idx = SESSION_ORDER.index(source_session)
    sessions_up_to = set(SESSION_ORDER[:src_idx + 1])
    mask = df["session"].isin(sessions_up_to)
    df.loc[mask, col_h] = np.nan
    df.loc[mask, col_l] = np.nan

    df.drop(columns="date", inplace=True)
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ATR column using standard True Range definition."""
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    df["atr"] = tr.ewm(span=period, min_periods=period).mean()
    return df


def prepare(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    """
    Full preparation pipeline: sessions + levels + ATR.
    Call this once after loading raw OHLCV data.

    Returns enriched DataFrame ready for SFP detection.
    """
    df = assign_sessions(df)
    df = compute_daily_levels(df)
    df = compute_asian_levels(df)
    df = compute_weekly_levels(df)
    df = compute_atr(df, period=atr_period)
    return df
