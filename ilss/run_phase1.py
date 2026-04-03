#!/usr/bin/env python3
"""
ILSS Phase 1 — Raw SFP Statistics + Win Rate

Questions answered:
  - How many SFPs occur per instrument per year? (frequency)
  - Raw win rate at 1:1 R:R — is there any edge before filtering?
  - Which levels and sessions have the highest raw win rate?

No bias filter, no session filter — pure unfiltered SFP performance.
Pass criteria: raw win rate > 50% on ≥5/8 instruments.

Usage:
    python run_phase1.py
    python run_phase1.py --symbol NAS100_USD
    python run_phase1.py --reward 1.5   # test at 1.5:1 R:R
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
from src.sfp_detector import detect_sfps, sfp_summary
from src.outcome_tracker import simulate_outcomes, outcome_stats, print_outcome_stats


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 1")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--reward", type=float, default=1.0,
                        help="R:R target multiple (default 1.0)")
    parser.add_argument("--max-bars", type=int, default=96,
                        help="Max bars before time-stop (default 96 = 24h)")
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg     = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]
    symbols     = [args.symbol] if args.symbol else all_symbols

    print("=" * 68)
    print("  ILSS Phase 1 — Raw SFP Statistics + Win Rate")
    print(f"  Period: 2020-01-01 → 2025-12-31  |  Timeframe: M15")
    print(f"  Target: {args.reward}:1 R:R  |  Time-stop: {args.max_bars} bars ({args.max_bars*15//60}h)")
    print("  No filters applied")
    print("=" * 68)

    all_results  = {}
    freq_pass    = 0
    winrate_pass = 0

    for symbol in symbols:
        df = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
        if df is None:
            print(f"\n  {symbol}: no cached data — run download_data.py first")
            continue

        print(f"\n  {'─'*64}")
        print(f"  {symbol}")
        print(f"  {'─'*64}")

        enriched = prepare(df, atr_period=sfp_cfg["atr_period"])

        sfps = detect_sfps(
            enriched, symbol=symbol,
            min_sweep_atr=sfp_cfg["sweep_min_depth"],
            max_sweep_atr=sfp_cfg["sweep_max_depth"],
            stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
            active_sessions=None,
        )

        # Frequency
        sfp_summary(sfps, symbol="")
        per_week = len(sfps) / (6 * 52)
        if per_week >= 2.0:
            print(f"    ✅ PASS frequency (≥2/wk)")
            freq_pass += 1
        else:
            print(f"    ❌ FAIL frequency (<2/wk)")

        # Outcomes — bull only (long-only initial mode)
        bull_sfps = sfps[sfps["direction"] == "bull"].copy()
        print(f"\n    Simulating outcomes for {len(bull_sfps):,} bull SFPs...")

        bull_with_outcomes = simulate_outcomes(
            bull_sfps, enriched,
            reward_r=args.reward,
            max_bars=args.max_bars,
        )
        stats = outcome_stats(bull_with_outcomes, label=symbol)
        print_outcome_stats(stats)

        if stats.get("win_rate", 0) >= 0.50:
            print(f"    ✅ PASS raw win rate (≥50%)")
            winrate_pass += 1
        else:
            print(f"    ❌ FAIL raw win rate (<50%)")

        # Breakdown by level type
        print(f"\n    By level (bull only):")
        for level, grp in bull_with_outcomes.groupby("level_type"):
            s = outcome_stats(grp)
            if s:
                print(f"      {level:<20} wr={s['win_rate']*100:.1f}%  "
                      f"pf={s['profit_factor']:.2f}  n={s['traded']}")

        # Breakdown by session
        print(f"\n    By session (bull only):")
        for sess, grp in bull_with_outcomes.groupby("session"):
            s = outcome_stats(grp)
            if s:
                print(f"      {sess:<16} wr={s['win_rate']*100:.1f}%  "
                      f"pf={s['profit_factor']:.2f}  n={s['traded']}")

        all_results[symbol] = {
            "frequency": {"total": len(sfps), "per_week": round(per_week, 1),
                          "pass": per_week >= 2.0},
            "bull_outcomes": stats,
        }

    # ── Final summary ─────────────────────────────────────────────────────────
    print()
    print("=" * 68)
    print(f"  PHASE 1 SUMMARY")
    print()
    print(f"  {'Symbol':<16} {'Per Wk':>7} {'Freq':>5} {'Win%':>7} "
          f"{'PF':>7} {'Total R':>8} {'WR OK':>6}")
    print(f"  {'─'*60}")

    for sym, r in all_results.items():
        freq = r["frequency"]
        out  = r.get("bull_outcomes", {})
        freq_ok = "✅" if freq["pass"] else "❌"
        wr_ok   = "✅" if out.get("win_rate", 0) >= 0.50 else "❌"
        print(f"  {sym:<16} {freq['per_week']:>7.1f} {freq_ok:>5} "
              f"{out.get('win_rate',0)*100:>6.1f}% "
              f"{out.get('profit_factor',0):>7.2f} "
              f"{out.get('total_r',0):>+8.1f}R {wr_ok:>6}")

    print()
    print(f"  Frequency filter:  {freq_pass}/{len(all_results)} pass")
    print(f"  Win rate filter:   {winrate_pass}/{len(all_results)} pass (need ≥5)")

    kill = winrate_pass < 5
    print()
    if kill:
        print("  ⛔ KILL CRITERIA MET — raw win rate below threshold")
        print("     No edge to filter for. Do not proceed.")
    else:
        print("  ✅ PROCEED to Phase 2 (daily bias filter)")

    # ── Save ─────────────────────────────────────────────────────────────────
    out_path = Path(__file__).parent / "results" / "phase1_full.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 68)


if __name__ == "__main__":
    main()
