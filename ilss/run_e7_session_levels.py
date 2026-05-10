#!/usr/bin/env python3
"""
ILSS Enhancement E7 — Per-instrument prior-session liquidity

For instruments that trade late in the UTC day, the Asian session H/L is
stale by the time their session opens.  This tests whether substituting or
adding the most recently completed session's range improves edge.

Mapping (instrument → trade session → prior session tested):
  XAU_USD   ny_afternoon (15:00–19:00 UTC) → London  (09:00–12:00 UTC)
  BTC_USD   ny_close     (19:00–21:00 UTC) → NY afternoon (15:00–19:00 UTC)

Three views per focus instrument:
  1. Baseline   — PDH/PDL + Asian H/L   (current validated system)
  2. Prior-sess — PDH/PDL + prior session H/L only (replaces Asian)
  3. Combined   — PDH/PDL + Asian H/L + prior session H/L (adds to baseline)

Other core instruments (GBP_USD, USD_JPY) shown for context — baseline only,
since their trade sessions are already close to the Asian session.

IS: 2020–2024  |  OOS: 2025
Sweep depth: 0.4–0.8× ATR (E2 locked)
Bull bias gate applied (same as live system)

Decision rule (per focus instrument):
  ADOPT    — prior-sess IS PF ≥ baseline IS PF  AND  OOS PF ≥ baseline OOS PF
  CONSIDER — combined IS PF ≥ baseline IS PF  AND  combined OOS PF ≥ baseline OOS PF
  REJECT   — prior-sess and combined both dilute edge

Usage:
    python run_e7_session_levels.py
    python run_e7_session_levels.py --symbol XAU_USD
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader    import load_cached
from src.session_labels import prepare, compute_intraday_session_levels
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
CORE = ["GBP_USD", "USD_JPY", "XAU_USD", "BTC_USD"]

SWEEP_MIN = 0.4
SWEEP_MAX = 0.8

IS_START  = "2020-01-01"
IS_END    = "2024-12-31"
OOS_START = "2025-01-01"
OOS_END   = "2025-12-31"

# The prior session to test for each focus instrument
PRIOR_SESSION = {
    "XAU_USD": "london",        # London (09:00–12:00) → available from 12:00 onward
    "BTC_USD": "ny_afternoon",  # NY afternoon (15:00–19:00) → available from 19:00 onward
}

# Bull levels for each view
DAILY_LEVELS   = {"prev_day_low", "prev_day_high", "asian_low", "asian_high"}


def _prior_level_set(symbol):
    """Level filter for prior-session-only view (PDH/PDL + prior session H/L)."""
    sess = PRIOR_SESSION.get(symbol)
    if sess is None:
        return None
    return {"prev_day_low", "prev_day_high", f"{sess}_low", f"{sess}_high"}


def _combined_level_set(symbol):
    """Level filter for combined view (PDH/PDL + Asian + prior session H/L)."""
    sess = PRIOR_SESSION.get(symbol)
    if sess is None:
        return None
    return DAILY_LEVELS | {f"{sess}_low", f"{sess}_high"}


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
              bias_df=None, level_filter=None):
    strat = _strat_cfg()
    all_sfps = detect_sfps(
        enriched, symbol=symbol,
        min_sweep_atr=SWEEP_MIN,
        max_sweep_atr=SWEEP_MAX,
        stop_buffer_atr=strat["exit"]["stop_buffer_atr"],
        active_sessions=None,
    )
    sfps = all_sfps[all_sfps["direction"] == "bull"].copy()

    sfps = sfps[(sfps.index >= pd.Timestamp(date_start)) &
                (sfps.index <= pd.Timestamp(date_end))].copy()

    sessions = PHASE3_SESSIONS.get(symbol, [])
    sfps = sfps[sfps["session"].isin(sessions)].copy()

    if BULL_BIAS.get(symbol, False) and bias_df is not None:
        sfps = _apply_bias(sfps, bias_df)

    if level_filter is not None:
        sfps = sfps[sfps["level_type"].isin(level_filter)].copy()

    return sfps


def _row(label, s, base_pf, friction):
    if not s or s.get("total", 0) == 0:
        print(f"    {label:<50} {'—':>5}")
        return
    pf   = s["profit_factor"]
    wr   = s["win_rate"]
    n    = s["total"]
    r    = s["total_r"]
    dpf  = pf - base_pf
    sign = "+" if dpf >= 0 else ""
    tag  = "✅" if dpf > 0.02 else ("❌" if dpf < -0.02 else "—")
    print(f"    {label:<50} {n:>5}  {wr*100:>5.1f}%  {pf:>6.3f} {tag}  "
          f"{r:>+7.1f}R  {sign}{dpf:>+.3f}")


# ── Per-instrument test ───────────────────────────────────────────────────────

def test_instrument(symbol):
    bars     = PHASE4_BARS[symbol]
    friction = FRICTION_R[symbol]
    prior    = PRIOR_SESSION.get(symbol)

    print(f"\n  {'═'*76}")
    print(f"  {symbol}  |  sessions: {PHASE3_SESSIONS[symbol]}  "
          f"|  bars: {bars}  |  friction: {friction}R")
    if prior:
        print(f"  Prior session tested: {prior}")
    print(f"  {'═'*76}")

    df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    if df_m15 is None:
        print("  No data — skipping")
        return {}
    enriched = prepare(df_m15, atr_period=14)

    # Add intraday session levels for this instrument's prior session
    if prior:
        enriched = compute_intraday_session_levels(enriched, prior)

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
    base_is_s  = sim(base_is)
    base_is_pf = base_is_s.get("profit_factor", 0)

    if prior:
        prior_is   = _get_sfps(enriched, symbol, IS_START, IS_END, bias_is,
                                level_filter=_prior_level_set(symbol))
        comb_is    = _get_sfps(enriched, symbol, IS_START, IS_END, bias_is,
                                level_filter=_combined_level_set(symbol))
        prior_is_s = sim(prior_is)
        comb_is_s  = sim(comb_is)

        if not comb_is.empty:
            breakdown = comb_is.groupby("level_type").size().to_dict()
            print(f"  IS level breakdown: {breakdown}")
    else:
        prior_is_s = {}
        comb_is_s  = {}

    # ── OOS ───────────────────────────────────────────────────────────────────
    base_oos    = _get_sfps(enriched, symbol, OOS_START, OOS_END, bias_oos,
                             level_filter=DAILY_LEVELS)
    base_oos_s  = sim(base_oos)
    base_oos_pf = base_oos_s.get("profit_factor", 0)

    if prior:
        prior_oos   = _get_sfps(enriched, symbol, OOS_START, OOS_END, bias_oos,
                                 level_filter=_prior_level_set(symbol))
        comb_oos    = _get_sfps(enriched, symbol, OOS_START, OOS_END, bias_oos,
                                 level_filter=_combined_level_set(symbol))
        prior_oos_s = sim(prior_oos)
        comb_oos_s  = sim(comb_oos)
    else:
        prior_oos_s = {}
        comb_oos_s  = {}

    hdr = (f"    {'Label':<50} {'n':>5}  {'WR%':>5}  {'PF':>6}"
           f"     {'TotalR':>7}  {'ΔPF':>6}")
    sep = f"    {'─'*76}"

    print(f"\n  IS  (2020–2024)")
    print(hdr); print(sep)
    _row("baseline  (PDH/PDL + Asian H/L)",       base_is_s,  base_is_pf,  friction)
    if prior:
        _row(f"prior-sess (PDH/PDL + {prior} H/L)",  prior_is_s, base_is_pf,  friction)
        _row("combined  (baseline + prior session)",  comb_is_s,  base_is_pf,  friction)

    print(f"\n  OOS (2025)")
    print(hdr); print(sep)
    _row("baseline  (PDH/PDL + Asian H/L)",       base_oos_s, base_oos_pf, friction)
    if prior:
        _row(f"prior-sess (PDH/PDL + {prior} H/L)",  prior_oos_s, base_oos_pf, friction)
        _row("combined  (baseline + prior session)",  comb_oos_s,  base_oos_pf, friction)

    return {
        "base_is":   base_is_s,  "prior_is":  prior_is_s,  "comb_is":  comb_is_s,
        "base_oos":  base_oos_s, "prior_oos": prior_oos_s, "comb_oos": comb_oos_s,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ILSS E7 — Prior-session liquidity levels")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    focus = [args.symbol] if args.symbol else (CORE + ["NAS100_USD", "EUR_USD"])
    focus = [s for s in focus if s in PHASE3_SESSIONS]

    print("=" * 78)
    print("  ILSS E7 — Per-instrument prior-session liquidity")
    print(f"  Sweep depth: {SWEEP_MIN}–{SWEEP_MAX}× ATR (E2 locked)  |  Bull bias gate: EWMAC ≥5")
    print(f"  IS: {IS_START} → {IS_END}  |  OOS: {OOS_START} → {OOS_END}")
    print(f"  Focus: XAU_USD → london H/L  |  BTC_USD → ny_afternoon H/L")
    print("=" * 78)

    all_results = {}
    for sym in focus:
        all_results[sym] = test_instrument(sym)

    # ── Decision summary ──────────────────────────────────────────────────────
    print()
    print("=" * 78)
    print("  DECISION SUMMARY  (focus instruments: XAU_USD, BTC_USD)")
    print()
    print(f"  {'Instrument':<14} {'Base IS':>8} {'Prior IS':>9} {'Comb IS':>8} "
          f"{'Base OOS':>9} {'Prior OOS':>10} {'Comb OOS':>9}  Decision")
    print(f"  {'─'*76}")

    adopt_count   = 0
    consider_count = 0

    for sym in CORE:
        r = all_results.get(sym, {})
        b_is   = r.get("base_is",  {}).get("profit_factor", 0)
        p_is   = r.get("prior_is", {}).get("profit_factor", 0)
        c_is   = r.get("comb_is",  {}).get("profit_factor", 0)
        b_oos  = r.get("base_oos", {}).get("profit_factor", 0)
        p_oos  = r.get("prior_oos",{}).get("profit_factor", 0)
        c_oos  = r.get("comb_oos", {}).get("profit_factor", 0)

        has_prior = sym in PRIOR_SESSION

        if has_prior:
            prior_beats  = (p_is >= b_is - 0.02) and (p_oos >= b_oos - 0.05)
            comb_ok      = (c_is >= b_is - 0.02) and (c_oos >= b_oos - 0.05)
            if prior_beats: adopt_count   += 1
            if comb_ok:     consider_count += 1
            tag = "✅" if prior_beats else ("⚠️" if comb_ok else "❌")
        else:
            tag = "—"

        p_is_s  = f"{p_is:.3f}" if p_is  else "—"
        c_is_s  = f"{c_is:.3f}" if c_is  else "—"
        p_oos_s = f"{p_oos:.3f}" if p_oos else "—"
        c_oos_s = f"{c_oos:.3f}" if c_oos else "—"

        print(f"  {sym:<14} {b_is:>8.3f} {p_is_s:>9} {c_is_s:>8} "
              f"{b_oos:>9.3f} {p_oos_s:>10} {c_oos_s:>9}  {tag}")

    print()
    focus_count = len(PRIOR_SESSION)
    decision = (
        "ADOPT  ✅" if adopt_count >= focus_count
        else "CONSIDER ⚠️" if consider_count >= focus_count
        else f"PARTIAL ⚠️ ({adopt_count}/{focus_count} prior-sess beats baseline)" if adopt_count > 0
        else "REJECT ❌"
    )
    print(f"  Prior-sess beats baseline (IS+OOS): {adopt_count}/{focus_count} focus instruments")
    print(f"  Combined doesn't hurt baseline:     {consider_count}/{focus_count} focus instruments")
    print(f"\n  → {decision}")
    print()

    out_path = Path(__file__).parent / "results" / "e7_session_levels.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  Results saved → {out_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()
