"""Hybrid multi-strategy trading bot.
"""

import time
from collections import deque
from typing import List, Dict
from bot_template import BaseBot, OrderBook, Trade
from execution.order_manager import OrderManager
from execution.smart_cancel import SmartCanceller
from strategies.market_maker import MarketMaker
from strategies.etf_arbitrage_v2 import ETFArbitrageV2
from pricing.fair_value import get_fair_value_estimator
from risk.position_limits import get_position_tracker
from risk.pnl_monitor import init_pnl_monitor
from config.settings import PRODUCTS, MAX_LOSS_THRESHOLD
from utils.logger import get_logger
from utils.metrics import get_metrics
from utils.alerting import get_alert_manager

logger = get_logger(__name__)


class HybridBot(BaseBot):
    """Multi-strategy bot with risk management."""

    def __init__(self, cmi_url: str, username: str, password: str, dry_run: bool = False):
        super().__init__(cmi_url, username, password)

        self.dry_run = dry_run
        if dry_run:
            logger.warning("DRY RUN MODE ENABLED - No real orders will be sent!")

        # Infrastructure
        self.alert_manager = get_alert_manager()
        self.pnl_monitor = init_pnl_monitor(
            stop_loss_callback=lambda: self.alert_manager.alert_stop_loss_triggered(
                self.pnl_monitor.get_total_realized_pnl(), MAX_LOSS_THRESHOLD
            )
        )
        self.order_manager = OrderManager(self, max_workers=10, rate_limit=1.0, dry_run=dry_run)
        self.smart_cancel = SmartCanceller(self)
        self.position_tracker = get_position_tracker()
        self.metrics = get_metrics()
        self.fair_value_estimator = get_fair_value_estimator()

        self.strategies = [
            MarketMaker(
                products=PRODUCTS,
                width=5.0,
                volume=5,
                fair_value_provider=lambda p: self.fair_value_estimator.get_all_fair_values(use_cache=True).get(p),
            ),
            ETFArbitrageV2(min_edge=10.0, volume=5),  # Using improved v2 with better execution
        ]

        self._last_sync = 0
        self._last_requote: Dict[str, float] = {}  # Per-product throttling
        # FIXED: Use deque instead of set to avoid memory leak
        self._processed_trades = deque(maxlen=1000)  # Auto-drops oldest when full

        # Track market prices for accurate P&L calculation
        self._market_prices = {}

        # Position reconciliation tracking
        self._last_position_reconciliation = 0
        self._position_reconciliation_interval = 60.0  # seconds
        self._orderbook_count = 0
        self._last_orderbook_check = time.time()

    def on_orderbook(self, orderbook: OrderBook):
        """React to orderbook updates."""
        self._last_orderbook_check = time.time()
        self._orderbook_count += 1

        # Debug: log orderbook receipt
        logger.debug(f"Orderbook: {orderbook.product} | Bid: {orderbook.buy_orders[0].price if orderbook.buy_orders else 'N/A'} | Ask: {orderbook.sell_orders[0].price if orderbook.sell_orders else 'N/A'}")

        # Update market price for accurate P&L calculation
        best_bid = orderbook.buy_orders[0].price if orderbook.buy_orders else None
        best_ask = orderbook.sell_orders[0].price if orderbook.sell_orders else None

        if best_bid and best_ask:
            self._market_prices[orderbook.product] = (best_bid + best_ask) / 2
        elif best_bid:
            self._market_prices[orderbook.product] = best_bid
        elif best_ask:
            self._market_prices[orderbook.product] = best_ask

        now = time.time()
        last_quote_time = self._last_requote.get(orderbook.product, 0)
        if now - last_quote_time < 0.25:  # 250ms per product
            return

        # Collect orders from all strategies
        all_orders = []
        for strategy in self.strategies:
            if strategy.enabled:
                orders = strategy.on_orderbook(orderbook)
                all_orders.extend(orders)

        # Log if orders were generated
        if all_orders:
            logger.debug(f"Generated {len(all_orders)} orders for {orderbook.product}")

        # Smart cancel and send (only if we have orders)
        if all_orders:
            self._selective_cancel_and_requote(orderbook.product, all_orders)
            self._last_requote[orderbook.product] = now

    def on_trades(self, trade: Trade):
        """React to trades.
        """
        # CRITICAL FIX: Only process OUR trades!
        if trade.buyer != self.username and trade.seller != self.username:
            return  # Not our trade, ignore it

        # FIXED: Deduplication using deque (auto-drops oldest, no manual clearing needed)
        trade_key = f"{trade.timestamp}_{trade.product}_{trade.price}_{trade.volume}"
        if trade_key in self._processed_trades:
            return
        self._processed_trades.append(trade_key)  # deque auto-drops oldest when full

        side = "BOUGHT" if trade.buyer == self.username else "SOLD"
        # Demote redundant trade log, PnLMonitor logs it with more detail
        logger.debug(f"TRADE: {side} {trade.volume}x {trade.product} @ {trade.price}")

        # Update P&L monitor (with current market prices for stop-loss check)
        trade_side = "BUY" if trade.buyer == self.username else "SELL"
        realized_pnl = self.pnl_monitor.update_trade(
            trade.product, trade_side, trade.price, trade.volume,
            current_prices=self._market_prices
        )

        # Update position tracker immediately (so MM sees it!)
        delta = trade.volume if trade_side == "BUY" else -trade.volume
        self.position_tracker.adjust_position(trade.product, delta)

        # Update metrics
        self.metrics.record_trade(trade.product, trade_side, trade.price, trade.volume, pnl=realized_pnl)

        # React with strategies
        for strategy in self.strategies:
            if strategy.enabled:
                orders = strategy.on_trade(trade)
                if orders:
                    self.order_manager.send_orders_batch(orders)

    def _selective_cancel_and_requote(self, product: str, new_orders: List):
        """Cancel existing orders for this product and send new ones."""
        if not new_orders:
            return

        active = self.get_orders(product)
        if active:
            for o in active:
                self.order_manager.cancel_order(o["id"])

        self.order_manager.send_orders_batch(new_orders)

    def run(self, sync_interval: int = 60):
        """Run hybrid bot"""
        logger.info("Starting SSE stream...")
        self.start()  # Start SSE
        logger.info("✓ SSE thread started")
        logger.info("Hybrid bot started")
        logger.info(f"Strategies: {[s.name for s in self.strategies]}")

        # Send startup alert
        self.alert_manager.alert_bot_started(dry_run=self.dry_run)

        self._last_heartbeat = time.time()

        try:
            while True:
                # Periodic tasks
                now = time.time()

                # Heartbeat every 5 minutes
                if now - self._last_heartbeat > 300:
                    total_pnl = self.metrics.total_pnl
                    # Update high water mark for drawdown tracking
                    self.pnl_monitor.update_high_water_mark(self._market_prices)
                    drawdown = self.pnl_monitor.get_drawdown(self._market_prices)
                    logger.info(
                        f" HEARTBEAT: Bot is active. "
                        f"Total P&L: {total_pnl:+.2f}, Drawdown: {drawdown:.2f}"
                    )
                    # Send heartbeat alert
                    self.alert_manager.alert_heartbeat(total_pnl, drawdown)
                    self._last_heartbeat = now

                if now - self._last_orderbook_check > 10:
                    logger.debug("SSE silent for 10s, triggering manual poll fallback...")
                    for product in PRODUCTS:
                        try:
                            self.order_manager.rate_limiter.acquire()
                            book = self.get_orderbook(product)
                            self.on_orderbook(book)
                        except Exception as e:
                            logger.error(f"Manual poll failed for {product}: {e}")

                    try:
                        self.order_manager.rate_limiter.acquire()
                        for t in self.get_market_trades():
                            self.on_trades(t)
                    except Exception as e:
                        logger.error(f"Manual trade poll failed: {e}")

                # Sync positions with exchange (critical for catching missed fills)
                if now - self._last_sync > sync_interval:
                    logger.debug("Syncing positions with exchange...")
                    self.order_manager.sync_positions()

                    try:
                        exchange_positions = self.get_positions()
                        for symbol, exchange_pos in exchange_positions.items():
                            internal_pos = self.position_tracker.get_position(symbol)
                            if exchange_pos != internal_pos:
                                logger.warning(
                                    f"⚠️ POSITION DIVERGENCE: {symbol} "
                                    f"internal={internal_pos}, exchange={exchange_pos} "
                                    f"(diff={exchange_pos - internal_pos})"
                                )
                                # Send alert
                                self.alert_manager.alert_position_divergence(
                                    symbol, internal_pos, exchange_pos
                                )
                                # Force update to exchange position (source of truth)
                                self.position_tracker.update_position(symbol, exchange_pos)
                    except Exception as e:
                        logger.error(f"Position reconciliation failed: {e}")
                        self.alert_manager.alert_error(f"Position reconciliation failed: {e}")

                    # Print summaries
                    self.position_tracker.print_summary()
                    self.pnl_monitor.print_summary(self._market_prices)
                    self.metrics.print_summary()
                    self._last_sync = now
                time.sleep(10)

        except KeyboardInterrupt:
            logger.info("Stopping...")
            self.alert_manager.alert_bot_stopped("User interrupt")
            self._shutdown()
        except Exception as e:
            logger.exception(f"CRITICAL ERROR in HybridBot run loop: {e}")
            self.alert_manager.alert_error(f"Bot crashed: {str(e)}")
            self._shutdown()
            raise

    def _shutdown(self):
        """Cleanup resources."""
        try:
            self.cancel_all_orders()
            self.stop()
            if self.order_manager:
                self.order_manager.shutdown()
            logger.info("Bot shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            self.alert_manager.alert_error(f"Shutdown error: {str(e)}")
