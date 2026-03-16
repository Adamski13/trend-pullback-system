#!/usr/bin/env python3
"""
Portfolio v2 backtester entry point.

Extends run_portfolio.py with support for an expanded instrument universe.
Phase 5 expansion results (PF > 1.3 filter):
  USO:     PF=0.65  → FAIL
  SLV:     PF=0.76  → FAIL
  EWG:     PF=1.01  → FAIL
  EWJ:     PF=0.87  → FAIL
  ETH-USD: PF=1.24  → FAIL (closest, positive P&L but below 1.3 threshold)

No new instruments passed the PF > 1.3 filter.
SYMBOLS_V2 therefore equals the original portfolio (QQQ + GLD + BTC-USD).
The script accepts --symbols to override at runtime.

Usage:
    python run_portfolio_v2.py
    python run_portfolio_v2.py --commission 0.1 --slippage 0.05
    python run_portfolio_v2.py --start 2018-01-01
    python run_portfolio_v2.py --symbols QQQ,GLD,BTC-USD,ETH-USD
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

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results", "portfolio_v2")

# Phase 5 filter: original 3 + any new instruments with PF > 1.3
# None of the 5 new instruments passed the filter, so v2 = v1 symbols.
SYMBOLS_V2 = ["QQQ", "GLD", "BTC-USD"]


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="TPS Portfolio v2 Backtester")
    parser.add_argument("--config", default="config/default_config.yaml")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--commission", type=float, default=None)
    parser.add_argument("--slippage", type=float, default=None)
    parser.add_argument("--outdir", default=RESULTS_DIR)
    parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol list to override the default (e.g. QQQ,GLD,BTC-USD,ETH-USD)"
    )
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
        [s.strip() for s in args.symbols.split(",")]
        if args.symbols
        else SYMBOLS_V2
    )

    os.makedirs(args.outdir, exist_ok=True)

    end = cfg["data"].get("end_date") or None
    start = cfg["data"]["start_date"]
    initial_capital = cfg["execution"]["initial_capital"]

    # ── Load + prepare signals for each instrument ────────────────────────────
    print(f"\nLoading data for {symbols}...")
    signals = {}
    raw_dfs = {}
    for sym in symbols:
        print(f"  [{sym}]", end=" ", flush=True)
        df = load_data(sym, start, end)
        print(f"{len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
        df = add_indicators(df, cfg)
        sig = generate_signals(df, cfg)
        signals[sym] = sig
        raw_dfs[sym] = df

    # ── Run portfolio simulation ───────────────────────────────────────────────
    print(f"\nRunning portfolio v2 simulation ({len(symbols)} instruments)...")
    result = run_portfolio(signals, cfg)

    portfolio_equity = result["portfolio_equity"]
    instrument_equity = result["instrument_equity"]
    instrument_pnl = result["instrument_pnl"]
    daily_returns = result["daily_returns"]
    trade_log = result["trade_log"]
    benchmark_equity = result["benchmark_equity"]

    # ── Per-instrument daily returns for correlation ──────────────────────────
    instr_returns = instrument_pnl.pct_change().fillna(0.0)

    # ── Portfolio-level metrics ───────────────────────────────────────────────
    metrics = calc_metrics(
        equity_curve=portfolio_equity,
        daily_returns=daily_returns,
        trade_log=trade_log,
        initial_capital=initial_capital,
        bah_equity=benchmark_equity,
    )
    metrics["symbol"] = "PORTFOLIO_V2"
    label = "PORTFOLIO v2 (" + " + ".join(symbols) + ")"
    print_summary(metrics, label)

    # ── Per-instrument contribution summary ───────────────────────────────────
    print("=" * 60)
    print("  PER-INSTRUMENT CONTRIBUTION")
    print("=" * 60)
    for sym in symbols:
        sym_trades = trade_log[trade_log["symbol"] == sym] if "symbol" in trade_log.columns else pd.DataFrame()
        total_pnl = sym_trades["pnl"].sum() if len(sym_trades) > 0 else 0.0
        n_trades = len(sym_trades)
        net = metrics["final_equity"] - initial_capital
        pct_of_total = total_pnl / net * 100 if net != 0 else 0.0
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

    # ── Yearly breakdown ───────────────────────────────────────────────────────
    yearly_rows = []
    for year in portfolio_equity.index.year.unique():
        mask = portfolio_equity.index.year == year
        ye = portfolio_equity[mask]
        if len(ye) < 2:
            continue
        yr_start = ye.iloc[0]
        yr_end = ye.iloc[-1]
        yr_ret = (yr_end - yr_start) / yr_start * 100

        # Max drawdown within year
        roll_max = ye.cummax()
        dd_pct = ((ye - roll_max) / roll_max).min() * 100

        # Trades in year
        if len(trade_log) > 0 and "exit_date" in trade_log.columns:
            yr_trades = trade_log[pd.to_datetime(trade_log["exit_date"]).dt.year == year]
        else:
            yr_trades = pd.DataFrame()

        n_tr = len(yr_trades)
        row = {"year": year, "return_%": round(yr_ret, 2), "max_dd_%": round(dd_pct, 2), "n_trades": n_tr}

        # Per-symbol PnL contribution
        for sym in symbols:
            if len(yr_trades) > 0 and "symbol" in yr_trades.columns:
                sym_yr = yr_trades[yr_trades["symbol"] == sym]
                row[f"{sym}_pnl"] = round(sym_yr["pnl"].sum(), 0)
            else:
                row[f"{sym}_pnl"] = 0.0

        row["total_pnl"] = round(yr_trades["pnl"].sum() if len(yr_trades) > 0 else 0.0, 0)
        yearly_rows.append(row)

    yearly_df = pd.DataFrame(yearly_rows)
    yearly_df.to_csv(os.path.join(args.outdir, "yearly_breakdown.csv"), index=False)

    # ── Top trades ────────────────────────────────────────────────────────────
    if len(trade_log) > 0:
        top = trade_log.nlargest(20, "pnl")[
            ["symbol", "entry_date", "exit_date", "direction", "pnl", "r_multiple", "exit_reason", "layer"]
        ].copy()
        top["holding_days"] = (
            pd.to_datetime(top["exit_date"]) - pd.to_datetime(top["entry_date"])
        ).dt.days
        top = top[["symbol", "entry_date", "exit_date", "holding_days", "pnl", "r_multiple", "direction", "exit_reason", "layer"]]
        top["pnl"] = top["pnl"].round(0)
        top["r_multiple"] = top["r_multiple"].round(2)
        top.to_csv(os.path.join(args.outdir, "top_trades.csv"))
    else:
        pd.DataFrame().to_csv(os.path.join(args.outdir, "top_trades.csv"))

    # ── Plots ──────────────────────────────────────────────────────────────────
    plot_portfolio_equity(
        portfolio_equity, instrument_pnl, benchmark_equity,
        save_path=os.path.join(args.outdir, "portfolio_equity.png"),
    )
    plot_drawdown(
        portfolio_equity, symbol="Portfolio v2",
        save_path=os.path.join(args.outdir, "portfolio_drawdown.png"),
    )
    plot_monthly_heatmap(
        portfolio_equity, symbol="Portfolio v2",
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
