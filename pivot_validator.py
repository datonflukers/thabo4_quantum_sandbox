#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pivot_validator.py

Event-Based Pivot Confirmation (ThalesFX Style)
================================================

Philosophy: "Better Late Than Never"
- We CANNOT predict peaks/troughs
- We CAN confirm them AFTER the market proves it
- Late but safer = fewer trades, higher quality

Key Features:
- max_lookahead_bars: Don't confirm ancient pivots
- confirmed_at_index: Know EXACTLY when confirmation occurred
- BOS triggers: Prior swing low/high (not midpoint)
- Quality scoring: 0-100 for filtering trades

CONFIRMATION RULES:
- TROUGH: Higher low must form + close above prior swing high (BOS)
- PEAK: Lower high must form + close below prior swing low (BOS)
- Must occur within max_lookahead_bars
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd
import numpy as np

LOG = logging.getLogger('pivot_validator')


class PivotType(Enum):
    PEAK = "PEAK"
    TROUGH = "TROUGH"


@dataclass
class ConfirmedPivot:
    """
    Event-based confirmed pivot with exact timing.
    """
    instrument: str
    pivot_type: PivotType
    pivot_price: float
    pivot_index: int
    confirmed_at_index: int
    trigger_level: float  # BOS level
    quality: float  # 0-100 score
    bars_to_confirm: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict:
        return {
            "instrument": self.instrument,
            "pivot_type": self.pivot_type.value,
            "pivot_price": self.pivot_price,
            "pivot_index": self.pivot_index,
            "confirmed_at_index": self.confirmed_at_index,
            "trigger_level": self.trigger_level,
            "quality": self.quality,
            "bars_to_confirm": self.bars_to_confirm,
            "timestamp": self.timestamp.isoformat(),
        }


