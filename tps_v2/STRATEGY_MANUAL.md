# TPS v2 — Complete Strategy Manual

## What This System Does in One Sentence

TPS v2 measures the strength of a trend using three moving average crossovers at different speeds, blends them into a single "forecast" number from 0 to 20, and sizes your position proportionally to that number — bigger position when the trend is strong, smaller when it's weakening, flat when there's no trend.

---

## The Core Concept: A Forecast Number

Everything in this system revolves around one number: **the forecast**. It ranges from 0 to 20.

- **0** = no trend, or bearish regime → you hold nothing
- **5** = mild trend → you hold a small position (half-size)
- **10** = average trend → you hold a full-size position
- **15** = strong trend → you hold 1.5x position
- **20** = maximum trend (capped here) → you hold 2x position

The forecast is not a buy/sell signal. It's a **conviction dial**. You're always either positioned or flat, and the forecast tells you *how much* to hold. There is no "entry" or "exit" in the traditional sense — your position size continuously adjusts to match the forecast.

---

## How the Forecast Is Calculated

### Step 1: Three EWMAC Signals (the raw ingredients)

EWMAC stands for Exponentially Weighted Moving Average Crossover. It's the difference between a fast EMA and a slow EMA:

```
EWMAC = EMA(fast) − EMA(slow)
```

When the fast EMA is above the slow EMA, the instrument is trending up (positive EWMAC). When below, it's trending down (negative EWMAC). The bigger the gap, the stronger the trend.

We use three speed pairs simultaneously:

| Variation | Fast EMA | Slow EMA | What it captures |
|-----------|----------|----------|------------------|
| Fast | 8 days | 32 days | Short-term trends (weeks) |
| Medium | 16 days | 64 days | Medium-term trends (1-3 months) |
| Slow | 32 days | 128 days | Long-term trends (3-6 months) |

Why three? Because we don't know which speed will work best in any given market regime. A fast crossover catches early trend changes but whipsaws more. A slow crossover is more stable but reacts late. Blending all three gives you the best of both worlds — the academic research (Shi & Lian 2025, Hurst et al. 2017) confirms that multi-speed blending is the single most reliable improvement in trend following.

### Step 2: Normalize by Volatility

The raw EWMAC number (EMA fast − EMA slow) is in price units. A $50 gap on BTC means something completely different than a $50 gap on gold. We normalize:

```
Risk-Adjusted EWMAC = Raw EWMAC / σ(daily price changes)
```

Where σ is the exponentially weighted standard deviation of daily price changes over ~25 days. After this step, a signal of "2.0" means the same thing on BTC as it does on gold — the trend is 2 standard deviations strong.

### Step 3: Scale to the Forecast Scale

We multiply by a pre-calculated "forecast scalar" so that the average absolute forecast value equals 10 (which represents a "normal" trend signal):

```
Scaled Forecast = Risk-Adjusted EWMAC × Forecast Scalar
```

The scalars are:
- EWMAC(8,32): **10.6**
- EWMAC(16,64): **7.5**
- EWMAC(32,128): **5.3**

These numbers come from Carver's research — they're calculated by looking at the historical distribution of each signal's risk-adjusted values and choosing the multiplier that makes the average absolute value equal 10. Faster signals are noisier (smaller raw values) so need larger scalars.

### Step 4: Blend the Three Forecasts

```
Combined = (⅓ × Fast + ⅓ × Medium + ⅓ × Slow) × FDM
```

Equal weights (⅓ each) — we don't try to pick the "best" speed. The FDM (Forecast Diversification Multiplier) is **1.15**. It compensates for the fact that blending three imperfectly correlated signals reduces the average absolute value below 10. The 1.15x bump restores it to the correct scale.

### Step 5: Cap and Floor

```
Final Forecast = clip(Combined, 0, 20)
```

- **Floor at 0:** We're long-only. Negative forecasts (downtrends) are clipped to 0 = flat.
- **Cap at 20:** Extreme forecasts are statistically unreliable and create dangerous position sizes. A forecast of 20 means "maximum conviction, double-sized position." Going beyond that is not justified by the signal quality.

### Step 6: Regime Filter

Before using the forecast, we check: **is the price above the 200-day Simple Moving Average?**

- **Yes (bullish regime):** Use the forecast as calculated.
- **No (bearish regime):** Override the forecast to 0. Be flat.

This is the safety gate. The 200 SMA is the institutional standard for defining whether a market is in a long-term uptrend. If the market is below its 200 SMA, we don't care what the EWMAC signals say — we sit out. This is the same regime filter from v1, and it's the single most important component of the system.

---

## How Position Size Is Determined

### The Carver Formula

Once you have the forecast, position size is calculated by:

