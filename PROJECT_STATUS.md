# Trend Pullback System (TPS) — Project Status & Development Log

**Last updated:** March 2026
**Author:** Adam (strategy design) + Claude (development partner)
**Repo:** https://github.com/Adamski13/trend-pullback-system

---

## What Is This Project?

A systematic trend-following trading strategy built from scratch through iterative testing. The system buys pullbacks to the 21 EMA during confirmed uptrends (price above 200 SMA), pyramids into winners, and exits via ATR trailing stops. It was designed to work across multiple asset classes (indices, commodities, crypto) with identical parameters.

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
We then tried to improve the regime-only system with:

| Enhancement | Result |
|-------------|--------|
| Regime strength filter (slope + MA separation) | **Worse** — increased churn, reduced PF from 2.41 to 1.5 |
| Volatility scaling (inverse ATR sizing) | **Worse** — reduced profit 29%, barely improved DD |
| Partial exit before MA cross (staged exit) | **Mixed** — reduced DD 36% but cut profits 25% |
| Pyramiding on new highs | **Better** — profit 4×, PF improved, return/DD ratio improved |

**Key insight #2:** On NAS100, the only modification that improved the system was pyramiding. Filters, scaling, and partial exits all hurt performance. The edge is in trend persistence and convexity — not in signal refinement.

### The Fundamental Rethink
At this point we stepped back and questioned everything:

1. The regime filter (4H MA crossover) was the entire edge, not the entries
2. MA crossovers are reactive and introduce unnecessary lag
3. The system was only tested on one instrument (NAS100 on OANDA)
4. We might be overfitting to Nasdaq's structural upward drift
5. A 4H crossover system is really just a trend filter with position sizing

**Key insight #3:** Instead of optimizing entries on a single instrument, we needed to build a robust system validated across multiple markets. If it only works on NAS100, it's curve-fitted. If it works on indices, commodities, AND crypto with the same parameters, it's structural.

### Building TPS v1 (Current System)
We redesigned from scratch with these principles:

- **Simple regime:** 200 SMA (institutional standard, not fitted)
- **Clean entry:** Pullback below 21 EMA then reclaim above it
- **Mechanical exit:** ATR trailing stop (no MA-based exits — those were proven too laggy)
- **Controlled pyramiding:** Each add requires a fresh pullback cycle
- **Per-layer risk management:** Individual stops per pyramid layer
- **Multi-asset validation:** Must work across asset classes with zero parameter changes

---

## Current System Specification

### Entry Rules (Long)
1. Regime check: Daily close > 200 SMA
2. Pullback: Price closes below 21 EMA (track lowest low during pullback)
3. Reclaim: Price closes back above 21 EMA → ENTER LONG
4. Stop: Below pullback low − 0.5 × ATR(14)
5. Size: 1% of total portfolio equity at risk

### Pyramiding
- Max 3 layers (base + 2 adds)
- Each add requires a FRESH pullback + reclaim cycle
- Each add risks 1% of current portfolio equity
- Each add gets its own stop below its own pullback low
- Total max risk: 3% of equity if all 3 layers active

### Exit Rules
1. **Regime break:** Close below 200 SMA → close ALL layers immediately
2. **Per-layer trailing stop:** 2.5 × ATR(14) from highest close since that layer's entry, ratchets up only
3. **Per-layer initial stop:** Below pullback low − 0.5 × ATR (before trail takes over)

### What the System Does NOT Have
- No time stops
- No partial profit taking
- No MA crossover signals
- No RSI, Bollinger, Fibonacci, or compression logic
- No fixed take-profit levels
- No per-instrument parameter tuning

---

## Backtest Results Summary

### Testing Infrastructure
- Python backtester with bar-by-bar simulation
- Data: stooq (equities/commodities), Binance (crypto)
- Execution: signals on close, fill at next bar open
- 4-phase validation protocol completed

### Phase 1: Single Instrument (QQQ, no frictions)
| Metric | Value |
|--------|-------|
| CAGR | 3.85% |
| Max DD | -9.1% |
| Sharpe | 0.70 |
| PF | 2.20 |
| Trades | 104 (~9/yr) |
| Win Rate | 46.2% |

### Phase 2: Cross-Market (same params, no frictions)
| Symbol | CAGR | Max DD | Sharpe | PF | Verdict |
|--------|------|--------|--------|-----|---------|
| QQQ | 3.85% | -9.1% | 0.70 | 2.20 | ✅ Pass |
| SPY | 1.78% | -10.8% | 0.37 | 1.39 | ✅ Pass (marginal) |
| GLD | 6.26% | -18.2% | 0.73 | 3.07 | ✅ Strongest |
| BTC-USD | 3.01% | -9.5% | 0.51 | 3.74 | ✅ Pass |

**4/4 profitable with zero parameter changes.**

### Phase 3: Sensitivity (81 combos on QQQ)
- **81/81 profitable** — zero losing combinations
- EMA length is the biggest lever (15 > 21 > 30)
- Regime MA (150/200/250) barely matters — confirms robustness
- Risk scales linearly (as expected)

