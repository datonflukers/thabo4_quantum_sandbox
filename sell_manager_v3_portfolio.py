# -*- coding: utf-8 -*
"""
Sell Manager V3.0 - Portfolio-Aware Sell Logic (clean)

- Reads trailing config from .env via CFG (TRAIL_ENABLE, TRAIL_ARM, TRAIL_PCT)
- Emits trail_armed, trail_updated, trail_triggered events into decision_report
- Keeps stop-loss and profit-gate logic intact
- Integrates Vortex Strategy exit signals (dual-layer EMA)
- Professional colorized console output
"""
from __future__ import annotations

from typing import Tuple, Dict, Optional, List, Any
from logging import getLogger
import time
import pandas as pd

from account_balance_fetcher_kraken import BalanceFetcher
from order_executor_kraken import OrderExecutor
from position_tracker_kraken import PositionTracker
from config_factory import CFG, get_exit_config
from market_utils import get_atr_for_pair
from decision_report import append_signal
from order_history_tracker_kraken import OrderHistoryTracker

# Vortex Strategy for exit signals
try:
    from live_vortex_strategy import get_vortex_strategy
    VORTEX_AVAILABLE = True
except ImportError:
    VORTEX_AVAILABLE = False

# Color support
try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLOR_AVAILABLE = True
except ImportError:
    COLOR_AVAILABLE = False

LOG = getLogger("sell_manager")

DUST_THRESHOLD_USD = 5.0

# Assets to skip from portfolio enumeration (fiat and stablecoins)
SKIP_ASSETS = {
    CFG.QUOTE,
    f"Z{CFG.QUOTE}",
    "USD",
    "ZUSD",
    "EUR",
    "ZEUR",
    "CHF",
    "ZCHF",
    "USDT",
    "USDC",
}

# Trailing stop state (per pair)
_trailing_state: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# PROFESSIONAL OUTPUT FORMATTING
# =============================================================================

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


def _log_info(label: str, value: Any) -> None:
    """Log info line with label."""
    LOG.info(f"    {label:<25} {_c(Fore.WHITE, str(value))}")


def _log_trade_action(action: str, pair: str, details: Dict[str, Any]) -> None:
    """Log a trade action with formatted output."""
    color = Fore.GREEN if action in ["SELL", "PROFIT"] else Fore.YELLOW if action == "TRAIL" else Fore.RED
    LOG.info(_c(color, f"  [{action}] {pair}"))
    for key, val in details.items():
        if isinstance(val, float):
            LOG.info(f"    {key:<20} {val:,.4f}")
        else:
            LOG.info(f"    {key:<20} {val}")


def _trail_key(pair: str) -> str:
    return pair.lower()


def _trail_enabled() -> bool:
    try:
        return bool(getattr(CFG, "TRAIL_ENABLE", True))
    except Exception:
        return True


def _trail_activation_pct() -> float:
    try:
        return float(getattr(CFG, "TRAIL_ARM", 0.02))
    except Exception:
        return 0.02


def _trail_distance_pct() -> float:
    """Get trail distance percentage with validation."""
    try:
        val = float(getattr(CFG, "TRAIL_PCT", 0.01))
        # Enforce minimum trail distance to prevent disabled trailing
        if val <= 0:
            LOG.warning("[TRAIL] TRAIL_PCT is 0 or negative (%.4f), using default 0.01 (1%%)", val)
            return 0.01
        return val
    except Exception:
        return 0.01


