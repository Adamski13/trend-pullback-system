#!/usr/bin/env python3
"""
TPS v2 — OANDA Signal Generator

Fetches account data and instrument prices directly from OANDA, computes
EWMAC forecasts + vol-targeted positions, and optionally places orders.

Environment variables required:
    OANDA_TOKEN    — your API token (Settings → Manage API Access)
    OANDA_ACCOUNT  — account ID  (e.g. 001-001-12345678-001)
    OANDA_ENV      — "practice" or "live"  (default: practice)

Usage:
    python generate_signals.py              # paper mode — report only
    python generate_signals.py --live       # execute orders on OANDA
    python generate_signals.py --capital 75000  # override account NAV
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from oanda_client import OandaClient
from indicators import (
    combined_forecast, regime_filter,
    instrument_volatility, sma, ewmac_forecast,
)

# ── paths ─────────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent
CONFIG     = ROOT / "config.yaml"
STATE_FILE = ROOT / "state.json"   # persists avg_pos between runs

# ── state (avg_pos for buffer calculation) ────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def update_avg_pos(state: dict, symbol: str, current_pos: float) -> float:
    """
    Exponential moving average of |position|.
    Initialises to |current_pos| on first run.
    Decays toward 0 when flat, grows when in position.
    """
    prev = state.get(symbol, {}).get("avg_pos", abs(current_pos))
    new  = 0.95 * prev + 0.05 * abs(current_pos)
    return new

# ── signal computation ────────────────────────────────────────────────────────

def compute_instrument_signal(df: pd.DataFrame, config: dict) -> dict:
    """
    Given a price DataFrame (Close column), return a dict of signal values
    for the most recent complete bar.
    """
    price        = df["Close"]
    ewmac_cfg    = config["ewmac"]
    sizing_cfg   = config["sizing"]
    forecast_cfg = config["forecast"]
    regime_cfg   = config["regime"]

    # Component forecasts
    fc_8_32   = ewmac_forecast(price, 8,  32,  ewmac_cfg["forecast_scalars"]["8_32"],
                               sizing_cfg["vol_lookback_days"]).iloc[-1]
    fc_16_64  = ewmac_forecast(price, 16, 64,  ewmac_cfg["forecast_scalars"]["16_64"],
                               sizing_cfg["vol_lookback_days"]).iloc[-1]
    fc_32_128 = ewmac_forecast(price, 32, 128, ewmac_cfg["forecast_scalars"]["32_128"],
                               sizing_cfg["vol_lookback_days"]).iloc[-1]

    # Blended forecast
    fc_raw = combined_forecast(
        price=price,
        variations=ewmac_cfg["variations"],
        forecast_scalars=ewmac_cfg["forecast_scalars"],
        forecast_weights=ewmac_cfg["forecast_weights"],
        forecast_div_multiplier=ewmac_cfg["forecast_div_multiplier"],
        forecast_cap=forecast_cfg["cap"],
        forecast_floor=forecast_cfg["floor"],
        vol_lookback=sizing_cfg["vol_lookback_days"],
    ).iloc[-1]

    # Regime
    sma200    = sma(price, regime_cfg["sma_period"]).iloc[-1]
    is_bull   = bool(price.iloc[-1] > sma200)
    fc_final  = fc_raw if (not regime_cfg["enabled"] or is_bull) else 0.0

    # Volatility
    inst_vol  = float(instrument_volatility(price, sizing_cfg["vol_lookback_days"]).iloc[-1])

    return {
        "price":     float(price.iloc[-1]),
        "price_date": str(price.index[-1].date()),
        "sma200":    float(sma200),
        "regime":    "BULL" if is_bull else "BEAR",
        "inst_vol":  inst_vol,
        "fc_8_32":   float(fc_8_32),
        "fc_16_64":  float(fc_16_64),
        "fc_32_128": float(fc_32_128),
        "forecast":  float(fc_final),
    }


def target_position(signal: dict, capital: float,
                    weight: float, config: dict) -> float:
    """Carver position sizing formula → rounded to nearest unit."""
    fc       = signal["forecast"]
    inst_vol = signal["inst_vol"]
    price    = signal["price"]
    sizing   = config["sizing"]

    if inst_vol <= 0 or price <= 0 or np.isnan(inst_vol) or fc == 0:
        return 0.0

    raw = (capital * sizing["vol_target_pct"] * weight
           * sizing["instrument_div_multiplier"] * (fc / 10.0)) / (inst_vol * price)
    return max(0.0, round(raw))

# ── report formatting ─────────────────────────────────────────────────────────

def _bar(value: float, cap: float = 20.0, width: int = 20) -> str:
    filled = int(round(value / cap * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _fc_label(fc: float) -> str:
    if fc >= 15: return "STRONG"
    if fc >= 10: return "MODERATE"
    if fc >=  5: return "WEAK"
    if fc >   0: return "MINIMAL"
    return "FLAT"


def print_report(account: dict, signals: list, env: str, live: bool):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
    env_tag = env.upper()

    print()
    print("=" * 72)
    print(f"  TPS v2 — OANDA Signal Report")
    print(f"  {now}   [{env_tag}]")
    print("=" * 72)
    print()

    if account:
        pl_sign = "+" if account["unrealized_pl"] >= 0 else ""
        print(f"  Account NAV:     ${account['nav']:>12,.2f}")
        print(f"  Balance:         ${account['balance']:>12,.2f}")
        print(f"  Unrealized P&L:  {pl_sign}${account['unrealized_pl']:>11,.2f}")
        print()

    actions = []

    for s in signals:
        sym   = s["symbol"]
        label = s.get("label", sym)
        print(f"  {'─'*68}")
        print(f"  {sym}  ({label})")
        print(f"  {'─'*68}")

        if "error" in s:
            print(f"  ERROR: {s['error']}")
            print()
            continue

        regime_tag = "▲ BULL" if s["regime"] == "BULL" else "▼ BEAR"
        print(f"  Price:   ${s['price']:>12,.2f}   SMA200: ${s['sma200']:>12,.2f}   Regime: {regime_tag}")
        print(f"  Vol:     {s['inst_vol']*100:>5.1f}% annual   (data as of {s['price_date']})")
        print()

        fc_bar   = _bar(s["forecast"])
        fc_label = _fc_label(s["forecast"])
        regime_note = "  (regime filter)" if s["regime"] == "BEAR" and s["forecast"] == 0.0 else ""
        print(f"  Forecast:  {s['forecast']:>5.1f} / 20  [{fc_bar}]  {fc_label}{regime_note}")
        print(f"    8/32:  {s['fc_8_32']:>7.2f}    16/64:  {s['fc_16_64']:>7.2f}    32/128:  {s['fc_32_128']:>7.2f}")
        print()

        cur   = s["current_units"]
        tgt   = s["target_units"]
        delta = s["delta"]
        buf   = s["buffer_thresh"]
        notional_cur = cur * s["price"]
        notional_tgt = tgt * s["price"]

        print(f"  Position:")
        print(f"    Target:   {tgt:>6.0f} units  (~${notional_tgt:>12,.0f} notional)")
        print(f"    Current:  {cur:>6.0f} units  (~${notional_cur:>12,.0f} notional)")
        print(f"    Delta:    {delta:>+6.0f} units  |  Buffer: ±{buf:.1f} units (30% × avg {s['avg_pos']:.1f})")
        print()

        action = s["action"]
        if action == "HOLD":
            print(f"  — HOLD  (delta within buffer)")
        elif action == "CLOSE":
            tag = "[PAPER — not executed]" if not live else "[EXECUTED]"
            print(f"  ✦ CLOSE ALL {sym}  {tag}")
            actions.append(s)
        elif action == "BUY":
            tag = "[PAPER — not executed]" if not live else "[EXECUTED]"
            print(f"  ✦ BUY  {abs(delta):.0f} {sym}  {tag}")
            actions.append(s)
        elif action == "SELL":
            tag = "[PAPER — not executed]" if not live else "[EXECUTED]"
            print(f"  ✦ SELL {abs(delta):.0f} {sym}  {tag}")
            actions.append(s)
        print()

    print(f"  {'─'*68}")
    n_actions = len(actions)
    if n_actions == 0:
        print("  PORTFOLIO: no trades — all instruments within buffer")
    else:
        print(f"  PORTFOLIO: {n_actions} action(s) pending")
        for s in actions:
            print(f"    {s['action']:5s} {abs(s['delta']):.0f} {s['symbol']}")

    if not live:
        print()
        print("  Mode: PAPER — run with --live to execute orders")

    print("=" * 72)
    print()

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TPS v2 OANDA signal generator")
    parser.add_argument("--live",    action="store_true",
                        help="Execute orders on OANDA (default: paper mode)")
    parser.add_argument("--capital", type=float, default=None,
                        help="Override account NAV for sizing (e.g. 75000)")
    args = parser.parse_args()

    # ── credentials ──────────────────────────────────────────────────────────
    token      = os.environ.get("OANDA_TOKEN")
    account_id = os.environ.get("OANDA_ACCOUNT")
    env        = os.environ.get("OANDA_ENV", "practice").lower()

    if not token or not account_id:
        print("ERROR: OANDA_TOKEN and OANDA_ACCOUNT environment variables must be set.")
        print("  export OANDA_TOKEN=your-token-here")
        print("  export OANDA_ACCOUNT=001-001-XXXXXXXX-001")
        sys.exit(1)

    if args.live and env != "live":
        print("WARNING: --live flag set but OANDA_ENV is 'practice'. Orders go to paper account.")

    # ── init ─────────────────────────────────────────────────────────────────
    with open(CONFIG) as f:
        config = yaml.safe_load(f)

    client = OandaClient(token, account_id, env)
    state  = load_state()

    # ── account ──────────────────────────────────────────────────────────────
    print("Fetching account data...")
    try:
        account = client.get_account()
        capital = args.capital if args.capital else account["nav"]
        print(f"  NAV: ${capital:,.2f}")
    except Exception as e:
        print(f"  WARNING: Could not fetch account ({e}). Using --capital or default $100k.")
        account = None
        capital = args.capital or 100_000

    # ── positions ─────────────────────────────────────────────────────────────
    print("Fetching current positions...")
    try:
        open_positions = client.get_positions()
    except Exception as e:
        print(f"  WARNING: Could not fetch positions ({e}). Assuming flat.")
        open_positions = {}

    # ── signals ───────────────────────────────────────────────────────────────
    print("Computing signals...")
    signals = []

    for instr in config["instruments"]:
        sym    = instr["symbol"]
        weight = instr["weight"]
        label  = instr.get("label", sym)

        print(f"  {sym}...", end=" ", flush=True)
        try:
            df = client.get_candles(sym, count=config["data"]["candle_count"],
                                         granularity=config["data"]["granularity"])
        except Exception as e:
            print(f"FAILED ({e})")
            signals.append({"symbol": sym, "label": label, "error": str(e)})
            continue

        if len(df) < 210:
            msg = f"only {len(df)} bars — need 210+ for SMA200"
            print(f"FAILED ({msg})")
            signals.append({"symbol": sym, "label": label, "error": msg})
            continue

        sig          = compute_instrument_signal(df, config)
        tgt          = target_position(sig, capital, weight, config)
        cur          = float(open_positions.get(sym, 0.0))
        avg_pos      = update_avg_pos(state, sym, cur)
        buf_thresh   = config["buffering"]["threshold_fraction"] * max(avg_pos, 1.0)
        delta        = tgt - cur

        # Buffer gate
        if abs(delta) < buf_thresh:
            action = "HOLD"
        elif tgt == 0 and cur > 0:
            action = "CLOSE"
        elif delta > 0:
            action = "BUY"
        else:
            action = "SELL"

        sig.update({
            "symbol":        sym,
            "label":         label,
            "weight":        weight,
            "current_units": cur,
            "target_units":  tgt,
            "delta":         delta,
            "avg_pos":       avg_pos,
            "buffer_thresh": buf_thresh,
            "action":        action,
        })
        signals.append(sig)
        print("OK")

    # ── report ─────────────────────────────────────────────────────────────────
    print_report(account, signals, env, live=args.live)

    # ── execute ────────────────────────────────────────────────────────────────
    if args.live:
        print("Executing orders...")
        for s in signals:
            if "error" in s or s["action"] == "HOLD":
                continue
            sym = s["symbol"]
            try:
                if s["action"] == "CLOSE":
                    resp = client.close_long(sym)
                    print(f"  CLOSED {sym}: {resp}")
                else:
                    units = round(s["delta"])
                    resp  = client.place_order(sym, units)
                    print(f"  ORDER {sym} {units:+d}: {resp.get('orderFillTransaction', {}).get('type', resp)}")
            except Exception as e:
                print(f"  ERROR executing {sym}: {e}")

    # ── update state (avg_pos) ─────────────────────────────────────────────────
    for s in signals:
        if "error" in s:
            continue
        sym = s["symbol"]
        # If we traded, new current = target. Otherwise current stays.
        new_cur = s["target_units"] if (args.live and s["action"] != "HOLD") else s["current_units"]
        if sym not in state:
            state[sym] = {}
        state[sym]["avg_pos"] = round(0.95 * s["avg_pos"] + 0.05 * abs(new_cur), 4)

    save_state(state)


if __name__ == "__main__":
    main()
