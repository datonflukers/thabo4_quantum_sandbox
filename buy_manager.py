# -*- coding: utf-8 -*-
"""Buy Manager - Vortex Strategy (Dual-Layer EMA) + Dynamic Position Sizing + Probe Sensor"""
from __future__ import annotations
from typing import Tuple, Dict, Optional, List
from logging import getLogger
import time
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import re
from account_balance_fetcher_kraken import BalanceFetcher
from order_executor_kraken import OrderExecutor
from risk_manager import get_risk_manager
from config_factory import CFG
import os

# NEW: Import position sizing helper
from position_sizing import (
    compute_notional_with_caps,
    ExposureTracker,
    is_instrument_active,
    calculate_dynamic_notional,
    get_safe_notional,
)

# NEW: Import probe sensor for deferred entry
try:
    from probe_sensor_live import get_probe_sensor, ProbeSensor
    PROBE_SENSOR_AVAILABLE = True
except ImportError:
    PROBE_SENSOR_AVAILABLE = False

# Vortex Strategy (Dual-Layer EMA - optimized from 2-year backtest)
try:
    from live_vortex_strategy import get_vortex_strategy, TRADEABLE_COINS
    VORTEX_STRATEGY_AVAILABLE = True
except ImportError:
    VORTEX_STRATEGY_AVAILABLE = False
    TRADEABLE_COINS = set()

# Legacy EMA Strategy (fallback)
try:
    from live_ema_strategy import get_live_strategy
    LIVE_STRATEGY_AVAILABLE = True
except ImportError:
    LIVE_STRATEGY_AVAILABLE = False

# Finta for technical indicators
try:
    from finta import TA
    FINTA_AVAILABLE = True
except ImportError:
    FINTA_AVAILABLE = False

LOG = getLogger("buy_manager")

# =============================================================================
# PROFESSIONAL OUTPUT FORMATTING
# =============================================================================

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLOR_AVAILABLE = True
except ImportError:
    COLOR_AVAILABLE = False

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    """Remove ANSI codes for width calculation."""
    return _ANSI_RE.sub("", s or "")


def _c(color, text):
    """Apply color if available."""
    if not COLOR_AVAILABLE:
        return str(text)
    return f"{color}{text}{Style.RESET_ALL}"


def _log_header(title: str) -> None:
    """Log a section header."""
    LOG.info("=" * 80)
    LOG.info(_c(Fore.CYAN, f"  {title}"))
    LOG.info("=" * 80)


def _log_subheader(title: str) -> None:
    """Log a subsection header."""
    LOG.info("-" * 80)
    LOG.info(_c(Fore.YELLOW, f"  {title}"))
    LOG.info("-" * 80)


def _log_success(msg: str) -> None:
    """Log success message."""
    LOG.info(_c(Fore.GREEN, f"  [OK] {msg}"))


def _log_warning(msg: str) -> None:
    """Log warning message."""
    LOG.warning(_c(Fore.YELLOW, f"  [WARN] {msg}"))


def _log_error(msg: str) -> None:
    """Log error message."""
    LOG.error(_c(Fore.RED, f"  [FAIL] {msg}"))


def _log_info(label: str, value) -> None:
    """Log info line with label."""
    LOG.info(f"    {label:<25} {value}")


def _color_amount(amount: float, include_sign: bool = False) -> str:
    """Color-code a dollar amount."""
    if not COLOR_AVAILABLE:
        sign = "+" if include_sign and amount > 0 else ""
        return f"{sign}${amount:,.2f}"
    if amount > 0:
        sign = "+" if include_sign else ""
        return f"{Fore.GREEN}{sign}${amount:,.2f}{Style.RESET_ALL}"
    elif amount < 0:
        return f"{Fore.RED}${amount:,.2f}{Style.RESET_ALL}"
    else:
        return f"{Fore.YELLOW}${amount:,.2f}{Style.RESET_ALL}"


def _color_pct(pct: float) -> str:
    """Color-code a percentage."""
    if not COLOR_AVAILABLE:
        return f"{pct:+.2f}%"
    if pct > 0:
        return f"{Fore.GREEN}{pct:+.2f}%{Style.RESET_ALL}"
    elif pct < 0:
        return f"{Fore.RED}{pct:+.2f}%{Style.RESET_ALL}"
    else:
        return f"{Fore.YELLOW}{pct:+.2f}%{Style.RESET_ALL}"


