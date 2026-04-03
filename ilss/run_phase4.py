#!/usr/bin/env python3
"""
ILSS Phase 4 — Exit Strategy Optimisation

Uses Phase 3 optimal session configs per instrument and tests all exit variants:
  1. Fixed target       — 1:1, 1.5:1, 2:1 R:R (binary outcomes)
  2. ATR trail          — trail at 1.5× ATR (continuous P&L)
  3. ATR trail + cap    — trail with 2:1 fixed target cap
  4. Session close      — hold to end of session (continuous P&L)
  5. Time stop          — exit after 2h / 4h / 6h (continuous P&L)

Friction estimates applied:
  FX majors (EUR, GBP, JPY):  ~0.05R round-trip
  Indices (NAS, SPX, UK100):  ~0.08R
  XAU:                        ~0.06R
  BTC:                        ~0.12R

Pass criteria: PF ≥ 1.20 net of friction on ≥ 5/8 instruments.
               Best exit method must maintain > 0.5 trades/week.

Usage:
    python run_phase4.py
    python run_phase4.py --symbol XAU_USD
    python run_phase4.py --trail-mult 2.0   # test wider trail
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
    simulate_atr_trail,
    simulate_session_close,
    simulate_time_stop,
    exit_stats,
)

# ── Phase 3 optimal configs ────────────────────────────────────────────────────
# From run_phase3.py results — best session(s) per instrument
PHASE3_CONFIG = {
    "NAS100_USD": {"sessions": ["london_open"],           "bias": True},
    "SPX500_USD": {"sessions": ["london"],                "bias": True},
    "UK100_GBP":  {"sessions": ["ny_open"],               "bias": True},
    "EUR_USD":    {"sessions": ["asian"],                  "bias": True},
    "GBP_USD":    {"sessions": ["london_open", "ny_close"],"bias": True},
    "USD_JPY":    {"sessions": ["asian"],                  "bias": False},
    "XAU_USD":    {"sessions": ["ny_afternoon"],           "bias": True},
    "BTC_USD":    {"sessions": ["ny_close"],               "bias": False},
}

# Round-trip friction estimates in R units
FRICTION_R = {
    "NAS100_USD": 0.08,
    "SPX500_USD": 0.08,
    "UK100_GBP":  0.08,
    "EUR_USD":    0.05,
    "GBP_USD":    0.05,
    "USD_JPY":    0.05,
    "XAU_USD":    0.06,
    "BTC_USD":    0.12,
}

WEEKS_IN_PERIOD = 6 * 52


def _row(label: str, s: dict, base_pf: float, friction: float):
    """Print one result row."""
    if not s:
        print(f"    {label:<28} {'—':>5}")
        return
    pf    = s["profit_factor"]
    wr    = s["win_rate"]
    r     = s["total_r"]
    tpw   = s["total"] / WEEKS_IN_PERIOD
    lift  = pf - base_pf
    pf_tag = "✅" if pf >= 1.20 else ("⚠️" if pf >= 1.10 else "")
    print(f"    {label:<28} {s['total']:>5}  {tpw:>5.1f}/wk  "
          f"{wr*100:>5.1f}%  {pf:>6.3f} {pf_tag}  "
          f"{r:>+8.1f}R  {lift:>+6.3f}")


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 4")
    parser.add_argument("--symbol",     type=str,   default=None)
    parser.add_argument("--threshold",  type=float, default=5.0)
    parser.add_argument("--trail-mult", type=float, default=1.5,
                        help="ATR trail multiplier (default 1.5)")
    parser.add_argument("--max-bars",   type=int,   default=96)
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg     = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]
    symbols     = [args.symbol] if args.symbol else all_symbols

    print("=" * 76)
    print("  ILSS Phase 4 — Exit Strategy Optimisation")
    print(f"  Using Phase 3 optimal session configs per instrument")
    print(f"  ATR trail mult: {args.trail_mult}×  |  Max bars: {args.max_bars}")
    print(f"  Friction applied: per-instrument spread+slippage estimates")
    print("=" * 76)

    all_results = {}
    pf_pass     = 0

    for symbol in symbols:
        cfg    = PHASE3_CONFIG.get(symbol)
        if not cfg:
            continue

        df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
        df_d   = load_cached(symbol, "D",   "2015-01-01", "2025-12-31") if cfg["bias"] else None

        if df_m15 is None:
            print(f"\n  {symbol}: no M15 data")
            continue

        friction = FRICTION_R.get(symbol, 0.07)

        print(f"\n  {'─'*72}")
        print(f"  {symbol}  |  sessions: {cfg['sessions']}  |  "
              f"bias: {cfg['bias']}  |  friction: {friction}R")
        print(f"  {'─'*72}")

        enriched = prepare(df_m15, atr_period=sfp_cfg["atr_period"])

        sfps = detect_sfps(
            enriched, symbol=symbol,
            min_sweep_atr=sfp_cfg["sweep_min_depth"],
            max_sweep_atr=sfp_cfg["sweep_max_depth"],
            stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
            active_sessions=None,
        )

        bull_sfps = sfps[sfps["direction"] == "bull"].copy()

        # Apply Phase 2 bias filter
        if cfg["bias"] and df_d is not None:
            bias_df = compute_daily_bias(df_d, long_threshold=args.threshold)
            bull_sfps = bull_sfps.copy()
            bull_sfps["date"] = bull_sfps.index.normalize()
            bias_daily = bias_df[["forecast", "bias", "above_sma"]].copy()
            bias_daily.index = pd.to_datetime(bias_daily.index).normalize()
            bull_sfps = bull_sfps.join(bias_daily, on="date", how="left")
            bull_sfps = bull_sfps[bull_sfps["bias"] == "bull"].copy()

        # Apply Phase 3 session filter
        bull_sfps = bull_sfps[bull_sfps["session"].isin(cfg["sessions"])].copy()
        print(f"  Filtered SFPs: {len(bull_sfps):,}")

        if len(bull_sfps) < 20:
            print(f"  Too few SFPs — skipping")
            continue

        print(f"\n  {'Exit strategy':<28} {'n':>5}  {'Tr/Wk':>6}  "
              f"{'WR%':>5}  {'PF(gross)':>9}  {'Total R':>8}  {'ΔPF':>6}")
        print(f"  {'─'*72}")

        sym_results = {}

        # ── 1. Fixed targets ────────────────────────────────────────────────
        for rr in [1.0, 1.5, 2.0]:
            out   = simulate_fixed_target(bull_sfps, enriched,
                                          reward_r=rr, max_bars=args.max_bars)
            s_raw = exit_stats(out, label=f"fixed_{rr}R", friction_r=0)
            s_net = exit_stats(out, label=f"fixed_{rr}R_net", friction_r=friction)

            if rr == 1.0:
                base_pf = s_net.get("profit_factor", 1.0)

            label = f"fixed {rr}:1 R:R"
            _row(label, s_net, base_pf, friction)
            sym_results[f"fixed_{rr}R"] = {"gross": s_raw, "net": s_net}

        print(f"  {'·'*72}")

        # ── 2. ATR trail ─────────────────────────────────────────────────────
        out  = simulate_atr_trail(bull_sfps, enriched,
                                  trail_mult=args.trail_mult, max_bars=args.max_bars)
        s_net = exit_stats(out, label=f"atr_trail_{args.trail_mult}x", friction_r=friction)
        _row(f"ATR trail {args.trail_mult}×", s_net, base_pf, friction)
        sym_results["atr_trail"] = exit_stats(out, friction_r=0)
        sym_results["atr_trail_net"] = s_net

        # ATR trail + 2:1 target cap
        out  = simulate_atr_trail(bull_sfps, enriched,
                                  trail_mult=args.trail_mult, max_bars=args.max_bars,
                                  target_r=2.0)
        s_net = exit_stats(out, label=f"atr_trail+2R_cap", friction_r=friction)
        _row(f"ATR trail {args.trail_mult}× + 2R cap", s_net, base_pf, friction)
        sym_results["atr_trail_2cap_net"] = s_net

        print(f"  {'·'*72}")

        # ── 3. Session close ─────────────────────────────────────────────────
        out  = simulate_session_close(bull_sfps, enriched, max_bars=args.max_bars)
        s_net = exit_stats(out, label="session_close", friction_r=friction)
        _row("session close", s_net, base_pf, friction)
        sym_results["session_close_net"] = s_net

        print(f"  {'·'*72}")

        # ── 4. Time stops ─────────────────────────────────────────────────────
        for hours, bars in [(2, 8), (4, 16), (6, 24)]:
            out   = simulate_time_stop(bull_sfps, enriched, hold_bars=bars)
            s_net = exit_stats(out, label=f"time_{hours}h", friction_r=friction)
            _row(f"time stop {hours}h ({bars} bars)", s_net, base_pf, friction)
            sym_results[f"time_{hours}h_net"] = s_net

        # ── Best exit ─────────────────────────────────────────────────────────
        # Rank net results by PF, requiring ≥ 0.5 trades/week
        candidates = [
            (k, v) for k, v in sym_results.items()
            if k.endswith("_net") and v and v.get("profit_factor", 0) > 0
            and v.get("total", 0) / WEEKS_IN_PERIOD >= 0.5
        ]
        if candidates:
            best_key, best_s = max(candidates, key=lambda x: x[1]["profit_factor"])
            best_pf  = best_s["profit_factor"]
            best_tpw = best_s["total"] / WEEKS_IN_PERIOD
            verdict  = "✅ PASS" if best_pf >= 1.20 else ("⚠️  MARGINAL" if best_pf >= 1.10 else "❌ FAIL")
            print(f"\n  {verdict}: best = {best_key}  PF={best_pf:.3f}  "
                  f"{best_tpw:.1f} tr/wk  (net of {friction}R friction)")
            if best_pf >= 1.20:
                pf_pass += 1
        else:
            print(f"\n  ❌ No exit method meets minimum trade frequency")

        all_results[symbol] = sym_results

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 76)
    print("  PHASE 4 SUMMARY — Best exit per instrument (net of friction)")
    print()
    print(f"  {'Symbol':<16} {'Best exit':<24} {'PF(net)':>8} {'WR%':>6} "
          f"{'Tr/Wk':>7} {'Total R':>8} {'OK':>4}")
    print(f"  {'─'*72}")

    for sym, r in all_results.items():
        candidates = [
            (k, v) for k, v in r.items()
            if k.endswith("_net") and v and v.get("profit_factor", 0) > 0
            and v.get("total", 0) / WEEKS_IN_PERIOD >= 0.5
        ]
        if not candidates:
            print(f"  {sym:<16} {'—':<24}")
            continue
        best_key, best_s = max(candidates, key=lambda x: x[1]["profit_factor"])
        pf  = best_s["profit_factor"]
        wr  = best_s["win_rate"]
        tpw = best_s["total"] / WEEKS_IN_PERIOD
        tot = best_s["total_r"]
        tag = "✅" if pf >= 1.20 else ("⚠️" if pf >= 1.10 else "❌")
        label = best_key.replace("_net", "").replace("_", " ")
        print(f"  {sym:<16} {label:<24} {pf:>8.3f} {wr*100:>5.1f}%  "
              f"{tpw:>6.1f}  {tot:>+8.1f}R  {tag}")

    print()
    print(f"  PF ≥ 1.20 net of friction: {pf_pass}/{len(all_results)} instruments")

    if pf_pass >= 5:
        print(f"\n  ✅ PASS — viable exit exists on ≥5/8 instruments after friction.")
        print(f"     Proceed to Phase 5 (full friction + portfolio construction).")
    elif pf_pass >= 3:
        print(f"\n  ⚠️  PARTIAL — viable exits on {pf_pass}/8.")
        print(f"     Consider only trading the {pf_pass} passing instruments.")
    else:
        print(f"\n  ⛔ FAIL — friction destroys edge on most instruments.")
        print(f"     Re-examine spread assumptions or stop proceeding.")

    # Save
    out_path = Path(__file__).parent / "results" / "phase4_exits.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 76)


if __name__ == "__main__":
    main()
