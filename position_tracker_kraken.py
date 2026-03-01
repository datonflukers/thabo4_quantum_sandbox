# -*- coding: utf-8 -*-
from logging import getLogger
from typing import Dict, Optional
from order_history_tracker_kraken import OrderHistoryTracker
from config_factory import CFG

LOG = getLogger("position_tracker")

_ASSET_SUFFIXES = [".F"]

def _strip_suffix(asset: str) -> str:
    if not asset:
        return asset
    for sfx in _ASSET_SUFFIXES:
        if asset.endswith(sfx):
            return asset[:-len(sfx)]
    return asset

def normalize_balance_map(raw_balances: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for raw, qty in (raw_balances or {}).items():
        try:
            q = float(qty)
        except Exception:
            continue
        if q == 0:
            continue
        base = _strip_suffix(raw)
        out[base] = out.get(base, 0.0) + q
    return out

class PositionTracker:
    def __init__(self, executor):
        self.client = executor.client
        self.history_tracker = OrderHistoryTracker()
        self._inventory = {}
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        # Restrict pairs to configured set
        try:
            self._enabled_pairs = set(getattr(CFG, "PAIRS", []) or [])
        except Exception:
            self._enabled_pairs = set()
    
    def refresh_from_fills(self):
        try:
            # Use configured lookback and page cap to reduce API pressure
            lookback_days = int(getattr(CFG, "ORDER_HISTORY_LOOKBACK_DAYS", 90) or 90)
            max_pages = int(getattr(CFG, "MAX_TRADES_PAGES_PER_REFRESH", 4) or 4)
            trades = self.history_tracker.get_trades(days=lookback_days, max_pages=max_pages)
            by_pair: Dict[str, list] = {}
            for trade in trades:
                pair = trade['pair']
                if self._enabled_pairs and pair not in self._enabled_pairs:
                    continue
                if pair not in by_pair:
                    by_pair[pair] = []
                by_pair[pair].append(trade)
    
            for pair, pair_trades in by_pair.items():
                pair_trades.sort(key=lambda t: t['timestamp'])
                buys = []
                for trade in pair_trades:
                    if trade['type'] == 'buy':
                        buys.append({'volume': trade['volume'], 'price': trade['price']})
                    elif trade['type'] == 'sell':
                        sell_vol = trade['volume']
                        while sell_vol > 0 and buys:
                            if buys[0]['volume'] <= sell_vol:
                                sell_vol -= buys[0]['volume']
                                buys.pop(0)
                            else:
                                buys[0]['volume'] -= sell_vol
                                sell_vol = 0
                
                if buys:
                    total_cost = sum(b['volume'] * b['price'] for b in buys)
                    total_volume = sum(b['volume'] for b in buys)
                    if total_volume > 0:
                        self._entry_prices[pair] = total_cost / total_volume
                        LOG.info(f"Entry price for {pair}: ${self._entry_prices[pair]:.2f}")
 
            LOG.info(f"Loaded {len(self._entry_prices)} entry prices from trade history")
        except Exception as e:
            LOG.error(f"Failed to refresh from fills: {e}")
    
    def get_entry_price(self, pair: str) -> Optional[float]:
        return self._entry_prices.get(pair)
    
    def set_entry_price(self, pair: str, price: float) -> None:
        self._entry_prices[pair] = price
        LOG.info(f"Manually set entry price for {pair}: ${price:.2f}")
    
    def get_peak_price(self, pair: str) -> Optional[float]:
        return self._peak_prices.get(pair)
    
    def update_peak_price(self, pair: str, current_price: float) -> None:
        if pair not in self._peak_prices or current_price > self._peak_prices[pair]:
            self._peak_prices[pair] = current_price
    
    def get_position(self, pair: str) -> Optional[Dict]:
        entry_price = self.get_entry_price(pair)
        if not entry_price:
            return None
        return {
            "pair": pair,
            "entry_price": entry_price,
            "qty": 1.0,
        }
    
    def get_inventory(self):
        return self._inventory
    
    def summary(self):
        return {
            pair: {
                "entry_price": price,
                "peak_price": self._peak_prices.get(pair),
            }
            for pair, price in self._entry_prices.items()
        }
