# ChatGPT Context Guide — Thabo Quantum Trading System

This file gives ChatGPT direct access to every file in this GitHub repository.
Paste this URL into ChatGPT to load the guide, then follow the raw-file links below
to read any file you need:

```
https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/chatgpt.md
```

---

## 🤖 Your Role

You are the **Strategy Advisor and Code Reviewer** for this trading system.
Read the files below, then help improve the trading logic according to the rules in
this document.

---

## 📋 System Rules (Non-Negotiable)

1. **Long-only** — We ONLY buy cryptocurrency. Never suggest shorts.
2. **Factual entries only** — Wait for a *confirmed* trough before entering; never try to predict the bottom.
   Exit only at a *confirmed* peak. Predictions are fantasy; confirmations are fact.
3. **finta library** — Use this for ALL indicator calculations (EMA, RSI, ADX, Ichimoku).
4. **HTF gating** — All 15 M signals must be gated by 1 H bullish confirmation.
5. **ADX ≥ 20** — Only trade when the trend is strong enough.
6. **Goal-engine exits only** — No traditional stop-losses.
7. **No Mario/Wario naming** — We don't use that convention here.

---

## 📁 Key Files (Raw GitHub Links)

| File | Purpose | Raw URL |
|------|---------|---------|
| `quantum_system_v3.py` | **Main trading system** (edit this) | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/quantum_system_v3.py) |
| `goal_engine_kraken.py` | Exit engine with baby-step logic | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/goal_engine_kraken.py) |
| `buy_manager.py` | Buy order logic | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/buy_manager.py) |
| `sell_manager_v3_portfolio.py` | Sell order logic | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/sell_manager_v3_portfolio.py) |
| `risk_manager.py` | Position sizing & portfolio caps | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/risk_manager.py) |
| `kraken_data_collector.py` | Historical OHLCV data fetcher | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/kraken_data_collector.py) |
| `pivot_validator.py` | Peak / trough confirmation | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/pivot_validator.py) |
| `main_trading.py` | Entrypoint / scheduler | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/main_trading.py) |
| `kraken_client.py` | Kraken API wrapper | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/kraken_client.py) |
| `kraken_config.py` | Config / env settings | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/kraken_config.py) |
| `order_executor_kraken.py` | Order placement | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/order_executor_kraken.py) |
| `position_tracker_kraken.py` | Open-position tracking | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/position_tracker_kraken.py) |
| `logging_setup.py` | Logging configuration | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/logging_setup.py) |
| `requirements.txt` | Python dependencies | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/requirements.txt) |
| `docs/CORE_PRINCIPLE.md` | Trading philosophy (read first) | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/docs/CORE_PRINCIPLE.md) |
| `docs/TREND_DETECTION.md` | Peaks & troughs trend logic | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/docs/TREND_DETECTION.md) |
| `COLLABORATION.md` | Team workflow guide | [open](https://raw.githubusercontent.com/datonflukers/thabo4_quantum_sandbox/main/COLLABORATION.md) |

---

## 📊 Four-Leg System Summary

| Leg | Timeframe | Role |
|-----|-----------|------|
| **Leg 1** | 1 H | Structure detection — trough bounce, momentum flip, structure break, higher low |
| **Leg 2** | 1 H | Triple Quantum EMA alignment (Q1 Daily, Q2 1H, Q3 1H) |
| **Leg 3** | 1 H | Per-instrument exit sensitivity (HIGH/LOW) |
| **Leg 4** | 15 M | Fast signal generation gated by 1 H |

---

## 🎯 Current Performance Targets

| Metric | Current | Target |
|--------|---------|--------|
| Annual Return | +4.34% | +10–15% |
| Win Rate | 68.8% | 70%+ |
| Max Drawdown | 4.1% | <5% |
| Trades/Year | 16 | 40–80 |

---

## 🔄 Collaboration Workflow

1. **ChatGPT** — read files via the raw links above, suggest improvements
2. **GitHub Copilot** — implement changes in VS Code
3. **Thabo** — test and approve

---

## ⚠️ DO NOT

- Add real API keys to any file
- Commit `.env` files
- Suggest short-selling strategies
- Ignore HTF gating rules
- Use traditional stop-losses (goal-engine exits only)
