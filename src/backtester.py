"""
Bar-by-bar backtesting engine.

Execution rules (mirrors the spec):
  - Signal fires on bar i close
  - Entry executes at bar i+1 open
  - Stops checked on bar i+1 using bar i+1 low/high
  - Regime-break exit uses bar i+1 open (same as any other exit)
  - Gap-through: if bar opens beyond stop, fill at open
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Layer:
    layer_num: int
    direction: int          # 1 = long, -1 = short
    entry_date: pd.Timestamp
    entry_price: float
    qty: float
    initial_stop: float
    stop: float             # current (ratcheting) stop
    trail_extreme: float    # highest close for longs, lowest close for shorts
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0


def _round_qty(qty: float, qty_step: float = 1.0) -> float:
    if qty_step <= 0:
        return qty
    return float(int(qty / qty_step) * qty_step)


def run_backtest(signals_df: pd.DataFrame, cfg: dict) -> dict:
    """
    Simulate the strategy bar by bar.

    Returns:
        {
          "equity_curve": pd.Series,
          "trade_log": pd.DataFrame,
          "daily_returns": pd.Series,
        }
    """
    s = cfg["strategy"]
    ex = cfg["execution"]

    initial_capital: float = ex["initial_capital"]
    commission_pct: float = ex.get("commission_pct", 0.0) / 100.0
    slippage_pct: float = ex.get("slippage_pct", 0.0) / 100.0
    point_value: float = ex.get("point_value", 1.0)
    risk_pct: float = s["risk_per_layer_pct"] / 100.0
    trail_mult: float = s["trail_atr_mult"]
    max_layers: int = s["max_layers"]
    allow_shorts: bool = s.get("allow_shorts", False)

    df = signals_df.reset_index(drop=False)   # keep Date as column
    date_col = df.columns[0]                  # usually 'Date' or 'index'
    n = len(df)

    equity = initial_capital
    equity_curve: List[float] = []
    active_layers: List[Layer] = []
    closed_trades: List[dict] = []

    # Pending orders: set on signal bar, executed at next bar open
    pending_long_stop: Optional[float] = None
    pending_short_stop: Optional[float] = None

    def _fill_price(raw: float, direction: int) -> float:
        """Apply slippage (adverse) to fill price."""
        if direction == 1:
            return raw * (1 + slippage_pct)
        return raw * (1 - slippage_pct)

    def _commission(price: float, qty: float) -> float:
        return price * qty * commission_pct

    def _close_layer(layer: Layer, price: float, date: pd.Timestamp, reason: str) -> float:
        """Close a layer, return realised P&L."""
        fill = _fill_price(price, -layer.direction)
        gross = (fill - layer.entry_price) * layer.qty * layer.direction * point_value
        cost = _commission(fill, layer.qty) + _commission(layer.entry_price, layer.qty)
        pnl = gross - cost
        layer.exit_date = date
        layer.exit_price = fill
        layer.exit_reason = reason
        layer.pnl = pnl
        r_multiple = (fill - layer.entry_price) / abs(layer.entry_price - layer.initial_stop) * layer.direction if layer.initial_stop != layer.entry_price else 0.0
        closed_trades.append({
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

    for i in range(n):
        bar = df.iloc[i]
        date = bar[date_col]
        open_p = bar["Open"]
        high_p = bar["High"]
        low_p = bar["Low"]
        close_p = bar["Close"]
        atr_val = bar["atr14"] if not np.isnan(bar["atr14"]) else 0.0

        # ── 1. Execute pending entries at this bar's open ──────────────────────
        long_layers = [l for l in active_layers if l.direction == 1]
        short_layers = [l for l in active_layers if l.direction == -1]

        if pending_long_stop is not None and len(long_layers) < max_layers:
            stop = pending_long_stop
            fill = _fill_price(open_p, 1)
            if stop >= fill:
                # Stop is already above entry — skip (degenerate case)
                pending_long_stop = None
            else:
                stop_dist = abs(fill - stop)
                if stop_dist > 0:
                    raw_qty = (equity * risk_pct) / (stop_dist * point_value)
                    qty = _round_qty(raw_qty)
                    if qty > 0:
                        cost = _commission(fill, qty)
                        equity -= cost
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
                        active_layers.append(layer)
            pending_long_stop = None

        if pending_short_stop is not None and allow_shorts and len(short_layers) < max_layers:
            stop = pending_short_stop
            fill = _fill_price(open_p, -1)
            if stop <= fill:
                pending_short_stop = None
            else:
                stop_dist = abs(fill - stop)
                if stop_dist > 0:
                    raw_qty = (equity * risk_pct) / (stop_dist * point_value)
                    qty = _round_qty(raw_qty)
                    if qty > 0:
                        cost = _commission(fill, qty)
                        equity -= cost
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
                        active_layers.append(layer)
            pending_short_stop = None

        # ── 2. Regime-break exit ───────────────────────────────────────────────
        regime_bull = bar["regime_bull"]
        regime_bear = bar["regime_bear"]

        to_remove = []
        for layer in active_layers:
            regime_ok = (layer.direction == 1 and regime_bull) or \
                        (layer.direction == -1 and regime_bear)
            if not regime_ok:
                pnl = _close_layer(layer, open_p, date, "regime_break")
                equity += pnl
                to_remove.append(layer)
        for l in to_remove:
            active_layers.remove(l)

        # ── 3. Update trailing stops and check stop hits ───────────────────────
        to_remove = []
        for layer in active_layers:
            # Update trail extreme (ratchet)
            if layer.direction == 1:
                layer.trail_extreme = max(layer.trail_extreme, close_p)
                trail_stop = layer.trail_extreme - trail_mult * atr_val
                layer.stop = max(layer.initial_stop, trail_stop)

                # Check if stop hit (use low)
                hit_price = min(low_p, layer.stop)  # gap-through: open could be worse
                if open_p <= layer.stop:
                    # Gapped through or opened at/below stop
                    exit_p = open_p
                    pnl = _close_layer(layer, exit_p, date, "stop_hit")
                    equity += pnl
                    to_remove.append(layer)
                elif low_p <= layer.stop:
                    pnl = _close_layer(layer, layer.stop, date, "stop_hit")
                    equity += pnl
                    to_remove.append(layer)

            else:  # short
                layer.trail_extreme = min(layer.trail_extreme, close_p)
                trail_stop = layer.trail_extreme + trail_mult * atr_val
                layer.stop = min(layer.initial_stop, trail_stop)

                if open_p >= layer.stop:
                    pnl = _close_layer(layer, open_p, date, "stop_hit")
                    equity += pnl
                    to_remove.append(layer)
                elif high_p >= layer.stop:
                    pnl = _close_layer(layer, layer.stop, date, "stop_hit")
                    equity += pnl
                    to_remove.append(layer)

        for l in to_remove:
            active_layers.remove(l)

        # ── 4. Mark-to-market equity for curve ────────────────────────────────
        open_pnl = sum(
            (close_p - l.entry_price) * l.qty * l.direction * point_value
            for l in active_layers
        )
        equity_curve.append(equity + open_pnl)

        # ── 5. Queue signals for NEXT bar ──────────────────────────────────────
        long_count = sum(1 for l in active_layers if l.direction == 1)
        short_count = sum(1 for l in active_layers if l.direction == -1)

        if bar["long_signal"] and long_count < max_layers and (short_count == 0):
            pending_long_stop = bar["signal_stop_long"]

        if bar["short_signal"] and allow_shorts and short_count < max_layers and (long_count == 0):
            pending_short_stop = bar["signal_stop_short"]

    # ── Build output objects ──────────────────────────────────────────────────
    dates = pd.to_datetime(df[date_col])
    equity_series = pd.Series(equity_curve, index=dates, name="equity")
    daily_returns = equity_series.pct_change().fillna(0.0)

    trade_log = pd.DataFrame(closed_trades) if closed_trades else pd.DataFrame(
        columns=["entry_date", "exit_date", "direction", "entry_price",
                 "exit_price", "qty", "pnl", "r_multiple", "layer", "exit_reason"]
    )

    return {
        "equity_curve": equity_series,
        "trade_log": trade_log,
        "daily_returns": daily_returns,
    }
