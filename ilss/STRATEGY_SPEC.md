# Intraday Liquidity Sweep System (ILSS) — Strategy Specification

**Version:** 0.1 (Research Draft)
**Date:** March 2026
**Author:** Adam (strategy design) + Claude (development partner)
**Status:** Pre-backtest — all parameters are hypotheses to be validated

---

## 1. System Overview

### What Is This?

A systematic intraday trading strategy that fades false breakouts (Swing Failure Patterns / liquidity sweeps) at key price levels, filtered by a higher-timeframe directional bias from TPS v2.

### Core Thesis

Most retail traders lose money because they:
1. Chase breakouts at obvious levels (prior highs/lows, session extremes)
2. Place stop losses at predictable locations just beyond those levels
3. Get swept out by institutional order flow before the real move begins

This system exploits that dynamic by:
- **Waiting** for price to sweep a key level (triggering retail stops)
- **Confirming** the sweep fails (price closes back inside the range)
- **Entering** in the opposite direction, aligned with the higher-timeframe trend
- **Placing stops** beyond the sweep wick (a structurally non-obvious location)

### Academic Foundation

| Concept | Academic Support | Key Papers |
|---------|-----------------|------------|
| Stop-loss clustering at obvious levels | Strong | Osler (2003, 2005) — institutional FX order data |
| S/R levels = real limit order depth | Strong | Kavajecz & Odders-White (2004) |
| Intraday momentum (session structure) | Strong | Gao et al. (2018, JFE), Baltussen et al. (2021, JFE) |
| False breakout reversal patterns | Moderate | Holmberg et al. (2012), Wang et al. (2019, IEEE) |
| Noise boundary / ORB strategies | Moderate | Zarattini et al. (2024, SFI) |
| Daily trend persistence as filter | Strong | Moskowitz et al. (2012), Hurst et al. (2017) — validated in TPS v2 |

### What This System Is NOT

- Not an ICT/SMC discretionary system with subjective "order block" identification
- Not a scalping system requiring sub-minute execution
- Not dependent on any single instrument or market
- Not a system that trades every day — it waits for specific setups

---

## 2. Instrument Universe

### Selection Criteria

For an intraday SFP system, instruments must satisfy:

1. **Sufficient daily range vs spread** — Average True Range (intraday) must be >50× the typical spread
2. **Clear session structure** — Distinct Asian/London/New York sessions with identifiable high/low levels
3. **Liquidity** — Enough volume that SFPs are meaningful (not just random noise)
4. **Available on OANDA** — All instruments must be tradeable via OANDA CFDs
5. **Diverse asset classes** — To test whether the SFP edge is structural across markets

### Proposed Instruments (8 candidates)

#### Indices (3)
| OANDA Symbol | Instrument | Typical Spread | Session Structure | Notes |
|-------------|-----------|---------------|-------------------|-------|
| NAS100_USD | Nasdaq 100 | ~1.0 pt | Strong US session | Already in TPS v2; volatile, good for SFPs |
| SPX500_USD | S&P 500 | ~0.4 pt | Strong US session | Most liquid index; strongest academic evidence |
| UK100_GBP | FTSE 100 | ~1.0 pt | Strong London session | Tests non-US session dynamics |

#### Forex (3)
| OANDA Symbol | Instrument | Typical Spread | Session Structure | Notes |
|-------------|-----------|---------------|-------------------|-------|
| EUR_USD | Euro/Dollar | ~1.4 pips | London + NY overlap | Most liquid FX pair globally |
| GBP_USD | Pound/Dollar | ~1.8 pips | Strong London session | More volatile than EUR/USD |
| USD_JPY | Dollar/Yen | ~1.4 pips | Asian + NY session | Tests Asian session SFPs |

#### Commodities / Metals (1)
| OANDA Symbol | Instrument | Typical Spread | Session Structure | Notes |
|-------------|-----------|---------------|-------------------|-------|
| XAU_USD | Gold | ~0.38 | London + NY | Already in TPS v2; known for stop hunts |