```
Position = (Capital × VolTarget × InstrWeight × IDM × Forecast/10) / (InstrVol × Price)
```

Let me explain each piece:

**Capital** — Your current account equity. As your account grows, positions grow proportionally. As it shrinks, positions shrink. This is automatic compounding.

**VolTarget (default: 20%)** — How much annual volatility you're willing to accept from the portfolio. 20% means you expect your equity to fluctuate roughly ±20% per year. Higher = more aggressive, more return, more drawdown. The Sharpe ratio stays the same regardless of this setting — it's pure leverage control.

**InstrWeight (default: 0.333 for 3 instruments)** — What fraction of your risk budget goes to this instrument. With 3 instruments at equal weight, each gets 33.3%.

**IDM (default: 1.5)** — Instrument Diversification Multiplier. Because your 3 instruments aren't perfectly correlated, the portfolio volatility is lower than the sum of individual volatilities. The IDM "takes credit" for this diversification by sizing each position slightly larger. For 3 moderately correlated instruments, 1.5 is standard. If you were trading only 1 instrument, IDM should be 1.0.

**Forecast/10** — The forecast scales position size linearly. Forecast 10 → full size. Forecast 5 → half size. Forecast 20 → double size. Forecast 0 → flat.

**InstrVol** — The instrument's annualized volatility, estimated from the last ~25 days of returns. High-volatility instruments (BTC at ~60% vol) automatically get fewer units. Low-volatility instruments (NAS100 at ~20% vol) get more. This is how every instrument contributes equal *risk* to the portfolio.

**Price** — Current instrument price. More expensive instruments = fewer units per dollar of risk.

### A Worked Example

Portfolio equity: $50,000. XAUUSD price: $3,000. Gold annual vol: 15%. Forecast: 14.

```
Position = (50,000 × 0.20 × 0.333 × 1.5 × 14/10) / (0.15 × 3,000)
         = (50,000 × 0.20 × 0.333 × 1.5 × 1.4) / 450
         = 6,993 / 450
         = 15.5 units (round to 16)
```

So you'd hold 16 units of XAUUSD. If the forecast drops to 7 tomorrow (half as bullish):
```
Position = ... × 7/10 / ... = 7.8 → round to 8 units
```
You'd sell 8 units to scale down from 16 to 8.

---

## Entry, Exit, and Everything In Between

### There Is No Traditional "Entry" or "Exit"

This is the hardest mindset shift from v1 or from discretionary trading. In TPS v2:

- You don't "enter a trade" — you **increase your position** from 0 to whatever the forecast says
- You don't "exit a trade" — you **decrease your position** to 0 when the forecast drops to 0
- You don't "add a pyramid layer" — your position **continuously adjusts** as the forecast changes
- You don't "take partial profits" — if the forecast drops from 18 to 12, your position automatically shrinks (which IS taking partial profits, but driven by the signal, not by an arbitrary rule)

### What Triggers a Position Increase (Scaling Up)

The forecast rises, making the target position larger than your current position by more than the buffer threshold. This happens when:

1. Fast EMA pulls further above slow EMA (trend strengthening)
2. Instrument volatility decreases (same forecast = larger position)
3. Account equity grows (more capital = larger positions)

In practice, a typical "entry" sequence looks like this:
- Day 1: Price crosses above 200 SMA. Regime turns bullish. EWMAC forecasts activate.
- Day 2: Combined forecast is 4. Target: 5 units. Buffer says trade. You buy 5.
- Day 8: Trend builds. Forecast rises to 9. Target: 12 units. Buy 7 more.
- Day 20: Strong trend. Forecast hits 16. Target: 22 units. Buy 10 more.

This IS pyramiding — but it's gradual and driven by the signal, not by discrete "add a layer when price makes a new high" rules.

### What Triggers a Position Decrease (Scaling Down)

The forecast falls, making the target position smaller than your current position by more than the buffer. This happens when:

1. Fast EMA converges toward slow EMA (trend weakening)
2. Instrument volatility increases (same forecast = smaller position — this is built-in crash protection)
3. Account equity shrinks

Example:
- Day 30: Forecast was 16, now drops to 11. Target goes from 22 to 15. Sell 7 units.
- Day 45: Forecast drops to 4. Target: 5 units. Sell 10 more.
- Day 50: Forecast hits 0 (fast EMA crosses below slow EMA, or price drops below 200 SMA). Sell remaining 5. Flat.

### What Triggers a Full Exit

Two conditions force the position to zero immediately:

1. **Forecast reaches 0** — all three EWMAC signals are negative or flat, meaning no trend exists. The position is gradually reduced to zero as the forecast drops.
2. **Regime filter triggers** — price closes below the 200 SMA. All forecasts are overridden to 0 regardless of what the EWMAC signals say. This is the emergency stop. You go flat on the next bar.

