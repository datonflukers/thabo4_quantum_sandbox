# -*- coding: utf-8 -*-
"""
Risk Manager - ATR-Based Position Sizing and Risk Evaluation

Implements:
 - NK Trading Amount Rule: $20 + 10% of available cash
 - Dynamic position sizing based on ATR (Average True Range)
 - Volatility-adjusted capital allocation
 - Risk score calculation for trade evaluation
 - Duplicate order prevention via position tracker
 - Stop-loss and take-profit level recommendations
 - Professional colorized dashboard output
"""
from __future__ import annotations

import pandas as pd
import re
from pathlib import Path
from typing import Tuple, Dict, Optional, List
from logging import getLogger
from datetime import datetime

from config_factory import CFG
from market_utils import atr_pct, rsi
from account_balance_fetcher_kraken import BalanceFetcher

LOG = getLogger("risk_manager")

# =============================================================================
# NK TRADING AMOUNT RULE CONSTANTS
# =============================================================================

TRADE_BONUS_USD = 20.0          # Fixed bonus per trade
TRADE_PCT_OF_CASH = 0.10        # 10% of available cash
PORTFOLIO_CAP_PCT = 0.70        # Maximum 70% of account in positions
MIN_TRADE_USD = 25.0            # Minimum trade size

# =============================================================================
# PROFESSIONAL OUTPUT FORMATTING
# =============================================================================

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLOR_AVAILABLE = True
except ImportError:
    COLOR_AVAILABLE = False


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


# Optional narrative voice
_voice = None


def _get_voice():
    global _voice
    if _voice is None:
        try:
            from narrative_voice import get_narrative_voice  # type: ignore
            _voice = get_narrative_voice()
        except Exception:
            _voice = None
    return _voice


