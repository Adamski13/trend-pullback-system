#!/usr/bin/env python3
"""
ILSS Enhancement E6 — Previous Week High/Low levels

Tests whether adding PWH/PWL as additional sweep targets improves
the system vs the current 4-level set (PDH/PDL + Asian H/L).

Three views per instrument:
  1. Baseline — PDH/PDL + Asian H/L only (current validated system)
  2. Weekly-only — PWH/PWL sweeps only (isolated signal quality)
  3. All levels — PDH/PDL + Asian H/L + PWH/PWL combined

IS: 2020–2024  |  OOS: 2025
Sweep depth: 0.4–0.8× ATR (E2 locked)
Bull bias gate applied (same as live system)

Decision rule:
  ADOPT   — combined PF ≥ baseline on IS AND OOS on ≥ 3/4 core
  MONITOR — combined IS ≥ baseline but OOS mixed
  REJECT  — weekly levels dilute the existing edge

Usage:
    python run_e6_weekly_levels.py
    python run_e6_weekly_levels.py --symbol XAU_USD
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader    import load_cached
from src.session_labels import prepare
from src.sfp_detector   import detect_sfps
from src.daily_bias     import compute_daily_bias
from src.exit_simulator import simulate_time_stop, exit_stats

# ── Locked params ─────────────────────────────────────────────────────────────
PHASE3_SESSIONS = {
    "NAS100_USD": ["london_open"],
    "EUR_USD":    ["asian"],
    "GBP_USD":    ["london_open", "ny_close"],
    "USD_JPY":    ["asian"],
    "XAU_USD":    ["ny_afternoon"],
    "BTC_USD":    ["ny_close"],
}
PHASE4_BARS = {
    "NAS100_USD": 16, "EUR_USD": 24, "GBP_USD": 8,
    "USD_JPY":    16, "XAU_USD": 8,  "BTC_USD": 24,
}
BULL_BIAS = {
    "NAS100_USD": True, "EUR_USD": True,  "GBP_USD": True,
    "USD_JPY":    True, "XAU_USD": True,  "BTC_USD": False,
}
FRICTION_R = {
    "NAS100_USD": 0.08, "EUR_USD": 0.05, "GBP_USD": 0.05,
    "USD_JPY":    0.05, "XAU_USD": 0.06, "BTC_USD": 0.12,
}
CORE      = ["GBP_USD", "USD_JPY", "XAU_USD", "BTC_USD"]
SWEEP_MIN = 0.4
SWEEP_MAX = 0.8

IS_START  = "2020-01-01"
IS_END    = "2024-12-31"
OOS_START = "2025-01-01"
OOS_END   = "2025-12-31"

DAILY_LEVELS   = {"prev_day_low", "prev_day_high", "asian_low", "asian_high"}
WEEKLY_LEVELS  = {"prev_week_low", "prev_week_high"}


# ── Helpers ───────────────────────────────────────────────────────────────────

_strat_cfg_cache = None
def _strat_cfg():
    global _strat_cfg_cache
    if _strat_cfg_cache is None:
        with open(Path(__file__).parent / "config" / "strategy.yaml") as f:
            _strat_cfg_cache = yaml.safe_load(f)
    return _strat_cfg_cache


def _load_bias(symbol, cutoff):
    df_d = load_cached(symbol, "D", "2015-01-01", "2025-12-31")
    if df_d is None:
        return None
    return compute_daily_bias(
        df_d[df_d.index <= pd.Timestamp(cutoff)].copy(),
        long_threshold=5.0,
    )


def _apply_bias(sfps, bias_df):
    sfps = sfps.copy()
    sfps["date"] = sfps.index.normalize()
    bd = bias_df[["bias"]].copy()
    bd.index = pd.to_datetime(bd.index).normalize()
    sfps = sfps.join(bd, on="date", how="left")
    return sfps[sfps["bias"] == "bull"].copy()


def _get_sfps(enriched, symbol, date_start, date_end,
              bias_df=None, level_filter: set | None = None):
    """
    Detect bull SFPs, apply date/session/bias filters.
    level_filter: if set, keep only SFPs whose level_type is in this set.
    """
    strat = _strat_cfg()
    all_sfps = detect_sfps(
        enriched, symbol=symbol,
        min_sweep_atr=SWEEP_MIN,
        max_sweep_atr=SWEEP_MAX,
        stop_buffer_atr=strat["exit"]["stop_buffer_atr"],
        active_sessions=None,
    )
    sfps = all_sfps[all_sfps["direction"] == "bull"].copy()

    # Date filter
    sfps = sfps[(sfps.index >= pd.Timestamp(date_start)) &
                (sfps.index <= pd.Timestamp(date_end))].copy()

    # Session filter
    sessions = PHASE3_SESSIONS.get(symbol, [])
    sfps = sfps[sfps["session"].isin(sessions)].copy()

    # Bias filter
    if BULL_BIAS.get(symbol, False) and bias_df is not None:
        sfps = _apply_bias(sfps, bias_df)

    # Level type filter
    if level_filter is not None:
        sfps = sfps[sfps["level_type"].isin(level_filter)].copy()

    return sfps


def _row(label, s, base_pf, friction):
    if not s or s.get("total", 0) == 0:
        print(f"    {label:<44} {'—':>5}")
        return
    pf   = s["profit_factor"]
    wr   = s["win_rate"]
    n    = s["total"]
    r    = s["total_r"]
    dpf  = pf - base_pf
    sign = "+" if dpf >= 0 else ""
    tag  = "✅" if dpf > 0.02 else ("❌" if dpf < -0.02 else "—")
    print(f"    {label:<44} {n:>5}  {wr*100:>5.1f}%  {pf:>6.3f} {tag}  "
          f"{r:>+7.1f}R  {sign}{dpf:>+.3f}")


# ── Per-instrument test ───────────────────────────────────────────────────────

def test_instrument(symbol):
    bars     = PHASE4_BARS[symbol]
    friction = FRICTION_R[symbol]

    print(f"\n  {'═'*74}")
    print(f"  {symbol}  |  sessions: {PHASE3_SESSIONS[symbol]}  "
          f"|  bars: {bars}  |  friction: {friction}R")
    print(f"  {'═'*74}")

    df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    if df_m15 is None:
        print("  No data — skipping")
        return {}
    enriched = prepare(df_m15, atr_period=14)

    bias_is  = _load_bias(symbol, IS_END)
    bias_oos = _load_bias(symbol, OOS_END)

    def sim(sfps):
        if sfps is None or sfps.empty:
            return {}
        out = simulate_time_stop(sfps, enriched, hold_bars=bars)
        return exit_stats(out, friction_r=friction)

    # ── IS ────────────────────────────────────────────────────────────────────
    base_is    = _get_sfps(enriched, symbol, IS_START, IS_END, bias_is,
                            level_filter=DAILY_LEVELS)
    weekly_is  = _get_sfps(enriched, symbol, IS_START, IS_END, bias_is,
                            level_filter=WEEKLY_LEVELS)
    all_is     = _get_sfps(enriched, symbol, IS_START, IS_END, bias_is,
                            level_filter=None)   # all level types

    base_is_s   = sim(base_is)
    weekly_is_s = sim(weekly_is)
    all_is_s    = sim(all_is)
    base_is_pf  = base_is_s.get("profit_factor", 0)

    # ── OOS ───────────────────────────────────────────────────────────────────
    base_oos   = _get_sfps(enriched, symbol, OOS_START, OOS_END, bias_oos,
                            level_filter=DAILY_LEVELS)
    weekly_oos = _get_sfps(enriched, symbol, OOS_START, OOS_END, bias_oos,
                            level_filter=WEEKLY_LEVELS)
    all_oos    = _get_sfps(enriched, symbol, OOS_START, OOS_END, bias_oos,
                            level_filter=None)

    base_oos_s   = sim(base_oos)
    weekly_oos_s = sim(weekly_oos)
    all_oos_s    = sim(all_oos)
    base_oos_pf  = base_oos_s.get("profit_factor", 0)

    # ── Level breakdown ───────────────────────────────────────────────────────
    if not all_is.empty:
        breakdown = all_is.groupby("level_type").size().to_dict()
        print(f"  IS level breakdown: {breakdown}")

    hdr = (f"    {'Label':<44} {'n':>5}  {'WR%':>5}  {'PF':>6}"
           f"     {'TotalR':>7}  {'ΔPF':>6}")
    sep = f"    {'─'*74}"

    print(f"\n  IS  (2020–2024)")
    print(hdr); print(sep)
    _row("baseline  (PDH/PDL + Asian H/L only)",  base_is_s,   base_is_pf,  friction)
    _row("weekly-only  (PWH/PWL only)",            weekly_is_s, base_is_pf,  friction)
    _row("all levels  (daily + Asian + weekly)",   all_is_s,    base_is_pf,  friction)

    print(f"\n  OOS (2025)")
    print(hdr); print(sep)
    _row("baseline  (PDH/PDL + Asian H/L only)",  base_oos_s,   base_oos_pf, friction)
    _row("weekly-only  (PWH/PWL only)",            weekly_oos_s, base_oos_pf, friction)
    _row("all levels  (daily + Asian + weekly)",   all_oos_s,    base_oos_pf, friction)

    return {
        "base_is":    base_is_s,   "weekly_is":  weekly_is_s, "all_is":    all_is_s,
        "base_oos":   base_oos_s,  "weekly_oos": weekly_oos_s, "all_oos":  all_oos_s,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ILSS E6 — Weekly levels test")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    focus = [args.symbol] if args.symbol else (CORE + ["NAS100_USD", "EUR_USD"])
    focus = [s for s in focus if s in PHASE3_SESSIONS]

    print("=" * 76)
    print("  ILSS E6 — Previous Week High/Low levels")
    print(f"  Sweep depth: {SWEEP_MIN}–{SWEEP_MAX}× ATR (E2 locked)  |  Bull bias gate: EWMAC ≥5")
    print(f"  IS: {IS_START} → {IS_END}  |  OOS: {OOS_START} → {OOS_END}")
    print(f"  Instruments: {', '.join(focus)}")
    print("=" * 76)

    all_results = {}
    for sym in focus:
        all_results[sym] = test_instrument(sym)

    # ── Decision summary ──────────────────────────────────────────────────────
    print()
    print("=" * 76)
    print("  DECISION SUMMARY")
    print()
    print(f"  {'Instrument':<14} {'Wkly IS PF':>10} {'Wkly OOS PF':>11} "
          f"{'All IS PF':>9} {'All OOS PF':>10}  Decision")
    print(f"  {'─'*74}")

    all_is_pass  = 0
    all_oos_pass = 0
    wkly_edge    = 0

    for sym in CORE:
        r = all_results.get(sym, {})
        w_is   = r.get("weekly_is",  {}).get("profit_factor", 0)
        w_oos  = r.get("weekly_oos", {}).get("profit_factor", 0)
        a_is   = r.get("all_is",     {}).get("profit_factor", 0)
        a_oos  = r.get("all_oos",    {}).get("profit_factor", 0)
        b_is   = r.get("base_is",    {}).get("profit_factor", 0)

        all_ok_is  = a_is  >= b_is - 0.02   # combined doesn't hurt baseline
        all_ok_oos = a_oos >= r.get("base_oos", {}).get("profit_factor", 0) - 0.05
        wkly_ok    = w_is  >= 1.0

        if all_ok_is:  all_is_pass  += 1
        if all_ok_oos: all_oos_pass += 1
        if wkly_ok:    wkly_edge    += 1

        tag = "✅" if (all_ok_is and all_ok_oos and wkly_ok) \
              else "⚠️" if (all_ok_is and all_ok_oos) \
              else "❌"

        w_is_str  = f"{w_is:.3f}" if w_is else "—"
        w_oos_str = f"{w_oos:.3f}" if w_oos else "—"
        print(f"  {sym:<14} {w_is_str:>10} {w_oos_str:>11} "
              f"{a_is:>9.3f} {a_oos:>10.3f}  {tag}")

    print()
    decision = (
        "ADOPT  ✅" if (all_is_pass >= 3 and all_oos_pass >= 3 and wkly_edge >= 2)
        else "MONITOR ⚠️" if (all_is_pass >= 3 and all_oos_pass >= 2)
        else "REJECT ❌"
    )
    print(f"  Weekly levels show IS edge (PF≥1.0): {wkly_edge}/4 core")
    print(f"  Combined doesn't hurt IS:  {all_is_pass}/4 core")
    print(f"  Combined doesn't hurt OOS: {all_oos_pass}/4 core")
    print(f"\n  → {decision}")
    print()

    out_path = Path(__file__).parent / "results" / "e6_weekly_levels.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  Results saved → {out_path}")
    print("=" * 76)


if __name__ == "__main__":
    main()
