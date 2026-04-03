#!/usr/bin/env python3
"""
ILSS Phase 2 — Daily Bias Filter

Questions answered:
  - Does filtering bull SFPs by daily EWMAC forecast improve win rate?
  - What forecast threshold produces best risk-adjusted performance?
  - Does SMA200 alone add lift vs no filter?

Tests four filter variants per instrument:
  1. Unfiltered       — Phase 1 baseline
  2. EWMAC ≥ threshold — daily trend forecast long days
  3. SMA200           — close > 200-day SMA
  4. EWMAC + SMA200   — both conditions

Pass criteria: EWMAC filter lifts WR on ≥5/8 instruments.
Kill criterion: filter provides negative lift on majority (>4/8).

Usage:
    python run_phase2.py
    python run_phase2.py --symbol NAS100_USD
    python run_phase2.py --threshold 8    # stricter bias threshold
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
from src.outcome_tracker import simulate_outcomes, outcome_stats
from src.daily_bias import compute_daily_bias


def _print_row(label: str, s: dict, base_wr: float):
    if not s:
        print(f"    {label:<22} {'—':>5}")
        return
    wr   = s.get("win_rate", 0)
    lift = (wr - base_wr) * 100
    tag  = "✅" if lift > 0.5 else ("❌" if lift < -0.5 else "—")
    print(f"    {label:<22} {s['traded']:>5}  {wr*100:>5.1f}%  "
          f"{s.get('profit_factor', 0):>6.2f}  {s.get('total_r', 0):>+8.1f}R  "
          f"{lift:>+5.1f}pp {tag}")


def main():
    parser = argparse.ArgumentParser(description="ILSS Phase 2")
    parser.add_argument("--symbol",    type=str,   default=None)
    parser.add_argument("--reward",    type=float, default=1.0)
    parser.add_argument("--max-bars",  type=int,   default=96)
    parser.add_argument("--threshold", type=float, default=5.0,
                        help="EWMAC forecast threshold for bull bias (default 5.0)")
    args = parser.parse_args()

    cfg_path = Path(__file__).parent / "config"
    with open(cfg_path / "instruments.yaml") as f:
        instr_cfg = yaml.safe_load(f)
    with open(cfg_path / "strategy.yaml") as f:
        strat_cfg = yaml.safe_load(f)

    sfp_cfg     = strat_cfg["sfp"]
    all_symbols = [i["symbol"] for i in instr_cfg["instruments"]]
    symbols     = [args.symbol] if args.symbol else all_symbols
    threshold   = args.threshold

    print("=" * 72)
    print("  ILSS Phase 2 — Daily Bias Filter")
    print(f"  Period: 2020-01-01 → 2025-12-31  |  M15 SFPs + Daily bias")
    print(f"  EWMAC threshold: ≥{threshold}  |  {args.reward}:1 R:R  |"
          f"  Time-stop: {args.max_bars} bars ({args.max_bars*15//60}h)")
    print("=" * 72)

    all_results = {}
    lift_pass   = 0

    for symbol in symbols:
        df_m15 = load_cached(symbol, "M15", "2020-01-01", "2025-12-31")
        df_d   = load_cached(symbol, "D",   "2015-01-01", "2025-12-31")

        if df_m15 is None:
            print(f"\n  {symbol}: no M15 data — run download_data.py first")
            continue
        if df_d is None:
            print(f"\n  {symbol}: no Daily data — run download_data.py first")
            continue

        print(f"\n  {'─'*68}")
        print(f"  {symbol}")
        print(f"  {'─'*68}")

        # ── Prepare M15 data ───────────────────────────────────────────────
        enriched = prepare(df_m15, atr_period=sfp_cfg["atr_period"])

        # ── Detect all SFPs ────────────────────────────────────────────────
        sfps = detect_sfps(
            enriched, symbol=symbol,
            min_sweep_atr=sfp_cfg["sweep_min_depth"],
            max_sweep_atr=sfp_cfg["sweep_max_depth"],
            stop_buffer_atr=strat_cfg["exit"]["stop_buffer_atr"],
            active_sessions=None,
        )

        bull_sfps = sfps[sfps["direction"] == "bull"].copy()
        if bull_sfps.empty:
            print(f"  {symbol}: no bull SFPs detected")
            continue

        # ── Daily bias ─────────────────────────────────────────────────────
        bias_df = compute_daily_bias(
            df_d,
            long_threshold=threshold,
        )

        # Merge bias onto SFPs by calendar date
        bull_sfps = bull_sfps.copy()
        bull_sfps["date"] = bull_sfps.index.normalize()

        bias_daily = bias_df[["forecast", "bias", "above_sma"]].copy()
        bias_daily.index = pd.to_datetime(bias_daily.index).normalize()

        bull_sfps = bull_sfps.join(bias_daily, on="date", how="left")

        # Diagnostic: how many SFP-days have a valid forecast?
        valid_fc   = bull_sfps["forecast"].notna().sum()
        bias_bull  = (bull_sfps["bias"] == "bull").sum()
        bias_bear  = (bull_sfps["bias"] == "bear").sum()
        above_sma  = bull_sfps["above_sma"].sum()
        print(f"  Bull SFPs: {len(bull_sfps):,}  |  "
              f"valid forecast: {valid_fc:,}  |  "
              f"bias=bull: {bias_bull:,}  bear: {bias_bear:,}  "
              f"above_sma: {above_sma:,}")

        # ── Simulate outcomes ──────────────────────────────────────────────
        # 1. Unfiltered
        base_out   = simulate_outcomes(bull_sfps, enriched,
                                       reward_r=args.reward, max_bars=args.max_bars)
        base_stats = outcome_stats(base_out, label="unfiltered")

        # 2. EWMAC bias filter
        ewmac_sfps = bull_sfps[bull_sfps["bias"] == "bull"].copy()
        ewmac_out  = simulate_outcomes(ewmac_sfps, enriched,
                                       reward_r=args.reward, max_bars=args.max_bars)
        ewmac_stats = outcome_stats(ewmac_out, label=f"ewmac≥{threshold}")

        # 3. SMA200 filter
        sma_sfps  = bull_sfps[bull_sfps["above_sma"] == True].copy()
        sma_out   = simulate_outcomes(sma_sfps, enriched,
                                      reward_r=args.reward, max_bars=args.max_bars)
        sma_stats = outcome_stats(sma_out, label="sma200")

        # 4. EWMAC + SMA200
        both_sfps  = bull_sfps[(bull_sfps["bias"] == "bull") &
                                (bull_sfps["above_sma"] == True)].copy()
        both_out   = simulate_outcomes(both_sfps, enriched,
                                       reward_r=args.reward, max_bars=args.max_bars)
        both_stats = outcome_stats(both_out, label="ewmac+sma200")

        # ── Print comparison ───────────────────────────────────────────────
        base_wr = base_stats.get("win_rate", 0)
        print(f"\n    {'Filter':<22} {'n':>5}  {'WR%':>5}  {'PF':>6}  "
              f"{'Total R':>8}  {'Lift':>5}")
        print(f"    {'─'*58}")
        _print_row("unfiltered",          base_stats,  base_wr)
        _print_row(f"ewmac≥{threshold}",  ewmac_stats, base_wr)
        _print_row("sma200",              sma_stats,   base_wr)
        _print_row("ewmac+sma200",        both_stats,  base_wr)

        # ── Threshold sensitivity ──────────────────────────────────────────
        print(f"\n    Threshold sensitivity (forecast ≥ X, long days only):")
        print(f"    {'Thresh':>7}  {'n':>5}  {'WR%':>5}  {'PF':>6}  {'Lift':>5}")
        for t in [0, 2, 5, 8, 10, 12]:
            t_sfps = bull_sfps[bull_sfps["forecast"].fillna(-999) >= t].copy()
            if len(t_sfps) < 20:
                continue
            t_out   = simulate_outcomes(t_sfps, enriched,
                                        reward_r=args.reward, max_bars=args.max_bars)
            t_stats = outcome_stats(t_out)
            if t_stats:
                wr   = t_stats["win_rate"]
                lift = (wr - base_wr) * 100
                print(f"    {t:>7}  {t_stats['traded']:>5}  {wr*100:>5.1f}%  "
                      f"{t_stats['profit_factor']:>6.2f}  {lift:>+5.1f}pp")

        # ── EWMAC-filtered breakdown by session ────────────────────────────
        if not ewmac_out.empty:
            print(f"\n    EWMAC-filtered WR by session (vs unfiltered baseline):")
            for sess, grp in ewmac_out.groupby("session"):
                s = outcome_stats(grp)
                if not s or s["traded"] < 10:
                    continue
                b_grp  = base_out[base_out["session"] == sess]
                b_s    = outcome_stats(b_grp)
                b_wr   = b_s.get("win_rate", 0) if b_s else 0
                lift   = (s["win_rate"] - b_wr) * 100
                tag    = "✅" if lift > 0.5 else ("❌" if lift < -0.5 else "—")
                print(f"      {sess:<16}  wr={s['win_rate']*100:.1f}%  "
                      f"pf={s['profit_factor']:.2f}  n={s['traded']}  "
                      f"lift={lift:+.1f}pp {tag}")

        # Pass count
        ewmac_wr = ewmac_stats.get("win_rate", 0) if ewmac_stats else 0
        if ewmac_wr > base_wr:
            lift_pass += 1

        all_results[symbol] = {
            "base":   base_stats,
            "ewmac":  ewmac_stats,
            "sma200": sma_stats,
            "both":   both_stats,
        }

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  PHASE 2 SUMMARY")
    print()
    print(f"  {'Symbol':<16} {'Base%':>6} {'EWMAC%':>7} {'SMA%':>6} {'Both%':>6} {'Lift':>5} {'OK':>3}")
    print(f"  {'─'*54}")

    for sym, r in all_results.items():
        b_wr  = r["base"].get("win_rate", 0)  * 100 if r.get("base")  else 0
        e_wr  = r["ewmac"].get("win_rate", 0) * 100 if r.get("ewmac") else 0
        s_wr  = r["sma200"].get("win_rate", 0)* 100 if r.get("sma200")else 0
        bo_wr = r["both"].get("win_rate", 0)  * 100 if r.get("both")  else 0
        lift  = e_wr - b_wr
        tag   = "✅" if lift > 0.5 else "❌"
        print(f"  {sym:<16} {b_wr:>5.1f}% {e_wr:>6.1f}% {s_wr:>5.1f}% "
              f"{bo_wr:>5.1f}% {lift:>+5.1f} {tag}")

    print()
    print(f"  EWMAC filter lifts WR on: {lift_pass}/{len(all_results)} instruments")

    if lift_pass >= 5:
        print(f"\n  ✅ PASS — bias filter adds value on ≥5/8 instruments.")
        print(f"     Proceed to Phase 3 (session filter).")
    elif lift_pass == 0:
        print(f"\n  ⛔ KILL — bias filter hurts on all instruments.")
        print(f"     No directional filter for ILSS — trade both directions or no filter.")
    else:
        print(f"\n  ⚠️  PARTIAL — bias filter helps {lift_pass}/8.")
        print(f"     Apply filter only to instruments where it adds lift.")
        print(f"     Proceed to Phase 3 with per-instrument filter flag.")

    # Save
    out_path = Path(__file__).parent / "results" / "phase2_bias.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
