#!/usr/bin/env python3
"""
TPS v2 Backtest Runner

Usage:
    python run_backtest.py                           # Defaults (weekly rebalance)
    python run_backtest.py --no-frictions             # No frictions
    python run_backtest.py --vol-target 0.25          # Custom vol target
    python run_backtest.py --symbols QQQ GLD          # Subset
    python run_backtest.py --rebalance daily           # Daily rebalance
    python run_backtest.py --buffer 0.20              # 20% buffer threshold
"""

import argparse
import yaml
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.data_loader import load_multiple
from src.strategy import StrategyV2, Backtester


def load_config(path="config/default_config.yaml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def print_metrics(metrics, title="BACKTEST RESULTS"):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    print(f"  Period:              {metrics['n_years']:.1f} years")
    print(f"  Rebalance:           {metrics.get('rebalance_frequency', 'daily')}")
    print(f"  Start Equity:        ${metrics['start_equity']:,.0f}")
    print(f"  End Equity:          ${metrics['end_equity']:,.0f}")
    print(f"  Net P&L:             ${metrics['net_pnl']:,.0f}")
    print(f"  Total Return:        {metrics['total_return_pct']:.1f}%")
    print(f"  CAGR:                {metrics['cagr_pct']:.2f}%")
    print(f"  Sharpe Ratio:        {metrics['sharpe']:.2f}")
    print(f"  Sortino Ratio:       {metrics['sortino']:.2f}")
    print(f"  Max Drawdown:        {metrics['max_drawdown_pct']:.1f}%")
    print(f"  Calmar Ratio:        {metrics['calmar']:.2f}")
    print(f"  Profit Factor:       {metrics['profit_factor']:.2f}")
    print(f"  Annual Volatility:   {metrics['annual_volatility_pct']:.1f}%")
    print(f"  Return/DD Ratio:     {metrics['return_dd_ratio']:.2f}")
    print(f"  Total Trades:        {metrics['total_trades']}")
    print(f"  Total Friction Cost: ${metrics['total_friction']:,.0f}")
    
    if metrics.get('instrument_pnl'):
        print(f"\n  Per-Instrument P&L:")
        for symbol, pnl in sorted(metrics['instrument_pnl'].items(), key=lambda x: -x[1]):
            pct = (pnl / metrics['start_equity']) * 100
            print(f"    {symbol:12s}: ${pnl:>12,.0f}  ({pct:>7.1f}%)")
    
    print(f"{'='*60}\n")


def print_yearly_breakdown(equity_df):
    equity = equity_df['equity']
    equity.index = pd.to_datetime(equity.index)
    yearly = equity.resample('YE').last()
    
    print(f"\n{'='*40}")
    print(f"  YEAR-BY-YEAR RETURNS")
    print(f"{'='*40}")
    
    prev = None
    for date, eq in yearly.items():
        if prev is not None:
            ret = (eq / prev - 1) * 100
            print(f"  {date.year}:  {ret:>8.1f}%   (${eq:>12,.0f})")
        prev = eq
    print(f"{'='*40}\n")


def main():
    parser = argparse.ArgumentParser(description="TPS v2 Backtester")
    parser.add_argument('--config', default='config/default_config.yaml')
    parser.add_argument('--no-frictions', action='store_true')
    parser.add_argument('--vol-target', type=float, default=None)
    parser.add_argument('--symbols', nargs='+', default=None)
    parser.add_argument('--start', default=None)
    parser.add_argument('--end', default=None)
    parser.add_argument('--rebalance', choices=['daily', 'weekly', 'monthly'], default=None)
    parser.add_argument('--buffer', type=float, default=None, help='Buffer threshold (0.05-0.30)')
    args = parser.parse_args()
    
    config = load_config(args.config)
    
    if args.no_frictions:
        config['frictions'] = {'commission_pct': 0, 'slippage_pct': 0}
    if args.vol_target:
        config['sizing']['vol_target_pct'] = args.vol_target
    if args.start:
        config['data']['start_date'] = args.start
    if args.end:
        config['data']['end_date'] = args.end
    if args.rebalance:
        config['rebalance'] = {'frequency': args.rebalance}
    if args.buffer:
        config['buffering']['threshold_fraction'] = args.buffer
    
    if args.symbols:
        symbols = args.symbols
        config['instruments'] = [{'symbol': s, 'asset_class': 'custom'} for s in symbols]
    else:
        symbols = [inst['symbol'] for inst in config['instruments']]
    
    n = len(symbols)
    for s in symbols:
        if s not in config['sizing']['instrument_weights']:
            config['sizing']['instrument_weights'][s] = 1.0 / n
    
    rebal = config.get('rebalance', {}).get('frequency', 'daily')
    buf = config['buffering']['threshold_fraction']
    
    print(f"\n{'='*60}")
    print(f"  TPS v2.1 BACKTEST")
    print(f"  Instruments: {', '.join(symbols)}")
    print(f"  Period: {config['data']['start_date']} to {config['data']['end_date']}")
    print(f"  Vol Target: {config['sizing']['vol_target_pct']*100:.0f}%")
    print(f"  Rebalance: {rebal}")
    print(f"  Buffer: {buf*100:.0f}%")
    print(f"  Frictions: {config['frictions']['commission_pct']*100:.2f}% + {config['frictions']['slippage_pct']*100:.2f}%")
    print(f"  EWMAC: {config['ewmac']['variations']}")
    print(f"{'='*60}\n")
    
    print("Loading data...")
    data = load_multiple(symbols, config['data']['start_date'], config['data']['end_date'])
    
    if not data:
        print("ERROR: No data loaded.")
        return
    
    for symbol, df in data.items():
        print(f"  {symbol}: {len(df)} bars, {df.index[0].date()} to {df.index[-1].date()}")
    
    print("\nInitializing strategy...")
    strategy = StrategyV2(config)
    
    print("Running backtest...")
    backtester = Backtester(strategy, data, config)
    results = backtester.run()
    
    print_metrics(results['metrics'])
    print_yearly_breakdown(results['equity_curve'])
    
    # Save
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)
    results['equity_curve'].to_csv(output_dir / "equity_curve.csv")
    if not results['trades'].empty:
        results['trades'].to_csv(output_dir / "trades.csv", index=False)
    if not results['positions'].empty:
        results['positions'].to_csv(output_dir / "positions.csv")
    if not results['daily_pnl'].empty:
        results['daily_pnl'].to_csv(output_dir / "daily_pnl.csv")
    for symbol, fc_df in results['forecasts'].items():
        fc_df.to_csv(output_dir / f"forecasts_{symbol}.csv")
    
    print(f"Results saved to {output_dir}/")
    return results


if __name__ == "__main__":
    main()
