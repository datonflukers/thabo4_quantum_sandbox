# Trend Detection via Peaks & Troughs (Market Structure)

Markets are fractal: the trend on Weekly/Daily can differ from the trend on 1H/15M.
To stay aligned with the "bigger" market, we classify trend using market structure
(swing highs = peaks, swing lows = troughs).

## Definitions

- **Peak (Swing High):** a local high formed after price stops rising and begins to pull back.
- **Trough (Swing Low):** a local low formed after price stops falling and begins to bounce.

## Core Rule (Structure = Trend)

We determine trend by comparing the most recent confirmed swings:

### Bullish Trend (Uptrend)
- **Higher Highs (HH):** latest peak > previous peak
- **Higher Lows (HL):** latest trough > previous trough

If both conditions repeat, price is trending upward.

### Bearish Trend (Downtrend)
- **Lower Highs (LH):** latest peak < previous peak
- **Lower Lows (LL):** latest trough < previous trough

If both conditions repeat, price is trending downward.

### Choppy / Range (No Trend)
If highs and lows are mixed (e.g., HH but LL, or flat highs/lows), structure is not clean.
This is treated as chop/range and trend-following entries should be reduced or blocked.

## Minimal Data Requirement

Trend classification requires at least:
- 2 confirmed peaks (to compare highs)
- 2 confirmed troughs (to compare lows)

## Why This Matters

Trading with higher-timeframe structure improves odds:
- If Daily/Weekly is bullish, lower-timeframe bearish movement is often a pullback.
- We prefer entries that align with the higher-timeframe bias rather than fighting it.

## Logic Summary

Let:
- H1, H2 = last two confirmed peaks (older -> newer)
- L1, L2 = last two confirmed troughs (older -> newer)

Then:
- **Bull trend** if `(H2 > H1) AND (L2 > L1)`
- **Bear trend** if `(H2 < H1) AND (L2 < L1)`
- **Otherwise** = Chop/Range

---

## How We Confirm Peaks/Troughs (Quantum System v3)

### Swing Low (Trough) Detection
```
Parameters:
- Lookback: 8 bars (8 hours on 1H timeframe)
- Minimum decline: 3% from recent high (last 48 bars)

A bar is confirmed as a trough when:
1. Its low is lower than ALL surrounding bars (±8 bars)
2. It came after a meaningful 3%+ decline from recent high
```

### Swing High (Peak) Detection
```
Parameters:
- Lookback: 120 bars (5 days on 1H timeframe)
- No minimum rise requirement (yet)

A bar is confirmed as a peak when:
1. Its high is higher than ALL surrounding bars (±120 bars)
2. This longer lookback filters out false peaks that get immediately violated
```

### Why Different Lookbacks?
- **Troughs (8 bars):** We want to catch reversals quickly after a decline
- **Peaks (120 bars):** We want to confirm peaks are "real" and won't be immediately broken

---

## Multi-Timeframe Alignment (HTF → LTF)

### The Pro Way to Trade

1. Pick your "market trend" timeframe (Daily or Weekly)
2. That becomes your **bias**: Bull / Bear / Chop
3. Use your entry timeframe (1H/15M) only to **time entries**

### Alignment Rules

| Daily Regime | 1H Signal | Action |
|--------------|-----------|--------|
| BULL | BUY signal | ✅ TAKE IT |
| BULL | SELL signal | ❌ SKIP (just a pullback) |
| BEAR | SELL signal | ✅ TAKE IT |
| BEAR | BUY signal | ❌ SKIP (just a bounce) |
| CHOP | Any signal | ⚠️ CAUTION (smaller size or skip) |

### HTF Bull, LTF Bear = Just a Pullback

If HTF = Bull, wait for LTF to do one of these before buying:
1. LTF prints a **higher low (HL)**, then breaks the last lower high (LH)
2. LTF reclaims key levels (like reclaiming EMA50 and holding)
3. Momentum flips back with structure (RSI back above 50 after the HL forms)

---

## Implementation in Quantum System v3

### HTFRegimeDetector Class

Determines Daily regime using:

| Factor | Bull Score | Bear Score |
|--------|------------|------------|
| Price > EMA50 | +1 | — |
| Price < EMA50 | — | +1 |
| EMA50 > EMA200 | +1 | — |
| EMA50 < EMA200 | — | +1 |
| EMA50 slope rising | +1 | — |
| EMA50 slope falling | — | +1 |
| Structure = HH/HL | +2 | — |
| Structure = LH/LL | — | +2 |

**Final Regime:**
- Bull Score ≥ 4 → BULL
- Bear Score ≥ 4 → BEAR
- Otherwise → CHOP

### Entry Gate

```python
# In _check_entry_conditions():
htf_ok, htf_reason = HTFRegimeDetector.should_allow_long(df)
if not htf_ok:
    return False, htf_reason  # "daily_bear_blocked_score=X"
```

---

## Key Corrections

| Wrong | Right |
|-------|-------|
| "Next peak/trough is higher" = bearish | "Next peak/trough is higher" = **BULLISH** |
| "Next peak/trough is lower" = bullish | "Next peak/trough is lower" = **BEARISH** |

**Remember:**
- Higher highs + Higher lows = Bulls in control
- Lower highs + Lower lows = Bears in control
- Mixed = Nobody in control (chop)

---

## Summary

> "Trade only what the market has already proven."

We don't predict trends. We **confirm** them using structure:
1. Detect peaks and troughs
2. Compare: are they making higher highs/lows or lower highs/lows?
3. Only trade in the direction the structure confirms
4. Use HTF (Daily) for bias, LTF (1H) for timing
