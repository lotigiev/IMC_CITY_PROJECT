"""Pre-trade risk checks.

Validates orders before execution to ensure compliance with:
- Position limits
- P&L thresholds
- Product specifications
- Stop-loss conditions
"""

from dataclasses import dataclass
from typing import Optional

from bot_template import OrderRequest
from config.products import get_product_spec
from risk.position_limits import get_position_tracker
from risk.pnl_monitor import get_pnl_monitor
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskCheckResult:
    """Result of a risk check."""
    allowed: bool
    reason: str
    severity: str = "INFO"  # INFO, WARNING, ERROR, CRITICAL


class RiskChecker:
    """Pre-trade risk validation."""

    def __init__(
        self,
        enable_position_checks: bool = True,
        enable_pnl_checks: bool = True,
        enable_product_checks: bool = True,
    ):
  
        self.enable_position_checks = enable_position_checks
        self.enable_pnl_checks = enable_pnl_checks
        self.enable_product_checks = enable_product_checks

        self.position_tracker = get_position_tracker()
        self.pnl_monitor = get_pnl_monitor()

    def check_order(
        self,
        order: OrderRequest,
        current_position: Optional[int] = None,
    ) -> RiskCheckResult:
        """Perform all risk checks on an order.

        Args:
            order: Order to validate
            current_position: Current position (if None, use tracked position)

        Returns:
            RiskCheckResult
        """
        # 1. Check stop-loss status
        if self.enable_pnl_checks:
            result = self._check_stop_loss()
            if not result.allowed:
                return result

        # 2. Check product specifications
        if self.enable_product_checks:
            result = self._check_product_specs(order)
            if not result.allowed:
                return result

        # 3. Check position limits
        if self.enable_position_checks:
            result = self._check_position_limits(order, current_position)
            if not result.allowed:
                return result

        # All checks passed
        return RiskCheckResult(allowed=True, reason="All checks passed", severity="INFO")

    def _check_stop_loss(self) -> RiskCheckResult:
        """Check if stop-loss has been triggered.

        Returns:
            RiskCheckResult
        """
        if self.pnl_monitor.is_stop_loss_triggered():
            return RiskCheckResult(
                allowed=False,
                reason="Stop-loss triggered - no new orders allowed",
                severity="CRITICAL",
            )

        return RiskCheckResult(allowed=True, reason="Stop-loss OK", severity="INFO")

    def _check_product_specs(self, order: OrderRequest) -> RiskCheckResult:
        """Validate order against product specifications.

        Args:
            order: Order to validate

        Returns:
            RiskCheckResult
        """
        try:
            spec = get_product_spec(order.product)
        except ValueError as e:
            return RiskCheckResult(
                allowed=False,
                reason=f"Unknown product: {order.product}",
                severity="ERROR",
            )

        # Check tick size
        tick_size = spec.tick_size
        if order.price % tick_size != 0:
            # Allow small floating point errors
            remainder = order.price % tick_size
            if remainder > tick_size * 0.01 and remainder < tick_size * 0.99:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Price {order.price} not aligned to tick size {tick_size}",
                    severity="ERROR",
                )

        # Check volume is positive
        if order.volume <= 0:
            return RiskCheckResult(
                allowed=False,
                reason=f"Invalid volume: {order.volume}",
                severity="ERROR",
            )

        # Check price is positive
        if order.price <= 0:
            return RiskCheckResult(
                allowed=False,
                reason=f"Invalid price: {order.price}",
                severity="ERROR",
            )

        return RiskCheckResult(allowed=True, reason="Product specs OK", severity="INFO")

    def _check_position_limits(
        self,
        order: OrderRequest,
        current_position: Optional[int] = None,
    ) -> RiskCheckResult:
        """Check if order would violate position limits.

        Args:
            order: Order to validate
            current_position: Current position (if None, use tracked)

        Returns:
            RiskCheckResult
        """
        # Get current position if not provided
        if current_position is None:
            current_position = self.position_tracker.get_position(order.product)

        # Check if trade is allowed
        allowed, reason = self.position_tracker.can_trade(
            symbol=order.product,
            side=order.side,
            volume=order.volume,
            current_position=current_position,
        )

        if not allowed:
            return RiskCheckResult(
                allowed=False,
                reason=f"Position limit check failed: {reason}",
                severity="WARNING",
            )

        return RiskCheckResult(allowed=True, reason="Position limits OK", severity="INFO")


# Global risk checker instance
_global_checker: Optional[RiskChecker] = None


def get_risk_checker() -> RiskChecker:
    """Get global risk checker instance.

    Returns:
        Global RiskChecker instance
    """
    global _global_checker
    if _global_checker is None:
        _global_checker = RiskChecker()
    return _global_checker
