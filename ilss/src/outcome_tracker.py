"""
ILSS Outcome Tracker

For each detected SFP, scans forward bars to determine trade outcome:
  WIN  — price reached entry + R before hitting stop
  LOSS — price hit stop before reaching entry + R
  TIME — neither hit within max_bars (treated as scratch / timed out)

Returns enriched SFP DataFrame with outcome columns appended.
Used in Phase 1 (raw win rate) and all subsequent phases.
"""

import numpy as np
import pandas as pd


def simulate_outcomes(
    sfps: pd.DataFrame,
    price_df: pd.DataFrame,
    reward_r: float = 1.0,
    max_bars: int = 96,        # 96 × 15min = 24 hours
) -> pd.DataFrame:
    """
    Simulate fixed-R outcomes for each SFP.

    For each SFP row:
      - Entry = sfp.entry_price
      - Stop  = sfp.stop_price
      - Target = entry + reward_r × stop_distance  (bull)
               = entry - reward_r × stop_distance  (bear)
      - Scan next max_bars candles for whichever is hit first

    Args:
        sfps:      DataFrame of detected SFPs (from detect_sfps)
        price_df:  Full OHLCV DataFrame (same instrument, same timeframe)
        reward_r:  Target as multiple of 1R (default 1.0 = 1:1 R:R)
        max_bars:  Max bars to hold before time-stop

    Returns:
        sfps DataFrame with additional columns:
          outcome        — "win", "loss", "time"
          bars_held      — bars until resolution
          exit_price     — price at resolution
          pnl_r          — P&L in R units (+reward_r, -1, 0)
    """
    if sfps.empty:
        return sfps

    sfps = sfps.copy()
    outcomes, bars_held, exit_prices, pnl_r_list = [], [], [], []

    price_index = price_df.index
    index_map   = {t: i for i, t in enumerate(price_index)}

    for sfp_time, row in sfps.iterrows():
        entry     = row["entry_price"]
        stop      = row["stop_price"]
        stop_dist = row["stop_distance"]
        direction = row["direction"]

        target = (entry + reward_r * stop_dist if direction == "bull"
                  else entry - reward_r * stop_dist)

        start_idx = index_map.get(sfp_time)
        if start_idx is None:
            outcomes.append("time")
            bars_held.append(0)
            exit_prices.append(entry)
            pnl_r_list.append(0.0)
            continue

        outcome    = "time"
        n_bars     = 0
        exit_price = entry

        end_idx = min(start_idx + 1 + max_bars, len(price_df))

        for j in range(start_idx + 1, end_idx):
            bar  = price_df.iloc[j]
            n_bars += 1

            if direction == "bull":
                # Check stop first (conservative — assume stop can be hit intrabar)
                if bar["Low"] <= stop:
                    outcome    = "loss"
                    exit_price = stop
                    break
                if bar["High"] >= target:
                    outcome    = "win"
                    exit_price = target
                    break
            else:
                if bar["High"] >= stop:
                    outcome    = "loss"
                    exit_price = stop
                    break
                if bar["Low"] <= target:
                    outcome    = "win"
                    exit_price = target
                    break

        outcomes.append(outcome)
        bars_held.append(n_bars)
        exit_prices.append(exit_price)

        if outcome == "win":
            pnl_r_list.append(reward_r)
        elif outcome == "loss":
            pnl_r_list.append(-1.0)
        else:
            pnl_r_list.append(0.0)

    sfps["outcome"]    = outcomes
    sfps["bars_held"]  = bars_held
    sfps["exit_price"] = exit_prices
    sfps["pnl_r"]      = pnl_r_list
    return sfps


def outcome_stats(sfps: pd.DataFrame, label: str = "") -> dict:
    """
    Compute win rate, expectancy, Sharpe from outcome-enriched SFP DataFrame.
    """
    if sfps.empty or "outcome" not in sfps.columns:
        return {}

    traded = sfps[sfps["outcome"] != "time"]
    if traded.empty:
        return {}

    wins   = (traded["outcome"] == "win").sum()
    losses = (traded["outcome"] == "loss").sum()
    total  = len(traded)
    timed  = (sfps["outcome"] == "time").sum()

    win_rate   = wins / total if total > 0 else 0
    avg_pnl_r  = traded["pnl_r"].mean()
    total_r    = traded["pnl_r"].sum()
    std_pnl_r  = traded["pnl_r"].std()
    sharpe     = (avg_pnl_r / std_pnl_r * np.sqrt(252)) if std_pnl_r > 0 else 0

    gross_wins  = traded.loc[traded["outcome"] == "win",  "pnl_r"].sum()
    gross_loss  = abs(traded.loc[traded["outcome"] == "loss", "pnl_r"].sum())
    profit_factor = gross_wins / gross_loss if gross_loss > 0 else float("inf")

    stats = {
        "label":         label,
        "total_sfps":    len(sfps),
        "traded":        total,
        "timed_out":     int(timed),
        "wins":          int(wins),
        "losses":        int(losses),
        "win_rate":      round(win_rate, 4),
        "avg_pnl_r":     round(avg_pnl_r, 4),
        "total_r":       round(total_r, 2),
        "profit_factor": round(profit_factor, 3),
        "sharpe_r":      round(sharpe, 3),
        "avg_bars_held": round(traded["bars_held"].mean(), 1),
    }
    return stats


def print_outcome_stats(stats: dict):
    if not stats:
        print("  (no outcome data)")
        return
    wr_tag = "✅" if stats["win_rate"] >= 0.50 else "❌"
    pf_tag = "✅" if stats["profit_factor"] >= 1.3 else "❌"
    print(f"    Win rate:      {stats['win_rate']*100:>5.1f}%  {wr_tag}")
    print(f"    Profit factor: {stats['profit_factor']:>5.3f}  {pf_tag}")
    print(f"    Avg P&L (R):   {stats['avg_pnl_r']:>+6.4f}")
    print(f"    Total R:       {stats['total_r']:>+7.1f}R  over {stats['traded']} trades")
    print(f"    Sharpe (R):    {stats['sharpe_r']:>6.3f}")
    print(f"    Avg hold:      {stats['avg_bars_held']:>5.1f} bars ({stats['avg_bars_held']*15/60:.1f}h)")
    print(f"    Timed out:     {stats['timed_out']:>5} ({stats['timed_out']/stats['total_sfps']*100:.1f}%)")
