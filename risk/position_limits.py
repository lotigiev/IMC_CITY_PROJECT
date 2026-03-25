"""Position limits and tracking.

Manages position limits per product and overall portfolio exposure.
Prevents concentration risk and enforces risk parameters.
"""

from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Optional

from config.settings import MAX_POSITION_PER_PRODUCT, MAX_TOTAL_EXPOSURE
from config.products import PRODUCT_SPECS
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PositionLimit:
    """Position limit configuration for a product."""
    symbol: str
    max_long: int  # Maximum long position
    max_short: int  # Maximum short position (negative)
    max_absolute: int  # Maximum absolute position


class PositionTracker:
    """Thread-safe position tracking with limits enforcement."""

    def __init__(self):
        """Initialize position tracker."""
        self._positions: Dict[str, int] = {}
        self._limits: Dict[str, PositionLimit] = {}
        self._lock = Lock()

        # Initialize limits for all products
        for symbol in PRODUCT_SPECS.keys():
            self.set_limit(
                symbol,
                max_long=MAX_POSITION_PER_PRODUCT,
                max_short=-MAX_POSITION_PER_PRODUCT,
            )

    def set_limit(
        self,
        symbol: str,
        max_long: Optional[int] = None,
        max_short: Optional[int] = None,
        max_absolute: Optional[int] = None,
    ):
        """Set position limit for a product.

        Args:
            symbol: Product symbol
            max_long: Maximum long position
            max_short: Maximum short position (negative value)
            max_absolute: Maximum absolute position
        """
        with self._lock:
            current = self._limits.get(symbol)

            if current is None:
                # Create new limit
                self._limits[symbol] = PositionLimit(
                    symbol=symbol,
                    max_long=max_long or MAX_POSITION_PER_PRODUCT,
                    max_short=max_short or -MAX_POSITION_PER_PRODUCT,
                    max_absolute=max_absolute or MAX_POSITION_PER_PRODUCT,
                )
            else:
                # Update existing limit
                if max_long is not None:
                    current.max_long = max_long
                if max_short is not None:
                    current.max_short = max_short
                if max_absolute is not None:
                    current.max_absolute = max_absolute

            logger.info(
                f"Position limit set for {symbol}: "
                f"long={self._limits[symbol].max_long}, "
                f"short={self._limits[symbol].max_short}, "
                f"abs={self._limits[symbol].max_absolute}"
            )

    def update_position(self, symbol: str, position: int):
        """Update position for a product.

        Args:
            symbol: Product symbol
            position: New position (positive = long, negative = short)
        """
        with self._lock:
            old_position = self._positions.get(symbol, 0)
            self._positions[symbol] = position

            if position != old_position:
                logger.debug(f"Position updated: {symbol} {old_position} → {position}")

    def adjust_position(self, symbol: str, delta: int):
        """Adjust position by a delta.

        Args:
            symbol: Product symbol
            delta: Change in position (positive for buy, negative for sell)
        """
        with self._lock:
            current = self._positions.get(symbol, 0)
            self._positions[symbol] = current + delta
            logger.debug(f"Position adjusted: {symbol} {current} → {self._positions[symbol]} (delta {delta:+.0f})")

    def get_position(self, symbol: str) -> int:
        """Get current position for a product.

        Args:
            symbol: Product symbol

        Returns:
            Current position (0 if not found)
        """
        with self._lock:
            return self._positions.get(symbol, 0)

    def get_all_positions(self) -> Dict[str, int]:
        """Get all current positions.

        Returns:
            Dict mapping symbol to position
        """
        with self._lock:
            return self._positions.copy()

    def get_position_limit(self, symbol: str) -> Optional[PositionLimit]:
        """Get position limit for a product.

        Args:
            symbol: Product symbol

        Returns:
            PositionLimit object or None if not set
        """
        with self._lock:
            return self._limits.get(symbol)

    def get_total_exposure(self) -> int:
        """Get total absolute exposure across all positions.

        Returns:
            Sum of absolute positions
        """
        with self._lock:
            return sum(abs(pos) for pos in self._positions.values())

    def can_trade(
        self,
        symbol: str,
        side: str,
        volume: int,
        current_position: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Check if a trade is allowed given position limits.

        Args:
            symbol: Product symbol
            side: BUY or SELL
            volume: Trade volume
            current_position: Current position (if None, use tracked position)

        Returns:
            Tuple of (allowed, reason)
        """
        with self._lock:
            # Get current position
            if current_position is None:
                current_position = self._positions.get(symbol, 0)

            # Calculate new position after trade
            if side == "BUY":
                new_position = current_position + volume
            elif side == "SELL":
                new_position = current_position - volume
            else:
                return False, f"Invalid side: {side}"

            # Get limit
            limit = self._limits.get(symbol)
            if limit is None:
                return False, f"No limit configured for {symbol}"

            # Check position limits
            if new_position > limit.max_long:
                return (
                    False,
                    f"Would exceed max long: {new_position} > {limit.max_long}",
                )

            if new_position < limit.max_short:
                return (
                    False,
                    f"Would exceed max short: {new_position} < {limit.max_short}",
                )

            if abs(new_position) > limit.max_absolute:
                return (
                    False,
                    f"Would exceed max absolute: {abs(new_position)} > {limit.max_absolute}",
                )

            # Check total exposure
            current_exposure = sum(abs(pos) for pos in self._positions.values())
            new_exposure = current_exposure - abs(current_position) + abs(new_position)

            if new_exposure > MAX_TOTAL_EXPOSURE:
                return (
                    False,
                    f"Would exceed max total exposure: {new_exposure} > {MAX_TOTAL_EXPOSURE}",
                )

            return True, "OK"

    def get_available_buy_size(self, symbol: str) -> int:
        """Get maximum volume that can be bought.

        Args:
            symbol: Product symbol

        Returns:
            Maximum buy volume
        """
        with self._lock:
            current_position = self._positions.get(symbol, 0)
            limit = self._limits.get(symbol)

            if limit is None:
                return 0

            # Max we can buy without exceeding long limit
            max_from_long = limit.max_long - current_position

            # Max we can buy without exceeding absolute limit
            max_from_abs = limit.max_absolute - current_position

            # Max we can buy without exceeding total exposure
            current_exposure = sum(abs(pos) for pos in self._positions.values())
            remaining_exposure = MAX_TOTAL_EXPOSURE - current_exposure
            max_from_exposure = remaining_exposure + abs(current_position)

            return max(0, min(max_from_long, max_from_abs, max_from_exposure))

    def get_available_sell_size(self, symbol: str) -> int:
        """Get maximum volume that can be sold.

        Args:
            symbol: Product symbol

        Returns:
            Maximum sell volume
        """
        with self._lock:
            current_position = self._positions.get(symbol, 0)
            limit = self._limits.get(symbol)

            if limit is None:
                return 0

            # Max we can sell without exceeding short limit (remember short is negative)
            max_from_short = current_position - limit.max_short

            # Max we can sell without exceeding absolute limit
            max_from_abs = limit.max_absolute + current_position

            # Max we can sell without exceeding total exposure
            current_exposure = sum(abs(pos) for pos in self._positions.values())
            remaining_exposure = MAX_TOTAL_EXPOSURE - current_exposure
            max_from_exposure = remaining_exposure + abs(current_position)

            return max(0, min(max_from_short, max_from_abs, max_from_exposure))

    def print_summary(self):
        """Print position summary."""
        with self._lock:
            logger.debug("=" * 60)
            logger.debug("POSITION SUMMARY")
            logger.debug("=" * 60)

            total_exposure = 0

            for symbol in sorted(self._positions.keys()):
                pos = self._positions[symbol]
                if pos == 0:
                    continue

                limit = self._limits.get(symbol)
                total_exposure += abs(pos)

                logger.debug(
                    f"{symbol:12s}: {pos:+6.0f}  "
                    f"(limit: {limit.max_short:+6.0f} to {limit.max_long:+6.0f})"
                )

            logger.debug("-" * 60)
            logger.debug(f"Total Exposure: {total_exposure:.0f} / {MAX_TOTAL_EXPOSURE}")
            logger.debug("=" * 60)


# Global position tracker instance
_global_tracker: Optional[PositionTracker] = None


def get_position_tracker() -> PositionTracker:
    """Get global position tracker instance.

    Returns:
        Global PositionTracker instance
    """
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = PositionTracker()
    return _global_tracker