#### Crypto (1)
| OANDA Symbol | Instrument | Typical Spread | Session Structure | Notes |
|-------------|-----------|---------------|-------------------|-------|
| BTC_USD | Bitcoin | Variable (~30-50) | 24/7 but US-session-biased | Already in TPS v2; extreme volatility |

### Instrument Qualification Protocol

Each instrument must pass these filters before inclusion in the final system:

1. **Spread-to-range ratio** < 2% (spread / average 4H ATR)
2. **SFP frequency** — minimum 2 SFPs per week on average (enough to be tradeable)
3. **SFP edge** — raw SFP win rate > 50% before any filtering
4. **Survives frictions** — positive expectancy after spread costs

Instruments that fail any filter get dropped, same as USO/SLV/EWG/EWJ in TPS v2.

---

## 3. Signal Architecture

### Layer 1: Daily Bias (from TPS v2)

The daily EWMAC forecast from TPS v2 provides the directional filter:

```
Bias = "LONG"  if Combined EWMAC Forecast ≥ 5 AND price > 200 SMA
Bias = "SHORT" if Combined EWMAC Forecast < 5 (for instruments where shorting is tested)
Bias = "FLAT"  if Combined EWMAC Forecast < 5 AND price > 200 SMA (long-only mode)
```

**Initial approach: Long-only**, matching TPS v2's regime filter. Short-side testing is Phase 2.

In long-only mode:
- Forecast ≥ 5 → eligible for long SFP entries
- Forecast < 5 → no trades that day

**Why 5 and not 10?** The intraday system is looking for high-conviction pullback entries within a trend. A forecast of 5 still indicates a mild bullish trend — we don't need maximum conviction, we just need directional alignment.

**To be tested:** Threshold sensitivity at 3, 5, 7, 10.

### Layer 2: Liquidity Level Identification

Key levels where retail stops predictably cluster:

#### Primary Levels (highest priority)
- **Previous Day High (PDH)** — the most universally watched level
- **Previous Day Low (PDL)** — the counterpart
- **Asian Session High/Low** — swept during London open ("Judas swing")

#### Secondary Levels (additional confluence)
- **Previous Week High/Low** — larger timeframe liquidity
- **Current Session High/Low** — if formed >2 hours ago
- **Round numbers** — psychological levels (e.g., 20000 on NAS100)

**Implementation note:** Start with PDH/PDL and Asian High/Low only. Add secondary levels in later phases.

### Layer 3: Swing Failure Pattern (SFP) Detection

The mechanical definition of a valid SFP:

#### Bullish SFP (long entry)
```
1. Daily bias = LONG (Layer 1 filter passes)
2. Price trades BELOW a key level (PDL or Asian Low)
   → Specifically: candle low < level (the "sweep")
3. The SAME candle or NEXT candle closes ABOVE the level
   → Specifically: candle close > level (the "failure")
4. The sweep depth is meaningful:
   → (level - candle low) > 0.25 × ATR(14) on the entry timeframe
   → This filters out tiny wicks that aren't real sweeps
5. The sweep doesn't exceed maximum depth:
   → (level - candle low) < 1.5 × ATR(14)
   → This filters out genuine breakdowns that close back on a dead cat bounce
```

#### Bearish SFP (short entry — Phase 2 only)
```
Mirror of bullish, sweeping above PDH/Asian High with close back below.
```

### Layer 4: Entry, Stop, and Target

#### Entry
- Enter at the **close of the confirmation candle** (the candle that closes back above the swept level)
- Market order at close, or limit order at level if candle hasn't closed yet

#### Stop Loss
- Below the **sweep wick low** minus a buffer
- Buffer = 0.25 × ATR(14) on entry timeframe
- This places the stop at a structurally non-obvious level — beyond where stops were just swept

