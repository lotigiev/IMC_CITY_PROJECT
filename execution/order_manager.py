import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Optional, List

from bot_template import BaseBot, OrderRequest, OrderResponse
from config.settings import MAX_REQUESTS_PER_SECOND
from risk.risk_checks import get_risk_checker
from risk.position_limits import get_position_tracker
from risk.pnl_monitor import get_pnl_monitor
from utils.logger import get_logger
from utils.metrics import get_metrics

logger = get_logger(__name__)


@dataclass
class OrderResult:
    """Result of an order execution."""
    order: OrderRequest
    response: Optional[OrderResponse]
    success: bool
    error: Optional[str] = None


class RateLimiter:
    """Token bucket rate limiter for API requests.

    CRITICAL FIX: Implements proper token bucket algorithm that actually enforces
    rate limits even with parallel requests. Previous implementation was a mutex
    that allowed parallel requests to all execute within 1 second, violating limits.
    """

    def __init__(self, rate: float = MAX_REQUESTS_PER_SECOND, capacity: Optional[int] = None):
        """Initialize rate limiter.

        Args:
            rate: Maximum requests per second (tokens added per second)
            capacity: Maximum burst size (defaults to rate)
        """
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(1, int(rate))
        self.tokens = float(self.capacity)  # Start with full bucket
        self.last_update = time.time()
        self._lock = Lock()

    def acquire(self, tokens: int = 1):
        """Wait until rate limit allows next request.

        Args:
            tokens: Number of tokens to consume (default 1)
        """
        with self._lock:
            while True:
                now = time.time()

                # Refill tokens based on time elapsed
                time_elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + time_elapsed * self.rate)
                self.last_update = now

                if self.tokens >= tokens:
                    # Sufficient tokens available
                    self.tokens -= tokens
                    return

                # Not enough tokens - calculate wait time
                tokens_needed = tokens - self.tokens
                wait_time = tokens_needed / self.rate

                logger.debug(f"Rate limiting: waiting {wait_time:.3f}s for {tokens} token(s)")

                # Release lock while sleeping to allow other threads to check
                self._lock.release()
                time.sleep(wait_time)
                self._lock.acquire()


