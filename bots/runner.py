"""Bot launcher with CLI."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_exchange_url, USERNAME, PASSWORD
from bots.hybrid_bot import HybridBot
from utils.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="IMCity Trading Bot")
    parser.add_argument("--bot", choices=["hybrid"], default="hybrid", help="Bot type")
    parser.add_argument("--exchange", choices=["test", "challenge"], default="test", help="Exchange")
    parser.add_argument("--width", type=float, default=5.0, help="Quote width")
    parser.add_argument("--volume", type=int, default=5, help="Quote volume")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no actual orders)")

    args = parser.parse_args()

    # Get exchange URL
    url = get_exchange_url(args.exchange)

    logger.info("=" * 60)
    logger.info("IMCity Trading Bot")
    logger.info("=" * 60)
    logger.info(f"Bot:      {args.bot}")
    logger.info(f"Exchange: {args.exchange}")
    logger.info(f"URL:      {url}")
    logger.info(f"User:     {USERNAME}")
    logger.info(f"Width:    {args.width}")
    logger.info(f"Volume:   {args.volume}")
    if args.dry_run:
        logger.warning("DRY RUN MODE (no orders will be sent)")
    logger.info("=" * 60)

    # Create and run bot
    try:
        if args.bot == "hybrid":
            bot = HybridBot(url, USERNAME, PASSWORD, dry_run=args.dry_run)
            bot.run(sync_interval=60)

    except KeyboardInterrupt:
        logger.info("\nBot stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Bot crashed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