#### Take Profit / Exit (multiple options to test)
- **Option A: Opposite liquidity level** — target the opposite key level (e.g., if you entered on a PDL sweep, target PDH)
- **Option B: Session close** — close at end of the active session (NY close for US instruments, London close for UK/European)
- **Option C: ATR trail** — trail stop at 1.5 × ATR(14) from highest price since entry
- **Option D: Time stop** — close after N hours if neither target nor stop hit

**To be tested:** All four options independently, then best performer selected.

---

## 4. Session Structure

### Session Definitions (UTC)

| Session | Start (UTC) | End (UTC) | Primary Action |
|---------|-------------|-----------|---------------|
| Asian | 00:00 | 07:00 | Sets the range to be swept |
| London Open | 07:00 | 08:30 | Sweeps Asian highs/lows |
| London | 08:30 | 12:00 | Main European trading |
| NY Open | 12:00 | 14:30 | Sweeps London/Asian levels; highest volume overlap |
| NY | 14:30 | 20:00 | Main US trading |
| NY Close | 20:00 | 21:00 | MIM effect (Gao et al. 2018) |

### Active Trading Windows

The system only looks for entries during specific windows where SFPs are most likely:

1. **London Open window** (07:00–09:00 UTC) — sweeping Asian session highs/lows
2. **NY Open window** (12:00–15:00 UTC) — sweeping prior day and London session levels
3. **NY Afternoon** (15:00–19:00 UTC) — continuation or reversal of earlier moves

**To be tested:** Which windows produce the best SFPs per instrument.

### Instrument-Session Mapping

| Instrument | Primary Window | Secondary Window |
|-----------|---------------|-----------------|
| NAS100_USD | NY Open (12:00–15:00) | NY Afternoon |
| SPX500_USD | NY Open (12:00–15:00) | NY Afternoon |
| UK100_GBP | London Open (07:00–09:00) | NY Open |
| EUR_USD | London Open (07:00–09:00) | NY Open |
| GBP_USD | London Open (07:00–09:00) | NY Open |
| USD_JPY | Asian (00:00–03:00) | London Open |
| XAU_USD | London Open (07:00–09:00) | NY Open |
| BTC_USD | NY Open (12:00–15:00) | London Open |

---

## 5. Position Sizing

### Capital Constraint Reality

With a £2,000 OANDA account:
- Maximum risk per trade: 1% = £20
- This is extremely tight — it limits position sizing significantly
- On NAS100 at ~20,000, a 50-point stop requires ~0.02 lots (£10/point × 2 points = £20 risk)
- OANDA minimum is typically 1 unit, so micro-sizing is possible

### Sizing Formula

```
Position Size (units) = (Account Equity × Risk%) / (Stop Distance in price × Value per Unit)

Where:
  Risk% = 1% (fixed)
  Stop Distance = sweep wick low - entry + buffer (as calculated in Layer 4)
  Value per Unit = instrument-specific (from OANDA contract spec)
```

### Maximum Concurrent Positions

- **Maximum 2 positions** at any time across all instruments
- If 2 positions are already open, no new entries regardless of signal quality
- This caps total portfolio risk at 2% of equity

### Daily Trade Limit

- **Maximum 2 entries per day** across all instruments
- Prevents overtrading on choppy days
- If both trades stop out, the day is done

---

## 6. Risk Management

### Hard Rules (Non-Negotiable)

1. **Never move a stop loss away from price** — only trail toward price or leave unchanged
2. **Never add to a losing position**
3. **Always close by session end** — no overnight holds on intraday trades (crypto exception: can hold through Asian session if stop is in place)
4. **Daily loss limit: 3%** — if cumulative daily P&L reaches -3%, stop trading for the day
5. **Weekly loss limit: 5%** — if cumulative weekly P&L reaches -5%, stop trading for the week
6. **No trading during major news** — avoid entries in the 15 minutes before and after scheduled high-impact news (NFP, FOMC, CPI, ECB)

### Drawdown Protocol

