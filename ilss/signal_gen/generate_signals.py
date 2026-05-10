#!/usr/bin/env python3
"""
ILSS Signal Generator

Polls OANDA for live M15 data, detects SFP signals at validated key levels,
applies daily bias filter, and manages trades by ID (hedging-safe).

Designed to run every 15 minutes via cron during market hours.

Environment variables:
    OANDA_TOKEN              — API token
    OANDA_ACCOUNT            — account ID  (e.g. 001-004-XXXXXXX-001)
    OANDA_ENV                — "practice" or "live"  (default: practice)
    ILSS_TELEGRAM_TOKEN      — Telegram bot token (ILSS-specific bot)
    ILSS_TELEGRAM_CHAT_ID    — Telegram chat ID

Usage:
    python generate_signals.py              # paper mode — log + alert only
    python generate_signals.py --live       # execute orders on OANDA
    python generate_signals.py --status     # send daily status to Telegram
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

# ── local imports ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT.parent / "src"))

from session_labels import prepare as prepare_m15
from sfp_detector   import detect_sfps
from daily_bias     import compute_daily_bias

from oanda_client    import OandaClient
from telegram_notify import ILSSTelegramNotifier

# ── paths ──────────────────────────────────────────────────────────────────────
CONFIG_FILE = _ROOT / "config.yaml"
STATE_FILE  = _ROOT / "state.json"
SIGNAL_LOG  = _ROOT / "signal_log.csv"

# ── session windows (UTC hours) ───────────────────────────────────────────────
SESSION_WINDOWS = {
    "asian":        ( 0,  7),
    "london_open":  ( 7,  9),
    "london":       ( 9, 12),
    "ny_open":      (12, 15),
    "ny_afternoon": (15, 19),
    "ny_close":     (19, 21),
}


def _current_sessions(utc_hour: int) -> list[str]:
    active = []
    for name, (start, end) in SESSION_WINDOWS.items():
        if start <= utc_hour < end:
            active.append(name)
    return active


# ── state ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "today":          None,
        "day_trades":     0,
        "day_pnl_pct":    0.0,
        "active_trades":  [],    # ILSS live trades: {trade_id, symbol, hold_bars, bars_elapsed, ...}
        "signalled_bars": [],    # "SYMBOL_YYYYMMDD_HHMM" — dedup for paper signals
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _reset_day_if_needed(state: dict, today_str: str):
    if state.get("today") != today_str:
        state["today"]      = today_str
        state["day_trades"] = 0
        state["day_pnl_pct"] = 0.0
        # Keep only today's signalled bars for dedup
        state["signalled_bars"] = [b for b in state.get("signalled_bars", [])
                                   if b.startswith(today_str[:8])]


# ── trade lifecycle ───────────────────────────────────────────────────────────

def tick_active_trades(state: dict, client: OandaClient,
                       notifier: ILSSTelegramNotifier, live: bool) -> None:
    """
    Increment bars_elapsed on every active ILSS trade.
    Close (by trade ID) any trade that has reached its time stop.
    Removes closed trades from state.
    """
    remaining = []
    for trade in state.get("active_trades", []):
        trade["bars_elapsed"] = trade.get("bars_elapsed", 0) + 1
        if trade["bars_elapsed"] >= trade["hold_bars"]:
            print(f"  TIME STOP: {trade['symbol']} trade {trade['trade_id']} "
                  f"({trade['bars_elapsed']}/{trade['hold_bars']} bars)")
            is_live_trade = (live and trade.get("execution") == "live"
                             and not trade["trade_id"].startswith("paper_"))
            if is_live_trade:
                try:
                    client.close_trade(trade["trade_id"])
                    print(f"    closed trade {trade['trade_id']}")
                    notifier.send(
                        f"⏹ *ILSS Time Stop*\n"
                        f"{trade['symbol']} trade {trade['trade_id']} closed\n"
                        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
                    )
                except Exception as e:
                    print(f"    ERROR closing trade {trade['trade_id']}: {e}")
                    remaining.append(trade)   # retry next tick
            else:
                print(f"    [PAPER/SIGNAL-ONLY] time stop reached — removed from tracking")
            # Don't append → trade removed from active list
        else:
            remaining.append(trade)
    state["active_trades"] = remaining


# ── signal log ────────────────────────────────────────────────────────────────

def _log_signal(signal: dict, paper: bool, trade_id: str = ""):
    row = {
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "mode":         "paper" if paper else "live",
        "symbol":       signal["symbol"],
        "session":      signal["session"],
        "direction":    signal["direction"],
        "level_type":   signal["level_type"],
        "bar_time":     signal["bar_time"],
        "entry":        signal["entry"],
        "stop":         signal["stop"],
        "stop_dist_r":  signal["stop_dist_r"],
        "units":        signal["units"],
        "nav":          signal["nav"],
        "execution":    signal["execution"],
        "trade_id":     trade_id,
    }
    write_header = not SIGNAL_LOG.exists()
    with open(SIGNAL_LOG, "a") as f:
        if write_header:
            f.write(",".join(row.keys()) + "\n")
        f.write(",".join(str(v) for v in row.values()) + "\n")


# ── position sizing ───────────────────────────────────────────────────────────

def compute_units(nav: float, risk_pct: float, stop_dist: float,
                  entry_price: float, symbol: str) -> int:
    """
    Risk-based position sizing (USD account).

    USD-quoted (EUR_USD, GBP_USD, XAU_USD, BTC_USD, NAS100_USD):
        units = (NAV × risk_pct) / stop_dist

    Non-USD quote (USD_JPY):
        units = (NAV × risk_pct × entry_price) / stop_dist
    """
    if stop_dist <= 0:
        return 0
    risk_dollar = nav * risk_pct
    if symbol == "USD_JPY":
        units = (risk_dollar * entry_price) / stop_dist
    else:
        units = risk_dollar / stop_dist
    return max(1, int(units))


# ── daily bias ────────────────────────────────────────────────────────────────

def check_bias(client: OandaClient, symbol: str, config: dict) -> bool:
    bias_cfg = config["bias"]
    try:
        daily_df = client.get_candles(symbol, count=config["data"]["daily_count"],
                                      granularity="D")
        if len(daily_df) < 50:
            return True
        bias_df = compute_daily_bias(
            daily_df,
            long_threshold=bias_cfg["long_threshold"],
            vol_lookback=bias_cfg["vol_lookback"],
        )
        return bias_df["bias"].iloc[-1] == "bull"
    except Exception as e:
        print(f"    bias check failed for {symbol}: {e} — allowing trade")
        return True


# ── SFP scan ──────────────────────────────────────────────────────────────────

def scan_for_sfp(client: OandaClient, instr_cfg: dict,
                 sfp_cfg: dict, data_cfg: dict) -> dict | None:
    symbol = instr_cfg["symbol"]

    m15 = client.get_m15_candles(symbol, count=data_cfg["m15_count"])
    if len(m15) < 30:
        print(f"    {symbol}: not enough M15 bars ({len(m15)})")
        return None

    m15p = prepare_m15(m15, atr_period=sfp_cfg["atr_period"])

    sfps = detect_sfps(
        m15p,
        symbol=symbol,
        min_sweep_atr=sfp_cfg["sweep_min_depth"],
        max_sweep_atr=sfp_cfg["sweep_max_depth"],
        stop_buffer_atr=sfp_cfg["stop_buffer_atr"],
        active_sessions=instr_cfg["sessions"],
    )

    if sfps.empty:
        return None

    latest_bar_time = m15p.index[-1]
    if latest_bar_time not in sfps.index:
        return None

    row = sfps.loc[latest_bar_time]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]

    if row["direction"] != "bull":   # long_only mode
        return None

    return {
        "symbol":      symbol,
        "label":       instr_cfg["label"],
        "session":     row["session"],
        "direction":   row["direction"],
        "level_type":  row["level_type"],
        "bar_time":    str(latest_bar_time),
        "entry":       round(float(row["entry_price"]),  5),
        "stop":        round(float(row["stop_price"]),   5),
        "stop_dist":   round(float(row["stop_distance"]), 5),
        "stop_dist_r": round(float(row["stop_distance_r"]), 3),
        "atr":         round(float(row["atr"]), 5),
        "hold_bars":   instr_cfg["hold_bars"],
        "hold_hours":  round(instr_cfg["hold_bars"] * 15 / 60, 1),
        "execution":   instr_cfg.get("execution", "live"),
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ILSS intraday signal generator")
    parser.add_argument("--live",    action="store_true",
                        help="Execute orders on OANDA (default: paper/log only)")
    parser.add_argument("--status",  action="store_true",
                        help="Send daily status summary to Telegram and exit")
    parser.add_argument("--capital", type=float, default=None,
                        help="Override account NAV for sizing")
    args = parser.parse_args()

    # ── credentials ───────────────────────────────────────────────────────────
    token      = os.environ.get("OANDA_TOKEN")
    account_id = os.environ.get("OANDA_ACCOUNT")
    env        = os.environ.get("OANDA_ENV", "practice").lower()

    if not token or not account_id:
        print("ERROR: OANDA_TOKEN and OANDA_ACCOUNT must be set.")
        sys.exit(1)

    if args.live and env != "live":
        print("WARNING: --live flag but OANDA_ENV='practice'. Orders go to paper account.")

    # ── init ──────────────────────────────────────────────────────────────────
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    client   = OandaClient(token, account_id, env)
    notifier = ILSSTelegramNotifier()
    state    = load_state()

    now_utc = datetime.now(timezone.utc)
    today   = now_utc.strftime("%Y%m%d")
    _reset_day_if_needed(state, today)

    # ── account ───────────────────────────────────────────────────────────────
    try:
        account = client.get_account()
        nav     = args.capital if args.capital else account["nav"]
    except Exception as e:
        print(f"WARNING: Could not fetch account ({e}). Using --capital or £1500.")
        account = None
        nav     = args.capital or float(config["risk"]["initial_capital_gbp"])

    # ── hard stop: 50% NAV ────────────────────────────────────────────────────
    risk_cfg      = config["risk"]
    initial_cap   = float(risk_cfg["initial_capital_gbp"])
    hard_stop_nav = initial_cap * risk_cfg["hard_stop_pct"]

    if nav < hard_stop_nav:
        msg = (f"HARD STOP: NAV {nav:.0f} < {hard_stop_nav:.0f} "
               f"(50% of {initial_cap:.0f}). Closing all trades.")
        print(f"🚨 {msg}")
        notifier.send(f"🚨 *ILSS HARD STOP*\n{msg}\n"
                      f"_{now_utc.strftime('%H:%M UTC')}_")
        if args.live:
            # Only close ILSS's own tracked trades — never touch TPS v2 trades
            for t in state.get("active_trades", []):
                if t.get("execution") == "live" and not t["trade_id"].startswith("paper_"):
                    try:
                        client.close_trade(t["trade_id"])
                    except Exception as ce:
                        print(f"  close error {t['trade_id']}: {ce}")
        save_state(state)
        sys.exit(1)

    # ── status mode ───────────────────────────────────────────────────────────
    if args.status:
        notifier.send_status(
            nav         = nav,
            open_trades = len(state.get("active_trades", [])),
            day_trades  = state["day_trades"],
            day_pnl_pct = state["day_pnl_pct"],
        )
        print(f"Status sent. NAV={nav:,.2f}  day_trades={state['day_trades']}")
        return

    # ── tick active trades (time stop management) ─────────────────────────────
    tick_active_trades(state, client, notifier, live=args.live)

    # ── daily loss halt ───────────────────────────────────────────────────────
    if state["day_pnl_pct"] <= -risk_cfg["daily_loss_limit_pct"]:
        msg = f"Daily loss limit hit ({state['day_pnl_pct']*100:.2f}%)"
        print(f"HALT: {msg}")
        notifier.send_halt_alert(msg, nav)
        save_state(state)
        return

    # ── header ────────────────────────────────────────────────────────────────
    current_sessions = _current_sessions(now_utc.hour)
    print(f"\n{now_utc.strftime('%Y-%m-%d %H:%M UTC')}  [{env.upper()}]")
    print(f"Active sessions: {current_sessions or ['none']}")
    print(f"Day trades: {state['day_trades']}/{config['sizing']['max_daily_trades']}")
    print(f"Active ILSS trades: {len(state.get('active_trades', []))}")

    # ── trade / sizing caps ───────────────────────────────────────────────────
    sizing       = config["sizing"]
    ilss_live    = [t for t in state.get("active_trades", [])
                    if t.get("execution") == "live"]
    ilss_count   = len(ilss_live)

    if state["day_trades"] >= sizing["max_daily_trades"]:
        print("Daily trade cap reached — no new entries.")
        save_state(state)
        return

    if ilss_count >= sizing["max_concurrent"]:
        print(f"Max concurrent ILSS positions ({sizing['max_concurrent']}) reached.")
        save_state(state)
        return

    # ── scan instruments ──────────────────────────────────────────────────────
    sfp_cfg  = config["sfp"]
    data_cfg = config["data"]
    new_signals = []

    for instr in config["instruments"]:
        symbol    = instr["symbol"]
        execution = instr.get("execution", "live")

        active_now = [s for s in instr["sessions"] if s in current_sessions]
        if not active_now:
            continue

        print(f"\n  {symbol} ({instr['label']})  [{execution}]  session: {active_now}")

        # Dedup: one signal per bar per instrument
        bar_key = (f"{symbol}_{now_utc.strftime('%Y%m%d_%H')}"
                   f"{(now_utc.minute // 15) * 15:02d}")
        if bar_key in state.get("signalled_bars", []):
            print(f"    already signalled this bar — skip")
            continue

        # Daily bias
        if instr["bias_filter"]:
            if not check_bias(client, symbol, config):
                print(f"    bias=BEAR — skip")
                continue
            print(f"    bias=BULL")
        else:
            print(f"    bias filter OFF")

        # SFP scan
        try:
            signal = scan_for_sfp(client, instr, sfp_cfg, data_cfg)
        except Exception as e:
            print(f"    ERROR scanning {symbol}: {e}")
            continue

        if signal is None:
            print(f"    no SFP on latest bar")
            continue

        print(f"    SFP FOUND: {signal['direction']} sweep of {signal['level_type']}")
        print(f"    entry={signal['entry']}  stop={signal['stop']}  "
              f"dist={signal['stop_dist_r']}× ATR")

        units = compute_units(nav, sizing["risk_pct"], signal["stop_dist"],
                              signal["entry"], symbol)
        signal["units"] = units
        signal["nav"]   = nav
        print(f"    size={units:,} units  ({sizing['risk_pct']*100:.0f}% risk)")

        new_signals.append((signal, bar_key))

        # Cap: live trades only count toward the concurrent limit
        live_after = ilss_count + sum(
            1 for s, _ in new_signals if s["execution"] == "live"
        )
        if live_after >= sizing["max_concurrent"]:
            break

    # ── act on signals ────────────────────────────────────────────────────────
    for signal, bar_key in new_signals:
        symbol    = signal["symbol"]
        execution = signal["execution"]
        trade_id  = ""

        state.setdefault("signalled_bars", []).append(bar_key)
        state["day_trades"] += 1

        if execution == "live" and args.live:
            try:
                resp     = client.place_market_order(symbol, signal["units"],
                                                     stop_loss=signal["stop"])
                fill     = resp.get("orderFillTransaction", {})
                trade_id = fill.get("tradeOpened", {}).get("tradeID", "")
                if not trade_id:
                    print(f"    WARNING: order filled but no tradeID in response — cannot track")
                    print(f"    Full response: {resp}")
                else:
                    print(f"    ORDER FILLED — trade_id={trade_id}")

                # Track trade for time stop management (only if we have a valid ID)
                if trade_id:
                    state.setdefault("active_trades", []).append({
                        "trade_id":     trade_id,
                        "symbol":       symbol,
                        "entry_price":  signal["entry"],
                        "stop_price":   signal["stop"],
                        "hold_bars":    signal["hold_bars"],
                        "bars_elapsed": 0,
                        "execution":    "live",
                        "opened_at":    now_utc.isoformat(),
                    })
            except Exception as e:
                print(f"    ORDER FAILED: {e}")
                state["day_trades"] -= 1   # don't count failed orders
                continue

        elif execution == "signal_only" or not args.live:
            mode_str = "SIGNAL ONLY" if execution == "signal_only" else "PAPER"
            print(f"    [{mode_str}] logged — no order placed")
            # Track paper trades for time stop logging
            state.setdefault("active_trades", []).append({
                "trade_id":     f"paper_{bar_key}",
                "symbol":       symbol,
                "entry_price":  signal["entry"],
                "stop_price":   signal["stop"],
                "hold_bars":    signal["hold_bars"],
                "bars_elapsed": 0,
                "execution":    execution,
                "opened_at":    now_utc.isoformat(),
            })

        _log_signal(signal, paper=(not args.live or execution == "signal_only"),
                    trade_id=trade_id)
        notifier.send_sfp_signal(signal, paper=(not args.live or execution == "signal_only"))

    if not new_signals:
        print("\nNo new signals this run.")

    save_state(state)
    active_count = len(state.get("active_trades", []))
    print(f"\nDone. Day trades: {state['day_trades']}/{sizing['max_daily_trades']}  "
          f"Active: {active_count}")

    notifier.send_heartbeat(
        nav          = nav,
        sessions     = current_sessions,
        day_trades   = state["day_trades"],
        active_trades= active_count,
    )


if __name__ == "__main__":
    main()