def _render_buy_signals_table(signals: List[Dict]) -> str:
    """Render a table of buy signals."""
    if not signals:
        return "  No buy signals"
    
    lines = []
    lines.append("")
    lines.append("    " + "-" * 76)
    lines.append(f"    {'Pair':<12} {'Signal':<10} {'Confidence':<12} {'Type':<12} {'Stop Price':<14} {'Notional':<12}")
    lines.append("    " + "-" * 76)
    
    for sig in signals:
        pair = sig.get('pair', 'Unknown')
        should_buy = sig.get('should_buy', False)
        conf = sig.get('confidence', 0)
        order_type = sig.get('order_type', 'none')
        stop_price = sig.get('stop_price')
        notional = sig.get('notional')
        
        # Determine signal status and color
        if should_buy:
            signal_str = _c(Fore.GREEN, "BUY")
        else:
            signal_str = _c(Fore.YELLOW, "WAIT")
        
        # Color confidence
        if conf >= 0.8:
            conf_str = _c(Fore.GREEN, f"{conf:.2f}")
        elif conf >= 0.5:
            conf_str = _c(Fore.YELLOW, f"{conf:.2f}")
        else:
            conf_str = _c(Fore.RED, f"{conf:.2f}")
        
        # Order type coloring
        if order_type == "stop_buy":
            type_str = _c(Fore.CYAN, "stop_buy")
        else:
            type_str = order_type
        
        stop_str = f"${stop_price:.2f}" if stop_price else "--"
        notional_str = f"${notional:.2f}" if notional else "--"
        
        lines.append(f"    {pair:<12} {signal_str:<18} {conf_str:<20} {type_str:<20} {stop_str:<14} {notional_str:<12}")
    
    lines.append("    " + "-" * 76)
    lines.append("")
    
    return "\n".join(lines)


# =============================================================================
# END PROFESSIONAL OUTPUT FORMATTING
# =============================================================================

try:
    from decision_report import append_signal as _append_signal
except Exception:
    def _append_signal(*args, **kwargs):
        return None

# Optional neural network integration
try:
    from neuro_network import NeuroNetwork
except Exception:
    NeuroNetwork = None # type: ignore

try:
    from rebound_tracker import ReboundTracker
    # Initialize rebound tracker with config settings
    _rebound_tracker = ReboundTracker(
        pct=float(getattr(CFG, "REBOUND_PCT_TARGET", 0.0175)),
        hold_bars=int(getattr(CFG, "REBOUND_HOLD_BARS", 2)),
        tf_seconds=int(getattr(CFG, "REBOUND_TF_SECONDS", 900)),
        continuous=str(getattr(CFG, "CONTINUOUS_REBOUND_MODE", "false")).lower() == "true",
        rsi_period=int(getattr(CFG, "RSI_PERIOD", 14)),
        rsi_delta_pts=float(getattr(CFG, "RSI_DELTA_PTS", 10.0)),
        rsi_cross_threshold=float(getattr(CFG, "RSI_CROSS_THRESHOLD", 40.0))
    )
except ImportError:
    ReboundTracker = None
    _rebound_tracker = None
except Exception as e:
    import logging
    logging.getLogger("buy_manager").warning(f"Failed to initialize ReboundTracker: {e}")
    _rebound_tracker = None

# Optional memory integration
try:
    from memory_store import MemoryStore # type: ignore
    from memory_aggregator import MemoryAggregator # type: ignore
except Exception:
    MemoryStore = None # type: ignore
    MemoryAggregator = None # type: ignore

_balance_fetcher: Optional[BalanceFetcher] = None
_order_executor: Optional[OrderExecutor] = None
_risk_manager = None
_neural_net: Optional[NeuroNetwork] = None
_buy_stop_thresholds: Dict[str, float] = {}
_rebound_state: Dict[str, Dict] = {}

# Injected memory components
_memory_store: Optional[MemoryStore] = None # type: ignore
_memory_aggr: Optional[MemoryAggregator] = None # type: ignore

# Expose memory injection for external callers
__all__ = [
 'set_balance_fetcher', 'set_risk_manager', 'set_neural_network', 'set_memory_components',
 'should_buy', 'record_stop_loss_exit', 'evaluate_all_pairs'
]

CONTINUOUS_REBOUND_MODE = bool(os.getenv("CONTINUOUS_REBOUND_MODE", "false").lower() in ("1", "true", "yes"))

# --- Public setters for external initialization ---

def set_balance_fetcher(bf: BalanceFetcher) -> None:
    global _balance_fetcher
    _balance_fetcher = bf

def set_risk_manager(risk_mgr) -> None:
    global _risk_manager
    _risk_manager = risk_mgr

def set_neural_network(nn: NeuroNetwork) -> None: # type: ignore
    """Allow external injection of a trained neural network."""
    global _neural_net
    _neural_net = nn

def set_memory_components(store, aggregator) -> None:
    """Inject memory store and aggregator for confidence fusion adjustments."""
    global _memory_store, _memory_aggr
    _memory_store = store
    _memory_aggr = aggregator

# --- Internals ---

def _get_balance_fetcher() -> BalanceFetcher:
    global _balance_fetcher
    if _balance_fetcher is None:
        _balance_fetcher = BalanceFetcher()
    return _balance_fetcher

def _get_order_executor() -> OrderExecutor:
    global _order_executor
    if _order_executor is None:
        _order_executor = OrderExecutor(_get_balance_fetcher())
    return _order_executor

