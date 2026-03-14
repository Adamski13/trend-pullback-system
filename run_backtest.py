#!/usr/bin/env python3
"""
Main entry point for the Trend Pullback System backtester.

Usage:
    python run_backtest.py                          # run all symbols in config
    python run_backtest.py --symbol QQQ             # single symbol
    python run_backtest.py --symbol QQQ --start 2018-01-01
    python run_backtest.py --config config/my_config.yaml
    python run_backtest.py --sensitivity             # run phase 3 sensitivity sweep
"""

import argparse
import itertools
import os
import sys
import copy
import yaml
import pandas as pd

from src.data_loader import load_data
from src.indicators import add_indicators
from src.strategy import generate_signals
from src.backtester import run_backtest
from src.metrics import calc_metrics
from src.visualizer import plot_equity_curve, plot_drawdown, plot_monthly_heatmap, print_summary

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def bah_equity(df: pd.DataFrame, initial_capital: float) -> pd.Series:
    """Buy-and-hold equity curve starting from first bar."""
    ratio = df["Close"] / df["Close"].iloc[0]
    return ratio * initial_capital


def run_single(symbol: str, cfg: dict, out_dir: str) -> dict:
    print(f"\n[{symbol}] Downloading data...")
    end = cfg["data"].get("end_date") or None
    df = load_data(symbol, cfg["data"]["start_date"], end)

    print(f"[{symbol}] {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")

    df = add_indicators(df, cfg)
    sig = generate_signals(df, cfg)

    print(f"[{symbol}] Running backtest...")
    result = run_backtest(sig, cfg)

    equity = result["equity_curve"]
    trades = result["trade_log"]
    daily_ret = result["daily_returns"]
    bah = bah_equity(df, cfg["execution"]["initial_capital"])

    metrics = calc_metrics(
        equity_curve=equity,
        daily_returns=daily_ret,
        trade_log=trades,
        initial_capital=cfg["execution"]["initial_capital"],
        bah_equity=bah,
    )
    metrics["symbol"] = symbol

    print_summary(metrics, symbol)

    # Save outputs
    os.makedirs(out_dir, exist_ok=True)
    trades.to_csv(os.path.join(out_dir, f"{symbol}_trades.csv"), index=False)
    equity.to_csv(os.path.join(out_dir, f"{symbol}_equity.csv"))

    plot_equity_curve(
        equity, bah, symbol=symbol,
        save_path=os.path.join(out_dir, f"{symbol}_equity.png"),
    )
    plot_drawdown(
        equity, symbol=symbol,
        save_path=os.path.join(out_dir, f"{symbol}_drawdown.png"),
    )
    plot_monthly_heatmap(
        equity, symbol=symbol,
        save_path=os.path.join(out_dir, f"{symbol}_monthly.png"),
    )

    return metrics


def run_sensitivity(base_cfg: dict, symbol: str = "QQQ") -> pd.DataFrame:
    """Phase 3: sweep key parameters and collect metrics."""
    param_grid = {
        "regime_ma_length": [150, 200, 250],
        "ema_length": [15, 21, 30],
        "trail_atr_mult": [2.0, 2.5, 3.0],
        "risk_per_layer_pct": [0.5, 1.0, 1.5],
    }

    results = []
    keys = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))
    total = len(combos)
    print(f"\nSensitivity sweep: {total} combinations on {symbol}")

    end = base_cfg["data"].get("end_date") or None
    raw_df = load_data(symbol, base_cfg["data"]["start_date"], end)

    for idx, combo in enumerate(combos, 1):
        cfg = copy.deepcopy(base_cfg)
        row = {}
        for k, v in zip(keys, combo):
            cfg["strategy"][k] = v
            row[k] = v

        df = add_indicators(raw_df.copy(), cfg)
        sig = generate_signals(df, cfg)
        result = run_backtest(sig, cfg)
        bah = bah_equity(raw_df, cfg["execution"]["initial_capital"])
        m = calc_metrics(
            result["equity_curve"], result["daily_returns"], result["trade_log"],
            cfg["execution"]["initial_capital"], bah,
        )
        row.update({
            "net_pnl_pct": m["net_pnl_pct"],
            "cagr_pct": m["cagr_pct"],
            "max_dd_pct": m["max_drawdown_pct"],
            "sharpe": m["sharpe"],
            "total_trades": m["total_trades"],
            "profit_factor": m["profit_factor"],
        })
        results.append(row)
        if idx % 10 == 0 or idx == total:
            print(f"  {idx}/{total} done")

    df_results = pd.DataFrame(results)
    out_dir = os.path.join(RESULTS_DIR, "sensitivity")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{symbol}_sensitivity.csv")
    df_results.to_csv(out_path, index=False)
    print(f"\nSensitivity results saved to {out_path}")

    profitable_count = (df_results["net_pnl_pct"] > 0).sum()
    print(f"Profitable combos: {profitable_count}/{total} ({profitable_count/total*100:.1f}%)")
    return df_results


def main():
    parser = argparse.ArgumentParser(description="Trend Pullback System Backtester")
    parser.add_argument("--config", default="config/default_config.yaml")
    parser.add_argument("--symbol", default=None, help="Override symbol(s), comma-separated")
    parser.add_argument("--start", default=None, help="Override start_date")
    parser.add_argument("--end", default=None, help="Override end_date")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--commission", type=float, default=None, help="Commission pct (e.g. 0.1)")
    parser.add_argument("--slippage", type=float, default=None, help="Slippage pct (e.g. 0.05)")
    parser.add_argument("--sensitivity", action="store_true", help="Run Phase 3 sensitivity sweep")
    parser.add_argument("--outdir", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.start:
        cfg["data"]["start_date"] = args.start
    if args.end:
        cfg["data"]["end_date"] = args.end
    if args.capital:
        cfg["execution"]["initial_capital"] = args.capital
    if args.commission is not None:
        cfg["execution"]["commission_pct"] = args.commission
    if args.slippage is not None:
        cfg["execution"]["slippage_pct"] = args.slippage

    symbols = (
        [s.strip() for s in args.symbol.split(",")]
        if args.symbol
        else cfg["data"]["symbols"]
    )

    if args.sensitivity:
        run_sensitivity(cfg, symbol=symbols[0])
        return

    summary_rows = []
    for sym in symbols:
        run_dir = args.outdir or os.path.join(RESULTS_DIR, sym)
        try:
            m = run_single(sym, cfg, run_dir)
            summary_rows.append(m)
        except Exception as e:
            print(f"[{sym}] ERROR: {e}", file=sys.stderr)

    if len(summary_rows) > 1:
        summary = pd.DataFrame(summary_rows).set_index("symbol")
        cols = ["net_pnl_pct", "cagr_pct", "max_drawdown_pct", "sharpe",
                "total_trades", "win_rate_pct", "profit_factor"]
        cols = [c for c in cols if c in summary.columns]
        print("\n" + "="*60)
        print("  CROSS-MARKET SUMMARY")
        print("="*60)
        print(summary[cols].to_string())
        print("="*60)
        out_dir = args.outdir or RESULTS_DIR
        os.makedirs(out_dir, exist_ok=True)
        summary.to_csv(os.path.join(out_dir, "summary.csv"))


if __name__ == "__main__":
    main()
