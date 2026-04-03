#!/usr/bin/env python3
"""
ILSS Phase 3 — Session Filter

Questions answered:
  - Which session windows produce tradeable edge after bias filter?
  - What is the optimal session set per instrument?
  - Does restricting to 2–3 sessions maintain volume while improving WR?

Methodology:
  - Apply Phase 2 bias filter (EWMAC ≥ 5 for 6 instruments; no filter for USD_JPY, BTC)
  - Evaluate each session individually and in combinations
  - Select optimal session set: highest PF with ≥ 1 trade/week average

Pass criteria: ≥ 5/8 instruments have at least one session with WR ≥ 53%
               OR PF ≥ 1.20 with ≥ 0.5 trades/week

Usage:
    python run_phase3.py
    python run_phase3.py --symbol EUR_USD
    python run_phase3.py --no-bias    # skip bias filter (compare)
"""

import argparse
import json
import sys
from pathlib import Path
from itertools import combinations

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader import load_cached
from src.session_labels import prepare
from src.sfp_detector import detect_sfps
from src.outcome_tracker import simulate_outcomes, outcome_stats
from src.daily_bias import compute_daily_bias


# Instruments where EWMAC bias filter adds lift (Phase 2 conclusion)
BIAS_INSTRUMENTS = {"NAS100_USD", "SPX500_USD", "UK100_GBP",
                    "EUR_USD", "GBP_USD", "XAU_USD"}

# All sessions to evaluate
ALL_SESSIONS = [
    "asian", "london_open", "london",
    "ny_open", "ny_afternoon", "ny_close",
]

WEEKS_IN_PERIOD = 6 * 52   # 2020–2025 ≈ 312 weeks


