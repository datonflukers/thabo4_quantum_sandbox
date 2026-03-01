# Collaboration Guide - Thabo Quantum System

## 📍 Location
**Dropbox Path**: `C:\Users\daton\Dropbox\thabo4_quantum_sandbox`

---

## 🤝 Collaborators

| Agent | Role | Access |
|-------|------|--------|
| **GitHub Copilot** | Primary developer, code editing | Full read/write via VS Code |
| **ChatGPT** | Strategy advisor, code review | Read via Dropbox sharing |
| **Thabo** | Project owner, decision maker | Full access |

---

## 📋 System Rules

### LONG-ONLY Trading
- We ONLY buy cryptocurrency
- If market is bearish → DO NOT TRADE
- No shorts, no Wario strategy

### Core Philosophy
> "Trade only what the market has already proven. Enter at confirmed troughs (factual) rather than peaks, and exit at confirmed peaks (factual). Predictions are fantasy; confirmations are fact."

### Indicators (via finta library)
- EMA 9/21/55 - Triple quantum alignment
- RSI - Momentum confirmation (>48 entry, <40 exit)
- ADX - Trend strength (>20 for entry)
- Ichimoku Cloud - Price above cloud = bullish
- +DI/-DI - Directional confirmation (+DI > -DI for longs)

---

## 📊 Current Architecture

### Four-Leg System

**Leg 1 (1H)**: Structure Detection
- L1: Trough bounce (8-bar confirmation)
- L2: Momentum flip (RSI > 50, EMA bullish)
- L3: Structure break (3%+ above previous peak)
- L4: Higher low (30%+ retention) → TRADE TRIGGER

**Leg 2 (1H)**: Triple Quantum EMA Entry
- Q1 (Daily): EMA 50 > EMA 200
- Q2 (1H): EMA 21 > EMA 55
- Q3 (1H): EMA 9 > EMA 21

**Leg 3 (1H)**: Per-Instrument Exit Sensitivity
- HIGH SENS: LINK, POL, AVAX, DOT, LTC → Exit on Q2 flip
- LOW SENS: BTC, ETH, ADA, XRP, SOL → Exit on Q1 flip only

**Leg 4 (15M)**: Fast Signal Generation (NEW)
- RSI >= 48
- Price above Ichimoku Cloud
- Price above EMA 50
- ADX >= 20
- Gated by HTF (1H) bullish confirmation

---

## 🎯 Current Goals

1. **Increase trade frequency** (currently 16 trades/year → target 40-80)
2. **Reduce deep losses** (BTC -22% was worst)
3. **Integrate 15M timeframe** for faster signals
4. **ADX filter** to avoid chop (ADX >= 20)

---

## ⚠️ Important Notes for ChatGPT

1. **No Mario/Wario** - We don't use that naming convention
2. **Long-only** - If you suggest shorts, we can't use them
3. **finta library** - Use this for all indicator calculations
4. **HTF gating** - All 15M signals must be gated by 1H bullish confirmation
5. **ADX >= 20** (not 14-20) - Higher ADX means stronger trend

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `quantum_system_v3.py` | Main trading system (EDIT THIS) |
| `goal_engine_kraken.py` | Exit engine with baby steps |
| `buy_manager.py` | Buy order logic |
| `sell_manager_v3_portfolio.py` | Sell order logic |
| `kraken_data_collector.py` | Historical data |
| `docs/CORE_PRINCIPLE.md` | Trading philosophy |

---

## 🔄 Workflow

1. **ChatGPT** reviews code and suggests improvements
2. **Copilot** implements changes in VS Code
3. **Thabo** tests and approves
4. Changes sync automatically via Dropbox

---

## 📈 Performance Targets

| Metric | Current | Target |
|--------|---------|--------|
| Annual Return | +4.34% | +10-15% |
| Win Rate | 68.8% | 70%+ |
| Max Drawdown | 4.1% | <5% |
| Trades/Year | 16 | 40-80 |

---

## 🚫 DO NOT

- Add real API keys to any file
- Commit .env files
- Suggest short selling strategies
- Ignore the HTF (Higher Timeframe) gating rules
