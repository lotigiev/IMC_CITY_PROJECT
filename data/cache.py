"""Data caching system for API responses. Provides disk-based and memory-based caching to minimize API calls
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from config.settings import CACHE_DIR
from utils.logger import get_logger

logger = get_logger(__name__)


class DataCache:
    """Disk and memory cache for API responses."""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True, parents=True)
        self.memory_cache: dict[str, tuple[Any, float]] = {}

    def _make_key(self, namespace: str, params: dict) -> str:
        """Create cache key from namespace and parameters """
        # Sort params for consistent hashing
        param_str = json.dumps(params, sort_keys=True)
        key_str = f"{namespace}:{param_str}"
        param_hash = hashlib.md5(key_str.encode()).hexdigest()
        # Include namespace prefix for easier filtering
        return f"{namespace}_{param_hash}"

    def _get_cache_file(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(
        self,
        namespace: str,
        params: dict,
        ttl_seconds: Optional[float] = None,
        memory_only: bool = False,
    ) -> Optional[Any]:
        """Get cached data if available and not expired.

        Args:
            namespace: Cache namespace
            params: Parameters dict
            ttl_seconds: Time-to-live in seconds (None = no expiry)
            memory_only: Only check memory cache, skip disk

        Returns:
            Cached data or None if not found/expired
        """
        key = self._make_key(namespace, params)

        # Check memory cache first
        if key in self.memory_cache:
            data, timestamp = self.memory_cache[key]
            if ttl_seconds is None or (time.time() - timestamp) < ttl_seconds:
                logger.debug(f"Memory cache HIT: {namespace} {key[:8]}")
                return data
            else:
                logger.debug(f"Memory cache EXPIRED: {namespace} {key[:8]}")
                del self.memory_cache[key]

        if memory_only:
            return None

        # Check disk cache
        cache_file = self._get_cache_file(key)
        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    cache_entry = json.load(f)

                timestamp = cache_entry["timestamp"]
                data = cache_entry["data"]

                if ttl_seconds is None or (time.time() - timestamp) < ttl_seconds:
                    logger.debug(f"Disk cache HIT: {namespace} {key[:8]}")
                    # Populate memory cache
                    self.memory_cache[key] = (data, timestamp)
                    return data
                else:
                    logger.debug(f"Disk cache EXPIRED: {namespace} {key[:8]}")
                    cache_file.unlink()
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Cache file corrupted: {cache_file}, {e}")
                cache_file.unlink()

        logger.debug(f"Cache MISS: {namespace} {key[:8]}")
        return None

    def set(
        self,
        namespace: str,
        params: dict,
        data: Any,
        memory_only: bool = False,
    ):
        """Store data in cache"""
        key = self._make_key(namespace, params)
        timestamp = time.time()

        # Store in memory cache
        self.memory_cache[key] = (data, timestamp)
        logger.debug(f"Cached in memory: {namespace} {key[:8]}")

        if memory_only:
            return

        # Store in disk cache
        cache_file = self._get_cache_file(key)
        try:
            with open(cache_file, "w") as f:
                json.dump({"timestamp": timestamp, "data": data}, f)
            logger.debug(f"Cached to disk: {namespace} {key[:8]}")
        except (TypeError, IOError) as e:
            logger.warning(f"Failed to cache to disk: {e}")


# Global cache instance
_global_cache: Optional[DataCache] = None


def get_cache() -> DataCache:
    """Get global cache instance."""

    global _global_cache
    if _global_cache is None:
        _global_cache = DataCache()
    return _global_cache