### Phase 4: Frictions (0.1% commission + 0.05% slippage)
| Symbol | CAGR after frictions | Survives? |
|--------|---------------------|-----------|
| QQQ | 2.52% | ✅ Yes |
| SPY | -0.06% | ❌ No — dropped |
| GLD | 4.02% | ✅ Yes |
| BTC-USD | 2.92% | ✅ Yes |

### Portfolio (QQQ + GLD + BTC-USD, with frictions)
| Metric | Portfolio | Best Individual (GLD) |
|--------|-----------|----------------------|
| Net P&L | +431.6% | +55.5% |
| CAGR | 16.09% | 4.02% |
| Max DD | -23.2% | -23.2% |
| Sharpe | 0.88 | 0.49 |
| PF | 3.65 | 2.18 |
| Return/DD | 18.59 | 2.39 |

---

## The Honest Assessment (Read This Carefully)

### What's genuinely good:
- The mechanism is structural — works across 3 distinct asset classes (equities, commodities, crypto) with identical parameters
- 81/81 parameter combos profitable confirms it's not curve-fitted to specific settings
- Portfolio diversification nearly doubles the Sharpe ratio (0.88 vs best individual 0.49)
- System drawdowns are 2-9× smaller than buy & hold
- The pyramiding design works exactly as intended — small losses, huge winners

### What's genuinely concerning:
- The system only works on 3 out of 9 tested instruments (33% hit rate)
- This means it's not a "universal trend system" — it requires instruments with very specific trend properties
- GLD contributes 86.4% of portfolio P&L — the "diversified portfolio" is really a gold trend system
- The instrument selection (QQQ, GLD, BTC) is itself a form of fitting — we chose these partly because they worked

### What you must understand before trading this:

**1. Returns are heavily concentrated.**
- Top 10 trades = 87.2% of total portfolio P&L
- 5 "monster" trades (>$20k each) generated $342k out of $431k total
- Three GLD trades in Aug-Oct 2025 alone made $285k (66% of lifetime P&L)
- 2025 returned +176%. Strip that out and the system made ~9.4% CAGR over 10 years.

**2. You will lose more often than you win.**
- Win rate: 38.1% (portfolio level)
- 169 out of 273 trades are losers
- Most weeks and most months will feel like the system is broken
- You will have entire losing years (2016: -7.6%, 2022: -10.9%)

**3. The shared equity pool is a double-edged amplifier.**
- When winning: position sizes grow because equity grows, creating convexity
- When losing: losses are sized off the full pool too
- The $285k GLD trades in 2025 were so large because equity had already grown to $300k+
- In a correlated drawdown across all instruments, the same amplifier works against you

**4. GLD dominates the portfolio.**
- 86.4% of total P&L comes from GLD
- The "diversified portfolio" is really a gold trend system with QQQ and BTC as side bets
- If gold stops trending for several years, the portfolio will underperform significantly
- This is why we're expanding the instrument universe (see roadmap below)

**5. This will NOT consistently beat equity buy & hold.**
- QQQ buy & hold returned +524% vs the system's +52.6% on QQQ alone
- The portfolio's +431% vs equal-weight B&H +414% is roughly comparable
- The system's advantage is risk-adjusted: similar returns with much less drawdown
- If your goal is pure maximum returns on equities, just buy and hold QQQ

**6. The system requires patience measured in years, not months.**
- The next "2025 GLD" moment might not come for 2-3 years
- You must be able to follow the signals through losing streaks without overriding
- Paper trade for 3-6 months minimum before going live
- The biggest risk to your success is abandoning the system during a drawdown

### What this system IS:
A disciplined, risk-managed trend-following approach that captures large moves across multiple asset classes while controlling drawdowns. It will underperform buy & hold in strong bull markets but protect capital during bear markets. Over full cycles, it should deliver competitive risk-adjusted returns.

### What this system IS NOT:
A way to get rich quick. A system that wins most of the time. A replacement for buy & hold on equities. A system you can override when it "feels wrong."

---

## Roadmap

### ✅ Completed
- [x] VCB breakout system (abandoned — edge was in regime, not breakouts)
- [x] Regime-only system on NAS100 (validated regime filter as core edge)
- [x] TPS v1 system design and Python backtester
- [x] Phase 1: Single instrument validation (QQQ)
- [x] Phase 2: Cross-market robustness (QQQ, SPY, GLD, BTC-USD)
- [x] Phase 3: Sensitivity analysis (81 parameter combinations)
- [x] Phase 4: Friction testing (0.1% commission + 0.05% slippage)
- [x] Portfolio simulation (QQQ + GLD + BTC-USD)
- [x] Year-by-year breakdown and trade concentration analysis
- [x] All results committed to GitHub
- [x] Phase 5: Instrument universe expansion (USO, SLV, EWG, EWJ, ETH — all failed PF > 1.3 filter)
- [x] Final portfolio confirmed: QQQ + GLD + BTC-USD (no additions)

### ✅ Phase 5: Expand Instrument Universe (Completed)
Tested 5 additional instruments with default config + frictions (0.1% comm + 0.05% slip):

