"""
Portfolio backtesting engine.

Runs TPS v1 strategy simultaneously across multiple instruments using a
single shared equity pool. Position sizing for every new layer on any
instrument uses total current portfolio equity × risk_pct.

Key rules (from spec):
  - Independent signals per instrument
  - Shared equity: one cash balance, all instruments draw from it
  - A trade on QQQ does NOT block a trade on GLD
  - Total risk cap: 6% (3 instruments × max 3 layers × 1% each)
  - Frictions applied per trade
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .backtester import Layer, _round_qty


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio engine
# ─────────────────────────────────────────────────────────────────────────────

def run_portfolio(
    signals: Dict[str, pd.DataFrame],
    cfg: dict,
) -> dict:
    """
    Simulate all instruments together on a shared equity pool.

    Args:
        signals: {symbol: signals_df} — each df has OHLCV + indicators + signal cols,
                 indexed by date (tz-naive).
        cfg: full config dict.

    Returns:
        {
          "portfolio_equity":     pd.Series   — combined daily equity,
          "instrument_equity":    pd.DataFrame — per-instrument mark-to-market,
          "instrument_pnl":       pd.DataFrame — per-instrument cumulative realised P&L,
          "daily_returns":        pd.Series,
          "trade_log":            pd.DataFrame,
          "benchmark_equity":     pd.Series   — equal-weight B&H,
        }
    """
    s = cfg["strategy"]
    ex = cfg["execution"]

    initial_capital: float = ex["initial_capital"]
    commission_pct: float  = ex.get("commission_pct", 0.0) / 100.0
    slippage_pct: float    = ex.get("slippage_pct", 0.0) / 100.0
    point_value: float     = ex.get("point_value", 1.0)
    risk_pct: float        = s["risk_per_layer_pct"] / 100.0
    trail_mult: float      = s["trail_atr_mult"]
    max_layers: int        = s["max_layers"]
    allow_shorts: bool     = s.get("allow_shorts", False)

    symbols = list(signals.keys())

    # ── Align all instruments to a common date index ──────────────────────────
    # Use union so no instrument is clipped. Missing dates filled forward.
    all_dates = sorted(set().union(*[set(df.index) for df in signals.values()]))
    all_dates = pd.DatetimeIndex(all_dates)

    aligned: Dict[str, pd.DataFrame] = {}
    for sym, df in signals.items():
        a = df.reindex(all_dates, method="ffill")
        aligned[sym] = a

    n = len(all_dates)

    # ── State ──────────────────────────────────────────────────────────────────
    cash = initial_capital
    closed_trades: List[dict] = []

    # Per-instrument: active layers and pending entry
    active: Dict[str, List[Layer]] = {sym: [] for sym in symbols}
    pending_long_stop:  Dict[str, Optional[float]] = {sym: None for sym in symbols}
    pending_short_stop: Dict[str, Optional[float]] = {sym: None for sym in symbols}

    # Equity series
    portfolio_equity_vals: List[float] = []
    instrument_equity_vals: Dict[str, List[float]] = {sym: [] for sym in symbols}
    instrument_realised: Dict[str, float] = {sym: 0.0 for sym in symbols}

    def _fill_price(raw: float, direction: int) -> float:
        if direction == 1:
            return raw * (1 + slippage_pct)
        return raw * (1 - slippage_pct)

    def _commission(price: float, qty: float) -> float:
        return price * qty * commission_pct

    def _total_equity(date_idx: int) -> float:
        """Cash + sum of all unrealised open P&L at current bar close."""
        total = cash
        for sym in symbols:
            bar = aligned[sym].iloc[date_idx]
            close_p = bar["Close"]
            for layer in active[sym]:
                total += (close_p - layer.entry_price) * layer.qty * layer.direction * point_value
        return total

    def _close_layer(sym: str, layer: Layer, price: float,
                     date: pd.Timestamp, reason: str) -> float:
        nonlocal cash
        fill = _fill_price(price, -layer.direction)
        gross = (fill - layer.entry_price) * layer.qty * layer.direction * point_value
        cost = _commission(fill, layer.qty) + _commission(layer.entry_price, layer.qty)
        pnl = gross - cost
        cash += pnl
        instrument_realised[sym] += pnl
        r_multiple = (
            (fill - layer.entry_price) / abs(layer.entry_price - layer.initial_stop) * layer.direction
            if layer.initial_stop != layer.entry_price else 0.0
        )
        closed_trades.append({
            "symbol": sym,
            "entry_date": layer.entry_date,
            "exit_date": date,
            "direction": "long" if layer.direction == 1 else "short",
            "entry_price": layer.entry_price,
            "exit_price": fill,
            "qty": layer.qty,
            "pnl": pnl,
            "r_multiple": r_multiple,
            "layer": layer.layer_num,
            "exit_reason": reason,
        })
        return pnl

    # ── Main loop ──────────────────────────────────────────────────────────────
    for i in range(n):
        date = all_dates[i]

        for sym in symbols:
            bar = aligned[sym].iloc[i]
            open_p  = bar["Open"]
            high_p  = bar["High"]
            low_p   = bar["Low"]
            close_p = bar["Close"]
            atr_val = bar["atr14"] if not np.isnan(bar["atr14"]) else 0.0
            regime_bull = bar["regime_bull"]
            regime_bear = bar["regime_bear"]

            long_layers  = [l for l in active[sym] if l.direction ==  1]
            short_layers = [l for l in active[sym] if l.direction == -1]

            # ── Execute pending entry at this bar's open ──────────────────
            if pending_long_stop[sym] is not None and len(long_layers) < max_layers:
                stop  = pending_long_stop[sym]
                fill  = _fill_price(open_p, 1)
                if stop < fill:
                    equity_now = _total_equity(i)
                    stop_dist  = abs(fill - stop)
                    raw_qty = (equity_now * risk_pct) / (stop_dist * point_value) if stop_dist > 0 else 0.0
                    qty = _round_qty(raw_qty)
                    if qty > 0:
                        cost = _commission(fill, qty)
                        cash -= cost
                        layer = Layer(
                            layer_num=len(long_layers) + 1,
                            direction=1,
                            entry_date=date,
                            entry_price=fill,
                            qty=qty,
                            initial_stop=stop,
                            stop=stop,
                            trail_extreme=close_p,
                        )
                        active[sym].append(layer)
                pending_long_stop[sym] = None

            if pending_short_stop[sym] is not None and allow_shorts and len(short_layers) < max_layers:
                stop  = pending_short_stop[sym]
                fill  = _fill_price(open_p, -1)
                if stop > fill:
                    equity_now = _total_equity(i)
                    stop_dist  = abs(fill - stop)
                    raw_qty = (equity_now * risk_pct) / (stop_dist * point_value) if stop_dist > 0 else 0.0
                    qty = _round_qty(raw_qty)
                    if qty > 0:
                        cost = _commission(fill, qty)
                        cash -= cost
                        layer = Layer(
                            layer_num=len(short_layers) + 1,
                            direction=-1,
                            entry_date=date,
                            entry_price=fill,
                            qty=qty,
                            initial_stop=stop,
                            stop=stop,
                            trail_extreme=close_p,
                        )
                        active[sym].append(layer)
                pending_short_stop[sym] = None

            # ── Regime-break exits ────────────────────────────────────────
            to_remove = []
            for layer in active[sym]:
                regime_ok = (layer.direction == 1 and regime_bull) or \
                            (layer.direction == -1 and regime_bear)
                if not regime_ok:
                    _close_layer(sym, layer, open_p, date, "regime_break")
                    to_remove.append(layer)
            for l in to_remove:
                active[sym].remove(l)

            # ── Trailing stop updates + stop checks ───────────────────────
            to_remove = []
            for layer in active[sym]:
                if layer.direction == 1:
                    layer.trail_extreme = max(layer.trail_extreme, close_p)
                    trail_stop = layer.trail_extreme - trail_mult * atr_val
                    layer.stop = max(layer.initial_stop, trail_stop)

                    if open_p <= layer.stop:
                        _close_layer(sym, layer, open_p, date, "stop_hit")
                        to_remove.append(layer)
                    elif low_p <= layer.stop:
                        _close_layer(sym, layer, layer.stop, date, "stop_hit")
                        to_remove.append(layer)
                else:
                    layer.trail_extreme = min(layer.trail_extreme, close_p)
                    trail_stop = layer.trail_extreme + trail_mult * atr_val
                    layer.stop = min(layer.initial_stop, trail_stop)

                    if open_p >= layer.stop:
                        _close_layer(sym, layer, open_p, date, "stop_hit")
                        to_remove.append(layer)
                    elif high_p >= layer.stop:
                        _close_layer(sym, layer, layer.stop, date, "stop_hit")
                        to_remove.append(layer)

            for l in to_remove:
                active[sym].remove(l)

            # ── Queue signals for next bar ────────────────────────────────
            long_count  = sum(1 for l in active[sym] if l.direction ==  1)
            short_count = sum(1 for l in active[sym] if l.direction == -1)

            if bar["long_signal"] and long_count < max_layers and short_count == 0:
                pending_long_stop[sym] = bar["signal_stop_long"]

            if bar["short_signal"] and allow_shorts and short_count < max_layers and long_count == 0:
                pending_short_stop[sym] = bar["signal_stop_short"]

        # ── Mark-to-market ─────────────────────────────────────────────────
        total_eq = cash
        for sym in symbols:
            bar = aligned[sym].iloc[i]
            close_p = bar["Close"]
            sym_open_pnl = sum(
                (close_p - l.entry_price) * l.qty * l.direction * point_value
                for l in active[sym]
            )
            sym_eq = instrument_realised[sym] + sym_open_pnl
            instrument_equity_vals[sym].append(sym_eq)
            total_eq += sym_open_pnl

        portfolio_equity_vals.append(total_eq)

    # ── Build outputs ──────────────────────────────────────────────────────────
    portfolio_equity = pd.Series(portfolio_equity_vals, index=all_dates, name="portfolio")
    daily_returns    = portfolio_equity.pct_change().fillna(0.0)

    instrument_equity_df = pd.DataFrame(
        {sym: pd.Series(instrument_equity_vals[sym], index=all_dates)
         for sym in symbols}
    )

    # Per-instrument P&L contribution (cumulative realised + current open)
    instrument_pnl_df = instrument_equity_df.copy()

    trade_log = pd.DataFrame(closed_trades) if closed_trades else pd.DataFrame(
        columns=["symbol", "entry_date", "exit_date", "direction",
                 "entry_price", "exit_price", "qty", "pnl", "r_multiple",
                 "layer", "exit_reason"]
    )

    # ── Equal-weight buy & hold benchmark ─────────────────────────────────────
    bah_parts = []
    for sym in symbols:
        prices = aligned[sym]["Close"]
        bah_parts.append(prices / prices.iloc[0])
    bah_equity = pd.concat(bah_parts, axis=1).mean(axis=1) * initial_capital
    bah_equity.name = "equal_weight_bah"

    return {
        "portfolio_equity":  portfolio_equity,
        "instrument_equity": instrument_equity_df,
        "instrument_pnl":    instrument_pnl_df,
        "daily_returns":     daily_returns,
        "trade_log":         trade_log,
        "benchmark_equity":  bah_equity,
    }