| Drawdown Level | Action |
|---------------|--------|
| 0–5% | Normal trading |
| 5–10% | Reduce risk to 0.5% per trade |
| 10–15% | Pause live trading, review signals vs backtest |
| >15% | Stop trading this system, reassess |

---

## 7. Timeframe Selection

### Entry Timeframe

The SFP detection and entry will be on the **15-minute chart**.

**Why 15-minute:**
- Short enough to capture session-level sweeps and reversals
- Long enough that OANDA spread doesn't dominate the signal
- Each candle has meaningful volume (unlike 1-minute on CFDs)
- Compatible with 1-2 hours of screen time (only need to monitor during active windows)
- SFP wicks are clear and unambiguous on 15-min

**Why not other timeframes:**
- 1-min / 5-min: Too noisy on OANDA CFDs; spread cost dominates
- 30-min: Misses some sweeps that complete within 15 minutes
- 1H: Too slow — sweep + failure can happen within one 15-min candle
- 4H: More like the daily system than an intraday system

**To be tested:** Compare SFP quality on 5-min, 15-min, and 30-min during validation.

### Bias Timeframe

Daily chart (from TPS v2 EWMAC signal).

---

## 8. Data Requirements

### For Backtesting

| Data Type | Source | Resolution | Period |
|-----------|--------|-----------|--------|
| Intraday OHLCV | OANDA v20 API (historical candles) | 15-min (primary), 5-min (sensitivity) | 2020-01-01 to 2025-12-31 (5+ years) |
| Daily OHLCV | Existing TPS v2 data sources | Daily | 2012-01-01 to 2025-12-31 |

### OANDA API Historical Data

OANDA v20 API provides historical candle data (endpoint: `/v3/instruments/{instrument}/candles`). This is the preferred source because:
- It matches the exact pricing we'll trade on
- Includes proper session handling (no gaps between sessions)
- Free with any OANDA account (including demo)
- Already have API connectivity from TPS v2 signal generator

**Limitation:** OANDA limits historical candle requests to 5,000 candles per request. For 15-min data over 5 years, that's ~125,000 candles per instrument — requires paginated requests.

### Alternative Data Sources

If OANDA historical data is insufficient:

| Source | Coverage | Cost | Notes |
|--------|----------|------|-------|
| Polygon.io | Forex, Indices, Crypto | Free tier available | Good for futures-equivalent data |
| Dukascopy | Forex tick data | Free | Download via JForex; excellent for FX |
| Binance | Crypto only | Free | Already used in TPS v2 for BTC |
| TradingView export | All instruments | Free (limited) | Manual export; good for validation |

---

## 9. Validation Protocol

Mirroring TPS v2's disciplined approach:

### Phase 0: Data Collection & Preparation
- Download 5 years of 15-min data for all 8 instruments from OANDA
- Download daily data for EWMAC calculation
- Verify data quality: gaps, outliers, session boundaries
- **Deliverable:** Clean dataset with session labels

### Phase 1: Raw SFP Statistics (No Filtering)
- Define SFP mechanically in code
- Count SFPs per instrument per year
- Measure: raw win rate, average R:R, distribution of outcomes
- **Question answered:** Do SFPs exist with any statistical regularity?
- **Pass/Fail:** Raw SFP win rate > 50% on at least 5/8 instruments

### Phase 2: Daily Bias Filter
- Add TPS v2 EWMAC forecast as directional filter
- Compare: SFP with bias filter vs SFP without
- Test forecast threshold sensitivity (3, 5, 7, 10)
- **Question answered:** Does the daily bias improve SFP performance?
- **Pass/Fail:** Filtered SFP win rate > unfiltered by ≥ 5pp on majority of instruments

### Phase 3: Session Filtering
- Test which trading windows produce the best SFPs per instrument
- London Open sweep of Asian range vs NY Open sweep of PDH/PDL
- **Question answered:** Which sessions have the strongest SFP edge?
- **Pass/Fail:** At least one window shows Sharpe > 0.5 after frictions

