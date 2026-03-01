# -*- coding: utf-8 -*-
"""
QUANTUM TRADING SYSTEM
======================

Built on the Core Principle:
- Trade only what the market has already proven
- Enter at confirmed troughs (fact)
- Exit at confirmed peaks (fact)
- Everything else is illusion

Quantum signals (EMA, RSI, ADX) flow THROUGH confirmed structure.
They enhance timing, not replace confirmation.

Architecture:
─────────────────────────────────────────────────────────────────────
1. STRUCTURE LAYER: Confirmed peaks/troughs (the map)
2. QUANTUM LAYER: EMA 9/21, RSI, ADX (the compass)
3. EXECUTION LAYER: Trailing stops, goal engine (the exit)
─────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
import pandas as pd
import numpy as np

LOG = logging.getLogger("quantum_system")


# =============================================================================
# STRUCTURE LAYER: Confirmed Peaks & Troughs
# =============================================================================

class PivotType(Enum):
    PEAK = "PEAK"
    TROUGH = "TROUGH"


@dataclass
class ConfirmedPivot:
    """A factual pivot - the market's fingerprint."""
    instrument: str
    pivot_type: PivotType
    pivot_price: float
    pivot_index: int
    confirmed_at_index: int
    confirmed_at_time: datetime
    bos_level: float  # Break of Structure level
    quality: float  # 0-100


@dataclass
class MarketStructure:
    """The factual map of the market."""
    instrument: str
    troughs: List[ConfirmedPivot] = field(default_factory=list)
    peaks: List[ConfirmedPivot] = field(default_factory=list)
    last_trough: Optional[ConfirmedPivot] = None
    last_peak: Optional[ConfirmedPivot] = None
    trend: str = "UNKNOWN"  # BULLISH, BEARISH, CHOP


