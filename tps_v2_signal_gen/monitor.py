#!/usr/bin/env python3
"""
TPS v2 — Account Monitor

Prints a live snapshot of your OANDA account: current positions, open P&L,
trade history, and daily equity log.

Run anytime — does not place orders.

Usage:
    python monitor.py              # full snapshot
    python monitor.py --trades 20  # show last 20 closed trades
    python monitor.py --log        # append today's equity to equity_log.csv
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from oanda_client import OandaClient

ROOT     = Path(__file__).parent
LOG_FILE = ROOT / "equity_log.csv"


def get_client() -> OandaClient:
    token      = os.environ.get("OANDA_TOKEN")
    account_id = os.environ.get("OANDA_ACCOUNT")
    env        = os.environ.get("OANDA_ENV", "practice").lower()
    if not token or not account_id:
        print("ERROR: set OANDA_TOKEN and OANDA_ACCOUNT environment variables.")
        sys.exit(1)
    return OandaClient(token, account_id, env), account_id, env


def get_trades(client: OandaClient, n: int = 50) -> list:
    """Fetch last n closed trades from OANDA."""
    data = client._get(
        f"/v3/accounts/{client.account_id}/trades",
        params={"state": "CLOSED", "count": n}
    )
    return data.get("trades", [])


def get_open_trades(client: OandaClient) -> list:
    data = client._get(f"/v3/accounts/{client.account_id}/trades",
                       params={"state": "OPEN"})
    return data.get("trades", [])


def get_transactions(client: OandaClient, n: int = 50) -> list:
    """Fetch recent account transactions (orders, fills, etc.)."""
    data = client._get(
        f"/v3/accounts/{client.account_id}/transactions",
        params={"count": n}
    )
    return data.get("transactions", [])


def log_equity(account: dict, account_id: str):
    """Append today's NAV + P&L to equity_log.csv."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_exists = LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "nav", "balance", "unrealized_pl",
                             "margin_used", "account"])
        writer.writerow([
            today,
            round(account["nav"], 2),
            round(account["balance"], 2),
            round(account["unrealized_pl"], 2),
            round(account["margin_used"], 2),
            account_id,
        ])
    print(f"  Logged to {LOG_FILE}")


def print_equity_history():
    """Print the equity log as a table."""
    if not LOG_FILE.exists():
        print("  No equity log yet. Run with --log to start tracking.")
        return
    df = pd.read_csv(LOG_FILE, parse_dates=["date"])
    df = df.sort_values("date")

    # Add daily change column
    df["daily_chg"] = df["nav"].diff()
    df["daily_pct"] = (df["nav"].pct_change() * 100).round(2)

    print(f"\n  {'Date':<12} {'NAV':>12} {'Daily $':>10} {'Daily %':>8} {'Unreal P&L':>12}")
    print(f"  {'─'*56}")
    for _, row in df.tail(30).iterrows():
        chg_str = f"{row['daily_chg']:>+.2f}" if pd.notna(row["daily_chg"]) else "       —"
        pct_str = f"{row['daily_pct']:>+.2f}%" if pd.notna(row["daily_pct"]) else "      —"
        print(f"  {str(row['date'].date()):<12} ${row['nav']:>11,.2f} "
              f"{chg_str:>10} {pct_str:>8} ${row['unrealized_pl']:>+11,.2f}")


def main():
    parser = argparse.ArgumentParser(description="TPS v2 account monitor")
    parser.add_argument("--trades", type=int, default=10,
                        help="Number of recent closed trades to show (default: 10)")
    parser.add_argument("--log", action="store_true",
                        help="Append today's equity to equity_log.csv")
    parser.add_argument("--history", action="store_true",
                        help="Print equity log history")
    args = parser.parse_args()

    client, account_id, env = get_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")

    print()
    print("=" * 64)
    print(f"  TPS v2 — Account Monitor")
    print(f"  {now}   [{env.upper()}]   {account_id}")
    print("=" * 64)

    # ── account summary ────────────────────────────────────────────────────
    print("\n  ACCOUNT")
    print(f"  {'─'*60}")
    account = client.get_account()
    pl_sign = "+" if account["unrealized_pl"] >= 0 else ""
    print(f"  NAV:             ${account['nav']:>12,.2f}")
    print(f"  Cash balance:    ${account['balance']:>12,.2f}")
    print(f"  Unrealized P&L:  {pl_sign}${account['unrealized_pl']:>11,.2f}")
    print(f"  Margin used:     ${account['margin_used']:>12,.2f}")

    # ── log equity ─────────────────────────────────────────────────────────
    if args.log:
        print()
        print("  Logging equity...")
        log_equity(account, account_id)

    # ── open positions ──────────────────────────────────────────────────────
    print("\n  OPEN POSITIONS")
    print(f"  {'─'*60}")
    open_trades = get_open_trades(client)
    if not open_trades:
        print("  — No open positions")
    else:
        print(f"  {'Instrument':<16} {'Units':>8} {'Open Price':>12} "
              f"{'Current':>12} {'Unreal P&L':>12} {'Open Date':<12}")
        print(f"  {'─'*78}")
        for t in open_trades:
            instr     = t["instrument"]
            units     = float(t["currentUnits"])
            open_px   = float(t["price"])
            unreal    = float(t["unrealizedPL"])
            open_date = t["openTime"][:10]
            # Current price from trade data if available
            cur_px_str = "—"
            if "takeProfitOrder" in t:
                cur_px_str = "—"
            pl_sign = "+" if unreal >= 0 else ""
            print(f"  {instr:<16} {units:>8,.1f} ${open_px:>11,.2f} "
                  f"{'':>12} {pl_sign}${unreal:>10,.2f}  {open_date}")

    # ── closed trades ───────────────────────────────────────────────────────
    print(f"\n  LAST {args.trades} CLOSED TRADES")
    print(f"  {'─'*60}")
    closed = get_trades(client, n=args.trades)
    if not closed:
        print("  — No closed trades")
    else:
        total_realised = 0.0
        print(f"  {'Instrument':<16} {'Units':>8} {'Open':>10} {'Close':>10} "
              f"{'P&L':>10} {'Closed':<12}")
        print(f"  {'─'*72}")
        for t in closed:
            instr      = t["instrument"]
            units      = abs(float(t["initialUnits"]))
            open_px    = float(t["price"])
            close_px   = float(t.get("averageClosePrice", 0))
            realised   = float(t.get("realizedPL", 0))
            close_date = t.get("closeTime", "")[:10]
            total_realised += realised
            pl_sign = "+" if realised >= 0 else ""
            print(f"  {instr:<16} {units:>8,.1f} ${open_px:>9,.2f} ${close_px:>9,.2f} "
                  f"{pl_sign}${realised:>8,.2f}  {close_date}")
        print(f"  {'─'*72}")
        sign = "+" if total_realised >= 0 else ""
        print(f"  {'Total realised P&L':>51} {sign}${total_realised:>8,.2f}")

    # ── equity history ─────────────────────────────────────────────────────
    if args.history:
        print(f"\n  EQUITY LOG (last 30 entries)")
        print(f"  {'─'*60}")
        print_equity_history()

    print()
    print(f"  Tip: run daily with --log to build equity history.")
    print(f"       run with --history to see the equity curve.")
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
