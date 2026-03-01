"""Analyze which instruments have the deepest losses."""

# From the 12-month backtest output, extract all losses by instrument

losses_by_instrument = {
    "AVAX/USD": [
        -6.36, -11.25, -7.14, -1.54, -2.47, -7.34, -11.82,
    ],
    "DOT/USD": [
        -16.20, -16.15, -1.12, -0.62, -1.30,
    ],
    "LTC/USD": [
        -2.86, -0.34, -0.51, -1.55, -0.95, -3.16, -1.58, -1.91, -12.05,
    ],
    "LINK/USD": [
        -4.13, -6.80, -2.66,
    ],
    "XRP/USD": [
        -0.81, -4.51, -5.27,
    ],
    "BTC/USD": [
        -0.43, -0.47, -4.46,
    ],
    "ETH/USD": [
        # Very few losses!
    ],
    "POL/USD": [
        # No losses in backtest
    ],
    "ADA/USD": [
        # No losses in backtest
    ],
}

print("=" * 70)
print("  DEEP LOSS ANALYSIS BY INSTRUMENT")
print("=" * 70)

print("\n  WORST LOSSES (sorted by deepest single loss)")
print("-" * 70)
print(f"  {'Instrument':<12} {'Worst Loss':>12} {'Avg Loss':>12} {'# Losses':>10}")
print("-" * 70)

# Calculate stats and sort by worst loss
stats = []
for inst, losses in losses_by_instrument.items():
    if losses:
        worst = min(losses)
        avg = sum(losses) / len(losses)
        stats.append((inst, worst, avg, len(losses)))
    else:
        stats.append((inst, 0, 0, 0))

# Sort by worst loss (most negative first)
stats.sort(key=lambda x: x[1])

for inst, worst, avg, count in stats:
    if count > 0:
        print(f"  {inst:<12} {worst:>11.2f}% {avg:>11.2f}% {count:>10}")
    else:
        print(f"  {inst:<12} {'N/A':>12} {'N/A':>12} {0:>10}")

print("\n  RISK TIER RECOMMENDATION")
print("-" * 70)
print("  🔴 HIGH RISK (deep losses, cut at $3):")
print("      DOT/USD  - Worst: -16.20% (catastrophic)")
print("      LTC/USD  - Worst: -12.05% (very bad)")
print("      AVAX/USD - Worst: -11.82% (very bad)")
print()
print("  🟡 MEDIUM RISK (moderate losses, $5-7 threshold):")
print("      LINK/USD - Worst: -6.80%")
print("      XRP/USD  - Worst: -5.27%")
print("      BTC/USD  - Worst: -4.46%")
print()
print("  🟢 LOW RISK (rare/no losses, $10 trailing):")
print("      ETH/USD  - 100% win rate")
print("      POL/USD  - 100% win rate")
print("      ADA/USD  - 100% win rate")

print("\n  CURRENT TIER CONFIG")
print("-" * 70)
print("  Baby Step ($3): AVAX, DOT, LTC  ← CORRECT (high risk)")
print("  Trailing ($10): BTC, ETH, POL, ADA, SOL, LINK, XRP")
print()
print("  SUGGESTION: Move LINK/XRP to baby step tier?")
print("    LINK had -6.80% loss")
print("    XRP had -5.27% loss")
print("=" * 70)