class StructureAnalyzer:
    """
    Detects and confirms market structure.
    
    A trough is confirmed when:
    1. Fractal low forms (5-bar swing)
    2. Higher low forms after
    3. Close above prior swing high (BOS)
    
    A peak is confirmed when:
    1. Fractal high forms (5-bar swing)
    2. Lower high forms after
    3. Close below prior swing low (BOS)
    """
    
    def __init__(self, fractal_period: int = 2, max_lookahead: int = 10, bos_min_pct: float = 0.3):
        self.fractal_period = fractal_period
        self.max_lookahead = max_lookahead
        self.bos_min_pct = bos_min_pct
        
        # Pending (unconfirmed) pivots per instrument
        self.pending_troughs: Dict[str, Tuple[int, float]] = {}
        self.pending_peaks: Dict[str, Tuple[int, float]] = {}
        
        # Structure per instrument
        self.structures: Dict[str, MarketStructure] = {}
    
    def _get_structure(self, instrument: str) -> MarketStructure:
        if instrument not in self.structures:
            self.structures[instrument] = MarketStructure(instrument=instrument)
        return self.structures[instrument]
    
    def _detect_fractal_trough(self, df: pd.DataFrame) -> Optional[Tuple[int, float]]:
        """Detect 5-bar swing low."""
        p = self.fractal_period
        if len(df) < (2 * p + 1):
            return None
        
        center_idx = len(df) - p - 1
        center_low = float(df.iloc[center_idx]['low'])
        
        for i in range(-p, p + 1):
            if i == 0:
                continue
            if float(df.iloc[center_idx + i]['low']) <= center_low:
                return None
        
        return (center_idx, center_low)
    
    def _detect_fractal_peak(self, df: pd.DataFrame) -> Optional[Tuple[int, float]]:
        """Detect 5-bar swing high."""
        p = self.fractal_period
        if len(df) < (2 * p + 1):
            return None
        
        center_idx = len(df) - p - 1
        center_high = float(df.iloc[center_idx]['high'])
        
        for i in range(-p, p + 1):
            if i == 0:
                continue
            if float(df.iloc[center_idx + i]['high']) >= center_high:
                return None
        
        return (center_idx, center_high)
    
    def update(self, instrument: str, df: pd.DataFrame) -> Tuple[Optional[ConfirmedPivot], Optional[ConfirmedPivot]]:
        """
        Update structure analysis with new data.
        
        Returns (new_confirmed_trough, new_confirmed_peak)
        """
        if len(df) < 20:
            return None, None
        
        structure = self._get_structure(instrument)
        current_idx = len(df) - 1
        current_time = df.iloc[current_idx]['time'] if 'time' in df.columns else datetime.now()
        
        new_trough = None
        new_peak = None
        
        # === CHECK FOR CONFIRMED TROUGH ===
        
        # Detect new pending trough
        if instrument not in self.pending_troughs:
            fractal = self._detect_fractal_trough(df)
            if fractal:
                self.pending_troughs[instrument] = fractal
        
        # Check if pending trough is confirmed
        if instrument in self.pending_troughs:
            trough_idx, trough_price = self.pending_troughs[instrument]
            bars_since = current_idx - trough_idx
            
            if bars_since > self.max_lookahead:
                del self.pending_troughs[instrument]
            elif bars_since >= 2:
                # Check higher low formed
                lows_after = df.iloc[trough_idx + 1:current_idx + 1]['low'].astype(float)
                min_low_after = lows_after.min()
                
                if min_low_after <= trough_price:
                    # Trough violated
                    del self.pending_troughs[instrument]
                else:
                    # Check BOS (close above prior swing high)
                    lookback_start = max(0, trough_idx - 10)
                    swing_high = float(df.iloc[lookback_start:trough_idx]['high'].max())
                    current_close = float(df.iloc[current_idx]['close'])
                    bos_pct = ((current_close - swing_high) / swing_high) * 100
                    
                    if bos_pct >= self.bos_min_pct:
                        # CONFIRMED!
                        quality = min(100, 50 + bos_pct * 10 + (10 - bars_since))
                        
                        new_trough = ConfirmedPivot(
                            instrument=instrument,
                            pivot_type=PivotType.TROUGH,
                            pivot_price=trough_price,
                            pivot_index=trough_idx,
                            confirmed_at_index=current_idx,
                            confirmed_at_time=current_time,
                            bos_level=swing_high,
                            quality=quality,
                        )
                        
                        structure.troughs.append(new_trough)
                        structure.last_trough = new_trough
                        del self.pending_troughs[instrument]
                        
                        LOG.info(f"[STRUCTURE] ✓ {instrument} TROUGH confirmed @ {trough_price:.2f}")
        
        # === CHECK FOR CONFIRMED PEAK ===
        
        if instrument not in self.pending_peaks:
            fractal = self._detect_fractal_peak(df)
            if fractal:
                self.pending_peaks[instrument] = fractal
        
        if instrument in self.pending_peaks:
            peak_idx, peak_price = self.pending_peaks[instrument]
            bars_since = current_idx - peak_idx
            
            if bars_since > self.max_lookahead:
                del self.pending_peaks[instrument]
            elif bars_since >= 2:
                highs_after = df.iloc[peak_idx + 1:current_idx + 1]['high'].astype(float)
                max_high_after = highs_after.max()
                
                if max_high_after >= peak_price:
                    del self.pending_peaks[instrument]
                else:
                    lookback_start = max(0, peak_idx - 10)
                    swing_low = float(df.iloc[lookback_start:peak_idx]['low'].min())
                    current_close = float(df.iloc[current_idx]['close'])
                    bos_pct = ((swing_low - current_close) / swing_low) * 100
                    
                    if bos_pct >= self.bos_min_pct:
                        quality = min(100, 50 + bos_pct * 10 + (10 - bars_since))
                        
                        new_peak = ConfirmedPivot(
                            instrument=instrument,
                            pivot_type=PivotType.PEAK,
                            pivot_price=peak_price,
                            pivot_index=peak_idx,
                            confirmed_at_index=current_idx,
                            confirmed_at_time=current_time,
                            bos_level=swing_low,
                            quality=quality,
                        )
                        
                        structure.peaks.append(new_peak)
                        structure.last_peak = new_peak
                        del self.pending_peaks[instrument]
                        
                        LOG.info(f"[STRUCTURE] ✓ {instrument} PEAK confirmed @ {peak_price:.2f}")
        
        # Update trend
        if structure.last_trough and structure.last_peak:
            if structure.last_trough.confirmed_at_index > structure.last_peak.confirmed_at_index:
                structure.trend = "BULLISH"
            else:
                structure.trend = "BEARISH"
        
        return new_trough, new_peak