def _ensure_neural_loaded() -> None:
    """Lazy-load neural network if available and not already loaded."""
    global _neural_net
    if _neural_net is None and NeuroNetwork is not None:
        try:
            nn = NeuroNetwork()
            if not nn.is_trained:
                nn.load()
            _neural_net = nn if nn.is_trained else None
            if _neural_net:
                LOG.info("[BUY] Neural network loaded for confidence fusion")
        except Exception as e:
            LOG.warning(f"Neural network load failed: {e}")
            _neural_net = None

def _csv_path(pair: str, tf: str) -> Path:
    tag = pair.replace("/", "")
    return Path(CFG.DATA_DIR) / f"bars_{tag}_{tf}.csv"

def _read_latest_close(pair: str) -> float:
    """Return latest close price from15m CSV or fallback to0.0."""
    path = _csv_path(pair, "15m")
    if path.exists():
        try:
            df = pd.read_csv(path)
            if len(df) >0 and "close" in df.columns:
                return float(df["close"].iloc[-1])
        except Exception:
            pass
    return 0.0

def _read_low_since_ts(pair: str, since_ts: float) -> Optional[float]:
    """Read the minimum low from data collector CSVs since a given epoch ts.
    Prefer1m, fallback to5m then15m. Returns None if not available.
    """
    for tf in ("1m", "5m", "15m"):
        path = _csv_path(pair, tf)
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path)
            if df.empty or "time" not in df.columns:
                continue
            ts_series = pd.to_datetime(df["time"], utc=True)
            try:
                epoch = ts_series.view("int64") //10**9
            except Exception:
                epoch = (ts_series.astype("int64") //10**9)
            mask = epoch >= since_ts
            if mask.any():
                df2 = df.loc[mask]
            else:
                df2 = df.tail(200)
            if "low" in df2.columns and not df2.empty:
                low_val = float(df2["low"].min())
                return low_val
        except Exception:
            continue
    return None

def _update_rebound_low(pair: str, current_price: float) -> None:
    """Update rebound low tracking using the enhanced tracker."""
    tracker = _get_rebound_tracker()
    if tracker is not None:
        df = _load_15m_data(pair)
        tracker.update_low(pair, current_price, df)

def record_stop_loss_exit(pair: str, exit_price: float) -> float:
    """Set re-entry threshold: exit_price * (1 + BUY_STOP_PCT). Also start rebound tracking."""
    try:
        exit_p = float(exit_price)
    except Exception:
        exit_p = 0.0
    pct = float(getattr(CFG, "BUY_STOP_PCT", 0.0175))
    if exit_p <= 0:
        return 0.0
    threshold = exit_p * (1.0 + pct)
    _buy_stop_thresholds[pair] = threshold

    # Update rebound tracker
    tracker = _get_rebound_tracker()
    if tracker is not None:
        tracker.record_stop_loss_exit(pair, exit_p)

    LOG.info(f"[BUY_STOP] {pair} threshold {threshold:.6f}; rebound tracking started at lowest={exit_p:.6f}")
    try:
        _append_signal("buy_manager", "15m", "buy_stop_set", {
            "pair": pair, "exit_price": round(exit_p, 8), "threshold": round(threshold, 8), "pct": pct
        })
        _append_signal("buy_manager", "15m", "rebound_tracking_started", {
            "pair": pair, "lowest": round(exit_p, 8)
        })
    except Exception:
        pass
    return threshold

def _evaluate_rebound_buy(pair: str, price: float, consume: bool = True) -> Tuple[bool, Dict]:
    """Check rebound condition with RSI assistance.
    
    Args:
        pair: Trading pair
        price: Current price
        consume: If True, reset state after returning signal (default behavior).
                 If False, just peek at state without consuming it.
    """
    tracker = _get_rebound_tracker()
    if tracker is None:
        return False, {}

    df = _load_15m_data(pair)
    rebound_state = tracker.evaluate(pair, price, df)

    armed = rebound_state.get("armed", False)
    lowest = rebound_state.get("lowest", price)
    rebound_pct = rebound_state.get("rebound_pct", 0.0)

    if armed and price > 0:
        # Place buy-stop 2% above current price
        stop_price = price * 1.02

        feats = {
            "reason": "rsi_assisted_rebound",
            "lowest": float(lowest),
            "rebound_pct": float(rebound_pct),
            "order_type": "stop_buy",
            "stop_price": float(stop_price),
            "current_price": float(price),
            "trailing": True,
            "offset_pct": 0.02,
            # RSI metrics
            "rsi": rebound_state.get("rsi"),
            "rsi_low_watch": rebound_state.get("rsi_low_watch"),
            "rsi_rebound_pct": rebound_state.get("rsi_rebound_pct"),
            "rsi_delta": rebound_state.get("rsi_delta"),
            "rsi_cross40_up": rebound_state.get("rsi_cross40_up"),
            "rsi_improving": rebound_state.get("rsi_improving"),
            "price_rebound_armed": rebound_state.get("price_rebound_armed"),
        }

        # Only consume state if requested (default=True for backward compatibility)
        if consume:
            # Reset rebound state after successful buy signal
            tracker.reset_on_fill_or_timeout(pair)
            
            # Reset troughs to current values for fresh down-leg requirement
            if df is not None:
                current_rsi = rebound_state.get("rsi")
                if current_rsi is not None:
                    tracker.rsi_state.setdefault(pair, {})["rsi_low_watch"] = current_rsi
            
            # Clear price low so it reseeds on next tick
            tracker.state[pair]["lowest"] = None
            
            # Optional cooldown (2 bars)
            cooldown_bars = getattr(CFG, "REBOUND_COOLDOWN_BARS_AFTER_BUY", 2)
            tracker.state[pair]["cooldown_until"] = time.time() + (cooldown_bars * tracker.tf_seconds)

            try:
                _append_signal("buy_manager", "15m", "rebound_buy_reset", {
                    "pair": pair,
                    "stop_price": round(float(stop_price), 8),
                    "rsi_low_watch_reset": round(float(current_rsi or 0), 2),
                    "cooldown_bars": cooldown_bars,
                    "lowest_cleared": True
                })
            except Exception:
                pass

        return True, feats

    return False, {}

def _get_current_price(pair: str) -> float:
    return _read_latest_close(pair)

# --- Technical helpers ---

def _calculate_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if df.empty or "close" not in df.columns or len(df) < period + 1:
        return None
    try:
        closes = df["close"].astype(float)
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("inf"))
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None
    except Exception:
        return None

