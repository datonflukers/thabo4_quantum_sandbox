# -*- coding: utf-8 -*-
"""
QUANTUM SYSTEM v3 - Structure-Following Transition Engine
==========================================================

FOUR-LEG QUANTUM SYSTEM:
─────────────────────────────────────────────────────────────────────────────
LEG 1 (1H): Trough → Momentum → Structure Break → Higher Low
LEG 2 (1H): Triple Quantum EMA (Q1 Daily + Q2 Trend + Q3 Momentum)  
LEG 3 (1H): Per-instrument sensitivity exits
LEG 4 (15M): RSI 48 + Ichimoku Cloud + MA Trend (NEW - faster signals)
─────────────────────────────────────────────────────────────────────────────

LEG 4 RULES:
- Only trades when HTF (Daily) is BULLISH
- RSI >= 48 on 15M
- Price above Ichimoku Cloud on 15M
- Price above MA 50 on 15M
- All three must agree → LONG SIGNAL

We don't catch bottoms.
We catch trend transitions.

That is professional trading.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
import pandas as pd
import numpy as np

LOG = logging.getLogger("quantum_v3")


# =============================================================================
# LEG 4: 15-MINUTE QUANTUM STRATEGY (RSI + Ichimoku + MA) - Using finta
# =============================================================================

class Quantum15MStrategy:
    """
    Fourth Leg: 15-Minute Quantum Strategy.

    Faster signal generation gated by HTF bullish flow.
    Uses finta library for indicator calculations.

    ENTRY REQUIREMENTS (ALL must be true):
    1. HTF (Daily) must be BULLISH (Q1: EMA 50 > EMA 200)
    2. RSI >= 48 on 15M timeframe
    3. Price above Ichimoku Cloud (Senkou Span A & B)
    4. Price above EMA 50 on 15M

    LONG-ONLY: Never trade against HTF direction.
    """

    @staticmethod
    def calculate_ichimoku(df: pd.DataFrame) -> Dict:
        """
        Calculate Ichimoku Cloud components using finta.

        Returns dict with:
        - price_above_cloud: True if price is above the cloud
        - cloud_bullish: True if Span A > Span B
        """
        try:
            from finta import TA
        except ImportError:
            LOG.warning("finta not installed, using manual Ichimoku calculation")
            return Quantum15MStrategy._calculate_ichimoku_manual(df)

        if len(df) < 52:
            return {"price_above_cloud": False, "cloud_bullish": False}

        # finta expects columns: open, high, low, close, volume
        ohlc = df[['open', 'high', 'low', 'close', 'volume']].copy()
        ohlc.columns = ['open', 'high', 'low', 'close', 'volume']

        try:
            ichimoku = TA.ICHIMOKU(ohlc)

            current_close = float(df['close'].iloc[-1])

            # Ichimoku returns: TENKAN, KIJUN, senkou_span_a, SENKOU, CHIKOU
            span_a = float(ichimoku['senkou_span_a'].iloc[-1]) if 'senkou_span_a' in ichimoku.columns else 0
            span_b = float(ichimoku['SENKOU'].iloc[-1]) if 'SENKOU' in ichimoku.columns else 0

            if span_a == 0 or span_b == 0:
                # Fallback to manual calculation
                return Quantum15MStrategy._calculate_ichimoku_manual(df)

            cloud_top = max(span_a, span_b)
            price_above_cloud = current_close > cloud_top
            cloud_bullish = span_a > span_b

            return {
                "senkou_span_a": span_a,
                "senkou_span_b": span_b,
                "cloud_top": cloud_top,
                "price_above_cloud": price_above_cloud,
                "cloud_bullish": cloud_bullish,
                "price": current_close,
            }
        except Exception as e:
            LOG.warning(f"finta Ichimoku failed: {e}, using manual")
            return Quantum15MStrategy._calculate_ichimoku_manual(df)

    @staticmethod
    def _calculate_ichimoku_manual(df: pd.DataFrame) -> Dict:
        """Manual Ichimoku calculation as fallback."""
        if len(df) < 52:
            return {"price_above_cloud": False, "cloud_bullish": False}

        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)

        # Tenkan-sen: (9-period high + 9-period low) / 2
        period9_high = high.rolling(window=9).max()
        period9_low = low.rolling(window=9).min()
        tenkan_sen = (period9_high + period9_low) / 2

        # Kijun-sen: (26-period high + 26-period low) / 2
        period26_high = high.rolling(window=26).max()
        period26_low = low.rolling(window=26).min()
        kijun_sen = (period26_high + period26_low) / 2

        # Senkou Span A: (Tenkan-sen + Kijun-sen) / 2
        senkou_span_a = (tenkan_sen + kijun_sen) / 2

        # Senkou Span B: (52-period high + 52-period low) / 2
        period52_high = high.rolling(window=52).max()
        period52_low = low.rolling(window=52).min()
        senkou_span_b = (period52_high + period52_low) / 2

        current_close = float(close.iloc[-1])
        current_span_a = float(senkou_span_a.iloc[-1])
        current_span_b = float(senkou_span_b.iloc[-1])

        cloud_top = max(current_span_a, current_span_b)
        price_above_cloud = current_close > cloud_top
        cloud_bullish = current_span_a > current_span_b

        return {
            "senkou_span_a": current_span_a,
            "senkou_span_b": current_span_b,
            "cloud_top": cloud_top,
            "price_above_cloud": price_above_cloud,
            "cloud_bullish": cloud_bullish,
            "price": current_close,
        }

    @staticmethod
    def check_rsi(df: pd.DataFrame, threshold: float = 48) -> Tuple[bool, float]:
        """
        Check if RSI is above threshold using finta.

        Returns: (is_above, rsi_value)
        """
        try:
            from finta import TA

            if len(df) < 20:
                return False, 0

            ohlc = df[['open', 'high', 'low', 'close', 'volume']].copy()
            ohlc.columns = ['open', 'high', 'low', 'close', 'volume']

            rsi = TA.RSI(ohlc, period=14)
            current_rsi = float(rsi.iloc[-1])

            return current_rsi >= threshold, current_rsi

        except ImportError:
            # Manual RSI calculation
            if len(df) < 20:
                return False, 0

            close = df['close'].astype(float)
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / (loss + 1e-10)
            rsi = 100 - (100 / (1 + rs))

            current_rsi = float(rsi.iloc[-1])
            return current_rsi >= threshold, current_rsi

    @staticmethod
    def check_ema_trend(df: pd.DataFrame, period: int = 50) -> Tuple[bool, float, float]:
        """
        Check if price is above EMA using finta.

        Returns: (price_above_ema, current_price, ema_value)
        """
        try:
            from finta import TA

            if len(df) < period:
                return False, 0, 0

            ohlc = df[['open', 'high', 'low', 'close', 'volume']].copy()
            ohlc.columns = ['open', 'high', 'low', 'close', 'volume']

            ema = TA.EMA(ohlc, period=period)

            current_price = float(df['close'].iloc[-1])
            current_ema = float(ema.iloc[-1])

            return current_price > current_ema, current_price, current_ema

        except ImportError:
            # Manual EMA calculation
            if len(df) < period:
                return False, 0, 0

            close = df['close'].astype(float)
            ema = close.ewm(span=period, adjust=False).mean()

            current_price = float(close.iloc[-1])
            current_ema = float(ema.iloc[-1])

            return current_price > current_ema, current_price, current_ema

    @staticmethod
    def check_adx(df: pd.DataFrame, threshold: float = 20) -> Tuple[bool, float]:
        """
        Check ADX for trend strength using finta.

        Returns: (is_trending, adx_value)
        """
        try:
            from finta import TA

            if len(df) < 30:
                return False, 0

            ohlc = df[['open', 'high', 'low', 'close', 'volume']].copy()
            ohlc.columns = ['open', 'high', 'low', 'close', 'volume']

            adx = TA.ADX(ohlc, period=14)
            current_adx = float(adx.iloc[-1])

            return current_adx >= threshold, current_adx

        except ImportError:
            return True, 25  # Assume trending if finta not available

    @staticmethod
    def check_entry_signal(df_15m: pd.DataFrame, htf_bullish: bool) -> Tuple[bool, str, Dict]:
        """
        Check if all 15M entry conditions are met.

        REQUIRES:
        1. HTF (Daily) is BULLISH
        2. RSI >= 48
        3. Price above Ichimoku Cloud
        4. Price above EMA 50
        5. ADX >= 20 (trending market)

        Returns: (should_enter, reason, details)
        """
        # Gate 1: HTF must be bullish (non-negotiable)
        if not htf_bullish:
            return False, "htf_bearish_blocked", {}

        if len(df_15m) < 55:
            return False, "insufficient_15m_data", {}

        # Check 1: RSI >= 48
        rsi_ok, rsi_value = Quantum15MStrategy.check_rsi(df_15m, threshold=48)
        if not rsi_ok:
            return False, f"rsi_low_15m_{rsi_value:.1f}", {"rsi": rsi_value}

        # Check 2: Price above Ichimoku Cloud
        ichimoku = Quantum15MStrategy.calculate_ichimoku(df_15m)
        if not ichimoku.get("price_above_cloud", False):
            return False, "below_ichimoku_cloud", ichimoku

        # Check 3: Price above EMA 50
        ema_ok, price, ema_value = Quantum15MStrategy.check_ema_trend(df_15m, period=50)
        if not ema_ok:
            return False, f"below_ema50_15m", {"price": price, "ema50": ema_value}

        # Check 4: ADX >= 20 (trending)
        adx_ok, adx_value = Quantum15MStrategy.check_adx(df_15m, threshold=20)
        if not adx_ok:
            return False, f"adx_low_15m_{adx_value:.1f}", {"adx": adx_value}

        # All conditions met!
        details = {
            "rsi": rsi_value,
            "ichimoku": ichimoku,
            "ema50": ema_value,
            "adx": adx_value,
            "price": price,
        }

        return True, f"leg4_15m_rsi={rsi_value:.0f}_adx={adx_value:.0f}_cloud_ok", details

    @staticmethod
    def check_exit_signal(df_15m: pd.DataFrame, htf_bullish: bool) -> Tuple[bool, str]:
        """
        Check if we should exit based on 15M conditions.

        EXIT when:
        1. HTF flips bearish, OR
        2. Price drops below Ichimoku Cloud, OR
        3. RSI drops below 40

        Returns: (should_exit, reason)
        """
        # Exit if HTF flips bearish
        if not htf_bullish:
            return True, "leg4_exit_htf_bearish"

        if len(df_15m) < 55:
            return False, "hold"

        # Exit if price drops below cloud
        ichimoku = Quantum15MStrategy.calculate_ichimoku(df_15m)
        if not ichimoku.get("price_above_cloud", True):
            return True, "leg4_exit_below_cloud"

        # Exit if RSI drops below 40 (momentum lost)
        rsi_ok, rsi_value = Quantum15MStrategy.check_rsi(df_15m, threshold=40)
        if not rsi_ok:
            return True, f"leg4_exit_rsi_weak_{rsi_value:.0f}"

        return False, "leg4_hold"


# =============================================================================
# TRIPLE QUANTUM EMA - Multi-Layer Trend Alignment
# =============================================================================

class TripleQuantumEMA:
    """
    Triple Quantum EMA System - Trade only when ALL layers align.

    MARKET-WIDE TREND: Uses BTC as proxy for overall crypto market.
    Only trade when the MARKET is bullish, not just individual instruments.

    HIGHER EMAs (reduced noise):
    QUANTUM 1 (Macro - Daily):   EMA 50 > EMA 200   → Market Bull
    QUANTUM 2 (Trend - 1H):      EMA 50 > EMA 100   → Trend Bull (was 21/55)
    QUANTUM 3 (Momentum - 1H):   EMA 21 > EMA 50    → Momentum Bull (was 9/21)

    HELPER MODE: Scans all instruments to find best 2-3 opportunities.

    "Trade only what the market has already proven - at ALL timeframes."
    """

    # Store BTC data for market-wide trend check
    _btc_data = None

    @classmethod
    def set_btc_data(cls, btc_df: pd.DataFrame):
        """Set BTC data for market-wide trend detection."""
        cls._btc_data = btc_df

    @classmethod
    def is_market_bullish(cls, current_btc_df: pd.DataFrame = None) -> Tuple[bool, str]:
        """
        Check if the OVERALL MARKET is bullish using BTC as proxy.
        BTC leads the crypto market - if BTC is bearish, don't trade anything.

        Uses the current BTC slice (up to current timestamp), not full data.

        Returns: (is_bullish, reason)
        """
        btc_df = current_btc_df if current_btc_df is not None else cls._btc_data

        if btc_df is None or len(btc_df) < 200:
            return True, "no_btc_data"

        # Check BTC daily trend (Q1 only - less strict)
        q1_bull, _, _ = cls.check_quantum_1_daily(btc_df)

        if q1_bull:
            return True, "btc_market_bull"
        else:
            return False, "btc_market_bear_daily"

    @staticmethod
    def check_quantum_1_daily(df_1h: pd.DataFrame) -> Tuple[bool, float, float]:
        """
        QUANTUM 1: Daily macro trend (EMA 50 > EMA 200)
        Returns: (is_bullish, ema50, ema200)
        """
        # Resample to daily
        df = df_1h.copy()
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')

        daily = df.resample('D').agg({
            'close': 'last'
        }).dropna()

        if len(daily) < 200:
            return True, 0, 0  # Not enough data, allow

        close = daily['close'].astype(float)
        ema_50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema_200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

        return ema_50 > ema_200, ema_50, ema_200

    @staticmethod
    def check_quantum_2_trend(df: pd.DataFrame) -> Tuple[bool, float, float]:
        """
        QUANTUM 2: 1H trend direction (EMA 21 > EMA 55)
        Returns: (is_bullish, ema21, ema55)
        """
        if len(df) < 60:
            return True, 0, 0

        close = df['close'].astype(float)
        ema_21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        ema_55 = float(close.ewm(span=55, adjust=False).mean().iloc[-1])

        return ema_21 > ema_55, ema_21, ema_55

    @staticmethod
    def check_quantum_3_momentum(df: pd.DataFrame) -> Tuple[bool, float, float]:
        """
        QUANTUM 3: 1H immediate momentum (EMA 9 > EMA 21)
        Returns: (is_bullish, ema9, ema21)
        """
        if len(df) < 25:
            return True, 0, 0

        close = df['close'].astype(float)
        ema_9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
        ema_21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])

        return ema_9 > ema_21, ema_9, ema_21

    @staticmethod
    def check_all_quantums(df: pd.DataFrame) -> Dict:
        """
        Check all 3 quantum layers and calculate trend strength.

        Returns dict with:
        - all_bullish: True if ALL 3 quantums are bullish
        - trend_strength: 0-100 score based on EMA spread
        - is_strong_trend: True if strength >= 60 (aggressive trading mode)
        """
        q1_bull, q1_ema50, q1_ema200 = TripleQuantumEMA.check_quantum_1_daily(df)
        q2_bull, q2_ema21, q2_ema55 = TripleQuantumEMA.check_quantum_2_trend(df)
        q3_bull, q3_ema9, q3_ema21 = TripleQuantumEMA.check_quantum_3_momentum(df)

        bullish_count = sum([q1_bull, q2_bull, q3_bull])
        all_bullish = bullish_count == 3

        # Calculate trend strength (0-100) based on EMA spreads
        strength = 0

        # Q1 contribution (40 points max) - wider daily spread = stronger trend
        if q1_bull and q1_ema200 > 0:
            q1_spread = ((q1_ema50 - q1_ema200) / q1_ema200) * 100
            strength += min(40, max(0, q1_spread * 4))

        # Q2 contribution (35 points max) - wider 1H trend spread
        if q2_bull and q2_ema55 > 0:
            q2_spread = ((q2_ema21 - q2_ema55) / q2_ema55) * 100
            strength += min(35, max(0, q2_spread * 7))

        # Q3 contribution (25 points max) - immediate momentum
        if q3_bull and q3_ema21 > 0:
            q3_spread = ((q3_ema9 - q3_ema21) / q3_ema21) * 100
            strength += min(25, max(0, q3_spread * 12.5))

        return {
            "all_bullish": all_bullish,
            "q1_bullish": q1_bull,
            "q2_bullish": q2_bull,
            "q3_bullish": q3_bull,
            "bullish_count": bullish_count,
            "q1_emas": (q1_ema50, q1_ema200),
            "q2_emas": (q2_ema21, q2_ema55),
            "q3_emas": (q3_ema9, q3_ema21),
            "trend_strength": min(100, strength),
            "is_strong_trend": strength >= 60,
        }

    @staticmethod
    def calculate_quantum_score(df: pd.DataFrame) -> float:
        """
        Calculate a quantum alignment score (0-100) for ranking opportunities.
        Higher score = better entry opportunity.
        """
        result = TripleQuantumEMA.check_all_quantums(df)
        return result.get("trend_strength", 0)

    @staticmethod
    def should_enter(df: pd.DataFrame) -> Tuple[bool, str, bool]:
        """
        Check if all 3 quantums are bullish for entry.
        Uses HTF regime detector (already proven to work).

        Returns: (should_enter, reason, is_strong_trend)
        - is_strong_trend: True if we should trade aggressively
        """
        # Use existing HTF regime detector (already works well)
        htf_ok, htf_reason = HTFRegimeDetector.should_allow_long(df)
        if not htf_ok:
            return False, htf_reason, False

        # Then check instrument-level quantums
        result = TripleQuantumEMA.check_all_quantums(df)

        if result["all_bullish"]:
            strength = result["trend_strength"]
            is_strong = result["is_strong_trend"]
            return True, f"quantum_3of3_str={strength:.0f}", is_strong
        else:
            failed = []
            if not result["q1_bullish"]:
                failed.append("Q1")
            if not result["q2_bullish"]:
                failed.append("Q2")
            if not result["q3_bullish"]:
                failed.append("Q3")
            return False, f"quantum_failed_{'+'.join(failed)}", False

    @staticmethod
    def check_quantum_exit(df: pd.DataFrame, instrument: str) -> Tuple[bool, str]:
        """
        Per-instrument quantum exit check.

        HIGH SENSITIVITY (exit on Q2 trend flip):
            LINK, POL, AVAX, DOT, LTC

        LOW SENSITIVITY (exit on Q1 daily flip only):
            BTC, ETH, ADA, XRP, SOL
        """
        result = TripleQuantumEMA.check_all_quantums(df)

        # Per-instrument exit sensitivity
        HIGH_SENS = {"LINK/USD", "POL/USD", "AVAX/USD", "DOT/USD", "LTC/USD"}
        LOW_SENS = {"BTC/USD", "ETH/USD", "ADA/USD", "XRP/USD", "SOL/USD"}

        if instrument in HIGH_SENS:
            # Exit if Q1 or Q2 flips bearish
            if not result["q2_bullish"]:
                return True, "quantum_exit_Q2_trend_flip"
            if not result["q1_bullish"]:
                return True, "quantum_exit_Q1_daily_flip"

        elif instrument in LOW_SENS:
            # Only exit if Q1 (daily) flips bearish
            if not result["q1_bullish"]:
                return True, "quantum_exit_Q1_daily_flip"

        return False, "quantum_hold"

    @classmethod
    def find_best_opportunities(cls, all_data: Dict[str, pd.DataFrame], max_trades: int = 3) -> List[Tuple[str, float, str]]:
        """
        QUANTUM HELPER: Scan all instruments and find the best opportunities.

        Returns list of (instrument, score, reason) sorted by score descending.
        Only returns instruments with all 3 quantums bullish.
        """
        opportunities = []

        # First check if market is bullish
        market_bull, market_reason = cls.is_market_bullish()
        if not market_bull:
            LOG.info(f"[QUANTUM HELPER] Market bearish: {market_reason} - no opportunities")
            return []

        for instrument, df in all_data.items():
            if len(df) < 100:
                continue

            result = cls.check_all_quantums(df)

            if result["all_bullish"]:
                score = cls.calculate_quantum_score(df)
                opportunities.append((instrument, score, f"score_{score}"))

        # Sort by score descending, take top N
        opportunities.sort(key=lambda x: x[1], reverse=True)

        if opportunities:
            LOG.info(f"[QUANTUM HELPER] Found {len(opportunities)} opportunities, top {max_trades}:")
            for inst, score, reason in opportunities[:max_trades]:
                LOG.info(f"  {inst}: {score}/100")

        return opportunities[:max_trades]


# =============================================================================
# HTF REGIME DETECTOR - Daily Trend Filter (Bull/Bear/Chop)
# =============================================================================

class HTFRegimeDetector:
    """
    Higher Timeframe Regime Detector.

    Determines if the DAILY trend is:
    - BULL: Higher highs + higher lows, price above EMA50, EMA50 > EMA200
    - BEAR: Lower highs + lower lows, price below EMA50, EMA50 < EMA200
    - CHOP: No clear structure, price whipping around MAs

    RULE: Only take BUY signals when Daily is BULL.
    """

    @staticmethod
    def resample_to_daily(df_1h: pd.DataFrame) -> pd.DataFrame:
        """Convert 1H bars to Daily bars."""
        df = df_1h.copy()
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')

        daily = df.resample('D').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        daily = daily.reset_index()
        return daily

    @staticmethod
    def detect_regime(df_daily: pd.DataFrame, lookback: int = 20) -> Dict:
        """
        Detect the daily regime.

        Returns:
        - regime: 'bull', 'bear', or 'chop'
        - ema50: current EMA50 value
        - ema200: current EMA200 value
        - price_vs_ema50: 'above' or 'below'
        - structure: 'hh_hl' (bullish), 'lh_ll' (bearish), or 'mixed'
        """
        if len(df_daily) < 200:
            return {"regime": "chop", "reason": "insufficient_data"}

        close = df_daily['close'].astype(float)
        high = df_daily['high'].astype(float)
        low = df_daily['low'].astype(float)

        current_price = float(close.iloc[-1])

        # Calculate EMAs
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        current_ema50 = float(ema50.iloc[-1])
        current_ema200 = float(ema200.iloc[-1])

        # EMA slope (is EMA50 rising or falling?)
        ema50_slope = float(ema50.iloc[-1] - ema50.iloc[-5])  # Last 5 days

        # Price position
        price_above_ema50 = current_price > current_ema50
        ema50_above_ema200 = current_ema50 > current_ema200

        # Detect swing highs and lows for structure
        # Look at last 20 daily bars
        recent_highs = high.iloc[-lookback:].values
        recent_lows = low.iloc[-lookback:].values

        # Find local peaks (swing highs) - simplified
        swing_highs = []
        swing_lows = []

        for i in range(2, len(recent_highs) - 2):
            if recent_highs[i] > recent_highs[i-1] and recent_highs[i] > recent_highs[i-2] and \
               recent_highs[i] > recent_highs[i+1] and recent_highs[i] > recent_highs[i+2]:
                swing_highs.append(recent_highs[i])

        for i in range(2, len(recent_lows) - 2):
            if recent_lows[i] < recent_lows[i-1] and recent_lows[i] < recent_lows[i-2] and \
               recent_lows[i] < recent_lows[i+1] and recent_lows[i] < recent_lows[i+2]:
                swing_lows.append(recent_lows[i])

        # Determine structure
        structure = "mixed"
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            # Check for higher highs + higher lows (bullish)
            hh = swing_highs[-1] > swing_highs[-2] if len(swing_highs) >= 2 else False
            hl = swing_lows[-1] > swing_lows[-2] if len(swing_lows) >= 2 else False

            # Check for lower highs + lower lows (bearish)
            lh = swing_highs[-1] < swing_highs[-2] if len(swing_highs) >= 2 else False
            ll = swing_lows[-1] < swing_lows[-2] if len(swing_lows) >= 2 else False

            if hh and hl:
                structure = "hh_hl"  # Bullish structure
            elif lh and ll:
                structure = "lh_ll"  # Bearish structure

        # Determine regime
        # BULL: Price > EMA50, EMA50 > EMA200, EMA50 rising, bullish structure
        # BEAR: Price < EMA50, EMA50 < EMA200, EMA50 falling, bearish structure
        # CHOP: Everything else

        bull_score = 0
        bear_score = 0

        if price_above_ema50:
            bull_score += 1
        else:
            bear_score += 1

        if ema50_above_ema200:
            bull_score += 1
        else:
            bear_score += 1

        if ema50_slope > 0:
            bull_score += 1
        else:
            bear_score += 1

        if structure == "hh_hl":
            bull_score += 2  # Structure is most important
        elif structure == "lh_ll":
            bear_score += 2

        # Determine final regime
        if bull_score >= 4:
            regime = "bull"
        elif bear_score >= 4:
            regime = "bear"
        else:
            regime = "chop"

        return {
            "regime": regime,
            "ema50": current_ema50,
            "ema200": current_ema200,
            "price": current_price,
            "price_vs_ema50": "above" if price_above_ema50 else "below",
            "ema50_vs_ema200": "above" if ema50_above_ema200 else "below",
            "ema50_slope": "rising" if ema50_slope > 0 else "falling",
            "structure": structure,
            "bull_score": bull_score,
            "bear_score": bear_score,
        }

    @staticmethod
    def should_allow_long(df_1h: pd.DataFrame) -> Tuple[bool, str]:
        """
        Check if we should allow long entries based on Daily regime.

        Returns: (allow_long, reason)
        """
        # Resample 1H to Daily
        df_daily = HTFRegimeDetector.resample_to_daily(df_1h)

        if len(df_daily) < 200:
            # Not enough data - allow with caution
            return True, "insufficient_daily_data"

        result = HTFRegimeDetector.detect_regime(df_daily)
        regime = result["regime"]

        if regime == "bull":
            return True, f"daily_bull_score={result['bull_score']}"
        elif regime == "bear":
            return False, f"daily_bear_blocked_score={result['bear_score']}"
        else:
            # Chop - allow but with warning
            return True, f"daily_chop_caution"


# =============================================================================
# BX TRENDER - Momentum Flip Detector
# =============================================================================

class BXTrender:
    """
    BX Trender / B-Xtrender style momentum flip indicator.

    Detects bullish/bearish momentum state and flips.
    - GREEN (bullish): Momentum rising, safe to enter longs
    - RED (bearish): Momentum falling, avoid longs

    Uses smoothed momentum calculation similar to TSI + trend filter.
    """

    @staticmethod
    def calculate(df: pd.DataFrame, fast: int = 13, slow: int = 25, signal: int = 13) -> Dict:
        """
        Calculate BX Trender state.

        Returns dict with:
        - state: 'bullish' or 'bearish'
        - momentum: current momentum value
        - signal_line: signal line value
        - just_flipped_bullish: True if just flipped from bearish to bullish
        - just_flipped_bearish: True if just flipped from bullish to bearish
        """
        if len(df) < slow + signal + 10:
            return {"state": "neutral", "momentum": 0, "signal_line": 0, 
                    "just_flipped_bullish": False, "just_flipped_bearish": False}

        close = df['close'].astype(float)

        # Calculate True Strength Index (TSI) - smoothed momentum
        # TSI = 100 * (Double Smoothed PC / Double Smoothed Abs PC)
        price_change = close.diff()

        # Double smooth the price change
        smooth1 = price_change.ewm(span=fast, adjust=False).mean()
        double_smooth_pc = smooth1.ewm(span=slow, adjust=False).mean()

        # Double smooth the absolute price change
        abs_smooth1 = price_change.abs().ewm(span=fast, adjust=False).mean()
        double_smooth_abs = abs_smooth1.ewm(span=slow, adjust=False).mean()

        # TSI value
        tsi = 100 * (double_smooth_pc / (double_smooth_abs + 1e-10))

        # Signal line (EMA of TSI)
        signal_line = tsi.ewm(span=signal, adjust=False).mean()

        current_tsi = float(tsi.iloc[-1])
        current_signal = float(signal_line.iloc[-1])
        prev_tsi = float(tsi.iloc[-2])
        prev_signal = float(signal_line.iloc[-2])

        # State: bullish when TSI > signal line, bearish when TSI < signal line
        current_bullish = current_tsi > current_signal
        prev_bullish = prev_tsi > prev_signal

        # Detect flips
        just_flipped_bullish = current_bullish and not prev_bullish
        just_flipped_bearish = not current_bullish and prev_bullish

        state = "bullish" if current_bullish else "bearish"

        return {
            "state": state,
            "momentum": current_tsi,
            "signal_line": current_signal,
            "just_flipped_bullish": just_flipped_bullish,
            "just_flipped_bearish": just_flipped_bearish,
            "tsi_above_zero": current_tsi > 0,
        }

    @staticmethod
    def is_bullish(df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Check if BX Trender is in bullish state.
        Returns: (is_bullish, reason)
        """
        result = BXTrender.calculate(df)

        if result["state"] == "bullish" and result["tsi_above_zero"]:
            return True, f"bx_bullish_tsi={result['momentum']:.1f}"
        elif result["state"] == "bullish":
            return True, f"bx_bullish_weak_tsi={result['momentum']:.1f}"
        else:
            return False, f"bx_bearish_tsi={result['momentum']:.1f}"


