#!/usr/bin/env python3
"""
TPS v2.1 Walk-Forward Validation

Tests whether the production config generalises to unseen data.

Three views:
  1. Full period          2012-01-01 → 2025-12-31  (baseline)
  2. In-sample (train)    2012-01-01 → 2020-12-31  (period used to select params)
  3. Out-of-sample (test) 2021-01-01 → 2025-12-31  (never seen — real validation)

Then a rolling walk-forward:
  - 5-year train window, 2-year test window, 1-year step
  - Shows whether the system is consistent or front-loaded

Key question: does Sharpe / CAGR / max-DD hold up in the OOS period?
If OOS Sharpe is within ~20% of IS Sharpe, the system is robust.
"""

import copy
import sys
import yaml
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.data_loader import load_multiple
from src.strategy import StrategyV2, Backtester


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config(path="config/default_config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def run_period(config, data, start, end, label=""):
    """Run one backtest slice; returns metrics dict or None."""
    cfg = copy.deepcopy(config)
    cfg['data']['start_date'] = start
    cfg['data']['end_date'] = end

    symbols = [i['symbol'] for i in cfg['instruments']]
    slice_data = {}
    for s in symbols:
        if s not in data:
            continue
        df = data[s]
        mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        sliced = df.loc[mask]
        if len(sliced) > 300:          # need enough bars for warmup + signal
            slice_data[s] = sliced

    if len(slice_data) < 2:
        print(f"  [{label}] not enough instruments with data, skipping")
        return None

    # Recalculate equal weights for available instruments
    n = len(slice_data)
    cfg['sizing']['instrument_weights'] = {s: 1.0 / n for s in slice_data}

    try:
        strategy = StrategyV2(cfg)
        backtester = Backtester(strategy, slice_data, cfg)
        results = backtester.run()
        return results['metrics']
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")
        return None


def fmt_row(label, m, width=28):
    if m is None:
        return f"  {label:<{width}}  {'NO DATA':>7}"
    return (
        f"  {label:<{width}}"
        f"  {m['cagr_pct']:>6.1f}%"
        f"  {m['sharpe']:>6.2f}"
        f"  {m['sortino']:>6.2f}"
        f"  {m['max_drawdown_pct']:>6.1f}%"
        f"  {m['calmar']:>5.2f}"
        f"  {m['total_trades']:>6d}"
        f"  {m['n_years']:>5.1f}yr"
    )


def print_table(rows, title):
    header = (
        f"\n  {'Config':<28}"
        f"  {'CAGR':>7}"
        f"  {'Sharpe':>6}"
        f"  {'Sortino':>7}"
        f"  {'MaxDD':>6}"
        f"  {'Calmar':>6}"
        f"  {'Trades':>6}"
        f"  {'Period':>7}"
    )
    sep = "  " + "-" * 82
    print(f"\n{'='*86}")
    print(f"  {title}")
    print(f"{'='*86}")
    print(header)
    print(sep)
    for label, m in rows:
        print(fmt_row(label, m))
    print(f"{'='*86}\n")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    symbols = [i['symbol'] for i in config['instruments']]

    print("\nLoading full data range (2012–2025)...")
    data = load_multiple(symbols, "2012-01-01", "2025-12-31")
    for s, df in data.items():
        print(f"  {s}: {len(df)} bars  {df.index[0].date()} → {df.index[-1].date()}")

    # ── Section 1: IS / OOS split ─────────────────────────────────────────
    print("\n" + "="*60)
    print("  SECTION 1: IN-SAMPLE vs OUT-OF-SAMPLE")
    print("  Train: 2012–2020   Test: 2021–2025")
    print("="*60)

    full   = run_period(config, data, "2012-01-01", "2025-12-31", "full")
    is_    = run_period(config, data, "2012-01-01", "2020-12-31", "in-sample")
    oos    = run_period(config, data, "2021-01-01", "2025-12-31", "out-of-sample")

    print_table([
        ("Full  2012–2025", full),
        ("IS    2012–2020", is_),
        ("OOS   2021–2025", oos),
    ], "IS vs OOS COMPARISON")

    # Degradation summary
    if is_ and oos:
        sharpe_deg = (oos['sharpe'] - is_['sharpe']) / abs(is_['sharpe']) * 100
        cagr_deg   = oos['cagr_pct'] - is_['cagr_pct']
        dd_change  = oos['max_drawdown_pct'] - is_['max_drawdown_pct']
        print(f"  OOS vs IS degradation:")
        print(f"    Sharpe:   {sharpe_deg:+.1f}%  ({'ROBUST' if abs(sharpe_deg) < 25 else 'DEGRADED'})")
        print(f"    CAGR:     {cagr_deg:+.1f}pp")
        print(f"    Max DD:   {dd_change:+.1f}pp  (negative = worse in OOS)\n")

    # ── Section 2: Rolling walk-forward ──────────────────────────────────
    print("="*60)
    print("  SECTION 2: ROLLING WALK-FORWARD")
    print("  5-year train window, 2-year test window, 1-year step")
    print("="*60)

    windows = [
        # (train_start, train_end, test_start, test_end)
        ("2012-01-01", "2016-12-31", "2017-01-01", "2018-12-31"),
        ("2013-01-01", "2017-12-31", "2018-01-01", "2019-12-31"),
        ("2014-01-01", "2018-12-31", "2019-01-01", "2020-12-31"),
        ("2015-01-01", "2019-12-31", "2020-01-01", "2021-12-31"),
        ("2016-01-01", "2020-12-31", "2021-01-01", "2022-12-31"),
        ("2017-01-01", "2021-12-31", "2022-01-01", "2023-12-31"),
        ("2018-01-01", "2022-12-31", "2023-01-01", "2024-12-31"),
    ]

    wf_rows = []
    for ts, te, vs, ve in windows:
        label_tr = f"Train {ts[:4]}–{te[:4]}"
        label_ts = f"Test  {vs[:4]}–{ve[:4]}"
        print(f"\n  {label_tr} → {label_ts}")
        m_tr = run_period(config, data, ts, te, label_tr)
        m_ts = run_period(config, data, vs, ve, label_ts)
        if m_tr:
            print(f"    Train: CAGR={m_tr['cagr_pct']:.1f}% Sharpe={m_tr['sharpe']:.2f} MaxDD={m_tr['max_drawdown_pct']:.1f}%")
        if m_ts:
            print(f"    Test:  CAGR={m_ts['cagr_pct']:.1f}% Sharpe={m_ts['sharpe']:.2f} MaxDD={m_ts['max_drawdown_pct']:.1f}%")
        wf_rows.append((f"Test {vs[:4]}–{ve[:4]}", m_ts))

    print_table(wf_rows, "ROLLING WALK-FORWARD: TEST WINDOWS")

    # Consistency stats across test windows
    test_metrics = [m for _, m in wf_rows if m is not None]
    if test_metrics:
        sharpes = [m['sharpe'] for m in test_metrics]
        cagrs   = [m['cagr_pct'] for m in test_metrics]
        dds     = [m['max_drawdown_pct'] for m in test_metrics]
        pos_windows = sum(1 for c in cagrs if c > 0)
        print(f"  Walk-forward consistency across {len(test_metrics)} test windows:")
        print(f"    Sharpe  — mean: {np.mean(sharpes):.2f}  std: {np.std(sharpes):.2f}  min: {np.min(sharpes):.2f}  max: {np.max(sharpes):.2f}")
        print(f"    CAGR    — mean: {np.mean(cagrs):.1f}%  std: {np.std(cagrs):.1f}pp  min: {np.min(cagrs):.1f}%  max: {np.max(cagrs):.1f}%")
        print(f"    Max DD  — mean: {np.mean(dds):.1f}%   worst: {np.min(dds):.1f}%")
        print(f"    Positive CAGR windows: {pos_windows}/{len(test_metrics)}")
        print()

    # Save results
    output_dir = Path("results/walkforward")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    all_sections = [
        ("full_2012_2025", full),
        ("is_2012_2020",   is_),
        ("oos_2021_2025",  oos),
    ] + [(f"wf_test_{vs[:4]}_{ve[:4]}", m) for (_, _, vs, ve), (_, m) in zip(windows, wf_rows)]

    for label, m in all_sections:
        if m:
            row = {'label': label}
            row.update(m)
            summary_rows.append(row)

    pd.DataFrame(summary_rows).to_csv(output_dir / "walkforward_results.csv", index=False)
    print(f"  Results saved to {output_dir}/walkforward_results.csv")


if __name__ == "__main__":
    main()