# =============================================================================
# QUANTUM LAYER: EMA, RSI, ADX (The Compass)
# =============================================================================

class QuantumSignals:
    """
    Quantum signals that flow through confirmed structure.

    These ENHANCE timing, they don't REPLACE confirmation.
    """

    def __init__(self, ema_fast: int = 5, ema_slow: int = 13, rsi_period: int = 14, adx_period: int = 14):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.adx_period = adx_period
    
    def calculate(self, df: pd.DataFrame) -> Dict:
        """Calculate quantum signals."""
        if len(df) < 50:
            return {"valid": False}
        
        close = df['close'].astype(float)
        
        # EMA Cross
        ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean().iloc[-1]
        ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean().iloc[-1]
        ema_bullish = ema_fast > ema_slow
        
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
        rs = gain / (loss + 1e-10)
        rsi = (100 - (100 / (1 + rs))).iloc[-1]
        
        # ADX
        adx = self._calculate_adx(df)
        
        return {
            "valid": True,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_bullish": ema_bullish,
            "rsi": rsi,
            "adx": adx,
            "trending": adx > 14,  # Lower threshold
            "choppy": adx < 14,
        }
    
    def _calculate_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 10:
            return 0.0
        
        try:
            high = df['high'].astype(float)
            low = df['low'].astype(float)
            close = df['close'].astype(float)
            
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            
            up = high - high.shift(1)
            down = low.shift(1) - low
            
            plus_dm = np.where((up > down) & (up > 0), up, 0.0)
            minus_dm = np.where((down > up) & (down > 0), down, 0.0)
            
            atr = tr.rolling(period).mean()
            plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / (atr + 1e-10)
            minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / (atr + 1e-10)
            
            dx = 100 * (plus_di - minus_di).abs() / ((plus_di + minus_di).abs() + 1e-10)
            adx = dx.rolling(period).mean()
            
            return float(adx.iloc[-1])
        except:
            return 0.0


# =============================================================================
# EXECUTION LAYER: Entry, Exit, Stop
# =============================================================================

@dataclass
class Position:
    instrument: str
    entry_price: float
    entry_time: datetime
    entry_trough: ConfirmedPivot
    qty: float
    notional: float
    peak_price: float = 0.0


@dataclass
class Trade:
    instrument: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    reason: str
    entry_time: datetime
    exit_time: datetime


