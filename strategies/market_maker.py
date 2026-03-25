"""Market making strategy with inventory management"""

from typing import Dict, List, Optional
from bot_template import OrderRequest, OrderBook, Trade, Side
from strategies.base_strategy import BaseStrategy
from config.products import get_product_spec
from risk.position_limits import get_position_tracker
from utils.helpers import floor_to_tick, ceil_to_tick, calculate_mid
from utils.logger import get_logger

logger = get_logger(__name__)


class MarketMaker(BaseStrategy):
    """Two-sided market maker with inventory skew."""

    def __init__(
        self,
        products: List[str],
        width: float = 5.0,
        volume: int = 5,
        max_inventory_skew: float = 0.5,
        fair_value_provider: Optional[callable] = None,
    ):
        super().__init__("MarketMaker")
        self.products = products
        self.width = width
        self.volume = volume
        self.max_inventory_skew = max_inventory_skew
        self.fair_value_provider = fair_value_provider
        self.position_tracker = get_position_tracker()

        self._last_quotes: Dict[str, tuple[float, float]] = {}

    def on_orderbook(self, orderbook: OrderBook) -> List[OrderRequest]:
        """Quote two-sided markets."""
        if not self.enabled or orderbook.product not in self.products:
            return []

        spec = get_product_spec(orderbook.product)
        tick = spec.tick_size

        # Calculate fair value
        fair = self._get_fair_value(orderbook)

        # Get position and limit
        position = self.position_tracker.get_position(orderbook.product)
        limit = self.position_tracker.get_position_limit(orderbook.product)
        max_pos = limit.max_absolute if limit else 100

        # Calculate inventory skew (-1 to +1, where +1 = max long, -1 = max short)
        skew = position / max_pos if max_pos > 0 else 0

        # Adjust widths and center based on inventory
        # We shift the 'center' of our spread away from the side we are already heavy on.
        # Shift magnitude: up to 100% of the default width at max skew
        shift = -skew * self.width 
        
        # Base widths - widen the side that adds to our position
        bid_width = self.width * (1 + max(0, skew))
        ask_width = self.width * (1 + max(0, -skew))

        # Calculate quotes around shifted center
        bid_price = floor_to_tick(fair + shift - bid_width, tick)
        ask_price = ceil_to_tick(fair + shift + ask_width, tick)

        # Ensure ask is always > bid
        if ask_price <= bid_price:
            ask_price = bid_price + tick

        # Check if should requote
        last = self._last_quotes.get(orderbook.product)
        if last and abs(last[0] - bid_price) < tick and abs(last[1] - ask_price) < tick:
            return []  # Preserve queue priority

        self._last_quotes[orderbook.product] = (bid_price, ask_price)

        # Adjust volume based on position (asymmetric)
        # When long: reduce bid volume, INCREASE ask volume to work off position
        # When short: INCREASE bid volume, reduce ask volume
        if skew > 0:  # Long position
            bid_vol = max(1, int(self.volume * (1 - skew)))
            ask_vol = int(self.volume * (1 + skew))
        elif skew < 0:  # Short position
            bid_vol = int(self.volume * (1 - skew))
            ask_vol = max(1, int(self.volume * (1 + skew)))
        else:  # Flat
            bid_vol = self.volume
            ask_vol = self.volume

        # LIQUIDITY SNIPER: If we are heavily skewed, check if we can 'take' existing liquidity
        # to flatten instantly, even if we lose a few ticks.
        sniper_orders = []
        if abs(position) > 25:
            if position > 0 and orderbook.buy_orders:
                # We are LONG, can we SELL to existing buyers?
                best_bid = orderbook.buy_orders[0].price
                # Be more patient: only take if bid is within 1.0x width of fair
                if best_bid >= fair - self.width:
                    take_vol = min(abs(position), 10)
                    logger.info(f"🎯 SNIPER SELL: Taking liquidity for {orderbook.product} @ {best_bid}")
                    sniper_orders.append(OrderRequest(orderbook.product, best_bid, Side.SELL, int(take_vol)))
            elif position < 0 and orderbook.sell_orders:
                # We are SHORT, can we BUY from existing sellers?
                best_ask = orderbook.sell_orders[0].price
                if best_ask <= fair + self.width:
                    take_vol = min(abs(position), 10)
                    logger.info(f"🎯 SNIPER BUY: Taking liquidity for {orderbook.product} @ {best_ask}")
                    sniper_orders.append(OrderRequest(orderbook.product, best_ask, Side.BUY, int(take_vol)))

        # Don't quote side if too skewed
        orders = sniper_orders # Prioritize sniper orders
        if skew < self.max_inventory_skew and bid_price > 0:
            orders.append(OrderRequest(orderbook.product, bid_price, Side.BUY, bid_vol))
        if skew > -self.max_inventory_skew and ask_price > bid_price:
            orders.append(OrderRequest(orderbook.product, ask_price, Side.SELL, ask_vol))

        return orders

    def on_trade(self, trade: Trade) -> List[OrderRequest]:
        """No action on trades."""
        return []

    def _get_fair_value(self, orderbook: OrderBook) -> float:
        """Get fair value estimate (Hybrid: Data + Market)."""
        # 1. Get Market Mid
        bids = [o.price for o in orderbook.buy_orders if o.volume > o.own_volume]
        asks = [o.price for o in orderbook.sell_orders if o.volume > o.own_volume]
        
        market_mid = None
        if bids and asks:
            market_mid = (max(bids) + min(asks)) / 2
        elif bids:
            market_mid = max(bids)
        elif asks:
            market_mid = min(asks)

        # 2. Get Theoretical Value from Data
        theo_fv = None
        if self.fair_value_provider:
            theo_fv = self.fair_value_provider(orderbook.product)

        # 3. Blend them (90% market / 10% theory)
        # If we only have one, use it. If we have both, blend them.
        if theo_fv is not None and market_mid is not None:
            # We trust the market mid mostly for current liquidity,
            # but lean 10% towards our data-driven theoretical value.
            return (theo_fv * 0.1) + (market_mid * 0.9)
        elif market_mid is not None:
            return market_mid
        elif theo_fv is not None:
            return theo_fv
        
        # Last resort: starting price
        spec = get_product_spec(orderbook.product)
        return float(spec.starting_price)