# =============================================================================
# TROUGH CONFIDENCE LEVELS
# =============================================================================

class TroughLevel(Enum):
    NONE = 0
    BOUNCE = 1           # Trough candidate detected
    MOMENTUM_FLIP = 2    # RSI > 50 + EMA bullish
    STRUCTURE_BREAK = 3  # Higher high formed
    HIGHER_LOW = 4       # Pullback confirmed → TRADE TRIGGER


@dataclass
class TroughCandidate:
    """Tracks a trough through its confirmation levels."""
    instrument: str
    trough_price: float
    trough_index: int
    trough_time: datetime
    level: TroughLevel = TroughLevel.BOUNCE
    
    # Level 2: Momentum
    momentum_flipped: bool = False
    rsi_at_flip: float = 0.0
    
    # Level 3: Structure break
    previous_peak: float = 0.0
    structure_broken: bool = False
    break_price: float = 0.0
    
    # Level 4: Higher low
    pullback_low: float = 0.0
    higher_low_confirmed: bool = False


@dataclass 
class ConfirmedTransition:
    """A fully confirmed trend transition - TRADE TRIGGER."""
    instrument: str
    trough_price: float
    entry_price: float
    entry_time: datetime
    previous_peak: float
    higher_low: float
    levels_passed: List[str]


