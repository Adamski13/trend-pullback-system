#!/usr/bin/env python3
"""
ILSS Enhancement Testing

Tests four targeted enhancements against the Phase 4 baseline.
Each enhancement is tested independently — no combinations.

IS period: full 2020-2025 (same data as Phase 4)
OOS check: 2025-only (W3 window from Phase 7) — quick sanity check per enhancement

Enhancements tested:
  E1. Asymmetric R:R + time stop  — early target exit (1.5R / 2R) within time window
  E2. Sweep depth filter          — narrow to 0.4–0.8× ATR sweet spot
  E3. Confirmation candle quality — close must be in upper 50% of bar range
  E4. Day-of-week filter          — exclude Monday / Friday per instrument

Instruments: all 4 core (GBP, JPY, XAU, BTC) + 2 marginal (NAS, EUR)

Decision rule per enhancement:
  ADOPT   — IS PF improves AND OOS confirms (≥ 1.0) on ≥ 3/4 core instruments
  MONITOR — IS improves but OOS mixed; note as tentative
  REJECT  — no consistent improvement or OOS degrades

Usage:
    python run_enhancements.py
    python run_enhancements.py --symbol USD_JPY
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader import load_cached
from src.session_labels import prepare
from src.sfp_detector import detect_sfps
from src.daily_bias import compute_daily_bias
from src.exit_simulator import simulate_time_stop, exit_stats

# ── Phase 3/4 fixed configs ───────────────────────────────────────────────────
PHASE3_SESSIONS = {
    "NAS100_USD": ["london_open"],
    "SPX500_USD": ["london"],
    "UK100_GBP":  ["ny_open"],
    "EUR_USD":    ["asian"],
    "GBP_USD":    ["london_open", "ny_close"],
    "USD_JPY":    ["asian"],
    "XAU_USD":    ["ny_afternoon"],
    "BTC_USD":    ["ny_close"],
}
PHASE4_BARS = {
    "NAS100_USD": 16, "SPX500_USD": 8,  "UK100_GBP": 8,
    "EUR_USD": 24,    "GBP_USD": 8,     "USD_JPY": 16,
    "XAU_USD": 8,     "BTC_USD": 24,
}
PHASE2_BIAS = {
    "NAS100_USD": True, "SPX500_USD": True, "UK100_GBP": True,
    "EUR_USD": True,    "GBP_USD": True,    "USD_JPY": False,
    "XAU_USD": True,    "BTC_USD": False,
}
FRICTION_R = {
    "NAS100_USD": 0.08, "SPX500_USD": 0.08, "UK100_GBP": 0.08,
    "EUR_USD":    0.05, "GBP_USD":    0.05, "USD_JPY":   0.05,
    "XAU_USD":    0.06, "BTC_USD":    0.12,
}
CORE = ["GBP_USD", "USD_JPY", "XAU_USD", "BTC_USD"]

# Baseline sweep depth params
SWEEP_MIN_DEFAULT = 0.25
SWEEP_MAX_DEFAULT = 1.5

# E2: sweet-spot sweep depth
SWEEP_MIN_NARROW = 0.4
SWEEP_MAX_NARROW = 0.8

# E4: day-of-week exclusions per instrument (0=Mon, 4=Fri)
DOW_EXCLUDE = {
    "USD_JPY":    [0],        # no Monday Asian (weekend gap / position reset)
    "BTC_USD":    [4],        # no Friday NY Close (thin liquidity)
    "GBP_USD":    [0],        # no Monday London Open
    "XAU_USD":    [0],        # no Monday NY Afternoon
    "NAS100_USD": [0],        # no Monday London Open
    "EUR_USD":    [0, 4],     # no Monday/Friday Asian
    "SPX500_USD": [4],
    "UK100_GBP":  [0],
}

OOS_START = "2025-01-01"
OOS_END   = "2025-12-31"
IS_START  = "2020-01-01"
IS_END    = "2025-12-31"


def _sfp_count_str(n, baseline):
    pct = 100 * n / baseline if baseline > 0 else 0
    return f"{n:,} ({pct:.0f}%)"


def _row(label, s, base_pf, n_baseline, friction):
    """Print one result row relative to baseline."""
    if not s or s.get("total", 0) == 0:
        print(f"    {label:<34} {'—':>5}")
        return
    pf   = s["profit_factor"]
    wr   = s["win_rate"]
    n    = s["total"]
    r    = s["total_r"]
    dpf  = pf - base_pf
    sign = "+" if dpf >= 0 else ""
    tag  = "✅" if dpf > 0.02 else ("❌" if dpf < -0.02 else "—")
    print(f"    {label:<34} {n:>5}  {wr*100:>5.1f}%  {pf:>6.3f} {tag}  "
          f"{r:>+8.1f}R  {sign}{dpf:>+.3f}")


def _load_and_filter(symbol, sfp_cfg, strat_cfg, date_start, date_end,
                     sweep_min=SWEEP_MIN_DEFAULT, sweep_max=SWEEP_MAX_DEFAULT,
                     bias_cutoff=None):
    """Load M15, apply bias + session filter, return filtered bull SFPs + enriched."""
    df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    if df_m15 is None:
        return None, None

    enriched = prepare(df_m15, atr_period=sfp_cfg["atr_period"])

    sfps = detect_sfps(
        enriched, symbol=symbol,
        min_sweep_atr=sweep_min,
        max_sweep_atr=sweep_max,
        stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
        active_sessions=None,
    )
    bull = sfps[sfps["direction"] == "bull"].copy()

    # Date slice
    s, e = pd.Timestamp(date_start), pd.Timestamp(date_end)
    bull = bull[(bull.index >= s) & (bull.index <= e)].copy()

    # Bias filter
    if PHASE2_BIAS.get(symbol, False):
        df_d = load_cached(symbol, "D", "2015-01-01", "2025-12-31")
        if df_d is not None:
            cutoff = bias_cutoff or date_end
            df_d_cut = df_d[df_d.index <= pd.Timestamp(cutoff)].copy()
            bias_df  = compute_daily_bias(df_d_cut, long_threshold=5.0)
            bull["date"] = bull.index.normalize()
            bd = bias_df[["bias"]].copy()
            bd.index = pd.to_datetime(bd.index).normalize()
            bull = bull.join(bd, on="date", how="left")
            bull = bull[bull["bias"] == "bull"].copy()

    # Session filter
    sessions = PHASE3_SESSIONS.get(symbol, [])
    bull = bull[bull["session"].isin(sessions)].copy()

    return bull, enriched


def test_instrument(symbol, sfp_cfg, strat_cfg):
    friction = FRICTION_R.get(symbol, 0.07)
    bars     = PHASE4_BARS.get(symbol, 8)

    print(f"\n  {'═'*70}")
    print(f"  {symbol}  |  sessions: {PHASE3_SESSIONS[symbol]}  "
          f"|  bars: {bars}  |  friction: {friction}R")
    print(f"  {'═'*70}")

    # ── Baseline (IS) ─────────────────────────────────────────────────────────
    bull_is, enriched = _load_and_filter(symbol, sfp_cfg, strat_cfg,
                                          IS_START, IS_END)
    if bull_is is None or bull_is.empty:
        print(f"  No data — skipping")
        return {}

    base_out  = simulate_time_stop(bull_is, enriched, hold_bars=bars)
    base_s    = exit_stats(base_out, friction_r=friction)
    base_pf   = base_s.get("profit_factor", 0) if base_s else 0
    base_n    = base_s.get("total", 0) if base_s else 0

    # ── Baseline (OOS) ────────────────────────────────────────────────────────
    bull_oos, _ = _load_and_filter(symbol, sfp_cfg, strat_cfg,
                                    OOS_START, OOS_END, bias_cutoff=OOS_END)
    if bull_oos is not None and not bull_oos.empty:
        oos_base_out = simulate_time_stop(bull_oos, enriched, hold_bars=bars)
        oos_base_s   = exit_stats(oos_base_out, friction_r=friction)
        oos_base_pf  = oos_base_s.get("profit_factor", 0) if oos_base_s else 0
    else:
        oos_base_s  = {}
        oos_base_pf = 0

    print(f"\n  {'Label':<34} {'n':>5}  {'WR%':>5}  {'PF':>6}     {'Total R':>8}  {'ΔPF':>6}")
    print(f"  IS   {'─'*62}")
    _row("baseline",          base_s,   base_pf, base_n, friction)

    results = {"baseline_is": base_s, "baseline_oos": oos_base_s}

    # ──────────────────────────────────────────────────────────────────────────
    # E1: Time stop + profit target
    # ──────────────────────────────────────────────────────────────────────────
    print(f"\n  E1 — Asymmetric R:R (target within time window)")
    print(f"  IS   {'─'*62}")
    _row("baseline (time-only)", base_s, base_pf, base_n, friction)

    e1_results = {}
    for tr in [1.5, 2.0]:
        out = simulate_time_stop(bull_is, enriched, hold_bars=bars, target_r=tr)
        s   = exit_stats(out, friction_r=friction)
        _row(f"time {bars*15//60}h + target {tr}R", s, base_pf, base_n, friction)
        e1_results[f"target_{tr}R_is"] = s

    print(f"  OOS  {'─'*62}")
    _row("baseline", oos_base_s, oos_base_pf, 0, friction)
    if bull_oos is not None and not bull_oos.empty:
        for tr in [1.5, 2.0]:
            out = simulate_time_stop(bull_oos, enriched, hold_bars=bars, target_r=tr)
            s   = exit_stats(out, friction_r=friction)
            _row(f"time {bars*15//60}h + target {tr}R", s, oos_base_pf, 0, friction)
            e1_results[f"target_{tr}R_oos"] = s

    results["e1"] = e1_results

    # ──────────────────────────────────────────────────────────────────────────
    # E2: Sweep depth filter
    # ──────────────────────────────────────────────────────────────────────────
    print(f"\n  E2 — Sweep depth filter (0.4–0.8× ATR vs 0.25–1.5× baseline)")
    print(f"  IS   {'─'*62}")
    _row(f"baseline (0.25–1.5× ATR)", base_s, base_pf, base_n, friction)

    bull_narrow_is, _ = _load_and_filter(
        symbol, sfp_cfg, strat_cfg, IS_START, IS_END,
        sweep_min=SWEEP_MIN_NARROW, sweep_max=SWEEP_MAX_NARROW,
    )
    if bull_narrow_is is not None and not bull_narrow_is.empty:
        out = simulate_time_stop(bull_narrow_is, enriched, hold_bars=bars)
        s   = exit_stats(out, friction_r=friction)
        _row(f"narrow (0.40–0.80× ATR)  n={_sfp_count_str(len(bull_narrow_is), base_n)}",
             s, base_pf, base_n, friction)
        results["e2_is"] = s

        print(f"  OOS  {'─'*62}")
        _row("baseline", oos_base_s, oos_base_pf, 0, friction)
        bull_narrow_oos, _ = _load_and_filter(
            symbol, sfp_cfg, strat_cfg, OOS_START, OOS_END,
            sweep_min=SWEEP_MIN_NARROW, sweep_max=SWEEP_MAX_NARROW,
            bias_cutoff=OOS_END,
        )
        if bull_narrow_oos is not None and not bull_narrow_oos.empty:
            out = simulate_time_stop(bull_narrow_oos, enriched, hold_bars=bars)
            s   = exit_stats(out, friction_r=friction)
            _row(f"narrow (0.40–0.80× ATR)", s, oos_base_pf, 0, friction)
            results["e2_oos"] = s
    else:
        print(f"    (too few SFPs in narrow range)")

    # ──────────────────────────────────────────────────────────────────────────
    # E3: Confirmation candle quality (close in upper 50% of range)
    # ──────────────────────────────────────────────────────────────────────────
    print(f"\n  E3 — Candle quality (close in upper 50% of bar range)")
    print(f"  IS   {'─'*62}")
    _row("baseline (all closes)", base_s, base_pf, base_n, friction)

    close_pct   = (bull_is["close"] - bull_is["low"]) / (bull_is["high"] - bull_is["low"])
    strong_is   = bull_is[close_pct >= 0.50].copy()
    weak_is     = bull_is[close_pct < 0.50].copy()

    if not strong_is.empty:
        out_strong = simulate_time_stop(strong_is, enriched, hold_bars=bars)
        s_strong   = exit_stats(out_strong, friction_r=friction)
        out_weak   = simulate_time_stop(weak_is, enriched, hold_bars=bars)
        s_weak     = exit_stats(out_weak, friction_r=friction)
        sp = len(strong_is)
        wp = len(weak_is)
        _row(f"strong close (≥50%)  n={_sfp_count_str(sp, base_n)}", s_strong, base_pf, base_n, friction)
        _row(f"weak close   (<50%)  n={_sfp_count_str(wp, base_n)}", s_weak,   base_pf, base_n, friction)
        results["e3_strong_is"] = s_strong
        results["e3_weak_is"]   = s_weak

        print(f"  OOS  {'─'*62}")
        _row("baseline", oos_base_s, oos_base_pf, 0, friction)
        if bull_oos is not None and not bull_oos.empty:
            cp_oos       = (bull_oos["close"] - bull_oos["low"]) / \
                           (bull_oos["high"] - bull_oos["low"])
            strong_oos   = bull_oos[cp_oos >= 0.50].copy()
            if not strong_oos.empty:
                out = simulate_time_stop(strong_oos, enriched, hold_bars=bars)
                s   = exit_stats(out, friction_r=friction)
                _row(f"strong close (≥50%)  n={_sfp_count_str(len(strong_oos), len(bull_oos))}",
                     s, oos_base_pf, 0, friction)
                results["e3_strong_oos"] = s

    # ──────────────────────────────────────────────────────────────────────────
    # E4: Day-of-week filter
    # ──────────────────────────────────────────────────────────────────────────
    print(f"\n  E4 — Day-of-week filter (exclude specific weekdays)")
    print(f"  IS   {'─'*62}")
    _row("baseline (all days)", base_s, base_pf, base_n, friction)

    exclude_days = DOW_EXCLUDE.get(symbol, [])
    if exclude_days:
        dow       = pd.to_datetime(bull_is.index).dayofweek
        dow_filt  = bull_is[~dow.isin(exclude_days)].copy()
        dow_excl  = bull_is[dow.isin(exclude_days)].copy()
        day_names = {0:"Mon", 1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri"}
        excl_str  = "+".join(day_names.get(d, str(d)) for d in exclude_days)

        if not dow_filt.empty:
            out_filt = simulate_time_stop(dow_filt, enriched, hold_bars=bars)
            s_filt   = exit_stats(out_filt, friction_r=friction)
            out_excl = simulate_time_stop(dow_excl, enriched, hold_bars=bars)
            s_excl   = exit_stats(out_excl, friction_r=friction)

            _row(f"excl {excl_str}  n={_sfp_count_str(len(dow_filt), base_n)}",
                 s_filt, base_pf, base_n, friction)
            _row(f"only {excl_str}  n={_sfp_count_str(len(dow_excl), base_n)}",
                 s_excl, base_pf, base_n, friction)
            results["e4_filtered_is"] = s_filt
            results["e4_excluded_is"] = s_excl

            print(f"  OOS  {'─'*62}")
            _row("baseline", oos_base_s, oos_base_pf, 0, friction)
            if bull_oos is not None and not bull_oos.empty:
                dow_oos       = pd.to_datetime(bull_oos.index).dayofweek
                dow_filt_oos  = bull_oos[~dow_oos.isin(exclude_days)].copy()
                if not dow_filt_oos.empty:
                    out = simulate_time_stop(dow_filt_oos, enriched, hold_bars=bars)
                    s   = exit_stats(out, friction_r=friction)
                    _row(f"excl {excl_str}  n={_sfp_count_str(len(dow_filt_oos), len(bull_oos))}",
                         s, oos_base_pf, 0, friction)
                    results["e4_filtered_oos"] = s
    else:
        print(f"    (no specific exclusions defined for {symbol})")

    return results


def main():
    parser = argparse.ArgumentParser(description="ILSS Enhancement Testing")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg = strat_cfg["sfp"]

    focus = [args.symbol] if args.symbol else (CORE + ["NAS100_USD", "EUR_USD"])

    print("=" * 72)
    print("  ILSS Enhancement Testing")
    print(f"  IS: {IS_START} → {IS_END}  |  OOS check: {OOS_START} → {OOS_END}")
    print(f"  Instruments: {', '.join(focus)}")
    print()
    print("  Decision rule: ADOPT if IS PF improves AND OOS ≥ baseline on ≥3/4 core")
    print("=" * 72)

    all_results = {}
    for sym in focus:
        if sym not in PHASE3_SESSIONS:
            continue
        r = test_instrument(sym, sfp_cfg, strat_cfg)
        all_results[sym] = r

    # ── Cross-instrument summary ───────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  ENHANCEMENT SUMMARY (core instruments)")
    print()

    enhancements = [
        ("E1: target 1.5R",  "e1", "target_1.5R_is",  "target_1.5R_oos"),
        ("E1: target 2.0R",  "e1", "target_2.0R_is",  "target_2.0R_oos"),
        ("E2: sweep 0.4-0.8","e2", "e2_is",            "e2_oos"),
        ("E3: strong close", "e3", "e3_strong_is",     "e3_strong_oos"),
        ("E4: DOW filter",   "e4", "e4_filtered_is",   "e4_filtered_oos"),
    ]

    for label, group, is_key, oos_key in enhancements:
        is_lifts  = []
        oos_lifts = []
        for sym in CORE:
            r = all_results.get(sym, {})
            base_is  = r.get("baseline_is",  {}).get("profit_factor", 0)
            base_oos = r.get("baseline_oos", {}).get("profit_factor", 0)

            # Navigate nested structure for E1
            data = r.get(group, r) if group == "e1" else r
            is_pf  = data.get(is_key,  {}).get("profit_factor", base_is)  if data.get(is_key)  else base_is
            oos_pf = data.get(oos_key, {}).get("profit_factor", base_oos) if data.get(oos_key) else base_oos

            is_lifts.append(is_pf  - base_is)
            oos_lifts.append(oos_pf - base_oos)

        n_is_pos  = sum(1 for x in is_lifts  if x > 0.01)
        n_oos_pos = sum(1 for x in oos_lifts if x > -0.05)  # OOS: tolerate small dip
        avg_is    = sum(is_lifts) / len(is_lifts)  if is_lifts  else 0
        avg_oos   = sum(oos_lifts)/ len(oos_lifts) if oos_lifts else 0

        decision = "ADOPT  ✅" if (n_is_pos >= 3 and n_oos_pos >= 3) \
              else "MONITOR ⚠️" if (n_is_pos >= 2 and n_oos_pos >= 2) \
              else "REJECT ❌"

        print(f"  {label:<24} IS lift: {avg_is:>+.3f} ({n_is_pos}/4)  "
              f"OOS: {avg_oos:>+.3f} ({n_oos_pos}/4)  → {decision}")

    print()

    out_path = Path(__file__).parent / "results" / "enhancements.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"  Results saved → {out_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
