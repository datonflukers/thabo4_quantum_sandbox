# -*- coding: utf-8 -*-
"""
QUANTUM TRADING SYSTEM v2
=========================

RULES (Non-Negotiable):
─────────────────────────────────────────────────────────────────────────────
1. CONFIRMED TROUGH: Price rises 5-10% above lowest low + several bars close above
2. CONFIRMED PEAK: Price falls 5-10% from highest high + several bars close below
3. STORE all confirmed troughs/peaks as factual structural points
4. NEVER open trades at peaks
5. NEVER use stop losses
6. NEVER allow goals to retract - once exceeded, profits protected by goal-cut
7. All entries follow widened EMA-cross confirmation
8. All exits follow peak confirmation OR trailing logic

This system trades only on COMPLETED, PROVEN market structure, not predictions.
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
import pandas as pd
import numpy as np

LOG = logging.getLogger("quantum_v2")


# =============================================================================
# STRUCTURE STORAGE: Confirmed Peaks & Troughs (Factual Points)
# =============================================================================

@dataclass
class ConfirmedTrough:
    """A factual trough - proven by 5-10% rise + bars closing above."""
    instrument: str
    trough_price: float
    trough_index: int
    trough_time: datetime
    confirmed_at_index: int
    confirmed_at_time: datetime
    confirmation_rise_pct: float  # How much price rose to confirm
    bars_above: int  # How many bars closed above trough


@dataclass
class ConfirmedPeak:
    """A factual peak - proven by 5-10% fall + bars closing below."""
    instrument: str
    peak_price: float
    peak_index: int
    peak_time: datetime
    confirmed_at_index: int
    confirmed_at_time: datetime
    confirmation_fall_pct: float  # How much price fell to confirm
    bars_below: int  # How many bars closed below peak


class StructureStore:
    """
    Stores all confirmed peaks and troughs as factual market structure.
    
    CONFIRMATION RULES:
    - Trough: Price must rise 5-10% above lowest low + 3+ bars close above
    - Peak: Price must fall 5-10% from highest high + 3+ bars close below
    """
    
    def __init__(
        self,
        min_confirm_pct: float = 5.0,  # Minimum 5% move to confirm
        min_bars_confirm: int = 3,     # Minimum 3 bars to confirm
    ):
        self.min_confirm_pct = min_confirm_pct
        self.min_bars_confirm = min_bars_confirm
        
        # Storage per instrument
        self.troughs: Dict[str, List[ConfirmedTrough]] = {}
        self.peaks: Dict[str, List[ConfirmedPeak]] = {}
        
        # Pending (unconfirmed) - tracking potential pivots
        self.pending_trough: Dict[str, Tuple[int, float, datetime]] = {}  # (index, price, time)
        self.pending_peak: Dict[str, Tuple[int, float, datetime]] = {}
        
        # Bars above/below tracking
        self.bars_above_trough: Dict[str, int] = {}
        self.bars_below_peak: Dict[str, int] = {}
    
    def _get_troughs(self, instrument: str) -> List[ConfirmedTrough]:
        if instrument not in self.troughs:
            self.troughs[instrument] = []
        return self.troughs[instrument]
    
    def _get_peaks(self, instrument: str) -> List[ConfirmedPeak]:
        if instrument not in self.peaks:
            self.peaks[instrument] = []
        return self.peaks[instrument]
    
    def update(self, instrument: str, df: pd.DataFrame) -> Tuple[Optional[ConfirmedTrough], Optional[ConfirmedPeak]]:
        """
        Update structure analysis with new bar.
        
        Returns (new_confirmed_trough, new_confirmed_peak) if any confirmed this bar.
        """
        if len(df) < 10:
            return None, None
        
        current_idx = len(df) - 1
        current_bar = df.iloc[current_idx]
        current_close = float(current_bar['close'])
        current_low = float(current_bar['low'])
        current_high = float(current_bar['high'])
        current_time = current_bar['time'] if 'time' in df.columns else datetime.now()
        
        new_trough = None
        new_peak = None
        
        # ========== TROUGH DETECTION & CONFIRMATION ==========
        
        # Check for new lowest low (potential trough)
        lookback = min(50, len(df))
        recent_lows = df.iloc[-lookback:]['low'].astype(float)
        min_low = recent_lows.min()
        min_low_idx = recent_lows.idxmin()
        
        # If we have a new lowest low, track it as pending
        if instrument not in self.pending_trough or min_low < self.pending_trough[instrument][1]:
            min_low_time = df.iloc[min_low_idx]['time'] if 'time' in df.columns else datetime.now()
            self.pending_trough[instrument] = (min_low_idx, min_low, min_low_time)
            self.bars_above_trough[instrument] = 0
        
        # Check confirmation of pending trough
        if instrument in self.pending_trough:
            trough_idx, trough_price, trough_time = self.pending_trough[instrument]
            
            # Is current close above trough?
            if current_close > trough_price:
                self.bars_above_trough[instrument] = self.bars_above_trough.get(instrument, 0) + 1
                
                # Calculate rise from trough
                rise_pct = ((current_close - trough_price) / trough_price) * 100
                bars_above = self.bars_above_trough[instrument]
                
                # Check confirmation: 5%+ rise AND 3+ bars above
                if rise_pct >= self.min_confirm_pct and bars_above >= self.min_bars_confirm:
                    new_trough = ConfirmedTrough(
                        instrument=instrument,
                        trough_price=trough_price,
                        trough_index=trough_idx,
                        trough_time=trough_time,
                        confirmed_at_index=current_idx,
                        confirmed_at_time=current_time,
                        confirmation_rise_pct=rise_pct,
                        bars_above=bars_above,
                    )
                    
                    self._get_troughs(instrument).append(new_trough)
                    
                    # Reset pending
                    del self.pending_trough[instrument]
                    self.bars_above_trough[instrument] = 0
                    
                    LOG.info(f"[STRUCTURE] ✓ {instrument} TROUGH CONFIRMED @ ${trough_price:.2f}")
                    LOG.info(f"  Rise: +{rise_pct:.1f}% | Bars above: {bars_above}")
            else:
                # Close below trough, reset bar count
                self.bars_above_trough[instrument] = 0
        
        # ========== PEAK DETECTION & CONFIRMATION ==========
        
        # Check for new highest high (potential peak)
        recent_highs = df.iloc[-lookback:]['high'].astype(float)
        max_high = recent_highs.max()
        max_high_idx = recent_highs.idxmax()
        
        # If we have a new highest high, track it as pending
        if instrument not in self.pending_peak or max_high > self.pending_peak[instrument][1]:
            max_high_time = df.iloc[max_high_idx]['time'] if 'time' in df.columns else datetime.now()
            self.pending_peak[instrument] = (max_high_idx, max_high, max_high_time)
            self.bars_below_peak[instrument] = 0
        
        # Check confirmation of pending peak
        if instrument in self.pending_peak:
            peak_idx, peak_price, peak_time = self.pending_peak[instrument]
            
            # Is current close below peak?
            if current_close < peak_price:
                self.bars_below_peak[instrument] = self.bars_below_peak.get(instrument, 0) + 1
                
                # Calculate fall from peak
                fall_pct = ((peak_price - current_close) / peak_price) * 100
                bars_below = self.bars_below_peak[instrument]
                
                # Check confirmation: 5%+ fall AND 3+ bars below
                if fall_pct >= self.min_confirm_pct and bars_below >= self.min_bars_confirm:
                    new_peak = ConfirmedPeak(
                        instrument=instrument,
                        peak_price=peak_price,
                        peak_index=peak_idx,
                        peak_time=peak_time,
                        confirmed_at_index=current_idx,
                        confirmed_at_time=current_time,
                        confirmation_fall_pct=fall_pct,
                        bars_below=bars_below,
                    )
                    
                    self._get_peaks(instrument).append(new_peak)
                    
                    # Reset pending
                    del self.pending_peak[instrument]
                    self.bars_below_peak[instrument] = 0
                    
                    LOG.info(f"[STRUCTURE] ✓ {instrument} PEAK CONFIRMED @ ${peak_price:.2f}")
                    LOG.info(f"  Fall: -{fall_pct:.1f}% | Bars below: {bars_below}")
            else:
                # Close above peak, reset bar count
                self.bars_below_peak[instrument] = 0
        
        return new_trough, new_peak
    
    def get_last_trough(self, instrument: str) -> Optional[ConfirmedTrough]:
        troughs = self._get_troughs(instrument)
        return troughs[-1] if troughs else None
    
    def get_last_peak(self, instrument: str) -> Optional[ConfirmedPeak]:
        peaks = self._get_peaks(instrument)
        return peaks[-1] if peaks else None
    
    def get_all_troughs(self, instrument: str) -> List[ConfirmedTrough]:
        return self._get_troughs(instrument)
    
    def get_all_peaks(self, instrument: str) -> List[ConfirmedPeak]:
        return self._get_peaks(instrument)


# =============================================================================
# EMA CROSS CONFIRMATION (Widened)
# =============================================================================

class EMAConfirmation:
    """
    Widened EMA cross confirmation + RSI + ADX for entries.

    Entry allowed only when:
    - EMA fast > EMA slow (bullish cross)
    - RSI > 50 (momentum direction confirmed)
    - ADX > 14 (trend confirmed, not chop)
    - Confirmed trough exists
    """

    def __init__(self, ema_fast: int = 9, ema_slow: int = 21, rsi_period: int = 14, adx_period: int = 14):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.adx_period = adx_period

    def _calc_rsi(self, close: pd.Series) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _calc_adx(self, df: pd.DataFrame) -> float:
        if len(df) < self.adx_period + 10:
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

            atr = tr.rolling(self.adx_period).mean()
            plus_di = 100 * pd.Series(plus_dm).rolling(self.adx_period).mean() / (atr + 1e-10)
            minus_di = 100 * pd.Series(minus_dm).rolling(self.adx_period).mean() / (atr + 1e-10)

            dx = 100 * (plus_di - minus_di).abs() / ((plus_di + minus_di).abs() + 1e-10)
            adx = dx.rolling(self.adx_period).mean()
            return float(adx.iloc[-1])
        except:
            return 0.0

    def check(self, df: pd.DataFrame) -> Dict:
        """Check EMA, RSI, ADX alignment."""
        if len(df) < max(self.ema_slow, self.rsi_period, self.adx_period) + 10:
            return {"valid": False, "all_confirmed": False}

        close = df['close'].astype(float)

        # EMA cross
        ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean().iloc[-1]
        ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean().iloc[-1]
        ema_bullish = ema_fast > ema_slow

        # RSI > 50 (momentum direction)
        rsi = self._calc_rsi(close)
        rsi_bullish = rsi > 50

        # ADX > 14 (trend exists)
        adx = self._calc_adx(df)
        adx_trending = adx > 14

        # ALL must confirm
        all_confirmed = ema_bullish and rsi_bullish and adx_trending

        return {
            "valid": True,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_bullish": ema_bullish,
            "rsi": rsi,
            "rsi_bullish": rsi_bullish,
            "adx": adx,
            "adx_trending": adx_trending,
            "all_confirmed": all_confirmed,
        }


# =============================================================================
# GOAL ENGINE (Never Retract)
# =============================================================================

class GoalEngine:
    """
    Goal-based profit protection.
    
    RULES:
    - Set goal (e.g., $7)
    - When equity exceeds goal above reference, cut ALL losers
    - Reference advances to new equity (never retracts)
    - Goals only go up, never down
    """
    
    def __init__(self, goal_amount: float = 5.0, buffer: float = 2.0):
        self.goal_amount = goal_amount
        self.buffer = buffer
        self.goal_threshold = goal_amount + buffer  # $7
        
        self.reference_equity = 0.0
        self.highest_equity = 0.0
        self.goals_reached = 0
    
    def initialize(self, equity: float):
        if self.reference_equity == 0:
            self.reference_equity = equity
            self.highest_equity = equity
    
    def check(self, current_equity: float) -> Tuple[bool, str]:
        """
        Check if goal is reached.
        
        Returns (should_cut_losers, reason)
        """
        if self.reference_equity == 0:
            self.initialize(current_equity)
            return False, "initialized"
        
        # Track highest (never retracts)
        if current_equity > self.highest_equity:
            self.highest_equity = current_equity
        
        # Calculate gain from reference
        gain = current_equity - self.reference_equity
        
        # Goal reached?
        if gain >= self.goal_threshold:
            self.goals_reached += 1
            LOG.info(f"[GOAL] 🎯 Goal #{self.goals_reached} reached! Gain: +${gain:.2f}")
            return True, "goal_reached"
        
        return False, "monitoring"
    
    def after_cut(self, new_equity: float):
        """Advance reference after cutting losers (never retract)."""
        # Reference only goes up, never down
        if new_equity > self.reference_equity:
            self.reference_equity = new_equity
            self.highest_equity = new_equity
            LOG.info(f"[GOAL] Reference advanced to ${new_equity:.2f}")


# =============================================================================
# POSITION & TRADE
# =============================================================================

@dataclass
class Position:
    instrument: str
    entry_price: float
    entry_time: datetime
    entry_trough: ConfirmedTrough
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


# =============================================================================
# QUANTUM EXECUTOR v2
# =============================================================================

class QuantumExecutorV2:
    """
    Executes trades on confirmed structure only.
    
    ENTRY: Confirmed trough (5%+ rise, 3+ bars) + EMA bullish
    EXIT: Confirmed peak (5%+ fall, 3+ bars) OR trailing stop
    NO STOP LOSS: Never
    GOAL ENGINE: Cuts losers when profit exceeds goal
    """
    
    def __init__(
        self,
        initial_balance: float = 10000.0,
        trailing_trigger_pct: float = 1.0,  # Lowered: arm at 1% profit
        trailing_atr_mult: float = 1.5,     # Tighter trailing
        min_confirm_pct: float = 5.0,
        min_bars_confirm: int = 3,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.trailing_trigger_pct = trailing_trigger_pct
        self.trailing_atr_mult = trailing_atr_mult
        
        # Structure storage
        self.structure = StructureStore(
            min_confirm_pct=min_confirm_pct,
            min_bars_confirm=min_bars_confirm,
        )
        
        # EMA confirmation (21/55 on 1H for better confirmation)
        self.ema = EMAConfirmation(ema_fast=21, ema_slow=55)
        
        # Goal engine
        self.goal_engine = GoalEngine(goal_amount=5.0, buffer=2.0)
        
        # Positions and trades
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        
        # Track which troughs we've already traded
        self.traded_troughs: set = set()
    
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
    
    def _calculate_equity(self, prices: Dict[str, float]) -> float:
        equity = self.balance
        for instrument, pos in self.positions.items():
            price = prices.get(instrument, pos.entry_price)
            equity += pos.qty * price
        return equity
    
    def process_bar(
        self,
        instrument: str,
        df: pd.DataFrame,
        current_time: datetime,
        all_prices: Dict[str, float],
    ) -> Optional[str]:
        """
        Process a bar for an instrument.
        
        Returns action taken.
        """
        if len(df) < 30:
            return None
        
        current_price = float(df.iloc[-1]['close'])
        
        # Update structure - detect new confirmed troughs/peaks
        new_trough, new_peak = self.structure.update(instrument, df)
        
        # ========== CHECK EXITS FOR EXISTING POSITIONS ==========
        if instrument in self.positions:
            pos = self.positions[instrument]
            
            # Update peak price tracking
            if current_price > pos.peak_price:
                pos.peak_price = current_price
            
            # EXIT 1: Confirmed peak - ONLY if in profit
            # Don't exit at a loss - let goal engine handle losers
            if new_peak:
                profit_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
                if profit_pct > 0:
                    return self._close_position(instrument, current_price, current_time, "confirmed_peak")
            
            # EXIT 2: Trailing stop (lock in profits)
            profit_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            if profit_pct >= self.trailing_trigger_pct:
                atr = self._get_atr(df)
                if atr > 0:
                    trailing_stop = pos.peak_price - (atr * self.trailing_atr_mult)
                    if current_price < trailing_stop and current_price > pos.entry_price:
                        return self._close_position(instrument, current_price, current_time, "trailing_stop")
            
            # NO STOP LOSS - Goal engine handles losers
            return None
        
        # ========== CHECK ENTRY AT CONFIRMED TROUGH ==========
        if new_trough and len(self.positions) < 5:
            # Don't trade same trough twice
            trough_key = f"{instrument}_{new_trough.trough_index}"
            if trough_key in self.traded_troughs:
                return None

            # ===== CONFIRM TREND DIRECTION FROM STRUCTURE =====
            # Get last 2 troughs and last 2 peaks
            all_troughs = self.structure.get_all_troughs(instrument)
            all_peaks = self.structure.get_all_peaks(instrument)

            # Need at least 2 of each to confirm trend
            if len(all_troughs) >= 2 and len(all_peaks) >= 2:
                prev_trough = all_troughs[-2]
                curr_trough = all_troughs[-1]  # or new_trough
                prev_peak = all_peaks[-2]
                curr_peak = all_peaks[-1]

                # Check for HIGHER LOWS
                higher_lows = curr_trough.trough_price > prev_trough.trough_price

                # Check for HIGHER HIGHS
                higher_highs = curr_peak.peak_price > prev_peak.peak_price

                # BULLISH = Higher highs + Higher lows
                bullish_structure = higher_lows and higher_highs

                # BEARISH = Lower highs + Lower lows
                lower_lows = curr_trough.trough_price < prev_trough.trough_price
                lower_highs = curr_peak.peak_price < prev_peak.peak_price
                bearish_structure = lower_lows and lower_highs

                if bearish_structure:
                    LOG.debug(f"[SKIP] {instrument}: BEARISH structure confirmed - no trades")
                    return None

                if not bullish_structure:
                    LOG.debug(f"[SKIP] {instrument}: No bullish structure - HH:{higher_highs} HL:{higher_lows}")
                    return None

                LOG.info(f"[STRUCTURE] {instrument}: BULLISH confirmed - Higher Highs + Higher Lows")

            # Check ALL confirmations: EMA bullish + RSI > 50 + ADX > 14
            confirm = self.ema.check(df)
            if confirm.get("valid") and confirm.get("all_confirmed"):
                self.traded_troughs.add(trough_key)
                LOG.info(f"  EMA: {confirm['ema_bullish']} | RSI: {confirm['rsi']:.1f} | ADX: {confirm['adx']:.1f}")
                return self._open_position(instrument, current_price, current_time, new_trough)
        
        return None
    
    def check_goal_engine(self, prices: Dict[str, float], current_time: datetime):
        """Check goal engine and cut losers if goal reached."""
        equity = self._calculate_equity(prices)
        self.goal_engine.initialize(equity)
        
        should_cut, reason = self.goal_engine.check(equity)
        
        if should_cut:
            # Cut ALL losers
            cut_count = 0
            for instrument in list(self.positions.keys()):
                pos = self.positions[instrument]
                price = prices.get(instrument, pos.entry_price)
                if price < pos.entry_price:
                    self._close_position(instrument, price, current_time, "goal_cut_loser")
                    cut_count += 1
            
            # Advance reference (never retract)
            new_equity = self._calculate_equity(prices)
            self.goal_engine.after_cut(new_equity)
            
            LOG.info(f"[GOAL] Cut {cut_count} losers")
    
    def _open_position(self, instrument: str, price: float, time: datetime, trough: ConfirmedTrough) -> str:
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
        
        LOG.info(f"[ENTRY] {instrument} @ ${price:.2f}")
        LOG.info(f"  Trough: ${trough.trough_price:.2f} | Rise: +{trough.confirmation_rise_pct:.1f}%")
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
            "goals_reached": self.goal_engine.goals_reached,
            "confirmed_troughs": sum(len(t) for t in self.structure.troughs.values()),
            "confirmed_peaks": sum(len(p) for p in self.structure.peaks.values()),
            "trades": self.trades,
        }


# =============================================================================
# BACKTEST
# =============================================================================

def run_quantum_v2_backtest(months: int = 4, balance: float = 10000.0, data_dir: str = "data"):
    """Run the Quantum v2 system backtest."""
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    
    pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "AVAX/USD",
             "ADA/USD", "LTC/USD", "DOT/USD", "POL/USD", "XRP/USD"]
    
    # Load data (1H timeframe for better confirmation)
    data = {}
    for pair in pairs:
        tag = pair.replace("/", "")
        path = Path(data_dir) / f"bars_{tag}_1h.csv"
        if path.exists():
            df = pd.read_csv(path)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_localize(None)
            df = df.sort_values('time').reset_index(drop=True)
            data[pair] = df
            LOG.info(f"Loaded {pair}: {len(df)} bars (1H)")
    
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
    executor = QuantumExecutorV2(initial_balance=balance)
    
    # Run
    for i, ts in enumerate(all_times):
        if i % 500 == 0 and i > 0:
            LOG.info(f"Bar {i}/{len(all_times)} | Pos: {len(executor.positions)} | Trades: {len(executor.trades)} | Goals: {executor.goal_engine.goals_reached}")
        
        # Get current prices
        prices = {}
        for pair, df in data.items():
            row = df[df['time'] == ts]
            if len(row) > 0:
                prices[pair] = float(row.iloc[0]['close'])
        
        # Check goal engine first
        if prices:
            executor.check_goal_engine(prices, ts)
        
        # Process each pair
        for pair, df in data.items():
            df_slice = df[df['time'] <= ts]
            if len(df_slice) >= 30:
                executor.process_bar(pair, df_slice, ts, prices)
    
    # Don't force close remaining positions - they stay open
    # Only trailing stop and goal engine close trades
    LOG.info(f"Backtest complete. {len(executor.positions)} positions still open (not force closed)")
    
    # Results
    results = executor.get_results()
    
    print("\n" + "=" * 70)
    print("  QUANTUM SYSTEM v2 RESULTS")
    print("  Confirmed Structure (5%+ rise/fall, 3+ bars) + EMA + Goal Engine")
    print("  NO STOP LOSS | Goals Never Retract")
    print("=" * 70)
    print(f"\n  PERFORMANCE")
    print("-" * 70)
    print(f"    Initial:        ${results['initial']:,.2f}")
    print(f"    Final:          ${results['final']:,.2f}")
    print(f"    Return:         {results['return_pct']:+.2f}%")
    print(f"\n  STRUCTURE (Factual Points)")
    print("-" * 70)
    print(f"    Confirmed Troughs: {results['confirmed_troughs']}")
    print(f"    Confirmed Peaks:   {results['confirmed_peaks']}")
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
    
    run_quantum_v2_backtest(months=args.months, balance=args.balance)