# =============================================================================
# STRUCTURE ANALYZER - Tracks Peaks and Troughs
# =============================================================================

class StructureAnalyzer:
    """
    Tracks market structure and trough confirmation levels.
    
    Only triggers trades at Level 4 (Higher Low Confirmed).
    """
    
    def __init__(self):
        # Confirmed structure points
        self.peaks: Dict[str, List[Tuple[int, float, datetime]]] = {}
        self.troughs: Dict[str, List[Tuple[int, float, datetime]]] = {}
        
        # Active trough candidates (working through levels)
        self.candidates: Dict[str, TroughCandidate] = {}
        
        # Confirmed transitions (ready to trade)
        self.transitions: Dict[str, ConfirmedTransition] = {}
        
        # Stats
        self.level1_count = 0
        self.level2_count = 0
        self.level3_count = 0
        self.level4_count = 0
    
    def _get_peaks(self, instrument: str) -> List[Tuple[int, float, datetime]]:
        if instrument not in self.peaks:
            self.peaks[instrument] = []
        return self.peaks[instrument]
    
    def _get_troughs(self, instrument: str) -> List[Tuple[int, float, datetime]]:
        if instrument not in self.troughs:
            self.troughs[instrument] = []
        return self.troughs[instrument]
    
    def _detect_swing_low(self, df: pd.DataFrame, lookback: int = 8) -> Optional[Tuple[int, float]]:
        """
        Detect a swing low (local minimum).

        8-candle confirmation (8 hours on 1H) - balanced speed and reliability.
        """
        if len(df) < lookback * 2 + 1:
            return None

        center = len(df) - lookback - 1
        center_low = float(df.iloc[center]['low'])

        # Check if center is lower than all surrounding bars
        for i in range(-lookback, lookback + 1):
            if i == 0:
                continue
            if float(df.iloc[center + i]['low']) <= center_low:
                return None

        # Require meaningful decline from recent high (last 48 bars = 2 days)
        # A true trough should come after at least 3% decline
        lookback_period = min(48, len(df) - 1)
        recent_high = float(df['high'].iloc[-lookback_period:].max())
        decline_pct = ((recent_high - center_low) / recent_high) * 100

        if decline_pct < 3.0:
            # Not a meaningful trough - just noise
            return None

        return (center, center_low)

    def _detect_swing_high(self, df: pd.DataFrame, lookback: int = 24) -> Optional[Tuple[int, float]]:
        """
        Detect a swing high (local maximum).

        24-candle confirmation (1 day on 1H) - balanced speed and reliability.
        """
        if len(df) < lookback * 2 + 1:
            return None

        center = len(df) - lookback - 1
        center_high = float(df.iloc[center]['high'])

        for i in range(-lookback, lookback + 1):
            if i == 0:
                continue
            if float(df.iloc[center + i]['high']) >= center_high:
                return None

        return (center, center_high)
    
    def _calc_rsi(self, close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])
    
    def _check_ema_bullish(self, close: pd.Series, fast: int = 21, slow: int = 55) -> bool:
        ema_fast = close.ewm(span=fast, adjust=False).mean().iloc[-1]
        ema_slow = close.ewm(span=slow, adjust=False).mean().iloc[-1]
        return ema_fast > ema_slow
    
    def update(self, instrument: str, df: pd.DataFrame) -> Optional[ConfirmedTransition]:
        """
        Update structure analysis and check for Level 4 confirmation.
        
        Returns ConfirmedTransition if Level 4 reached (trade trigger).
        """
        if len(df) < 60:
            return None
        
        current_idx = len(df) - 1
        current_time = df.iloc[current_idx]['time'] if 'time' in df.columns else datetime.now()
        current_close = float(df.iloc[current_idx]['close'])
        current_low = float(df.iloc[current_idx]['low'])
        current_high = float(df.iloc[current_idx]['high'])
        close = df['close'].astype(float)
        
        # Detect and store swing highs (peaks)
        swing_high = self._detect_swing_high(df)
        if swing_high:
            self._get_peaks(instrument).append((swing_high[0], swing_high[1], current_time))
        
        # Detect and store swing lows (troughs)
        swing_low = self._detect_swing_low(df)
        if swing_low:
            self._get_troughs(instrument).append((swing_low[0], swing_low[1], current_time))
        
        # ========== LEVEL 1: BOUNCE (Trough Candidate) ==========
        if swing_low and instrument not in self.candidates:
            trough_idx, trough_price = swing_low
            
            # Get previous peak for structure break check
            peaks = self._get_peaks(instrument)
            previous_peak = peaks[-1][1] if peaks else current_high
            
            self.candidates[instrument] = TroughCandidate(
                instrument=instrument,
                trough_price=trough_price,
                trough_index=trough_idx,
                trough_time=current_time,
                level=TroughLevel.BOUNCE,
                previous_peak=previous_peak,
            )
            self.level1_count += 1
            LOG.debug(f"[L1 BOUNCE] {instrument}: Trough candidate @ ${trough_price:.2f}")
        
        # Check existing candidate for level progression
        if instrument not in self.candidates:
            return None
        
        candidate = self.candidates[instrument]
        
        # Check if trough violated (new low)
        if current_low < candidate.trough_price:
            LOG.debug(f"[INVALIDATED] {instrument}: New low ${current_low:.2f} < trough ${candidate.trough_price:.2f}")
            del self.candidates[instrument]
            return None
        
        # ========== LEVEL 2: MOMENTUM FLIP ==========
        if candidate.level == TroughLevel.BOUNCE:
            rsi = self._calc_rsi(close)
            ema_bullish = self._check_ema_bullish(close)
            
            if rsi > 50 and ema_bullish:
                candidate.level = TroughLevel.MOMENTUM_FLIP
                candidate.momentum_flipped = True
                candidate.rsi_at_flip = rsi
                self.level2_count += 1
                LOG.info(f"[L2 MOMENTUM] {instrument}: RSI={rsi:.1f} + EMA bullish")
        
        # ========== LEVEL 3: STRUCTURE BREAK (Higher High) ==========
        # STRENGTHENED: Require significant break (3%+ above previous peak)
        if candidate.level == TroughLevel.MOMENTUM_FLIP:
            break_pct = ((current_high - candidate.previous_peak) / candidate.previous_peak) * 100
            if break_pct >= 3.0:  # Must break by at least 3%
                candidate.level = TroughLevel.STRUCTURE_BREAK
                candidate.structure_broken = True
                candidate.break_price = current_high
                self.level3_count += 1
                LOG.info(f"[L3 STRUCTURE] {instrument}: Broke ${candidate.previous_peak:.2f} by {break_pct:.1f}% @ ${current_high:.2f}")
        
        # ========== LEVEL 4: HIGHER LOW (Pullback Confirmed) ==========
        # STRENGTHENED: Higher low must be significantly above trough (at least 30% of the move)
        if candidate.level == TroughLevel.STRUCTURE_BREAK:
            # Look for pullback that holds well above the original trough
            if swing_low:
                pullback_low = swing_low[1]

                # Calculate how much higher the pullback is vs trough
                move_size = candidate.break_price - candidate.trough_price
                pullback_above_trough = pullback_low - candidate.trough_price
                pullback_retention = (pullback_above_trough / move_size) * 100 if move_size > 0 else 0

                # Require pullback to hold at least 30% of the move
                if pullback_low > candidate.trough_price and pullback_retention >= 30:
                    candidate.level = TroughLevel.HIGHER_LOW
                    candidate.pullback_low = pullback_low
                    candidate.higher_low_confirmed = True
                    self.level4_count += 1

                    LOG.info(f"[L4 HIGHER LOW] {instrument}: Pullback ${pullback_low:.2f} retained {pullback_retention:.0f}% of move")
                    LOG.info(f"  ✅ TRADE TRIGGER - Trend transition confirmed!")

                    # Create confirmed transition
                    transition = ConfirmedTransition(
                        instrument=instrument,
                        trough_price=candidate.trough_price,
                        entry_price=current_close,
                        entry_time=current_time,
                        previous_peak=candidate.previous_peak,
                        higher_low=pullback_low,
                        levels_passed=["BOUNCE", "MOMENTUM_FLIP", "STRUCTURE_BREAK", "HIGHER_LOW"],
                    )

                    self.transitions[instrument] = transition
                    del self.candidates[instrument]

                    return transition
        
        return None
    
    def get_stats(self) -> Dict:
        return {
            "level1_bounce": self.level1_count,
            "level2_momentum": self.level2_count,
            "level3_structure": self.level3_count,
            "level4_higher_low": self.level4_count,
        }