def _trail_log(event_type: str, pair: str, data: Dict[str, Any]) -> None:
    """Log trailing stop events with professional formatting."""
    payload = {
        "pair": pair,
        "timestamp": int(time.time()),
        "trail_activation_pct": _trail_activation_pct(),
        "trail_distance_pct": _trail_distance_pct(),
        **data,
    }
    try:
        append_signal("sell_manager", "15m", event_type, payload)
    except Exception:
        pass
    
    # Professional formatted logging
    try:
        if event_type == "trail_armed":
            color = Fore.GREEN if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            profit_pct = data.get("profit_pct", 0) * 100
            LOG.info(f"{color}  [TRAIL ARMED] {pair} | Profit: {profit_pct:+.2f}% | Arm threshold reached{reset}")
        elif event_type == "trail_updated":
            color = Fore.CYAN if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            new_high = data.get("new_high", 0) * 100
            LOG.info(f"{color}  [TRAIL UPDATE] {pair} | New high: {new_high:+.2f}%{reset}")
        elif event_type == "trail_triggered":
            color = Fore.YELLOW if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            profit_pct = data.get("profit_pct", 0) * 100
            trail_peak = data.get("trail_peak", 0) * 100
            LOG.info(f"{color}  [TRAIL TRIGGER] {pair} | Profit: {profit_pct:+.2f}% | Peak was: {trail_peak:+.2f}% | SELLING{reset}")
        elif event_type == "hard_stop_loss":
            color = Fore.RED if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            profit_pct = data.get("profit_pct", 0) * 100
            LOG.info(f"{color}  [HARD STOP] {pair} | Loss: {profit_pct:+.2f}% | Emergency exit{reset}")
        elif event_type == "soft_stop_loss_age":
            color = Fore.RED if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            profit_pct = data.get("profit_pct", 0) * 100
            hold_hours = data.get("hold_time_hours", 0)
            LOG.info(f"{color}  [SOFT STOP] {pair} | Loss: {profit_pct:+.2f}% | Held: {hold_hours:.1f}h | Exiting{reset}")
        elif event_type == "profit_gate":
            color = Fore.GREEN if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            profit_pct = data.get("profit_pct", 0) * 100
            LOG.info(f"{color}  [PROFIT GATE] {pair} | Profit: {profit_pct:+.2f}% | Target reached | SELLING{reset}")
        elif event_type == "breakeven_exit":
            color = Fore.YELLOW if COLOR_AVAILABLE else ""
            reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
            profit_pct = data.get("profit_pct", 0) * 100
            LOG.info(f"{color}  [BREAKEVEN] {pair} | P&L: {profit_pct:+.2f}% | Max hold reached | Exiting{reset}")
        else:
            LOG.info(f"  [TRAIL] {pair} | {event_type} | {data}")
    except Exception:
        LOG.info("[REPORT] sell_manager.15m.%s | pair=%s | details=%s", event_type, pair, payload)


def _report_eval(pair: str, details: Dict[str, Any]) -> None:
    snap = {
        "pair": pair,
        "timestamp": int(time.time()),
        "trail_activation_pct": _trail_activation_pct(),
        "trail_distance_pct": _trail_distance_pct(),
        **details,
    }
    try:
        append_signal("sell_manager", "15m", "evaluation", snap)
    except Exception:
        pass
    try:
        LOG.info(
            "[REPORT] sell_manager.15m.evaluation | pair=%s | reason=%s | profit=%.4f | threshold=%.4f | trail_armed=%s | trail_high=%.4f | soft_sl=%.1f | hard_sl=%.1f | hold_time=%.1fh",
            pair,
            str(details.get("reason")),
            float(details.get("profit_pct") or 0.0),
            float(details.get("threshold_pct") or 0.0),
            str(details.get("trail_armed")),
            float(details.get("trail_high") or 0.0),
            float(details.get("soft_stop_loss_pct") or 0.0),
            float(details.get("hard_stop_loss_pct") or 0.0),
            float(details.get("hold_time_hours") or 0.0),
        )
    except Exception:
        pass


def _to_kraken_pair(pair: str) -> str:
    """Map BASE/QUOTE like BTC/USD to Kraken altname (e.g., XBTUSD)."""
    mapping = {
        "BTC": "XBT",
        "XBT": "XBT",
        "ETH": "ETH",
        "USD": "USD",
        "EUR": "EUR",
        "SOL": "SOL",
        "LINK": "LINK",
        "ADA": "ADA",
        "DOT": "DOT",
        "AVAX": "AVAX",
        "POL": "POL",
        "XRP": "XRP",
        "LTC": "LTC",
    }
    try:
        base, quote = pair.split("/")
    except ValueError:
        return pair.replace("/", "")
    base = mapping.get(base, base)
    quote = mapping.get(quote, quote)
    return f"{base}{quote}"