def trades_per_week(n: int) -> float:
    return n / WEEKS_IN_PERIOD


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 3")
    parser.add_argument("--symbol",    type=str,   default=None)
    parser.add_argument("--reward",    type=float, default=1.0)
    parser.add_argument("--max-bars",  type=int,   default=96)
    parser.add_argument("--threshold", type=float, default=5.0,
                        help="EWMAC bias threshold (default 5.0)")
    parser.add_argument("--no-bias",   action="store_true",
                        help="Skip bias filter for all instruments")
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
    print("  ILSS Phase 3 — Session Filter")
    print(f"  Period: 2020-01-01 → 2025-12-31  |  M15 SFPs + Daily bias")
    print(f"  EWMAC threshold: ≥{args.threshold}  |  {args.reward}:1 R:R  |"
          f"  Time-stop: {args.max_bars} bars ({args.max_bars*15//60}h)")
    bias_note = "no bias filter" if args.no_bias else "EWMAC bias applied to 6/8 instruments"
    print(f"  Bias: {bias_note}")
    print("=" * 72)

    all_results  = {}
    session_pass = 0

    for symbol in symbols:
        df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
        df_d   = load_cached(symbol, "D",   "2015-01-01", "2025-12-31")

        if df_m15 is None:
            print(f"\n  {symbol}: no M15 data")
            continue
        if df_d is None:
            print(f"\n  {symbol}: no Daily data")
            continue

        print(f"\n  {'─'*68}")
        print(f"  {symbol}")
        print(f"  {'─'*68}")

        enriched = prepare(df_m15, atr_period=sfp_cfg["atr_period"])

        sfps = detect_sfps(
            enriched, symbol=symbol,
            min_sweep_atr=sfp_cfg["sweep_min_depth"],
            max_sweep_atr=sfp_cfg["sweep_max_depth"],
            stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
            active_sessions=None,
        )

        bull_sfps = sfps[sfps["direction"] == "bull"].copy()
        if bull_sfps.empty:
            print(f"  {symbol}: no bull SFPs")
            continue

        # Apply bias filter (Phase 2)
        use_bias = (not args.no_bias) and (symbol in BIAS_INSTRUMENTS)
        if use_bias:
            bias_df = compute_daily_bias(df_d, long_threshold=args.threshold)
            bull_sfps = bull_sfps.copy()
            bull_sfps["date"] = bull_sfps.index.normalize()
            bias_daily = bias_df[["forecast", "bias", "above_sma"]].copy()
            bias_daily.index = pd.to_datetime(bias_daily.index).normalize()
            bull_sfps = bull_sfps.join(bias_daily, on="date", how="left")
            bull_sfps = bull_sfps[bull_sfps["bias"] == "bull"].copy()
            print(f"  After EWMAC bias filter: {len(bull_sfps):,} bull SFPs")
        else:
            print(f"  No bias filter: {len(bull_sfps):,} bull SFPs")

        # ── Per-session stats ──────────────────────────────────────────────
        print(f"\n  Individual sessions:")
        print(f"  {'Session':<16} {'n':>5}  {'Tr/Wk':>6}  {'WR%':>5}  "
              f"{'PF':>6}  {'Total R':>8}  {'Sharpe':>7}")
        print(f"  {'─'*68}")

        session_stats = {}
        for sess in ALL_SESSIONS:
            sess_sfps = bull_sfps[bull_sfps["session"] == sess].copy()
            if len(sess_sfps) < 10:
                session_stats[sess] = None
                continue
            out = simulate_outcomes(sess_sfps, enriched,
                                    reward_r=args.reward, max_bars=args.max_bars)
            s = outcome_stats(out)
            if not s:
                session_stats[sess] = None
                continue
            session_stats[sess] = s
            tpw = trades_per_week(s["traded"])
            wr_tag = "✅" if s["win_rate"] >= 0.53 else (
                      "⚠️" if s["win_rate"] >= 0.50 else "❌")
            pf_tag = "✅" if s["profit_factor"] >= 1.20 else ""
            print(f"  {sess:<16} {s['traded']:>5}  {tpw:>6.2f}  "
                  f"{s['win_rate']*100:>5.1f}% {wr_tag}  "
                  f"{s['profit_factor']:>6.2f} {pf_tag}  "
                  f"{s['total_r']:>+8.1f}R  "
                  f"{s['sharpe_r']:>7.3f}")

        # ── Ranked session combos ──────────────────────────────────────────
        # Test all 2-session and 3-session combinations, find best by PF
        print(f"\n  Best session combinations (ranked by profit factor):")
        print(f"  {'Sessions':<36} {'n':>5}  {'Tr/Wk':>6}  {'WR%':>5}  "
              f"{'PF':>6}  {'Total R':>8}")
        print(f"  {'─'*68}")

        combo_results = []
        # Individual + combos of 2 and 3
        for r in range(1, 4):
            for combo in combinations(ALL_SESSIONS, r):
                combo_sfps = bull_sfps[bull_sfps["session"].isin(combo)].copy()
                if len(combo_sfps) < 20:
                    continue
                out = simulate_outcomes(combo_sfps, enriched,
                                        reward_r=args.reward, max_bars=args.max_bars)
                s = outcome_stats(out)
                if not s or s["traded"] < 20:
                    continue
                tpw = trades_per_week(s["traded"])
                if tpw < 0.3:  # too sparse
                    continue
                combo_results.append({
                    "sessions":      list(combo),
                    "sessions_str":  "+".join(combo),
                    "n_sessions":    r,
                    "traded":        s["traded"],
                    "tpw":           round(tpw, 2),
                    "win_rate":      s["win_rate"],
                    "profit_factor": s["profit_factor"],
                    "total_r":       s["total_r"],
                    "sharpe_r":      s["sharpe_r"],
                })

        # Sort by profit factor descending
        combo_results.sort(key=lambda x: x["profit_factor"], reverse=True)

        # Print top-10 best combos
        for cr in combo_results[:10]:
            pf_tag = "✅" if cr["profit_factor"] >= 1.20 else (
                      "⚠️" if cr["profit_factor"] >= 1.05 else "")
            print(f"  {cr['sessions_str']:<36} {cr['traded']:>5}  "
                  f"{cr['tpw']:>6.2f}  {cr['win_rate']*100:>5.1f}%  "
                  f"{cr['profit_factor']:>6.2f} {pf_tag}  "
                  f"{cr['total_r']:>+8.1f}R")

        # ── By level within best session ──────────────────────────────────
        best_combo = combo_results[0] if combo_results else None
        if best_combo:
            best_sessions = best_combo["sessions"]
            best_sfps = bull_sfps[bull_sfps["session"].isin(best_sessions)].copy()
            print(f"\n  Level breakdown for best combo "
                  f"({best_combo['sessions_str']}):")
            print(f"  {'Level':<20} {'n':>5}  {'WR%':>5}  {'PF':>6}  {'Total R':>8}")
            print(f"  {'─'*55}")
            for level, grp in best_sfps.groupby("level_type"):
                out = simulate_outcomes(grp.copy(), enriched,
                                        reward_r=args.reward, max_bars=args.max_bars)
                s = outcome_stats(out)
                if s and s["traded"] >= 5:
                    print(f"  {level:<20} {s['traded']:>5}  "
                          f"{s['win_rate']*100:>5.1f}%  "
                          f"{s['profit_factor']:>6.2f}  "
                          f"{s['total_r']:>+8.1f}R")

        # ── Pass check ────────────────────────────────────────────────────
        best_pf = combo_results[0]["profit_factor"] if combo_results else 0
        best_wr = combo_results[0]["win_rate"] if combo_results else 0
        if best_wr >= 0.53 or best_pf >= 1.20:
            session_pass += 1
            verdict = "✅ PASS"
        else:
            verdict = "❌ FAIL"

        print(f"\n  {verdict}: best combo PF={best_pf:.3f}, WR={best_wr*100:.1f}%")

        all_results[symbol] = {
            "use_bias":       use_bias,
            "total_sfps":     len(bull_sfps),
            "per_session":    {k: v for k, v in session_stats.items() if v},
            "top_combos":     combo_results[:5],
            "best_combo":     best_combo,
        }

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  PHASE 3 SUMMARY — Optimal Session Sets")
    print()
    print(f"  {'Symbol':<16} {'Best sessions':<36} {'WR%':>5}  "
          f"{'PF':>6}  {'Tr/Wk':>6}  {'OK':>3}")
    print(f"  {'─'*72}")

    for sym, r in all_results.items():
        bc = r.get("best_combo")
        if not bc:
            print(f"  {sym:<16} {'—':<36}")
            continue
        tag = "✅" if bc["win_rate"] >= 0.53 or bc["profit_factor"] >= 1.20 else "❌"
        print(f"  {sym:<16} {bc['sessions_str']:<36} "
              f"{bc['win_rate']*100:>5.1f}%  "
              f"{bc['profit_factor']:>6.3f}  "
              f"{bc['tpw']:>6.2f}  {tag}")

    print()
    print(f"  Session filter pass: {session_pass}/{len(all_results)} instruments")

    if session_pass >= 5:
        print(f"\n  ✅ PASS — session filter finds edge on ≥5/8 instruments.")
        print(f"     Proceed to Phase 4 (exit strategy).")
    else:
        print(f"\n  ⚠️  Weak session edge — {session_pass}/8 pass.")
        print(f"     Investigate if different bias thresholds help.")

    # Save
    out_path = Path(__file__).parent / "results" / "phase3_sessions.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
