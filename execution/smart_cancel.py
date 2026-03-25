"""Smart order cancellation

Selective cancellation to preserve queue priority when possible.
Only cancels orders when necessary (price changed, inventory too skewed, etc).
"""

from dataclasses import dataclass
from typing import List, Optional, Dict

from bot_template import BaseBot
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OrderState:
    """Tracked state of an order."""
    order_id: str
    product: str
    side: str
    price: float
    volume: int
    timestamp: float


class SmartCanceller:
    """Smart order cancellation logic."""

    def __init__(
        self,
        bot: BaseBot,
        price_change_threshold: float = 1.0,  # Cancel if price changes by this many ticks
        inventory_skew_threshold: float = 0.5,  # Cancel if inventory skew exceeds this
    ):
        self.bot = bot
        self.price_change_threshold = price_change_threshold
        self.inventory_skew_threshold = inventory_skew_threshold

        self._tracked_orders: Dict[str, OrderState] = {}

    def should_cancel_order(
        self,
        order_state: OrderState,
        current_fair_value: float,
        current_position: int,
        position_limit: int,
        tick_size: float,
    ) -> tuple[bool, str]:
        # Check 1: Fair value moved significantly
        price_diff_ticks = abs(order_state.price - current_fair_value) / tick_size

        if price_diff_ticks > self.price_change_threshold:
            return (
                True,
                f"Fair value moved {price_diff_ticks:.1f} ticks from order price",
            )

        # Check 2: Inventory too skewed
        if position_limit > 0:
            inventory_skew = abs(current_position) / position_limit

            if inventory_skew > self.inventory_skew_threshold:
                # Cancel orders that would add to the skewed side
                if (current_position > 0 and order_state.side == "BUY") or \
                   (current_position < 0 and order_state.side == "SELL"):
                    return (
                        True,
                        f"Inventory too skewed: {current_position}/{position_limit}",
                    )

        return False, "OK"

    def get_orders_to_cancel(
        self,
        active_orders: List[dict],
        fair_values: Dict[str, float],
        positions: Dict[str, int],
        position_limits: Dict[str, int],
        tick_sizes: Dict[str, float],
    ) -> List[str]:
        """Determine which active orders should be cancelled.

        Args:
            active_orders: List of active orders from exchange
            fair_values: Dict mapping symbol to fair value
            positions: Dict mapping symbol to current position
            position_limits: Dict mapping symbol to position limit
            tick_sizes: Dict mapping symbol to tick size

        Returns:
            List of order IDs to cancel
        """
        to_cancel = []

        for order in active_orders:
            order_id = order["id"]
            product = order["product"]
            side = order["side"]
            price = order["price"]

            # Get current state
            fair_value = fair_values.get(product)
            position = positions.get(product, 0)
            limit = position_limits.get(product, 100)
            tick_size = tick_sizes.get(product, 1.0)

            if fair_value is None:
                logger.debug(f"No fair value for {product}, keeping order {order_id}")
                continue

            # Create order state
            order_state = OrderState(
                order_id=order_id,
                product=product,
                side=side,
                price=price,
                volume=order["volume"],
                timestamp=0.0,  # Not tracked
            )

            # Check if should cancel
            should_cancel, reason = self.should_cancel_order(
                order_state=order_state,
                current_fair_value=fair_value,
                current_position=position,
                position_limit=limit,
                tick_size=tick_size,
            )

            if should_cancel:
                logger.info(
                    f"Cancelling order {order_id[:8]}: {product} {side} "
                    f"{order['volume']}@{price} - {reason}"
                )
                to_cancel.append(order_id)

        return to_cancel

    def cancel_stale_orders(
        self,
        fair_values: Dict[str, float],
        positions: Dict[str, int],
        position_limits: Dict[str, int],
        tick_sizes: Dict[str, float],
    ) -> List[str]:
        """Cancel orders that are stale based on current market state.

        Args:
            fair_values: Current fair values
            positions: Current positions
            position_limits: Position limits
            tick_sizes: Tick sizes

        Returns:
            List of cancelled order IDs
        """
        # Get active orders
        try:
            active_orders = self.bot.get_orders()
        except Exception as e:
            logger.error(f"Failed to get active orders: {e}")
            return []

        # Determine which to cancel
        to_cancel = self.get_orders_to_cancel(
            active_orders=active_orders,
            fair_values=fair_values,
            positions=positions,
            position_limits=position_limits,
            tick_sizes=tick_sizes,
        )

        # Cancel them
        cancelled = []
        for order_id in to_cancel:
            try:
                self.bot.cancel_order(order_id)
                cancelled.append(order_id)
            except Exception as e:
                logger.error(f"Failed to cancel order {order_id}: {e}")

        if cancelled:
            logger.info(f"Cancelled {len(cancelled)} stale orders")

        return cancelled

    def should_requote(
        self,
        product: str,
        old_bid: Optional[float],
        old_ask: Optional[float],
        new_bid: float,
        new_ask: float,
        tick_size: float,
    ) -> bool:
        """Determine if quotes should be refreshed """
        # Always quote if no existing quotes
        if old_bid is None or old_ask is None:
            return True

        # Check if prices changed by more than threshold
        bid_change_ticks = abs(new_bid - old_bid) / tick_size
        ask_change_ticks = abs(new_ask - old_ask) / tick_size

        if bid_change_ticks > self.price_change_threshold or \
           ask_change_ticks > self.price_change_threshold:
            logger.debug(
                f"{product}: Requoting - bid moved {bid_change_ticks:.1f} ticks, "
                f"ask moved {ask_change_ticks:.1f} ticks"
            )
            return True

        # Prices haven't changed enough - preserve queue priority
        logger.debug(f"{product}: Keeping existing quotes to preserve queue priority")
        return False


# Global smart canceller instance
_global_canceller: Optional[SmartCanceller] = None


def get_smart_canceller(bot: Optional[BaseBot] = None) -> SmartCanceller:
    """Get global smart canceller instance.

    Args:
        bot: BaseBot instance (required for first call)

    Returns:
        Global SmartCanceller instance
    """
    global _global_canceller
    if _global_canceller is None:
        if bot is None:
            raise ValueError("Must provide bot for first call to get_smart_canceller()")
        _global_canceller = SmartCanceller(bot)
    return _global_canceller


def reset_smart_canceller():
    """Reset global smart canceller (for testing)."""
    global _global_canceller
    _global_canceller = None