class RiskManager:
    """
    Manages ATR-based position sizing and risk evaluation.
    
    Key Features:
    - NK Trading Amount Rule: $20 + 10% of cash
    - Duplicate order prevention
    - ATR-based stop-loss and take-profit
    - Professional dashboard output
    """

    def __init__(self, balance_fetcher: Optional[BalanceFetcher] = None, position_tracker=None) -> None:
        self.max_risk_per_trade = 0.02  # 2% risk per trade
        self.atr_multiplier_stop = 2.0  # SL at 2x ATR
        self.atr_multiplier_target = 3.0  # TP at 3x ATR (1.5:1 R:R)
        self.min_risk_reward_ratio = 1.5
        self.volatility_scale_factor = 1.0
        
        # NK Trading Amount Rule
        self.trade_bonus_usd = TRADE_BONUS_USD
        self.trade_pct_of_cash = TRADE_PCT_OF_CASH
        self.min_trade_usd = MIN_TRADE_USD

        self.balance_fetcher = balance_fetcher if balance_fetcher is not None else BalanceFetcher()
        self.position_tracker = position_tracker  # For duplicate prevention
        
        LOG.info("RiskManager initialized with ATR-based sizing and autonomous balance access")

    # --- NK Trading Amount Rule ---
    def compute_trade_budget(self, cash_available: float = None) -> float:
        """
        Compute trade budget using NK Trading Amount Rule.
        
        Formula: budget = $20 + (10% of cash)
        
        Args:
            cash_available: Available USD cash (auto-fetch if None)
            
        Returns:
            Trade budget (capped at available cash, floored at min trade)
        """
        if cash_available is None:
            cash_available = self.get_usd_balance()
        
        if cash_available <= 0:
            return 0.0
        
        budget = self.trade_bonus_usd + (self.trade_pct_of_cash * cash_available)
        
        # Cap at available cash
        budget = min(budget, cash_available)
        
        # Floor at minimum trade size
        if budget < self.min_trade_usd:
            if cash_available >= self.min_trade_usd:
                budget = self.min_trade_usd
            else:
                budget = 0.0
        
        return max(0.0, budget)

    # --- Duplicate Prevention ---
    def has_pending_order(self, pair: str, order_type: str = "stop_buy") -> bool:
        """
        Check if there's a pending order for the pair.
        
        Uses position_tracker if available, otherwise returns False.
        """
        if self.position_tracker is None:
            return False
        
        try:
            symbol = pair.replace("/", "").upper()
            return self.position_tracker.has_pending_order(symbol, order_type)
        except Exception:
            return False

    def has_open_position(self, pair: str) -> bool:
        """Check if there's an open position for the pair."""
        if self.position_tracker is None:
            return False
        
        try:
            symbol = pair.replace("/", "").upper()
            return self.position_tracker.has_open_position(symbol)
        except Exception:
            return False

    def can_open_new_position(self, pair: str) -> Tuple[bool, str]:
        """
        Check if we can open a new position for the pair.
        
        Returns:
            Tuple of (can_open, reason)
        """
        # Check for existing position
        if self.has_open_position(pair):
            return False, "already_has_position"
        
        # Check for pending stop-buy
        if self.has_pending_order(pair, "stop_buy"):
            return False, "pending_stop_buy"
        
        # Check for pending stop-sell
        if self.has_pending_order(pair, "stop_sell"):
            return False, "pending_stop_sell"
        
        return True, "ok"

    # --- Account helpers ---
    def get_usd_balance(self) -> float:
        summary = self.balance_fetcher.get_account_summary()
        return summary["cash"]

    def get_all_balances(self) -> Dict[str, float]:
        return self.balance_fetcher.get_all_balances()

    # --- Data loading ---
    def _load_pair_df(self, pair: str, tf: str = "15m") -> pd.DataFrame:
        tag = pair.replace("/", "")
        path = Path(CFG.DATA_DIR) / f"bars_{tag}_{tf}.csv"
        if not path.exists():
            LOG.warning(f"Data file not found: {path}")
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except Exception as e:
            LOG.warning(f"Failed reading {path}: {e}")
            return pd.DataFrame()

    # --- ATR & volatility ---
    def get_atr_data(self, pair: str, period: int = 14, tf: str = "15m") -> Dict[str, float]:
        df = self._load_pair_df(pair, tf)
        if df.empty or len(df) < max(period, 2):
            return {
                "atr_pct": 0.02,
                "current_price": 0.0,
                "atr_absolute": 0.0,
                "volatility_rank": 0.5,
            }

        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        atr_percentage = atr_pct(df, period)
        current_atr_pct = float(atr_percentage.iloc[-1]) if len(atr_percentage) > 0 else 0.02
        current_price = float(df["close"].iloc[-1])
        atr_absolute = current_atr_pct * current_price

        recent_atr = atr_percentage.tail(50)
        if len(recent_atr) > 0:
            mn, mx = float(recent_atr.min()), float(recent_atr.max())
            volatility_rank = (current_atr_pct - mn) / (mx - mn + 1e-12) if mx > mn else 0.5
        else:
            volatility_rank = 0.5

        return {
            "atr_pct": float(current_atr_pct),
            "current_price": float(current_price),
            "atr_absolute": float(atr_absolute),
            "volatility_rank": float(volatility_rank),
        }

    # --- Position sizing ---
    def calculate_position_size_multiplier(self, pair: str, account_balance: Optional[float] = None) -> float:
        if account_balance is None:
            account_balance = self.get_usd_balance()

        atr_data = self.get_atr_data(pair)
        curr_atr_pct = float(atr_data.get("atr_pct", 0.02))

        base_atr = 0.01  # 1% reference
        multiplier = base_atr / (curr_atr_pct + 1e-6)
        multiplier = max(0.2, min(1.0, multiplier))

        vol_rank = float(atr_data.get("volatility_rank", 0.5))
        if vol_rank > 0.8:
            multiplier *= 0.7
        elif vol_rank < 0.3:
            multiplier *= 1.2
        multiplier = min(1.0, multiplier)

        LOG.debug(
            f"{pair}: ATR={curr_atr_pct:.4f}, VolRank={vol_rank:.2f}, Balance=${account_balance:.2f}, Mult={multiplier:.2f}"
        )
        return float(multiplier)

    def calculate_optimal_position_size(
        self,
        pair: str,
        account_balance: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> Dict[str, float]:
        if account_balance is None:
            account_balance = self.get_usd_balance()
        LOG.debug(f"Auto-fetched balance: ${account_balance:.2f}")

        atr_data = self.get_atr_data(pair)
        if entry_price is None:
            entry_price = float(atr_data.get("current_price", 0.0) or 0.0)

        if entry_price <= 0.0 or account_balance <= 0.0:
            return {
                "position_size_quote": 0.0,
                "position_size_base": 0.0,
                "stop_loss_price": 0.0,
                "take_profit_price": 0.0,
                "risk_amount": 0.0,
                "reward_amount": 0.0,
                "risk_reward_ratio": 0.0,
                "atr_pct": float(atr_data.get("atr_pct", 0.0) or 0.0),
                "entry_price": float(entry_price),
                "account_balance": float(account_balance),
            }

        risk_amount = float(account_balance) * self.max_risk_per_trade
        atr_abs = float(atr_data.get("atr_absolute", 0.0) or 0.0)

        stop_distance = atr_abs * self.atr_multiplier_stop
        stop_loss_price = entry_price - stop_distance

        target_distance = atr_abs * self.atr_multiplier_target
        take_profit_price = entry_price + target_distance

        risk_per_unit = max(1e-12, entry_price - stop_loss_price)
        position_size_base = risk_amount / risk_per_unit
        position_size_quote = position_size_base * entry_price

        multiplier = self.calculate_position_size_multiplier(pair, account_balance)
        max_position = float(CFG.MAX_POSITION_USD) * multiplier

        if position_size_quote > max_position:
            position_size_quote = max_position
            position_size_base = position_size_quote / entry_price

        if position_size_quote > account_balance:
            position_size_quote = float(account_balance)
            position_size_base = position_size_quote / entry_price

        if position_size_quote < float(CFG.MIN_NOTIONAL_BUY):
            position_size_quote = 0.0
            position_size_base = 0.0

        reward_amount = max(0.0, (take_profit_price - entry_price) * position_size_base)
        actual_risk = max(1e-12, (entry_price - stop_loss_price) * position_size_base)
        rrr = reward_amount / actual_risk

        return {
            "position_size_quote": float(position_size_quote),
            "position_size_base": float(position_size_base),
            "stop_loss_price": float(stop_loss_price),
            "take_profit_price": float(take_profit_price),
            "risk_amount": float(actual_risk),
            "reward_amount": float(reward_amount),
            "risk_reward_ratio": float(rrr),
            "atr_pct": float(atr_data.get("atr_pct", 0.0) or 0.0),
            "entry_price": float(entry_price),
            "account_balance": float(account_balance),
        }

    # --- Risk scoring ---
    def calculate_risk_score(self, pair: str, timeframe: str = "15m") -> Tuple[float, Dict[str, float]]:
        df = self._load_pair_df(pair, timeframe)
        if df.empty:
            return (0.5, {"error": "No data"})

        if "close" in df.columns:
            df["close"] = pd.to_numeric(df["close"], errors="coerce")

        details: Dict[str, float] = {}
        risk_components = []

        atr_data = self.get_atr_data(pair, tf=timeframe)
        curr_atr_pct = float(atr_data.get("atr_pct", 0.0) or 0.0)
        vol_rank = float(atr_data.get("volatility_rank", 0.5) or 0.5)

        atr_risk = min(1.0, curr_atr_pct / 0.05)
        risk_components.append(atr_risk * 0.4)
        details["atr_risk"] = atr_risk
        details["atr_pct"] = curr_atr_pct

        vol_risk = vol_rank
        risk_components.append(vol_risk * 0.2)
        details["volatility_rank_risk"] = vol_risk

        rsi_series = rsi(df["close"], 14)
        current_rsi = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 50.0
        if current_rsi < 30:
            rsi_risk = (30.0 - current_rsi) / 30.0
        elif current_rsi > 70:
            rsi_risk = (current_rsi - 70.0) / 30.0
        else:
            rsi_risk = 0.0
        risk_components.append(rsi_risk * 0.2)
        details["rsi_risk"] = rsi_risk
        details["rsi"] = current_rsi

        if len(df) >= 11 and "close" in df.columns:
            momentum_10 = float(df["close"].pct_change(10).iloc[-1])
            momentum_risk = min(1.0, abs(momentum_10) / 0.10)
        else:
            momentum_risk = 0.0
        risk_components.append(momentum_risk * 0.2)
        details["momentum_risk"] = momentum_risk

        risk_score = sum(risk_components)
        risk_score = max(0.0, min(1.0, risk_score))
        details["total_risk_score"] = risk_score
        return (risk_score, details)

    # --- Trade evaluation ---
    def evaluate_trade_opportunity(
        self,
        pair: str,
        account_balance: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> Dict[str, float]:
        if account_balance is None:
            account_balance = self.get_usd_balance()
        LOG.debug(f"Auto-fetched balance for trade evaluation: ${account_balance:.2f}")

        risk_score, risk_details = self.calculate_risk_score(pair)
        position_details = self.calculate_optimal_position_size(pair, account_balance, entry_price)

        rrr = float(position_details.get("risk_reward_ratio", 0.0) or 0.0)
        approved = (
            risk_score <= float(CFG.RISK_SCORE_THRESHOLD)
            and rrr >= self.min_risk_reward_ratio
            and float(position_details.get("position_size_quote", 0.0) or 0.0) >= float(CFG.MIN_NOTIONAL_BUY)
            and float(account_balance) >= float(CFG.MIN_NOTIONAL_BUY)
        )
        
        # Small account additional checks
        from config_factory import is_small_account, get_min_cash_for_new_position
        balances = self.balance_fetcher.get_account_summary()
        small = is_small_account(balances)
        if small:
            min_cash_needed = get_min_cash_for_new_position(balances)
            if account_balance < min_cash_needed:
                LOG.info(
                    "[RISK] Small account mode: blocking new position for %s due to low cash "
                    "(cash=%.2f < min_required=%.2f)",
                    pair, account_balance, min_cash_needed,
                )
                approved = False
            # Optionally scale down max_units even more in small mode
            position_details["position_size_quote"] *= 0.5  # half size in small mode
        
        return {
            "approved": bool(approved),
            "risk_score": float(risk_score),
            "risk_details": risk_details,
            "position_size_quote": float(position_details.get("position_size_quote", 0.0) or 0.0),
            "position_size_base": float(position_details.get("position_size_base", 0.0) or 0.0),
            "stop_loss_price": float(position_details.get("stop_loss_price", 0.0) or 0.0),
            "take_profit_price": float(position_details.get("take_profit_price", 0.0) or 0.0),
            "risk_reward_ratio": float(rrr),
            "risk_amount": float(position_details.get("risk_amount", 0.0) or 0.0),
            "reward_amount": float(position_details.get("reward_amount", 0.0) or 0.0),
            "entry_price": float(position_details.get("entry_price", 0.0) or 0.0),
            "atr_pct": float(position_details.get("atr_pct", 0.0) or 0.0),
            "account_balance": float(account_balance),
        }

    # --- Stop/take-profit helpers ---
    def get_stop_loss_for_position(self, pair: str, entry_price: float) -> float:
        atr_abs = float(self.get_atr_data(pair).get("atr_absolute", 0.0) or 0.0)
        return float(entry_price - (atr_abs * self.atr_multiplier_stop))

    def get_take_profit_for_position(self, pair: str, entry_price: float) -> float:
        atr_abs = float(self.get_atr_data(pair).get("atr_absolute", 0.0) or 0.0)
        return float(entry_price + (atr_abs * self.atr_multiplier_target))

    # --- Stop-loss decision ---
    def should_trigger_stop_loss(
        self,
        pair: str,
        entry_price: float,
        current_price: float,
        position_size_base: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, float]]:
        """
        Check if ATR-based stop-loss should be triggered.
        
        Stop-loss is set at 2x ATR below entry price.
        Returns detailed information about the stop-loss status.
        """
        atr_data = self.get_atr_data(pair)
        atr_abs = float(atr_data.get("atr_absolute", 0.0) or 0.0)
        atr_pct = float(atr_data.get("atr_pct", 0.0) or 0.0)
        
        # Calculate stop-loss price (2x ATR below entry)
        stop_loss_price = float(entry_price - (atr_abs * self.atr_multiplier_stop))
        
        # Calculate metrics
        loss_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
        distance_to_stop = current_price - stop_loss_price
        distance_to_stop_pct = (distance_to_stop / current_price) if current_price > 0 else 0.0
        
        # Check if stop-loss triggered
        stop_triggered = current_price <= stop_loss_price
        
        # Calculate loss amount
        loss_amount = 0.0
        if position_size_base and position_size_base > 0:
            loss_amount = max(0.0, (entry_price - current_price) * position_size_base)
        
        # Get account info
        account_balance = self.get_usd_balance()
        exposure_pct = 0.0
        if position_size_base and account_balance > 0:
            position_value = current_price * position_size_base
            exposure_pct = position_value / account_balance
        
        # Professional logging
        if stop_triggered:
            # STOP-LOSS TRIGGERED - RED ALERT
            LOG.error(
                f"\n{'='*80}\n"
                f"?? [STOP LOSS TRIGGERED] {pair}\n"
                f"{'='*80}\n"
                f"Entry Price:           ${entry_price:,.2f}\n"
                f"Current Price:         ${current_price:,.2f}\n"
                f"Stop-Loss Price:       ${stop_loss_price:,.2f}\n"
                f"ATR (Absolute):        ${atr_abs:,.4f} ({atr_pct*100:.2f}%)\n"
                f"ATR Multiplier:        {self.atr_multiplier_stop}x\n"
                f"Loss:                  {loss_pct*100:+.2f}%\n"
                f"Loss Amount:           ${loss_amount:,.2f}\n"
                f"Position Size:         {position_size_base:.8f} {pair.split('/')[0]}\n"
                f"Position Value:        ${current_price * (position_size_base or 0):,.2f}\n"
                f"Account Balance:       ${account_balance:,.2f}\n"
                f"Portfolio Exposure:    {exposure_pct*100:.1f}%\n"
                f"{'='*80}\n"
                f"ACTION: Close position immediately at market price\n"
                f"{'='*80}\n"
            )
        else:
            # Stop-loss monitoring - professional status update
            safety_margin_pct = (distance_to_stop / atr_abs) if atr_abs > 0 else 0.0
            
            LOG.info(
                f"[STOP-LOSS MONITOR] {pair}: "
                f"Entry=${entry_price:,.2f} | Current=${current_price:,.2f} | "
                f"Stop=${stop_loss_price:,.2f} | "
                f"Distance={distance_to_stop_pct*100:+.1f}% | "
                f"Loss={loss_pct*100:+.2f}% | "
                f"Status={'?? SAFE' if distance_to_stop > 0 else '?? BREACHED'}"
            )
        
        # Build details dictionary
        details = {
            "pair": pair,
            "entry_price": float(entry_price),
            "current_price": float(current_price),
            "stop_loss_price": float(stop_loss_price),
            "atr_absolute": float(atr_abs),
            "atr_pct": float(atr_pct),
            "atr_multiplier": float(self.atr_multiplier_stop),
            "loss_pct": float(loss_pct),
            "loss_amount": float(loss_amount),
            "stop_triggered": bool(stop_triggered),
            "account_balance": float(account_balance),
            "exposure_pct": float(exposure_pct),
            "distance_to_stop_pct": float(distance_to_stop_pct),
            "distance_to_stop": float(distance_to_stop),
            "stop_type": "atr_based",
            "reason": "atr_stop_loss_triggered" if stop_triggered else "atr_stop_loss_active",
        }
        
        # Optional narrative voice
        if stop_triggered:
            voice = _get_voice()
            if voice:
                try:
                    voice.emit(
                        module="RiskManager",
                        tags=["stoploss", "atr", "position_closed"],
                        values={
                            "pair": pair,
                            "entry_price": entry_price,
                            "current_price": current_price,
                            "stop_loss_price": stop_loss_price,
                            "loss_pct": loss_pct,
                            "loss_amount": loss_amount,
                            "atr": atr_abs,
                        },
                    )
                except Exception:
                    pass
        
        return (stop_triggered, details)

    # --- Trailing stop ---
    def calculate_trailing_stop(
        self,
        pair: str,
        entry_price: float,
        current_price: float,
        peak_price: float,
    ) -> Dict[str, float]:
        """
        Calculate trailing stop based on ATR.
        
        Uses 2x ATR as initial stop, then trails at 1.5x ATR from peak price.
        Returns stop price and trigger status with professional logging.
        """
        atr_data = self.get_atr_data(pair)
        atr_abs = float(atr_data.get("atr_absolute", 0.0) or 0.0)
        atr_pct = float(atr_data.get("atr_pct", 0.0) or 0.0)
        
        # Calculate initial stop (2x ATR below entry)
        initial_stop = float(entry_price - (atr_abs * self.atr_multiplier_stop))
        
        # Calculate trailing stop (1.5x ATR below peak)
        trailing_multiplier = float(self.atr_multiplier_stop * 0.75)
        trailing_stop = float(peak_price - (atr_abs * trailing_multiplier))
        
        # Use maximum of initial and trailing (most restrictive)
        final_stop = max(initial_stop, trailing_stop)
        
        # Check if triggered
        stop_triggered = current_price <= final_stop
        
        # Calculate metrics
        profit_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
        drawdown_from_peak_pct = (current_price - peak_price) / peak_price if peak_price > 0 else 0.0
        distance_to_stop = current_price - final_stop
        distance_to_stop_pct = (distance_to_stop / current_price) if current_price > 0 else 0.0
        
        # Professional logging
        if stop_triggered:
            # TRAILING STOP TRIGGERED
            LOG.error(
                f"\n{'='*80}\n"
                f"[TRAILING STOP TRIGGERED] {pair}\n"
                f"{'='*80}\n"
                f"Entry Price:           ${entry_price:,.2f}\n"
                f"Peak Price:            ${peak_price:,.2f}\n"
                f"Current Price:         ${current_price:,.2f}\n"
                f"Trailing Stop Price:   ${final_stop:,.2f}\n"
                f"Profit from Entry:     {profit_pct*100:+.2f}%\n"
                f"Drawdown from Peak:    {drawdown_from_peak_pct*100:+.2f}%\n"
                f"ATR (Absolute):        ${atr_abs:,.4f} ({atr_pct*100:.2f}%)\n"
                f"Using Trailing Stop:   {'YES' if trailing_stop > initial_stop else 'NO (Initial Stop)'}\n"
                f"{'='*80}\n"
                f"ACTION: Close position to lock in profits\n"
                f"{'='*80}\n"
            )
        else:
            # Trailing stop monitoring
            is_trailing = trailing_stop > initial_stop
            LOG.info(
                f"[TRAILING STOP MONITOR] {pair}: "
                f"Entry=${entry_price:,.2f} | Peak=${peak_price:,.2f} | "
                f"Current=${current_price:,.2f} | Stop=${final_stop:,.2f} | "
                f"Profit={profit_pct*100:+.2f}% | Drawdown={drawdown_from_peak_pct*100:+.2f}% | "
                f"Mode={'TRAILING' if is_trailing else 'INITIAL'} | "
                f"Status={'SAFE' if distance_to_stop > 0 else 'AT RISK'}"
            )
        
        # Build details dictionary
        details = {
            "trailing_stop_price": float(final_stop),
            "initial_stop_price": float(initial_stop),
            "trailing_stop_price_calc": float(trailing_stop),
            "peak_price": float(peak_price),
            "current_price": float(current_price),
            "entry_price": float(entry_price),
            "stop_triggered": bool(stop_triggered),
            "profit_pct": float(profit_pct),
            "drawdown_from_peak_pct": float(drawdown_from_peak_pct),
            "using_trailing": bool(trailing_stop > initial_stop),
            "atr_absolute": float(atr_abs),
            "atr_pct": float(atr_pct),
            "atr_multiplier_initial": float(self.atr_multiplier_stop),
            "atr_multiplier_trailing": float(trailing_multiplier),
            "distance_to_stop": float(distance_to_stop),
            "distance_to_stop_pct": float(distance_to_stop_pct),
            "stop_type": "atr_trailing_stop",
            "reason": "trailing_stop_triggered" if stop_triggered else "trailing_stop_active",
        }
        
        return details

    # --- Portfolio risk ---
    def assess_portfolio_risk(self) -> Dict:
        summary = self.balance_fetcher.get_account_summary()
        usd_balance = summary["cash"]
        total_portfolio_value = summary["equity"]

        # No manual portfolio math: do not value positions, trust broker equity
        total_crypto_value = 0.0
        position_exposures: Dict[str, float] = {}
        position_risks: Dict[str, float] = {}

        # Since no position valuing, exposure_pcts empty
        exposure_pcts: Dict[str, float] = {}

        max_exposure_pair = None
        max_exposure_pct = 0.0

        weighted_risk = 0.0  # no positions, no risk

        risk_flags = []
        if total_portfolio_value > 0 and usd_balance / total_portfolio_value < 0.10:
            risk_flags.append(f"Low cash reserve: ${usd_balance:.2f}")

        allow_new_trades = usd_balance >= float(CFG.MIN_NOTIONAL_BUY)

        return {
            "total_portfolio_value": float(total_portfolio_value),
            "usd_balance": float(usd_balance),
            "total_crypto_value": float(total_crypto_value),
            "crypto_exposure_pct": 0.0,  # no manual math
            "num_positions": 0,  # not assessing positions
            "position_exposures": position_exposures,
            "exposure_pcts": exposure_pcts,
            "position_risks": position_risks,
            "max_exposure_pair": max_exposure_pair,
            "max_exposure_pct": float(max_exposure_pct),
            "weighted_portfolio_risk": float(weighted_risk),
            "risk_flags": risk_flags,
            "allow_new_trades": bool(allow_new_trades),
            "available_for_trades": float(usd_balance),
        }

    # --- Position reduction ---
    def should_reduce_position(
        self,
        pair: str,
        entry_price: float,
        current_price: float,
        position_size_base: float,
    ) -> Tuple[bool, Dict[str, float]]:
        loss_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
        position_value = current_price * position_size_base

        portfolio_risk = self.assess_portfolio_risk()

        triggers = []
        should_reduce = False

        stop_loss_details = self.should_trigger_stop_loss(pair, entry_price, current_price, position_size_base)[1]
        if loss_pct < 0:
            stop_distance_pct = abs((entry_price - stop_loss_details["stop_loss_price"]) / entry_price) if entry_price > 0 else 0.0
            loss_ratio = abs(loss_pct) / stop_distance_pct if stop_distance_pct > 0 else 0.0
            if loss_ratio > 0.5:
                triggers.append(f"Loss {loss_pct*100:.1f}% (>{loss_ratio*100:.0f}% to stop)")
                should_reduce = True

        if float(portfolio_risk.get("max_exposure_pct", 0.0)) > 0.50:
            triggers.append(f"High portfolio concentration: {portfolio_risk['max_exposure_pct']*100:.0f}%")
            should_reduce = True

        atr_data = self.get_atr_data(pair)
        if float(atr_data.get("volatility_rank", 0.0)) > 0.90:
            triggers.append(f"Volatility spike: {atr_data['volatility_rank']*100:.0f}th percentile")
            should_reduce = True

        reduce_pct = 0.0
        if should_reduce:
            num_triggers = len(triggers)
            reduce_pct = min(0.50, 0.20 + (num_triggers * 0.10))

        return (
            should_reduce,
            {
                "should_reduce": bool(should_reduce),
                "triggers": triggers,
                "recommended_reduction_pct": float(reduce_pct),
                "current_loss_pct": float(loss_pct),
                "position_value": float(position_value),
                "portfolio_exposure": float(portfolio_risk.get("exposure_pcts", {}).get(pair, 0.0)),
                "volatility_rank": float(atr_data.get("volatility_rank", 0.0)),
            },
        )


# --- Module-level singletons and wrappers ---
_risk_manager: Optional[RiskManager] = None


def get_risk_manager(balance_fetcher: Optional[BalanceFetcher] = None, position_tracker=None) -> RiskManager:
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager(balance_fetcher, position_tracker)
    return _risk_manager


def calculate_position_size_multiplier(pair: str, account_balance: Optional[float] = None) -> float:
    rm = get_risk_manager()
    return rm.calculate_position_size_multiplier(pair, account_balance)


def evaluate_trade(pair: str, account_balance: Optional[float] = None, entry_price: Optional[float] = None) -> Dict[str, float]:
    rm = get_risk_manager()
    return rm.evaluate_trade_opportunity(pair, account_balance, entry_price)


def get_position_sizing(pair: str, account_balance: Optional[float] = None, entry_price: Optional[float] = None) -> Dict[str, float]:
    rm = get_risk_manager()
    return rm.calculate_optimal_position_size(pair, account_balance, entry_price)


def check_stop_loss(pair: str, entry_price: float, current_price: float, position_size_base: Optional[float] = None) -> Tuple[bool, Dict[str, float]]:
    rm = get_risk_manager()
    return rm.should_trigger_stop_loss(pair, entry_price, current_price, position_size_base)


def check_trailing_stop(pair: str, entry_price: float, current_price: float, peak_price: float) -> Dict[str, float]:
    rm = get_risk_manager()
    return rm.calculate_trailing_stop(pair, entry_price, current_price, peak_price)


def check_position_reduction(pair: str, entry_price: float, current_price: float, position_size_base: float) -> Tuple[bool, Dict[str, float]]:
    rm = get_risk_manager()
    return rm.should_reduce_position(pair, entry_price, current_price, position_size_base)


def get_portfolio_risk_assessment() -> Dict:
    rm = get_risk_manager()
    return rm.assess_portfolio_risk()


def compute_trade_budget(cash_available: float = None) -> float:
    """Compute trade budget using NK Trading Amount Rule: $20 + 10% of cash."""
    rm = get_risk_manager()
    return rm.compute_trade_budget(cash_available)


def can_open_position(pair: str) -> Tuple[bool, str]:
    """Check if we can open a new position (duplicate prevention)."""
    rm = get_risk_manager()
    return rm.can_open_new_position(pair)


# =============================================================================
# PROFESSIONAL DASHBOARD
# =============================================================================

def run_risk_manager_dashboard(pairs: List[str] = None) -> Dict:
    """
    Run the risk manager dashboard with professional output.
    
    Shows:
    - Account status
    - NK Trading Amount Rule calculation
    - Risk analysis per pair
    - Portfolio risk assessment
    
    Args:
        pairs: List of pairs to analyze (default: from config)
        
    Returns:
        Dict with all analysis results
    """
    now = datetime.now().strftime("%H:%M:%S")
    
    # Get pairs from config if not provided
    if pairs is None:
        pairs = getattr(CFG, "PAIRS", ["BTC/USD", "ETH/USD", "SOL/USD"])
    
    rm = get_risk_manager()
    
    # Header
    _log_header(f"RISK MANAGER DASHBOARD ({now})")
    
    # Account info
    summary = rm.balance_fetcher.get_account_summary()
    cash = summary.get("cash", 0)
    equity = summary.get("equity", 0)
    
    _log_info("Account Equity", _color_amount(equity))
    _log_info("Available Cash", _color_amount(cash))
    
    # NK Trading Amount Rule
    trade_budget = rm.compute_trade_budget(cash)
    _log_subheader("NK TRADING AMOUNT RULE")
    _log_info("Formula", f"${TRADE_BONUS_USD:.0f} + ({TRADE_PCT_OF_CASH*100:.0f}% x ${cash:.2f})")
    _log_info("Trade Budget", _color_amount(trade_budget))
    _log_info("Min Trade Size", f"${MIN_TRADE_USD:.2f}")
    _log_info("Portfolio Cap", f"{PORTFOLIO_CAP_PCT*100:.0f}%")
    
    # Risk analysis per pair
    _log_subheader(f"RISK ANALYSIS ({len(pairs)} PAIRS)")
    
    LOG.info("")
    LOG.info(f"    {'Pair':<12} {'Risk':<8} {'ATR %':<10} {'RSI':<8} {'Stop Loss':<12} {'Take Profit':<12} {'R:R':<8} {'Approved':<10}")
    LOG.info("    " + "-" * 90)
    
    results = []
    approved_count = 0
    
    for pair in pairs:
        try:
            eval_result = rm.evaluate_trade_opportunity(pair)
            
            risk_score = eval_result.get('risk_score', 0)
            atr_pct_val = eval_result.get('atr_pct', 0) * 100
            risk_details = eval_result.get('risk_details', {})
            rsi_val = risk_details.get('rsi', 0)
            stop_loss = eval_result.get('stop_loss_price', 0)
            take_profit = eval_result.get('take_profit_price', 0)
            rrr = eval_result.get('risk_reward_ratio', 0)
            approved = eval_result.get('approved', False)
            
            if approved:
                approved_count += 1
                status = _c(Fore.GREEN, "YES")
            else:
                status = _c(Fore.RED, "NO")
            
            # Risk score coloring
            if risk_score < 0.15:
                risk_str = _c(Fore.GREEN, f"{risk_score:.3f}")
            elif risk_score < 0.25:
                risk_str = _c(Fore.YELLOW, f"{risk_score:.3f}")
            else:
                risk_str = _c(Fore.RED, f"{risk_score:.3f}")
            
            LOG.info(f"    {pair:<12} {risk_str:<16} {atr_pct_val:<10.2f} {rsi_val:<8.1f} ${stop_loss:<11.2f} ${take_profit:<11.2f} {rrr:<8.2f} {status:<18}")
            
            results.append(eval_result)
            
        except Exception as e:
            LOG.error(f"    {pair:<12} ERROR: {e}")
    
    LOG.info("    " + "-" * 90)
    LOG.info("")
    
    # Portfolio risk
    _log_subheader("PORTFOLIO RISK ASSESSMENT")
    
    try:
        portfolio_risk = rm.assess_portfolio_risk()
        
        _log_info("Total Positions", portfolio_risk.get('total_positions', 0))
        _log_info("Total Exposure", _color_amount(portfolio_risk.get('total_exposure_usd', 0)))
        _log_info("Exposure %", f"{portfolio_risk.get('exposure_pct', 0) * 100:.1f}%")
        _log_info("Avg Risk Score", f"{portfolio_risk.get('avg_risk_score', 0):.3f}")
        _log_info("Max Risk Score", f"{portfolio_risk.get('max_risk_score', 0):.3f}")
        _log_info("Portfolio At Risk", _color_amount(portfolio_risk.get('portfolio_risk_usd', 0)))
        _log_info("Allow New Trades", _c(Fore.GREEN, "YES") if portfolio_risk.get('allow_new_trades') else _c(Fore.RED, "NO"))
        
    except Exception as e:
        _log_error(f"Portfolio assessment failed: {e}")
    
    # Summary
    _log_subheader("SUMMARY")
    _log_info("Pairs Analyzed", len(pairs))
    _log_info("Approved for Trading", f"{approved_count} " + (_c(Fore.GREEN, "[READY]") if approved_count > 0 else ""))
    _log_info("Rejected", f"{len(pairs) - approved_count}")
    
    LOG.info("=" * 80)
    
    return {
        "cash": cash,
        "equity": equity,
        "trade_budget": trade_budget,
        "pairs_analyzed": len(pairs),
        "approved_count": approved_count,
        "results": results,
    }


# =============================================================================
# STANDALONE EXECUTION
# =============================================================================

if __name__ == "__main__":
    """
    Run risk manager dashboard as a standalone script.
    
    Usage:
        python risk_manager.py
    """
    import sys
    import logging
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )
    
    print("\n" + "=" * 60)
    print("  RISK MANAGER - DASHBOARD MODE")
    print("=" * 60 + "\n")
    
    # Get pairs from config
    pairs = getattr(CFG, "PAIRS", ["BTC/USD", "ETH/USD", "SOL/USD"])
    
    results = run_risk_manager_dashboard(pairs)
    
    print("\n" + "=" * 60)
    print(f"  Dashboard complete. {results['approved_count']}/{results['pairs_analyzed']} pairs approved.")
    print(f"  Trade Budget: ${results['trade_budget']:.2f} ($20 + 10% rule)")
    print("=" * 60 + "\n")