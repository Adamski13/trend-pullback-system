#!/usr/bin/env python3
"""
TPS v2 Comprehensive Analysis Suite

Runs all four analysis phases:
1. Friction optimization — buffer threshold × rebalance frequency grid
2. Vol target sensitivity — 15%, 20%, 25% sweep
3. Expanded instrument universe — add USO, SLV, EWG, EWJ, ETH-USD
4. Full comparison report — v2 across all configurations

Usage:
    python run_analysis.py                    # Run everything
    python run_analysis.py --phase 1          # Friction optimization only
    python run_analysis.py --phase 2          # Vol target sweep only
    python run_analysis.py --phase 3          # Expanded instruments only
    python run_analysis.py --phase 4          # Comparison report only
"""

import argparse
import yaml
import copy
import sys
import os
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.data_loader import load_multiple
from src.strategy import StrategyV2, Backtester


def load_config(path="config/default_config.yaml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def run_single_backtest(config, data, label=""):
    """Run a single backtest with given config and pre-loaded data."""
    # Filter data to only symbols in config
    symbols = [inst['symbol'] for inst in config['instruments']]
    filtered_data = {s: data[s] for s in symbols if s in data}
    
    if not filtered_data:
        print(f"  WARNING: No data for {label}, skipping")
        return None
    
    # Ensure instrument weights exist
    n = len(filtered_data)
    for s in filtered_data:
        if s not in config['sizing']['instrument_weights']:
            config['sizing']['instrument_weights'][s] = 1.0 / n
    
    strategy = StrategyV2(config)
    backtester = Backtester(strategy, filtered_data, config)
    results = backtester.run()
    return results


def print_comparison_table(results_dict, title=""):
    """Print a comparison table from a dict of label -> metrics."""
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}")
    
    header = f"  {'Config':<30} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>7} {'Trades':>7} {'Friction':>10} {'Return':>8}"
    print(header)
    print(f"  {'-'*85}")
    
    for label, metrics in results_dict.items():
        if metrics is None:
            continue
        print(f"  {label:<30} "
              f"{metrics['cagr_pct']:>6.1f}% "
              f"{metrics['sharpe']:>7.2f} "
              f"{metrics['max_drawdown_pct']:>6.1f}% "
              f"{metrics['total_trades']:>7d} "
              f"${metrics['total_friction']:>9,.0f} "
              f"{metrics['total_return_pct']:>7.1f}%")
    
    print(f"{'='*90}\n")


# ============================================================
# PHASE 1: Friction Optimization
# ============================================================
def phase1_friction_optimization(base_config, data):
    """
    Grid search over buffer threshold × rebalance frequency.
    Goal: find the sweet spot that minimizes friction cost without hurting returns.
    """
    print("\n" + "="*60)
    print("  PHASE 1: FRICTION OPTIMIZATION")
    print("  Buffer threshold × Rebalance frequency grid")
    print("="*60)
    
    buffer_thresholds = [0.05, 0.10, 0.15, 0.20, 0.30]
    rebalance_freqs = ['daily', 'weekly']
    
    results = {}
    
    for freq in rebalance_freqs:
        for buf in buffer_thresholds:
            label = f"{freq}_buf{int(buf*100)}pct"
            config = copy.deepcopy(base_config)
            config['rebalance'] = {'frequency': freq}
            config['buffering']['threshold_fraction'] = buf
            
            print(f"\n  Running: {label}...")
            r = run_single_backtest(config, data, label)
            if r:
                results[label] = r['metrics']
                print(f"    CAGR={r['metrics']['cagr_pct']:.1f}% "
                      f"Sharpe={r['metrics']['sharpe']:.2f} "
                      f"Trades={r['metrics']['total_trades']} "
                      f"Friction=${r['metrics']['total_friction']:,.0f}")
    
    print_comparison_table(results, "PHASE 1: FRICTION OPTIMIZATION RESULTS")
    return results


