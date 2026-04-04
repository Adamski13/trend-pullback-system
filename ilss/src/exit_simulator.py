"""
ILSS Exit Simulator

Tests multiple exit strategies on a filtered SFP set.
Returns enriched DataFrames with outcome + pnl_r columns.

Exit strategies:
  fixed_target  — hit profit target (reward_r × stop_dist) or stop
  atr_trail     — trail stop at best_extreme - trail_mult × ATR; no fixed target
  session_close — hold until end of session, exit at close (or stop if hit first)
  time_stop     — hold N bars, exit at close (or stop if hit first)

All strategies:
  - Stop is checked first on each bar (conservative)
  - pnl_r is continuous (not binary) for trail/session/time exits
  - Partial-win and partial-loss both contribute to profit factor
"""

import numpy as np
import pandas as pd


# Session end hours in UTC (exclusive upper bound)
SESSION_END_HOUR = {
    "asian":        7,
    "london_open":  9,
    "london":       12,
    "ny_open":      15,
    "ny_afternoon": 19,
    "ny_close":     21,
    "off_hours":    24,
}


def _pnl_r_bull(exit_price: float, entry: float, stop_dist: float) -> float:
    if stop_dist <= 0:
        return 0.0
    return (exit_price - entry) / stop_dist


def _pnl_r_bear(exit_price: float, entry: float, stop_dist: float) -> float:
    if stop_dist <= 0:
        return 0.0
    return (entry - exit_price) / stop_dist


def simulate_fixed_target(
    sfps: pd.DataFrame,
    price_df: pd.DataFrame,
    reward_r: float = 1.0,
    max_bars: int = 96,
) -> pd.DataFrame:
    """
    Fixed profit target at reward_r × stop_dist.
    Standard implementation — stop checked before target.
    pnl_r: +reward_r (win), -1.0 (loss), 0.0 (timed out)
    """
    if sfps.empty:
        return sfps.copy()

    sfps = sfps.copy()
    price_index = list(price_df.index)
    index_map   = {t: i for i, t in enumerate(price_index)}

    outcomes, bars_held, exit_prices, pnl_r_list = [], [], [], []

    for sfp_time, row in sfps.iterrows():
        entry     = row["entry_price"]
        stop      = row["stop_price"]
        stop_dist = row["stop_distance"]
        direction = row["direction"]
        target    = (entry + reward_r * stop_dist if direction == "bull"
                     else entry - reward_r * stop_dist)

        start_idx = index_map.get(sfp_time)
        if start_idx is None:
            outcomes.append("time"); bars_held.append(0)
            exit_prices.append(entry); pnl_r_list.append(0.0)
            continue

        outcome = "time"; n_bars = 0; exit_price = entry
        end_idx = min(start_idx + 1 + max_bars, len(price_df))

        for j in range(start_idx + 1, end_idx):
            bar = price_df.iloc[j]
            n_bars += 1
            if direction == "bull":
                if bar["Low"] <= stop:
                    outcome = "loss"; exit_price = stop; break
                if bar["High"] >= target:
                    outcome = "win"; exit_price = target; break
            else:
                if bar["High"] >= stop:
                    outcome = "loss"; exit_price = stop; break
                if bar["Low"] <= target:
                    outcome = "win"; exit_price = target; break

        outcomes.append(outcome); bars_held.append(n_bars)
        exit_prices.append(exit_price)
        pnl_r_list.append(reward_r if outcome == "win"
                          else -1.0 if outcome == "loss" else 0.0)

    sfps["outcome"]    = outcomes
    sfps["bars_held"]  = bars_held
    sfps["exit_price"] = exit_prices
    sfps["pnl_r"]      = pnl_r_list
    return sfps