### Phase 4: Exit Strategy Optimization
- Test all four exit options (opposite level, session close, ATR trail, time stop)
- Measure: Sharpe, max DD, win rate, average R:R
- **Question answered:** Which exit approach preserves the most edge?
- **Pass/Fail:** Best exit approach produces PF > 1.3 after frictions

### Phase 5: Friction Testing
- Apply OANDA spread costs (instrument-specific)
- Include slippage estimate (0.5× spread as worst case)
- **Question answered:** Does the system survive real-world costs?
- **Pass/Fail:** All included instruments profitable after frictions

### Phase 6: Portfolio Construction
- Combine surviving instruments
- Test correlation of signals across instruments
- Set position limits (max 2 concurrent)
- **Deliverable:** Final instrument list, position sizing, and expected performance

### Phase 7: Walk-Forward Validation
- Rolling 1-year in-sample / 6-month out-of-sample windows
- **Question answered:** Is this robust or curve-fitted?
- **Pass/Fail:** All OOS windows positive

### Kill Criteria (Abandon the System If...)
- Raw SFP win rate < 45% across instruments (no edge to filter for)
- Daily bias filter doesn't improve results (TPS v2 synergy doesn't work)
- After frictions, no instrument has PF > 1.3
- Walk-forward shows > 2 negative OOS windows out of 6

---

## 10. Implementation Plan

### Step 1: Data Pipeline (Week 1)
- Build OANDA historical data downloader (paginated, all 8 instruments, 15-min)
- Calculate and store daily EWMAC signals for each instrument
- Label sessions (Asian, London, NY) on each bar
- Store as parquet files (matching TPS v2 convention)

### Step 2: SFP Detector (Week 1–2)
- Write mechanical SFP detection function
- Inputs: OHLCV bars, key levels (PDH/PDL, session highs/lows)
- Outputs: list of SFP events with metadata (direction, sweep depth, level swept, time)
- Unit tests against manually identified examples

### Step 3: Backtester (Week 2–3)
- Bar-by-bar simulation engine (similar to TPS v2 backtester)
- Track: entries, exits, P&L per trade, equity curve
- Apply frictions per instrument
- Output: standard metrics (Sharpe, Max DD, PF, win rate, R:R)

### Step 4: Run Validation Phases (Week 3–5)
- Execute Phases 1–7 sequentially
- Document results in PROJECT_STATUS.md (same as TPS v2)
- Go/no-go decision after Phase 5

### Step 5: If Go — Live Infrastructure (Week 5–6)
- Adapt existing OANDA signal generator for intraday
- Session-aware monitoring (alert only during active windows)
- Telegram notifications with SFP details
- Paper trade for minimum 4 weeks before live

---

## 11. Relationship to TPS v2

### What's Shared
- Daily EWMAC forecast calculation (exact same code)
- 200 SMA regime filter
- OANDA API connectivity
- Telegram notification infrastructure
- Risk management philosophy

### What's Independent
- Entry logic (SFP vs EWMAC-driven continuous sizing)
- Timeframe (intraday vs daily)
- Position sizing (fixed % risk vs volatility-targeted)
- Holding period (hours vs weeks/months)
- Exit logic (session-based vs forecast-driven)

### How They Coexist
- TPS v2 runs on the daily cron job (22:05 UTC) — unchanged
- ILSS runs during active trading windows (separate process)
- Separate position tracking — ILSS positions don't affect TPS v2 capital allocation
- If both systems want to be long NAS100 simultaneously, that's fine — they have separate stops

### Account Structure
- **Option A:** Single OANDA account, separate position tracking in code
- **Option B:** Two OANDA sub-accounts (if available)
- **Recommended:** Option A initially (simpler), migrate to B if both systems are live

---

## 12. Honest Pre-Assessment

### What could go right:
- SFPs are a well-documented microstructure phenomenon backed by institutional FX data (Osler)
- The daily bias from TPS v2 adds a validated edge layer that pure SFP traders don't have
- Session timing is supported by Gao et al. and Baltussen et al. in top-tier journals
- Low trade frequency (1-2 per day max) means friction burden is manageable
- Multiple asset classes provide genuine diversification

