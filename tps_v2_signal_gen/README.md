# TPS v2 — OANDA Signal Generator

Fetches prices directly from OANDA, computes EWMAC forecasts and
vol-targeted positions for XAU_USD, NAS100_USD, and BTC_USD, and
optionally executes orders.

---

## Setup

```bash
tar -xzf tps_v2_signal_generator.tar.gz
cd tps_v2_signal_gen
pip install -r requirements.txt
```

Set credentials (get your token at OANDA → Settings → Manage API Access):

```bash
export OANDA_TOKEN="your-token-here"
export OANDA_ACCOUNT="001-001-XXXXXXXX-001"
export OANDA_ENV="practice"   # or "live"
```

---

## Usage

```bash
# Paper mode — report only, no orders placed (default)
python generate_signals.py

# Live mode — executes orders on OANDA
python generate_signals.py --live

# Override sizing capital (useful if you run multiple accounts)
python generate_signals.py --capital 75000
python generate_signals.py --capital 75000 --live
```

---

## What It Does

1. Fetches your OANDA account NAV (used as sizing capital)
2. Fetches 400 daily candles per instrument directly from OANDA
3. Computes EWMAC(8/32), EWMAC(16/64), EWMAC(32/128) → blends → forecast 0–20
4. Checks 200-day SMA regime filter (below SMA → forecast = 0)
5. Applies Carver position sizing formula:
   `N = (NAV × 20% vol target × weight × IDM × forecast/10) / (inst_vol × price)`
6. Compares target vs current position; applies 30% buffer to suppress noise trades
7. Prints a full signal report
8. If `--live`: places market orders for any instruments outside the buffer

---

## Sample Output

```
========================================================================
  TPS v2 — OANDA Signal Report
  2026-03-28  |  Account: 001-001-XXXXXXXX-001  |  PRACTICE
========================================================================

  Account NAV:     $    52,430.18
  Balance:         $    52,000.00
  Unrealized P&L:  +$      430.18

  ────────────────────────────────────────────────────────────────────
  XAU_USD  (Gold)
  ────────────────────────────────────────────────────────────────────
  Price:   $     3,125.40   SMA200: $     2,850.20   Regime: ▲ BULL
  Vol:      14.2% annual

  Forecast:   12.4 / 20  [████████████░░░░░░░░]  MODERATE
    8/32:    14.10    16/64:   11.20    32/128:    9.80

  Position:
    Target:      44 units  (~$     137,518 notional)
    Current:     38 units  (~$     118,765 notional)
    Delta:       +6 units  |  Buffer: ±5.7 units (30% × avg 19.0)

  ✦ BUY 6 XAU_USD  [PAPER — not executed]
```

---

## Configuration

Edit `config.yaml` to change instruments, vol target, IDM, or buffer.

### Changing Instruments

OANDA instrument codes use underscore format. Common examples:

| Market | OANDA Code |
|--------|-----------|
| Gold | `XAU_USD` |
| Silver | `XAG_USD` |
| Nasdaq 100 | `NAS100_USD` |
| S&P 500 | `SPX500_USD` |
| Bitcoin | `BTC_USD` |
| Ethereum | `ETH_USD` |
| Crude Oil | `WTICO_USD` |

> **Note:** Some accounts use `US100_USD` instead of `NAS100_USD` for Nasdaq.
> Check your available instruments in the OANDA platform.

### Instrument Weights

If you change the number of instruments, update:
- `weight` for each instrument (must sum to ~1.0)
- `instrument_div_multiplier` (IDM):
  - 1 instrument: 1.0
  - 2 instruments: 1.2
  - 3 instruments: 1.5
  - 4 instruments: 1.7
  - 5+ instruments: 2.0

### Vol Target

`vol_target_pct: 0.20` (20%) is the production-tested default.
- Increase to 0.25–0.30 for higher returns + higher drawdown
- Decrease to 0.10–0.15 for lower risk

---

## Important Notes

**OANDA units vs lots:** This script outputs OANDA API units, not broker lots.
For XAU_USD, 1 unit = 1 troy oz of gold. For NAS100_USD, 1 unit = 1 CFD unit
(where P&L = index points × units in USD). Verify contract specs with OANDA
before live trading.

**BTC on small accounts:** At ~$66k/BTC with 44% vol, the target position
rounds to 0 for accounts under ~$250k. This is correct behaviour — the formula
is protecting you from oversized BTC exposure. With a larger account or lower
BTC price, BTC positions will appear.

**Multi-instrument sizing in TradingView:** The Pine Script strategy on
TradingView sizes each instrument independently based on that chart's equity.
This Python generator is the correct way to size a real multi-instrument
portfolio from a single account NAV.

**State file:** `state.json` stores the running average position per instrument
(used for buffer calculation). Delete it to reset if you change instruments.

---

## Daily Workflow

```bash
# Every day after market close:
python generate_signals.py

# If actions are shown:
python generate_signals.py --live
```

Takes ~10 seconds. Most days: "no trades — all instruments within buffer."
