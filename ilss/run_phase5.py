#!/usr/bin/env python3
"""
ILSS Phase 5 — Friction Sensitivity Analysis

For each instrument, sweeps friction from 0.00 to 0.15R in steps of 0.01R
using the Phase 4 optimal exit strategy. Identifies break-even friction
(PF crosses 1.0) and target friction (PF crosses 1.2) per instrument.

Usage:
    python run_phase5.py
    python run_phase5.py --symbol GBP_USD
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
    "NAS100_USD": {"exit": "time_stop", "bars": 16,  "reward_r": None},   # marginal
    "SPX500_USD": {"exit": "time_stop", "bars": None, "reward_r": 1.0},   # fails
    "UK100_GBP":  {"exit": "time_stop", "bars": 8,   "reward_r": None},   # fails
    "EUR_USD":    {"exit": "time_stop", "bars": 24,  "reward_r": None},   # marginal
    "GBP_USD":    {"exit": "time_stop", "bars": 8,   "reward_r": None},   # PASS
    "USD_JPY":    {"exit": "time_stop", "bars": 16,  "reward_r": None},   # PASS
    "XAU_USD":    {"exit": "time_stop", "bars": 8,   "reward_r": None},   # PASS
    "BTC_USD":    {"exit": "time_stop", "bars": 24,  "reward_r": None},   # PASS
}

FRICTION_R = {
    "NAS100_USD": 0.08, "SPX500_USD": 0.08, "UK100_GBP": 0.08,
    "EUR_USD": 0.05, "GBP_USD": 0.05, "USD_JPY": 0.05,
    "XAU_USD": 0.06, "BTC_USD": 0.12,
}

FRICTION_LEVELS = [round(x * 0.01, 2) for x in range(16)]   # 0.00 … 0.15

WEEKS_IN_PERIOD = 6 * 52


def _find_breakeven(pf_by_friction: list[tuple[float, float]], threshold: float) -> float | None:
    """Return the first friction level where PF drops below threshold."""
    above = None
    for friction, pf in pf_by_friction:
        if pf >= threshold and above is None:
            above = friction
        if pf < threshold and above is not None:
            return friction
    return None


def run_instrument(symbol: str, sfp_cfg: dict, strat_cfg: dict) -> dict:
    cfg = PHASE3_CONFIG.get(symbol)
    ex  = PHASE4_EXIT.get(symbol)
    if not cfg or not ex:
        return {}

    df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
    df_d   = load_cached(symbol, "D",   "2015-01-01", "2025-12-31") if cfg["bias"] else None

    if df_m15 is None:
        print(f"\n  {symbol}: no M15 data — skipping")
        return {}

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

    if len(bull_sfps) < 20:
        print(f"\n  {symbol}: too few SFPs ({len(bull_sfps)}) — skipping")
        return {}

    # Run the Phase 4 optimal exit (once, raw outcomes)
    if ex["reward_r"] is not None:
        # SPX uses fixed target
        out = simulate_fixed_target(bull_sfps, enriched, reward_r=ex["reward_r"])
    else:
        out = simulate_time_stop(bull_sfps, enriched, hold_bars=ex["bars"])

    actual_friction = FRICTION_R[symbol]
    pf_series: list[tuple[float, float]] = []

    print(f"\n  {symbol}")
    print(f"  {'─'*68}")
    print(f"  {'Friction':>8}  {'PF':>7}  {'WR%':>6}  {'Total R':>9}  {'Net R':>9}")
    print(f"  {'─'*68}")

    for f in FRICTION_LEVELS:
        s = exit_stats(out, friction_r=f)
        if not s:
            continue
        pf = s["profit_factor"]
        wr = s["win_rate"]
        tr = s["total_r"]
        pf_series.append((f, pf))
        marker = ""
        if abs(f - actual_friction) < 0.001:
            marker = " ← actual"
        print(f"  {f:>8.2f}  {pf:>7.3f}  {wr*100:>5.1f}%  {tr:>+9.1f}R  {tr:>+9.1f}R{marker}")

    be_10 = _find_breakeven(pf_series, 1.0)
    be_12 = _find_breakeven(pf_series, 1.2)

    s_actual = exit_stats(out, friction_r=actual_friction)
    pf_actual = s_actual.get("profit_factor", 0) if s_actual else 0

    verdict = "PASS" if pf_actual >= 1.20 else ("MARGINAL" if pf_actual >= 1.10 else "FAIL")
    print(f"\n  Break-even (PF=1.0): {be_10 if be_10 is not None else '>0.15'}R")
    print(f"  Target    (PF=1.2): {be_12 if be_12 is not None else '>0.15'}R")
    print(f"  At actual {actual_friction}R friction:  PF={pf_actual:.3f}  [{verdict}]")

    return {
        "symbol": symbol,
        "actual_friction": actual_friction,
        "pf_at_actual": round(pf_actual, 3),
        "verdict": verdict,
        "breakeven_pf10": be_10,
        "breakeven_pf12": be_12,
        "pf_by_friction": {str(f): round(pf, 3) for f, pf in pf_series},
        "exit_used": ex,
        "n_trades": len(bull_sfps),
    }


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 5 — Friction Sensitivity")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg     = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]
    symbols     = [args.symbol] if args.symbol else all_symbols

    print("=" * 72)
    print("  ILSS Phase 5 — Friction Sensitivity Analysis")
    print(f"  Sweep: 0.00 → 0.15R in 0.01R steps")
    print(f"  Exit: Phase 4 optimal per instrument")
    print("=" * 72)

    all_results = {}
    for symbol in symbols:
        if symbol not in PHASE3_CONFIG:
            continue
        r = run_instrument(symbol, sfp_cfg, strat_cfg)
        if r:
            all_results[symbol] = r

    # ── Summary ─────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  PHASE 5 SUMMARY — Friction Survivability")
    print()
    print(f"  {'Symbol':<16} {'Actual F':>8} {'PF@actual':>10} {'BE(1.0)':>8} "
          f"{'BE(1.2)':>8} {'Verdict':>10}")
    print(f"  {'─'*68}")

    surviving = []
    for sym, r in all_results.items():
        be10 = f"{r['breakeven_pf10']:.2f}R" if r["breakeven_pf10"] is not None else ">0.15R"
        be12 = f"{r['breakeven_pf12']:.2f}R" if r["breakeven_pf12"] is not None else ">0.15R"
        tag  = {"PASS": "PASS", "MARGINAL": "MARGINAL", "FAIL": "FAIL"}.get(r["verdict"], "")
        print(f"  {sym:<16} {r['actual_friction']:>8.2f}  {r['pf_at_actual']:>10.3f}  "
              f"{be10:>8}  {be12:>8}  {tag:>10}")
        if r["verdict"] == "PASS":
            surviving.append(sym)

    print()
    print(f"  Instruments surviving at actual friction (PF≥1.20): {len(surviving)}/8")
    if surviving:
        print(f"  Survivors: {', '.join(surviving)}")

    # Save
    out_path = Path(__file__).parent / "results" / "phase5_friction.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
