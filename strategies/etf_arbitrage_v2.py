"""ETF arbitrage strategy
"""

import time
from typing import Dict, List, Optional
from bot_template import OrderRequest, OrderBook, Trade, Side
from strategies.base_strategy import BaseStrategy
from config.products import LON_ETF_COMPONENTS
from risk.position_limits import get_position_tracker
from utils.logger import get_logger

logger = get_logger(__name__)


class ETFArbitrageV2(BaseStrategy):
    """Improved ETF arbitrage with better execution."""

    def __init__(
        self,
        min_edge: float = 10.0,
        volume: int = 5,
        fill_check_delay: float = 0.5,
        max_edge_for_unwind: float = 50.0,
    ):
        super().__init__("ETFArbitrageV2")
        self.min_edge = min_edge
        self.volume = volume
        self.fill_check_delay = fill_check_delay  # Time to wait for fills
        self.max_edge_for_unwind = max_edge_for_unwind  # Max loss to unwind partial fills

        self._last_books: Dict[str, OrderBook] = {}
        self._last_arb_time = 0
        self._arb_cooldown = 3.0  # Reduced from 5s (but still conservative)
        self.position_tracker = get_position_tracker()

        # Track pending arb trades
        self._pending_arb: Optional[Dict] = None

    def on_orderbook(self, orderbook: OrderBook) -> List[OrderRequest]:
        """Check for arbitrage opportunities."""
        if not self.enabled:
            return []

        # Update book cache
        self._last_books[orderbook.product] = orderbook

        # Check if we have all components + ETF
        required = LON_ETF_COMPONENTS + ["LON_ETF"]
        if not all(p in self._last_books for p in required):
            return []

        if self._pending_arb is not None:
            if time.time() - self._pending_arb["timestamp"] > 2.0:
                logger.warning("ETF arb timed out with no fills, unwinding")
                unwind = self._generate_unwind_orders()
                self._pending_arb = None
                return unwind
            return []

        # Cooldown to avoid rapid-fire arb attempts
        now = time.time()
        if now - self._last_arb_time < self._arb_cooldown:
            return []

        # Get best prices
        etf_book = self._last_books["LON_ETF"]
        etf_bid = self._get_best_bid(etf_book)
        etf_ask = self._get_best_ask(etf_book)

        comp_bids = {p: self._get_best_bid(self._last_books[p]) for p in LON_ETF_COMPONENTS}
        comp_asks = {p: self._get_best_ask(self._last_books[p]) for p in LON_ETF_COMPONENTS}

        if None in [etf_bid, etf_ask] or None in comp_bids.values() or None in comp_asks.values():
            return []

        # Calculate theoretical ETF value
        theo_bid = sum(comp_bids.values())  # Sell components at bid
        theo_ask = sum(comp_asks.values())  # Buy components at ask

        # Calculate true edge (accounting for crossing spreads)
        # When buying ETF and selling components, we pay ETF ask and receive component bids
        edge_buy_etf = theo_bid - etf_ask

        # When selling ETF and buying components, we receive ETF bid and pay component asks
        edge_sell_etf = etf_bid - theo_ask

        orders = []
        arb_direction = None

        # Arb 1: ETF too cheap (buy ETF, sell components)
        if edge_buy_etf > self.min_edge:
            # Check position limits before trading
            if not self._check_position_limits("BUY"):
                logger.debug("ETF arb skipped: position limits would be exceeded")
                return []

            logger.info(
                f"🎯 ETF ARB: BUY ETF @ {etf_ask:.2f}, SELL components @ {theo_bid:.2f}, "
                f"edge={edge_buy_etf:.2f}"
            )
            orders.append(OrderRequest("LON_ETF", etf_ask, Side.BUY, self.volume))
            for comp in LON_ETF_COMPONENTS:
                orders.append(OrderRequest(comp, comp_bids[comp], Side.SELL, self.volume))

            arb_direction = "BUY_ETF"
            self._last_arb_time = now

        # Arb 2: ETF too expensive (sell ETF, buy components)
        elif edge_sell_etf > self.min_edge:
            # Check position limits before trading
            if not self._check_position_limits("SELL"):
                logger.debug("ETF arb skipped: position limits would be exceeded")
                return []

            logger.info(
                f"🎯 ETF ARB: SELL ETF @ {etf_bid:.2f}, BUY components @ {theo_ask:.2f}, "
                f"edge={edge_sell_etf:.2f}"
            )
            orders.append(OrderRequest("LON_ETF", etf_bid, Side.SELL, self.volume))
            for comp in LON_ETF_COMPONENTS:
                orders.append(OrderRequest(comp, comp_asks[comp], Side.BUY, self.volume))

            arb_direction = "SELL_ETF"
            self._last_arb_time = now

        # Store pending arb for verification
        if orders:
            self._pending_arb = {
                "direction": arb_direction,
                "orders": orders,
                "timestamp": now,
                "expected_fills": {o.product: 0 for o in orders},
            }

        return orders

    def on_trade(self, trade: Trade) -> List[OrderRequest]:
        """Track fills for pending arb and unwind if partial fill.

        NOTE: This is called by bot for ALL trades, not just arb trades.
        We need to track if this trade is part of our pending arb.
        """
        if self._pending_arb is None:
            return []

        # Check if this trade is one of our arb legs
        pending = self._pending_arb
        if trade.product in pending["expected_fills"]:
            # Update fill count
            pending["expected_fills"][trade.product] += trade.volume
            logger.debug(
                f"Arb leg filled: {trade.product} {trade.volume}x @ {trade.price:.2f}"
            )

        # Check if all legs are filled
        all_filled = all(
            filled >= self.volume
            for filled in pending["expected_fills"].values()
        )

        if all_filled:
            logger.info("✅ ETF ARB COMPLETE: All legs filled successfully")
            self._pending_arb = None
            return []

        # Check timeout - if arb was sent >2s ago and not all filled, unwind
        if time.time() - pending["timestamp"] > 2.0:
            logger.warning(
                "⚠️ ETF ARB PARTIAL FILL DETECTED - Unwinding filled legs"
            )
            unwind_orders = self._generate_unwind_orders()
            self._pending_arb = None  # Clear pending
            return unwind_orders

        return []

    def _generate_unwind_orders(self) -> List[OrderRequest]:
        """Generate orders to flatten partial fills from failed arb.

        Returns:
            List of orders to unwind the position
        """
        if self._pending_arb is None:
            return []

        unwind_orders = []
        filled_volumes = self._pending_arb["expected_fills"]

        # For each product that filled, send opposite order
        for product, filled_vol in filled_volumes.items():
            if filled_vol == 0:
                continue

            # Get current market to determine unwind price
            book = self._last_books.get(product)
            if not book:
                logger.error(f"No orderbook for {product}, cannot unwind!")
                continue

            # Determine unwind side and price
            if self._pending_arb["direction"] == "BUY_ETF":
                # We bought ETF / sold components, so unwind = sell ETF / buy components
                if product == "LON_ETF":
                    # Sell ETF at bid
                    price = self._get_best_bid(book)
                    side = Side.SELL
                else:
                    # Buy component at ask
                    price = self._get_best_ask(book)
                    side = Side.BUY
            else:
                # We sold ETF / bought components, so unwind = buy ETF / sell components
                if product == "LON_ETF":
                    # Buy ETF at ask
                    price = self._get_best_ask(book)
                    side = Side.BUY
                else:
                    # Sell component at bid
                    price = self._get_best_bid(book)
                    side = Side.SELL

            if price is None:
                logger.error(f"No price for {product}, cannot unwind!")
                continue

            # Calculate expected loss from unwind
            # (we're paying the spread to get out)
            logger.warning(
                f"Unwinding {product}: {side} {int(filled_vol)}x @ {price:.2f}"
            )
            unwind_orders.append(OrderRequest(product, price, side, int(filled_vol)))

        return unwind_orders

    def _get_best_bid(self, book: OrderBook) -> Optional[float]:
        """Get best non-own bid."""
        bids = [o.price for o in book.buy_orders if o.volume > o.own_volume]
        return max(bids) if bids else None

    def _get_best_ask(self, book: OrderBook) -> Optional[float]:
        """Get best non-own ask."""
        asks = [o.price for o in book.sell_orders if o.volume > o.own_volume]
        return min(asks) if asks else None

    def _check_position_limits(self, etf_side: str) -> bool:
        """Check if arb trade would exceed position limits.

        Args:
            etf_side: "BUY" or "SELL" for the ETF leg

        Returns:
            True if trade is safe, False if would exceed limits
        """
        # Get current positions
        etf_pos = self.position_tracker.get_position("LON_ETF")
        comp_positions = {p: self.position_tracker.get_position(p) for p in LON_ETF_COMPONENTS}

        # Get limits
        etf_limit = self.position_tracker.get_position_limit("LON_ETF")
        max_pos = etf_limit.max_absolute if etf_limit else 100

        # Simulate trade impact
        if etf_side == "BUY":
            new_etf_pos = etf_pos + self.volume
            # Components will be sold
            new_comp_positions = {p: comp_positions[p] - self.volume for p in LON_ETF_COMPONENTS}
        else:
            new_etf_pos = etf_pos - self.volume
            # Components will be bought
            new_comp_positions = {p: comp_positions[p] + self.volume for p in LON_ETF_COMPONENTS}

        # Check if any position would exceed 70% of limit (conservative)
        if abs(new_etf_pos) > max_pos * 0.7:
            logger.debug(f"ETF arb blocked: ETF position would be {new_etf_pos} (limit {max_pos})")
            return False

        for comp, new_pos in new_comp_positions.items():
            if abs(new_pos) > max_pos * 0.7:
                logger.debug(f"ETF arb blocked: {comp} position would be {new_pos} (limit {max_pos})")
                return False

        return True
