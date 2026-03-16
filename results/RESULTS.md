# Trend Pullback System v1 — Backtest Results

**Strategy:** 200 SMA regime filter + 21 EMA pullback/reclaim entries, per-layer trailing stops (2.5× ATR), max 3 pyramid layers at 1% risk each.
**Data:** Daily bars via stooq/Binance. Period: 2015-01-01 to 2026-03-15.
**Capital:** $100,000 starting equity.

---

## Phase 1 — Single Instrument Validation (QQQ, default config, no frictions)

> Goal: Verify trade count is reasonable, entries match logic, stops are placed correctly.

| Metric | Value |
|--------|-------|
| Net P&L | +$52,601 (+52.6%) |
| CAGR | 3.85% |
| Max Drawdown | -$13,492 (-9.13%) |
| Return / Max DD | 5.76 |
| Sharpe | 0.70 |
| Sortino | 0.59 |
| Total Trades | 104 (~9.3/yr) |
| Win Rate | 46.2% |
| Profit Factor | 2.20 |
| Avg Win | +$2,091 |
| Avg Loss | -$813 |
| Avg Holding | 31.1 days |
| Time in Market | 50.2% |
| B&H Return | +524.8% |
| B&H Max Drawdown | -35.1% |

**Verdict:** ✅ Pass. Trade count reasonable (9/yr). System spends ~50% of time in market, drawdown less than 1/3 of buy-and-hold.

---

## Phase 2 — Cross-Market Robustness (default config, no frictions)

> Goal: Same parameters across 4 instruments. 3+ profitable = structural edge.

| Symbol | Net P&L % | CAGR | Max DD | Sharpe | Profit Factor | Trades | B&H Return |
|--------|-----------|------|--------|--------|---------------|--------|------------|
| QQQ | +52.6% | 3.85% | -9.1% | 0.70 | 2.20 | 104 | +524.8% |
| SPY | +21.8% | 1.78% | -10.8% | 0.37 | 1.39 | 116 | +284.9% |
| GLD | +97.3% | 6.26% | -18.2% | 0.73 | 3.07 | 115 | +304.0% |
| BTC-USD | +29.0% | 3.01% | -9.5% | 0.51 | 3.74 | 24 | +1560.4% |

**Verdict:** ✅ Pass. **4/4 instruments profitable with zero parameter changes.** Edge is structural.
GLD strongest (CAGR 6.26%, PF 3.07). BTC most efficient per trade (PF 3.74). SPY weakest but still positive.

---

## Phase 3 — Sensitivity Analysis (QQQ, 81 parameter combinations, no frictions)

> Goal: System should profit across a range of settings, not only at one point.

**Parameters swept:**

| Parameter | Values Tested |
|-----------|--------------|
| Regime MA | 150, 200, 250 |
| EMA length | 15, 21, 30 |
| Trail ATR mult | 2.0, 2.5, 3.0 |
| Risk per layer | 0.5%, 1.0%, 1.5% |

**Distribution across 81 combos:**

| Metric | Min | Median | Max |
|--------|-----|--------|-----|
| Net P&L % | +10.6% | +51.2% | +315.6% |
| CAGR | 0.90% | 3.76% | 13.6% |
| Sharpe | 0.42 | 0.70 | 0.97 |
| Profit Factor | 1.51 | 2.22 | 4.44 |
| Max Drawdown | -3.6% | -9.1% | -17.2% |

**Profitable combinations: 81/81 (100%)**

**Parameter sensitivity (mean metrics by value):**

*EMA length — biggest lever:*
| EMA | CAGR | Sharpe | PF |
|-----|------|--------|----|
| 15 | 7.1% | 0.87 | 3.04 |
| 21 | 3.7% | 0.70 | 2.22 |
| 30 | 2.5% | 0.50 | 1.77 |

*Trail ATR mult — more room, more returns, more DD:*
| Trail | CAGR | Sharpe | Avg Max DD |
|-------|------|--------|------------|
| 2.0× | 3.5% | 0.68 | -8.4% |
| 2.5× | 4.4% | 0.68 | -9.6% |
| 3.0× | 5.4% | 0.71 | -10.2% |

