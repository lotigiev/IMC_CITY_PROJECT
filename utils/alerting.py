"""Monitoring and alerting module.
Sends critical alerts to Discord/Slack/webhook for real-time monitoring.
"""

import os
import time
import requests
from typing import Optional
from utils.logger import get_logger

logger = get_logger(__name__)


class AlertManager:
    """Send critical alerts to external services."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("ALERT_WEBHOOK_URL")

        # Check if webhook is a placeholder
        if self.webhook_url and "YOUR_WEBHOOK" in self.webhook_url:
            logger.warning("⚠️ Alerting disabled (webhook URL is placeholder)")
            self.enabled = False
        else:
            self.enabled = bool(self.webhook_url)

        if self.enabled:
            logger.info(f"✅ Alerting enabled (webhook configured)")
        else:
            logger.warning("⚠️ Alerting disabled (no webhook URL configured)")

        # Rate limiting to avoid spam
        self._last_alert_time = {}
        self._alert_cooldown = 60.0  # Min 60s between same alert types

    def send_alert(
        self,
        message: str,
        alert_type: str = "INFO",
        cooldown: bool = True,
    ) -> bool:
        """Send alert to webhook.

        Args:
            message: Alert message
            alert_type: Alert severity (INFO, WARNING, CRITICAL)
            cooldown: Whether to enforce cooldown (prevents spam)

        Returns:
            True if alert sent successfully
        """
        if not self.enabled:
            logger.debug(f"Alert (not sent, disabled): [{alert_type}] {message}")
            return False

        # Check cooldown
        if cooldown:
            now = time.time()
            last_alert = self._last_alert_time.get(alert_type, 0)
            if now - last_alert < self._alert_cooldown:
                logger.debug(f"Alert skipped (cooldown): [{alert_type}] {message}")
                return False
            self._last_alert_time[alert_type] = now

        # Format message with emoji
        emoji_map = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "CRITICAL": "🚨",
            "SUCCESS": "✅",
            "ERROR": "❌",
        }
        emoji = emoji_map.get(alert_type, "📢")

        payload = {
            "content": f"{emoji} **[{alert_type}]** {message}"
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=5
            )
            response.raise_for_status()
            logger.info(f"Alert sent: [{alert_type}] {message}")
            return True

        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
            return False

    def alert_bot_started(self, dry_run: bool = False):
        """Alert that bot has started."""
        mode = "DRY RUN" if dry_run else "LIVE"
        self.send_alert(
            f"🤖 Trading bot started in {mode} mode",
            alert_type="SUCCESS",
            cooldown=False
        )

    def alert_bot_stopped(self, reason: str = "User request"):
        """Alert that bot has stopped."""
        self.send_alert(
            f"Bot stopped: {reason}",
            alert_type="WARNING",
            cooldown=False
        )

    def alert_stop_loss_triggered(self, total_pnl: float, max_loss: float):
        """Alert that stop-loss has been triggered."""
        self.send_alert(
            f"STOP-LOSS TRIGGERED! Total P&L: ${total_pnl:.2f} (limit: ${max_loss:.2f})",
            alert_type="CRITICAL",
            cooldown=False
        )

    def alert_position_divergence(self, symbol: str, internal: int, exchange: int):
        """Alert position mismatch between internal tracking and exchange."""
        self.send_alert(
            f"Position divergence detected: {symbol} internal={internal}, exchange={exchange}",
            alert_type="WARNING"
        )

    def alert_heartbeat(self, total_pnl: float, drawdown: float):
        """Send periodic heartbeat (low priority)."""
        self.send_alert(
            f"Heartbeat: P&L=${total_pnl:+.2f}, Drawdown=${drawdown:.2f}",
            alert_type="INFO",
            cooldown=True
        )

    def alert_error(self, error_msg: str):
        """Alert on critical errors."""
        self.send_alert(
            f"Error: {error_msg}",
            alert_type="ERROR"
        )


# Global alert manager instance
_global_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get global alert manager instance.

    Returns:
        Global AlertManager instance
    """
    global _global_alert_manager
    if _global_alert_manager is None:
        _global_alert_manager = AlertManager()
    return _global_alert_manager
