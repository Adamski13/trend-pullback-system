#!/usr/bin/env python3
"""
Portfolio backtester entry point.

Runs TPS v1 on QQQ + GLD + BTC-USD simultaneously using a shared equity
pool. Position sizing for every layer uses total portfolio equity × 1% risk.

Usage:
    python run_portfolio.py
    python run_portfolio.py --commission 0.1 --slippage 0.05
    python run_portfolio.py --start 2018-01-01
"""

import argparse
import os

import pandas as pd
import yaml

from src.data_loader import load_data
from src.indicators import add_indicators
from src.strategy import generate_signals
from src.portfolio import run_portfolio
from src.metrics import calc_metrics, monthly_returns_table
from src.visualizer import (
    plot_drawdown,
    plot_monthly_heatmap,
    plot_portfolio_equity,
    plot_instrument_contributions,
    plot_correlation_matrix,
    print_summary,
)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results", "portfolio")
SYMBOLS = ["QQQ", "GLD", "BTC-USD"]


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="TPS Portfolio Backtester")
    parser.add_argument("--config", default="config/default_config.yaml")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--commission", type=float, default=None)
    parser.add_argument("--slippage", type=float, default=None)
    parser.add_argument("--outdir", default=RESULTS_DIR)
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

    os.makedirs(args.outdir, exist_ok=True)

    end = cfg["data"].get("end_date") or None
    start = cfg["data"]["start_date"]
    initial_capital = cfg["execution"]["initial_capital"]

    # ── Load + prepare signals for each instrument ────────────────────────────
    print(f"\nLoading data for {SYMBOLS}...")
    signals = {}
    raw_dfs = {}
    for sym in SYMBOLS:
        print(f"  [{sym}]", end=" ", flush=True)
        df = load_data(sym, start, end)
        print(f"{len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
        df = add_indicators(df, cfg)
        sig = generate_signals(df, cfg)
        signals[sym] = sig
        raw_dfs[sym] = df

    # ── Run portfolio simulation ───────────────────────────────────────────────
    print(f"\nRunning portfolio simulation...")
    result = run_portfolio(signals, cfg)

    portfolio_equity = result["portfolio_equity"]
    instrument_equity = result["instrument_equity"]
    instrument_pnl = result["instrument_pnl"]
    daily_returns = result["daily_returns"]
    trade_log = result["trade_log"]
    benchmark_equity = result["benchmark_equity"]

    # ── Per-instrument daily returns for correlation ──────────────────────────
    # Use the per-instrument equity contribution series
    instr_returns = instrument_pnl.pct_change().fillna(0.0)

    # ── Portfolio-level metrics ───────────────────────────────────────────────
    metrics = calc_metrics(
        equity_curve=portfolio_equity,
        daily_returns=daily_returns,
        trade_log=trade_log,
        initial_capital=initial_capital,
        bah_equity=benchmark_equity,
    )
    metrics["symbol"] = "PORTFOLIO"
    print_summary(metrics, "PORTFOLIO (QQQ + GLD + BTC-USD)")

    # ── Per-instrument contribution summary ───────────────────────────────────
    print("=" * 60)
    print("  PER-INSTRUMENT CONTRIBUTION")
    print("=" * 60)
    for sym in SYMBOLS:
        sym_trades = trade_log[trade_log["symbol"] == sym] if "symbol" in trade_log.columns else pd.DataFrame()
        total_pnl = sym_trades["pnl"].sum() if len(sym_trades) > 0 else 0.0
        n_trades = len(sym_trades)
        pct_of_total = total_pnl / (metrics["final_equity"] - initial_capital) * 100 if (metrics["final_equity"] - initial_capital) != 0 else 0.0
        print(f"  {sym:<10}  trades={n_trades:>3}   P&L=${total_pnl:>10,.0f}   ({pct_of_total:>+.1f}% of net)")
    print("=" * 60 + "\n")

    # ── Benchmark comparison ──────────────────────────────────────────────────
    bah_start = benchmark_equity.iloc[0]
    bah_end = benchmark_equity.iloc[-1]
    bah_return = (bah_end - bah_start) / bah_start * 100
    print(f"  Equal-Weight B&H Return: {bah_return:.2f}%  (vs Portfolio: {metrics['net_pnl_pct']:.2f}%)\n")

    # ── Save outputs ──────────────────────────────────────────────────────────
    trade_log.to_csv(os.path.join(args.outdir, "portfolio_trades.csv"), index=False)
    portfolio_equity.to_csv(os.path.join(args.outdir, "portfolio_equity.csv"))
    instrument_pnl.to_csv(os.path.join(args.outdir, "instrument_pnl.csv"))

    # Correlation matrix data
    corr = instr_returns.corr()
    corr.to_csv(os.path.join(args.outdir, "correlation_matrix.csv"))

    # Monthly returns
    monthly = monthly_returns_table(portfolio_equity)
    monthly.to_csv(os.path.join(args.outdir, "monthly_returns.csv"))

    # Metrics
    pd.Series(metrics).to_csv(os.path.join(args.outdir, "portfolio_metrics.csv"))

    # ── Plots ──────────────────────────────────────────────────────────────────
    plot_portfolio_equity(
        portfolio_equity, instrument_pnl, benchmark_equity,
        save_path=os.path.join(args.outdir, "portfolio_equity.png"),
    )
    plot_drawdown(
        portfolio_equity, symbol="Portfolio",
        save_path=os.path.join(args.outdir, "portfolio_drawdown.png"),
    )
    plot_monthly_heatmap(
        portfolio_equity, symbol="Portfolio",
        save_path=os.path.join(args.outdir, "portfolio_monthly.png"),
    )
    plot_instrument_contributions(
        instrument_pnl,
        save_path=os.path.join(args.outdir, "instrument_contributions.png"),
    )
    plot_correlation_matrix(
        instr_returns,
        save_path=os.path.join(args.outdir, "correlation_matrix.png"),
    )

    print(f"Results saved to {args.outdir}/")


if __name__ == "__main__":
    main()