class SellManagerV3:
    """Portfolio-aware sell manager with entry price tracking and trailing stops."""

    def __init__(
        self,
        balance_fetcher: Optional[BalanceFetcher] = None,
        order_executor: Optional[OrderExecutor] = None,
        history_tracker: Optional[Any] = None,
    ) -> None:
        self.balance_fetcher = balance_fetcher or BalanceFetcher()
        self.order_executor = order_executor or OrderExecutor(self.balance_fetcher)
        self.position_tracker = PositionTracker(self.order_executor)
        self._entry_price_overrides: Dict[str, float] = {}
        self.entry_prices: Dict[str, float] = {}
        self.history_tracker = history_tracker or OrderHistoryTracker()
        
        # Professional initialization output with full config details
        _log_header("SELL MANAGER V3 INITIALIZED")
        _log_info("Mode", CFG.MODE)
        
        # Trailing Stop Configuration
        trail_enabled = _trail_enabled()
        trail_arm = _trail_activation_pct()
        trail_dist = _trail_distance_pct()
        
        _log_info("Trailing Enabled", trail_enabled)
        _log_info("Trail Arm Threshold", f"{trail_arm * 100:.2f}% profit to arm")
        _log_info("Trail Distance", f"{trail_dist * 100:.2f}% drop from peak to trigger")
        
        # Validation warnings
        if trail_enabled and trail_dist <= 0:
            _log_warning("TRAIL_PCT is 0 - trailing stop will never trigger!")
        if trail_enabled and trail_arm <= 0:
            _log_warning("TRAIL_ARM is 0 - trailing stop arms immediately (risky)")

        # ATR Trailing Stop Config (separate system)
        atr_trigger = getattr(CFG, "ATR_TRAILING_TRIGGER_PCT", 7.0)
        atr_mult = getattr(CFG, "ATR_TRAILING_MULTIPLIER", 2.0)
        _log_info("ATR Trailing Trigger", f"{atr_trigger:.1f}% profit to arm")
        _log_info("ATR Trailing Multiplier", f"{atr_mult}x ATR buffer")

        # Initial ledger refresh to load entry prices for all positions
        try:
            self.refresh_ledger()
            _log_info("Initial Entry Prices", len(self.entry_prices))
        except Exception as e:
            _log_warning(f"Initial ledger refresh failed: {e}")

        _log_success("SellManagerV3 ready")

    def _ensure_executor(self) -> None:
        if self.order_executor is None:
            try:
                self.order_executor = OrderExecutor(self.balance_fetcher)
                self.position_tracker = PositionTracker(self.order_executor)
                LOG.info("SellManagerV3: OrderExecutor auto-initialized")
            except Exception as e:
                LOG.error("SellManagerV3: failed to init OrderExecutor: %s", e)

    def refresh_ledger(self) -> None:
        """Refresh position ledger and sync entry prices from position tracker."""
        try:
            self.position_tracker.refresh_from_fills()
            # Sync entry prices from position tracker to sell manager
            for pair, price in self.position_tracker._entry_prices.items():
                if pair not in self.entry_prices or self.entry_prices[pair] != price:
                    self.entry_prices[pair] = price
                    LOG.debug(f"Synced entry price for {pair}: ${price:.2f}")
            if self.entry_prices:
                LOG.info(f"[LEDGER] Synced {len(self.entry_prices)} entry prices: {list(self.entry_prices.keys())}")
        except Exception as e:
            LOG.warning("Ledger refresh failed: %s", e)

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Get all open positions from the balance fetcher.
        
        Returns a list of position dictionaries with:
        - asset: The asset symbol (e.g., 'BTC', 'ETH')
        - pair: The trading pair (e.g., 'BTC/USD')
        - qty: The quantity held
        - value_usd: The USD value of the position
        - current_price: The current price per unit
        
        Filters out:
        - Fiat currencies (USD, EUR, CHF)
        - Stablecoins (USDT, USDC)
        - Dust positions (< $5 USD value)
        """
        positions = []
        
        try:
            # Get all balances from Kraken
            balances = self.balance_fetcher.get_all_balances(use_cache=True)
            
            for asset, qty in balances.items():
                # Skip fiat and stablecoins
                if asset in SKIP_ASSETS:
                    continue
                    
                # Skip zero or negative balances
                if qty <= 0:
                    continue
                
                # Normalize asset name
                normalized_asset = self._normalize_asset_name(asset)
                if not normalized_asset or normalized_asset in SKIP_ASSETS:
                    continue
                
                # Build pair
                pair = f"{normalized_asset}/{CFG.QUOTE}"
                
                # Get current price
                try:
                    from account_balance_fetcher_kraken import get_current_price
                    current_price = get_current_price(pair)
                except Exception:
                    current_price = 0.0
                
                # Calculate USD value
                value_usd = qty * current_price if current_price > 0 else 0.0
                
                # Skip dust positions
                if value_usd < DUST_THRESHOLD_USD:
                    continue
                
                positions.append({
                    "asset": normalized_asset,
                    "pair": pair,
                    "qty": qty,
                    "value_usd": value_usd,
                    "current_price": current_price,
                })
            
            # Sort by USD value descending
            positions.sort(key=lambda x: x["value_usd"], reverse=True)
            
            _log_success(f"Found {len(positions)} open position(s)")
            for pos in positions:
                _log_info(pos["pair"], f"Qty: {pos['qty']:.8f} | Value: ${pos['value_usd']:,.2f}")
            
        except Exception as e:
            _log_error(f"Failed to get open positions: {e}")
        
        return positions

    def get_total_exposure_usd(self) -> float:
        """Get total USD exposure across all open positions."""
        positions = self.get_open_positions()
        return sum(pos["value_usd"] for pos in positions)

    def set_entry_price(self, pair: str, price: float) -> None:
        p = float(price)
        self._entry_price_overrides[pair] = p
        self.entry_prices[pair] = p

    def get_entry_price(self, pair: str) -> Optional[float]:
        if pair in self._entry_price_overrides:
            return self._entry_price_overrides[pair]
        if pair in self.entry_prices:
            return self.entry_prices[pair]
        # Fallback to position tracker
        try:
            pt_price = self.position_tracker.get_entry_price(pair)
        except Exception:
            pt_price = None
        if pt_price and pt_price >0:
            # cache it locally so repeated calls don't depend on tracker
            self.entry_prices[pair] = float(pt_price)
            return float(pt_price)
        # Last resort: attempt deep history load once to populate missing pairs
        try:
            self.load_entry_prices_from_history(days=730, force_refresh=True, max_pages=20)
        except Exception:
            pass
        return self.entry_prices.get(pair)

    def load_entry_prices_from_history(self, days: int = 365, force_refresh: bool = True, max_pages: Optional[int] = None) -> None:
        """Load entry prices using FIFO from trade history.

        - Pulls trades via OrderHistoryTracker with optional max_pages override
        - Matches sells against buys (FIFO) to compute remaining cost basis
        - Sets entry price per held pair to weighted avg of remaining buys
        """
        # Determine page cap (use a deeper default specifically for entry price discovery)
        if max_pages is not None:
            page_cap = int(max_pages)
        else:
            # Prefer a dedicated ENTRY_PRICE_HISTORY_PAGES if present, else fall back to global setting, else20
            try:
                page_cap = int(getattr(CFG, 'ENTRY_PRICE_HISTORY_PAGES', getattr(CFG, 'MAX_TRADES_PAGES_PER_REFRESH',20)))
            except Exception:
                page_cap = 20
        # Ensure we probe deep enough at least once to find older pairs like LTC/XRP
        if page_cap < 15:
            page_cap = 15

        # Fetch current balances to know which bases we care about
        balances = self.balance_fetcher.get_all_balances()
        held_bases: Dict[str, float] = {}
        for asset_code, qty_val in balances.items():
            try:
                qty = float(qty_val)
            except (ValueError, TypeError):
                continue
            if qty <= 0 or asset_code in SKIP_ASSETS:
                continue
            base = self._normalize_asset_name(asset_code)
            held_bases[base] = held_bases.get(base, 0.0) + qty

        if not held_bases:
            LOG.info("No non-fiat balances to compute entry prices from history")
            return

        def compute_from_trades(trades: List[Dict[str, Any]], targets: List[str]) -> int:
            # Group by pair
            trades_by_pair: Dict[str, List[Dict[str, Any]]] = {}
            for t in trades:
                pair = t.get("pair")
                if not pair:
                    continue
                trades_by_pair.setdefault(pair, []).append(t)

            loaded_local = 0
            for base in targets:
                pair = f"{base}/{CFG.QUOTE}"
                pair_trades = trades_by_pair.get(pair, [])
                if not pair_trades:
                    continue
                pair_trades.sort(key=lambda x: float(x.get("timestamp", 0)))
                buy_queue: List[Dict[str, float]] = []
                for t in pair_trades:
                    typ = t.get("type")
                    vol = float(t.get("volume", 0) or 0)
                    if vol <= 0:
                        continue
                    if typ == 'buy':
                        # effective unit cost includes fee
                        cost = float(t.get("cost", 0) or 0)
                        fee = float(t.get("fee", 0) or 0)
                        unit_cost = (cost + fee) / vol if vol > 0 else 0.0
                        buy_queue.append({"remaining": vol, "unit_cost": unit_cost})
                    elif typ == 'sell':
                        remaining_to_match = vol
                        # consume from FIFO
                        i = 0
                        while remaining_to_match > 1e-18 and i < len(buy_queue):
                            r = buy_queue[i]
                            take = min(r["remaining"], remaining_to_match)
                            r["remaining"] -= take
                            remaining_to_match -= take
                            if r["remaining"] <= 1e-18:
                                buy_queue.pop(i)
                            else:
                                i += 1
                # After matching, the remaining buys represent current position cost basis
                total_qty = sum(b["remaining"] for b in buy_queue)
                if total_qty > 0:
                    total_cost = sum(b["remaining"] * b["unit_cost"] for b in buy_queue)
                    avg_cost = total_cost / total_qty
                    self.entry_prices[pair] = avg_cost
                    loaded_local += 1
            return loaded_local

        # First pass: use provided days and computed page_cap
        try:
            trades = self.history_tracker.get_trades(days=days, force_refresh=force_refresh, max_pages=page_cap)
        except Exception as e:
            LOG.error("Failed to fetch trades for entry price load: %s", e)
            return
        targets = list(held_bases.keys())
        loaded = compute_from_trades(trades, targets)

        # If some targets still missing, retry once with deeper history
        missing = [b for b in held_bases.keys() if f"{b}/{CFG.QUOTE}" not in self.entry_prices]
        if missing:
            deeper_days = max(days, 730)
            deeper_pages = max(page_cap or 0, 20)
            LOG.info("Entry price retry for %d missing pairs (%s) with deeper history: days=%d, pages=%d", len(missing), ",".join(missing), deeper_days, deeper_pages)
            try:
                trades2 = self.history_tracker.get_trades(days=deeper_days, force_refresh=True, max_pages=deeper_pages)
            except Exception as e:
                LOG.warning("Deeper history fetch failed: %s", e)
                trades2 = []
            if trades2:
                loaded += compute_from_trades(trades2, missing)

        LOG.info("Loaded entry prices from history for %d pairs (days=%s, pages=%s)", loaded, days, page_cap)

    def get_all_positions(self) -> Dict[str, List[Dict[str, Any]]]:
        try:
            balances = self.balance_fetcher.get_all_balances()
            tracked: List[Dict[str, Any]] = []
            untracked: List[Dict[str, Any]] = []
            dust: List[Dict[str, Any]] = []
            configured_bases = [p.split("/")[0] for p in CFG.PAIRS]
            for asset_code, qty_val in balances.items():
                try:
                    qty = float(qty_val)
                except (ValueError, TypeError):
                    continue
                if qty <=1e-8 or asset_code in SKIP_ASSETS:
                    continue
                base = self._normalize_asset_name(asset_code)
                pair = f"{base}/{CFG.QUOTE}"
                try:
                    self._ensure_executor()
                    price = self.order_executor._get_current_price(pair, side="bid") if self.order_executor else 0.0
                except Exception:
                    price = 0.0
                usd_value = qty * price
                pos = {
                    "asset": base,
                    "pair": pair,
                    "qty": qty,
                    "price": price,
                    "usd_value": usd_value,
                    "is_staking": asset_code.endswith(".F"),
                    "original_asset_code": asset_code,
                }
                if usd_value < DUST_THRESHOLD_USD:
                    dust.append(pos)
                elif base in configured_bases:
                    tracked.append(pos)
                else:
                    untracked.append(pos)
            tracked.sort(key=lambda x: x["usd_value"], reverse=True)
            untracked.sort(key=lambda x: x["usd_value"], reverse=True)
            LOG.info(
                "Positions: tracked=%d, untracked=%d, dust=%d",
                len(tracked),
                len(untracked),
                len(dust),
            )
            return {"tracked": tracked, "untracked": untracked, "dust": dust}
        except Exception as e:
            LOG.warning("get_all_positions failed: %s", e)
            return {"tracked": [], "untracked": [], "dust": []}

    def calculate_sell_amount(self, pair: str) -> float:
        base = pair.split("/")[0]
        if base == "BTC":
            base = "XBT"
        balances = self.balance_fetcher.get_all_balances()
        keys_to_check = [
            base,
            f"X{base}",
            f"XX{base}",
            f"{base}.F",
            f"X{base}.F",
            f"XX{base}.F",
        ]
        total_balance = 0.0
        for key in keys_to_check:
            if key in balances:
                try:
                    bal = float(balances[key])
                    if bal > 0:
                        total_balance += bal
                except (ValueError, TypeError):
                    continue
        LOG.info("Sell amount for %s: %.8f %s", pair, total_balance, base)
        return total_balance

    def should_sell(self, pair: str, current_price: float, use_neural: bool = True) -> Tuple[bool, Dict]:
        entry = self.get_entry_price(pair)
        if not entry or entry <=0:
            # Try an immediate deep load if entry missing
            try:
                self.load_entry_prices_from_history(days=730, force_refresh=True, max_pages=20)
                entry = self.get_entry_price(pair)
            except Exception:
                entry = None
        if not entry or entry <=0:
            return False, {"reason": "no_entry_price"}

        profit_pct = (current_price - entry) / entry
        asset_type = "crypto"  # Assume crypto for now
        cfg = get_exit_config(asset_type)
        tp = cfg["take_profit_pct"]
        soft_sl = cfg["soft_stop_loss_pct"]
        hard_sl = cfg["hard_stop_loss_pct"]
        max_hold_hours = cfg["max_hold_hours"]
        breakeven_band = cfg.get("breakeven_band_pct", 1.0)

        # Convert thresholds from percentage to decimal to match profit_pct format
        tp_decimal = tp / 100.0  # Convert 4.0% to 0.04
        soft_sl_decimal = soft_sl / 100.0  # Convert -3.0% to -0.03
        hard_sl_decimal = hard_sl / 100.0  # Convert -6.0% to -0.06
        breakeven_band_decimal = breakeven_band / 100.0  # Convert 1.0% to 0.01

        # Placeholder for hold time - in real implementation, calculate from position entry timestamp
        hold_time_hours = 24.0  # TODO: Calculate actual hold time

        # Hard stop-loss
        if profit_pct <= hard_sl_decimal:
            details = {
                "reason": "hard_stop_loss",
                "profit_pct": profit_pct,
                "entry_price": float(entry),
                "current_price": float(current_price),
                "threshold_pct": tp,  # Keep original for logging
                "soft_stop_loss_pct": soft_sl,
                "hard_stop_loss_pct": hard_sl,
                "hold_time_hours": hold_time_hours,
                "trail_armed": False,
                "trail_high": 0.0,
            }
            _trail_log("hard_stop_loss", pair, details)
            _report_eval(pair, details)
            return True, details

        # Soft stop-loss + age check
        if profit_pct <= soft_sl_decimal and hold_time_hours >= max_hold_hours:
            details = {
                "reason": "soft_stop_loss_age",
                "profit_pct": profit_pct,
                "entry_price": float(entry),
                "current_price": float(current_price),
                "threshold_pct": tp,
                "soft_stop_loss_pct": soft_sl,
                "hard_stop_loss_pct": hard_sl,
                "hold_time_hours": hold_time_hours,
                "trail_armed": False,
                "trail_high": 0.0,
            }
            _trail_log("soft_stop_loss_age", pair, details)
            _report_eval(pair, details)
            return True, details

        # Breakeven exit for stale trades
        if -breakeven_band_decimal <= profit_pct <= breakeven_band_decimal and hold_time_hours >= max_hold_hours:
            details = {
                "reason": "breakeven_exit",
                "profit_pct": profit_pct,
                "entry_price": float(entry),
                "current_price": float(current_price),
                "threshold_pct": tp,
                "breakeven_band_pct": breakeven_band,
                "hold_time_hours": hold_time_hours,
                "trail_armed": False,
                "trail_high": 0.0,
            }
            _trail_log("breakeven_exit", pair, details)
            _report_eval(pair, details)
            return True, details

        # === VORTEX STRATEGY EXIT CHECK ===
        # Check for dual-layer EMA exit signal (both bearish OR macro death cross)
        if VORTEX_AVAILABLE:
            try:
                vortex = get_vortex_strategy()
                # Load 15m data for the pair
                from buy_manager import _load_15m_data
                df = _load_15m_data(pair)
                if df is not None and len(df) >= 50:
                    should_exit, exit_reason, vortex_feats = vortex.should_sell(pair, df, current_price)
                    if should_exit:
                        details = {
                            "reason": f"vortex_exit_{exit_reason}",
                            "profit_pct": profit_pct,
                            "entry_price": float(entry),
                            "current_price": float(current_price),
                            "vortex_signal": exit_reason,
                            "trail_armed": False,
                            "trail_high": 0.0,
                        }
                        _trail_log("vortex_exit", pair, details)
                        _report_eval(pair, details)
                        LOG.info(f"[VORTEX_EXIT] {pair} - {exit_reason}")
                        return True, details
            except Exception as e:
                LOG.debug(f"Vortex exit check failed: {e}")

        # Trailing stop if enabled
        if _trail_enabled():
            arm_pct = _trail_activation_pct()
            dist_pct = _trail_distance_pct()
            key = _trail_key(pair)
            state = _trailing_state.setdefault(key, {"armed": False, "high": 0.0})
            if not state["armed"] and profit_pct >= arm_pct:
                state["armed"] = True
                state["high"] = profit_pct
                _trail_log("trail_armed", pair, {"profit_pct": profit_pct})
            elif state["armed"]:
                if profit_pct > state["high"]:
                    state["high"] = profit_pct
                    _trail_log("trail_updated", pair, {"new_high": profit_pct})
                elif profit_pct <= (state["high"] - dist_pct):
                    details = {
                        "reason": "trailing_stop_triggered",
                        "profit_pct": profit_pct,
                        "trail_peak": state["high"],
                        "entry_price": float(entry),
                        "current_price": float(current_price),
                    }
                    _trail_log("trail_triggered", pair, details)
                    _report_eval(pair, details)
                    return True, details
        else:
            # Ensure clean state if trailing is disabled
            key = _trail_key(pair)
            _trailing_state.pop(key, None)

        # Convert threshold from percentage to decimal to match profit_pct format
        tp_decimal = tp / 100.0  # Convert 4.0% to 0.04
        
        is_triggered = profit_pct >= tp_decimal
        reason = "profit_gate" if is_triggered else "below_threshold"
        details = {
            "reason": reason,
            "profit_pct": profit_pct,
            "entry_price": float(entry),
            "current_price": float(current_price),
            "threshold_pct": tp,  # Keep original for logging
            "soft_stop_loss_pct": soft_sl,
            "hard_stop_loss_pct": hard_sl,
            "hold_time_hours": hold_time_hours,
            "trail_armed": bool(_trailing_state.get(_trail_key(pair), {}).get("armed", False)),
            "trail_high": float(_trailing_state.get(_trail_key(pair), {}).get("high", 0.0)),
        }
        _report_eval(pair, details)
        if is_triggered:
            _trail_log("profit_gate", pair, details)
        return is_triggered, details

    def should_sell_swing(self, pair: str, current_price: float) -> Tuple[bool, Dict]:
        entry = self.get_entry_price(pair)
        if not entry or entry <= 0:
            return False, {"reason": "no_entry_price"}
        try:
            atr_1h = float(get_atr_for_pair(pair, timeframe="1h") or 0.0)
        except Exception:
            atr_1h = 0.0
        trailing_trigger = entry + atr_1h * float(getattr(CFG, "SWING_ATR_MULTIPLIER", 1.5))
        is_triggered = current_price <= trailing_trigger and current_price > 0
        return (
            is_triggered,
            {
                "reason": "atr_trailing_stop",
                "entry_price": float(entry),
                "current_price": float(current_price),
                "atr_1h": atr_1h,
                "trailing_trigger": trailing_trigger,
            },
        )

    def execute_sell(self, pair: str, qty: float, cost_basis: Optional[float] = None, reason: str = "") -> Dict:
        """Execute a market sell with robust fallback logic.

        1) Try OrderExecutor.market_sell
        2) If it fails or returns None, attempt direct AddOrder with sane rounding and min-volume enforcement
        3) If both fail, return a synthetic 'skipped' result with fee-adjusted net estimate
        """
        self._ensure_executor()
        
        # Professional sell execution logging
        color = Fore.CYAN if COLOR_AVAILABLE else ""
        reset = Style.RESET_ALL if COLOR_AVAILABLE else ""
        LOG.info(f"{color}  [SELL EXEC] {pair} | Qty: {qty:.8f} | Reason: {reason}{reset}")
        
        try:
            if self.order_executor:
                result = self.order_executor.market_sell(
                    pair=pair,
                    qty=qty,
                    reason=reason,
                    cost_basis=cost_basis,
                    validate_profit=False,
                )
            else:
                result = None
        except Exception as e:
            _log_error(f"market_sell raised: {e}")
            result = None

        if result and not isinstance(result, dict) or (isinstance(result, dict) and not result.get("error") and result is not None):
            _log_success(f"{pair} sell order placed successfully")
            return result

        # Fallback: build order via executor internals
        try:
            exe = self.order_executor or OrderExecutor(self.balance_fetcher)
            bid_price = exe._get_current_price(pair, side="bid") or 0.0
            if bid_price <= 0:
                raise RuntimeError("bid price unavailable")
            # Round qty to exchange step and enforce min volume
            _, qty_r = exe._round_pair(pair, None, qty)
            ok, qty_ok, why = exe._enforce_min_volume(pair, qty_r)
            if not ok or (qty_ok or 0.0) <= 0:
                LOG.warning("[SELL] %s skipped in fallback: %s", pair, why or "min_volume")
                raise RuntimeError(why or "min_volume")
            # Place order via client
            kraken_pair = _to_kraken_pair(pair)
            data = {
                "ordertype": "market",
                "type": "sell",
                "pair": kraken_pair,
                "volume": f"{qty_ok:.10f}",
            }
            res = exe.client.private_post("/0/private/AddOrder", data)
            LOG.info("[SELL] Fallback order placed: %s", res)
            return res
        except Exception as fe:
            LOG.error("Fallback sell failed: %s", fe)

        # Last resort: return a skipped result with fee-adjusted net
        try:
            exe = self.order_executor or OrderExecutor(self.balance_fetcher)
            bid = exe._get_current_price(pair, side="bid") or 0.0
        except Exception:
            bid = 0.0
        fee = (qty * bid) * float(getattr(self.order_executor, "SELL_FEE", 0.0026)) if bid > 0 else 0
        net = qty * current_price - fee
        LOG.info("[SELL] %s skipped: unable to execute sell order, returning synthetic result (net=%.2f)", pair, net)
        return {
            "pair": pair,
            "type": "sell",
            "ordertype": "market",
            "volume": f"{qty:.10f}",
            "cost": f"{net:.2f}",
            "fee": f"{fee:.2f}",
            "success": False,
            "error": "sell_skipped",
        }

    def _normalize_asset_name(self, asset: str) -> str:
        """
        Normalize Kraken asset names to internal symbol format.
        
        For raw asset names (like 'LINK', 'XXBT', 'XETH'):
        - Strips Kraken prefixes (X, XX, Z, ZZ)
        - Maps XBT -> BTC
        - Returns the base symbol (e.g., 'BTC', 'ETH', 'LINK')
        
        Examples:
          - 'XXBT' -> 'BTC'
          - 'XETH' -> 'ETH'
          - 'LINK' -> 'LINK'
          - 'SOL' -> 'SOL'
        """
        if not asset:
            return ""
            
        a = asset.strip()
        
        # Remove .F suffix (for staking variants)
        if a.endswith(".F"):
            a = a[:-2]
        
        # Map common Kraken asset names FIRST before stripping prefixes
        # This handles the full asset names from Kraken's balance API
        full_asset_mapping = {
            "XXBT": "BTC",
            "XETH": "ETH",
            "XXRP": "XRP",
            "XLTC": "LTC",
            "XXLM": "XLM",
            "ZUSD": "USD",
            "ZEUR": "EUR",
        }
        if a in full_asset_mapping:
            a = full_asset_mapping[a]
        else:
            # Strip Kraken prefixes for other assets (order matters - check longer prefixes first)
            if a.startswith("XX"):
                a = a[2:]
            elif a.startswith("X") and len(a) > 3:
                a = a[1:]
            if a.startswith("Z"):
                a = a[1:]
            
            # Map XBT -> BTC for any remaining cases
            asset_mapping = {
                "XBT": "BTC",
                "RP": "XRP",  # XXRP -> RP after prefix strip, map to XRP
            }
            a = asset_mapping.get(a, a)
        
        # Skip fiat and stablecoins
        if a in {"USD", "EUR", "CHF", "USDT", "USDC", "ZUSD", "ZEUR"}:
            return ""

        return a

    # NOTE: Dust collection moved to dust_collector.py module


# Singleton helpers for backward compatibility
_sell_manager_singleton: Optional[SellManagerV3] = None


def get_sell_manager() -> SellManagerV3:
    global _sell_manager_singleton
    if _sell_manager_singleton is None:
        _sell_manager_singleton = SellManagerV3()
    return _sell_manager_singleton


def should_sell_with_portfolio(pair: str, current_price: float, use_neural: bool = True) -> Tuple[bool, Dict]:
    return get_sell_manager().should_sell(pair, current_price, use_neural)


def refresh_portfolio_ledger() -> None:
    get_sell_manager().refresh_ledger()


# Alias for any modules that might still import the old name
SellManager = SellManagerV3
