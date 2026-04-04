#!/usr/bin/env python3
"""
ILSS Phase 6 — Portfolio Construction & Equity Curve Analysis

Combines the 4 core PASS instruments (GBP_USD, USD_JPY, XAU_USD, BTC_USD)
plus 2 marginal instruments (NAS100_USD, EUR_USD) at half position size.

Position sizing: 1% risk per trade, max 2 concurrent positions.
Concurrent limit approximation: if on the same calendar day more than 2 trades
signal, keep only the top 2 by priority (XAU > USD_JPY > GBP_USD > BTC_USD
> NAS100 > EUR_USD).

Usage:
    python run_phase6.py
    python run_phase6.py --symbol GBP_USD   # single instrument view
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader import load_cached
from src.session_labels import prepare
from src.sfp_detector import detect_sfps
from src.daily_bias import compute_daily_bias
from src.exit_simulator import (
    simulate_fixed_target,
    simulate_time_stop,
    exit_stats,
)

# ── Phase 3 optimal configs ─────────────────────────────────────────────────
PHASE3_CONFIG = {
    "NAS100_USD": {"sessions": ["london_open"],            "bias": True},
    "SPX500_USD": {"sessions": ["london"],                 "bias": True},
    "UK100_GBP":  {"sessions": ["ny_open"],                "bias": True},
    "EUR_USD":    {"sessions": ["asian"],                  "bias": True},
    "GBP_USD":    {"sessions": ["london_open", "ny_close"],"bias": True},
    "USD_JPY":    {"sessions": ["asian"],                  "bias": False},
    "XAU_USD":    {"sessions": ["ny_afternoon"],           "bias": True},
    "BTC_USD":    {"sessions": ["ny_close"],               "bias": False},
}

# ── Phase 4 optimal exits ───────────────────────────────────────────────────
PHASE4_EXIT = {
    "NAS100_USD": {"exit": "time_stop", "bars": 16,  "reward_r": None},
    "SPX500_USD": {"exit": "time_stop", "bars": None, "reward_r": 1.0},
    "UK100_GBP":  {"exit": "time_stop", "bars": 8,   "reward_r": None},
    "EUR_USD":    {"exit": "time_stop", "bars": 24,  "reward_r": None},
    "GBP_USD":    {"exit": "time_stop", "bars": 8,   "reward_r": None},
    "USD_JPY":    {"exit": "time_stop", "bars": 16,  "reward_r": None},
    "XAU_USD":    {"exit": "time_stop", "bars": 8,   "reward_r": None},
    "BTC_USD":    {"exit": "time_stop", "bars": 24,  "reward_r": None},
}

FRICTION_R = {
    "NAS100_USD": 0.08, "SPX500_USD": 0.08, "UK100_GBP": 0.08,
    "EUR_USD": 0.05, "GBP_USD": 0.05, "USD_JPY": 0.05,
    "XAU_USD": 0.06, "BTC_USD": 0.12,
}

# Core PASS instruments and their position size multiplier
CORE_SYMBOLS = ["GBP_USD", "USD_JPY", "XAU_USD", "BTC_USD"]
MARGINAL_SYMBOLS = ["NAS100_USD", "EUR_USD"]

# Priority order for concurrent position limit (highest priority first)
PRIORITY = {
    "XAU_USD": 1, "USD_JPY": 2, "GBP_USD": 3,
    "BTC_USD": 4, "NAS100_USD": 5, "EUR_USD": 6,
}

# Position size fraction of capital per trade
RISK_PER_TRADE = 0.01     # 1% risk per trade
HALF_SIZE_MULT = 0.5      # marginal instruments trade at half size
MAX_CONCURRENT = 2


def _load_instrument(symbol: str, sfp_cfg: dict, strat_cfg: dict) -> pd.DataFrame | None:
    """Load, filter, and simulate one instrument. Returns trade log DataFrame."""
    cfg = PHASE3_CONFIG.get(symbol)
    ex  = PHASE4_EXIT.get(symbol)
    if not cfg or not ex:
        return None

    df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    df_d   = load_cached(symbol, "D",   "2015-01-01", "2025-12-31") if cfg["bias"] else None

    if df_m15 is None:
        return None

    enriched = prepare(df_m15, atr_period=sfp_cfg["atr_period"])
    sfps = detect_sfps(
        enriched, symbol=symbol,
        min_sweep_atr=sfp_cfg["sweep_min_depth"],
        max_sweep_atr=sfp_cfg["sweep_max_depth"],
        stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
        active_sessions=None,
    )
    bull_sfps = sfps[sfps["direction"] == "bull"].copy()

    # Phase 2 bias filter
    if cfg["bias"] and df_d is not None:
        bias_df = compute_daily_bias(df_d, long_threshold=5.0)
        bull_sfps["date"] = bull_sfps.index.normalize()
        bias_daily = bias_df[["forecast", "bias", "above_sma"]].copy()
        bias_daily.index = pd.to_datetime(bias_daily.index).normalize()
        bull_sfps = bull_sfps.join(bias_daily, on="date", how="left")
        bull_sfps = bull_sfps[bull_sfps["bias"] == "bull"].copy()

    # Phase 3 session filter
    bull_sfps = bull_sfps[bull_sfps["session"].isin(cfg["sessions"])].copy()

    if len(bull_sfps) < 5:
        return None

    # Phase 4 exit simulation
    if ex["reward_r"] is not None:
        out = simulate_fixed_target(bull_sfps, enriched, reward_r=ex["reward_r"])
    else:
        out = simulate_time_stop(bull_sfps, enriched, hold_bars=ex["bars"])

    # Apply friction
    friction = FRICTION_R[symbol]
    out = out.copy()
    out["pnl_r_net"] = out["pnl_r"] - friction
    out["symbol"]    = symbol
    out["friction"]  = friction

    # Date column (signal date)
    out["date"] = out.index.normalize()

    return out[["symbol", "date", "pnl_r", "pnl_r_net", "friction", "session",
                "outcome", "bars_held"]].copy()


def _build_portfolio(trade_logs: dict[str, pd.DataFrame], focus_symbols: list[str]) -> pd.DataFrame:
    """
    Combine trade logs, apply concurrent position limit, assign position sizes.
    Returns a DataFrame with one row per trade included in portfolio.
    """
    frames = []
    for sym in focus_symbols:
        if sym not in trade_logs or trade_logs[sym] is None:
            continue
        df = trade_logs[sym].copy()
        df["priority"] = PRIORITY.get(sym, 99)
        df["size_mult"] = HALF_SIZE_MULT if sym in MARGINAL_SYMBOLS else 1.0
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["date", "priority"]).reset_index(drop=True)

    # Apply concurrent limit: on each calendar date, keep top MAX_CONCURRENT by priority
    rows_to_keep = []
    for date, group in combined.groupby("date"):
        group_sorted = group.sort_values("priority")
        rows_to_keep.append(group_sorted.head(MAX_CONCURRENT))

    portfolio = pd.concat(rows_to_keep, ignore_index=True)
    portfolio = portfolio.sort_values("date").reset_index(drop=True)

    # P&L in % of capital = pnl_r_net * risk_per_trade * size_mult
    portfolio["pnl_pct"] = portfolio["pnl_r_net"] * RISK_PER_TRADE * portfolio["size_mult"]

    return portfolio


def _equity_curve(portfolio: pd.DataFrame) -> pd.Series:
    """Build daily equity series starting at 1.0."""
    daily_pnl = portfolio.groupby("date")["pnl_pct"].sum()

    # Reindex over full date range
    date_range = pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq="B")
    daily_pnl = daily_pnl.reindex(date_range, fill_value=0.0)

    equity = (1 + daily_pnl).cumprod()
    return equity


def _max_drawdown(equity: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp]:
    """Returns (max_dd_pct, peak_date, trough_date)."""
    rolling_max = equity.cummax()
    drawdown    = (equity - rolling_max) / rolling_max
    max_dd      = drawdown.min()
    trough_date = drawdown.idxmin()
    peak_date   = equity[:trough_date].idxmax()
    return float(max_dd), peak_date, trough_date


def _sharpe(daily_returns: pd.Series, periods_per_year: int = 252) -> float:
    if daily_returns.std() == 0:
        return 0.0
    return float(daily_returns.mean() / daily_returns.std() * np.sqrt(periods_per_year))


def _calmar(equity: pd.Series, max_dd: float) -> float:
    if max_dd == 0:
        return 0.0
    years = len(equity) / 252
    if years == 0:
        return 0.0
    cagr = equity.iloc[-1] ** (1 / years) - 1
    return float(cagr / abs(max_dd))


def _monthly_win_rate(portfolio: pd.DataFrame) -> float:
    """Fraction of calendar months with positive net P&L."""
    df = portfolio.copy()
    df["month"] = df["date"].dt.to_period("M")
    monthly = df.groupby("month")["pnl_pct"].sum()
    return float((monthly > 0).mean())


def _ascii_equity(equity: pd.Series, width: int = 60) -> str:
    """Render a simple ASCII equity curve."""
    vals  = equity.values
    lo    = vals.min()
    hi    = vals.max()
    span  = hi - lo if hi != lo else 1.0
    rows  = 10
    lines = []
    cols  = min(width, len(vals))
    step  = max(1, len(vals) // cols)
    sampled = vals[::step][:cols]

    for row in range(rows, -1, -1):
        threshold = lo + span * row / rows
        line = ""
        for v in sampled:
            line += "#" if v >= threshold else " "
        label = f"{threshold:.3f}" if row % 2 == 0 else "      "
        lines.append(f"  {label} |{line}|")

    # x-axis: year labels
    start_year = equity.index[0].year
    end_year   = equity.index[-1].year
    n_years    = end_year - start_year + 1
    year_axis  = " " * 10 + "|"
    for yr in range(start_year, end_year + 1):
        pos = int((yr - start_year) / max(n_years - 1, 1) * (cols - 1))
        year_str = str(yr)
        year_axis += " " * max(0, pos - len(year_axis) + 10) + year_str
    lines.append(f"         {year_axis[:cols + 10]}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 6 — Portfolio Construction")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Analyse a single instrument only")
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg     = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]

    focus_symbols = CORE_SYMBOLS + MARGINAL_SYMBOLS
    if args.symbol:
        focus_symbols = [args.symbol] if args.symbol in focus_symbols else []

    print("=" * 72)
    print("  ILSS Phase 6 — Portfolio Construction & Equity Curve")
    print(f"  Core instruments:     {', '.join(CORE_SYMBOLS)}")
    print(f"  Marginal (0.5x size): {', '.join(MARGINAL_SYMBOLS)}")
    print(f"  Risk per trade: {RISK_PER_TRADE*100:.0f}%  |  Max concurrent: {MAX_CONCURRENT}")
    print("=" * 72)

    # ── Load all instruments ─────────────────────────────────────────────────
    trade_logs: dict[str, pd.DataFrame] = {}
    for sym in focus_symbols:
        print(f"\n  Loading {sym}...", end=" ", flush=True)
        tl = _load_instrument(sym, sfp_cfg, strat_cfg)
        trade_logs[sym] = tl
        if tl is not None:
            s = exit_stats(tl.assign(pnl_r=tl["pnl_r_net"]),
                           friction_r=0)  # friction already applied
            n = len(tl)
            pf = s.get("profit_factor", 0) if s else 0
            print(f"{n} trades  PF={pf:.3f}")
        else:
            print("no data")

    # ── Individual instrument summary ─────────────────────────────────────────
    print()
    print("  Individual instrument results (net of friction):")
    print(f"  {'Symbol':<16} {'N':>5}  {'PF':>7}  {'WR%':>6}  {'Total R':>9}  {'Role':>10}")
    print(f"  {'─'*64}")
    for sym in focus_symbols:
        tl = trade_logs.get(sym)
        if tl is None:
            print(f"  {sym:<16} {'—':>5}")
            continue
        pnl_col = tl["pnl_r_net"].values
        wins     = (pnl_col > 0).sum()
        losses   = (pnl_col < 0).sum()
        total    = len(pnl_col)
        wr       = wins / total if total > 0 else 0
        gw       = pnl_col[pnl_col > 0].sum()
        gl       = abs(pnl_col[pnl_col < 0].sum())
        pf       = gw / gl if gl > 0 else float("inf")
        tr       = pnl_col.sum()
        role     = "core" if sym in CORE_SYMBOLS else "marginal(0.5x)"
        print(f"  {sym:<16} {total:>5}  {pf:>7.3f}  {wr*100:>5.1f}%  {tr:>+9.1f}R  {role:>14}")

    # ── Build portfolio ──────────────────────────────────────────────────────
    print()
    print("  Building portfolio...")
    portfolio = _build_portfolio(trade_logs, focus_symbols)

    if portfolio.empty:
        print("  No trades in portfolio — exiting.")
        return

    print(f"  Total portfolio trades: {len(portfolio)}")
    print(f"  Trades per year (approx): {len(portfolio) / 6:.0f}")

    equity = _equity_curve(portfolio)
    daily_returns = equity.pct_change().dropna()

    max_dd, peak_date, trough_date = _max_drawdown(equity)
    sharpe  = _sharpe(daily_returns)
    calmar  = _calmar(equity, max_dd)
    mwr     = _monthly_win_rate(portfolio)
    total_r = portfolio["pnl_r_net"].sum()

    years   = len(equity) / 252
    cagr    = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else 0

    # ── Print equity curve ────────────────────────────────────────────────────
    print()
    print("  Portfolio Equity Curve (1.0 = starting capital):")
    print()
    print(_ascii_equity(equity))
    print()

    # ── Metrics ───────────────────────────────────────────────────────────────
    print()
    print("  Portfolio Metrics:")
    print(f"  {'─'*48}")
    print(f"  Starting equity:      1.0000")
    print(f"  Final equity:         {equity.iloc[-1]:.4f}")
    print(f"  CAGR:                 {cagr*100:+.2f}%")
    print(f"  Sharpe (annualised):  {sharpe:.3f}")
    print(f"  Max drawdown:         {max_dd*100:.2f}%")
    print(f"    Peak:  {peak_date.date()}")
    print(f"    Trough:{trough_date.date()}")
    print(f"  Calmar ratio:         {calmar:.3f}")
    print(f"  Monthly win rate:     {mwr*100:.1f}%")
    print(f"  Total R (portfolio):  {total_r:+.1f}R")

    # Drawdown periods (> 5% peak-to-trough)
    rolling_max = equity.cummax()
    in_dd = ((equity - rolling_max) / rolling_max) < -0.05
    dd_periods = []
    start = None
    for date, flag in in_dd.items():
        if flag and start is None:
            start = date
        elif not flag and start is not None:
            dd_periods.append((start, date))
            start = None
    if start is not None:
        dd_periods.append((start, equity.index[-1]))

    if dd_periods:
        print()
        print(f"  Significant drawdown periods (>5%):")
        for s, e in dd_periods[:8]:
            depth = ((equity[s:e] - equity[s:e].cummax()) / equity[s:e].cummax()).min()
            print(f"    {s.date()} → {e.date()}  ({depth*100:.1f}%)")

    # ── Yearly breakdown ──────────────────────────────────────────────────────
    print()
    print(f"  {'Year':<8} {'Trades':>7} {'Net R':>9} {'Return%':>9} {'WR%':>6}")
    print(f"  {'─'*48}")
    portfolio["year"] = pd.to_datetime(portfolio["date"]).dt.year
    for yr, grp in portfolio.groupby("year"):
        n   = len(grp)
        r   = grp["pnl_r_net"].sum()
        ret = grp["pnl_pct"].sum() * 100
        wr  = (grp["pnl_r_net"] > 0).mean() * 100
        print(f"  {yr:<8} {n:>7}  {r:>+9.1f}R  {ret:>+8.1f}%  {wr:>5.1f}%")

    # ── Save ─────────────────────────────────────────────────────────────────
    out_data = {
        "symbols": focus_symbols,
        "core": CORE_SYMBOLS,
        "marginal": MARGINAL_SYMBOLS,
        "risk_per_trade": RISK_PER_TRADE,
        "max_concurrent": MAX_CONCURRENT,
        "total_trades": int(len(portfolio)),
        "metrics": {
            "final_equity": round(float(equity.iloc[-1]), 4),
            "cagr_pct":     round(cagr * 100, 2),
            "sharpe":       round(sharpe, 3),
            "max_dd_pct":   round(max_dd * 100, 2),
            "calmar":       round(calmar, 3),
            "monthly_wr":   round(mwr * 100, 1),
            "total_r":      round(total_r, 1),
        },
        "equity_curve": {str(k.date()): round(v, 6) for k, v in equity.items()},
        "per_symbol": {},
    }
    for sym in focus_symbols:
        tl = trade_logs.get(sym)
        if tl is None:
            continue
        pnl = tl["pnl_r_net"].values
        wins = (pnl > 0).sum()
        gw   = pnl[pnl > 0].sum()
        gl   = abs(pnl[pnl < 0].sum())
        out_data["per_symbol"][sym] = {
            "n_trades": int(len(tl)),
            "total_r":  round(float(pnl.sum()), 2),
            "pf":       round(gw / gl if gl > 0 else 0, 3),
            "wr_pct":   round(wins / len(pnl) * 100, 1),
            "role":     "core" if sym in CORE_SYMBOLS else "marginal",
        }

    out_path = Path(__file__).parent / "results" / "phase6_portfolio.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out_data, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