def simulate_atr_trail(
    sfps: pd.DataFrame,
    price_df: pd.DataFrame,
    trail_mult: float = 1.5,
    max_bars: int = 96,
    target_r: float | None = None,   # optional cap; None = no fixed target
) -> pd.DataFrame:
    """
    ATR trailing stop exit.

    Bull: trail_stop = max(initial_stop, best_high - trail_mult × bar_atr)
    Bear: trail_stop = min(initial_stop, best_low  + trail_mult × bar_atr)

    Optional fixed target cap (target_r × stop_dist).
    If neither stop nor target hit within max_bars: exit at last bar close.
    pnl_r is continuous.
    """
    if sfps.empty:
        return sfps.copy()

    sfps      = sfps.copy()
    has_atr   = "atr" in price_df.columns
    price_index = list(price_df.index)
    index_map   = {t: i for i, t in enumerate(price_index)}

    outcomes, bars_held, exit_prices, pnl_r_list = [], [], [], []

    for sfp_time, row in sfps.iterrows():
        entry     = row["entry_price"]
        stop      = row["stop_price"]
        stop_dist = row["stop_distance"]
        direction = row["direction"]
        bar_atr   = row["atr"]   # use SFP-bar ATR as initial trail width

        target = None
        if target_r is not None:
            target = (entry + target_r * stop_dist if direction == "bull"
                      else entry - target_r * stop_dist)

        start_idx = index_map.get(sfp_time)
        if start_idx is None:
            outcomes.append("time_close"); bars_held.append(0)
            exit_prices.append(entry); pnl_r_list.append(0.0)
            continue

        trail_stop  = stop
        best_high   = entry   # for bull
        best_low    = entry   # for bear
        outcome     = "time_close"
        n_bars      = 0
        exit_price  = entry
        end_idx     = min(start_idx + 1 + max_bars, len(price_df))

        for j in range(start_idx + 1, end_idx):
            bar    = price_df.iloc[j]
            n_bars += 1
            atr_j  = bar["atr"] if (has_atr and not pd.isna(bar["atr"]) and bar["atr"] > 0) else bar_atr

            if direction == "bull":
                # Update trail
                if bar["High"] > best_high:
                    best_high  = bar["High"]
                    trail_stop = max(trail_stop, best_high - trail_mult * atr_j)

                # Check stop first
                if bar["Low"] <= trail_stop:
                    outcome    = "loss"
                    exit_price = trail_stop
                    break
                # Check fixed cap target
                if target is not None and bar["High"] >= target:
                    outcome    = "win"
                    exit_price = target
                    break
            else:
                if bar["Low"] < best_low:
                    best_low   = bar["Low"]
                    trail_stop = min(trail_stop, best_low + trail_mult * atr_j)

                if bar["High"] >= trail_stop:
                    outcome    = "loss"
                    exit_price = trail_stop
                    break
                if target is not None and bar["Low"] <= target:
                    outcome    = "win"
                    exit_price = target
                    break

        if outcome == "time_close":
            # Exit at last bar's close
            last_bar   = price_df.iloc[min(start_idx + n_bars, len(price_df) - 1)]
            exit_price = last_bar["Close"]

        outcomes.append(outcome); bars_held.append(n_bars)
        exit_prices.append(exit_price)

        if direction == "bull":
            pnl = _pnl_r_bull(exit_price, entry, stop_dist)
        else:
            pnl = _pnl_r_bear(exit_price, entry, stop_dist)

        if outcome == "win":
            pnl = target_r  # exact target hit
        elif outcome == "loss":
            # trail stop hit — could be better than -1R if trail moved up
            pnl = max(pnl, -1.0)   # never worse than initial stop
        pnl_r_list.append(round(pnl, 4))

    sfps["outcome"]    = outcomes
    sfps["bars_held"]  = bars_held
    sfps["exit_price"] = exit_prices
    sfps["pnl_r"]      = pnl_r_list
    return sfps


