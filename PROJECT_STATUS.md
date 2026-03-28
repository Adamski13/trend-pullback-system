# Trend Pullback System (TPS) — Project Status & Development Log

**Last updated:** March 2026
**Author:** Adam (strategy design) + Claude (development partner)
**Repo:** https://github.com/Adamski13/trend-pullback-system

---

## What Is This Project?

A systematic trend-following trading strategy built from scratch through iterative testing. Originally designed as a pullback-reclaim system (v1), it evolved into a Carver-style continuous forecast system (v2) after deep-dive research into the academic and practitioner literature on trend following, signal construction, and position sizing.

The system trades across multiple asset classes (indices, commodities, crypto) via OANDA CFDs with identical parameters.

---

## How We Got Here (Development Journey)

### Starting Point: Volatility Compression Breakout (VCB)
We started with a completely different strategy — a volatility compression breakout system on NAS100 (OANDA, 4H timeframe). This system looked for periods where price range compressed relative to ATR, then entered on breakouts above/below the range.

**Problems discovered:**
- Very few trades on NAS100
- Severely underperformed buy & hold
- Only profitable with a regime filter (4H MA crossover + 170D daily SMA)
- Without the regime filter, the strategy lost money
- Multiple iterations of stop-entry breakouts, partial exits, and trailing stops couldn't fix the fundamental issue

**Key insight #1:** On NAS100, the compression breakout logic added no value. The regime filter alone was doing all the work. We tested this directly — a simple "be long when regime is bullish, flat when not" strategy (no fancy entries at all) produced PF 2.41 and $11.8k profit.

### Testing Improvements on Regime-Only

| Enhancement | Result |
|-------------|--------|
| Regime strength filter (slope + MA separation) | **Worse** — increased churn, reduced PF from 2.41 to 1.5 |
| Volatility scaling (inverse ATR sizing) | **Worse** — reduced profit 29%, barely improved DD |
| Partial exit before MA cross (staged exit) | **Mixed** — reduced DD 36% but cut profits 25% |
| Pyramiding on new highs | **Better** — profit 4×, PF improved, return/DD ratio improved |

**Key insight #2:** On NAS100, the only modification that improved the system was pyramiding. Filters, scaling, and partial exits all hurt performance. The edge is in trend persistence and convexity — not in signal refinement.

### The Fundamental Rethink

**Key insight #3:** Instead of optimizing entries on a single instrument, we needed to build a robust system validated across multiple markets. If it only works on NAS100, it's curve-fitted. If it works on indices, commodities, AND crypto with the same parameters, it's structural.

### Building TPS v1

Redesigned from scratch: 200 SMA regime, 21 EMA pullback-reclaim entry, ATR trailing stops, controlled pyramiding (max 3 layers), per-layer risk management, multi-asset validation with zero parameter changes.

### The v2 Evolution: Research-Driven Redesign

After completing all four validation phases on v1, we conducted a deep-dive literature review covering 7 academic papers, Robert Carver's practitioner framework, and AQR's research. Conclusion: v1's core edge (regime filter + trend persistence) was sound, but signal construction and position sizing could be substantially improved.

### v2 Head-to-Head Results

v1 vs v2 on identical data (2017-08-17 to 2025-12-31, QQQ + GLD + BTC-USD, 0.1% commission + 0.05% slippage):

| Metric | TPS v1 | TPS v2 (daily) | Edge |
|--------|--------|----------------|------|
| Net Return | +362% | +726% | v2 doubles v1 |
| CAGR | 20.1% | ~27% | v2 |
| Sharpe | 1.00 | 0.92 | v1 (slightly) |
| Sortino | 0.91 | 0.98 | v2 |
| Max Drawdown | -23.3% | -26.5% | v1 |
| Trades | 204 | 1,374 | v1 (lower cost) |
| Friction Cost | ~$19k | $134k | v1 |
| QQQ P&L | $63k | $60k | ≈ |
| GLD P&L | $252k | $406k | v2 |
| BTC P&L | $83k | $413k | v2 massively |

**The key finding:** v2 properly unlocks BTC. The continuous EWMAC forecast scales position size with trend strength, capturing BTC's massive 2020-2021 and 2024-2025 bull runs ($83k → $413k).

---

