"""Thames tidal data client for EA Flood Monitoring API.

Fetches tidal level readings at Westminster gauge.
Readings are in metres Above Ordnance Datum (mAOD).
"""

from typing import Optional

import pandas as pd
import requests

from config.settings import THAMES_MEASURE_ID, LONDON_TIMEZONE, TIDES_CACHE_TTL_SECONDS
from data.cache import get_cache
from utils.logger import get_logger

logger = get_logger(__name__)


class TidesClient:
    """Client for EA Flood Monitoring API (Thames tidal data)."""

    BASE_URL = "https://environment.data.gov.uk/flood-monitoring"

    def __init__(self):
        """Initialize tides client."""
        self.cache = get_cache()

    def fetch_tidal_levels(
        self,
        limit: int = 200,
        measure_id: str = THAMES_MEASURE_ID,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch recent Thames tidal readings at Westminster.

        Args:
            limit: Number of readings to fetch (max ~400 for ~4 days of history)
            measure_id: Measure ID for Westminster gauge
            use_cache: Whether to use cache

        Returns:
            DataFrame with columns:
                - time: Timestamp (London timezone)
                - level_maod: Tidal level in metres Above Ordnance Datum
                - level_mm: Tidal level in millimetres (for TIDE_SPOT)
                - tide_spot_value: abs(level_mm) for TIDE_SPOT settlement
        """
        # Check cache
        cache_params = {
            "limit": limit,
            "measure_id": measure_id,
        }

        if use_cache:
            cached = self.cache.get("tides", cache_params, TIDES_CACHE_TTL_SECONDS)
            if cached is not None:
                logger.debug("Tidal data loaded from cache")
                df = pd.DataFrame(cached)
                # Convert time strings back to datetime
                df["time"] = pd.to_datetime(df["time"])
                return df

        # Fetch from API
        logger.debug(f"Fetching tidal data: limit={limit}")

        url = f"{self.BASE_URL}/id/measures/{measure_id}/readings"
        params = {
            "_sorted": "",  # Sort by time descending
            "_limit": limit,
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Parse response
            items = data.get("items", [])
            if not items:
                logger.warning("No tidal data returned from API")
                return pd.DataFrame()

            df = pd.DataFrame(items)[["dateTime", "value"]].rename(
                columns={"dateTime": "time", "value": "level_maod"}
            )

            # Convert time to datetime (London timezone)
            df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(LONDON_TIMEZONE)

            # Sort by time ascending
            df = df.sort_values("time").reset_index(drop=True)

            # Add derived fields
            df["level_mm"] = df["level_maod"] * 1000  # Convert to mm
            df["tide_spot_value"] = df["level_mm"].abs()  # TIDE_SPOT = abs(level)

            logger.debug(f"Fetched {len(df)} tidal records from {df['time'].min()} to {df['time'].max()}")

            # Cache the result (convert timestamps to strings for JSON serialization)
            if use_cache:
                df_cache = df.copy()
                df_cache["time"] = df_cache["time"].astype(str)
                self.cache.set("tides", cache_params, df_cache.to_dict("records"))

            return df

        except requests.RequestException as e:
            logger.error(f"Failed to fetch tidal data: {e}")
            raise

    def get_current_tide_spot(self, use_cache: bool = True) -> Optional[float]:
        """Get current TIDE_SPOT value (abs tidal level in mm).

        Args:
            use_cache: Whether to use cache

        Returns:
            Current TIDE_SPOT value or None if unavailable
        """
        try:
            df = self.fetch_tidal_levels(limit=1, use_cache=use_cache)
            if df.empty:
                return None
            return df.iloc[-1]["tide_spot_value"]
        except Exception as e:
            logger.error(f"Failed to get current TIDE_SPOT: {e}")
            return None

    def calculate_tide_swing(
        self,
        df: Optional[pd.DataFrame] = None,
        use_cache: bool = True,
    ) -> Optional[float]:
        """Calculate TIDE_SWING settlement value.

        TIDE_SWING = sum of strangle payoffs on 15-min absolute changes (in cm)
        For each consecutive pair:
            diff_cm = abs(level(t) - level(t-1)) * 100
            payoff = max(0, 20 - diff_cm) + max(0, diff_cm - 25)

        Args:
            df: DataFrame with tidal data (if None, fetch fresh data)
            use_cache: Whether to use cache when fetching

        Returns:
            TIDE_SWING settlement value or None
        """
        try:
            if df is None:
                # Fetch 24h of data (96 x 15min intervals + 1 for diff calculation)
                df = self.fetch_tidal_levels(limit=97, use_cache=use_cache)

            if df.empty or len(df) < 2:
                logger.warning(f"Insufficient tidal data for TIDE_SWING: {len(df)} records")
                return None

            # Calculate absolute changes in cm
            df = df.copy()
            df["level_change_cm"] = df["level_maod"].diff().abs() * 100

            # Calculate strangle payoff for each interval
            # Put leg: max(0, 20 - diff_cm)
            # Call leg: max(0, diff_cm - 25)
            df["strangle_payoff"] = (
                (20 - df["level_change_cm"]).clip(lower=0) +
                (df["level_change_cm"] - 25).clip(lower=0)
            )

            # Sum payoffs (skip first row which has NaN diff)
            tide_swing = df["strangle_payoff"].iloc[1:].sum()

            logger.debug(
                f"TIDE_SWING calculated: {tide_swing:.2f} "
                f"(from {len(df)-1} intervals, "
                f"mean change: {df['level_change_cm'].iloc[1:].mean():.2f} cm)"
            )

            return tide_swing

        except Exception as e:
            logger.error(f"Failed to calculate TIDE_SWING: {e}")
            return None

    def get_tide_swing_estimate(self, use_cache: bool = True) -> Optional[float]:
        """Get current estimate of TIDE_SWING settlement value.

        Args:
            use_cache: Whether to use cache

        Returns:
            Estimated TIDE_SWING value or None
        """
        try:
            # Fetch last 24h + 1 for diff
            df = self.fetch_tidal_levels(limit=97, use_cache=use_cache)
            return self.calculate_tide_swing(df, use_cache=False)
        except Exception as e:
            logger.error(f"Failed to estimate TIDE_SWING: {e}")
            return None


# Global client instance
_global_client: Optional[TidesClient] = None


def get_tides_client() -> TidesClient:
    """Get global tides client instance.

    Returns:
        Global TidesClient instance
    """
    global _global_client
    if _global_client is None:
        _global_client = TidesClient()
    return _global_client