*Regime MA — nearly invariant:*
| SMA | CAGR | Sharpe |
|-----|------|--------|
| 150 | 4.4% | 0.67 |
| 200 | 4.4% | 0.68 |
| 250 | 4.6% | 0.72 |

*Risk per layer — clean linear scaling (as expected):*
| Risk | CAGR | Avg Max DD |
|------|------|------------|
| 0.5% | 2.3% | -5.1% |
| 1.0% | 4.5% | -9.5% |
| 1.5% | 6.6% | -13.5% |

**Best combo found:** EMA=15, Trail=3.0×, SMA=250, Risk=1.5% → CAGR 13.6%, Sharpe 0.97, PF 3.6

**Verdict:** ✅ Pass. No cliff edges. System works across the full parameter space.

---

## Phase 4 — Realistic Frictions (commission 0.1% + slippage 0.05%)

### 4a: Default config + frictions

| Symbol | CAGR clean → friction | Sharpe clean → friction | PF clean → friction | Survives? |
|--------|-----------------------|-------------------------|---------------------|-----------|
| QQQ | 3.85% → 2.52% | 0.70 → 0.46 | 2.20 → 1.80 | ✅ Yes |
| SPY | 1.78% → -0.06% | 0.37 → 0.01 | 1.39 → 1.07 | ❌ No |
| GLD | 6.26% → 4.02% | 0.73 → 0.49 | 3.07 → 2.18 | ✅ Yes |
| BTC-USD | 3.01% → 2.92% | 0.51 → 0.49 | 3.74 → 3.85 | ✅ Yes |

### 4b: Best combo (EMA=15, Trail=3.0, SMA=250, Risk=1.5%) + frictions

| Symbol | Net P&L | CAGR | Max DD | Sharpe | PF | vs 4a |
|--------|---------|------|--------|--------|-----|-------|
| QQQ | +225.2% | 11.1% | -16.3% | 0.83 | 3.07 | ✅ Much better |
| SPY | +35.1% | 2.73% | -21.6% | 0.30 | 1.41 | ✅ Now survives |
| GLD | +20.8% | 1.71% | -41.3% | 0.19 | 1.28 | ❌ Worse — EMA=15 too noisy for gold |
| BTC-USD | +119.8% | 9.62% | -26.3% | 0.64 | 2.86 | ✅ Much better |

**Verdict:** 3/4 survive realistic frictions with default config. SPY is the only casualty — insufficient edge after costs. The "best combo" is instrument-specific: excellent for QQQ/BTC, harmful for GLD (shorter EMA generates too many trades on gold's choppier pullbacks).

---

## Overall Conclusions

| Finding | Detail |
|---------|--------|
| **Structural edge** | 4/4 instruments profitable before frictions, 3/4 after |
| **Not curve-fitted** | 81/81 parameter combos profitable on QQQ |
| **SPY is marginal** | Thin edge disappears under realistic transaction costs |
| **GLD is the standout** | Best risk-adjusted returns with default config |
| **BTC is resilient** | High PF, friction-immune (low trade frequency) |
| **Regime filter works** | System drawdowns 2–9× smaller than buy-and-hold |
| **Optimal default config** | EMA=21, Trail=2.5×, SMA=200, Risk=1% — most robust across markets |
| **Aggressive config** | EMA=15, Trail=3.0×, Risk=1.5% — best for QQQ/BTC, avoid for GLD |

---

## File Index

```
results/
├── RESULTS.md                          ← this file
├── phase1/QQQ/                         ← single instrument validation
├── phase2/{QQQ,SPY,GLD,BTC-USD}/       ← cross-market, default config, no frictions
├── phase3/sensitivity/                 ← 81-combo parameter sweep (QQQ)
└── phase4/
    ├── default_friction/{symbols}/     ← default config + 0.1% comm + 0.05% slip
    └── best_friction/{symbols}/        ← EMA=15/Trail=3.0/Risk=1.5% + frictions
```

Each symbol directory contains:
- `{SYMBOL}_equity.png` — equity curve vs buy & hold
- `{SYMBOL}_drawdown.png` — drawdown chart
- `{SYMBOL}_monthly.png` — monthly returns heatmap
- `{SYMBOL}_trades.csv` — full trade log
- `{SYMBOL}_equity.csv` — daily equity series