# =============================================================================
# REGIME TRACKER - Detects Rising/Falling Peak Direction
# =============================================================================

@dataclass
class Regime:
    """Tracks the current market regime for an instrument."""
    instrument: str
    direction: str = "neutral"  # "bullish", "bearish", "neutral"
    consecutive_stops: int = 0
    regime_start_time: datetime = None
    regime_trades: int = 0
    regime_wins: int = 0
    regime_losses: int = 0
    last_peak: float = 0.0
    peak_direction: str = "unknown"  # "rising", "falling", "flat"


class RegimeTracker:
    """
    Tracks market regime based on peak direction.

    REGIME RULES:
    - Rising peaks (higher highs) → Bullish regime → Trade long
    - Falling peaks (lower highs) → Bearish regime → Sit out
    - 3 consecutive goal stops → Exit regime
    - Structure break (EMA flip) → Exit regime
    """

    MAX_CONSECUTIVE_STOPS = 3  # Exit regime after 3 stops

    def __init__(self):
        self.regimes: Dict[str, Regime] = {}
        self.regime_changes = 0

    def get_regime(self, instrument: str) -> Regime:
        if instrument not in self.regimes:
            self.regimes[instrument] = Regime(instrument=instrument)
        return self.regimes[instrument]

    def update_peak_direction(self, instrument: str, peaks: List[Tuple[int, float, datetime]]) -> bool:
        """
        Analyze recent peaks to determine direction.
        Rising peaks = bullish regime, Falling peaks = bearish regime.

        Returns True if regime just changed to bearish (signal to exit positions).
        """
        regime = self.get_regime(instrument)

        if len(peaks) < 3:
            return False

        # Get last 3 peaks
        recent_peaks = [p[1] for p in peaks[-3:]]

        # Check if rising or falling
        if recent_peaks[2] > recent_peaks[1] > recent_peaks[0]:
            new_direction = "rising"
        elif recent_peaks[2] < recent_peaks[1] < recent_peaks[0]:
            new_direction = "falling"
        else:
            new_direction = "mixed"

        # Detect regime change
        regime_turned_bearish = False
        if regime.peak_direction != new_direction:
            old = regime.peak_direction
            regime.peak_direction = new_direction

            if new_direction == "rising":
                regime.direction = "bullish"
                regime.consecutive_stops = 0  # Reset on new regime
                regime.regime_start_time = datetime.now()
                regime.regime_trades = 0
                regime.regime_wins = 0
                regime.regime_losses = 0
                self.regime_changes += 1
                LOG.info(f"[REGIME] {instrument}: {old} → BULLISH (rising peaks)")
            elif new_direction == "falling":
                if regime.direction == "bullish":
                    regime_turned_bearish = True  # Signal to exit
                regime.direction = "bearish"
                self.regime_changes += 1
                LOG.info(f"[REGIME] {instrument}: {old} → BEARISH (falling peaks)")

        regime.last_peak = recent_peaks[-1]
        return regime_turned_bearish

    def record_trade_result(self, instrument: str, is_win: bool, reason: str):
        """
        Record trade result and check for regime exit.
        3 consecutive stops = exit regime.
        """
        regime = self.get_regime(instrument)
        regime.regime_trades += 1

        if is_win:
            regime.regime_wins += 1
            regime.consecutive_stops = 0  # Reset on win
        else:
            regime.regime_losses += 1
            # Check if it was a goal stop (not just a trailing exit)
            if "goal" in reason.lower() or "cut" in reason.lower():
                regime.consecutive_stops += 1
                LOG.info(f"[REGIME] {instrument}: Consecutive stops = {regime.consecutive_stops}/{self.MAX_CONSECUTIVE_STOPS}")

                if regime.consecutive_stops >= self.MAX_CONSECUTIVE_STOPS:
                    LOG.warning(f"[REGIME EXIT] {instrument}: {self.MAX_CONSECUTIVE_STOPS} consecutive stops → exiting bullish regime")
                    regime.direction = "neutral"
                    regime.consecutive_stops = 0

    def should_trade(self, instrument: str) -> Tuple[bool, str]:
        """
        Check if we should take trades in this instrument.
        Returns: (should_trade, reason)
        """
        regime = self.get_regime(instrument)

        if regime.direction == "bullish":
            return True, f"bullish_regime (wins={regime.regime_wins}, stops={regime.consecutive_stops})"
        elif regime.direction == "bearish":
            return False, "bearish_regime (falling peaks)"
        else:
            # Neutral - allow entry to establish new regime
            return True, "neutral_regime (waiting for confirmation)"

    def get_stats(self) -> Dict:
        bullish_count = sum(1 for r in self.regimes.values() if r.direction == "bullish")
        bearish_count = sum(1 for r in self.regimes.values() if r.direction == "bearish")
        return {
            "bullish_regimes": bullish_count,
            "bearish_regimes": bearish_count,
            "regime_changes": self.regime_changes,
        }