def simulate_session_close(
    sfps: pd.DataFrame,
    price_df: pd.DataFrame,
    max_bars: int = 96,
) -> pd.DataFrame:
    """
    Exit at the end of the session in which the SFP occurred.
    If stop is hit before session close, exit at stop (-1R).
    pnl_r is continuous at session close.

    Session end is the last M15 bar whose open is within the session window.
    """
    if sfps.empty:
        return sfps.copy()

    sfps      = sfps.copy()
    price_index = list(price_df.index)
    index_map   = {t: i for i, t in enumerate(price_index)}

    outcomes, bars_held, exit_prices, pnl_r_list = [], [], [], []

    for sfp_time, row in sfps.iterrows():
        entry     = row["entry_price"]
        stop      = row["stop_price"]
        stop_dist = row["stop_distance"]
        direction = row["direction"]
        session   = row.get("session", "")

        # Session end: last bar before session_end_hour UTC, same date
        sess_end_hour = SESSION_END_HOUR.get(session, 24)
        sfp_date      = pd.Timestamp(sfp_time).normalize()
        if sess_end_hour == 24:
            sess_end_time = sfp_date + pd.Timedelta(hours=23, minutes=59)
        else:
            sess_end_time = sfp_date + pd.Timedelta(hours=sess_end_hour) - pd.Timedelta(minutes=15)

        start_idx = index_map.get(sfp_time)
        if start_idx is None:
            outcomes.append("time_close"); bars_held.append(0)
            exit_prices.append(entry); pnl_r_list.append(0.0)
            continue

        outcome    = "time_close"
        n_bars     = 0
        exit_price = entry
        end_idx    = min(start_idx + 1 + max_bars, len(price_df))

        for j in range(start_idx + 1, end_idx):
            bar      = price_df.iloc[j]
            bar_time = price_index[j]
            n_bars  += 1

            # Check stop first
            if direction == "bull" and bar["Low"] <= stop:
                outcome    = "loss"
                exit_price = stop
                break
            elif direction == "bear" and bar["High"] >= stop:
                outcome    = "loss"
                exit_price = stop
                break

            # Check session end
            if bar_time >= sess_end_time:
                outcome    = "time_close"
                exit_price = bar["Close"]
                break

        if outcome == "time_close" and exit_price == entry:
            # Didn't reach session close (max_bars hit first) — use last bar
            last_j = min(start_idx + n_bars, len(price_df) - 1)
            exit_price = price_df.iloc[last_j]["Close"]

        outcomes.append(outcome); bars_held.append(n_bars)
        exit_prices.append(exit_price)

        if outcome == "loss":
            pnl = -1.0
        elif direction == "bull":
            pnl = _pnl_r_bull(exit_price, entry, stop_dist)
        else:
            pnl = _pnl_r_bear(exit_price, entry, stop_dist)
        pnl_r_list.append(round(pnl, 4))

    sfps["outcome"]    = outcomes
    sfps["bars_held"]  = bars_held
    sfps["exit_price"] = exit_prices
    sfps["pnl_r"]      = pnl_r_list
    return sfps