## TPS v2 Specification (Carver-Style Continuous Forecast System)

### What Changed and Why

| Component | v1 | v2 | Why |
|-----------|----|----|-----|
| Signal type | Binary (in/out) | Continuous forecast (0 to 20) | Reduces whipsaw, enables gradual position scaling |
| Entry signal | 21 EMA pullback-reclaim | EWMAC blend at 3 speeds | Multi-speed captures trends at different horizons |
| Position sizing | Fixed 1% risk per trade | Volatility-targeted per instrument | Equal risk contribution; auto-scales during vol spikes |
| Pyramiding | Discrete 3 layers | Forecast-driven (automatic) | Forecast strength drives position size — soft pyramiding |
| Stop losses | ATR-based per layer | Embedded in signal (forecast → 0 = exit) | Gradual exit as trend weakens |
| Regime filter | 200 SMA binary gate | 200 SMA floors forecasts at 0 | Unchanged in effect |
| Rebalancing | Only on new signals | Daily with buffering | Keeps risk aligned with current volatility |

### Signal: EWMAC at 3 Speeds

```
Raw = EMA(fast) - EMA(slow)
Risk-Adjusted = Raw / σ(daily price changes)
Scaled = Risk-Adjusted × Forecast Scalar
Combined = clip(FDM × Σ(weight_i × Scaled_i), 0, +20)
```

Speeds: (8,32) scalar 10.6, (16,64) scalar 7.5, (32,128) scalar 5.3. Equal weights. FDM = 1.15. Floor 0, cap 20.

### Sizing: Volatility Targeting

```
Position = (Capital × VolTarget × InstrWeight × IDM × Forecast/10) / (InstrVol × Price)
```

### Buffering

Only trade when |target − current| > 25% of average position. Primary friction reducer.

---

## v2.1 Optimization Results

### Phase 1: Friction Optimization

**Key finding: Buffer > rebalance frequency.** Daily + 25% buffer is optimal (signal-aware > calendar-aware).

| Config | CAGR | Sharpe | Trades | Friction |
|--------|------|--------|--------|----------|
| Daily + 30% buffer | 30.6% | 1.15 | 570 | $110k |
| Daily + 25% buffer | ~29% | ~1.10 | ~650 | ~$95k |
| Weekly + 10% buffer | ~26% | ~1.00 | 545 | ~$77k |
| Daily + 5% buffer | ~27% | ~0.92 | 2,012 | ~$134k |

### Phase 2: Vol Target Sensitivity

**Key finding: Sharpe flat across all targets (0.99–1.03).** Vol targeting is pure leverage. 20% is sweet spot.

| Vol Target | CAGR | Sharpe | Max DD | Friction |
|------------|------|--------|--------|----------|
| 10% | 13.4% | ~1.00 | -15.3% | $23k |
| 20% | 26.7% | ~1.00 | -26.8% | $90k |
| 30% | 38.6% | ~1.00 | -39.7% | $242k |

### Phase 3: Expanded Universe

USO (0.98), SLV (0.96), EWG (1.01), EWJ (1.01) all fail PF > 1.3. ETH-USD marginal at 1.15. Core portfolio remains NAS100 + Gold + BTC.

---

## Walk-Forward Validation

| Test Window | CAGR | Sharpe | Comment |
|-------------|------|--------|---------|
| 2017–2018 | 1.3% | 0.18 | Weak — flat crypto, choppy equities |
| 2018–2019 | 35.4% | 1.71 | Strong |
| 2019–2020 | 49.1% | 1.72 | Strong — COVID trend |
| 2020–2021 | 29.0% | 1.26 | Solid |
| 2021–2022 | 0.9% | 0.13 | Weakest — crypto crash + rate shock |
| 2022–2023 | 13.9% | 0.75 | Recovering |
| 2023–2024 | 50.7% | 1.69 | Strong |

**7/7 windows positive. Mean OOS Sharpe: 1.06. Verdict: robust.**

---

## Live Infrastructure

### Pine Script v6

**Strategy version:** Full EWMAC with vol-targeted sizing via `strategy.order()`. Validated against OANDA chart data for NAS100USD, XAUUSD, BTCUSD. Debugged `strategy.entry()` pyramiding behavior and Pine v6 `:=` scoping rules.