# =============================================================================
# GOAL ENGINE
# =============================================================================

class GoalEngine:
    """
    Goal-based profit protection with per-instrument tiers.

    TIERED GOALS:
    - Tier 1 (Strong): $10 activation + $3 trail - BTC, ETH, POL, ADA, SOL
    - Tier 2 (Moderate): $10 activation + $3 trail - LINK, XRP  
    - Tier 3 (Risky): $3 baby step cut - AVAX, DOT, LTC

    Goals never retract.
    """

    # Per-instrument tier configuration
    # Format: (activation_threshold, trail_amount)
    # None = use baby step (cut at $3)
    TIER_CONFIG = {
        # Tier 1 & 2: Strong/Moderate performers - $10 activation, $3 trail
        "BTC/USD": (10.0, 3.0),
        "ETH/USD": (10.0, 3.0),
        "POL/USD": (10.0, 3.0),
        "ADA/USD": (10.0, 3.0),
        "SOL/USD": (10.0, 3.0),
        "LINK/USD": (10.0, 3.0),
        "XRP/USD": (10.0, 3.0),

        # Tier 3: Risky - $3 baby step (cut immediately)
        "AVAX/USD": None,  # Baby step
        "DOT/USD": None,   # Baby step
        "LTC/USD": None,   # Baby step
    }

    BABY_STEP_THRESHOLD = 3.0  # Cut risky instruments when up $3

    def __init__(self, default_goal: float = 5.0, buffer: float = 0.0):
        self.default_goal = default_goal
        self.buffer = buffer
        self.reference_equity = 0.0
        self.highest_equity = 0.0
        self.peak_equity = 0.0  # For trailing
        self.goals_reached = 0
        self.trailing_active = False

    def get_tier_config(self, instrument: str) -> Optional[Tuple[float, float]]:
        """Get the tier config for an instrument. None = baby step."""
        return self.TIER_CONFIG.get(instrument, (10.0, 3.0))

    def is_baby_step(self, instrument: str) -> bool:
        """Check if instrument uses baby step (risky tier)."""
        return self.TIER_CONFIG.get(instrument) is None

    def initialize(self, equity: float):
        if self.reference_equity == 0:
            self.reference_equity = equity
            self.highest_equity = equity
            self.peak_equity = equity

    def check(self, current_equity: float) -> Tuple[bool, str, str]:
        """
        Check goal engine status.
        Returns: (should_cut, reason, cut_type)
        cut_type: 'baby_step' or 'trailing'
        """
        if self.reference_equity == 0:
            self.initialize(current_equity)
            return False, "initialized", ""

        gain = current_equity - self.reference_equity

        # Track peak for trailing
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # Check baby step threshold ($3) for risky instruments
        if gain >= self.BABY_STEP_THRESHOLD:
            self.goals_reached += 1
            LOG.info(f"[GOAL] 🎯 Goal #{self.goals_reached}! Gain: +${gain:.2f}")
            return True, "goal_reached", "baby_step"

        # Check trailing for strong performers
        # Activate trailing when we've made $10+
        if self.peak_equity - self.reference_equity >= 10.0:
            self.trailing_active = True

            # If we've dropped $3 from peak, cut strong losers
            drawdown = self.peak_equity - current_equity
            if drawdown >= 3.0 and gain > 0:
                LOG.info(f"[TRAILING GOAL] Peak: ${self.peak_equity:.2f} → Now: ${current_equity:.2f} (${drawdown:.2f} drawdown)")
                return True, "trailing_goal", "trailing"

        return False, "monitoring", ""

    def after_cut(self, new_equity: float):
        if new_equity > self.reference_equity:
            self.reference_equity = new_equity
            self.highest_equity = new_equity
            self.peak_equity = new_equity
            self.trailing_active = False


# =============================================================================
# POSITION & TRADE
# =============================================================================

@dataclass
class Position:
    instrument: str
    entry_price: float
    entry_time: datetime
    transition: ConfirmedTransition
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


# =============================================================================
# QUANTUM EXECUTOR v3
# =============================================================================

