# -*- coding: utf-8 -*-
"""
Kraken Goal Engine - Profit-Based Loser Management

CONCEPT:
═══════════════════════════════════════════════════════════════════════════════
Instead of hard stops (which don't work well with Kraken), we use PROFIT GOALS
to manage losers. When we hit a profit goal, we cut ALL losing positions.

This way, WINNERS PAY FOR LOSERS.

RULES:
────────────────────────────────────────────────────────────────────────────────
1. Set a goal: $25 profit from reference peak
2. Track equity vs reference peak
3. When equity reaches goal (+$25):
   → Cut ALL losing positions
   → Reset reference peak to current equity
   → Start new cycle

4. Extension Protection:
   → If we go +$10 above reference and fall back, cut losers early
   → This prevents "round-tripping" gains

5. Catastrophic Protection:
   → If total drawdown exceeds 25%, emergency close all

PHASES:
────────────────────────────────────────────────────────────────────────────────
MONITORING:      Normal state, tracking equity vs reference
EXTENDED_10:     +$10 above reference, momentum intact
GOAL_REACHED:    +$25 reached, cutting losers and resetting
FAILED_EXTENSION: Was +$10, fell back to reference → cut losers
CATASTROPHIC:    -25% drawdown → emergency close all

KRAKEN ADAPTATIONS:
────────────────────────────────────────────────────────────────────────────────
- Uses account_balance_fetcher for equity tracking
- Uses sell_manager for position closing
- Percentage-based catastrophic stop (not pip-based)
- USD goal amounts (not pip-based)

Usage:
    from goal_engine_kraken import KrakenGoalEngine
    
    goal_engine = KrakenGoalEngine(
        goal_amount=25.0,
        extension_threshold=10.0,
    )
    
    # In scheduler loop:
    should_cut = goal_engine.check_and_update(current_equity)
    if should_cut:
        cut_all_losers()
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from logging import getLogger
from enum import Enum
import json
from pathlib import Path
import pandas as pd

LOG = getLogger("goal_engine")


class GoalPhase(Enum):
    """Current phase in the goal cycle."""
    MONITORING = "MONITORING"
    EXTENDED_10 = "EXTENDED_10"
    GOAL_REACHED = "GOAL_REACHED"
    FAILED_EXTENSION = "FAILED_EXTENSION"
    CATASTROPHIC = "CATASTROPHIC"
    CUT_COMPLETE = "CUT_COMPLETE"


@dataclass
class GoalState:
    """State tracking for goal-based loser management."""
    
    # Reference point (starting equity for this cycle)
    reference_peak: float = 0.0
    
    # Highest equity seen since reference was set
    highest_since_reference: float = 0.0
    
    # Current phase
    phase: GoalPhase = GoalPhase.MONITORING
    
    # Thresholds (USD)
    goal_amount: float = 25.0          # +$25 = goal reached
    extension_threshold: float = 10.0  # +$10 = extended
    
    # Catastrophic threshold (percentage)
    catastrophic_pct: float = 0.25     # 25% drawdown
    
    # Starting balance (for catastrophic calc)
    starting_balance: float = 0.0
    
    # Stats
    goals_completed: int = 0
    losers_cut: int = 0
    
    # Flag to prevent multiple cuts in same cycle
    cut_this_cycle: bool = False
    
    # Loser cooldowns (pairs that recently lost)
    loser_cooldowns: Dict[str, datetime] = field(default_factory=dict)


class KrakenGoalEngine:
    """
    Goal-based loser management for Kraken.
    
    When profit goals are hit, ALL losing positions are cut.
    Winners pay for losers.
    """
    
    def __init__(
        self,
        goal_amount: float = 25.0,
        extension_threshold: float = 10.0,
        catastrophic_pct: float = 0.25,
        cooldown_minutes: int = 60,
        state_file: str = "data/goal_state.json",
    ):
        self.goal_amount = goal_amount
        self.extension_threshold = extension_threshold
        self.catastrophic_pct = catastrophic_pct
        self.cooldown_minutes = cooldown_minutes
        self.state_file = Path(state_file)
        
        # Initialize state
        self.state = GoalState(
            goal_amount=goal_amount,
            extension_threshold=extension_threshold,
            catastrophic_pct=catastrophic_pct,
        )
        
        # Try to load previous state
        self._load_state()
        
        LOG.info("=" * 60)
        LOG.info("KRAKEN GOAL ENGINE INITIALIZED")
        LOG.info("=" * 60)
        LOG.info(f"  Goal Amount: +${goal_amount:.2f}")
        LOG.info(f"  Extension Threshold: +${extension_threshold:.2f}")
        LOG.info(f"  Catastrophic Stop: {catastrophic_pct*100:.0f}% drawdown")
        LOG.info(f"  Loser Cooldown: {cooldown_minutes} minutes")
        LOG.info("=" * 60)
    
    def _load_state(self):
        """Load state from file if exists."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                
                self.state.reference_peak = data.get("reference_peak", 0.0)
                self.state.highest_since_reference = data.get("highest_since_reference", 0.0)
                self.state.starting_balance = data.get("starting_balance", 0.0)
                self.state.goals_completed = data.get("goals_completed", 0)
                self.state.losers_cut = data.get("losers_cut", 0)
                self.state.phase = GoalPhase(data.get("phase", "MONITORING"))
                self.state.cut_this_cycle = data.get("cut_this_cycle", False)
                
                LOG.info(f"[GOAL] Loaded state: ref=${self.state.reference_peak:.2f}, "
                        f"goals={self.state.goals_completed}")
            except Exception as e:
                LOG.warning(f"[GOAL] Failed to load state: {e}")
    
    def _save_state(self):
        """Save state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "reference_peak": self.state.reference_peak,
                "highest_since_reference": self.state.highest_since_reference,
                "starting_balance": self.state.starting_balance,
                "goals_completed": self.state.goals_completed,
                "losers_cut": self.state.losers_cut,
                "phase": self.state.phase.value,
                "cut_this_cycle": self.state.cut_this_cycle,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            LOG.error(f"[GOAL] Failed to save state: {e}")
    
    def initialize(self, current_equity: float):
        """
        Initialize the goal engine with current equity.
        
        Call this at bot startup to set the reference point.
        """
        if self.state.reference_peak == 0:
            self.state.reference_peak = current_equity
            self.state.highest_since_reference = current_equity
            self.state.starting_balance = current_equity
            self.state.phase = GoalPhase.MONITORING
            self.state.cut_this_cycle = False
            
            LOG.info(f"[GOAL] Initialized with equity ${current_equity:.2f}")
            self._save_state()
    
    def check_and_update(self, current_equity: float) -> Tuple[bool, str, GoalPhase]:
        """
        Check equity and determine if losers should be cut.
        
        Args:
            current_equity: Current total account equity
            
        Returns:
            (should_cut_losers, reason, phase)
        """
        # Initialize if needed
        if self.state.reference_peak == 0:
            self.initialize(current_equity)
            return False, "initialized", GoalPhase.MONITORING
        
        # Don't cut again in same cycle
        if self.state.cut_this_cycle:
            return False, "already_cut_this_cycle", self.state.phase
        
        reference = self.state.reference_peak
        
        # Update highest since reference
        if current_equity > self.state.highest_since_reference:
            self.state.highest_since_reference = current_equity
        
        # Calculate gain from reference
        gain_from_reference = current_equity - reference
        highest_gain = self.state.highest_since_reference - reference
        
        # ===== CHECK CATASTROPHIC =====
        drawdown_from_start = (self.state.starting_balance - current_equity) / self.state.starting_balance
        if drawdown_from_start >= self.catastrophic_pct:
            self.state.phase = GoalPhase.CATASTROPHIC
            LOG.error(f"[GOAL] CATASTROPHIC: {drawdown_from_start*100:.1f}% drawdown!")
            self._save_state()
            return True, "catastrophic_drawdown", GoalPhase.CATASTROPHIC
        
        # ===== CHECK GOAL REACHED =====
        if gain_from_reference >= self.goal_amount:
            old_ref = reference
            self.state.reference_peak = current_equity
            self.state.highest_since_reference = current_equity
            self.state.phase = GoalPhase.GOAL_REACHED
            self.state.goals_completed += 1
            self.state.cut_this_cycle = False  # Reset for new cycle
            
            LOG.info(f"[GOAL] 🎯 GOAL REACHED!")
            LOG.info(f"  Old reference: ${old_ref:.2f}")
            LOG.info(f"  New reference: ${current_equity:.2f}")
            LOG.info(f"  Gain: +${gain_from_reference:.2f}")
            LOG.info(f"  Goals completed: {self.state.goals_completed}")
            
            self._save_state()
            return True, "goal_reached", GoalPhase.GOAL_REACHED
        
        # ===== CHECK EXTENSION =====
        if gain_from_reference >= self.extension_threshold:
            if self.state.phase != GoalPhase.EXTENDED_10:
                self.state.phase = GoalPhase.EXTENDED_10
                LOG.info(f"[GOAL] Extended +${gain_from_reference:.2f} from reference")
            
            self._save_state()
            return False, "extended_momentum", GoalPhase.EXTENDED_10
        
        # ===== CHECK FAILED EXTENSION =====
        # If we hit +$10 before and now fell back to reference, cut losers
        if highest_gain >= self.extension_threshold and gain_from_reference <= 0:
            self.state.phase = GoalPhase.FAILED_EXTENSION
            self.state.cut_this_cycle = True
            self.state.losers_cut += 1
            
            LOG.warning(f"[GOAL] ⚠️ FAILED EXTENSION!")
            LOG.warning(f"  Was: +${highest_gain:.2f}")
            LOG.warning(f"  Now: +${gain_from_reference:.2f}")
            LOG.warning(f"  Cutting losers to protect gains")
            
            self._save_state()
            return True, "failed_extension", GoalPhase.FAILED_EXTENSION
        
        # ===== MONITORING =====
        self.state.phase = GoalPhase.MONITORING
        self._save_state()
        return False, "monitoring", GoalPhase.MONITORING
    
    def after_loser_cut(self, pairs_cut: List[str], total_loss: float):
        """
        Call this after losers have been cut.
        
        Args:
            pairs_cut: List of pairs that were closed as losers
            total_loss: Total USD loss from cutting
        """
        # Add to cooldown
        now = datetime.now(timezone.utc)
        for pair in pairs_cut:
            self.state.loser_cooldowns[pair] = now
        
        # Reset for next cycle
        self.state.cut_this_cycle = True
        self.state.phase = GoalPhase.CUT_COMPLETE
        
        LOG.info(f"[GOAL] Cut {len(pairs_cut)} losers | Loss: ${total_loss:.2f}")
        LOG.info(f"  Cooldown applied: {pairs_cut}")
        
        self._save_state()
    
    def reset_cycle(self, new_equity: float):
        """
        Reset the goal cycle with new reference.
        
        Call this after goal is reached and losers are cut.
        """
        self.state.reference_peak = new_equity
        self.state.highest_since_reference = new_equity
        self.state.phase = GoalPhase.MONITORING
        self.state.cut_this_cycle = False
        
        LOG.info(f"[GOAL] Cycle reset | New reference: ${new_equity:.2f}")
        self._save_state()
    
    def is_on_cooldown(self, pair: str) -> bool:
        """Check if a pair is on loser cooldown."""
        if pair not in self.state.loser_cooldowns:
            return False
        
        cooldown_end = self.state.loser_cooldowns[pair]
        from datetime import timedelta
        expiry = cooldown_end + timedelta(minutes=self.cooldown_minutes)
        
        if datetime.now(timezone.utc) >= expiry:
            # Cooldown expired
            del self.state.loser_cooldowns[pair]
            return False
        
        return True
    
    def get_status(self) -> Dict:
        """Get current goal engine status."""
        return {
            "phase": self.state.phase.value,
            "reference_peak": self.state.reference_peak,
            "highest_since_reference": self.state.highest_since_reference,
            "goal_amount": self.goal_amount,
            "goals_completed": self.state.goals_completed,
            "losers_cut": self.state.losers_cut,
            "on_cooldown": list(self.state.loser_cooldowns.keys()),
            "cut_this_cycle": self.state.cut_this_cycle,
        }
    
    def print_status(self, current_equity: float):
        """Print formatted status."""
        gain = current_equity - self.state.reference_peak
        to_goal = self.goal_amount - gain
        
        print("\n" + "=" * 50)
        print("  KRAKEN GOAL ENGINE STATUS")
        print("=" * 50)
        print(f"  Phase:           {self.state.phase.value}")
        print(f"  Current Equity:  ${current_equity:,.2f}")
        print(f"  Reference Peak:  ${self.state.reference_peak:,.2f}")
        print(f"  Gain from Ref:   ${gain:+,.2f}")
        print(f"  To Next Goal:    ${to_goal:,.2f}")
        print(f"  Goals Completed: {self.state.goals_completed}")
        print(f"  Losers Cut:      {self.state.losers_cut}")
        print("=" * 50 + "\n")


# Singleton instance
_goal_engine: Optional[KrakenGoalEngine] = None


def get_goal_engine(
    goal_amount: float = 25.0,
    extension_threshold: float = 10.0,
) -> KrakenGoalEngine:
    """Get or create the Goal Engine singleton."""
    global _goal_engine
    if _goal_engine is None:
        _goal_engine = KrakenGoalEngine(
            goal_amount=goal_amount,
            extension_threshold=extension_threshold,
        )
    return _goal_engine


# =============================================================================
# PIVOT VALIDATOR INTEGRATION
# =============================================================================

from pivot_validator import EventBasedPivotValidator, ConfirmedPivot, PivotType

# Singleton pivot validator
_pivot_validator: Optional[EventBasedPivotValidator] = None


def get_pivot_validator(
    max_lookahead_bars: int = 10,
    fractal_period: int = 2,
    bos_min_pct: float = 0.3,
) -> EventBasedPivotValidator:
    """Get or create the Pivot Validator singleton."""
    global _pivot_validator
    if _pivot_validator is None:
        _pivot_validator = EventBasedPivotValidator(
            max_lookahead_bars=max_lookahead_bars,
            fractal_period=fractal_period,
            bos_min_pct=bos_min_pct,
        )
    return _pivot_validator


def check_trough_entry(pair: str, df_1h: pd.DataFrame, min_quality: int = 50) -> Tuple[bool, Optional[ConfirmedPivot]]:
    """
    Check if a CONFIRMED trough exists for entry.

    This is the ThalesFX-style entry:
    - Wait for fractal low
    - Wait for higher low to form
    - Wait for break above prior swing high (BOS)
    - THEN enter

    Args:
        pair: Trading pair (e.g., "BTC/USD")
        df_1h: 1-hour OHLCV DataFrame
        min_quality: Minimum quality score (0-100)

    Returns:
        (should_enter, confirmed_pivot)
    """
    validator = get_pivot_validator()

    # Check for confirmed trough
    confirmed = validator.check_for_confirmed_trough(pair, df_1h)

    if confirmed and confirmed.quality >= min_quality:
        LOG.info(f"[ENTRY] {pair}: Confirmed trough entry signal!")
        LOG.info(f"  Trough: {confirmed.pivot_price:.4f}")
        LOG.info(f"  BOS level: {confirmed.trigger_level:.4f}")
        LOG.info(f"  Quality: {confirmed.quality:.0f}")
        return True, confirmed

    return False, None


def check_peak_exit(pair: str, df_1h: pd.DataFrame, min_quality: int = 50) -> Tuple[bool, Optional[ConfirmedPivot]]:
    """
    Check if a CONFIRMED peak exists for exit.

    Args:
        pair: Trading pair
        df_1h: 1-hour OHLCV DataFrame
        min_quality: Minimum quality score (0-100)

    Returns:
        (should_exit, confirmed_pivot)
    """
    validator = get_pivot_validator()

    # Check for confirmed peak
    confirmed = validator.check_for_confirmed_peak(pair, df_1h)

    if confirmed and confirmed.quality >= min_quality:
        LOG.info(f"[EXIT] {pair}: Confirmed peak exit signal!")
        LOG.info(f"  Peak: {confirmed.pivot_price:.4f}")
        LOG.info(f"  Quality: {confirmed.quality:.0f}")
        return True, confirmed

    return False, None


def clear_pivot_signals(pair: str):
    """Clear pivot signals after trade is placed."""
    validator = get_pivot_validator()
    validator.clear_confirmed(pair)


def get_pivot_stats() -> Dict:
    """Get pivot validator statistics."""
    validator = get_pivot_validator()
    return validator.get_stats()


# =============================================================================
# COMPLETE TRADING SYSTEM INTEGRATION
# =============================================================================
# Entry: Confirmed trough (ThalesFX)
# Exit: Trailing stop (ATR-based) OR Confirmed peak
# Backup: Goal engine cuts losers when profitable

from atr_trailing_stop import (
    evaluate_trailing_stop,
    get_atr_from_data,
    is_trailing_armed,
)


def check_exit_signals(
    pair: str,
    entry_price: float,
    current_price: float,
    peak_price: float,
    df_1h: pd.DataFrame,
    atr_multiplier: float = 2.0,
    trigger_pct: float = 5.0,  # Lower trigger for faster profit taking
) -> Tuple[bool, str, Optional[float]]:
    """
    Check all exit conditions for a position.

    Priority:
    1. Trailing stop (fastest profit taking)
    2. Confirmed peak (structure-based exit)
    3. Goal engine (handled separately in main loop)

    Args:
        pair: Trading pair
        entry_price: Position entry price
        current_price: Current market price
        peak_price: Highest price since entry
        df_1h: 1-hour OHLCV data
        atr_multiplier: ATR multiplier for trailing buffer
        trigger_pct: Profit % to arm trailing stop

    Returns:
        (should_exit, reason, stop_price)
    """
    # Get ATR for trailing stop calculation
    atr = get_atr_from_data(pair, timeframe="1h")

    if atr > 0:
        # Check trailing stop
        should_close, stop_price = evaluate_trailing_stop(
            entry_price=entry_price,
            current_price=current_price,
            peak_price=peak_price,
            atr_value=atr,
            atr_multiplier=atr_multiplier,
            trigger_pct=trigger_pct,
        )

        if should_close:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            LOG.info(f"[EXIT] {pair}: Trailing stop triggered @ ${current_price:.2f} (+{profit_pct:.1f}%)")
            return True, "trailing_stop", stop_price

    # Check for confirmed peak (structure-based exit)
    should_exit_peak, peak_pivot = check_peak_exit(pair, df_1h, min_quality=50)
    if should_exit_peak:
        LOG.info(f"[EXIT] {pair}: Confirmed peak exit @ ${current_price:.2f}")
        return True, "confirmed_peak", None

    return False, "holding", None


def get_complete_system_status() -> Dict:
    """Get status of the complete trading system."""
    goal_engine = get_goal_engine()
    pivot_validator = get_pivot_validator()

    return {
        "goal_engine": goal_engine.get_status(),
        "pivot_validator": pivot_validator.get_stats(),
        "system": "ThalesFX + Trailing Stop + Goal Engine",
        "entry": "Confirmed trough (BOS validation)",
        "exit_primary": "ATR trailing stop",
        "exit_secondary": "Confirmed peak",
        "exit_backup": "Goal engine (cut losers on profit)",
    }


def print_system_status(current_equity: float):
    """Print complete system status."""
    goal_engine = get_goal_engine()
    pivot_stats = get_pivot_stats()

    print("\n" + "=" * 60)
    print("  KRAKEN TRADING SYSTEM STATUS")
    print("  ThalesFX Entries + Trailing Exits + Goal Engine")
    print("=" * 60)

    # Goal Engine
    goal_engine.print_status(current_equity)

    # Pivot Validator
    print("\n  PIVOT VALIDATOR")
    print("-" * 60)
    print(f"  Troughs Confirmed: {pivot_stats['troughs_confirmed']}")
    print(f"  Peaks Confirmed:   {pivot_stats['peaks_confirmed']}")
    print(f"  Pending Troughs:   {pivot_stats['pending_troughs']}")
    print(f"  Pending Peaks:     {pivot_stats['pending_peaks']}")
    print("=" * 60 + "\n")