class OrderManager:
    """Manages order execution with thread pooling and risk checks."""

    def __init__(
        self,
        bot: BaseBot,
        max_workers: int = 10,
        rate_limit: float = MAX_REQUESTS_PER_SECOND,
        enable_risk_checks: bool = True,
        dry_run: bool = False,
    ):
        """Initialize order manager.

        Args:
            bot: BaseBot instance for order execution
            max_workers: Maximum threads in pool
            rate_limit: Maximum requests per second
            enable_risk_checks: Whether to perform pre-trade risk checks
            dry_run: If True, simulate orders without sending to exchange
        """
        self.bot = bot
        self.max_workers = max_workers
        self.enable_risk_checks = enable_risk_checks
        self.dry_run = dry_run

        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.rate_limiter = RateLimiter(rate=rate_limit)

        self.risk_checker = get_risk_checker()
        self.position_tracker = get_position_tracker()
        self.pnl_monitor = get_pnl_monitor()
        self.metrics = get_metrics()

        logger.info(
            f"Order manager initialized: max_workers={max_workers}, "
            f"rate_limit={rate_limit}/s, risk_checks={enable_risk_checks}, "
            f"dry_run={dry_run}"
        )

    def send_order(
        self,
        order: OrderRequest,
        skip_risk_check: bool = False,
    ) -> OrderResult:
        """Send a single order.

        Args:
            order: Order to send
            skip_risk_check: Skip pre-trade risk check

        Returns:
            OrderResult
        """
        # Risk check
        if self.enable_risk_checks and not skip_risk_check:
            check = self.risk_checker.check_order(order)
            if not check.allowed:
                logger.warning(
                    f"Order rejected by risk check: {order.product} "
                    f"{order.side} {order.volume}@{order.price}, reason: {check.reason}"
                )
                self.metrics.record_order_rejected()
                return OrderResult(
                    order=order,
                    response=None,
                    success=False,
                    error=f"Risk check failed: {check.reason}",
                )

        # Dry run mode - simulate order without sending
        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would send order: {order.product} {order.side} "
                f"{order.volume}@{order.price}"
            )
            # Create simulated response with all required fields
            from bot_template import OrderResponse
            from datetime import datetime
            simulated_response = OrderResponse(
                id=f"DRY-{int(time.time() * 1000)}",
                status="ACTIVE",
                product=order.product,
                side=order.side,
                price=order.price,
                volume=order.volume,
                filled=0,
                user="DRY_RUN_USER",
                timestamp=datetime.now().isoformat(),
                targetUser=None,
                message="Simulated order (dry run)"
            )
            self.metrics.record_order_sent()
            return OrderResult(order=order, response=simulated_response, success=True)

        # Rate limiting
        self.rate_limiter.acquire()

        # Send order
        start_time = time.time()

        try:
            response = self.bot.send_order(order)
            latency_ms = (time.time() - start_time) * 1000

            if response:
                logger.debug(
                    f"Order sent: {order.product} {order.side} "
                    f"{order.volume}@{order.price} → {response.status} "
                    f"(latency: {latency_ms:.1f}ms)"
                )
                self.metrics.record_order_sent()

                if response.status in ["ACTIVE", "PART_FILLED"]:
                    if response.filled > 0:
                        self.metrics.record_order_partially_filled()
                    return OrderResult(order=order, response=response, success=True)
                else:
                    return OrderResult(order=order, response=response, success=True)
            else:
                logger.error(f"Order failed: {order.product} {order.side} {order.volume}@{order.price}")
                self.metrics.record_order_rejected()
                return OrderResult(
                    order=order,
                    response=None,
                    success=False,
                    error="Order returned None",
                )

        except Exception as e:
            logger.error(f"Order exception: {e}")
            self.metrics.record_order_rejected()
            return OrderResult(
                order=order,
                response=None,
                success=False,
                error=str(e),
            )

    def send_orders_batch(
        self,
        orders: List[OrderRequest],
        skip_risk_check: bool = False,
    ) -> List[OrderResult]:
        """Send multiple orders in parallel using thread pool.

        Args:
            orders: List of orders to send
            skip_risk_check: Skip pre-trade risk checks

        Returns:
            List of OrderResults
        """
        logger.debug(f"Sending batch of {len(orders)} orders")

        # Submit all orders to thread pool
        futures = {
            self.executor.submit(self.send_order, order, skip_risk_check): order
            for order in orders
        }

        # Collect results as they complete
        results = []
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                order = futures[future]
                logger.error(f"Batch order exception for {order.product}: {e}")
                results.append(
                    OrderResult(
                        order=order,
                        response=None,
                        success=False,
                        error=str(e),
                    )
                )

        # Log summary
        successful = sum(1 for r in results if r.success)
        logger.debug(f"Batch complete: {successful}/{len(orders)} orders successful")

        return results

    def cancel_order(self, order_id: str):
        """Cancel an order by ID.

        Args:
            order_id: Order ID to cancel
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would cancel order: {order_id}")
            self.metrics.record_order_cancelled()
            return

        try:
            self.rate_limiter.acquire()
            self.bot.cancel_order(order_id)
            self.metrics.record_order_cancelled()
            logger.debug(f"Order cancelled: {order_id}")
        except Exception as e:
            logger.error(f"Cancel failed for {order_id}: {e}")

    def cancel_all_orders(self):
        """Cancel all active orders."""
        logger.debug("Canceling all orders")

        try:
            # Get active orders
            self.rate_limiter.acquire()
            orders = self.bot.get_orders()

            if not orders:
                logger.debug("No active orders to cancel")
                return

            logger.debug(f"Canceling {len(orders)} active orders")

            # Cancel in parallel using thread pool
            futures = {
                self.executor.submit(self.cancel_order, order["id"]): order["id"]
                for order in orders
            }

            # Wait for all cancellations
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    order_id = futures[future]
                    logger.error(f"Cancel failed for {order_id}: {e}")

            logger.debug("All orders cancelled")

        except Exception as e:
            logger.error(f"Cancel all failed: {e}")

    def sync_positions(self):
        """Synchronize positions from exchange with position tracker."""
        try:
            self.rate_limiter.acquire()
            positions = self.bot.get_positions()

            for symbol, position in positions.items():
                self.position_tracker.update_position(symbol, position)

            logger.debug(f"Positions synced: {positions}")

        except Exception as e:
            logger.error(f"Position sync failed: {e}")

    def shutdown(self):
        """Shutdown order manager and thread pool."""
        logger.info("Shutting down order manager")
        self.executor.shutdown(wait=True)


# Global order manager instance
_global_manager: Optional[OrderManager] = None
