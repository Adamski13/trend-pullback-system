"""
Performance metrics calculation.
All metrics defined in the spec are computed here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


TRADING_DAYS = 252


def _max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """Returns (max_dd_dollars, max_dd_pct)."""
    roll_max = equity.cummax()
    dd = equity - roll_max
    dd_pct = dd / roll_max
    return float(dd.min()), float(dd_pct.min())


def _cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def _sharpe(daily_returns: pd.Series, rf: float = 0.0) -> float:
    excess = daily_returns - rf / TRADING_DAYS
    std = excess.std()
    if std == 0:
        return 0.0
    return float(excess.mean() / std * np.sqrt(TRADING_DAYS))


def _sortino(daily_returns: pd.Series, rf: float = 0.0) -> float:
    excess = daily_returns - rf / TRADING_DAYS
    downside = excess[excess < 0]
    dd_std = downside.std()
    if dd_std == 0:
        return 0.0
    return float(excess.mean() / dd_std * np.sqrt(TRADING_DAYS))


def _profit_factor(trade_log: pd.DataFrame) -> float:
    wins = trade_log[trade_log["pnl"] > 0]["pnl"].sum()
    losses = abs(trade_log[trade_log["pnl"] <= 0]["pnl"].sum())
    return float(wins / losses) if losses > 0 else float("inf")


def _consecutive(wins: pd.Series) -> tuple[int, int]:
    """Returns (max_consecutive_wins, max_consecutive_losses)."""
    max_w = max_l = cur_w = cur_l = 0
    for w in wins:
        if w:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l


def calc_metrics(
    equity_curve: pd.Series,
    daily_returns: pd.Series,
    trade_log: pd.DataFrame,
    initial_capital: float,
    bah_equity: Optional[pd.Series] = None,
) -> dict:
    """
    Compute all performance metrics defined in the spec.
    Returns a flat dict of metric_name → value.
    """
    m: dict = {}

    # Basic
    m["initial_capital"] = initial_capital
    m["final_equity"] = float(equity_curve.iloc[-1])
    m["net_pnl_dollars"] = m["final_equity"] - initial_capital
    m["net_pnl_pct"] = m["net_pnl_dollars"] / initial_capital * 100

    # CAGR
    m["cagr_pct"] = _cagr(equity_curve) * 100

    # Drawdown
    dd_dollars, dd_pct = _max_drawdown(equity_curve)
    m["max_drawdown_dollars"] = dd_dollars
    m["max_drawdown_pct"] = dd_pct * 100
    m["return_to_max_dd"] = (
        m["net_pnl_pct"] / abs(m["max_drawdown_pct"])
        if m["max_drawdown_pct"] != 0
        else float("inf")
    )

    # Risk-adjusted
    m["sharpe"] = _sharpe(daily_returns)
    m["sortino"] = _sortino(daily_returns)

    # Trade stats
    n_trades = len(trade_log)
    m["total_trades"] = n_trades

    if n_trades > 0:
        wins = trade_log[trade_log["pnl"] > 0]
        losses = trade_log[trade_log["pnl"] <= 0]

        m["win_rate_pct"] = len(wins) / n_trades * 100
        m["profit_factor"] = _profit_factor(trade_log)
        m["avg_win_dollars"] = float(wins["pnl"].mean()) if len(wins) > 0 else 0.0
        m["avg_loss_dollars"] = float(losses["pnl"].mean()) if len(losses) > 0 else 0.0
        m["avg_trade_dollars"] = float(trade_log["pnl"].mean())
        m["largest_win"] = float(trade_log["pnl"].max())
        m["largest_loss"] = float(trade_log["pnl"].min())

        is_win = trade_log["pnl"] > 0
        m["max_consecutive_wins"], m["max_consecutive_losses"] = _consecutive(is_win)

        if "entry_date" in trade_log.columns and "exit_date" in trade_log.columns:
            holding = (
                pd.to_datetime(trade_log["exit_date"]) - pd.to_datetime(trade_log["entry_date"])
            ).dt.days
            m["avg_holding_days"] = float(holding.mean())
        else:
            m["avg_holding_days"] = 0.0

        years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
        m["trades_per_year"] = n_trades / years if years > 0 else 0.0

        # Per-layer breakdown
        if "layer" in trade_log.columns:
            for lnum in [1, 2, 3]:
                lt = trade_log[trade_log["layer"] == lnum]
                m[f"layer{lnum}_trades"] = len(lt)
    else:
        for k in ["win_rate_pct", "profit_factor", "avg_win_dollars", "avg_loss_dollars",
                  "avg_trade_dollars", "largest_win", "largest_loss",
                  "max_consecutive_wins", "max_consecutive_losses",
                  "avg_holding_days", "trades_per_year"]:
            m[k] = 0.0

    # Time in market
    n_bars = len(daily_returns)
    # Approximate: any day the system had non-zero position open
    # We flag days where equity changed by more than rounding noise
    # (Simpler: use trade log date ranges)
    if n_trades > 0 and "entry_date" in trade_log.columns:
        in_market_days: set = set()
        for _, row in trade_log.iterrows():
            ed = pd.to_datetime(row["entry_date"])
            xd = pd.to_datetime(row["exit_date"])
            for d in pd.date_range(ed, xd, freq="B"):
                in_market_days.add(d.date())
        all_days = set(equity_curve.index.date)
        pct = len(in_market_days & all_days) / len(all_days) * 100 if all_days else 0.0
        m["time_in_market_pct"] = pct
    else:
        m["time_in_market_pct"] = 0.0

    # Buy & hold comparison
    if bah_equity is not None:
        bah_start = bah_equity.iloc[0]
        bah_end = bah_equity.iloc[-1]
        m["bah_return_pct"] = (bah_end - bah_start) / bah_start * 100
        _, bah_dd_pct = _max_drawdown(bah_equity)
        m["bah_max_drawdown_pct"] = bah_dd_pct * 100
    else:
        m["bah_return_pct"] = None
        m["bah_max_drawdown_pct"] = None

    return m


def monthly_returns_table(equity_curve: pd.Series) -> pd.DataFrame:
    """
    Returns a (year x month) DataFrame of monthly returns in percent.
    """
    monthly = equity_curve.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna() * 100
    df = monthly_ret.to_frame("ret")
    df["year"] = df.index.year
    df["month"] = df.index.month
    pivot = df.pivot(index="year", columns="month", values="ret")
    pivot.columns = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ][:len(pivot.columns)]
    return pivot