### What could go wrong:
- OANDA CFD spread may eat the intraday edge that exists in futures markets
- SFPs on 15-min charts may be too noisy to trade mechanically
- The "sweep + fail" pattern may not be reliably distinguishable from genuine breakouts in code
- £2k capital means position sizes are tiny — one bad week could trigger the drawdown protocol
- 1-2 hours of daily screen time may not coincide with the best setups
- The daily bias filter may add lag that misses fast intraday reversals
- Crypto SFPs may behave differently from FX/equity SFPs

### Expected base case:
- 4-5 instruments survive the full validation protocol
- Win rate around 50-55% with average R:R of 1.5:1
- Monthly return of 3-5% after frictions (highly uncertain)
- Significant drawdown periods where the system produces no setups for weeks
- Total annual return highly dependent on market volatility — more volatility = more SFPs

### Expected kill probability: ~60%
We should be mentally prepared that this system does not pass validation. That's the honest assessment. The 97% day trader loss rate exists for a reason — intraday edges are thin, fragile, and often disappear when you account for realistic execution costs. If we find no edge, the correct action is to focus capital and energy on TPS v2.

---

## 13. References

### Academic (Peer-Reviewed)
1. Osler, C.L. (2003). "Currency orders and exchange-rate dynamics: Explaining the success of technical analysis." *Journal of Finance*, 58(5), 1791–1819.
2. Osler, C.L. (2005). "Stop-loss orders and price cascades in currency markets." *Journal of International Money and Finance*, 24(2), 219–241.
3. Kavajecz, K.A. & Odders-White, E.R. (2004). "Technical analysis and liquidity provision." *Review of Financial Studies*, 17(4), 1043–1071.
4. Gao, L., Han, Y., Zhengzi Li, S., & Zhou, G. (2018). "Market intraday momentum." *Journal of Financial Economics*, 129(2), 394–414.
5. Baltussen, G., Da, Z., Lammers, S., & Martens, M. (2021). "Hedging demand and market intraday momentum." *Journal of Financial Economics*, 142(1), 377–403.
6. Bouchaud, J.-P., Farmer, J.D., & Lillo, F. (2009). "How markets slowly digest changes in supply and demand." *Handbook of Financial Markets: Dynamics and Evolution*.
7. Holmberg, U., Lönnbark, C., & Lundström, C. (2012). "Assessing the profitability of intraday opening range breakout strategies." *Finance Research Letters*, 10(1), 27–33.
8. Wang, C.-J., et al. (2019). "Assessing the profitability of timely opening range breakout on index futures markets." *IEEE Access*.

### Working Papers / Non-Peer-Reviewed
9. Zarattini, C., Aziz, A., & Barbon, A. (2024). "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)." Swiss Finance Institute Research Paper No. 24-97.
10. Maróy, Á. (2025). "Improvements to Intraday Momentum Strategies Using Parameter Optimization and Different Exit Strategies." SSRN.

### From TPS v2 (Directional Bias Foundation)
11. Moskowitz, T.J., Ooi, Y.H., & Pedersen, L.H. (2012). "Time Series Momentum." *JFE*, 104(2), 228–250.
12. Hurst, B., Ooi, Y.H., & Pedersen, L.H. (2017). "A Century of Evidence on Trend-Following Investing." *JPM*, 44(1).
13. Carver, R. (2015). *Systematic Trading.* Harriman House.

### Day Trading Evidence (Context)
14. Barber, B.M., Lee, Y.-T., Liu, Y.-J., & Odean, T. (2009). "Do Individual Day Traders Make Money? Evidence from Taiwan." *Working Paper*.
15. Barber, B.M. & Odean, T. (2000). "Trading Is Hazardous to Your Wealth." *Journal of Finance*, 55(2), 773–806.
