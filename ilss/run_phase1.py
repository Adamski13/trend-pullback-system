#!/usr/bin/env python3
"""
ILSS Phase 1 — Raw SFP Statistics

Questions answered:
  - How many SFPs occur per instrument per year?
  - Do they pass the frequency filter (≥2/week)?
  - What is the distribution across levels and sessions?

No bias filter, no exit logic — pure pattern counting.

Usage:
    python run_phase1.py
    python run_phase1.py --symbol NAS100_USD
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


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 1: raw SFP statistics")
    parser.add_argument("--symbol", type=str, default=None)
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg    = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]
    symbols     = [args.symbol] if args.symbol else all_symbols

    print("=" * 64)
    print("  ILSS Phase 1 — Raw SFP Statistics")
    print("  Period: 2020-01-01 → 2025-12-31  |  Timeframe: M15")
    print("  No filters applied")
    print("=" * 64)

    all_summaries = {}
    pass_count = 0

    for symbol in symbols:
        df = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
        if df is None:
            print(f"\n  {symbol}: no cached data — run download_data.py first")
            continue

        enriched = prepare(df, atr_period=sfp_cfg["atr_period"])

        sfps = detect_sfps(
            enriched,
            symbol=symbol,
            min_sweep_atr=sfp_cfg["sweep_min_depth"],
            max_sweep_atr=sfp_cfg["sweep_max_depth"],
            stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
            active_sessions=None,    # Phase 1: no session filter
        )

        summary = sfp_summary(sfps, symbol)
        all_summaries[symbol] = summary

        # Qualification check: ≥2 SFPs per week
        if summary.get("per_week", 0) >= 2.0:
            print(f"    ✅ PASS frequency filter (≥2/week)")
            pass_count += 1
        else:
            print(f"    ❌ FAIL frequency filter (<2/week)")

    # ── Cross-instrument summary ──────────────────────────────────────────────
    print()
    print("=" * 64)
    print(f"  SUMMARY: {pass_count}/{len(symbols)} instruments pass frequency filter")
    print()
    print(f"  {'Symbol':<16} {'Total':>7} {'Bull':>7} {'Bear':>7} "
          f"{'Per Wk':>8} {'Freq OK':>8}")
    print(f"  {'─'*58}")
    for sym, s in all_summaries.items():
        if not s:
            continue
        ok = "✅" if s.get("per_week", 0) >= 2.0 else "❌"
        print(f"  {sym:<16} {s['total']:>7,} {s['bull']:>7,} {s['bear']:>7,} "
              f"{s['per_week']:>8.1f} {ok:>8}")

    # ── Save results ──────────────────────────────────────────────────────────
    out_path = Path(__file__).parent / "results" / "phase1_sfp_stats.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_summaries, f, indent=2, default=str)
    print()
    print(f"  Results saved → {out_path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
