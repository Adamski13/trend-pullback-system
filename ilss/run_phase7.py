#!/usr/bin/env python3
"""
ILSS Phase 7 — Walk-Forward Validation

Tests whether the edge discovered in Phases 1-6 is real or curve-fit.
Uses an expanding-anchor walk-forward design:

  Window 1: Train 2020-01-01 → 2022-12-31   OOS 2023-01-01 → 2023-12-31
  Window 2: Train 2020-01-01 → 2023-12-31   OOS 2024-01-01 → 2024-12-31
  Window 3: Train 2020-01-01 → 2024-12-31   OOS 2025-01-01 → 2025-12-31

For each window / instrument:
  - IS:  find best session (Phase 3 logic) and best time stop (8, 16, 24 bars)
  - OOS: apply IS-selected params, compute PF / WR / total_R

Pass criteria:
  - 4 core instruments: avg OOS PF ≥ 1.10 across windows
  - Walk-forward efficiency ≥ 0.70 on core instruments
  - No instrument OOS PF < 1.0 in ALL windows (complete failure)

Usage:
    python run_phase7.py
    python run_phase7.py --symbol GBP_USD
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
from src.exit_simulator import (
    simulate_time_stop,
    exit_stats,
)

# ── Walk-forward windows (expanding anchor) ──────────────────────────────────
WINDOWS = [
    {"label": "W1", "train_start": "2020-01-01", "train_end": "2022-12-31",
                    "oos_start":   "2023-01-01", "oos_end":   "2023-12-31"},
    {"label": "W2", "train_start": "2020-01-01", "train_end": "2023-12-31",
                    "oos_start":   "2024-01-01", "oos_end":   "2024-12-31"},
    {"label": "W3", "train_start": "2020-01-01", "train_end": "2024-12-31",
                    "oos_start":   "2025-01-01", "oos_end":   "2025-12-31"},
]

# All sessions to search over during IS optimisation
ALL_SESSIONS = ["asian", "london_open", "london", "ny_open", "ny_afternoon", "ny_close"]
TIME_STOP_BARS = [8, 16, 24]   # 2h, 4h, 6h

EWMAC_THRESHOLD = 5.0
MIN_TRADES = 10

# Phase 3 structural session priors — used when --fixed-params is set
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
# Phase 4 optimal time-stop bars
PHASE4_BARS = {
    "NAS100_USD": 16, "SPX500_USD": 8,  "UK100_GBP": 8,
    "EUR_USD": 24,    "GBP_USD": 8,     "USD_JPY": 16,
    "XAU_USD": 8,     "BTC_USD": 24,
}
# Phase 2 bias usage per instrument
PHASE2_BIAS = {
    "NAS100_USD": True, "SPX500_USD": True, "UK100_GBP": True,
    "EUR_USD": True,    "GBP_USD": True,    "USD_JPY": False,
    "XAU_USD": True,    "BTC_USD": False,
}

FRICTION_R = {
    "NAS100_USD": 0.08, "SPX500_USD": 0.08, "UK100_GBP": 0.08,
    "EUR_USD": 0.05, "GBP_USD": 0.05, "USD_JPY": 0.05,
    "XAU_USD": 0.06, "BTC_USD": 0.12,
}

CORE_SYMBOLS = ["GBP_USD", "USD_JPY", "XAU_USD", "BTC_USD"]


def _weeks_in_range(start: str, end: str) -> float:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    return max(1.0, (e - s).days / 7.0)


def _filter_sfps_by_date(sfps: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    return sfps[(sfps.index >= s) & (sfps.index <= e)].copy()


def _apply_bias_filter(
    bull_sfps: pd.DataFrame,
    df_d: pd.DataFrame,
    cutoff_date: str,
) -> pd.DataFrame:
    """
    Compute bias using daily data only up to cutoff_date (no lookahead).
    """
    df_d_cut = df_d[df_d.index <= pd.Timestamp(cutoff_date)].copy()
    if df_d_cut.empty:
        return bull_sfps

    bias_df = compute_daily_bias(df_d_cut, long_threshold=EWMAC_THRESHOLD)
    bull_sfps = bull_sfps.copy()
    bull_sfps["date"] = bull_sfps.index.normalize()
    bias_daily = bias_df[["forecast", "bias", "above_sma"]].copy()
    bias_daily.index = pd.to_datetime(bias_daily.index).normalize()
    bull_sfps = bull_sfps.join(bias_daily, on="date", how="left")
    bull_sfps = bull_sfps[bull_sfps["bias"] == "bull"].copy()
    return bull_sfps


def _best_session_and_bars(
    sfps: pd.DataFrame,
    enriched: pd.DataFrame,
    friction: float,
    weeks: float,
) -> tuple[list[str], int, float]:
    """
    Grid search over sessions × time_stop_bars on training data.
    Returns (best_sessions, best_bars, best_pf_net).
    """
    best_sessions = ALL_SESSIONS[:1]
    best_bars     = 8
    best_pf       = 0.0

    for sess in ALL_SESSIONS:
        sub = sfps[sfps["session"] == sess].copy()
        if len(sub) < MIN_TRADES:
            continue
        for bars in TIME_STOP_BARS:
            out = simulate_time_stop(sub, enriched, hold_bars=bars)
            s   = exit_stats(out, friction_r=friction)
            if not s:
                continue
            pf  = s.get("profit_factor", 0.0)
            tpw = s.get("total", 0) / weeks
            if tpw < 0.3:   # minimum activity
                continue
            if pf > best_pf:
                best_pf       = pf
                best_sessions = [sess]
                best_bars     = bars

    # Also try the Phase 3 default: top-2 sessions combined
    # (already covered by single-session loop above; return single best)
    return best_sessions, best_bars, best_pf


def run_instrument_wfa(
    symbol: str,
    sfp_cfg: dict,
    strat_cfg: dict,
    windows: list[dict],
    fixed_params: bool = False,
) -> dict:
    """Run walk-forward analysis for one instrument across all windows.

    fixed_params=True: use Phase 3 sessions + Phase 4 time stop as fixed priors.
                       Only evaluates OOS P&L — no IS re-optimisation.
    fixed_params=False: full IS grid search (re-fits session + bars each window).
    """
    friction = FRICTION_R.get(symbol, 0.07)

    # Load full history once
    df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    df_d   = load_cached(symbol, "D",   "2015-01-01", "2025-12-31")

    if df_m15 is None:
        return {"symbol": symbol, "error": "no M15 data"}

    enriched = prepare(df_m15, atr_period=sfp_cfg["atr_period"])
    all_sfps = detect_sfps(
        enriched, symbol=symbol,
        min_sweep_atr=sfp_cfg["sweep_min_depth"],
        max_sweep_atr=sfp_cfg["sweep_max_depth"],
        stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
        active_sessions=None,
    )
    bull_all = all_sfps[all_sfps["direction"] == "bull"].copy()

    window_results = []

    for w in windows:
        label       = w["label"]
        train_start = w["train_start"]
        train_end   = w["train_end"]
        oos_start   = w["oos_start"]
        oos_end     = w["oos_end"]

        train_weeks = _weeks_in_range(train_start, train_end)
        oos_weeks   = _weeks_in_range(oos_start, oos_end)

        # ── IS: build bias-filtered SFP set ─────────────────────────────────
        is_sfps = _filter_sfps_by_date(bull_all, train_start, train_end)

        use_bias = PHASE2_BIAS.get(symbol, True) if fixed_params else True
        if df_d is not None and use_bias:
            is_sfps = _apply_bias_filter(is_sfps, df_d, train_end)
        # Note: if bias gives too few, we still use session filter below

        if len(is_sfps) < MIN_TRADES:
            window_results.append({
                "window": label, "train_end": train_end,
                "is_pf": None, "oos_pf": None,
                "selected_sessions": [], "selected_bars": None,
                "oos_trades": 0, "oos_wr": None, "oos_total_r": None,
                "note": "insufficient IS trades after bias filter",
            })
            continue

        # ── IS: select params ─────────────────────────────────────────────────
        if fixed_params:
            # Use Phase 3/4 structural priors — no re-optimisation
            best_sessions = PHASE3_SESSIONS.get(symbol, ALL_SESSIONS[:1])
            best_bars     = PHASE4_BARS.get(symbol, 8)
        else:
            best_sessions, best_bars, _ = _best_session_and_bars(
                is_sfps, enriched, friction, train_weeks
            )

        is_sub = is_sfps[is_sfps["session"].isin(best_sessions)].copy()
        is_out = simulate_time_stop(is_sub, enriched, hold_bars=best_bars)
        is_stats = exit_stats(is_out, friction_r=friction)
        is_pf_exact = is_stats.get("profit_factor", 0.0) if is_stats else 0.0

        # ── OOS: apply selected params ────────────────────────────────────────
        oos_sfps = _filter_sfps_by_date(bull_all, oos_start, oos_end)

        # Bias filter: apply same bias decision as IS (parameters fixed at train_end)
        if df_d is not None and use_bias:
            oos_sfps = _apply_bias_filter(oos_sfps, df_d, oos_end)

        oos_sub = oos_sfps[oos_sfps["session"].isin(best_sessions)].copy()

        if len(oos_sub) < 3:
            window_results.append({
                "window": label, "train_end": train_end,
                "is_pf": round(is_pf_exact, 3),
                "oos_pf": None,
                "selected_sessions": best_sessions, "selected_bars": best_bars,
                "oos_trades": 0, "oos_wr": None, "oos_total_r": None,
                "note": "insufficient OOS trades",
            })
            continue

        oos_out   = simulate_time_stop(oos_sub, enriched, hold_bars=best_bars)
        oos_stats = exit_stats(oos_out, friction_r=friction)
        oos_pf    = oos_stats.get("profit_factor", 0.0) if oos_stats else 0.0
        oos_wr    = oos_stats.get("win_rate", 0.0) if oos_stats else 0.0
        oos_r     = oos_stats.get("total_r", 0.0) if oos_stats else 0.0
        oos_n     = oos_stats.get("total", 0) if oos_stats else 0

        window_results.append({
            "window":            label,
            "train_end":         train_end,
            "is_pf":             round(is_pf_exact, 3),
            "oos_pf":            round(oos_pf, 3),
            "selected_sessions": best_sessions,
            "selected_bars":     best_bars,
            "oos_trades":        oos_n,
            "oos_wr":            round(oos_wr, 4),
            "oos_total_r":       round(oos_r, 2),
            "oos_tpw":           round(oos_n / oos_weeks, 2),
        })

    # ── Aggregate across windows ──────────────────────────────────────────────
    valid = [r for r in window_results if r.get("oos_pf") is not None]
    if valid:
        oos_pfs   = [r["oos_pf"] for r in valid]
        is_pfs    = [r["is_pf"]  for r in valid if r.get("is_pf") is not None]
        avg_oos   = sum(oos_pfs) / len(oos_pfs)
        std_oos   = (sum((x - avg_oos) ** 2 for x in oos_pfs) / len(oos_pfs)) ** 0.5
        avg_is    = sum(is_pfs) / len(is_pfs) if is_pfs else 0.0
        wfe       = avg_oos / avg_is if avg_is > 0 else 0.0
        pct_deg   = (1 - wfe) * 100
        n_above   = sum(1 for p in oos_pfs if p >= 1.10)
        consistent = (n_above >= 2) and (avg_oos >= 1.10)
        all_fail   = all(p < 1.0 for p in oos_pfs)
    else:
        avg_oos = std_oos = avg_is = wfe = pct_deg = 0.0
        n_above = 0
        consistent = False
        all_fail   = True

    return {
        "symbol":          symbol,
        "windows":         window_results,
        "avg_oos_pf":      round(avg_oos, 3),
        "std_oos_pf":      round(std_oos, 3),
        "avg_is_pf":       round(avg_is, 3),
        "wf_efficiency":   round(wfe, 3),
        "pct_degradation": round(pct_deg, 1),
        "n_windows_above": n_above,
        "consistent":      consistent,
        "all_fail":        all_fail,
    }


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 7 — Walk-Forward Validation")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--fixed-params", action="store_true",
                        help="Lock Phase 3 sessions + Phase 4 exits; no IS re-optimisation")
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg     = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]
    focus_syms  = [args.symbol] if args.symbol else all_symbols

    print("=" * 76)
    print("  ILSS Phase 7 — Walk-Forward Validation (Expanding Anchor)")
    print()
    print("  Windows:")
    for w in WINDOWS:
        print(f"    {w['label']}  IS: {w['train_start']} → {w['train_end']}"
              f"   OOS: {w['oos_start']} → {w['oos_end']}")
    print()
    if args.fixed_params:
        print("  Mode: FIXED PARAMS — Phase 3 sessions + Phase 4 exits (no IS re-fit)")
    else:
        print("  IS optimisation: grid search over sessions × time_stop_bars {8,16,24}")
    print("  OOS: apply params, evaluate on held-out data")
    print("=" * 76)

    all_results: dict[str, dict] = {}

    for symbol in focus_syms:
        if symbol not in FRICTION_R:
            continue

        print(f"\n  ── {symbol} {'─' * (60 - len(symbol))}")
        r = run_instrument_wfa(symbol, sfp_cfg, strat_cfg, WINDOWS,
                               fixed_params=args.fixed_params)
        all_results[symbol] = r

        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue

        # Per-window detail
        print(f"  {'Win':>3}  {'IS PF':>7}  {'OOS PF':>7}  {'OOS n':>6}  "
              f"{'OOS WR%':>7}  {'OOS R':>8}  {'Sessions':<22}  {'Bars':>5}")
        print(f"  {'─'*72}")
        for wr in r["windows"]:
            is_pf   = f"{wr['is_pf']:.3f}"  if wr.get("is_pf")  else "—"
            oos_pf  = f"{wr['oos_pf']:.3f}" if wr.get("oos_pf") else "—"
            oos_wr  = f"{wr['oos_wr']*100:.1f}%" if wr.get("oos_wr") is not None else "—"
            oos_r   = f"{wr['oos_total_r']:+.1f}R" if wr.get("oos_total_r") is not None else "—"
            sess    = ",".join(wr.get("selected_sessions", []))[:22]
            bars    = str(wr.get("selected_bars", "—"))
            n       = wr.get("oos_trades", 0)
            note    = f"  [{wr['note']}]" if wr.get("note") else ""
            print(f"  {wr['window']:>3}  {is_pf:>7}  {oos_pf:>7}  {n:>6}  "
                  f"{oos_wr:>7}  {oos_r:>8}  {sess:<22}  {bars:>5}{note}")

        # Summary line
        verdict = "CONSISTENT" if r["consistent"] else "DEGRADED"
        tag = "✅" if r["consistent"] else ("⚠️" if r["avg_oos_pf"] >= 1.0 else "❌")
        print(f"\n  Avg OOS PF: {r['avg_oos_pf']:.3f}  ±{r['std_oos_pf']:.3f}  |  "
              f"Avg IS PF: {r['avg_is_pf']:.3f}  |  "
              f"WF Efficiency: {r['wf_efficiency']:.2f}  "
              f"({r['pct_degradation']:.0f}% degradation)")
        print(f"  Verdict: {tag} {verdict}")

    # ── Final summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 76)
    print("  PHASE 7 FINAL SUMMARY")
    print()
    print(f"  {'Symbol':<16} {'Avg OOS PF':>11} {'±Std':>6} {'IS PF':>7} "
          f"{'WFE':>6} {'Windows≥1.1':>12} {'Verdict':>12}")
    print(f"  {'─'*72}")

    core_pass   = 0
    any_all_fail = False

    for sym in focus_syms:
        r = all_results.get(sym)
        if not r or "error" in r:
            print(f"  {sym:<16}  —")
            continue
        tag = "CONSISTENT" if r["consistent"] else ("MARGINAL" if r["avg_oos_pf"] >= 1.0 else "DEGRADED")
        print(f"  {sym:<16} {r['avg_oos_pf']:>11.3f} {r['std_oos_pf']:>6.3f} "
              f"{r['avg_is_pf']:>7.3f} {r['wf_efficiency']:>6.2f} "
              f"{r['n_windows_above']:>12}/3  {tag:>12}")

        if sym in CORE_SYMBOLS and r["consistent"]:
            core_pass += 1
        if sym in CORE_SYMBOLS and r["all_fail"]:
            any_all_fail = True

    print()

    # Check walk-forward efficiency on core symbols
    core_wfe_vals = []
    for sym in CORE_SYMBOLS:
        r = all_results.get(sym)
        if r and "wf_efficiency" in r and r["wf_efficiency"] > 0:
            core_wfe_vals.append(r["wf_efficiency"])
    avg_core_wfe = sum(core_wfe_vals) / len(core_wfe_vals) if core_wfe_vals else 0.0

    print(f"  Core instruments consistent (avg OOS PF ≥ 1.10, ≥2/3 windows): "
          f"{core_pass}/{len(CORE_SYMBOLS)}")
    print(f"  Average walk-forward efficiency (core): {avg_core_wfe:.2f}")
    print(f"  Any core instrument complete failure (OOS PF <1.0 in all windows): "
          f"{'YES' if any_all_fail else 'NO'}")
    print()

    # Pass / fail verdict
    pass_criteria = (
        core_pass >= 3 and
        avg_core_wfe >= 0.70 and
        not any_all_fail
    )

    if pass_criteria:
        print("  ✅ PASS — edge appears genuine:")
        print(f"     {core_pass}/4 core instruments consistent across walk-forward windows")
        print(f"     Walk-forward efficiency {avg_core_wfe:.2f} ≥ 0.70 (not curve-fit)")
        print(f"     No complete failures detected")
        print(f"\n     → Proceed to live deployment with 4 core instruments.")
    elif core_pass >= 2 and avg_core_wfe >= 0.60 and not any_all_fail:
        print("  ⚠️  PARTIAL — edge is present but reduced out-of-sample:")
        print(f"     {core_pass}/4 core instruments consistent")
        print(f"     Walk-forward efficiency {avg_core_wfe:.2f}")
        print(f"\n     → Consider reduced position sizing and close monitoring.")
    else:
        print("  ❌ FAIL — walk-forward results indicate likely over-fitting:")
        if core_pass < 3:
            print(f"     Only {core_pass}/4 core instruments consistent OOS")
        if avg_core_wfe < 0.70:
            print(f"     WF efficiency {avg_core_wfe:.2f} < 0.70 (significant IS→OOS decay)")
        if any_all_fail:
            print(f"     One or more core instruments completely fail OOS")
        print(f"\n     → Do not proceed to live trading. Re-examine methodology.")

    # ── Save ─────────────────────────────────────────────────────────────────
    summary = {
        "windows":          WINDOWS,
        "core_symbols":     CORE_SYMBOLS,
        "core_pass":        core_pass,
        "avg_core_wfe":     round(avg_core_wfe, 3),
        "any_core_all_fail": any_all_fail,
        "overall_pass":     pass_criteria,
        "instruments":      all_results,
    }

    out_path = Path(__file__).parent / "results" / "phase7_walkforward.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 76)


if __name__ == "__main__":
    main()