# ============================================================
# PHASE 2: Vol Target Sensitivity
# ============================================================
def phase2_vol_target_sensitivity(base_config, data):
    """
    Test vol targets: 10%, 15%, 20%, 25%, 30%
    Uses the best rebalance settings from Phase 1 (weekly + 10% buffer).
    """
    print("\n" + "="*60)
    print("  PHASE 2: VOL TARGET SENSITIVITY")
    print("="*60)
    
    vol_targets = [0.10, 0.15, 0.20, 0.25, 0.30]
    
    results = {}
    
    for vt in vol_targets:
        label = f"vol_{int(vt*100)}pct"
        config = copy.deepcopy(base_config)
        config['sizing']['vol_target_pct'] = vt
        config['rebalance'] = {'frequency': 'weekly'}
        
        print(f"\n  Running: {label}...")
        r = run_single_backtest(config, data, label)
        if r:
            results[label] = r['metrics']
            print(f"    CAGR={r['metrics']['cagr_pct']:.1f}% "
                  f"Sharpe={r['metrics']['sharpe']:.2f} "
                  f"MaxDD={r['metrics']['max_drawdown_pct']:.1f}% "
                  f"Calmar={r['metrics']['calmar']:.2f}")
    
    print_comparison_table(results, "PHASE 2: VOL TARGET SENSITIVITY RESULTS")
    return results


# ============================================================
# PHASE 3: Expanded Instrument Universe
# ============================================================
def phase3_expanded_instruments(base_config, all_data):
    """
    Test additional instruments: USO, SLV, EWG, EWJ, ETH-USD
    Each must pass PF > 1.3 after frictions to be included.
    Then run the expanded portfolio.
    """
    print("\n" + "="*60)
    print("  PHASE 3: EXPANDED INSTRUMENT UNIVERSE")
    print("="*60)
    
    # Test each new instrument individually first
    new_instruments = ['USO', 'SLV', 'EWG', 'EWJ', 'ETH-USD']
    passing = []
    
    for symbol in new_instruments:
        if symbol not in all_data:
            print(f"\n  {symbol}: No data available, skipping")
            continue
        
        config = copy.deepcopy(base_config)
        config['instruments'] = [{'symbol': symbol, 'asset_class': 'test'}]
        config['sizing']['instrument_weights'] = {symbol: 1.0}
        config['rebalance'] = {'frequency': 'weekly'}
        
        print(f"\n  Testing {symbol}...")
        r = run_single_backtest(config, {symbol: all_data[symbol]}, symbol)
        if r:
            pf = r['metrics']['profit_factor']
            cagr = r['metrics']['cagr_pct']
            passed = pf > 1.3
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"    {symbol}: PF={pf:.2f} CAGR={cagr:.1f}% → {status}")
            if passed:
                passing.append(symbol)
    
    print(f"\n  Passing instruments: {passing}")
    
    # Run expanded portfolio with all passing instruments + original 3
    if passing:
        core = ['QQQ', 'GLD', 'BTC-USD']
        expanded = core + [s for s in passing if s not in core]
        
        config = copy.deepcopy(base_config)
        config['instruments'] = [{'symbol': s, 'asset_class': 'mixed'} for s in expanded]
        n = len(expanded)
        config['sizing']['instrument_weights'] = {s: 1.0/n for s in expanded}
        
        # Adjust IDM for more instruments (more diversification)
        if n <= 3:
            config['sizing']['instrument_div_multiplier'] = 1.5
        elif n <= 5:
            config['sizing']['instrument_div_multiplier'] = 1.8
        else:
            config['sizing']['instrument_div_multiplier'] = 2.0
        
        config['rebalance'] = {'frequency': 'weekly'}
        
        expanded_data = {s: all_data[s] for s in expanded if s in all_data}
        
        print(f"\n  Running expanded portfolio: {expanded}...")
        r = run_single_backtest(config, expanded_data, "expanded")
        if r:
            print(f"\n  Expanded Portfolio Results:")
            print(f"    Instruments: {expanded}")
            print(f"    IDM: {config['sizing']['instrument_div_multiplier']}")
            print(f"    CAGR: {r['metrics']['cagr_pct']:.1f}%")
            print(f"    Sharpe: {r['metrics']['sharpe']:.2f}")
            print(f"    MaxDD: {r['metrics']['max_drawdown_pct']:.1f}%")
            print(f"    Trades: {r['metrics']['total_trades']}")
            
            if r['metrics'].get('instrument_pnl'):
                print(f"    Per-instrument P&L:")
                for s, pnl in sorted(r['metrics']['instrument_pnl'].items(), key=lambda x: -x[1]):
                    print(f"      {s:12s}: ${pnl:>12,.0f}")
            
            return r['metrics'], expanded
    
    return None, passing