class EventBasedPivotValidator:
    """
    Strict event-based pivot confirmation.
    
    TROUGH CONFIRMATION:
    1. Detect fractal low (5-bar pattern: lower lows on each side)
    2. Wait for price to form a HIGHER LOW after the trough
    3. Wait for close ABOVE prior swing high (Break of Structure)
    4. THEN the trough is confirmed - enter long
    
    PEAK CONFIRMATION:
    1. Detect fractal high (5-bar pattern: lower highs on each side)
    2. Wait for price to form a LOWER HIGH after the peak
    3. Wait for close BELOW prior swing low (Break of Structure)
    4. THEN the peak is confirmed - exit/short
    """
    
    def __init__(self, 
                 max_lookahead_bars: int = 10,
                 fractal_period: int = 2,
                 bos_min_pct: float = 0.3,  # 0.3% minimum BOS
                 swing_lookback: int = 10):
        
        self.max_lookahead_bars = max_lookahead_bars
        self.fractal_period = fractal_period
        self.bos_min_pct = bos_min_pct
        self.swing_lookback = swing_lookback
        
        # Cache confirmed pivots per instrument
        self.confirmed_troughs: Dict[str, ConfirmedPivot] = {}
        self.confirmed_peaks: Dict[str, ConfirmedPivot] = {}
        
        # Pending (unconfirmed) pivots
        self.pending_troughs: Dict[str, Tuple[int, float]] = {}  # instrument -> (index, price)
        self.pending_peaks: Dict[str, Tuple[int, float]] = {}
        
        # Stats
        self.troughs_confirmed = 0
        self.peaks_confirmed = 0
        self.blocked_ancient = 0
        
        LOG.info(f"[PIVOT] Validator initialized: max_lookahead={max_lookahead_bars}, "
                 f"fractal_period={fractal_period}, bos_min_pct={bos_min_pct}%")
    
    def _detect_fractal_trough(self, df: pd.DataFrame) -> Optional[Tuple[int, float]]:
        """
        Detect most recent fractal trough (5-bar swing low).
        
        Pattern: Higher lows on both sides of center bar.
        """
        period = self.fractal_period
        if len(df) < (2 * period + 1):
            return None
        
        # Check the bar at position -period-1 (center of pattern)
        center_idx = len(df) - period - 1
        center_low = df.iloc[center_idx]['low']
        
        # All surrounding bars must have higher lows
        for i in range(-period, period + 1):
            if i == 0:
                continue
            if df.iloc[center_idx + i]['low'] <= center_low:
                return None
        
        return (center_idx, float(center_low))
    
    def _detect_fractal_peak(self, df: pd.DataFrame) -> Optional[Tuple[int, float]]:
        """
        Detect most recent fractal peak (5-bar swing high).
        
        Pattern: Lower highs on both sides of center bar.
        """
        period = self.fractal_period
        if len(df) < (2 * period + 1):
            return None
        
        center_idx = len(df) - period - 1
        center_high = df.iloc[center_idx]['high']
        
        for i in range(-period, period + 1):
            if i == 0:
                continue
            if df.iloc[center_idx + i]['high'] >= center_high:
                return None
        
        return (center_idx, float(center_high))
    
    def check_for_confirmed_trough(self, instrument: str, df: pd.DataFrame) -> Optional[ConfirmedPivot]:
        """
        Check if a trough has been CONFIRMED by market structure.
        
        Confirmation requires:
        1. Fractal low exists
        2. Higher low formed after it
        3. Close above prior swing high (BOS)
        
        Returns ConfirmedPivot if confirmed, None otherwise.
        """
        if len(df) < 20:
            return None
        
        current_idx = len(df) - 1
        
        # Step 1: Check for pending trough or detect new one
        if instrument not in self.pending_troughs:
            fractal = self._detect_fractal_trough(df)
            if fractal:
                self.pending_troughs[instrument] = fractal
                LOG.debug(f"[PIVOT] {instrument}: Pending trough at idx {fractal[0]}, price {fractal[1]:.4f}")
        
        if instrument not in self.pending_troughs:
            return None
        
        trough_idx, trough_price = self.pending_troughs[instrument]
        bars_since = current_idx - trough_idx
        
        # Check if too old
        if bars_since > self.max_lookahead_bars:
            LOG.debug(f"[PIVOT] {instrument}: Trough expired ({bars_since} bars)")
            del self.pending_troughs[instrument]
            self.blocked_ancient += 1
            return None
        
        # Need at least 2 bars after trough
        if bars_since < 2:
            return None
        
        # Step 2: Check for higher low after trough
        lows_after = df.iloc[trough_idx + 1:current_idx + 1]['low']
        min_low_after = lows_after.min()
        
        if min_low_after <= trough_price:
            # Trough violated - not valid
            LOG.debug(f"[PIVOT] {instrument}: Trough violated (new low {min_low_after:.4f} <= {trough_price:.4f})")
            del self.pending_troughs[instrument]
            return None
        
        # Step 3: Find prior swing high (BOS trigger)
        lookback_start = max(0, trough_idx - self.swing_lookback)
        swing_high = df.iloc[lookback_start:trough_idx]['high'].max()
        
        # Step 4: Check for BOS (close above swing high)
        current_close = df.iloc[current_idx]['close']
        bos_pct = ((current_close - swing_high) / swing_high) * 100
        
        if bos_pct < self.bos_min_pct:
            LOG.debug(f"[PIVOT] {instrument}: BOS not triggered (close={current_close:.4f}, "
                     f"swing_high={swing_high:.4f}, bos={bos_pct:.2f}%)")
            return None
        
        # ===== CONFIRMED! =====
        higher_low_depth = min_low_after - trough_price
        quality = self._calculate_quality(higher_low_depth, bos_pct, bars_since, trough_price)
        
        confirmed = ConfirmedPivot(
            instrument=instrument,
            pivot_type=PivotType.TROUGH,
            pivot_price=trough_price,
            pivot_index=trough_idx,
            confirmed_at_index=current_idx,
            trigger_level=swing_high,
            quality=quality,
            bars_to_confirm=bars_since,
        )
        
        # Update cache
        self.confirmed_troughs[instrument] = confirmed
        del self.pending_troughs[instrument]
        self.troughs_confirmed += 1
        
        LOG.info(f"[PIVOT] ✅ {instrument}: TROUGH CONFIRMED!")
        LOG.info(f"  Trough price: {trough_price:.4f}")
        LOG.info(f"  Confirmed at: {current_close:.4f}")
        LOG.info(f"  BOS: {bos_pct:.2f}%")
        LOG.info(f"  Quality: {quality:.0f}/100")
        LOG.info(f"  Bars to confirm: {bars_since}")
        
        return confirmed
    
    def check_for_confirmed_peak(self, instrument: str, df: pd.DataFrame) -> Optional[ConfirmedPivot]:
        """
        Check if a peak has been CONFIRMED by market structure.
        
        Confirmation requires:
        1. Fractal high exists
        2. Lower high formed after it
        3. Close below prior swing low (BOS)
        
        Returns ConfirmedPivot if confirmed, None otherwise.
        """
        if len(df) < 20:
            return None
        
        current_idx = len(df) - 1
        
        # Step 1: Check for pending peak or detect new one
        if instrument not in self.pending_peaks:
            fractal = self._detect_fractal_peak(df)
            if fractal:
                self.pending_peaks[instrument] = fractal
                LOG.debug(f"[PIVOT] {instrument}: Pending peak at idx {fractal[0]}, price {fractal[1]:.4f}")
        
        if instrument not in self.pending_peaks:
            return None
        
        peak_idx, peak_price = self.pending_peaks[instrument]
        bars_since = current_idx - peak_idx
        
        # Check if too old
        if bars_since > self.max_lookahead_bars:
            del self.pending_peaks[instrument]
            self.blocked_ancient += 1
            return None
        
        if bars_since < 2:
            return None
        
        # Step 2: Check for lower high after peak
        highs_after = df.iloc[peak_idx + 1:current_idx + 1]['high']
        max_high_after = highs_after.max()
        
        if max_high_after >= peak_price:
            # Peak violated
            del self.pending_peaks[instrument]
            return None
        
        # Step 3: Find prior swing low (BOS trigger)
        lookback_start = max(0, peak_idx - self.swing_lookback)
        swing_low = df.iloc[lookback_start:peak_idx]['low'].min()
        
        # Step 4: Check for BOS (close below swing low)
        current_close = df.iloc[current_idx]['close']
        bos_pct = ((swing_low - current_close) / swing_low) * 100
        
        if bos_pct < self.bos_min_pct:
            return None
        
        # ===== CONFIRMED! =====
        lower_high_depth = peak_price - max_high_after
        quality = self._calculate_quality(lower_high_depth, bos_pct, bars_since, peak_price)
        
        confirmed = ConfirmedPivot(
            instrument=instrument,
            pivot_type=PivotType.PEAK,
            pivot_price=peak_price,
            pivot_index=peak_idx,
            confirmed_at_index=current_idx,
            trigger_level=swing_low,
            quality=quality,
            bars_to_confirm=bars_since,
        )
        
        self.confirmed_peaks[instrument] = confirmed
        del self.pending_peaks[instrument]
        self.peaks_confirmed += 1
        
        LOG.info(f"[PIVOT] ✅ {instrument}: PEAK CONFIRMED!")
        LOG.info(f"  Peak price: {peak_price:.4f}")
        LOG.info(f"  Confirmed at: {current_close:.4f}")
        LOG.info(f"  Quality: {quality:.0f}/100")
        
        return confirmed
    
    def _calculate_quality(self, depth: float, bos_pct: float, bars: int, ref_price: float) -> float:
        """Calculate pivot quality score (0-100)."""
        score = 50.0
        
        # Depth contribution (how clear is the reversal)
        depth_pct = (depth / ref_price) * 100
        if depth_pct > 0.5:
            score += min(20, depth_pct * 10)
        
        # BOS contribution (how strong is the breakout)
        if bos_pct > 0.3:
            score += min(20, bos_pct * 20)
        
        # Speed contribution (faster confirmation = better)
        if bars <= 3:
            score += 10
        elif bars <= 5:
            score += 5
        
        return min(100, max(0, score))
    
    def has_confirmed_trough(self, instrument: str, min_quality: int = 0) -> bool:
        """Check if instrument has a recent confirmed trough."""
        pivot = self.confirmed_troughs.get(instrument)
        return pivot is not None and pivot.quality >= min_quality
    
    def has_confirmed_peak(self, instrument: str, min_quality: int = 0) -> bool:
        """Check if instrument has a recent confirmed peak."""
        pivot = self.confirmed_peaks.get(instrument)
        return pivot is not None and pivot.quality >= min_quality
    
    def get_confirmed_trough(self, instrument: str) -> Optional[ConfirmedPivot]:
        """Get the most recent confirmed trough for instrument."""
        return self.confirmed_troughs.get(instrument)
    
    def get_confirmed_peak(self, instrument: str) -> Optional[ConfirmedPivot]:
        """Get the most recent confirmed peak for instrument."""
        return self.confirmed_peaks.get(instrument)
    
    def clear_confirmed(self, instrument: str):
        """Clear confirmed pivots after trade is placed."""
        if instrument in self.confirmed_troughs:
            del self.confirmed_troughs[instrument]
        if instrument in self.confirmed_peaks:
            del self.confirmed_peaks[instrument]
    
    def get_stats(self) -> Dict:
        return {
            "troughs_confirmed": self.troughs_confirmed,
            "peaks_confirmed": self.peaks_confirmed,
            "blocked_ancient": self.blocked_ancient,
            "pending_troughs": len(self.pending_troughs),
            "pending_peaks": len(self.pending_peaks),
        }
