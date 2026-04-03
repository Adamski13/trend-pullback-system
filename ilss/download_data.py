#!/usr/bin/env python3
"""
ILSS Data Download Script

Downloads 15-min and daily OHLCV data for all 8 instruments from OANDA.
Run this once before any backtesting. Takes ~5–10 minutes.

Usage:
    python download_data.py
    python download_data.py --symbol NAS100_USD   # single instrument
    python download_data.py --granularity M5      # different resolution
    python download_data.py --no-cache            # force re-download

Requires:
    export OANDA_TOKEN="your-token"
    export OANDA_ENV="practice"  # or "live" — both have same historical data
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.data_loader import download_all, download_instrument
from src.session_labels import prepare


def main():
    parser = argparse.ArgumentParser(description="ILSS data downloader")
    parser.add_argument("--symbol",      type=str, default=None,
                        help="Single instrument (default: all 8)")
    parser.add_argument("--granularity", type=str, default="M15",
                        help="Candle granularity (default: M15)")
    parser.add_argument("--start",       type=str, default="2020-01-01")
    parser.add_argument("--end",         type=str, default="2025-12-31")
    parser.add_argument("--no-cache",    action="store_true",
                        help="Force re-download even if cached")
    parser.add_argument("--verify",      action="store_true",
                        help="Load and print stats after download")
    args = parser.parse_args()

    if not os.environ.get("OANDA_TOKEN"):
        print("ERROR: set OANDA_TOKEN environment variable")
        sys.exit(1)

    # Load instrument list
    config_path = Path(__file__).parent / "config" / "instruments.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    all_symbols = [i["symbol"] for i in cfg["instruments"]]

    symbols = [args.symbol] if args.symbol else all_symbols

    print(f"ILSS Data Download")
    print(f"  Granularity : {args.granularity}")
    print(f"  Period      : {args.start} → {args.end}")
    print(f"  Instruments : {', '.join(symbols)}")
    print()

    # ── Download 15-min (or specified granularity) ────────────────────────────
    data = download_all(
        symbols,
        granularity=args.granularity,
        start_date=args.start,
        end_date=args.end,
        use_cache=not args.no_cache,
    )

    # ── Also download daily for EWMAC bias calculation ────────────────────────
    if args.granularity != "D":
        print()
        print("Downloading daily bars for EWMAC bias...")
        daily_data = download_all(
            symbols,
            granularity="D",
            start_date="2015-01-01",   # need extra history for 200 SMA warmup
            end_date=args.end,
            use_cache=not args.no_cache,
        )

    # ── Verify ────────────────────────────────────────────────────────────────
    if args.verify:
        print()
        print("Verification:")
        print(f"  {'Symbol':<16} {'Bars':>8} {'Start':<12} {'End':<12} "
              f"{'Sessions':<10} {'PDH ok'}")
        print(f"  {'─'*66}")
        for sym, df in data.items():
            enriched = prepare(df)
            has_sessions = "session" in enriched.columns
            has_pdh      = enriched["prev_day_high"].notna().any()
            print(f"  {sym:<16} {len(df):>8,} "
                  f"{str(df.index[0].date()):<12} "
                  f"{str(df.index[-1].date()):<12} "
                  f"{'yes' if has_sessions else 'NO':<10} "
                  f"{'yes' if has_pdh else 'NO'}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