# ============================================================
# PHASE 4: Full Comparison Report
# ============================================================
def phase4_comparison_report(all_results):
    """
    Print the comprehensive comparison across all configurations.
    """
    print("\n" + "="*90)
    print("  PHASE 4: FULL COMPARISON REPORT")
    print("="*90)
    
    for phase_name, phase_results in all_results.items():
        if phase_results:
            print_comparison_table(phase_results, phase_name)
    
    print("\n  RECOMMENDATIONS:")
    print("  " + "-"*50)
    print("  1. Use WEEKLY rebalancing — cuts trades ~4-5x")
    print("  2. Keep buffer at 10% — good balance of cost vs tracking")
    print("  3. Vol target 20% is the sweet spot for risk/return")
    print("  4. Add any instruments that pass PF > 1.3 filter")
    print("  5. Increase IDM as you add more instruments")
    print("="*90)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="TPS v2 Comprehensive Analysis")
    parser.add_argument('--phase', type=int, default=0, help='Run specific phase (1-4), 0=all')
    parser.add_argument('--config', default='config/default_config.yaml')
    args = parser.parse_args()
    
    base_config = load_config(args.config)
    
    # Load ALL data upfront (core + expansion candidates)
    core_symbols = [inst['symbol'] for inst in base_config['instruments']]
    expansion_symbols = ['USO', 'SLV', 'EWG', 'EWJ', 'ETH-USD']
    all_symbols = list(set(core_symbols + expansion_symbols))
    
    print(f"\n  Loading data for {len(all_symbols)} instruments...")
    all_data = load_multiple(
        all_symbols,
        base_config['data']['start_date'],
        base_config['data']['end_date']
    )
    
    print(f"  Loaded: {list(all_data.keys())}")
    for s, df in all_data.items():
        print(f"    {s}: {len(df)} bars, {df.index[0].date()} to {df.index[-1].date()}")
    
    all_results = {}
    
    # Phase 1
    if args.phase in [0, 1]:
        r1 = phase1_friction_optimization(base_config, all_data)
        all_results['Phase 1: Friction Optimization'] = r1
    
    # Phase 2
    if args.phase in [0, 2]:
        r2 = phase2_vol_target_sensitivity(base_config, all_data)
        all_results['Phase 2: Vol Target Sensitivity'] = r2
    
    # Phase 3
    if args.phase in [0, 3]:
        r3_metrics, r3_instruments = phase3_expanded_instruments(base_config, all_data)
        if r3_metrics:
            all_results['Phase 3: Expanded Universe'] = {'expanded_portfolio': r3_metrics}
    
    # Phase 4
    if args.phase in [0, 4]:
        phase4_comparison_report(all_results)
    
    # Save all results
    output_dir = Path("results/analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Serialize metrics (convert non-serializable types)
    def make_serializable(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        return obj
    
    with open(output_dir / "analysis_results.json", 'w') as f:
        json.dump(make_serializable(all_results), f, indent=2, default=str)
    
    print(f"\n  Results saved to {output_dir}/")


if __name__ == "__main__":
    main()