### There Are No Stop Losses

This is intentional. In the Carver framework, the forecast IS the stop loss:

- Trend weakening → forecast drops → position shrinks → exposure reduces
- Market crash → volatility spikes → InstrVol rises → position automatically shrinks even if forecast hasn't changed yet
- 200 SMA broken → all forecasts zeroed → full exit

There's no arbitrary "exit if I lose 2%" rule. The system manages risk through continuous position adjustment based on market conditions, not based on your entry price (which the market doesn't know or care about).

---

## Trade Buffering: The Anti-Churn Mechanism

### The Problem Without Buffering

The forecast changes every day. Without buffering, you'd rebalance your position every single day — hundreds of trades per year, each costing commissions and slippage. Most of these daily changes are noise (forecast moves from 12.3 to 12.7), not signal.

### How Buffering Works

You only trade when:

```
|Target Position − Current Position| > Buffer Threshold × Average Position
```

With a 30% buffer threshold and an average position of 20 units:
- Buffer zone = 30% × 20 = 6 units
- If target is 22 and current is 20 → delta is 2 → less than 6 → DON'T TRADE
- If target is 28 and current is 20 → delta is 8 → more than 6 → TRADE

This cuts trades by ~70% while barely affecting returns. Our optimization testing confirmed that the buffer threshold is more important than rebalance frequency — a 25-30% buffer on daily data outperforms weekly rebalancing.

---

## What You See on the Chart

### Bar Colors (Forecast Strength)

| Color | Meaning |
|-------|---------|
| Bright green | Forecast ≥ 15 (strong trend, large position) |
| Medium green | Forecast 10-15 (moderate trend, full position) |
| Yellow | Forecast 5-10 (weak trend, small position) |
| Gray | Forecast 0-5 (minimal conviction) |
| Red | Below 200 SMA (bearish regime, no position) |

### Signal Markers

| Marker | Meaning |
|--------|---------|
| Green triangle (below bar) | Forecast just crossed above 10 — trend strengthening |
| Orange triangle (above bar) | Forecast just dropped below 5 — trend weakening |
| Red X (above bar) with "REGIME" | Price crossed below 200 SMA — all positions closing |

### The Forecast Table (bottom right)

Shows the current blended forecast out of 20, plus the three individual component forecasts (8/32, 16/64, 32/128), regime status, and target position size.

### The Info Label (last bar)

Detailed readout: forecast value, signal strength classification, regime status, current volatility, current position, target position, and whether the buffer says to trade or hold.

---

## What This System Cannot Do

1. **It cannot manage a multi-instrument portfolio in Pine Script.** Each chart runs independently. The position sizing formula uses `strategy.equity` from that one chart, not your total account across all instruments. For proper portfolio-level sizing, you need the Python signal generator.

2. **It does not know about CFD contract specifications.** The formula outputs "number of units" but doesn't know that 1 lot of XAUUSD on OANDA = 100 oz. You may need to divide the target by the lot multiplier for your broker.

3. **It does not account for margin requirements.** The system assumes you can hold the target position. On a leveraged CFD account, you might not have enough margin for the full position, especially with multiple instruments.

4. **It does not handle correlated drawdowns.** If NAS100, gold, and BTC all sell off at the same time, the system will be losing on all three simultaneously. The IDM assumes imperfect correlation — if everything correlates to 1.0 in a crisis, the portfolio risk is higher than targeted.

---

## The Daily Routine

If you're trading this system manually:

1. **After market close:** Check each chart (NAS100, XAUUSD, BTCUSD daily)
2. **Read the forecast table:** Note the forecast value for each instrument
3. **Check "should trade":** Is the target position different enough from current to trigger a rebalance?
4. **If yes:** Adjust your OANDA position to match the target
5. **If no:** Do nothing. Most days will be "do nothing."

The system is designed to require 5 minutes per day, not 5 hours. Most days, the buffer will say "hold" and you do nothing. On the days it says "trade," you adjust position size. That's it.

---

## Summary of Parameters

| Parameter | Default | What It Controls |
|-----------|---------|-----------------|
| EWMAC speeds | 8/32, 16/64, 32/128 | Signal sensitivity (don't change) |
| Forecast scalars | 10.6, 7.5, 5.3 | Signal calibration (don't change) |
| FDM | 1.15 | Forecast diversification (don't change) |
| Forecast cap | 20 | Maximum conviction (don't change) |
| 200 SMA | 200 | Regime filter speed (don't change) |
| Vol target | 20% | How aggressive — YOUR CHOICE |
| Instrument weight | 0.333 | Depends on how many instruments |
| IDM | 1.5 | Depends on instrument count |
| Buffer | 25-30% | Trade frequency — can increase to reduce costs |