class QuantumExecutorV3:
    """
    Executes trades only on Level 4 confirmed transitions.

    ENTRY: Level 4 Higher Low (trend transition proven)
    EXIT: Trailing stop (profit only) OR Goal engine (cuts losers)
    NO STOP LOSS

    HYBRID EMA CROSS: Risky instruments require additional EMA verification
    - DOT, LTC, AVAX: Must have EMA 9 > EMA 21 > EMA 55 (triple bullish)
    - Strong instruments: Standard Level 4 entry

    BUDGET FORMULA (Scalable):
    - base_unit = balance / 9 (max 9 positions)
    - trade_size = base_unit / divisor
    - divisor controls exposure:
        1.0 = 100% | 1.3 = 77% | 1.6 = 62.5% | 2.0 = 50%
    """

    # Risky instruments that need extra EMA verification
    RISKY_INSTRUMENTS = {"AVAX/USD", "DOT/USD", "LTC/USD"}

    # Strong instruments eligible for 2x leverage
    STRONG_INSTRUMENTS = {"BTC/USD", "ETH/USD", "POL/USD", "ADA/USD", "SOL/USD", "LINK/USD", "XRP/USD"}

    # Maximum number of 2x positions allowed at any time
    MAX_2X_POSITIONS = 2

    # Per-instrument base leverage (before cap check)
    INSTRUMENT_LEVERAGE = {
        # Strong performers - eligible for 2x leverage
        "BTC/USD": 2.0,
        "ETH/USD": 2.0,
        "POL/USD": 2.0,
        "ADA/USD": 2.0,
        "SOL/USD": 2.0,
        "LINK/USD": 2.0,
        "XRP/USD": 2.0,

        # Risky instruments - always 1x leverage
        "AVAX/USD": 1.0,
        "DOT/USD": 1.0,
        "LTC/USD": 1.0,
    }

    def __init__(
        self,
        initial_balance: float = 10000.0,
        trailing_trigger_pct: float = 2.0,  # Activate trailing at 2% profit
        trailing_pct: float = 1.75,  # Trail 1.75% below peak
        max_positions: int = 9,
        exposure_divisor: float = 1.3,  # 77% exposure
        leverage: float = 2.0,  # 2x leverage
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.trailing_trigger_pct = trailing_trigger_pct
        self.trailing_pct = trailing_pct
        self.max_positions = max_positions
        self.exposure_divisor = exposure_divisor
        self.leverage = leverage

        self.structure = StructureAnalyzer()
        self.goal_engine = GoalEngine(default_goal=5.0, buffer=0.0)  # Tiered goals
        self.regime_tracker = RegimeTracker()  # NEW: Track market regimes

        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.traded_transitions: set = set()

        # Track rejected entries for risky instruments
        self.risky_rejections = 0

        # Track which positions are using 2x leverage
        self.positions_at_2x: set = set()

        # ========== INSTITUTIONAL RISK METRICS ==========
        # Max Floating Drawdown tracking
        self.peak_equity = initial_balance
        self.max_floating_drawdown = 0.0
        self.max_floating_drawdown_pct = 0.0
        self.current_drawdown_pct = 0.0

        # Worst cluster loss (max simultaneous open loss - SUM of all negative unrealized P&L)
        self.worst_cluster_loss = 0.0

        # Fee tracking (Kraken taker fees)
        self.TAKER_FEE_RATE = 0.0026  # 0.26% per side
        self.total_fees_paid = 0.0

        # Drawdown thresholds for 2x control
        self.DRAWDOWN_FREEZE_2X = 12.0  # Freeze new 2x entries above this DD%
        self.DRAWDOWN_DEMOTE_2X = 15.0  # Demote existing 2x to 1x above this DD%

        # Exposure throttling thresholds (with hysteresis to prevent chattering)
        self.DRAWDOWN_THROTTLE_30 = 20.0  # Reduce size by 30% above this DD%
        self.DRAWDOWN_THROTTLE_30_OFF = 17.0  # Resume full size below this DD%
        self.DRAWDOWN_THROTTLE_60 = 25.0  # Reduce size by 60% above this DD%
        self.DRAWDOWN_THROTTLE_60_OFF = 22.0  # Resume 70% below this DD%
        self.DRAWDOWN_BLOCK_ENTRIES = 30.0  # Block new entries above this DD%
        self.DRAWDOWN_BLOCK_OFF = 27.0  # Resume entries below this DD%

        # 2x frozen state
        self.leverage_2x_frozen = False
        self.demotions_count = 0
        self.entries_blocked_count = 0

        # Current throttle state (for hysteresis)
        self.current_throttle_level = 0  # 0=full, 1=70%, 2=40%, 3=blocked

        # Time in drawdown tracking
        self.drawdown_start_bar = None
        self.current_bar_index = 0
        self.longest_drawdown_bars = 0
        self.total_bars_in_drawdown = 0
        self.drawdown_events = 0

    def _count_2x_positions(self) -> int:
        """Count how many positions are currently at 2x leverage."""
        return len(self.positions_at_2x)

    def _update_risk_metrics(self, prices: Dict[str, float]):
        """
        Update institutional risk metrics:
        - Max Floating Drawdown (MFD)
        - Current drawdown percentage
        - Worst cluster loss (SUM of all negative unrealized P&L)
        - Time in drawdown (bars underwater)
        - 2x freeze/demotion state
        - Exposure throttling state
        """
        equity = self._calculate_equity(prices)
        self.current_bar_index += 1

        # Update peak equity
        if equity > self.peak_equity:
            self.peak_equity = equity

            # End of drawdown event
            if self.drawdown_start_bar is not None:
                drawdown_duration = self.current_bar_index - self.drawdown_start_bar
                if drawdown_duration > self.longest_drawdown_bars:
                    self.longest_drawdown_bars = drawdown_duration
                self.drawdown_start_bar = None

        # Calculate current drawdown
        drawdown = self.peak_equity - equity
        drawdown_pct = (drawdown / self.peak_equity) * 100 if self.peak_equity > 0 else 0
        self.current_drawdown_pct = drawdown_pct

        # Track time in drawdown
        if drawdown_pct > 1.0:  # Consider >1% as "in drawdown"
            if self.drawdown_start_bar is None:
                self.drawdown_start_bar = self.current_bar_index
                self.drawdown_events += 1
            self.total_bars_in_drawdown += 1

        # Update max floating drawdown
        if drawdown > self.max_floating_drawdown:
            self.max_floating_drawdown = drawdown
            self.max_floating_drawdown_pct = drawdown_pct

        # FIXED: Cluster loss = SUM of all negative unrealized P&L (absolute value)
        # This is the total pain across all losing positions at this moment
        cluster_loss = 0.0
        for instrument, pos in self.positions.items():
            price = prices.get(instrument, pos.entry_price)
            unrealized_pnl = (price - pos.entry_price) * pos.qty
            if unrealized_pnl < 0:
                cluster_loss += abs(unrealized_pnl)  # Sum of all losses

        if cluster_loss > self.worst_cluster_loss:
            self.worst_cluster_loss = cluster_loss

        # Check if we need to freeze 2x entries
        if drawdown_pct >= self.DRAWDOWN_FREEZE_2X:
            if not self.leverage_2x_frozen:
                self.leverage_2x_frozen = True
                LOG.warning(f"[RISK] 2x FROZEN: DD={drawdown_pct:.1f}% >= {self.DRAWDOWN_FREEZE_2X}%")
        else:
            if self.leverage_2x_frozen:
                self.leverage_2x_frozen = False
                LOG.info(f"[RISK] 2x UNFROZEN: DD={drawdown_pct:.1f}% < {self.DRAWDOWN_FREEZE_2X}%")

        # Check if we need to demote 2x positions
        if drawdown_pct >= self.DRAWDOWN_DEMOTE_2X:
            self._demote_2x_positions(prices)

    def _get_exposure_throttle(self) -> float:
        """
        Get exposure throttle multiplier based on current drawdown.
        Uses HYSTERESIS to prevent rapid on/off flipping.

        Returns multiplier to apply to position size:
        - Level 0: DD < 17% → 1.0 (full size)
        - Level 1: DD 20-22% → 0.7 (30% reduction)
        - Level 2: DD 25-27% → 0.4 (60% reduction)
        - Level 3: DD > 30% → 0.0 (block entries)

        Hysteresis:
        - Enter throttle at ON threshold
        - Exit throttle at OFF threshold (lower)
        - Prevents chattering at boundary
        """
        dd = self.current_drawdown_pct

        # Level 3: Block entries
        if self.current_throttle_level == 3:
            if dd < self.DRAWDOWN_BLOCK_OFF:
                self.current_throttle_level = 2
                LOG.info(f"[THROTTLE] Unblocked: DD={dd:.1f}% < {self.DRAWDOWN_BLOCK_OFF}% → 40% size")
        elif dd >= self.DRAWDOWN_BLOCK_ENTRIES:
            self.current_throttle_level = 3
            LOG.warning(f"[THROTTLE] BLOCKED: DD={dd:.1f}% >= {self.DRAWDOWN_BLOCK_ENTRIES}%")

        # Level 2: 60% reduction
        if self.current_throttle_level == 2:
            if dd < self.DRAWDOWN_THROTTLE_60_OFF:
                self.current_throttle_level = 1
                LOG.info(f"[THROTTLE] Eased: DD={dd:.1f}% < {self.DRAWDOWN_THROTTLE_60_OFF}% → 70% size")
        elif dd >= self.DRAWDOWN_THROTTLE_60 and self.current_throttle_level < 2:
            self.current_throttle_level = 2
            LOG.info(f"[THROTTLE] Heavy: DD={dd:.1f}% >= {self.DRAWDOWN_THROTTLE_60}% → 40% size")

        # Level 1: 30% reduction
        if self.current_throttle_level == 1:
            if dd < self.DRAWDOWN_THROTTLE_30_OFF:
                self.current_throttle_level = 0
                LOG.info(f"[THROTTLE] Full: DD={dd:.1f}% < {self.DRAWDOWN_THROTTLE_30_OFF}% → 100% size")
        elif dd >= self.DRAWDOWN_THROTTLE_30 and self.current_throttle_level < 1:
            self.current_throttle_level = 1
            LOG.info(f"[THROTTLE] Light: DD={dd:.1f}% >= {self.DRAWDOWN_THROTTLE_30}% → 70% size")

        # Return multiplier based on level
        throttle_map = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.0}
        return throttle_map.get(self.current_throttle_level, 1.0)

    def _demote_2x_positions(self, prices: Dict[str, float]):
        """
        Demote underwater 2x positions back to 1x by reducing position size.
        This is a risk control measure during high drawdown.

        In live trading, this would sell half the position.
        In backtest, we simulate by marking as demoted.
        """
        for instrument in list(self.positions_at_2x):
            pos = self.positions.get(instrument)
            if pos:
                price = prices.get(instrument, pos.entry_price)
                if price < pos.entry_price:  # Only demote underwater positions
                    LOG.warning(f"[DEMOTE] {instrument}: Demoting 2x → 1x due to DD > {self.DRAWDOWN_DEMOTE_2X}%")
                    self.positions_at_2x.discard(instrument)
                    self.demotions_count += 1
                    # In live: would sell half the position
                    # In backtest: just track it

    def _get_effective_leverage(self, instrument: str) -> float:
        """
        Get the effective leverage for an instrument, respecting the 2x cap and DD freeze.

        Rules:
        1. Risky instruments (DOT, LTC, AVAX) → always 1x
        2. If DD > 12% → 2x FROZEN for all new entries
        3. Strong instruments → 2x IF we have room (< MAX_2X_POSITIONS at 2x)
        4. If at 2x cap → strong instruments get 1x until a 2x closes
        """
        base_leverage = self.INSTRUMENT_LEVERAGE.get(instrument, 1.0)

        # Risky instruments always 1x
        if instrument in self.RISKY_INSTRUMENTS:
            return 1.0

        # 2x frozen due to high drawdown
        if self.leverage_2x_frozen:
            LOG.info(f"[2x FROZEN] {instrument}: DD={self.current_drawdown_pct:.1f}% → using 1x")
            return 1.0

        # Strong instruments: check if we have 2x room
        if base_leverage == 2.0:
            if self._count_2x_positions() < self.MAX_2X_POSITIONS:
                return 2.0
            else:
                LOG.info(f"[LEVERAGE CAP] {instrument}: 2x requested but {self.MAX_2X_POSITIONS} already at 2x → using 1x")
                return 1.0

        return base_leverage

    def _budget(self, instrument: str = None) -> Tuple[float, float]:
        """
        Scalable budget formula with per-instrument leverage, 2x cap, and exposure throttling.

        Returns: (budget, effective_leverage)

        Leverage rules:
        - Max 2 positions at 2x at any time
        - Strong instruments get 2x IF room available
        - Risky instruments always 1x (capital protection)

        Exposure throttling (based on current DD):
        - DD < 20%: Full size
        - DD 20-25%: 70% size
        - DD 25-30%: 40% size
        - DD > 30%: Block entries

        Example with $2,000 balance, 9 positions, 1.3 divisor:
        - base_unit = 2000 / 9 = $222.22
        - Strong (2x): 222.22 / 1.3 * 2 = $341.88 (if room)
        - Strong (capped): 222.22 / 1.3 * 1 = $170.94 (if at 2x limit)
        - Risky:  222.22 / 1.3 * 1 = $170.94 (always)
        """
        base_unit = self.balance / self.max_positions

        # Get effective leverage (respects 2x cap)
        if instrument:
            effective_leverage = self._get_effective_leverage(instrument)
        else:
            effective_leverage = 1.0

        budget = (base_unit / self.exposure_divisor) * effective_leverage

        # Apply exposure throttle based on drawdown
        throttle = self._get_exposure_throttle()
        if throttle < 1.0 and throttle > 0:
            LOG.info(f"[THROTTLE] DD={self.current_drawdown_pct:.1f}% → size reduced to {throttle*100:.0f}%")
        budget *= throttle

        return budget, effective_leverage

    def _check_triple_ema_bullish(self, df: pd.DataFrame) -> bool:
        """
        Check for triple EMA alignment: EMA 9 > EMA 21 > EMA 55
        This is a stronger bullish confirmation for risky instruments.

        Returns True if all three EMAs are properly aligned (bullish stack).
        """
        if len(df) < 60:
            return False

        close = df['close'].astype(float)

        ema_9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        ema_21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema_55 = close.ewm(span=55, adjust=False).mean().iloc[-1]

        # Triple stack: 9 > 21 > 55
        is_stacked = ema_9 > ema_21 > ema_55

        if not is_stacked:
            LOG.debug(f"Triple EMA check failed: EMA9={ema_9:.2f}, EMA21={ema_21:.2f}, EMA55={ema_55:.2f}")

        return is_stacked

    def _check_adx_strength(self, df: pd.DataFrame, min_adx: float = 25.0) -> bool:
        """
        Check ADX for trend strength. ADX > 25 indicates a strong trend.
        This filters out weak/ranging markets where risky instruments fail.
        """
        if len(df) < 30:
            return False

        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)

        # Calculate +DI and -DI
        plus_dm = high.diff()
        minus_dm = -low.diff()

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_14 = tr.rolling(14).mean()
        plus_di = 100 * (plus_dm.rolling(14).mean() / atr_14)
        minus_di = 100 * (minus_dm.rolling(14).mean() / atr_14)

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx = dx.rolling(14).mean().iloc[-1]

        is_strong = adx >= min_adx

        if not is_strong:
            LOG.debug(f"ADX check failed: ADX={adx:.1f} < {min_adx}")

        return is_strong

    def _check_entry_conditions(self, instrument: str, df: pd.DataFrame) -> Tuple[bool, str, bool]:
        """
        Check entry conditions using TRIPLE QUANTUM EMA.

        QUANTUM ENTRY REQUIREMENTS:
        1. QUANTUM 1 (Daily):   EMA 50 > EMA 200  → Macro BULL
        2. QUANTUM 2 (1H):      EMA 21 > EMA 55   → Trend BULL
        3. QUANTUM 3 (1H):      EMA 9 > EMA 21    → Momentum BULL
        4. RSI >= 45 (confirmation) - RELAXED to 40 in strong trends
        5. ADX >= 15 (trend present) - RELAXED to 12 in strong trends
        6. +DI > -DI (buyers stronger)

        Returns: (passed, reason, is_strong_trend)
        - is_strong_trend: True = trade aggressively (larger size, faster re-entry)
        """
        if len(df) < 60:
            return False, "insufficient_data", False

        # TRIPLE QUANTUM CHECK - All 3 must be bullish
        quantum_ok, quantum_reason, is_strong = TripleQuantumEMA.should_enter(df)
        if not quantum_ok:
            return False, quantum_reason, False

        close = df['close'].astype(float)
        high = df['high'].astype(float)
        low = df['low'].astype(float)

        # RELAXED thresholds in strong trends
        rsi_threshold = 40 if is_strong else 45
        adx_threshold = 12 if is_strong else 15

        # RSI check
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        current_rsi = float(rsi.iloc[-1])

        if current_rsi < rsi_threshold:
            return False, f"rsi_low_{current_rsi:.1f}", False

        # Calculate directional indicators
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_14 = tr.rolling(14).mean()
        plus_di = 100 * (plus_dm.rolling(14).mean() / atr_14)
        minus_di = 100 * (minus_dm.rolling(14).mean() / atr_14)
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx = float(dx.rolling(14).mean().iloc[-1])
        current_plus_di = float(plus_di.iloc[-1])
        current_minus_di = float(minus_di.iloc[-1])

        # ADX check
        if adx < adx_threshold:
            return False, f"adx_low_{adx:.1f}", False

        # +DI > -DI
        if current_plus_di <= current_minus_di:
            return False, f"di_bearish_+{current_plus_di:.0f}/-{current_minus_di:.0f}", False

        trend_mode = "STRONG" if is_strong else "normal"
        return True, f"quantum_{trend_mode}_rsi={current_rsi:.0f}_adx={adx:.0f}", is_strong

    def _check_risky_entry_conditions(self, instrument: str, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Stricter entry conditions for risky instruments (DOT, LTC, AVAX).

        Requirements:
        1. Triple EMA alignment (9 > 21 > 55)
        2. ADX > 25 (strong trend)
        3. Price above EMA 9 (immediate momentum)

        Returns: (passed, reason)
        """
        close = df['close'].astype(float)
        current_price = float(close.iloc[-1])
        ema_9 = close.ewm(span=9, adjust=False).mean().iloc[-1]

        # Check 1: Triple EMA
        if not self._check_triple_ema_bullish(df):
            return False, "triple_ema_failed"

        # Check 2: ADX strength
        if not self._check_adx_strength(df, min_adx=25.0):
            return False, "adx_weak"

        # Check 3: Price above EMA 9
        if current_price < ema_9:
            return False, "price_below_ema9"

        return True, "all_checks_passed"

    def _is_risky_instrument(self, instrument: str) -> bool:
        """Check if instrument requires extra EMA verification."""
        return instrument in self.RISKY_INSTRUMENTS
    
    def _get_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return 0.0
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _check_signal_still_valid(self, instrument: str, df: pd.DataFrame, pos: Position) -> bool:
        """
        Re-evaluate the conditions that triggered the trade.
        Returns True if signal is still valid, False if we should exit.

        Signal is INVALID if:
        1. EMA crossed bearish (fast < slow)
        2. RSI dropped below 40 (bearish momentum)
        3. Price broke below the higher low we entered on
        """
        if len(df) < 60:
            return True  # Not enough data, stay in

        close = df['close'].astype(float)
        current_low = float(df.iloc[-1]['low'])

        # Check 1: EMA still bullish?
        ema_fast = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema_slow = close.ewm(span=55, adjust=False).mean().iloc[-1]
        ema_bearish = ema_fast < ema_slow

        # Check 2: RSI still healthy?
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        current_rsi = float(rsi.iloc[-1])
        rsi_weak = current_rsi < 40

        # Check 3: Price still above the higher low?
        higher_low_broken = current_low < pos.transition.higher_low

        # Signal is invalid if ANY of these conditions are met
        if ema_bearish and rsi_weak:
            LOG.debug(f"  {instrument}: EMA bearish + RSI weak ({current_rsi:.1f})")
            return False

        if higher_low_broken:
            LOG.debug(f"  {instrument}: Higher low broken (${current_low:.2f} < ${pos.transition.higher_low:.2f})")
            return False

        return True
    
    def _calculate_equity(self, prices: Dict[str, float]) -> float:
        equity = self.balance
        for instrument, pos in self.positions.items():
            price = prices.get(instrument, pos.entry_price)
            equity += pos.qty * price
        return equity
    
    def process_bar(self, instrument: str, df: pd.DataFrame, current_time: datetime) -> Optional[str]:
        if len(df) < 60:
            return None

        current_price = float(df.iloc[-1]['close'])

        # Update structure and check for Level 4 transition
        transition = self.structure.update(instrument, df)

        # ========== CHECK EXITS ==========
        if instrument in self.positions:
            pos = self.positions[instrument]
            loss_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100

            # Update peak price tracking
            if current_price > pos.peak_price:
                pos.peak_price = current_price

            # 10% TRAILING STOP - Activates immediately from entry
            # Trail 10% below peak price at all times
            trailing_stop_10pct = pos.peak_price * 0.90  # 10% below peak
            if current_price < trailing_stop_10pct:
                drawdown_from_peak = ((pos.peak_price - current_price) / pos.peak_price) * 100
                LOG.info(f"[TRAILING STOP 10%] {instrument}: {drawdown_from_peak:.1f}% from peak @ {loss_pct:.1f}% P&L")
                return self._close_position(instrument, current_price, "trailing_stop_10pct")

            # TRIPLE QUANTUM EXIT - Per-instrument sensitivity (only when underwater)
            if loss_pct < 0:
                quantum_exit, quantum_reason = TripleQuantumEMA.check_quantum_exit(df, instrument)
                if quantum_exit:
                    LOG.info(f"[QUANTUM EXIT] {instrument}: {quantum_reason} @ {loss_pct:.1f}%")
                    return self._close_position(instrument, current_price, quantum_reason)

            # LEG 4 EXIT: Check 15M conditions (using 1H as proxy for now)
            htf_bullish, _ = HTFRegimeDetector.should_allow_long(df)
            leg4_exit, leg4_reason = Quantum15MStrategy.check_exit_signal(df, htf_bullish)
            if leg4_exit and loss_pct < -5:  # Only exit via Leg 4 if underwater -5%+
                LOG.info(f"[LEG4 EXIT] {instrument}: {leg4_reason} @ {loss_pct:.1f}%")
                return self._close_position(instrument, current_price, leg4_reason)

            # PROFIT TRAILING STOP - 2% activate, 1.75% trail (tighter for winners)
            profit_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            if profit_pct >= self.trailing_trigger_pct:
                trailing_stop = pos.peak_price * (1 - self.trailing_pct / 100)
                if current_price < trailing_stop and current_price > pos.entry_price:
                    return self._close_position(instrument, current_price, "trailing_stop")

            return None

        # ========== CHECK ENTRY (Level 4 + Leg 4) ==========
        # Check HTF regime for Leg 4
        htf_bullish, htf_reason = HTFRegimeDetector.should_allow_long(df)

        # ORIGINAL ENTRY: Level 4 Structure Transition
        if transition and len(self.positions) < self.max_positions:
            key = f"{instrument}_{transition.trough_price}"
            if key not in self.traded_transitions:

                # UNIVERSAL ENTRY CHECK with trend strength
                passed, reason, is_strong_trend = self._check_entry_conditions(instrument, df)
                if not passed:
                    LOG.info(f"[REJECTED] {instrument}: Level 4 reached but {reason}")
                    self.risky_rejections += 1
                    return None

                # ADDITIONAL RISKY CHECK: Triple EMA + ADX 25+ for DOT/LTC/AVAX
                if self._is_risky_instrument(instrument):
                    passed, reason = self._check_risky_entry_conditions(instrument, df)
                    if not passed:
                        LOG.info(f"[REJECTED RISKY] {instrument}: Failed {reason}")
                        self.risky_rejections += 1
                        return None
                    else:
                        LOG.info(f"[HYBRID OK] {instrument}: All risky checks passed")

                self.traded_transitions.add(key)
                return self._open_position(instrument, current_price, current_time, transition, is_strong_trend)

        # LEG 4 ENTRY: 15M Quantum (using 1H as proxy) - Only if no L4 transition
        # This generates ADDITIONAL signals when structure isn't ready but momentum is
        if instrument not in self.positions and len(self.positions) < self.max_positions:
            if htf_bullish:
                leg4_ok, leg4_reason, leg4_details = Quantum15MStrategy.check_entry_signal(df, htf_bullish)
                if leg4_ok:
                    # Create a synthetic transition for Leg 4 entries
                    synthetic_transition = ConfirmedTransition(
                        instrument=instrument,
                        trough_price=current_price * 0.97,  # Assume 3% below as trough
                        entry_price=current_price,
                        entry_time=current_time,
                        previous_peak=current_price * 1.03,
                        higher_low=current_price * 0.98,
                        levels_passed=["LEG4_ENTRY"],
                    )

                    # Check if we've recently traded this instrument via Leg 4
                    leg4_key = f"{instrument}_leg4_{current_time.date()}"
                    if leg4_key not in self.traded_transitions:
                        self.traded_transitions.add(leg4_key)
                        LOG.info(f"[LEG4 ENTRY] {instrument}: {leg4_reason}")
                        return self._open_position(instrument, current_price, current_time, synthetic_transition, True)

        return None
    
    def check_goal_engine(self, prices: Dict[str, float]):
        equity = self._calculate_equity(prices)
        self.goal_engine.initialize(equity)

        # Update institutional risk metrics
        self._update_risk_metrics(prices)

        should_cut, reason, cut_type = self.goal_engine.check(equity)

        if should_cut:
            gain = equity - self.goal_engine.reference_equity

            for instrument in list(self.positions.keys()):
                pos = self.positions[instrument]
                price = prices.get(instrument, pos.entry_price)

                if price < pos.entry_price:  # Only cut losers
                    is_risky = self.goal_engine.is_baby_step(instrument)

                    if cut_type == "baby_step" and is_risky:
                        # Risky instruments: cut at $3 baby step
                        LOG.info(f"[BABY STEP] {instrument} - cutting risky loser")
                        self._close_position(instrument, price, "baby_step_cut")

                    elif cut_type == "trailing" and not is_risky:
                        # Strong instruments: cut on trailing ($10 activate, $3 trail)
                        LOG.info(f"[TRAILING CUT] {instrument} - equity dropped $3 from peak")
                        self._close_position(instrument, price, "trailing_cut")

                    elif cut_type == "baby_step" and not is_risky:
                        # Strong instruments at baby step: only cut if trailing active
                        if self.goal_engine.trailing_active:
                            LOG.info(f"[TRAILING CUT] {instrument} - trailing active, cutting")
                            self._close_position(instrument, price, "trailing_cut")

            new_equity = self._calculate_equity(prices)
            self.goal_engine.after_cut(new_equity)
    
    def _open_position(self, instrument: str, price: float, time: datetime, transition: ConfirmedTransition, is_strong_trend: bool = False) -> str:
        budget, effective_leverage = self._budget(instrument)  # Per-instrument leverage with cap

        # STRONG TREND: Increase position size by 50%
        if is_strong_trend:
            budget *= 1.5
            LOG.info(f"[STRONG TREND] {instrument}: Position size +50%")

        # Check if entries are blocked due to high DD
        if budget < 10:
            if self.current_drawdown_pct >= self.DRAWDOWN_BLOCK_ENTRIES:
                LOG.warning(f"[BLOCKED] {instrument}: Entries blocked due to DD={self.current_drawdown_pct:.1f}% >= {self.DRAWDOWN_BLOCK_ENTRIES}%")
                self.entries_blocked_count += 1
            return None

        # Calculate entry fee (Kraken taker)
        entry_fee = budget * self.TAKER_FEE_RATE
        self.total_fees_paid += entry_fee

        qty = budget / price
        self.balance -= budget

        self.positions[instrument] = Position(
            instrument=instrument,
            entry_price=price,
            entry_time=time,
            transition=transition,
            qty=qty,
            notional=budget,
            peak_price=price,
        )

        # Track if this position is at 2x
        if effective_leverage == 2.0:
            self.positions_at_2x.add(instrument)

        throttle = self._get_exposure_throttle()
        throttle_str = f" | {throttle*100:.0f}% size" if throttle < 1.0 else ""
        trend_str = " | STRONG" if is_strong_trend else ""

        LOG.info(f"[ENTRY] {instrument} @ ${price:.2f} | {effective_leverage}x | ${budget:.2f} | fee=${entry_fee:.2f}{throttle_str}{trend_str}")
        LOG.info(f"  Trough: ${transition.trough_price:.2f} → Higher Low: ${transition.higher_low:.2f}")
        LOG.info(f"  Levels: {' → '.join(transition.levels_passed)}")
        return "ENTRY"
    
    def _close_position(self, instrument: str, price: float, reason: str) -> str:
        if instrument not in self.positions:
            return None

        pos = self.positions.pop(instrument)

        # Calculate exit fee (Kraken taker)
        exit_notional = pos.qty * price
        exit_fee = exit_notional * self.TAKER_FEE_RATE
        self.total_fees_paid += exit_fee

        # Net proceeds after exit fee
        proceeds = exit_notional - exit_fee
        pnl = proceeds - pos.notional
        pnl_pct = (pnl / pos.notional) * 100

        self.balance += proceeds

        # Remove from 2x tracking if applicable
        was_2x = instrument in self.positions_at_2x
        if was_2x:
            self.positions_at_2x.discard(instrument)

        self.trades.append(Trade(
            instrument=instrument,
            entry_price=pos.entry_price,
            exit_price=price,
            pnl_pct=pnl_pct,
            pnl_usd=pnl,
            reason=reason,
        ))

        # RE-ENTRY LOGIC: If we exited via trailing stop (profit), allow re-entry
        # This lets us keep trading in a bullish market after taking profit
        if "trailing" in reason and pnl > 0:
            # Remove this transition from traded set so we can re-enter
            key_to_remove = None
            for key in self.traded_transitions:
                if key.startswith(instrument):
                    key_to_remove = key
                    break
            if key_to_remove:
                self.traded_transitions.discard(key_to_remove)
                LOG.info(f"[RE-ENTRY ENABLED] {instrument}: Profitable exit, can re-enter if HTF still bull")

        leverage_str = "2x" if was_2x else "1x"
        LOG.info(f"[EXIT] {instrument} @ ${price:.2f} | {pnl_pct:+.2f}% | ${pnl:+.2f} | fee=${exit_fee:.2f} | {reason}")
        return f"EXIT_{reason.upper()}"
    
    def get_results(self, final_prices: Dict[str, float] = None) -> Dict:
        winners = [t for t in self.trades if t.pnl_pct > 0]
        losers = [t for t in self.trades if t.pnl_pct <= 0]
        stats = self.structure.get_stats()

        # Calculate final equity (cash + open position values)
        if final_prices:
            final_equity = self._calculate_equity(final_prices)
        else:
            # Use notional values as fallback
            open_value = sum(pos.notional for pos in self.positions.values())
            final_equity = self.balance + open_value

        # Calculate gross P&L (before fees would have been deducted)
        gross_pnl = sum(t.pnl_usd for t in self.trades)
        # Net P&L is already fee-adjusted in the trades
        net_pnl = final_equity - self.initial_balance

        return {
            "initial": self.initial_balance,
            "final": final_equity,  # Use equity, not just cash
            "cash_balance": self.balance,
            "return_pct": ((final_equity - self.initial_balance) / self.initial_balance) * 100,
            "total_trades": len(self.trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": len(winners) / len(self.trades) * 100 if self.trades else 0,
            "goals_reached": self.goal_engine.goals_reached,
            "positions_open": len(self.positions),
            "level_stats": stats,
            "trades": self.trades,
            "risky_rejections": self.risky_rejections,
            # Institutional risk metrics
            "max_floating_drawdown": self.max_floating_drawdown,
            "max_floating_drawdown_pct": self.max_floating_drawdown_pct,
            "worst_cluster_loss": self.worst_cluster_loss,
            "demotions_count": self.demotions_count,
            # Fee tracking
            "total_fees_paid": self.total_fees_paid,
            "entries_blocked": self.entries_blocked_count,
            # Drawdown duration
            "longest_drawdown_bars": self.longest_drawdown_bars,
            "longest_drawdown_days": self.longest_drawdown_bars / 24,  # 1H bars to days
            "total_bars_in_drawdown": self.total_bars_in_drawdown,
            "pct_time_in_drawdown": (self.total_bars_in_drawdown / max(self.current_bar_index, 1)) * 100,
            "drawdown_events": self.drawdown_events,
            # Regime tracking
            "regime_stats": self.regime_tracker.get_stats(),
        }


# =============================================================================
# BACKTEST
# =============================================================================

def run_quantum_v3_backtest(months: int = 4, balance: float = 10000.0, data_dir: str = "data"):
    """Run the Quantum v3 system backtest."""
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    
    pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "AVAX/USD",
             "ADA/USD", "LTC/USD", "DOT/USD", "POL/USD", "XRP/USD"]
    
    # Load 1H data
    data = {}
    for pair in pairs:
        tag = pair.replace("/", "")
        path = Path(data_dir) / f"bars_{tag}_1h.csv"
        if path.exists():
            df = pd.read_csv(path)
            # Handle different timestamp formats
            if df['time'].dtype == 'object':
                # ISO 8601 format string (e.g., "2024-01-15T14:30:00Z")
                df['time'] = pd.to_datetime(df['time'], utc=True).dt.tz_localize(None)
            else:
                # Unix timestamp in seconds
                df['time'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_localize(None)
            df = df.sort_values('time').reset_index(drop=True)
            data[pair] = df
            LOG.info(f"Loaded {pair}: {len(df)} bars (1H) | {df['time'].min()} to {df['time'].max()}")
    
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
    executor = QuantumExecutorV3(initial_balance=balance)

    # Set BTC data for market-wide trend detection
    if "BTC/USD" in data:
        TripleQuantumEMA.set_btc_data(data["BTC/USD"])
        LOG.info("[QUANTUM] BTC data set for market-wide trend detection")
    
    # Run
    for i, ts in enumerate(all_times):
        if i % 500 == 0 and i > 0:
            stats = executor.structure.get_stats()
            LOG.info(f"Bar {i}/{len(all_times)} | Pos:{len(executor.positions)} | L1:{stats['level1_bounce']} L2:{stats['level2_momentum']} L3:{stats['level3_structure']} L4:{stats['level4_higher_low']} | Trades:{len(executor.trades)} | Goals:{executor.goal_engine.goals_reached}")
        
        # Get prices
        prices = {}
        for pair, df in data.items():
            row = df[df['time'] == ts]
            if len(row) > 0:
                prices[pair] = float(row.iloc[0]['close'])
        
        # Check goal engine
        if prices:
            executor.check_goal_engine(prices)
        
        # Process bars
        for pair, df in data.items():
            df_slice = df[df['time'] <= ts]
            if len(df_slice) >= 60:
                executor.process_bar(pair, df_slice, ts)
    
    # Results - pass final prices to calculate equity correctly
    results = executor.get_results(final_prices=prices)
    stats = results['level_stats']
    
    print("\n" + "=" * 70)
    print("  QUANTUM SYSTEM v3 - Structure-Following Transition Engine")
    print("  Trade only Level 4: Bounce → Momentum → Structure Break → Higher Low")
    print("=" * 70)
    print(f"\n  LEVEL FUNNEL")
    print("-" * 70)
    print(f"    L1 Bounce:         {stats['level1_bounce']}")
    print(f"    L2 Momentum Flip:  {stats['level2_momentum']}")
    print(f"    L3 Structure Break:{stats['level3_structure']}")
    print(f"    L4 Higher Low:     {stats['level4_higher_low']} ← TRADES")
    print(f"\n  PERFORMANCE")
    print("-" * 70)
    print(f"    Initial:        ${results['initial']:,.2f}")
    print(f"    Final:          ${results['final']:,.2f}")
    print(f"    Return:         {results['return_pct']:+.2f}%")
    print(f"\n  TRADES")
    print("-" * 70)
    print(f"    Total Closed:   {results['total_trades']}")
    print(f"    Winners:        {results['winners']}")
    print(f"    Losers:         {results['losers']}")
    print(f"    Win Rate:       {results['win_rate']:.1f}%")
    print(f"    Still Open:     {results['positions_open']}")
    print(f"\n  GOAL ENGINE")
    print("-" * 70)
    print(f"    Goals Reached:  {results['goals_reached']}")
    print(f"    Risky Rejects:  {results.get('risky_rejections', 0)} (DOT/LTC/AVAX failed triple EMA)")

    print(f"\n  INSTITUTIONAL RISK METRICS")
    print("-" * 70)
    print(f"    Max Floating DD:    ${results['max_floating_drawdown']:.2f} ({results['max_floating_drawdown_pct']:.1f}%)")
    print(f"    Worst Cluster Loss: ${results['worst_cluster_loss']:.2f}")
    print(f"    2x Demotions:       {results['demotions_count']} (DD > 15% events)")
    print(f"    Entries Blocked:    {results.get('entries_blocked', 0)} (DD > 30% events)")

    print(f"\n  DRAWDOWN DURATION")
    print("-" * 70)
    print(f"    Longest Drawdown:   {results.get('longest_drawdown_bars', 0)} bars ({results.get('longest_drawdown_days', 0):.1f} days)")
    print(f"    Time in Drawdown:   {results.get('pct_time_in_drawdown', 0):.1f}% of backtest")
    print(f"    Drawdown Events:    {results.get('drawdown_events', 0)}")

    print(f"\n  FEE IMPACT (Kraken 0.26% taker)")
    print("-" * 70)
    print(f"    Total Fees Paid:    ${results['total_fees_paid']:.2f}")
    print(f"    Fees as % of P&L:   {(results['total_fees_paid'] / max(results['return_pct'] * results['initial'] / 100, 1)) * 100:.1f}%")

    regime_stats = results.get('regime_stats', {})
    print(f"\n  REGIME TRACKING")
    print("-" * 70)
    print(f"    Bullish Regimes:    {regime_stats.get('bullish_regimes', 0)}")
    print(f"    Bearish Regimes:    {regime_stats.get('bearish_regimes', 0)}")
    print(f"    Regime Changes:     {regime_stats.get('regime_changes', 0)}")
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
    
    run_quantum_v3_backtest(months=args.months, balance=args.balance)