| Symbol | CAGR | Max DD | Sharpe | PF | Trades | Verdict |
|--------|------|--------|--------|-----|--------|---------|
| USO (Oil) | -1.32% | -19.3% | -0.19 | 0.65 | 64 | ❌ Fail — mean-reverting, structural breaks |
| SLV (Silver) | -1.46% | -32.6% | -0.19 | 0.76 | 70 | ❌ Fail — too choppy relative to trend strength |
| EWG (Germany) | -0.42% | -28.9% | -0.03 | 1.01 | 99 | ❌ Fail — mean-reverting, ATR stops too wide |
| EWJ (Japan) | -1.26% | -17.1% | -0.16 | 0.87 | 95 | ❌ Fail — same issue as EWG |
| ETH-USD | +1.24% | -25.7% | 0.16 | 1.24 | 84 | ❌ Fail — closest miss, friction drag too high |

**Result: 0/5 passed the PF > 1.3 filter.** Portfolio stays at QQQ + GLD + BTC-USD.

**What this means:**
- The system works on instruments with long, sustained directional moves and clean pullback structures
- Oil/silver/international equities don't have this property — they're too mean-reverting or choppy
- ETH nearly passed but generates too many trades, making friction drag fatal
- The 3-instrument portfolio (QQQ, GLD, BTC) is the validated final universe for this strategy
- Instrument selection is itself a form of fitting — we should be honest that we're trading a curated set, not "all trending markets"
- The structural reasons these 3 work (tech sector drift, macro gold cycles, crypto adoption) are defensible but not guaranteed to persist

### 📋 Planned
- [ ] **Pine Script conversion** — TradingView version of TPS v1 for visual chart analysis and practice trading
- [ ] **Alert/notification system** — automated daily signal checking with alerts via email/Telegram/webhook
- [ ] **Paper trading period** — 3-6 months of live signal tracking without real capital
- [ ] **Intraday system (separate project)** — different edge, different timeframe, different validation process. Designed for engagement and income while the daily system runs in the background.

### 💡 Future Considerations
- Short-side testing on commodities and crypto (where downtrends are real)
- Walk-forward optimization (rolling out-of-sample validation)
- Monte Carlo simulation for drawdown confidence intervals
- Broker API integration for semi-automated execution
- Per-instrument config optimization (only after proving default works live)

---

## Technical Setup

### Running Backtests
```bash
cd /Users/adam/trend-pullback-system

# Single instrument
python run_backtest.py --symbol QQQ

# All default instruments
python run_backtest.py

# With frictions
python run_backtest.py --commission 0.1 --slippage 0.05

# Sensitivity sweep
python run_backtest.py --sensitivity

# Portfolio
python run_portfolio.py --commission 0.1 --slippage 0.05
```

### Project Structure
```
trend-pullback-system/
├── STRATEGY_SPEC.md          ← detailed strategy specification
├── PORTFOLIO_SPEC.md         ← portfolio simulation spec
├── PROJECT_STATUS.md         ← this file (development log + honest assessment)
├── config/default_config.yaml
├── src/
│   ├── data_loader.py        ← yfinance + stooq + Binance fallbacks
│   ├── indicators.py         ← SMA, EMA, ATR
│   ├── strategy.py           ← core strategy logic
│   ├── backtester.py         ← bar-by-bar simulation engine
│   ├── portfolio.py          ← multi-instrument portfolio engine
│   ├── metrics.py            ← 25+ performance metrics
│   └── visualizer.py         ← equity curves, drawdowns, heatmaps, correlations
├── tests/test_strategy.py    ← 17 unit tests
├── run_backtest.py           ← single/multi-instrument backtest CLI
├── run_portfolio.py          ← portfolio backtest CLI
└── results/                  ← all backtest outputs organized by phase
```

### Data Sources
- **Equities/Commodities:** stooq via pandas-datareader (primary), yfinance (secondary — often rate-limited)
- **Crypto:** Binance public klines API (no auth needed)
- Data cached locally as parquet files in data/

---

## Key Lessons Learned

1. **Regime filter is the edge, not the entry signal.** Every attempt to improve entries was less valuable than simply being on the right side of the market.

2. **Simpler is better.** The VCB system had compression scores, median ATR windows, failed breakout detection, and time stops. The final system has 200 SMA + 21 EMA + ATR trail. The simple version performs better.

3. **Validate across markets before optimizing.** We almost spent weeks optimizing NAS100-specific parameters. Cross-market testing revealed the system was structural, which is far more valuable than a 0.5% improvement on one instrument.

4. **Additions that reduce participation usually hurt.** Strength filters, volatility scaling, and partial exits all reduced the system's ability to capture the big moves that drive returns.

5. **Pyramiding works when the base system has positive expectancy.** Adding to winners amplified the edge. Taking partial profits reduced it. On trending instruments, convexity beats variance reduction.

6. **Position sizing matters more than entry timing.** Risk-based sizing across a portfolio with shared equity creates compounding effects that dwarf any entry signal improvement.

7. **Accept what the system is.** This is a patient, low-frequency trend follower. Trying to make it trade more often or win more often degraded performance every time.