**Indicator version:** Clean visual dashboard. Bar colors = forecast strength. Background = regime zones. Tiny signal markers (entry/add/reduce/close). Forecast table bottom-right. EMAs off by default. Native timeframe calculations (no `request.security`).

### OANDA Signal Generator (Python)

- `oanda_client.py` — v20 REST API (candles, account, positions, orders)
- `forecast_engine.py` — EWMAC computation (same logic as backtester)
- `generate_signals.py` — daily runner with paper/live modes
- `telegram_notify.py` — phone notifications via Telegram bot
- `monitor.py` — account snapshots and equity logging
- `setup.py` — guided setup wizard (OANDA + Telegram + cron)

Connected to live OANDA account. Tested and verified: fetches candles, reads positions, computes forecasts, sends Telegram notifications.

### Telegram Notifications

Bot created, connected, tested. Sends formatted signal reports with forecast bars, position targets, action labels. Alerts for ENTER/CLOSE signals.

### Automated Scheduling

Cron job: Monday–Friday at 22:05 UTC. Computes forecasts, sends Telegram, logs state. Fully hands-free (Mac must be awake).

---

## Production Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Rebalance | Daily | Signal-aware > calendar-aware |
| Buffer | 25% | ~600 trades, good cost/tracking balance |
| Vol target | 20% | Flat Sharpe, 20% balances return/DD |
| Instruments | NAS100_USD, XAU_USD, BTC_USD | Core 3 pass quality filter (OANDA CFDs) |
| IDM | 1.5 | Standard for 3 instruments |
| EWMAC | (8,32), (16,64), (32,128) | Fast + medium + slow |
| FDM | 1.15 | For 3 forecast variations |
| Forecast cap | 20 | Standard Carver cap |
| Regime | 200 SMA | Floor forecasts at 0 below |

---

## Research That Informed v2

### Academic Papers
1. Moskowitz, Ooi & Pedersen (2012) — "Time Series Momentum" — return persistence across 58 instruments, sign as predictive as magnitude
2. Hurst, Ooi & Pedersen (2017) — "A Century of Evidence" — trend following profitable since 1880, signal methodology matters less than diversification
3. Harvey et al. (2018) — "Impact of Volatility Targeting" — improves Sharpe for equities/credit, reduces left-tail events
4. Baltas & Kosowski (2015) — "Demystifying Time-Series Momentum" — risk-adjusting signals by instrument vol is essential
5. Shi & Lian (2025) — "Trend Following: A Practical Guide" — multi-scale blending is most robust approach
6. Winton Capital (2013) — intermediate-speed Sharpe 1.12 vs 0.87/0.81 for fast/slow
7. CFM (2014) — "Two Centuries of Trend Following" — 200 years, Sharpe 0.72, "10 sigma" phenomenon

### Practitioner: Robert Carver
Continuous forecasts, forecast scaling/capping, multiple speed blending, vol-targeted sizing, trade buffering, no separate stop losses.

### Partial Profits & Break-Even Stops
Both hurt trend following. Partials cap positive skew. Break-even worsens EV at every trigger point. v2's forecast-driven sizing replaces all three (partials, break-even, pyramiding) with one mechanism.

---

## The Honest Assessment

### What's genuinely good:
- Structural mechanism across asset classes with identical parameters
- 81/81 parameter combos profitable (not curve-fitted)
- v2 doubled v1 returns (+726% vs +362%)
- Walk-forward validated: 7/7 OOS windows positive, mean Sharpe 1.06
- Edge documented across 200 years by independent research teams
- Full live infrastructure operational: OANDA + Telegram + cron

### What you must understand:
1. You will lose more often than you win (35–45% win rate)
2. Returns are concentrated in trending periods (next big trend may be 2–3 years away)
3. Won't consistently beat equity buy & hold (advantage is risk-adjusted)
4. Requires patience measured in years
5. Concentration risk persists (whichever instruments trend will dominate)
6. Higher friction than v1 (even optimized: ~$90-110k vs ~$19k)
7. Capital constraint: vol-targeted sizing needs ~$50k+ for whole-unit positions on expensive instruments

---

## Roadmap

