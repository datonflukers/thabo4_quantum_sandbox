from logging import getLogger
from typing import Dict, Optional, Tuple
from config_factory import CFG
from kraken_client import KrakenClient
from precision_cache_builder import get_precision_cache, round_price, round_volume


LOG = getLogger("order_executor")


def _to_kraken_pair(pair: str) -> str:
    """Map BASE/QUOTE like BTC/USD to Kraken altname (e.g., XBTUSD)."""
    mapping = {
        "BTC": "XBT", "XBT": "XBT", "ETH": "ETH", "USD": "USD", "EUR": "EUR",
        "SOL": "SOL", "LINK": "LINK", "ADA": "ADA", "DOT": "DOT", "AVAX": "AVAX",
        "POL": "POL", "XRP": "XRP", "LTC": "LTC"
    }
    try:
        base, quote = pair.split("/")
    except ValueError:
        return pair.replace("/", "")
    base = mapping.get(base, base)
    quote = mapping.get(quote, quote)
    return f"{base}{quote}"


class OrderExecutor:
    """
    Order executor with full fractional trading support and fee accounting.
    """

    # Kraken fee structure
    MAKER_FEE = 0.0016  # 0.16% maker fee
    TAKER_FEE = 0.0026  # 0.26% taker fee (market orders)
    SELL_FEE = 0.0026   # Align sell fee to taker fee (0.26%)

    def __init__(self, balance_fetcher):
        self.client = KrakenClient()
        self.prec = get_precision_cache()
        self.balance_fetcher = balance_fetcher
        self.trailing_buy_orders = {}  # txid: {'pair': str, 'high': float, 'offset_pct': float}

        LOG.info("OrderExecutor initialized with fractional trading and fee accounting")
        LOG.info(
            f"Fees: Maker={self.MAKER_FEE*100:.2f}%, Taker={self.TAKER_FEE*100:.2f}%, Sell={self.SELL_FEE*100:.2f}%"
        )

    def _enforce_min_volume(self, pair: str, qty: float) -> tuple[bool, float, str]:
        info = self.prec.get(pair)
        if not info:
            return True, qty, ""
        lot_step = info.get("lot_step") or 0.0
        ord_min = info.get("min_order") or 0.0
        if lot_step > 0:
            qty = (int(qty / lot_step)) * lot_step
        ok = (qty >= ord_min) if ord_min > 0 else qty > 0
        reason = "" if ok else f"volume minimum not met (qty={qty:.10f} < ord_min={ord_min})"
        return ok, qty, reason

    def guard_min_notional(self, notional: float) -> bool:
        if notional < CFG.MIN_NOTIONAL_BUY and CFG.SKIP_BUY_IF_CASH_LT_MIN_NOTIONAL:
            LOG.info("Skip buy: notional %.2f < min %.2f", notional, CFG.MIN_NOTIONAL_BUY)
            return False
        return True

    def calculate_sell_proceeds(self, qty: float, price: float) -> Dict:
        gross_proceeds = qty * price
        fee_amount = gross_proceeds * self.SELL_FEE
        net_proceeds = gross_proceeds - fee_amount
        return {
            "gross_proceeds": gross_proceeds,
            "fee_amount": fee_amount,
            "fee_pct": self.SELL_FEE,
            "net_proceeds": net_proceeds,
            "quantity": qty,
            "price": price
        }

    def calculate_required_sell_price(
        self,
        cost_basis: float,
        target_profit_pct: float = None
    ) -> Dict:
        if target_profit_pct is None:
            target_profit_pct = CFG.TARGET_PROFIT_PCT
        if cost_basis is None or cost_basis <= 0:
            return {
                "cost_basis": cost_basis or 0.0,
                "target_profit_pct": target_profit_pct,
                "target_net_proceeds": 0.0,
                "required_sell_price": 0.0,
                "effective_increase_pct": 0.0,
                "sell_fee_pct": self.SELL_FEE
            }
        target_net_proceeds = cost_basis * (1 + target_profit_pct)
        required_gross_proceeds = target_net_proceeds / (1 - self.SELL_FEE)
        required_sell_price = required_gross_proceeds
        effective_increase_pct = (required_sell_price - cost_basis) / cost_basis
        return {
            "cost_basis": cost_basis,
            "target_profit_pct": target_profit_pct,
            "target_net_proceeds": target_net_proceeds,
            "required_sell_price": required_sell_price,
            "effective_increase_pct": effective_increase_pct,
            "sell_fee_pct": self.SELL_FEE
        }

    def validate_sell_profitability(
        self,
        cost_basis: float,
        current_price: float,
        min_profit_pct: float = None
    ) -> Tuple[bool, Dict]:
        if min_profit_pct is None:
            min_profit_pct = CFG.TARGET_PROFIT_PCT
        gross_proceeds = current_price
        fee_amount = gross_proceeds * self.SELL_FEE
        net_proceeds = gross_proceeds - fee_amount
        actual_profit = net_proceeds - (cost_basis if cost_basis and cost_basis > 0 else 0.0)
        actual_profit_pct = (actual_profit / cost_basis) if (cost_basis and cost_basis > 0) else 0.0
        meets_threshold = (actual_profit_pct >= min_profit_pct) if (cost_basis and cost_basis > 0) else False
        if cost_basis and cost_basis > 0:
            pricing = self.calculate_required_sell_price(cost_basis, min_profit_pct)
            required_price = pricing["required_sell_price"]
            price_gap = required_price - current_price
            price_gap_pct = price_gap / current_price if current_price > 0 else 0
        else:
            required_price = current_price
            price_gap = 0.0
            price_gap_pct = 0.0
        return (meets_threshold, {
            "cost_basis": cost_basis or 0.0,
            "current_price": current_price,
            "gross_proceeds": gross_proceeds,
            "fee_amount": fee_amount,
            "net_proceeds": net_proceeds,
            "actual_profit": actual_profit,
            "actual_profit_pct": actual_profit_pct,
            "min_profit_pct": min_profit_pct,
            "meets_threshold": meets_threshold,
            "required_price": required_price,
            "price_gap": price_gap,
            "price_gap_pct": price_gap_pct
        })

    def _round_pair(self, pair: str, price: float | None, qty: float | None):
        info = self.prec.get(pair)
        if not info:
            LOG.warning(f"No precision info for {pair}, using raw values")
            return price, qty
        def rstep(v, step):
            if v is None or step is None or step <= 0:
                return v
            return (int(v / step)) * step
        price = rstep(price, info.get('price_step')) if price is not None else None
        qty = rstep(qty, info.get('lot_step')) if qty is not None else None
        LOG.debug(f"{pair}: Rounded to price={price}, qty={qty}")
        return price, qty

    def _get_current_price(self, pair: str, side: str = "ask") -> float:
        try:
            kraken_pair = _to_kraken_pair(pair)
            tick = self.client.public_get("/0/public/Ticker", {"pair": kraken_pair})
            if not tick or 'result' not in tick:
                LOG.error(f"Failed to get ticker for {pair}")
                return 0.0
            result = tick['result']
            key = None
            for k in result.keys():
                if k.lower().startswith(kraken_pair.lower()):
                    key = k
                    break
            if key is None:
                key = next(iter(result.keys()))
            ticker_data = result[key]
            if side == "ask":
                price = float(ticker_data['a'][0])
            elif side == "bid":
                price = float(ticker_data['b'][0])
            else:
                ask = float(ticker_data['a'][0])
                bid = float(ticker_data['b'][0])
                price = (ask + bid) / 2
            return price
        except Exception as e:
            LOG.error(f"Error fetching price for {pair}: {e}")
            return 0.0

    def market_buy_notional(
        self,
        pair: str,
        notional: float,
        reason: str = "",
        validate_balance: bool = True
    ):
        """
        Execute a market buy order with proper volume precision.
        
        Args:
            pair: Trading pair (e.g., "BTC/USD")
            notional: Dollar amount to buy
            reason: Reason for the order
            validate_balance: Check if we have enough balance
        
        Returns:
            API response or error dict
        """
        LOG.info(f"[BUY] {pair}: notional=${notional:.2f} | reason={reason}")
        
        if validate_balance:
            balances = self.balance_fetcher.get_all_balances()
            available_usd = balances.get(CFG.QUOTE, 0.0)
            if notional > available_usd:
                LOG.error(f"Insufficient balance: ${available_usd:.2f} < ${notional:.2f}")
                return {"error": "Insufficient balance"}
        
        # Get current ask price
        ask_price = self._get_current_price(pair, side="ask")
        if ask_price == 0:
            LOG.error(f"Failed to get price for {pair}")
            return {"error": "Price fetch failed"}
        
        # Calculate quantity and round to proper precision
        qty = notional / ask_price
        qty = round_volume(pair, qty)
        
        # Enforce minimum volume
        ok, qty, why = self._enforce_min_volume(pair, qty)
        if not ok:
            LOG.warning("[BUY] %s skipped: %s", pair, why)
            return None
        
        # Actual notional with rounded volume
        actual_notional = qty * ask_price
        
        LOG.info(
            f"[BUY] {pair}: qty={qty:.8f}, price=${ask_price:.2f}, notional=${actual_notional:.2f}"
        )
        
        kraken_pair = _to_kraken_pair(pair)
        
        data = {
            'ordertype': 'market',
            'type': 'buy',
            'pair': kraken_pair,
            'volume': f"{qty:.10f}",
        }
        
        try:
            res = self.client.private_post("/0/private/AddOrder", data)
            
            if res and 'error' in res and res['error']:
                LOG.error(f"[BUY] Order failed: Kraken error: {res['error']}")
                return res
            
            LOG.info(f"[BUY] Order placed successfully: {res}")
            return res
            
        except Exception as e:
            LOG.error(f"[BUY] Exception: {e}")
            return {"error": str(e)}

    def stop_buy_notional(
        self,
        pair: str,
        notional: float,
        stop_price: float,
        reason: str = "",
        validate_balance: bool = True,
        trailing: bool = False,
        offset_pct: float = 0.02
    ):
        """
        Place a stop-loss buy order with proper price precision.
        
        Args:
            pair: Trading pair (e.g., "AVAX/USD")
            notional: Dollar amount to buy
            stop_price: Stop trigger price (will be rounded to pair's precision)
            reason: Reason for the order
            validate_balance: Check if we have enough balance
            trailing: Whether this is a trailing order
            offset_pct: Trailing offset percentage
        
        Returns:
            API response or error dict
        """
        # Round stop_price to Kraken's precision FIRST
        stop_price_raw = stop_price
        stop_price = round_price(pair, stop_price)
        
        # Get precision for formatting
        from precision_cache_builder import get_price_precision
        price_decimals = get_price_precision(pair)
        
        if abs(stop_price_raw - stop_price) > 0.0001:  # Log if changed
            LOG.info(
                f"[STOP BUY] {pair}: Rounded stop price ${stop_price_raw:.8f} ? ${stop_price:.{price_decimals}f}"
            )
        
        LOG.info(f"[STOP BUY] {pair}: notional=${notional:.2f}, stop_price=${stop_price:.{price_decimals}f} | reason={reason}")
        
        if validate_balance:
            balances = self.balance_fetcher.get_all_balances()
            available_usd = balances.get(CFG.QUOTE, 0.0)
            if notional > available_usd:
                LOG.error(f"Insufficient balance: ${available_usd:.2f} < ${notional:.2f}")
                return {"error": "Insufficient balance"}
        
        # Calculate quantity with rounded stop price
        qty = notional / stop_price
        qty = round_volume(pair, qty)
        
        # Enforce minimum volume
        ok, qty, why = self._enforce_min_volume(pair, qty)
        if not ok:
            LOG.warning("[STOP BUY] %s skipped: %s", pair, why)
            return None
        
        # Actual notional with rounded values
        actual_notional = qty * stop_price
        
        LOG.info(
            f"[STOP BUY] {pair}: qty={qty:.8f}, stop_price=${stop_price:.{price_decimals}f}, notional=${actual_notional:.2f}"
        )
        
        kraken_pair = _to_kraken_pair(pair)
        
        # Format price with EXACT precision required by Kraken
        stop_price_str = f"{stop_price:.{price_decimals}f}"
        
        # Prepare order data with properly formatted values
        data = {
            'ordertype': 'stop-loss',
            'type': 'buy',
            'pair': kraken_pair,
            'volume': f"{qty:.8f}",  # Volume always 8 decimals
            'price': stop_price_str,  # Use formatted string with EXACT decimals
            'oflags': 'fciq',  # Fees in quote currency
        }
        
        LOG.debug(f"[STOP BUY] {pair}: Sending to Kraken with price='{stop_price_str}' ({price_decimals} decimals)")
        
        try:
            res = self.client.private_post("/0/private/AddOrder", data)
            
            if res and 'error' in res and res['error']:
                LOG.error(f"[STOP BUY] Order failed: Kraken error: {res['error']}")
                return res
            
            LOG.info(f"[STOP BUY] Order placed successfully: {res}")
            
            # Track trailing order if requested
            if res and 'result' in res and 'txid' in res['result'] and trailing:
                txid = res['result']['txid'][0] if isinstance(res['result']['txid'], list) else res['result']['txid']
                initial_high = stop_price / (1 - offset_pct)
                self.trailing_buy_orders[txid] = {'pair': pair, 'high': initial_high, 'offset_pct': offset_pct}
                LOG.info(f"[TRAILING] Started trailing buy stop for {pair} with high=${initial_high:.2f}, offset={offset_pct*100:.1f}%")
            
            return res
            
        except Exception as e:
            LOG.error(f"[STOP BUY] Exception: {e}")
            return {"error": str(e)}

    def market_sell(
        self,
        pair: str,
        qty: float,
        reason: str = "",
        cost_basis: Optional[float] = None,
        validate_profit: bool = False
    ):
        LOG.info(f"[SELL] {pair}: qty={qty:.8f} | reason={reason}")
        bid_price = self._get_current_price(pair, side="bid")
        if bid_price == 0:
            LOG.error(f"Failed to get price for {pair}")
            return {"error": "Price fetch failed"}
        if validate_profit and cost_basis is not None and cost_basis > 0:
            is_profitable, details = self.validate_sell_profitability(
                cost_basis, bid_price, CFG.TARGET_PROFIT_PCT
            )
            if not is_profitable:
                LOG.warning(
                    f"[SELL] {pair}: Below profit threshold! Actual={details['actual_profit_pct']*100:.2f}%, "
                    f"Target={details['min_profit_pct']*100:.2f}%"
                )
        proceeds = self.calculate_sell_proceeds(qty, bid_price)
        LOG.info(
            f"[SELL] {pair}: qty={qty:.8f}, price=${bid_price:.2f}, gross=${proceeds['gross_proceeds']:.2f}, "
            f"fee=${proceeds['fee_amount']:.2f}, net=${proceeds['net_proceeds']:.2f}"
        )
        if cost_basis is not None and cost_basis > 0:
            total_cost = qty * max(cost_basis, 0.0)
            profit = proceeds['net_proceeds'] - total_cost
            profit_pct = (profit / total_cost * 100) if total_cost > 0 else 0
            LOG.info(
                f"[SELL] {pair}: cost_basis=${cost_basis:.2f}, total_cost=${total_cost:.2f}, "
                f"profit={profit:+.2f} ({profit_pct:+.2f}%)"
            )
        _, qty = self._round_pair(pair, None, qty)
        ok, qty, why = self._enforce_min_volume(pair, qty)
        if not ok:
            LOG.warning("[SELL] %s skipped: %s", pair, why)
            return None
        kraken_pair = _to_kraken_pair(pair)
        data = {
            'ordertype': 'market',
            'type': 'sell',
            'pair': kraken_pair,
            'volume': f"{qty:.10f}",
        }
        try:
            res = self.client.private_post("/0/private/AddOrder", data)
            LOG.info(f"[SELL] Order placed: {res}")
            return res
        except Exception as e:
            LOG.error(f"[SELL] Order failed: {e}")
            return {"error": str(e)}

    def edit_stop_price(self, txid: str, new_stop_price: float, pair: str):
        new_stop_price_rounded, _ = self._round_pair(pair, new_stop_price, None)
        data = {'txid': txid, 'price': f"{new_stop_price_rounded:.10f}"}
        try:
            res = self.client.private_post("/0/private/EditOrder", data)
            LOG.info(f"[EDIT ORDER] Updated stop price for {txid} to ${new_stop_price_rounded:.2f}: {res}")
            return res
        except Exception as e:
            LOG.error(f"[EDIT ORDER] Failed to update stop price for {txid}: {e}")
            return {"error": str(e)}

    def cancel_order(self, txid: str):
        """Cancel an order by txid."""
        data = {'txid': txid}
        try:
            res = self.client.private_post("/0/private/CancelOrder", data)
            LOG.info(f"[CANCEL] Order {txid} canceled: {res}")
            return res
        except Exception as e:
            LOG.error(f"[CANCEL] Failed to cancel order {txid}: {e}")
            return {"error": str(e)}

    def update_trailing_buy_stops(self):
        to_remove = []
        for txid, info in list(self.trailing_buy_orders.items()):
            pair = info['pair']
            current_price = self._get_current_price(pair, side="bid")  # use bid for trailing high
            if current_price > info['high']:
                info['high'] = current_price
                new_stop = info['high'] * (1 - info['offset_pct'])
                edit_res = self.edit_stop_price(txid, new_stop, info['pair'])
                if edit_res and 'error' not in edit_res:
                    LOG.info(f"[TRAILING] Updated buy stop for {pair}: high=${info['high']:.2f}, new_stop=${new_stop:.2f}")
                else:
                    LOG.warning(f"[TRAILING] Failed to update buy stop for {pair}: {edit_res}")
                    # Optionally remove if edit fails, but for now keep
        # Note: In production, check if order is still open and remove if filled or canceled

    # --- NK-TRADING-AMOUNT-FIX: Risk Reduction Methods ---
    
    def close_position_market(
        self,
        pair: str,
        qty: Optional[float] = None,
        reason: str = "risk_reduction"
    ) -> Dict:
        """
        Close a position immediately using a market sell order.
        
        Args:
            pair: Trading pair
            qty: Quantity to sell (if None, uses full balance)
            reason: Reason for closure
        
        Returns:
            Order result dictionary
        """
        try:
            if qty is None:
                # Get full balance for the pair
                base = pair.split("/")[0]
                balances = self.balance_fetcher.get_all_balances()
                qty = float(balances.get(base, 0.0))
            
            if qty <= 0:
                LOG.warning(f"[CLOSE] No position to close for {pair}")
                return {"error": "no_position"}
            
            LOG.info(f"[CLOSE] Closing position {pair}: qty={qty}, reason={reason}")
            
            # Execute market sell
            result = self.market_sell(pair, qty, reason=reason)
            
            return result
            
        except Exception as e:
            LOG.error(f"[CLOSE] Error closing position {pair}: {e}")
            return {"error": str(e)}

    def cancel_stop_buys(
        self,
        pair: Optional[str] = None,
        max_to_cancel: Optional[int] = None,
        reason: str = "risk_reduction"
    ) -> Dict:
        """
        Cancel stop-buy orders to reduce exposure.
        
        Args:
            pair: If specified, only cancel orders for this pair (if None, cancel all)
            max_to_cancel: Maximum number of orders to cancel (if None, cancel all matching)
            reason: Reason for cancellation
        
        Returns:
            Dictionary with cancellation results
        """
        try:
            # Fetch open orders
            response = self.client.private_post("/0/private/OpenOrders", {"trades": False})
            
            if response.get("error"):
                LOG.error(f"[CANCEL_STOP_BUYS] API error: {response['error']}")
                return {"error": response["error"], "cancelled": 0}
            
            all_orders = response.get("open", {})
            
            # Filter for stop-loss buy orders
            matching_orders = []
            for txid, order in all_orders.items():
                descr = order.get("descr", {})
                
                # Check if stop-loss buy
                if descr.get("ordertype") != "stop-loss" or descr.get("type") != "buy":
                    continue
                
                # Check pair filter
                if pair is not None:
                    order_pair = descr.get("pair", "")
                    # Normalize pair names for comparison
                    if not self._pairs_match(pair, order_pair):
                        continue
                
                matching_orders.append(txid)
            
            # Apply max limit
            if max_to_cancel is not None:
                matching_orders = matching_orders[:max_to_cancel]
            
            # Cancel orders
            cancelled = 0
            failed = 0
            
            for txid in matching_orders:
                try:
                    LOG.info(f"[CANCEL_STOP_BUYS] Cancelling order {txid}: reason={reason}")
                    result = self.cancel_order(txid)
                    
                    if result and "error" not in result:
                        cancelled += 1
                    else:
                        failed += 1
                        LOG.warning(f"[CANCEL_STOP_BUYS] Failed to cancel {txid}: {result}")
                        
                except Exception as e:
                    failed += 1
                    LOG.error(f"[CANCEL_STOP_BUYS] Exception cancelling {txid}: {e}")
            
            result = {
                "cancelled": cancelled,
                "failed": failed,
                "total_found": len(matching_orders),
                "reason": reason
            }
            
            LOG.info(
                f"[CANCEL_STOP_BUYS] Complete: {cancelled} cancelled, {failed} failed, "
                f"{len(matching_orders)} total (pair={pair or 'all'}, reason={reason})"
            )
            
            return result
            
        except Exception as e:
            LOG.error(f"[CANCEL_STOP_BUYS] Error: {e}")
            return {"error": str(e), "cancelled": 0}
    
    def _pairs_match(self, pair1: str, pair2: str) -> bool:
        """Check if two pair names match (handles Kraken format differences)."""
        # Normalize both pairs
        p1 = pair1.replace("/", "").upper()
        p2 = pair2.replace("/", "").upper()
        
        # Handle XBT/BTC equivalence
        p1 = p1.replace("XBT", "BTC")
        p2 = p2.replace("XBT", "BTC")
        
        # Remove X prefix if present
        if p1.startswith("X") and len(p1) > 3:
            p1 = p1[1:]
        if p2.startswith("X") and len(p2) > 3:
            p2 = p2[1:]
        
        return p1 == p2