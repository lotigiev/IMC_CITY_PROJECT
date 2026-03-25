"""Base strategy interface - provided by IMC"""

from abc import ABC, abstractmethod
from typing import List
from bot_template import OrderRequest, OrderBook, Trade
from utils.logger import get_logger

logger = get_logger(__name__)


class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""

    def __init__(self, name: str):
        self.name = name
        self.enabled = True

    @abstractmethod
    def on_orderbook(self, orderbook: OrderBook) -> List[OrderRequest]:
        
        pass

    @abstractmethod
    def on_trade(self, trade: Trade) -> List[OrderRequest]:
        
        pass

    def enable(self):
        """Enable strategy."""
        self.enabled = True
        logger.info(f"Strategy '{self.name}' enabled")

    def disable(self):
        """Disable strategy."""
        self.enabled = False
        logger.info(f"Strategy '{self.name}' disabled")
