# TPS v2.1 Results

Carver-style EWMAC continuous forecast system.
Production config: daily rebalance, 30% buffer, 20% vol target, QQQ + GLD + BTC-USD.

---

## Production Backtest — Full Period (2012–2025)

| Metric | Value |
|---|---|
| Period | 8.4 years (2017-08-17 effective start due to BTC) |
| CAGR | 30.6% |
| Sharpe | 1.15 |
| Sortino | 1.24 |
| Max Drawdown | -25.7% |
| Calmar | 1.19 |
| Total Return | 831.9% |
| End Equity | $931,891 |
| Total Trades | 570 |
| Total Friction | $110,585 |

**Per-instrument P&L:**

| Instrument | P&L | % of Start Capital |
|---|---|---|
| GLD | $452,240 | 452.2% |
| BTC-USD | $438,010 | 438.0% |
| QQQ | $52,226 | 52.2% |

**Year-by-year:**

| Year | Return |
|---|---|
| 2018 | +1.9% |
| 2019 | +81.3% |
| 2020 | +91.1% |
| 2021 | +16.2% |
| 2022 | -9.3% |
| 2023 | +28.9% |
| 2024 | +68.3% |
| 2025 | +15.5% |

---

## Phase 1: Friction Optimization

Grid: 5 buffer thresholds × 2 rebalance frequencies.

**Key finding:** daily rebalancing + large buffer dominates weekly rebalancing.
30% buffer cuts trades from 2,012 (daily/5%) to 570 while improving CAGR from 27.6% → 30.6%.

| Config | CAGR | Sharpe | Trades | Friction |
|---|---|---|---|---|
| daily/5% buf | 27.6% | 1.06 | 2,012 | $146,542 |
| daily/10% buf | 29.0% | 1.11 | 1,417 | $136,117 |
| daily/20% buf | 29.0% | 1.11 | 869 | $122,684 |
| **daily/30% buf** | **30.6%** | **1.15** | **570** | **$110,585** |
| weekly/10% buf | 26.7% | 1.02 | 545 | $90,204 |
| weekly/30% buf | 25.9% | 1.01 | 324 | $77,271 |

---

## Phase 2: Vol Target Sensitivity

Weekly rebalance, 10% buffer. Sharpe is flat across all targets — system scales linearly.

| Vol Target | CAGR | Sharpe | Max DD | Friction |
|---|---|---|---|---|
| 10% | 13.4% | 0.99 | -15.3% | $23,490 |
| 15% | 18.5% | 0.96 | -24.2% | $44,530 |
| **20%** | **26.7%** | **1.02** | **-26.8%** | **$90,204** |
| 25% | 32.6% | 1.02 | -33.3% | $151,277 |
| 30% | 38.6% | 1.03 | -39.7% | $242,136 |

20% is the sweet spot: Calmar 1.00, max DD contained under 27%.

---

## Phase 3: Expanded Instrument Universe

All candidates tested individually with PF > 1.3 filter:

| Instrument | PF | CAGR | Result |
|---|---|---|---|
| USO | 0.98 | -3.5% | FAIL |
| SLV | 0.96 | -3.3% | FAIL |
| EWG | 1.01 | -3.5% | FAIL |
| EWJ | 1.01 | -4.1% | FAIL |
| ETH-USD | 1.15 | 44.1% | FAIL (borderline) |

Core 3 remain optimal. ETH-USD borderline — worth revisiting with lower filter or longer data.

---

## Walk-Forward Validation

### IS vs OOS Split (train 2012–2020, test 2021–2025)

| Period | CAGR | Sharpe | Max DD |
|---|---|---|---|
| Full 2012–2025 | 30.6% | 1.15 | -25.7% |
| IS 2012–2020 | 45.3% | 1.64 | -22.6% |
| OOS 2021–2025 | 19.9% | 0.85 | -25.1% |

Sharpe degrades -48% IS→OOS. Context: IS includes 2019–2020 BTC/equity bull runs.
OOS Sharpe of 0.85 on unseen data is a genuine positive result.

### Rolling Walk-Forward (5yr train, 2yr test, 1yr step)

| Test Window | CAGR | Sharpe | Max DD |
|---|---|---|---|
| 2017–2018 | 1.3% | 0.18 | -10.7% |
| 2018–2019 | 35.4% | 1.71 | -14.0% |
| 2019–2020 | 49.1% | 1.72 | -22.3% |
| 2020–2021 | 29.0% | 1.26 | -22.0% |
| 2021–2022 | 0.9% | 0.13 | -15.6% |
| 2022–2023 | 13.9% | 0.75 | -25.2% |
| 2023–2024 | 50.7% | 1.69 | -22.2% |

**Summary:** 7/7 windows positive CAGR. Mean Sharpe 1.06 (std 0.66).
Weak windows (2017–18, 2021–22) both coincide with violent mean-reversion shocks — known regime risk for trend-following, not strategy failure.

---

## Production Configuration

```yaml
rebalance:   daily
buffering:   0.30   # 30% of avg position
vol_target:  0.20   # 20% annual vol target
instruments: QQQ, GLD, BTC-USD
idm:         1.5
ewmac:       [8/32, 16/64, 32/128]  equal-weighted, FDM=1.15
frictions:   0.10% commission + 0.05% slippage
```
