"""
Plotting: equity curve, drawdown, monthly returns heatmap.
"""

from __future__ import annotations

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

from .metrics import monthly_returns_table


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def plot_equity_curve(
    equity: pd.Series,
    bah_equity: pd.Series | None = None,
    symbol: str = "",
    save_path: str | None = None,
) -> None:
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(equity.index, equity.values, label="Strategy", color="steelblue", linewidth=1.5)
    if bah_equity is not None:
        # Normalise B&H to same starting equity
        bah_norm = bah_equity / bah_equity.iloc[0] * equity.iloc[0]
        ax.plot(bah_norm.index, bah_norm.values, label="Buy & Hold", color="grey",
                linewidth=1.0, linestyle="--", alpha=0.7)
    ax.set_title(f"Equity Curve — {symbol}")
    ax.set_ylabel("Portfolio Value ($)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    if save_path:
        _ensure_dir(os.path.dirname(save_path))
        fig.savefig(save_path, dpi=150)
    plt.close(fig)


def plot_drawdown(
    equity: pd.Series,
    symbol: str = "",
    save_path: str | None = None,
) -> None:
    roll_max = equity.cummax()
    dd_pct = (equity - roll_max) / roll_max * 100

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(dd_pct.index, dd_pct.values, 0, color="crimson", alpha=0.4)
    ax.plot(dd_pct.index, dd_pct.values, color="crimson", linewidth=0.8)
    ax.set_title(f"Drawdown — {symbol}")
    ax.set_ylabel("Drawdown (%)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax.grid(alpha=0.3)
    plt.tight_layout()
    if save_path:
        _ensure_dir(os.path.dirname(save_path))
        fig.savefig(save_path, dpi=150)
    plt.close(fig)


def plot_monthly_heatmap(
    equity: pd.Series,
    symbol: str = "",
    save_path: str | None = None,
) -> None:
    table = monthly_returns_table(equity)
    if table.empty:
        return

    fig, ax = plt.subplots(figsize=(14, max(4, len(table) * 0.5)))
    sns.heatmap(
        table,
        annot=True,
        fmt=".1f",
        center=0,
        cmap="RdYlGn",
        linewidths=0.5,
        linecolor="white",
        ax=ax,
        cbar_kws={"label": "Return (%)"},
    )
    ax.set_title(f"Monthly Returns (%) — {symbol}")
    ax.set_xlabel("")
    ax.set_ylabel("Year")
    plt.tight_layout()
    if save_path:
        _ensure_dir(os.path.dirname(save_path))
        fig.savefig(save_path, dpi=150)
    plt.close(fig)


def print_summary(metrics: dict, symbol: str = "") -> None:
    """Pretty-print the metrics table to stdout."""
    print(f"\n{'='*60}")
    print(f"  BACKTEST SUMMARY — {symbol}")
    print(f"{'='*60}")
    rows = [
        ("Initial Capital",       f"${metrics['initial_capital']:,.0f}"),
        ("Final Equity",          f"${metrics['final_equity']:,.0f}"),
        ("Net P&L ($)",           f"${metrics['net_pnl_dollars']:,.0f}"),
        ("Net P&L (%)",           f"{metrics['net_pnl_pct']:.2f}%"),
        ("CAGR",                  f"{metrics['cagr_pct']:.2f}%"),
        ("Max Drawdown ($)",      f"${metrics['max_drawdown_dollars']:,.0f}"),
        ("Max Drawdown (%)",      f"{metrics['max_drawdown_pct']:.2f}%"),
        ("Return / Max DD",       f"{metrics['return_to_max_dd']:.2f}"),
        ("Sharpe Ratio",          f"{metrics['sharpe']:.2f}"),
        ("Sortino Ratio",         f"{metrics['sortino']:.2f}"),
        ("Total Trades",          str(metrics['total_trades'])),
        ("Win Rate",              f"{metrics['win_rate_pct']:.1f}%"),
        ("Profit Factor",         f"{metrics['profit_factor']:.2f}"),
        ("Avg Win ($)",           f"${metrics['avg_win_dollars']:,.0f}"),
        ("Avg Loss ($)",          f"${metrics['avg_loss_dollars']:,.0f}"),
        ("Avg Trade ($)",         f"${metrics['avg_trade_dollars']:,.0f}"),
        ("Largest Win",           f"${metrics['largest_win']:,.0f}"),
        ("Largest Loss",          f"${metrics['largest_loss']:,.0f}"),
        ("Max Consec. Wins",      str(int(metrics['max_consecutive_wins']))),
        ("Max Consec. Losses",    str(int(metrics['max_consecutive_losses']))),
        ("Avg Holding (days)",    f"{metrics['avg_holding_days']:.1f}"),
        ("Trades / Year",         f"{metrics['trades_per_year']:.1f}"),
        ("Time in Market",        f"{metrics['time_in_market_pct']:.1f}%"),
    ]
    if metrics.get("bah_return_pct") is not None:
        rows += [
            ("B&H Return",        f"{metrics['bah_return_pct']:.2f}%"),
            ("B&H Max Drawdown",  f"{metrics['bah_max_drawdown_pct']:.2f}%"),
        ]
    for label, val in rows:
        print(f"  {label:<26} {val:>12}")
    print(f"{'='*60}\n")