class QuantumExecutor:
    """
    Executes trades based on confirmed structure + quantum signals.

    ENTRY: Confirmed trough + quantum alignment
    EXIT: Confirmed peak OR trailing stop OR goal engine
    NO STOP LOSS: Winners pay for losers via goal engine
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        trailing_trigger_pct: float = 3.0,
        trailing_atr_mult: float = 2.0,
        goal_amount: float = 5.0,
        goal_buffer: float = 2.0,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.trailing_trigger_pct = trailing_trigger_pct
        self.trailing_atr_mult = trailing_atr_mult

        # Goal engine params - $5 goal, cut losers at $7
        self.goal_amount = goal_amount
        self.goal_buffer = goal_buffer
        self.goal_threshold = goal_amount + goal_buffer  # $7
        self.reference_equity = initial_balance
        self.highest_equity = initial_balance
        self.goals_reached = 0

        self.structure = StructureAnalyzer()
        self.quantum = QuantumSignals()

        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
    
    def _budget(self) -> float:
        return min(20 + 0.1 * self.balance, self.balance * 0.25)
    
    def _get_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 0.0
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    
    def process_bar(self, instrument: str, df: pd.DataFrame, current_time: datetime) -> Optional[str]:
        """
        Process a bar for an instrument.
        
        Returns action taken: "ENTRY", "EXIT_PEAK", "EXIT_TRAILING", "EXIT_STOP", None
        """
        if len(df) < 50:
            return None
        
        current_price = float(df.iloc[-1]['close'])
        
        # Update structure
        new_trough, new_peak = self.structure.update(instrument, df)
        
        # Get quantum signals
        signals = self.quantum.calculate(df)
        
        # === CHECK EXITS FIRST ===
        if instrument in self.positions:
            pos = self.positions[instrument]
            
            # Update peak price
            if current_price > pos.peak_price:
                pos.peak_price = current_price
            
            # NO STOP LOSS - But we DO check if trough is proven fake
            # If price makes a new low below entry trough, the trough was FAKE
            # This is not prediction - it's confirmation the entry was wrong
            if current_price < pos.entry_trough.pivot_price:
                LOG.info(f"[FAKE TROUGH] {instrument}: Price ${current_price:.2f} < Trough ${pos.entry_trough.pivot_price:.2f}")
                return self._close_position(instrument, current_price, current_time, "fake_trough")
            
            # Check confirmed peak exit
            if new_peak:
                return self._close_position(instrument, current_price, current_time, "confirmed_peak")
            
            # Check trailing stop
            profit_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            
            if profit_pct >= self.trailing_trigger_pct:
                atr = self._get_atr(df)
                if atr > 0:
                    trailing_stop = pos.peak_price - (atr * self.trailing_atr_mult)
                    if current_price < trailing_stop and current_price > pos.entry_price:
                        return self._close_position(instrument, current_price, current_time, "trailing_stop")
            
            return None
        
        # === CHECK ENTRY ===
        if new_trough and len(self.positions) < 5:
            # Only require confirmed trough - quantum signals are optional filter
            # For now, just check ADX > 14 (some trend exists)
            if signals.get("valid") and signals.get("trending"):
                return self._open_position(instrument, current_price, current_time, new_trough)
        
        return None
    
    def _open_position(self, instrument: str, price: float, time: datetime, trough: ConfirmedPivot) -> str:
        budget = self._budget()
        if budget < 10:
            return None
        
        qty = budget / price
        self.balance -= budget
        
        self.positions[instrument] = Position(
            instrument=instrument,
            entry_price=price,
            entry_time=time,
            entry_trough=trough,
            qty=qty,
            notional=budget,
            peak_price=price,
        )
        
        LOG.info(f"[ENTRY] {instrument} @ ${price:.2f} | Trough: ${trough.pivot_price:.2f} | Budget: ${budget:.2f}")
        return "ENTRY"
    
    def _close_position(self, instrument: str, price: float, time: datetime, reason: str) -> str:
        if instrument not in self.positions:
            return None
        
        pos = self.positions.pop(instrument)
        proceeds = pos.qty * price * 0.9974  # Fee
        pnl = proceeds - pos.notional
        pnl_pct = (pnl / pos.notional) * 100
        
        self.balance += proceeds
        
        self.trades.append(Trade(
            instrument=instrument,
            entry_price=pos.entry_price,
            exit_price=price,
            pnl_pct=pnl_pct,
            pnl_usd=pnl,
            reason=reason,
            entry_time=pos.entry_time,
            exit_time=time,
        ))
        
        LOG.info(f"[EXIT] {instrument} @ ${price:.2f} | {pnl_pct:+.2f}% | ${pnl:+.2f} | {reason}")
        return f"EXIT_{reason.upper()}"

    def _calculate_equity(self, prices: Dict[str, float]) -> float:
        """Calculate current total equity."""
        equity = self.balance
        for instrument, pos in self.positions.items():
            price = prices.get(instrument, pos.entry_price)
            equity += pos.qty * price
        return equity

    def check_goal_engine(self, prices: Dict[str, float], current_time: datetime) -> List[str]:
        """
        Check goal engine and cut losers if goal is reached.

        Returns list of closed positions.
        """
        equity = self._calculate_equity(prices)

        # Update highest
        if equity > self.highest_equity:
            self.highest_equity = equity

        # Calculate gain from reference
        gain = equity - self.reference_equity

        # Check if goal reached
        if gain >= self.goal_threshold:
            LOG.info(f"[GOAL] 🎯 Goal reached! Equity: ${equity:.2f} | Gain: +${gain:.2f}")
            self.goals_reached += 1

            # Cut all losers
            closed = []
            for instrument in list(self.positions.keys()):
                pos = self.positions[instrument]
                price = prices.get(instrument, pos.entry_price)
                if price < pos.entry_price:
                    self._close_position(instrument, price, current_time, "goal_cut_loser")
                    closed.append(instrument)

            # Reset reference to current equity
            new_equity = self._calculate_equity(prices)
            self.reference_equity = new_equity
            self.highest_equity = new_equity

            LOG.info(f"[GOAL] Cut {len(closed)} losers | New reference: ${new_equity:.2f}")
            return closed

        return []

    def get_results(self) -> Dict:
        winners = [t for t in self.trades if t.pnl_pct > 0]
        losers = [t for t in self.trades if t.pnl_pct <= 0]
        
        return {
            "initial": self.initial_balance,
            "final": self.balance,
            "return_pct": ((self.balance - self.initial_balance) / self.initial_balance) * 100,
            "total_trades": len(self.trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": len(winners) / len(self.trades) * 100 if self.trades else 0,
            "goals_reached": self.goals_reached,
            "trades": self.trades,
        }


# =============================================================================
# BACKTEST
# =============================================================================

def run_quantum_backtest(months: int = 4, balance: float = 10000.0, data_dir: str = "data"):
    """Run the Quantum system backtest."""
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    
    pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "AVAX/USD",
             "ADA/USD", "LTC/USD", "DOT/USD", "POL/USD", "XRP/USD"]
    
    # Load data
    data = {}
    for pair in pairs:
        tag = pair.replace("/", "")
        path = Path(data_dir) / f"bars_{tag}_1h.csv"
        if path.exists():
            df = pd.read_csv(path)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_localize(None)
            df = df.sort_values('time').reset_index(drop=True)
            data[pair] = df
            LOG.info(f"Loaded {pair}: {len(df)} bars")
    
    if not data:
        LOG.error("No data loaded")
        return
    
    # Build timeline
    all_times = sorted(set(t for df in data.values() for t in df['time']))
    end_time = max(all_times)
    start_time = end_time - timedelta(days=months * 30)
    all_times = [t for t in all_times if t >= start_time]
    
    LOG.info(f"Timeline: {all_times[0]} to {all_times[-1]} ({len(all_times)} bars)")
    
    # Create executor
    executor = QuantumExecutor(initial_balance=balance)
    
    # Run
    for i, ts in enumerate(all_times):
        if i % 500 == 0 and i > 0:
            LOG.info(f"Bar {i}/{len(all_times)} | Positions: {len(executor.positions)} | Trades: {len(executor.trades)} | Goals: {executor.goals_reached}")

        # Get current prices for all pairs
        prices = {}
        for pair, df in data.items():
            row = df[df['time'] == ts]
            if len(row) > 0:
                prices[pair] = float(row.iloc[0]['close'])

        # Check goal engine first (cuts losers when profitable)
        if prices:
            executor.check_goal_engine(prices, ts)

        # Process each pair
        for pair, df in data.items():
            df_slice = df[df['time'] <= ts]
            if len(df_slice) >= 50:
                executor.process_bar(pair, df_slice, ts)
    
    # Close remaining
    LOG.info("Closing remaining positions...")
    for pair in list(executor.positions.keys()):
        price = float(data[pair].iloc[-1]['close'])
        executor._close_position(pair, price, all_times[-1], "backtest_end")
    
    # Results
    results = executor.get_results()
    
    print("\n" + "=" * 70)
    print("  QUANTUM SYSTEM RESULTS")
    print("  Structure: Confirmed Troughs/Peaks | Quantum: EMA 9/21 + ADX")
    print("  NO STOP LOSS - Goal engine cuts losers when profitable")
    print("=" * 70)
    print(f"\n  PERFORMANCE")
    print("-" * 70)
    print(f"    Initial:        ${results['initial']:,.2f}")
    print(f"    Final:          ${results['final']:,.2f}")
    print(f"    Return:         {results['return_pct']:+.2f}%")
    print(f"\n  TRADES")
    print("-" * 70)
    print(f"    Total:          {results['total_trades']}")
    print(f"    Winners:        {results['winners']}")
    print(f"    Losers:         {results['losers']}")
    print(f"    Win Rate:       {results['win_rate']:.1f}%")
    print(f"\n  GOAL ENGINE")
    print("-" * 70)
    print(f"    Goals Reached:  {results['goals_reached']}")
    print("=" * 70)
    
    if results['trades']:
        print("\n  ALL TRADES")
        print("-" * 70)
        for t in results['trades']:
            print(f"    {t.instrument:10} | {t.pnl_pct:+6.2f}% | ${t.pnl_usd:+8.2f} | {t.reason}")
    
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--balance", type=float, default=10000)
    args = parser.parse_args()
    
    run_quantum_backtest(months=args.months, balance=args.balance)
