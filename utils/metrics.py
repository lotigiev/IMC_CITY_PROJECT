"""Performance metrics tracking for the trading bot.

Tracks trading performance metrics including:
- Fill rates
- P&L (total, per-product, per-strategy)
- Sharpe ratio
- Max drawdown
- Win rate
- Adverse selection
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradeMetrics:
    """Metrics for a single trade."""
    timestamp: float
    product: str
    side: str
    price: float
    volume: int
    pnl: float = 0.0
    strategy: Optional[str] = None


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics."""

    # Trade tracking
    trades: List[TradeMetrics] = field(default_factory=list)

    # Order tracking
    orders_sent: int = 0
    orders_filled: int = 0
    orders_partially_filled: int = 0
    orders_cancelled: int = 0
    orders_rejected: int = 0

    # P&L tracking
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    # Per-product tracking
    pnl_by_product: Dict[str, float] = field(default_factory=lambda: defaultdict(float))
    trades_by_product: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Per-strategy tracking
    pnl_by_strategy: Dict[str, float] = field(default_factory=lambda: defaultdict(float))
    trades_by_strategy: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Risk metrics
    max_pnl: float = 0.0
    min_pnl: float = 0.0
    max_drawdown: float = 0.0

    # Timing
    start_time: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)

    def record_trade(
        self,
        product: str,
        side: str,
        price: float,
        volume: int,
        pnl: float = 0.0,
        strategy: Optional[str] = None,
    ):
        """Record a trade.

        Args:
            product: Product symbol
            side: BUY or SELL
            price: Execution price
            volume: Volume traded
            pnl: P&L from this trade
            strategy: Strategy name (optional)
        """
        trade = TradeMetrics(
            timestamp=time.time(),
            product=product,
            side=side,
            price=price,
            volume=volume,
            pnl=pnl,
            strategy=strategy,
        )
        self.trades.append(trade)

        # Update totals
        self.realized_pnl += pnl
        self.total_pnl = self.realized_pnl + self.unrealized_pnl

        # Update per-product
        self.pnl_by_product[product] += pnl
        self.trades_by_product[product] += 1

        # Update per-strategy
        if strategy:
            self.pnl_by_strategy[strategy] += pnl
            self.trades_by_strategy[strategy] += 1

        # Update risk metrics
        self.max_pnl = max(self.max_pnl, self.total_pnl)
        self.min_pnl = min(self.min_pnl, self.total_pnl)
        self.max_drawdown = max(self.max_drawdown, self.max_pnl - self.total_pnl)

        self.last_update = time.time()

        logger.debug(
            f"Trade recorded: {side} {volume}x {product} @ {price:.2f}, "
            f"P&L: {pnl:+.2f}, Total P&L: {self.total_pnl:+.2f}"
        )

    def record_order_sent(self):
        """Record an order sent."""
        self.orders_sent += 1

    def record_order_partially_filled(self):
        """Record an order partially filled."""
        self.orders_partially_filled += 1

    def record_order_cancelled(self):
        """Record an order cancelled."""
        self.orders_cancelled += 1

    def record_order_rejected(self):
        """Record an order rejected."""
        self.orders_rejected += 1

    def get_fill_rate(self) -> float:
        """Calculate fill rate (filled / sent).

        Returns:
            Fill rate as percentage (0-100)
        """
        if self.orders_sent == 0:
            return 0.0
        return (self.orders_filled + self.orders_partially_filled) / self.orders_sent * 100

    def get_win_rate(self) -> float:
        """Calculate win rate (profitable trades / total trades).

        Returns:
            Win rate as percentage (0-100)
        """
        if not self.trades:
            return 0.0
        winning_trades = sum(1 for t in self.trades if t.pnl > 0)
        return winning_trades / len(self.trades) * 100

    def get_avg_win(self) -> float:
        """Calculate average winning trade P&L.

        Returns:
            Average winning trade P&L
        """
        winning_trades = [t.pnl for t in self.trades if t.pnl > 0]
        if not winning_trades:
            return 0.0
        return np.mean(winning_trades)

    def get_avg_loss(self) -> float:
        """Calculate average losing trade P&L.

        Returns:
            Average losing trade P&L (negative)
        """
        losing_trades = [t.pnl for t in self.trades if t.pnl < 0]
        if not losing_trades:
            return 0.0
        return np.mean(losing_trades)

    def get_sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        """Calculate Sharpe ratio from trade P&L.

        Args:
            risk_free_rate: Risk-free rate (annualized)

        Returns:
            Sharpe ratio
        """
        if not self.trades:
            return 0.0

        pnls = [t.pnl for t in self.trades]
        if len(pnls) < 2:
            return 0.0

        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls, ddof=1)

        if std_pnl == 0:
            return 0.0

        return (mean_pnl - risk_free_rate) / std_pnl

    def get_profit_factor(self) -> float:
        """Calculate profit factor (gross profit / gross loss).

        Returns:
            Profit factor (higher is better)
        """
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))

        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def get_elapsed_time(self) -> float:
        """Get elapsed time since start.

        Returns:
            Elapsed time in seconds
        """
        return time.time() - self.start_time

    def get_pnl_per_hour(self) -> float:
        """Calculate P&L per hour.

        Returns:
            P&L per hour
        """
        elapsed_hours = self.get_elapsed_time() / 3600
        if elapsed_hours == 0:
            return 0.0
        return self.total_pnl / elapsed_hours

    def print_summary(self):
        """Print performance summary."""
        try:
            logger.debug("=" * 60)
            logger.debug("PERFORMANCE SUMMARY")
            logger.debug("=" * 60)

            # Overall P&L
            logger.debug(f"Total P&L:      {float(self.total_pnl):+.2f}")
            logger.debug(f"Realized P&L:   {float(self.realized_pnl):+.2f}")
            logger.debug(f"Unrealized P&L: {float(self.unrealized_pnl):+.2f}")
            logger.debug(f"Max Drawdown:   {float(self.max_drawdown):.2f}")
            logger.debug("")

            # Trade statistics
            total_trades = len(self.trades)
            logger.debug(f"Total Trades:   {total_trades}")
            logger.debug(f"Win Rate:       {float(self.get_win_rate()):.1f}%")
            logger.debug(f"Avg Win:        {float(self.get_avg_win()):+.2f}")
            logger.debug(f"Avg Loss:       {float(self.get_avg_loss()):+.2f}")
            logger.debug(f"Profit Factor:  {float(self.get_profit_factor()):.2f}")
            logger.debug(f"Sharpe Ratio:   {float(self.get_sharpe_ratio()):.2f}")
            logger.debug("")

            # Order statistics
            logger.debug(f"Orders Sent:    {int(self.orders_sent)}")
            logger.debug(f"Orders Filled:  {int(self.orders_filled)}")
            logger.debug(f"Fill Rate:      {float(self.get_fill_rate()):.1f}%")
            logger.debug("")

            # Per-product P&L
            if self.pnl_by_product:
                logger.debug("P&L by Product:")
                for product, pnl in sorted(self.pnl_by_product.items(), key=lambda x: x[1], reverse=True):
                    num_trades = self.trades_by_product[product]
                    logger.debug(f"  {product:12s}: {float(pnl):+8.2f}  ({num_trades} trades)")
                logger.debug("")

            # Per-strategy P&L
            if self.pnl_by_strategy:
                logger.debug("P&L by Strategy:")
                for strategy, pnl in sorted(self.pnl_by_strategy.items(), key=lambda x: x[1], reverse=True):
                    num_trades = self.trades_by_strategy[strategy]
                    logger.debug(f"  {strategy:12s}: {float(pnl):+8.2f}  ({num_trades} trades)")
                logger.debug("")

            # Time metrics
            elapsed = self.get_elapsed_time()
            logger.debug(f"Elapsed Time:   {float(elapsed)/3600:.2f} hours")
            logger.debug(f"P&L per Hour:   {float(self.get_pnl_per_hour()):+.2f}")
            logger.debug("=" * 60)
        except Exception as e:
            logger.error(f"Error printing metrics summary: {e}")


# Global metrics instance
_global_metrics: Optional[PerformanceMetrics] = None


def get_metrics() -> PerformanceMetrics:
    """Get global metrics instance.

    Returns:
        Global PerformanceMetrics instance
    """
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = PerformanceMetrics()
    return _global_metrics
