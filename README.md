# Trend Pullback System (TPS) v1

A Python backtesting framework for a systematic trend-following strategy based on 200 SMA regime filtering and 21 EMA pullback/reclaim entries with per-layer trailing stops.

## Strategy Logic

| Component | Rule |
|-----------|------|
| **Regime** | Long only when `Close > SMA(200)` |
| **Entry** | Pullback below EMA(21) → reclaim above EMA(21) → enter long at next bar open |
| **Stop** | `pullback_low - 0.5 × ATR(14)` per layer |
| **Pyramiding** | Up to 3 layers; each requires a fresh pullback cycle |
| **Trail** | `highest_close_since_entry - 2.5 × ATR(14)`, ratchets up only |
| **Regime exit** | Close all immediately if price breaks below SMA(200) |

## Project Structure

```
trend-pullback-system/
├── config/default_config.yaml   # Strategy parameters
├── src/
│   ├── data_loader.py           # yfinance download + disk cache
│   ├── indicators.py            # SMA, EMA, ATR
│   ├── strategy.py              # Signal generation (no look-ahead)
│   ├── backtester.py            # Bar-by-bar simulation engine
│   ├── metrics.py               # 25+ performance metrics
│   └── visualizer.py           # Equity curve, drawdown, heatmap
├── tests/test_strategy.py       # Unit tests
└── run_backtest.py              # CLI entry point
```

## Quick Start

```bash
pip install -r requirements.txt

# Phase 1 — single instrument validation
python run_backtest.py --symbol QQQ

# Phase 2 — cross-market robustness
python run_backtest.py

# Phase 3 — sensitivity analysis (QQQ)
python run_backtest.py --sensitivity

# Phase 4 — realistic frictions
python run_backtest.py --commission 0.1 --slippage 0.05

# Run unit tests
python -m pytest tests/ -v
```

## Configuration

Edit `config/default_config.yaml` or pass overrides via CLI flags:

```bash
python run_backtest.py \
  --symbol QQQ \
  --start 2015-01-01 \
  --capital 100000 \
  --commission 0.1 \
  --slippage 0.05
```

## Output

Each run saves to `results/<SYMBOL>/`:
- `<SYMBOL>_equity.png` — equity curve vs buy & hold
- `<SYMBOL>_drawdown.png` — drawdown over time
- `<SYMBOL>_monthly.png` — monthly returns heatmap
- `<SYMBOL>_trades.csv` — full trade log
- `<SYMBOL>_equity.csv` — daily equity series

## Metrics Computed

CAGR · Net P&L · Max Drawdown · Sharpe · Sortino · Profit Factor · Win Rate · Avg Win/Loss · Largest Win/Loss · Max Consecutive Wins/Losses · Avg Holding Period · Trades/Year · Time in Market · Buy & Hold comparison

## Testing Protocol

| Phase | Description |
|-------|-------------|
| 1 | Single instrument: QQQ 2015–present |
| 2 | Cross-market: QQQ, SPY, GLD, BTC-USD — same parameters |
| 3 | Sensitivity sweep: vary regime MA, EMA, trail mult, risk % |
| 4 | Add realistic frictions: commission 0.1%, slippage 0.05% |

## Version

- **v1.0** — Daily timeframe, 200 SMA regime, 21 EMA pullback/reclaim, per-layer trailing stops, max 3 layers at 1% risk each.
