#!/usr/bin/env python3
"""
TPS v2 — Live Signal Generator

Fetches current prices for QQQ, GLD, BTC-USD and prints today's
EWMAC forecasts, regime status, and target positions.

Usage:
    python live_signals.py                     # uses $100k default capital
    python live_signals.py --capital 250000    # custom portfolio size
    python live_signals.py --json              # machine-readable JSON output
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ── path setup so we can import src/ regardless of cwd ──────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.data_loader import load_multiple
from src.indicators import (
    combined_forecast, regime_filter, instrument_volatility,
    sma, ewmac_forecast
)


# ── config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = ROOT / "config" / "default_config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── data fetch ───────────────────────────────────────────────────────────────

def fetch_recent(symbols: list, lookback_days: int = 300) -> dict:
    """Fetch recent price history. lookback_days must be > longest EMA (128)."""
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    print(f"Fetching data ({start} → {end})...")
    return load_multiple(symbols, start_date=start, end_date=end, use_cache=False)


# ── signal computation ───────────────────────────────────────────────────────

def compute_signals(data: dict, config: dict, capital: float) -> list:
    """
    Compute current forecast + target position for each instrument.

    Returns list of signal dicts, one per instrument.
    """
    ewmac_cfg   = config["ewmac"]
    sizing_cfg  = config["sizing"]
    regime_cfg  = config["regime"]
    forecast_cfg = config["forecast"]

    signals = []

    for instrument in config["instruments"]:
        symbol = instrument["symbol"]
        if symbol not in data:
            signals.append({"symbol": symbol, "error": "no data"})
            continue

        df   = data[symbol]
        price_series = df["Close"]

        if len(price_series) < 250:
            signals.append({"symbol": symbol, "error": f"only {len(price_series)} bars — need 250+"})
            continue

        current_price = float(price_series.iloc[-1])
        current_date  = price_series.index[-1]

        # ── individual EWMAC components ──────────────────────────────────────
        fc_8_32   = ewmac_forecast(price_series, 8,  32,  ewmac_cfg["forecast_scalars"]["8_32"],
                                   sizing_cfg["vol_lookback_days"]).iloc[-1]
        fc_16_64  = ewmac_forecast(price_series, 16, 64,  ewmac_cfg["forecast_scalars"]["16_64"],
                                   sizing_cfg["vol_lookback_days"]).iloc[-1]
        fc_32_128 = ewmac_forecast(price_series, 32, 128, ewmac_cfg["forecast_scalars"]["32_128"],
                                   sizing_cfg["vol_lookback_days"]).iloc[-1]

        # ── blended forecast ─────────────────────────────────────────────────
        forecast_raw = combined_forecast(
            price=price_series,
            variations=ewmac_cfg["variations"],
            forecast_scalars=ewmac_cfg["forecast_scalars"],
            forecast_weights=ewmac_cfg["forecast_weights"],
            forecast_div_multiplier=ewmac_cfg["forecast_div_multiplier"],
            forecast_cap=forecast_cfg["cap"],
            forecast_floor=forecast_cfg["floor"],
            vol_lookback=sizing_cfg["vol_lookback_days"]
        ).iloc[-1]

        # ── regime ───────────────────────────────────────────────────────────
        sma200_series = sma(price_series, regime_cfg["sma_period"])
        sma200_val    = float(sma200_series.iloc[-1])
        is_bull       = current_price > sma200_val

        forecast_final = forecast_raw if (not regime_cfg["enabled"] or is_bull) else 0.0

        # ── vol + sizing ─────────────────────────────────────────────────────
        inst_vol = float(instrument_volatility(price_series, sizing_cfg["vol_lookback_days"]).iloc[-1])

        weight = sizing_cfg["instrument_weights"].get(symbol, 1.0 / len(config["instruments"]))
        vol_target = sizing_cfg["vol_target_pct"]
        idm        = sizing_cfg["instrument_div_multiplier"]

        if inst_vol > 0 and current_price > 0:
            target_raw = (capital * vol_target * weight * idm * (forecast_final / 10.0)) / (inst_vol * current_price)
            target_shares = max(0, round(target_raw))
        else:
            target_shares = 0

        target_value  = target_shares * current_price
        target_pct    = target_value / capital * 100 if capital > 0 else 0.0

        signals.append({
            "symbol":         symbol,
            "date":           str(current_date.date()),
            "price":          round(current_price, 2),
            "sma200":         round(sma200_val, 2),
            "regime":         "BULL" if is_bull else "BEAR",
            "inst_vol_pct":   round(inst_vol * 100, 1),
            "fc_8_32":        round(float(fc_8_32),   2),
            "fc_16_64":       round(float(fc_16_64),  2),
            "fc_32_128":      round(float(fc_32_128), 2),
            "forecast":       round(float(forecast_final), 2),
            "target_shares":  target_shares,
            "target_value":   round(target_value, 0),
            "target_pct":     round(target_pct, 1),
        })

    return signals


# ── output ───────────────────────────────────────────────────────────────────

SIGNAL_LABELS = {
    (15, 20):  "STRONG",
    (10, 15):  "MODERATE",
    (5,  10):  "WEAK",
    (0,   5):  "MINIMAL",
}


def classify_forecast(fc: float) -> str:
    for (lo, hi), label in SIGNAL_LABELS.items():
        if lo <= fc < hi:
            return label
    return "FLAT" if fc == 0 else "STRONG" if fc >= 20 else "FLAT"


def print_report(signals: list, capital: float):
    today = date.today().isoformat()
    print()
    print("=" * 62)
    print(f"  TPS v2 — Live Signals   {today}")
    print(f"  Portfolio: ${capital:,.0f}")
    print("=" * 62)

    total_allocated = sum(s.get("target_value", 0) for s in signals if "error" not in s)
    cash = capital - total_allocated

    for s in signals:
        print()
        if "error" in s:
            print(f"  {s['symbol']:10s}  ERROR: {s['error']}")
            continue

        regime_tag = "▲ BULL" if s["regime"] == "BULL" else "▼ BEAR"
        fc_label   = classify_forecast(s["forecast"])
        fc_bar     = int(s["forecast"] / 20.0 * 20)
        bar_str    = "█" * fc_bar + "░" * (20 - fc_bar)

        print(f"  ─── {s['symbol']} ───────────────────────────────────────")
        print(f"  Price:    ${s['price']:>10,.2f}   SMA200: ${s['sma200']:>10,.2f}   Regime: {regime_tag}")
        print(f"  Vol:      {s['inst_vol_pct']:>5.1f}% annual")
        print(f"  Forecast: {s['forecast']:>5.1f} / 20  [{bar_str}]  {fc_label}")
        print(f"    8/32:   {s['fc_8_32']:>6.2f}")
        print(f"    16/64:  {s['fc_16_64']:>6.2f}")
        print(f"    32/128: {s['fc_32_128']:>6.2f}")
        print(f"  Target:   {s['target_shares']:>6,} sh   ${s['target_value']:>10,.0f}  ({s['target_pct']:.1f}% of portfolio)")

    print()
    print(f"  ─── Portfolio Summary ───────────────────────────────")
    print(f"  Total allocated: ${total_allocated:>10,.0f}  ({total_allocated/capital*100:.1f}%)")
    print(f"  Cash:            ${cash:>10,.0f}  ({cash/capital*100:.1f}%)")
    print("=" * 62)
    print()


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TPS v2 live signal generator")
    parser.add_argument("--capital",  type=float, default=100_000,
                        help="Portfolio value in USD (default: 100000)")
    parser.add_argument("--lookback", type=int,   default=400,
                        help="Days of history to fetch (default: 400)")
    parser.add_argument("--json",     action="store_true",
                        help="Output JSON instead of formatted report")
    args = parser.parse_args()

    config  = load_config()
    symbols = [i["symbol"] for i in config["instruments"]]

    data    = fetch_recent(symbols, lookback_days=args.lookback)
    signals = compute_signals(data, config, capital=args.capital)

    if args.json:
        print(json.dumps({
            "date":     date.today().isoformat(),
            "capital":  args.capital,
            "signals":  signals,
        }, indent=2))
    else:
        print_report(signals, capital=args.capital)


if __name__ == "__main__":
    main()
