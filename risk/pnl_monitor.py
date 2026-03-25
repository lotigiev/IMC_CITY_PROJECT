"""P&L monitoring and stop-loss management.

Tracks realized and unrealized P&L in real-time.
Implements stop-loss and take-profit logic.
"""

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Optional, Callable

from config.settings import MAX_LOSS_THRESHOLD
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Position:
    """Position state for P&L calculation."""
    symbol: str
    volume: int  # Current position (positive = long, negative = short)
    entry_price: float  # Weighted average entry price
    realized_pnl: float = 0.0  # Realized P&L from closed trades


class PnLMonitor:
    """Real-time P&L monitoring with stop-loss."""

    def __init__(
        self,
        max_loss: float = MAX_LOSS_THRESHOLD,
        stop_loss_callback: Optional[Callable[[], None]] = None,
    ):
        """Initialize P&L monitor.

        Args:
            max_loss: Maximum loss threshold (negative value)
            stop_loss_callback: Function to call when stop-loss triggered
        """
        self.max_loss = max_loss
        self.stop_loss_callback = stop_loss_callback

        self._positions: Dict[str, Position] = {}
        self._lock = Lock()

        self._total_realized_pnl: float = 0.0
        self._stop_loss_triggered: bool = False
        self._high_water_mark: float = 0.0

        logger.info(f"P&L monitor initialized with max loss: {max_loss}")

    def update_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        volume: int,
        timestamp: Optional[float] = None,
        current_prices: Optional[Dict[str, float]] = None,
    ) -> float:
        """Update position after a trade.

        Args:
            symbol: Product symbol
            side: BUY or SELL
            price: Trade price
            volume: Trade volume
            timestamp: Trade timestamp (optional)
            current_prices: Current market prices for stop-loss check (optional)

        Returns:
            Realized P&L from this trade
        """
        with self._lock:
            # Get or create position
            if symbol not in self._positions:
                self._positions[symbol] = Position(
                    symbol=symbol,
                    volume=0,
                    entry_price=0.0,
                    realized_pnl=0.0,
                )

            pos = self._positions[symbol]
            realized_pnl = 0.0

            # Calculate new position
            if side == "BUY":
                new_volume = pos.volume + volume

                if pos.volume < 0:
                    # Closing short position (realize P&L)
                    close_volume = min(volume, abs(pos.volume))
                    realized_pnl = (pos.entry_price - price) * close_volume
                    pos.realized_pnl += realized_pnl
                    self._total_realized_pnl += realized_pnl

                    logger.debug(
                        f"Closed {close_volume} short {symbol} @ {price:.2f}, "
                        f"realized P&L: {realized_pnl:+.2f}"
                    )

                # Update entry price (weighted average for new longs)
                if new_volume > pos.volume and new_volume > 0:
                    # Adding to long or opening new long
                    added_volume = max(0, new_volume - max(0, pos.volume))
                    if added_volume > 0:
                        old_value = max(0, pos.volume) * pos.entry_price
                        new_value = added_volume * price
                        pos.entry_price = (old_value + new_value) / new_volume

                pos.volume = new_volume

            elif side == "SELL":
                new_volume = pos.volume - volume

                if pos.volume > 0:
                    # Closing long position (realize P&L)
                    close_volume = min(volume, pos.volume)
                    realized_pnl = (price - pos.entry_price) * close_volume
                    pos.realized_pnl += realized_pnl
                    self._total_realized_pnl += realized_pnl

                    logger.debug(
                        f"Closed {close_volume} long {symbol} @ {price:.2f}, "
                        f"realized P&L: {realized_pnl:+.2f}"
                    )

                # Update entry price (weighted average for new shorts)
                if new_volume < pos.volume and new_volume < 0:
                    # Adding to short or opening new short
                    added_volume = max(0, min(0, pos.volume) - new_volume)
                    if added_volume > 0:
                        old_value = abs(min(0, pos.volume)) * pos.entry_price
                        new_value = added_volume * price
                        pos.entry_price = (old_value + new_value) / abs(new_volume)

                pos.volume = new_volume

            # Check if position closed
            if pos.volume == 0:
                pos.entry_price = 0.0

            # Log trade
            logger.info(
                f"Trade: {side} {volume}x {symbol} @ {price:.2f}, "
                f"realized P&L: {realized_pnl:+.2f}, "
                f"position: {pos.volume:+.0f}"
            )

            # Check stop-loss after each trade (with current prices if available)
            self._check_stop_loss(current_prices)

            return realized_pnl

    def get_unrealized_pnl(
        self,
        current_prices: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate unrealized P&L for all positions.

        Args:
            current_prices: Dict mapping symbol to current market price

        Returns:
            Dict mapping symbol to unrealized P&L
        """
        with self._lock:
            unrealized = {}

            for symbol, pos in self._positions.items():
                if pos.volume == 0:
                    unrealized[symbol] = 0.0
                    continue

                current_price = current_prices.get(symbol)
                if current_price is None:
                    logger.warning(f"No current price for {symbol}, using entry price")
                    current_price = pos.entry_price

                if pos.volume > 0:
                    # Long position
                    unrealized[symbol] = (current_price - pos.entry_price) * pos.volume
                else:
                    # Short position
                    unrealized[symbol] = (pos.entry_price - current_price) * abs(pos.volume)

            return unrealized

    def get_total_unrealized_pnl(self, current_prices: Dict[str, float]) -> float:
        """Get total unrealized P&L.

        Args:
            current_prices: Dict mapping symbol to current price

        Returns:
            Total unrealized P&L
        """
        unrealized = self.get_unrealized_pnl(current_prices)
        return sum(unrealized.values())

    def get_total_pnl(self, current_prices: Dict[str, float]) -> float:
        """Get total P&L (realized + unrealized).

        Args:
            current_prices: Dict mapping symbol to current price

        Returns:
            Total P&L
        """
        with self._lock:
            unrealized = sum(self.get_unrealized_pnl(current_prices).values())
            return self._total_realized_pnl + unrealized

    def get_total_realized_pnl(self) -> float:
        """Get total realized P&L.

        Returns:
            Total realized P&L
        """
        with self._lock:
            return self._total_realized_pnl

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a symbol.

        Args:
            symbol: Product symbol

        Returns:
            Position object or None
        """
        with self._lock:
            return self._positions.get(symbol)

    def get_all_positions(self) -> Dict[str, Position]:
        """Get all positions.

        Returns:
            Dict mapping symbol to Position
        """
        with self._lock:
            return self._positions.copy()

    def is_stop_loss_triggered(self) -> bool:
        """Check if stop-loss has been triggered.

        Returns:
            True if stop-loss triggered
        """
        with self._lock:
            return self._stop_loss_triggered

    def _check_stop_loss(self, current_prices: Optional[Dict[str, float]] = None):
        """Check if stop-loss threshold exceeded (internal, must hold lock).

        CRITICAL: This is called from within update_trade() which already holds self._lock.
        DO NOT call methods that acquire the lock (like get_total_pnl) - it will deadlock!

        Args:
            current_prices: Current market prices for unrealized P&L calculation
        """
        if self._stop_loss_triggered:
            return

        # Calculate total P&L inline (already have lock, can't call get_total_pnl)
        if current_prices:
            # Calculate unrealized P&L inline to avoid deadlock
            unrealized_pnl = 0.0
            for symbol, pos in self._positions.items():
                if pos.volume == 0:
                    continue

                current_price = current_prices.get(symbol)
                if current_price is None:
                    current_price = pos.entry_price

                if pos.volume > 0:
                    # Long position
                    unrealized_pnl += (current_price - pos.entry_price) * pos.volume
                else:
                    # Short position
                    unrealized_pnl += (pos.entry_price - current_price) * abs(pos.volume)

            total_pnl = self._total_realized_pnl + unrealized_pnl
        else:
            # Fallback to realized only if no prices available (conservative)
            total_pnl = self._total_realized_pnl
            unrealized_pnl = 0.0
            logger.warning("Stop-loss checking realized P&L only - no current prices available")

        if total_pnl <= self.max_loss:
            self._stop_loss_triggered = True
            logger.critical(
                f"🚨 STOP-LOSS TRIGGERED! 🚨 "
                f"Total P&L: {total_pnl:.2f} <= {self.max_loss:.2f} "
                f"(Realized: {self._total_realized_pnl:.2f}, Unrealized: {unrealized_pnl:.2f})"
            )

            # Call callback if provided
            if self.stop_loss_callback:
                try:
                    self.stop_loss_callback()
                except Exception as e:
                    logger.error(f"Stop-loss callback failed: {e}")

    def update_high_water_mark(self, current_prices: Dict[str, float]):
        """Update high water mark for drawdown tracking.

        Args:
            current_prices: Current market prices
        """
        with self._lock:
            total_pnl = self.get_total_pnl(current_prices)
            if total_pnl > self._high_water_mark:
                self._high_water_mark = total_pnl

    def get_drawdown(self, current_prices: Dict[str, float]) -> float:
        """Get current drawdown from high water mark.

        Args:
            current_prices: Current market prices

        Returns:
            Drawdown (positive value)
        """
        with self._lock:
            total_pnl = self.get_total_pnl(current_prices)
            return max(0, self._high_water_mark - total_pnl)

    def print_summary(self, current_prices: Optional[Dict[str, float]] = None):
        """Print P&L summary.

        Args:
            current_prices: Current prices for unrealized P&L calculation
        """
        with self._lock:
            logger.debug("=" * 60)
            logger.debug("P&L SUMMARY")
            logger.debug("=" * 60)

            # Realized P&L
            logger.debug(f"Total Realized P&L: {self._total_realized_pnl:+.2f}")

            if current_prices:
                # Unrealized P&L
                unrealized = self.get_unrealized_pnl(current_prices)
                total_unrealized = sum(unrealized.values())
                total_pnl = self._total_realized_pnl + total_unrealized

                logger.debug(f"Total Unrealized P&L: {total_unrealized:+.2f}")
                logger.debug(f"Total P&L: {total_pnl:+.2f}")

                # Drawdown
                drawdown = self.get_drawdown(current_prices)
                logger.debug(f"High Water Mark: {self._high_water_mark:+.2f}")
                logger.debug(f"Drawdown: {drawdown:.2f}")

                logger.debug("")
                logger.debug("Per-Product Unrealized P&L:")
                for symbol, pnl in sorted(unrealized.items(), key=lambda x: x[1], reverse=True):
                    if abs(pnl) > 0.01:
                        pos = self._positions.get(symbol)
                        logger.debug(
                            f"  {symbol:12s}: {pnl:+8.2f}  "
                            f"(pos: {pos.volume:+6.0f}, entry: {pos.entry_price:.2f})"
                        )

            # Stop-loss status
            if self._stop_loss_triggered:
                logger.critical("⚠ STOP-LOSS TRIGGERED ⚠")

            logger.debug("=" * 60)


# Global P&L monitor instance
_global_monitor: Optional[PnLMonitor] = None


def get_pnl_monitor() -> PnLMonitor:
    """Get global P&L monitor instance.

    Returns:
        Global PnLMonitor instance
    """
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PnLMonitor()
    return _global_monitor


def init_pnl_monitor(stop_loss_callback=None) -> PnLMonitor:
    global _global_monitor
    _global_monitor = PnLMonitor(stop_loss_callback=stop_loss_callback)
    return _global_monitor