### ✅ Completed
- [x] VCB breakout system (abandoned — edge was in regime)
- [x] TPS v1 design, backtester, 4-phase validation
- [x] v1 Portfolio simulation (QQQ + GLD + BTC-USD)
- [x] Deep-dive research review (7 papers, Carver framework)
- [x] Research on partial profits and break-even stops
- [x] TPS v2 design and backtester (EWMAC + vol targeting)
- [x] v1 vs v2 head-to-head (v2 doubles v1)
- [x] v2.1 Friction optimization (buffer > rebalance frequency)
- [x] v2.1 Vol target sensitivity (Sharpe flat, 20% sweet spot)
- [x] v2.1 Expanded universe (all fail quality filter)
- [x] Walk-forward validation (7/7 OOS windows positive)
- [x] Pine Script v6 strategy (validated on OANDA charts)
- [x] Pine Script v6 indicator (clean visual dashboard)
- [x] Python signal generator (OANDA v20 API)
- [x] OANDA live account connection (tested, verified)
- [x] Telegram notifications (working)
- [x] Automated cron scheduling (Mon-Fri 22:05 UTC)
- [x] All code committed to GitHub

### 🔄 In Progress
- [ ] Paper trading — running signals daily, logging, building track record
- [ ] Fund OANDA account (£500 initial)

### 📋 Planned
- [ ] Manual live trading (minimum-size trades based on Telegram signals)
- [ ] Auto-execution (switch to `--live` flag when confident)
- [ ] Donchian breakout rules (signal diversification)
- [ ] Intraday system (separate project, separate edge)

### 💡 Future
- Short-side testing on commodities/crypto
- Carry signal addition (Carver's second rule)
- Monte Carlo drawdown simulation
- Dashboard/app for equity curve visualization
- Scale to proper vol-targeted sizing at £5-10k+

---

## Key Lessons Learned (20 insights)

### From v1 (1-7)
1. Regime filter is the edge, not the entry signal
2. Simpler is better
3. Validate across markets before optimizing
4. Additions that reduce participation usually hurt
5. Pyramiding works with positive expectancy
6. Position sizing matters more than entry timing
7. Accept what the system is

### From v2 Research (8-13)
8. Signal methodology matters less than speed and diversification
9. Continuous forecasts beat binary signals
10. Volatility-targeted sizing is the biggest upgrade available
11. Partial profits and break-even stops hurt trend following
12. Time series momentum is a 200-year phenomenon
13. Instrument diversification beats everything else

### From v2.1 Optimization (14-17)
14. Trade buffering beats rebalance frequency — signal-aware > calendar-aware
15. Sharpe is flat across vol targets — vol targeting is pure leverage
16. Not everything trends — the system correctly rejects instruments that don't
17. Continuous sizing unlocks crypto — BTC from $83k to $413k

### From Going Live (18-20)
18. CFD contract specs matter — XAUUSD lot sizing differs from ETF shares; sizing formula must account for broker's unit definition
19. Pine Script can't manage multi-instrument portfolios — Python signal generator is the correct tool for portfolio-level sizing
20. Capital is the real constraint — system works, infrastructure is built, signals are live; below ~£5k, trade minimum sizes manually until capital grows

---

## References

### Academic Papers
1. Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H. (2012). "Time Series Momentum." *JFE*, 104(2), 228-250.
2. Hurst, B., Ooi, Y.H., Pedersen, L.H. (2017). "A Century of Evidence on Trend-Following Investing." *JPM*, 44(1).
3. Harvey, C.R. et al. (2018). "The Impact of Volatility Targeting." Man Group.
4. Baltas, A.N., Kosowski, R. (2015). "Demystifying Time-Series Momentum Strategies." Imperial College.
5. Shi, C., Lian, X. (2025). "Trend Following Strategies: A Practical Guide." SSRN.
6. CFM (2014). "Two Centuries of Trend Following."
7. Moreira, A., Muir, T. (2017). "Volatility-Managed Portfolios." *JF*, 72(4).

### Practitioner Sources
8. Carver, R. (2015). *Systematic Trading.* Harriman House.
9. Carver, R. (2023). *Advanced Futures Trading Strategies.* Harriman House.
10. Carver, R. Blog: qoppac.blogspot.com
11. GitHub: github.com/robcarver17/pysystemtrade
12. Concretum Group (2025). "Position Sizing in Trend-Following."