def simulate_time_stop(
    sfps: pd.DataFrame,
    price_df: pd.DataFrame,
    hold_bars: int = 16,      # 16 × 15min = 4h
    target_r: float | None = None,  # optional profit target; if hit before clock runs out, exit early
) -> pd.DataFrame:
    """
    Hold for exactly hold_bars bars, exit at close (or stop / target if hit first).

    target_r: if set, checks for an early exit at entry ± target_r × stop_dist
              on every bar within the time window. Stop is still checked first.
              pnl_r is continuous (target hit → +target_r, stop → -1.0, time → partial).
    """
    if sfps.empty:
        return sfps.copy()

    sfps = sfps.copy()
    price_index = list(price_df.index)
    index_map   = {t: i for i, t in enumerate(price_index)}

    outcomes, bars_held, exit_prices, pnl_r_list = [], [], [], []

    for sfp_time, row in sfps.iterrows():
        entry     = row["entry_price"]
        stop      = row["stop_price"]
        stop_dist = row["stop_distance"]
        direction = row["direction"]

        target = None
        if target_r is not None and stop_dist > 0:
            target = (entry + target_r * stop_dist if direction == "bull"
                      else entry - target_r * stop_dist)

        start_idx = index_map.get(sfp_time)
        if start_idx is None:
            outcomes.append("time_close"); bars_held.append(0)
            exit_prices.append(entry); pnl_r_list.append(0.0)
            continue

        outcome    = "time_close"
        n_bars     = 0
        exit_price = entry
        end_idx    = min(start_idx + 1 + hold_bars, len(price_df))

        for j in range(start_idx + 1, end_idx):
            bar    = price_df.iloc[j]
            n_bars += 1

            # Stop checked first (conservative)
            if direction == "bull" and bar["Low"] <= stop:
                outcome    = "loss"
                exit_price = stop
                break
            elif direction == "bear" and bar["High"] >= stop:
                outcome    = "loss"
                exit_price = stop
                break

            # Profit target (early exit within time window)
            if target is not None:
                if direction == "bull" and bar["High"] >= target:
                    outcome    = "win"
                    exit_price = target
                    break
                elif direction == "bear" and bar["Low"] <= target:
                    outcome    = "win"
                    exit_price = target
                    break

            # Last bar in window — exit at close
            if j == end_idx - 1:
                outcome    = "time_close"
                exit_price = bar["Close"]

        outcomes.append(outcome); bars_held.append(n_bars)
        exit_prices.append(exit_price)

        if outcome == "loss":
            pnl = -1.0
        elif outcome == "win":
            pnl = target_r  # exact target hit
        elif direction == "bull":
            pnl = _pnl_r_bull(exit_price, entry, stop_dist)
        else:
            pnl = _pnl_r_bear(exit_price, entry, stop_dist)
        pnl_r_list.append(round(pnl, 4))

    sfps["outcome"]    = outcomes
    sfps["bars_held"]  = bars_held
    sfps["exit_price"] = exit_prices
    sfps["pnl_r"]      = pnl_r_list
    return sfps


# ── Stats ──────────────────────────────────────────────────────────────────────

def exit_stats(sfps: pd.DataFrame, label: str = "", friction_r: float = 0.0) -> dict:
    """
    Compute stats from any exit-simulated SFP DataFrame.
    Works with continuous pnl_r (trail/session/time) and binary (fixed target).

    friction_r: round-trip friction in R units (spread + slippage).
                Subtracted from every trade's pnl_r.
    """
    if sfps.empty or "pnl_r" not in sfps.columns:
        return {}

    pnl = sfps["pnl_r"] - friction_r   # apply friction

    wins       = (pnl > 0).sum()
    losses     = (pnl < 0).sum()
    total      = len(sfps)
    timed      = (sfps["outcome"].isin(["time", "time_close"])).sum()

    win_rate   = wins / total if total > 0 else 0
    avg_pnl    = pnl.mean()
    total_r    = pnl.sum()
    std_pnl    = pnl.std()
    sharpe     = (avg_pnl / std_pnl * np.sqrt(252)) if std_pnl > 0 else 0

    gross_win  = pnl[pnl > 0].sum()
    gross_loss = abs(pnl[pnl < 0].sum())
    pf         = gross_win / gross_loss if gross_loss > 0 else float("inf")

    return {
        "label":          label,
        "total":          total,
        "wins":           int(wins),
        "losses":         int(losses),
        "timed_out":      int(timed),
        "win_rate":       round(win_rate, 4),
        "avg_pnl_r":      round(avg_pnl, 4),
        "total_r":        round(total_r, 2),
        "total_r_net":    round(total_r, 2),   # same here; gross shown in caller
        "profit_factor":  round(pf, 3),
        "sharpe_r":       round(sharpe, 3),
        "avg_bars_held":  round(sfps["bars_held"].mean(), 1),
        "friction_r":     friction_r,
    }