def _ema(series: pd.Series, length: int) -> Optional[float]:
    if series.empty or len(series) < length:
        return None
    return float(series.ewm(span=length, adjust=False).mean().iloc[-1])

def _ichimoku(df: pd.DataFrame, tenkan=9, kijun=26, span_b=52) -> Tuple[Optional[float], Optional[float]]:
    """Return (span_a, span_b) shifted forward (we only need last values)."""
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        conv = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2.0
        base = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2.0
        span_a = ((conv + base) / 2.0).shift(kijun)
        span_bv = ((high.rolling(span_b).max() + low.rolling(span_b).min()) / 2.0).shift(kijun)
        return (
            float(span_a.iloc[-1]) if not pd.isna(span_a.iloc[-1]) else None,
            float(span_bv.iloc[-1]) if not pd.isna(span_bv.iloc[-1]) else None,
        )
    except Exception:
        return (None, None)


def _calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Calculate ADX (Average Directional Index) for trend strength.
    
    ADX values:
    - < 20: Weak/No trend
    - 20-25: Trend emerging  
    - 25-50: Strong trend
    - 50-75: Very strong trend
    - > 75: Extremely strong trend
    
    Uses finta if available, otherwise manual calculation.
    """
    if df is None or len(df) < period + 10:
        return None
    
    try:
        # Ensure required columns exist
        if not all(col in df.columns for col in ["high", "low", "close"]):
            return None
        
        # Use finta if available
        if FINTA_AVAILABLE:
            try:
                # Finta expects ohlc DataFrame
                ohlc = df[["open", "high", "low", "close"]].copy()
                ohlc.columns = ["open", "high", "low", "close"]
                adx_df = TA.ADX(ohlc, period=period)
                if adx_df is not None and len(adx_df) > 0:
                    adx_val = float(adx_df.iloc[-1])
                    if not pd.isna(adx_val):
                        return adx_val
            except Exception as e:
                LOG.debug(f"Finta ADX failed, using manual: {e}")
        
        # Manual ADX calculation fallback
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Directional Movement
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        # Smoothed averages
        atr = pd.Series(tr).rolling(window=period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(window=period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(window=period).mean() / atr
        
        # ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
        return float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else None
        
    except Exception as e:
        LOG.debug(f"ADX calculation failed: {e}")
        return None


def _get_ichimoku_cloud_position(df: pd.DataFrame, price: float) -> Dict:
    """
    Determine price position relative to Ichimoku cloud.
    
    Returns:
        Dict with:
        - above_cloud: bool
        - in_cloud: bool
        - below_cloud: bool
        - cloud_top: float
        - cloud_bottom: float
        - distance_pct: float (positive = above, negative = below)
    """
    result = {
        "above_cloud": False,
        "in_cloud": False,
        "below_cloud": False,
        "cloud_top": None,
        "cloud_bottom": None,
        "distance_pct": 0.0,
    }
    
    try:
        span_a, span_b = _ichimoku(df)
        
        if span_a is None or span_b is None:
            return result
        
        cloud_top = max(span_a, span_b)
        cloud_bottom = min(span_a, span_b)
        
        result["cloud_top"] = cloud_top
        result["cloud_bottom"] = cloud_bottom
        
        if price > cloud_top:
            result["above_cloud"] = True
            result["distance_pct"] = ((price - cloud_top) / cloud_top) * 100
        elif price < cloud_bottom:
            result["below_cloud"] = True
            result["distance_pct"] = ((price - cloud_bottom) / cloud_bottom) * 100
        else:
            result["in_cloud"] = True
            result["distance_pct"] = 0.0
            
    except Exception as e:
        LOG.debug(f"Ichimoku cloud position failed: {e}")
    
    return result


def _evaluate_trend_strength(df: pd.DataFrame, price: float) -> Dict:
    """
    Comprehensive trend strength evaluation using ADX and Ichimoku.
    
    Returns:
        Dict with trend analysis:
        - trend_strength: 'strong', 'medium', 'weak', 'none'
        - adx: float
        - cloud_position: 'above', 'in', 'below'
        - trend_score: 0-100
        - trend_direction: 'bullish', 'bearish', 'neutral'
    """
    result = {
        "trend_strength": "none",
        "adx": 0.0,
        "cloud_position": "unknown",
        "trend_score": 0.0,
        "trend_direction": "neutral",
        "rsi": None,
    }
    
    if df is None or len(df) < 60:
        return result
    
    try:
        # Calculate ADX
        adx = _calculate_adx(df)
        result["adx"] = adx or 0.0
        
        # Calculate RSI
        rsi = _calculate_rsi(df)
        result["rsi"] = rsi
        
        # Get Ichimoku cloud position
        cloud = _get_ichimoku_cloud_position(df, price)
        
        if cloud["above_cloud"]:
            result["cloud_position"] = "above"
        elif cloud["in_cloud"]:
            result["cloud_position"] = "in"
        elif cloud["below_cloud"]:
            result["cloud_position"] = "below"
        
        # Calculate trend score (0-100)
        score = 0.0
        
        # ADX contribution (0-40 points)
        if adx:
            if adx >= 50:
                score += 40
            elif adx >= 30:
                score += 30
            elif adx >= 20:
                score += 20
            elif adx >= 15:
                score += 10
        
        # Cloud position contribution (0-30 points)
        if cloud["above_cloud"]:
            score += 30
        elif cloud["in_cloud"]:
            score += 15
        
        # RSI contribution (0-30 points) - favor 40-60 range for trend
        if rsi:
            if 40 <= rsi <= 60:
                score += 30  # Healthy trend
            elif 30 <= rsi <= 70:
                score += 20  # Moderate
            elif rsi > 70:
                score += 10  # Overbought but trending
            else:
                score += 5   # Oversold
        
        result["trend_score"] = score
        
        # Determine trend strength
        if score >= 70:
            result["trend_strength"] = "strong"
        elif score >= 50:
            result["trend_strength"] = "medium"
        elif score >= 30:
            result["trend_strength"] = "weak"
        else:
            result["trend_strength"] = "none"
        
        # Determine trend direction
        if cloud["above_cloud"] and adx and adx >= 20:
            result["trend_direction"] = "bullish"
        elif cloud["below_cloud"] and adx and adx >= 20:
            result["trend_direction"] = "bearish"
        else:
            result["trend_direction"] = "neutral"
            
    except Exception as e:
        LOG.debug(f"Trend strength evaluation failed: {e}")
    
    return result


def _load_15m_data(pair: str) -> Optional[pd.DataFrame]:
    path = _csv_path(pair, "15m")
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        if df.empty:
            return None
        return df
    except Exception:
        return None

# --- Existing RSI oversold logic retained ---

def _evaluate_standard_buy(pair: str, price: float, account_balance: float) -> Tuple[bool, float, Dict]:
    feats = {}
    df = _load_15m_data(pair)
    if df is None or len(df) < 60: # need enough for Ichimoku/EMA anyway
        feats["reason"] = "insufficient_data"
        return False, 0.0, feats
    rsi = _calculate_rsi(df, period=14)
    if rsi is None:
        feats["reason"] = "rsi_calculation_failed"
        return False, 0.0, feats
    feats["rsi"] = round(float(rsi), 2)
    if rsi < 40:
        if rsi <= 30:
            conf = 1.0
        else:
            conf = 1.0 - ((rsi - 30) / 10) * 0.35
        threshold = float(getattr(CFG, "BUY_CONF_THRESHOLD", 0.65))
        if conf >= threshold:
            feats["reason"] = f"oversold_rsi_{rsi:.1f}"
            return True, conf, feats
        else:
            feats["reason"] = "rsi_low_but_below_threshold"
            return False, conf, feats
    feats["reason"] = "rsi_neutral_or_overbought"
    return False, 0.0, feats

# --- New fused scoring (RSI + Ichimoku + EMA + Rebound + Neural) ---

# Epsilon floor to prevent geometric mean collapse
EPS = 1e-3

# memory weighting constants (conservative)
W_MOM = 0.05
W_RISK = 0.07
W_WIN = 0.03
W_FLAGS = 0.05 # penalty if conflicting buy orders exist


def _soft_rsi_score(rsi_val: Optional[float]) -> float:
    """Soft RSI penalty (no hard zeroing at overbought levels)."""
    if rsi_val is None:
        return 0.5
    if rsi_val <= 30:  # oversold = bullish
        return 1.0
    if rsi_val >= 70:  # overbought = soft penalty (not zero)
        return 0.3
    # Linear interpolation between 30-70
    return 1.0 - 0.7 * ((rsi_val - 30) / 40)


def _compute_rule_subscores(price, rsi_val, ema_fast, ema_slow, span_a, span_b, rebound_pct):
    """Compute indicator subscores."""
    def clamp(x, lo=0.0, hi=1.0):
        return max(lo, min(hi, x))
    
    s_rsi = _soft_rsi_score(rsi_val)
    
    # Cloud position
    s_cloud = 0.5
    if span_a is not None and span_b is not None:
        cloud_top = max(span_a, span_b)
        cloud_bot = min(span_a, span_b)
        if price > cloud_top:
            s_cloud = 1.0
        elif price < cloud_bot:
            s_cloud = 0.2
        else:
            s_cloud = 0.5
    
    # EMA crossover
    s_ema = 0.5
    if ema_fast is not None and ema_slow is not None:
        spread = (ema_fast - ema_slow) / ema_slow if ema_slow > 0 else 0
        s_ema = clamp(1 / (1 + np.exp(-6.0 * spread)))
    
    # Rebound
    s_rebound = clamp(rebound_pct / 0.0175)
    
    # Apply epsilon floor
    s_rsi = max(s_rsi, EPS)
    s_cloud = max(s_cloud, EPS)
    s_ema = max(s_ema, EPS)
    s_rebound = max(s_rebound, EPS)
    
    s_rules = (s_rsi * s_cloud * s_ema * s_rebound) ** 0.25
    
    return {
        "s_rsi": s_rsi,
        "s_cloud": s_cloud,
        "s_ema": s_ema,
        "s_rebound": s_rebound,
        "s_rules": s_rules,
    }


def _fused_confidence(pair: str, df, price: float, lowest, rebound_pct: float):
    """Fused confidence scoring."""
    if df is None or len(df) < 50:
        return {"fused_conf": 0.0}
    
    rsi_val = _calculate_rsi(df, 14)
    ema_fast = _ema(df["close"], 9)
    ema_slow = _ema(df["close"], 21)
    span_a, span_b = _ichimoku(df)
    
    subs = _compute_rule_subscores(price, rsi_val, ema_fast, ema_slow, span_a, span_b, rebound_pct)
    
    fused = subs.get("s_rules", 0.0)
    fused = max(0.0, min(1.0, fused))
    
    return {
        "fused_conf": fused,
        **subs
    }


def should_buy(pair: str, account_balance: float = 0.0, explain: bool = False, consume: bool = True, **kwargs) -> Tuple[bool, float, Dict]:
    """
    Determine if a buy signal is valid for the given pair.

    Args:
        pair: Trading pair (e.g., "BTC/USD")
        account_balance: Available cash for sizing
        explain: If True, include detailed explanation
        consume: If True (default), consume rebound signal state after returning.
        **kwargs: Additional parameters

    Returns:
        Tuple of (should_buy, confidence, features_dict)
    """
    price = _get_current_price(pair)
    feats = {"current_price": float(price or 0.0), "mode": getattr(CFG, "MODE", "sandbox")}

    if price <= 0:
        feats["reason"] = "no_price"
        return False, 0.0, feats

    # === VORTEX STRATEGY POSITION FILTERING ===
    # Check if pair is in tradeable coins (based on backtest performance)
    if VORTEX_STRATEGY_AVAILABLE and TRADEABLE_COINS:
        if pair not in TRADEABLE_COINS:
            feats["reason"] = f"pair_not_tradeable (PF < 1.3)"
            feats["tradeable_coins"] = list(TRADEABLE_COINS)
            return False, 0.0, feats

        # Check position limits using Vortex strategy
        try:
            strategy = get_vortex_strategy()
            can_open, reason = strategy.can_open_position(pair)
            if not can_open:
                feats["reason"] = f"strategy_blocked: {reason}"
                feats["active_positions"] = strategy.get_position_count()
                feats["max_positions"] = strategy.max_positions
                return False, 0.0, feats

            # Apply tiered position sizing
            size_multiplier = strategy.get_position_size_multiplier(pair)
            feats["tier"] = strategy.get_coin_tier(pair)
            feats["size_multiplier"] = size_multiplier
        except Exception as e:
            LOG.debug(f"Vortex strategy check failed: {e}")

    # Calculate notional
    safe_notional = 0.0
    if account_balance > 0:
        try:
            bf = _get_balance_fetcher()
            exe = _get_order_executor()
            tracker = ExposureTracker(bf, exe, sell_manager=None)
            exposure_info = tracker.get_exposure_info()

            safe_notional = calculate_dynamic_notional(
                pair=pair,
                current_price=price,
                account_balance_usd=account_balance,
                exposure_info=exposure_info,
                min_notional=float(getattr(CFG, "MIN_TRADE_USD", 25.0)),
                order_type="market"
            )

            # Apply tiered sizing from Vortex strategy
            if VORTEX_STRATEGY_AVAILABLE and safe_notional > 0:
                try:
                    strategy = get_vortex_strategy()
                    size_mult = strategy.get_position_size_multiplier(pair)
                    original_notional = safe_notional
                    safe_notional = safe_notional * size_mult
                    if size_mult < 1.0:
                        LOG.info(f"[TIERED_SIZING] {pair}: ${original_notional:.2f} * {size_mult*100:.0f}% = ${safe_notional:.2f}")
                except Exception:
                    pass

        except Exception:
            safe_notional = 0.0

        feats["suggested_notional"] = float(safe_notional)
        feats["available_balance"] = float(account_balance)

        if safe_notional <= 0:
            feats["reason"] = "no_capacity_or_instrument_active"
            return False, 0.0, feats

    # === VORTEX STRATEGY SIGNAL CHECK ===
    # Primary signal: Dual-Layer EMA (10/20 micro + 9/50 macro)
    if VORTEX_STRATEGY_AVAILABLE:
        try:
            strategy = get_vortex_strategy()
            df = _load_15m_data(pair)

            if df is not None and len(df) >= 50:
                should_buy_vortex, size_mult, vortex_feats = strategy.should_buy(pair, df, price)

                if should_buy_vortex:
                    feats.update(vortex_feats)
                    feats["signal_type"] = "vortex_dual_ema"
                    feats["size_multiplier"] = size_mult
                    LOG.info(f"[VORTEX_SIGNAL] {pair} BUY - {vortex_feats.get('reason', 'vortex_entry')}")
                    return True, 1.0, feats
                else:
                    # No Vortex signal - propagate reason
                    feats["reason"] = vortex_feats.get("reason", "no_vortex_signal")
                    feats["vortex_status"] = vortex_feats.get("reason", "no_signal")
                    feats.update({k: v for k, v in vortex_feats.items() if k not in feats})
                    return False, 0.0, feats
            else:
                feats["reason"] = f"insufficient_data_for_vortex (rows={len(df) if df is not None else 0})"
                return False, 0.0, feats
        except Exception as e:
            LOG.warning(f"Vortex strategy signal check failed: {e}")
            feats["reason"] = f"vortex_error: {e}"

    # Fallback: Check rebound buy signal (legacy)
    ok_rebound, rebound_feats = _evaluate_rebound_buy(pair, price, consume=consume)
    if ok_rebound:
        conf = 1.0
        feats.update(rebound_feats)
        return True, conf, feats

    # Fallback: Standard evaluation with fused confidence (legacy)
    df = _load_15m_data(pair)
    fused_data = {}
    if df is not None:
        lowest = None
        rebound_pct = 0.0
        fused_data = _fused_confidence(pair, df, price, lowest, rebound_pct)
    feats.update(fused_data)

    final_conf = fused_data.get("fused_conf", 0.0)
    buy_threshold = float(getattr(CFG, "BUY_CONF_THRESHOLD", 0.65))

    is_signal = final_conf >= buy_threshold

    if is_signal:
        feats["reason"] = "confidence_threshold_met"
        return True, final_conf, feats

    feats["reason"] = "no_vortex_signal"
    return False, final_conf, feats


def _get_rebound_tracker():
    """Get the global rebound tracker instance."""
    global _rebound_tracker
    return _rebound_tracker


def get_dynamic_notional_for_buy(pair, account_balance, atr_value=None, order_type="market"):
    """Calculate dynamic notional using NK trading amount rules."""
    try:
        bf = _get_balance_fetcher()
        exe = _get_order_executor()
        tracker_inst = ExposureTracker(bf, exe, sell_manager=None)
        exposure_info = tracker_inst.get_exposure_info()
        current_price = _get_current_price(pair)
        
        if current_price <= 0:
            return 0.0
        
        return calculate_dynamic_notional(
            pair=pair,
            current_price=current_price,
            account_balance_usd=account_balance,
            exposure_info=exposure_info,
            atr_value=atr_value,
            min_notional=float(getattr(CFG, "MIN_TRADE_USD", 25.0)),
            order_type=order_type
        )
    except Exception as e:
        LOG.debug("[SIZING] Error: %s", e)
        return 0.0


def evaluate_all_pairs(pairs=None, account_balance=0.0):
    """Evaluate all pairs for buy signals."""
    if pairs is None:
        pairs = list(CFG.PAIRS)
    
    if account_balance <= 0:
        try:
            bf = _get_balance_fetcher()
            summary = bf.get_account_summary()
            account_balance = summary.get("cash", 0.0)
        except Exception:
            pass
    
    results = []
    for pair in pairs:
        try:
            result, conf, feats = should_buy(pair, account_balance=account_balance)
            results.append({
                "pair": pair,
                "should_buy": result,
                "confidence": conf,
                **feats
            })
        except Exception as e:
            results.append({
                "pair": pair,
                "should_buy": False,
                "confidence": 0.0,
                "error": str(e)
            })

    return results


def run_buy_evaluation_dashboard(pairs=None):
    """Run buy evaluation and display dashboard."""
    results = evaluate_all_pairs(pairs)

    _log_header("BUY SIGNAL EVALUATION")

    buy_count = len([r for r in results if r.get("should_buy")])
    _log_info("Total pairs", len(results))
    _log_info("Buy signals", buy_count)

    table = _render_buy_signals_table(results)
    for line in table.split("\n"):
        LOG.info(line)

    return results


# =============================================================================
# PROBE SENSOR INTEGRATION
# =============================================================================

def is_probe_enabled() -> bool:
    """Check if probe sensor is enabled."""
    if not PROBE_SENSOR_AVAILABLE:
        return False
    # Check config setting
    return bool(getattr(CFG, "PROBE_SENSOR_ENABLED", True))


def start_buy_probe(pair: str, signal_price: float, notional: float, reason: str = "") -> bool:
    """
    Start a probe instead of executing immediately.

    Returns True if probe started, False if not (already probing or disabled).
    """
    if not is_probe_enabled():
        return False

    try:
        probe = get_probe_sensor()
        return probe.start_probe(pair, "BUY", signal_price, notional, reason)
    except Exception as e:
        LOG.warning(f"[PROBE] Failed to start probe for {pair}: {e}")
        return False


def update_probe(pair: str, current_price: float, high: float = None, low: float = None) -> Tuple[str, Dict]:
    """
    Update a probe with current price.

    Returns (decision, details) where decision is:
    - "none": No active probe
    - "wait": Still monitoring
    - "confirm": Ready to execute!
    - "abort": Signal cancelled (bad entry)
    - "timeout": Took too long
    """
    if not is_probe_enabled():
        return "none", {}

    try:
        probe = get_probe_sensor()
        return probe.update(pair, current_price, high, low)
    except Exception as e:
        LOG.warning(f"[PROBE] Error updating probe for {pair}: {e}")
        return "none", {"error": str(e)}


def has_active_probe(pair: str) -> bool:
    """Check if there's an active probe for a pair."""
    if not is_probe_enabled():
        return False

    try:
        probe = get_probe_sensor()
        return probe.has_active_probe(pair)
    except:
        return False


