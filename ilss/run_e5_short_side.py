#!/usr/bin/env python3
"""
ILSS Enhancement E5 — Short Side (Bear SFP symmetry)

Tests whether bear SFPs (sweep above PDH/Asian High, close back below)
with a bear daily bias gate (forecast ≤ -5) hold comparable edge to
the validated long side.

Three views per instrument:
  1. Bull-only (current validated baseline, E2 sweep depth locked)
  2. Bear-only (mirror: bear SFP + bear bias gate)
  3. Combined (bull + bear together)

IS: 2020–2024  |  OOS: 2025

Decision rule:
  ADOPT   — bear IS PF ≥ 1.0 AND OOS PF ≥ 1.0 on ≥ 3/4 core
  MONITOR — bear IS PF ≥ 1.0 on ≥ 3/4 but OOS mixed
  REJECT  — IS edge absent or OOS degrades combined PF

Usage:
    python run_e5_short_side.py
    python run_e5_short_side.py --symbol XAU_USD
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader   import load_cached
from src.session_labels import prepare
from src.sfp_detector  import detect_sfps
from src.daily_bias    import compute_daily_bias
from src.exit_simulator import simulate_time_stop, exit_stats

# ── Locked Phase 3/4 params (same as run_enhancements.py) ────────────────────
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
    "USD_JPY": 16,    "XAU_USD": 8,  "BTC_USD": 24,
}
# Bull bias filter settings (from Phase 2)
BULL_BIAS = {
    "NAS100_USD": True, "EUR_USD": True,  "GBP_USD": True,
    "USD_JPY":    True, "XAU_USD": True,  "BTC_USD": False,
}
FRICTION_R = {
    "NAS100_USD": 0.08, "EUR_USD": 0.05, "GBP_USD": 0.05,
    "USD_JPY":    0.05, "XAU_USD": 0.06, "BTC_USD": 0.12,
}
CORE = ["GBP_USD", "USD_JPY", "XAU_USD", "BTC_USD"]

# E2 validated sweep depth (locked)
SWEEP_MIN = 0.4
SWEEP_MAX = 0.8

BEAR_THRESHOLD = -5.0   # EWMAC forecast ≤ this → bear day

IS_START  = "2020-01-01"
IS_END    = "2024-12-31"
OOS_START = "2025-01-01"
OOS_END   = "2025-12-31"


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_enriched(symbol):
    df = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    if df is None:
        return None
    return prepare(df, atr_period=14)


def _load_bias(symbol, cutoff):
    df_d = load_cached(symbol, "D", "2015-01-01", "2025-12-31")
    if df_d is None:
        return None
    df_cut = df_d[df_d.index <= pd.Timestamp(cutoff)].copy()
    return compute_daily_bias(df_cut, long_threshold=5.0)


def _apply_date(sfps, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    return sfps[(sfps.index >= s) & (sfps.index <= e)].copy()


def _apply_bull_bias(sfps, bias_df):
    """Keep only bars where daily forecast ≥ 5 (bull days)."""
    sfps = sfps.copy()
    sfps["date"] = sfps.index.normalize()
    bd = bias_df[["bias"]].copy()
    bd.index = pd.to_datetime(bd.index).normalize()
    sfps = sfps.join(bd, on="date", how="left")
    return sfps[sfps["bias"] == "bull"].copy()


def _apply_bear_bias(sfps, bias_df):
    """Keep only bars where daily forecast ≤ -5 (bear days)."""
    sfps = sfps.copy()
    sfps["date"] = sfps.index.normalize()
    bd = bias_df[["forecast"]].copy()
    bd.index = pd.to_datetime(bd.index).normalize()
    sfps = sfps.join(bd, on="date", how="left")
    return sfps[sfps["forecast"] <= BEAR_THRESHOLD].copy()


def _get_sfps(enriched, symbol, direction, date_start, date_end,
              bias_df=None, apply_bias=True):
    """Detect + filter SFPs for one direction and date range."""
    strat_cfg = _strat_cfg()
    all_sfps = detect_sfps(
        enriched, symbol=symbol,
        min_sweep_atr=SWEEP_MIN,
        max_sweep_atr=SWEEP_MAX,
        stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
        active_sessions=None,
    )
    sfps = all_sfps[all_sfps["direction"] == direction].copy()
    sfps = _apply_date(sfps, date_start, date_end)

    # Session filter
    sessions = PHASE3_SESSIONS.get(symbol, [])
    sfps = sfps[sfps["session"].isin(sessions)].copy()

    # Bias filter
    if apply_bias and bias_df is not None:
        if direction == "bull" and BULL_BIAS.get(symbol, False):
            sfps = _apply_bull_bias(sfps, bias_df)
        elif direction == "bear":
            # Symmetric bear bias — always apply (test both with/without for BTC below)
            sfps = _apply_bear_bias(sfps, bias_df)

    return sfps


_strat_cfg_cache = None
def _strat_cfg():
    global _strat_cfg_cache
    if _strat_cfg_cache is None:
        with open(Path(__file__).parent / "config" / "strategy.yaml") as f:
            _strat_cfg_cache = yaml.safe_load(f)
    return _strat_cfg_cache


# ── Formatting ────────────────────────────────────────────────────────────────

def _row(label, s, base_pf, friction):
    if not s or s.get("total", 0) == 0:
        print(f"    {label:<40} {'—':>5}")
        return
    pf   = s["profit_factor"]
    wr   = s["win_rate"]
    n    = s["total"]
    r    = s["total_r"]
    dpf  = pf - base_pf
    sign = "+" if dpf >= 0 else ""
    tag  = "✅" if dpf > 0.02 else ("❌" if dpf < -0.02 else "—")
    print(f"    {label:<40} {n:>5}  {wr*100:>5.1f}%  {pf:>6.3f} {tag}  "
          f"{r:>+7.1f}R  {sign}{dpf:>+.3f}")


# ── Per-instrument test ───────────────────────────────────────────────────────

def test_instrument(symbol):
    bars     = PHASE4_BARS[symbol]
    friction = FRICTION_R[symbol]

    print(f"\n  {'═'*72}")
    print(f"  {symbol}  |  sessions: {PHASE3_SESSIONS[symbol]}  "
          f"|  bars: {bars}  |  friction: {friction}R")
    print(f"  {'═'*72}")

    enriched = _load_enriched(symbol)
    if enriched is None:
        print("  No data — skipping")
        return {}

    bias_is  = _load_bias(symbol, IS_END)
    bias_oos = _load_bias(symbol, OOS_END)

    # ── IS ────────────────────────────────────────────────────────────────────
    bull_is = _get_sfps(enriched, symbol, "bull", IS_START, IS_END, bias_is)
    bear_is = _get_sfps(enriched, symbol, "bear", IS_START, IS_END, bias_is)

    # BTC bear side: also test without bias (since bull side has no bias filter)
    if symbol == "BTC_USD":
        bear_is_no_bias = _get_sfps(enriched, symbol, "bear", IS_START, IS_END,
                                     bias_is, apply_bias=False)
    else:
        bear_is_no_bias = pd.DataFrame()

    # Combined: concat bull + bear
    combined_is = pd.concat([bull_is, bear_is]).sort_index() if not bear_is.empty else bull_is

    # ── OOS ───────────────────────────────────────────────────────────────────
    bull_oos = _get_sfps(enriched, symbol, "bull", OOS_START, OOS_END, bias_oos)
    bear_oos = _get_sfps(enriched, symbol, "bear", OOS_START, OOS_END, bias_oos)
    combined_oos = pd.concat([bull_oos, bear_oos]).sort_index() if not bear_oos.empty else bull_oos

    # ── Simulate ──────────────────────────────────────────────────────────────
    def sim(sfps):
        if sfps is None or sfps.empty:
            return {}
        out = simulate_time_stop(sfps, enriched, hold_bars=bars)
        return exit_stats(out, friction_r=friction)

    bull_is_s   = sim(bull_is)
    bear_is_s   = sim(bear_is)
    comb_is_s   = sim(combined_is)
    bull_oos_s  = sim(bull_oos)
    bear_oos_s  = sim(bear_oos)
    comb_oos_s  = sim(combined_oos)
    bear_no_b_s = sim(bear_is_no_bias) if symbol == "BTC_USD" else {}

    base_is_pf  = bull_is_s.get("profit_factor", 0)
    base_oos_pf = bull_oos_s.get("profit_factor", 0)

    hdr = f"    {'Label':<40} {'n':>5}  {'WR%':>5}  {'PF':>6}     {'TotalR':>7}  {'ΔPF':>6}"
    sep = f"    {'─'*72}"

    print(f"\n  IS  (2020–2024)")
    print(hdr); print(sep)
    _row("bull-only (baseline)",         bull_is_s,  base_is_pf,  friction)
    _row("bear-only (bear bias ≤-5)",    bear_is_s,  base_is_pf,  friction)
    if symbol == "BTC_USD":
        _row("bear-only (no bias filter)", bear_no_b_s, base_is_pf, friction)
    _row("combined bull+bear",           comb_is_s,  base_is_pf,  friction)

    print(f"\n  OOS (2025)")
    print(hdr); print(sep)
    _row("bull-only (baseline)",         bull_oos_s, base_oos_pf, friction)
    _row("bear-only (bear bias ≤-5)",    bear_oos_s, base_oos_pf, friction)
    _row("combined bull+bear",           comb_oos_s, base_oos_pf, friction)

    return {
        "bull_is":   bull_is_s,  "bear_is":   bear_is_s,  "comb_is":   comb_is_s,
        "bull_oos":  bull_oos_s, "bear_oos":  bear_oos_s, "comb_oos":  comb_oos_s,
        "bear_no_bias_is": bear_no_b_s,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ILSS E5 — Short side test")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    focus = [args.symbol] if args.symbol else (CORE + ["NAS100_USD", "EUR_USD"])
    focus = [s for s in focus if s in PHASE3_SESSIONS]

    print("=" * 74)
    print("  ILSS E5 — Short Side (Bear SFP symmetry)")
    print(f"  Sweep depth: {SWEEP_MIN}–{SWEEP_MAX}× ATR (E2 locked)")
    print(f"  Bear bias gate: EWMAC forecast ≤ {BEAR_THRESHOLD}")
    print(f"  IS: {IS_START} → {IS_END}  |  OOS: {OOS_START} → {OOS_END}")
    print(f"  Instruments: {', '.join(focus)}")
    print("=" * 74)

    all_results = {}
    for sym in focus:
        all_results[sym] = test_instrument(sym)

    # ── Cross-instrument decision ──────────────────────────────────────────────
    print()
    print("=" * 74)
    print("  DECISION SUMMARY")
    print()
    print(f"  {'Instrument':<14} {'Bear IS PF':>10} {'Bear OOS PF':>11} "
          f"{'Comb IS PF':>10} {'Comb OOS PF':>11}  Decision")
    print(f"  {'─'*72}")

    bear_is_pass  = 0
    bear_oos_pass = 0
    comb_improve  = 0

    for sym in CORE:
        r = all_results.get(sym, {})
        b_is  = r.get("bear_is",  {}).get("profit_factor", 0)
        b_oos = r.get("bear_oos", {}).get("profit_factor", 0)
        c_is  = r.get("comb_is",  {}).get("profit_factor", 0)
        c_oos = r.get("comb_oos", {}).get("profit_factor", 0)
        bl_is = r.get("bull_is",  {}).get("profit_factor", 0)

        bear_is_ok  = b_is  >= 1.0
        bear_oos_ok = b_oos >= 1.0
        comb_ok     = c_is  > bl_is - 0.02   # combined doesn't hurt bull baseline

        if bear_is_ok:  bear_is_pass  += 1
        if bear_oos_ok: bear_oos_pass += 1
        if comb_ok:     comb_improve  += 1

        tag = "✅" if (bear_is_ok and bear_oos_ok) else ("⚠️" if bear_is_ok else "❌")
        print(f"  {sym:<14} {b_is:>10.3f} {b_oos:>11.3f} {c_is:>10.3f} {c_oos:>11.3f}  {tag}")

    print()
    decision = (
        "ADOPT  ✅" if (bear_is_pass >= 3 and bear_oos_pass >= 3)
        else "MONITOR ⚠️" if (bear_is_pass >= 2 and bear_oos_pass >= 2)
        else "REJECT ❌"
    )
    print(f"  Bear IS  ≥1.0: {bear_is_pass}/4 core")
    print(f"  Bear OOS ≥1.0: {bear_oos_pass}/4 core")
    print(f"  Combined doesn't hurt: {comb_improve}/4 core")
    print(f"\n  → {decision}")
    print()

    out_path = Path(__file__).parent / "results" / "e5_short_side.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  Results saved → {out_path}")
    print("=" * 74)


if __name__ == "__main__":
    main()
