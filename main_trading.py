#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_trading.py - Trading evaluation pass for the Kraken Trading Bot

This module contains the main trading evaluation function.
Trades execute immediately when Vortex strategy signals (probe sensor removed).

Includes A3/C3 Gated Trailing Stop System:
- C3 (Sprinter): Tight trailing stops, probes for direction
- A3 (Marathoner): Takes over when commitment proven, widens stops as profit grows
"""

import os
import time
from logging import getLogger
from typing import Dict, Optional

from config_factory import CFG
from tick_budget import check_budget
from decision_report import append_signal
from market_status import should_allow_trading
from buy_manager import should_buy, record_stop_loss_exit
from atr_trailing_stop import evaluate_all_positions_for_trailing_stop, reset_peak_price
from open_orders_utils import audit_open_orders

from main_helpers import (
    rotated_pairs, in_buy_cooldown, mark_bought,
    normalize_asset_symbol, get_current_price,
    has_open_stop_buy
)

# A3/C3 Gated Trailing Stop System
try:
    from sandbox.a3_c3_relay.relay_config import RelayConfig
    from sandbox.a3_c3_relay.flag_state_manager import FlagStateManager, ScriptOwner
    from sandbox.a3_c3_relay.a3_gated_trailing import A3GatedTrailing
    from sandbox.a3_c3_relay.c3_simple_trailing import C3SimpleTrailing
    A3C3_AVAILABLE = True
except ImportError as e:
    A3C3_AVAILABLE = False
    RelayConfig = None  # Type stub for when import fails
    FlagStateManager = None
    A3GatedTrailing = None
    C3SimpleTrailing = None
    LOGGER_IMPORT = getLogger("main")
    LOGGER_IMPORT.debug(f"A3/C3 gated trailing not available: {e}")

LOGGER = getLogger("main")

# Anti-bias settings
MAX_BUYS_PER_TICK = int(os.getenv("MAX_BUYS_PER_TICK", "1"))

# A3/C3 Gated Trailing Stop - Global State
# These persist across evaluation passes to track peak prices and gate states
_a3c3_config = None
_a3c3_flags = None
_a3c3_a3 = None
_a3c3_c3 = None
_a3c3_initialized: bool = False


def _init_a3c3_system(pairs: list) -> bool:
    """Initialize the A3/C3 gated trailing stop system."""
    global _a3c3_config, _a3c3_flags, _a3c3_a3, _a3c3_c3, _a3c3_initialized

    if not A3C3_AVAILABLE:
        LOGGER.debug("[A3C3] System not available (import failed)")
        return False

    if _a3c3_initialized:
        return True

    try:
        # Create config with percentage-based gates for crypto
        _a3c3_config = RelayConfig(
            # Gate thresholds (percentage based)
            gate_1_activation_pct=float(os.getenv("A3C3_GATE1_PCT", "0.5")),   # 0.5% to arm
            gate_1_trail_pct=float(os.getenv("A3C3_GATE1_TRAIL", "0.2")),      # 0.2% trail
            gate_2_activation_pct=float(os.getenv("A3C3_GATE2_PCT", "1.0")),   # 1.0% 
            gate_2_trail_pct=float(os.getenv("A3C3_GATE2_TRAIL", "0.4")),      # 0.4% trail
            gate_3_activation_pct=float(os.getenv("A3C3_GATE3_PCT", "2.0")),   # 2.0%
            gate_3_trail_pct=float(os.getenv("A3C3_GATE3_TRAIL", "1.2")),      # 1.2% trail
            # C3 tight trailing
            c3_trail_pct=float(os.getenv("A3C3_C3_TRAIL_PCT", "0.3")),         # 0.3% tight trail
            c3_min_trail_floor_pct=float(os.getenv("A3C3_C3_FLOOR_PCT", "0.15")),
            # Timing
            cooldown_ticks=int(os.getenv("A3C3_COOLDOWN_TICKS", "3")),
            tick_interval_seconds=10.0,
            # Use percentage mode for crypto
            use_percentage_mode=True,
            use_shared_pairs=True,
            shared_pairs=pairs,
        )

        # Initialize components
        _a3c3_flags = FlagStateManager(_a3c3_config, pairs)
        _a3c3_a3 = A3GatedTrailing(_a3c3_config, _a3c3_flags)
        _a3c3_c3 = C3SimpleTrailing(_a3c3_config, _a3c3_flags)

        _a3c3_initialized = True
        LOGGER.info("[A3C3] Gated trailing stop system initialized | pairs=%d | gates=[%.1f%%, %.1f%%, %.1f%%]",
                   len(pairs), 
                   _a3c3_config.gate_1_activation_pct,
                   _a3c3_config.gate_2_activation_pct,
                   _a3c3_config.gate_3_activation_pct)
        return True

    except Exception as e:
        LOGGER.error("[A3C3] Initialization failed: %s", e)
        return False

# Color support
try:
    from colorama import Fore, Style
    COLOR_AVAILABLE = True
except ImportError:
    COLOR_AVAILABLE = False
    Fore = type('Fore', (), {'RED': '', 'GREEN': '', 'YELLOW': ''})()
    Style = type('Style', (), {'RESET_ALL': ''})()


def run_trading_evaluation_pass(
    exe,          # OrderExecutor
    pt,           # PositionTracker
    sell_mgr,     # SellManagerV3
    bf,           # BalanceFetcher
    risk_mgr,     # RiskManager
    mem_store=None,    # MemoryStore
    mem_aggr=None,     # MemoryAggregator
    history_tracker=None,  # OrderHistoryTracker
    fee_fetcher=None,  # FeeFetcher
) -> None:
    """Evaluation pass: refresh ledger, log signals, place buys, evaluate sells."""
    LOGGER.info("[TRADE] Evaluation pass start")

    if not should_allow_trading():
        LOGGER.info("[TRADE] Trading not allowed by market status")
        try:
            append_signal("main", "tick", "trading_blocked", {"reason": "market_status"})
        except Exception:
            pass
        return

    # Refresh ledger
    try:
        sell_mgr.refresh_ledger()
    except Exception as e:
        LOGGER.warning(f"[TRADE] Ledger refresh: {e}")

    # Get account summary
    try:
        bf.get_all_balances(use_cache=False)
        summary = bf.get_account_summary()
        portfolio_value = summary["equity"]
        quote_cash = summary["cash"]
        LOGGER.info(
            "Balance fetch complete. Portfolio value: $%.2f, Unrealized P&L: $%.2f, Realized P&L: $%.2f",
            summary["equity"], summary["unrealized_pl"], summary["realized_pl"],
        )
        from account_balance_fetcher_kraken import log_account_summary
        log_account_summary(summary)
        
        from config_factory import is_small_account
        account_mode = "SMALL" if is_small_account(summary) else "NORMAL"
        LOGGER.info("[health] Account mode: %s | equity=%.2f | cash=%.2f",
                   account_mode, summary["equity"], summary["cash"])
    except Exception as e:
        LOGGER.error(f"[TRADE] Account summary fetch failed: {e}")
        summary = {"equity": 0.0, "cash": 0.0, "unrealized_pl": 0.0, "realized_pl": 0.0}
        portfolio_value = 0.0
        quote_cash = 0.0

    # Refresh balance
    try:
        balances = bf.get_all_balances()
        quote_cash = float(balances.get(CFG.QUOTE, 0.0))
    except Exception as e:
        LOGGER.warning(f"[TRADE] Balance refresh failed: {e}")

    # Get positions
    positions = sell_mgr.get_all_positions()
    tracked_positions = positions.get("tracked", [])
    tracked_assets = {
        normalize_asset_symbol(pos.get("asset", ""))
        for pos in tracked_positions
        if isinstance(pos, dict)
    }

    # === SELL EVALUATION ===
    sell_count = 0
    _evaluate_sells(sell_mgr, tracked_positions, exe, sell_count)

    # === A3/C3 GATED TRAILING STOP ===
    use_gated_trailing = str(os.getenv("USE_GATED_TRAILING", "true")).lower() in ("true", "1", "yes")
    if use_gated_trailing and A3C3_AVAILABLE:
        sell_count = _evaluate_gated_trailing(sell_mgr, tracked_positions, exe, sell_count)

    # === ATR TRAILING STOP (legacy, disabled by default) ===
    use_atr_trailing = str(getattr(CFG, "USE_ATR_TRAILING", "false")).lower() in ("true", "1", "yes")
    if use_atr_trailing:
        _evaluate_atr_trailing(bf, sell_mgr, sell_count)

    # === BUY EVALUATION ===
    buy_count = 0
    if quote_cash > 0:
        buy_count = _evaluate_buys(
            exe, bf, sell_mgr, risk_mgr, fee_fetcher,
            quote_cash, summary, tracked_positions, tracked_assets
        )

    # Health summary for small accounts
    from config_factory import is_small_account
    if is_small_account(summary) and buy_count == 0:
        LOGGER.info(
            "[health] Small account mode note: No buys placed this cycle. "
            "Likely reasons: low cash (%.2f), max positions reached, or tight risk gates.",
            summary["cash"],
        )

    # Log open orders
    _log_open_orders(exe)

    # Portfolio risk assessment
    try:
        risk = risk_mgr.get_portfolio_risk_assessment() if hasattr(risk_mgr, 'get_portfolio_risk_assessment') else {}
        if isinstance(risk, dict) and risk:
            append_signal("risk_manager", "tick", "risk_assessment", risk)
    except Exception:
        pass


def _evaluate_sells(sell_mgr, tracked_positions, exe, sell_count):
    """
    Evaluate sell signals for tracked positions using MONEY MAKER gated trailing.

    Uses the Vortex Strategy's gated system:
    - Gate 1: +2% → 0.5% trail
    - Gate 2: +3% → 0.7% trail
    - Gate 3: +4% → 1.0% trail
    - Gate 4: +5% → 1.25% trail
    - NO hard stop (trust the entry)
    """
    # Import vortex strategy for Money Maker gated trailing
    try:
        from live_vortex_strategy import get_vortex_strategy
        vortex = get_vortex_strategy()
        use_vortex_gates = True
    except ImportError:
        vortex = None
        use_vortex_gates = False

    for pos in tracked_positions:
        if not check_budget("sell_evaluation"):
            break
        if not isinstance(pos, dict):
            continue

        asset = pos.get("asset", "")
        pair = pos.get("pn") or f"{asset}/{CFG.QUOTE}"
        price = get_current_price(pair)
        if price <= 0:
            continue

        # Get entry price
        entry_price = sell_mgr.get_entry_price(pair)
        if not entry_price or entry_price <= 0:
            LOGGER.warning(f"[SELL] {pair}: No entry price - skipping gated trailing")
            continue

        try:
            # === MONEY MAKER GATED TRAILING (via Vortex Strategy) ===
            if use_vortex_gates and vortex:
                qty = pos.get("qty", 0) or pos.get("value", 0) / price if price > 0 else 0

                # Get entry time from position tracker if available
                entry_time = None
                try:
                    from position_tracker_kraken import PositionTracker
                    pt = PositionTracker(sell_mgr.order_executor)
                    pos_info = pt.get_position_info(pair)
                    if pos_info and pos_info.get("entry_time"):
                        entry_time = pos_info["entry_time"]
                except Exception:
                    pass

                # Get peak price from historical data if available
                peak_price = None
                try:
                    from pathlib import Path
                    csv_path = Path(CFG.DATA_DIR) / f"bars_{pair.replace('/', '')}_15m.csv"
                    if csv_path.exists():
                        import pandas as pd
                        df = pd.read_csv(csv_path)
                        if len(df) > 0 and 'high' in df.columns:
                            # Get max high since entry (approximation)
                            peak_price = float(df['high'].tail(100).max())
                except Exception:
                    pass

                result = vortex.sync_position_from_kraken(
                    pair=pair,
                    entry_price=entry_price,
                    current_price=price,
                    qty=qty,
                    entry_time=entry_time,
                    peak_price=peak_price,
                )

                gate = result.get("gate", 0)
                stop_price = result.get("stop_price", 0)
                profit_pct = result.get("profit_pct", 0)

                # Log gate status
                if gate > 0:
                    LOGGER.info(f"[GATE] {pair}: Gate {gate} active | "
                               f"profit={profit_pct:+.1f}% | stop=${stop_price:.2f} | price=${price:.2f}")

                # Check for sell signal from gated trailing
                if result.get("should_sell"):
                    triggered = True
                    details = {
                        "reason": result.get("reason", "gate_trail"),
                        "profit_pct": profit_pct / 100,  # Convert back to decimal
                        "entry_price": entry_price,
                        "current_price": price,
                        "gate": gate,
                        "stop_price": stop_price,
                    }
                else:
                    triggered = False
                    details = {
                        "reason": "below_gate_threshold" if gate == 0 else "gate_hold",
                        "profit_pct": profit_pct / 100,
                        "entry_price": entry_price,
                        "current_price": price,
                        "gate": gate,
                        "stop_price": stop_price,
                    }
            else:
                # Fallback to legacy sell manager
                triggered, details = sell_mgr.should_sell(pair, price, use_neural=True)

            # Log stop loss evaluations more clearly
            entry_price = details.get("entry_price")
            reason = details.get("reason", "unknown")

            if entry_price and entry_price > 0:
                profit_pct = (price - entry_price) / entry_price

                # Log evaluation at various loss levels
                if profit_pct <= -0.03:
                    LOGGER.info(f"[LOSS_CHECK] {pair}: profit={profit_pct:.2%} | entry=${entry_price:.2f} | current=${price:.2f} | reason={reason}")

                # Deep loss check (backup for any missed stops)
                if profit_pct <= -0.10:
                    LOGGER.info(f"[DEEP_LOSS] Triggering sell for {pair}: profit_pct={profit_pct:.2%}")
                    triggered = True
                    details["reason"] = "deep_loss_cut"
                    details["profit_pct"] = profit_pct
            else:
                # No entry price - log this as it prevents stop losses
                LOGGER.warning(f"[STOP_LOSS] {pair}: No entry price found - stop loss disabled!")

            # Log evaluation
            try:
                append_signal("sell_manager", "15m", "sell_evaluation", {
                    "pair": pair,
                    "reason": details.get("reason", "n/a"),
                    "profit_pct": details.get("profit_pct"),
                    "entry_price": details.get("entry_price"),
                    "current_price": price,
                })
            except Exception:
                pass

            if triggered:
                LOGGER.info(f"[TRADE] SELL signal {pair} | reason={details.get('reason', 'n/a')}")
                try:
                    qty = sell_mgr.calculate_sell_amount(pair)
                    cost_basis = sell_mgr.get_entry_price(pair)
                    if qty > 0:
                        result = sell_mgr.execute_sell(
                            pair=pair, qty=qty, cost_basis=cost_basis,
                            reason=details.get('reason', 'signal')
                        )
                        LOGGER.info(f"[TRADE] SELL EXECUTED {pair}: {result}")

                        # Remove position from Vortex tracking after sell
                        if use_vortex_gates and vortex:
                            vortex.remove_position(pair)

                        # Record stop loss exit for buy manager (include gate trails)
                        stop_loss_reasons = ("stop_loss", "trailing_stop_triggered", "hard_stop_loss", 
                                           "soft_stop_loss_age", "deep_loss_cut", 
                                           "gate_1_trail", "gate_2_trail", "gate_3_trail", "gate_4_trail")
                        if details.get("reason") in stop_loss_reasons:
                            try:
                                exit_price = exe._get_current_price(pair, side="bid") or 0.0
                                if exit_price > 0:
                                    threshold = record_stop_loss_exit(pair, exit_price)
                                    LOGGER.info(f"[TRADE] Buy Stop set for {pair}: threshold=${threshold:.6f}")
                            except Exception as e:
                                LOGGER.debug(f"[TRADE] Buy Stop set error for {pair}: {e}")
                    else:
                        LOGGER.warning(f"[TRADE] SELL SKIPPED {pair}: qty=0")
                except Exception as e:
                    LOGGER.error(f"[TRADE] SELL FAILED {pair}: {e}")
                
                sell_count += 1
                
        except Exception as e:
            LOGGER.error(f"[TRADE] Sell eval error {pair}: {e}")


def _evaluate_atr_trailing(bf, sell_mgr, sell_count):
    """Evaluate ATR trailing stops."""
    try:
        LOGGER.info("[ATR_TRAILING] Evaluating all positions for ATR trailing stop...")
        atr_multiplier = float(getattr(CFG, "ATR_TRAILING_MULTIPLIER", 2.0))
        trigger_pct = float(getattr(CFG, "ATR_TRAILING_TRIGGER_PCT", 7.0))
        
        atr_results = evaluate_all_positions_for_trailing_stop(
            balance_fetcher=bf,
            sell_manager=sell_mgr,
            data_dir=CFG.DATA_DIR,
            atr_multiplier=atr_multiplier,
            trigger_pct=trigger_pct,
        )
        
        armed_count = sum(1 for r in atr_results if r.get("armed"))
        triggered_count = sum(1 for r in atr_results if r.get("should_close"))
        
        if atr_results:
            LOGGER.info(f"[ATR_TRAILING] Monitored {len(atr_results)} positions | Armed: {armed_count} | Triggered: {triggered_count}")
            
            for result in atr_results:
                if result["should_close"]:
                    pair = result["pair"]
                    LOGGER.info(f"[TRADE] ATR TRAILING STOP SELL signal for {pair}")
                    try:
                        qty = sell_mgr.calculate_sell_amount(pair)
                        if qty > 0:
                            sell_mgr.execute_sell(
                                pair=pair, qty=qty,
                                cost_basis=result["entry_price"],
                                reason=f"atr_trailing_stop|profit={result['profit_pct']:.2f}%",
                            )
                            LOGGER.info(f"[TRADE] ATR TRAILING STOP SELL EXECUTED {pair}")
                            reset_peak_price(pair)
                            sell_count += 1
                    except Exception as e:
                        LOGGER.error(f"[TRADE] ATR TRAILING STOP SELL FAILED {pair}: {e}")
        
        append_signal("atr_trailing_stop", "15m", "evaluation", {
            "positions_evaluated": len(atr_results),
            "armed_count": armed_count,
            "triggered_count": triggered_count,
        })
    except Exception as e:
        LOGGER.error(f"[ATR_TRAILING] Evaluation error: {e}")


def _evaluate_gated_trailing(sell_mgr, tracked_positions, exe, sell_count) -> int:
    """
    Evaluate A3/C3 gated trailing stops for all positions.

    This is a behavior-harvesting system:
    - C3 (Sprinter): Tight 0.3% trail, probes for direction
    - A3 (Marathoner): Takes over at Gate 1 (0.5%), widens trail as profit grows

    Gate Structure:
    - Gate 1: +0.5% profit → 0.2% trail
    - Gate 2: +1.0% profit → 0.4% trail  
    - Gate 3: +2.0% profit → 1.2% trail

    Returns: Updated sell_count
    """
    global _a3c3_flags, _a3c3_a3, _a3c3_c3, _a3c3_config

    if not A3C3_AVAILABLE:
        return sell_count

    # Get all trading pairs
    pairs = [pos.get("pn") or f"{pos.get('asset', '')}/{CFG.QUOTE}" 
             for pos in tracked_positions if isinstance(pos, dict)]

    if not pairs:
        return sell_count

    # Initialize if needed
    if not _init_a3c3_system(pairs):
        LOGGER.debug("[A3C3] System not initialized, skipping gated trailing")
        return sell_count

    try:
        c3_count = 0
        a3_count = 0
        handoffs = 0
        triggered = 0

        for pos in tracked_positions:
            if not isinstance(pos, dict):
                continue

            asset = pos.get("asset", "")
            pair = pos.get("pn") or f"{asset}/{CFG.QUOTE}"
            price = get_current_price(pair)

            if price <= 0:
                continue

            # Get entry price
            entry_price = sell_mgr.get_entry_price(pair)
            if not entry_price or entry_price <= 0:
                continue

            # Get flag state for this pair
            flag = _a3c3_flags.get_state(pair)

            # If not tracked by A3C3 yet, register with C3
            if not flag or not flag.in_trade:
                # Register new position with C3
                qty = pos.get("qty", 0) or pos.get("value", 0) / price if price > 0 else 0
                result = _a3c3_c3.open_trade(
                    pair=pair,
                    entry_price=entry_price,
                    direction="long",
                    quantity=qty,
                )
                if result.get("success"):
                    LOGGER.info(f"[A3C3] C3 tracking {pair} | entry=${entry_price:.2f} | stop=${result.get('stop_price', 0):.2f}")
                continue

            # Evaluate based on current owner
            owner = _a3c3_flags.get_owner(pair)

            if owner == ScriptOwner.C3:
                c3_count += 1
                result = _a3c3_c3.evaluate(pair, price)

                if result["type"] == "close":
                    # C3 stop hit - execute sell
                    triggered += 1
                    LOGGER.info(f"[A3C3] C3 STOP HIT {pair} | profit={result.get('profit_pct', 0)*100:.2f}%")

                    qty = sell_mgr.calculate_sell_amount(pair)
                    if qty > 0:
                        sell_mgr.execute_sell(
                            pair=pair, qty=qty, cost_basis=entry_price,
                            reason=f"c3_trailing_stop|profit={result.get('profit_pct', 0)*100:.2f}%"
                        )
                        sell_count += 1

                        # Record for buy manager
                        try:
                            record_stop_loss_exit(pair, price)
                        except Exception:
                            pass

                elif result["type"] == "handoff":
                    # C3 reached Gate 1 - hand off to A3
                    handoffs += 1
                    a3_result = _a3c3_a3.accept_handoff(
                        pair=pair,
                        entry_price=result["entry_price"],
                        peak_price=result["peak_price"],
                        direction=result["direction"],
                        quantity=result["quantity"],
                    )

                    if a3_result.get("success"):
                        _a3c3_c3.complete_handoff(pair, a3_result["stop_price"])
                        LOGGER.info(f"[A3C3] HANDOFF C3→A3 {pair} | gate={a3_result['gate']} | stop=${a3_result['stop_price']:.2f}")

                elif result["type"] == "update_stop":
                    LOGGER.debug(f"[A3C3] C3 stop updated {pair} | stop=${result.get('stop_price', 0):.2f}")

            elif owner == ScriptOwner.A3:
                a3_count += 1
                result = _a3c3_a3.evaluate(pair, price)

                if result["type"] == "close":
                    # A3 stop hit - execute sell
                    triggered += 1
                    gate = result.get("gate", 0)
                    profit_pct = result.get("profit_pct", 0) * 100
                    LOGGER.info(f"[A3C3] A3 STOP HIT {pair} | gate={gate} | profit={profit_pct:.2f}%")

                    qty = sell_mgr.calculate_sell_amount(pair)
                    if qty > 0:
                        sell_mgr.execute_sell(
                            pair=pair, qty=qty, cost_basis=entry_price,
                            reason=f"a3_gated_stop_g{gate}|profit={profit_pct:.2f}%"
                        )
                        sell_count += 1

                        # Record for buy manager
                        try:
                            record_stop_loss_exit(pair, price)
                        except Exception:
                            pass

                elif result["type"] == "gate_upgrade":
                    LOGGER.info(f"[A3C3] GATE UPGRADE {pair} | {result.get('old_gate')}→{result.get('new_gate')} | stop=${result.get('stop_price', 0):.2f}")

                elif result["type"] == "update_stop":
                    LOGGER.debug(f"[A3C3] A3 stop updated {pair} | gate={result.get('gate', 0)} | stop=${result.get('stop_price', 0):.2f}")

        # Log summary
        if c3_count + a3_count > 0:
            LOGGER.info(f"[A3C3] Positions: C3={c3_count} A3={a3_count} | Handoffs={handoffs} | Triggered={triggered}")

        # Log to decision report
        try:
            append_signal("a3c3_gated_trailing", "tick", "evaluation", {
                "c3_positions": c3_count,
                "a3_positions": a3_count,
                "handoffs": handoffs,
                "triggered": triggered,
            })
        except Exception:
            pass

    except Exception as e:
        LOGGER.error(f"[A3C3] Evaluation error: {e}")

    return sell_count


def _evaluate_buys(exe, bf, sell_mgr, risk_mgr, fee_fetcher,
                   quote_cash, summary, tracked_positions, tracked_assets) -> int:
    """Evaluate buy signals. Returns number of buys placed."""
    buy_count = 0
    
    min_notional = float(getattr(CFG, "MIN_NOTIONAL_BUY", 10.0))
    if quote_cash < min_notional:
        LOGGER.info(f"[TRADE] Cash below minimum notional: ${quote_cash:.2f} < ${min_notional:.2f} - skipping buys")
        return 0

    from config_factory import is_small_account, get_min_cash_for_new_position
    small = is_small_account(summary)
    min_cash_needed = get_min_cash_for_new_position(summary)
    num_positions = len(tracked_positions)
    
    if small and (quote_cash < min_cash_needed or num_positions >= CFG.SMALL_ACCOUNT_CONFIG["max_positions_small"]):
        LOGGER.info(
            "[BUY-GATE] Small account mode active. Skipping new position evaluations."
        )
        return 0

    for pair in rotated_pairs():
        LOGGER.info(f"[BUY_LOOP_DEBUG] Evaluating {pair}")
        if not check_budget("buy_evaluation"):
            break
        if buy_count >= MAX_BUYS_PER_TICK:
            LOGGER.info("[TRADE] Buy cap reached for this tick: %d", MAX_BUYS_PER_TICK)
            break

        base = pair.split("/")[0]
        if normalize_asset_symbol(base) in tracked_assets:
            LOGGER.debug(f"[TRADE] Skip {pair}: already tracked")
            continue
        if in_buy_cooldown(pair):
            LOGGER.debug(f"[TRADE] Skip {pair}: in buy cooldown")
            continue

        try:
            ok, conf, feats = should_buy(pair, account_balance=quote_cash)

            # Log why signals are rejected
            if not ok:
                reason = feats.get("reason", "unknown")
                LOGGER.info(f"[BUY_SIGNAL] {pair}: SKIP - {reason}")
                continue

            LOGGER.info(f"[BUY_SIGNAL] {pair}: SIGNAL OK - conf={conf:.2f}, reason={feats.get('reason', 'n/a')}")

            dynamic_notional = feats.get("suggested_notional", 0.0)
            
            # Apply fee adjustments
            if fee_fetcher is not None and dynamic_notional > 0:
                try:
                    taker_fee = fee_fetcher.get_buy_fee(use_limit_order=False)
                    dynamic_notional = dynamic_notional * (1 - taker_fee)
                except Exception:
                    pass
            
            if dynamic_notional <= 0:
                LOGGER.info(f"[TRADE] Skip {pair}: {feats.get('reason', 'no_capacity')}")
                continue

            # Stop-buy order
            if feats.get("order_type") == "stop_buy" and feats.get("stop_price"):
                if has_open_stop_buy(pair, exe):
                    LOGGER.info(f"[MAIN] Skipping duplicate stop-buy for {pair}")
                    continue
                
                notional = dynamic_notional
                if notional < min_notional or quote_cash < notional * 1.01:
                    continue
                
                stop_price = float(feats["stop_price"])
                if exe.guard_min_notional(notional):
                    reason = f"rebound_stop_buy|conf={conf:.2f}"
                    LOGGER.info(f"[MAIN] Placing trailing stop-buy for {pair}: stop=${stop_price:.2f}, notional=${notional:.2f}")
                    exe.stop_buy_notional(pair, notional, stop_price, reason=reason, 
                                         trailing=feats.get("trailing", False),
                                         offset_pct=feats.get("offset_pct", 0.02))
                    quote_cash -= notional
                    mark_bought(pair)
                    buy_count += 1
                    continue

            # Market buy - execute immediately on Vortex signal
            notional = dynamic_notional
            if notional < min_notional or quote_cash < notional * 1.01:
                continue

            if exe.guard_min_notional(notional):
                reason = f"conf={conf:.2f}|dynamic_size=${notional:.2f}"
                exe.market_buy_notional(pair, notional, reason=reason)
                quote_cash -= notional
                mark_bought(pair)
                buy_count += 1
                LOGGER.info(f"[TRADE] BUY placed {pair} | notional=${notional:.2f}")
                
        except Exception as e:
            LOGGER.error(f"[TRADE] Buy eval error {pair}: {e}")

    return buy_count


def _log_open_orders(exe):
    """Log open stop-buy orders from Kraken using unified audit."""
    try:
        # Use unified audit function for consistent parsing
        audit = audit_open_orders(
            client=exe.client,
            target_pairs=CFG.PAIRS,
            log_output=True,  # This will log the audit line
        )
        
        # Additional context for mode verification
        mode = getattr(CFG, 'MODE', 'unknown')
        if audit["stop_buy_count"] > 0:
            LOGGER.debug(
                "[ORDERS] Mode=%s | Pairs tracked: %s",
                mode,
                ", ".join(audit["all_stop_buy_pairs"][:5])
            )
    except Exception as e:
        LOGGER.debug(f"[ORDERS] Open orders check error: {e}")