def get_all_active_probes() -> Dict:
    """Get all active probes."""
    if not is_probe_enabled():
        return {}

    try:
        probe = get_probe_sensor()
        return probe.get_active_probes()
    except:
        return {}


def cancel_probe(pair: str, reason: str = "manual_cancel") -> bool:
    """Cancel an active probe."""
    if not is_probe_enabled():
        return False

    try:
        probe = get_probe_sensor()
        return probe.cancel_probe(pair, reason)
    except:
        return False


def update_all_probes_and_execute(exe, quote_cash: float) -> int:
    """
    Update all active probes and execute confirmed ones.

    Returns number of trades executed.
    """
    if not is_probe_enabled():
        return 0

    executed = 0

    try:
        probe = get_probe_sensor()
        active_probes = probe.get_active_probes()

        for pair, probe_state in list(active_probes.items()):
            # Get current price
            try:
                current_price = _get_current_price(pair)
                if current_price <= 0:
                    continue
            except:
                continue

            # Update probe
            decision, details = probe.update(pair, current_price)

            if decision == "confirm":
                # Execute the trade!
                notional = details.get("notional", 0)

                if notional > 0 and quote_cash >= notional:
                    min_notional = float(getattr(CFG, "MIN_NOTIONAL_BUY", 10.0))

                    if notional >= min_notional and exe.guard_min_notional(notional):
                        reason = f"probe_confirmed|{details.get('reason', 'signal')}"
                        LOG.info(f"[PROBE] EXECUTING {pair} | ${notional:.2f} | waited {details.get('waited_minutes', 0):.1f}min")

                        exe.market_buy_notional(pair, notional, reason=reason)
                        quote_cash -= notional
                        executed += 1

                        # Mark as bought
                        try:
                            from buy_cooldown import mark_bought
                            mark_bought(pair)
                        except:
                            pass

            elif decision in ("abort", "timeout"):
                LOG.info(f"[PROBE] {decision.upper()} {pair} | move={details.get('move_pct', 0):.2f}%")

    except Exception as e:
        LOG.error(f"[PROBE] Error in update_all_probes_and_execute: {e}")

    return executed


def get_probe_stats() -> Dict:
    """Get probe sensor statistics."""
    if not is_probe_enabled():
        return {"enabled": False}

    try:
        probe = get_probe_sensor()
        stats = probe.get_stats()
        stats["enabled"] = True
        stats["active_probes"] = len(probe.get_active_probes())
        return stats
    except:
        return {"enabled": False, "error": "failed_to_get_stats"}