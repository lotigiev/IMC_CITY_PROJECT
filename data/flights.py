"""Flight data client for AeroDataBox API via RapidAPI.

CRITICAL: This API has severe rate limits (150 requests/month on free tier).
Must cache aggressively and use sparingly.
"""

import time
from typing import Optional, Dict, List

import requests

from config.settings import (
    RAPIDAPI_KEY,
    HEATHROW_IATA,
    LONDON_TIMEZONE,
    FLIGHTS_CACHE_TTL_SECONDS,
)
from data.cache import get_cache
from utils.logger import get_logger
from utils.helpers import get_london_time

logger = get_logger(__name__)


class FlightsClient:
    """Client for AeroDataBox flight API.

    WARNING: Free tier has only 150 requests/month. Cache aggressively!
    """

    BASE_URL = "https://aerodatabox.p.rapidapi.com"

    def __init__(self, api_key: str = RAPIDAPI_KEY):
        """Initialize flights client.

        Args:
            api_key: RapidAPI key for AeroDataBox
        """
        self.api_key = api_key
        self.cache = get_cache()
        self.request_count = 0  # Track API usage
        self._last_request_time = 0
        self.SAFE_TTL = 3600 # 1 HOUR - Safe balance for 150/month limit

    def _make_request(self, url: str, params: Optional[Dict] = None) -> Dict:
        """Make request to AeroDataBox API."""
        now = time.time()
        # EMERGENCY SAFETY: Never call API more than once every 15 minutes
        if now - self._last_request_time < 900:
            logger.error("🚫 FLIGHT API BLOCK: Attempted to call API too soon. Using stale data.")
            raise requests.RequestException("Rate limit safety block active")

        headers = {
            "x-rapidapi-host": "aerodatabox.p.rapidapi.com",
            "x-rapidapi-key": self.api_key,
        }

        self.request_count += 1
        self._last_request_time = now
        logger.warning(
            f"!!! MAKING FLIGHT API REQUEST #{self.request_count} !!! "
            f"(Monthly limit: 150)"
        )

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def fetch_flights_relative(
        self,
        airport: str = HEATHROW_IATA,
        offset_minutes: int = -360,
        duration_minutes: int = 720,
        filters: Optional[Dict] = None,
        use_cache: bool = True,
    ) -> Dict:
        # Check cache first (CRITICAL!)
        cache_params = {
            "method": "relative",
            "airport": airport,
            "offset_minutes": offset_minutes,
            "duration_minutes": duration_minutes,
            "filters": filters or {},
        }

        if use_cache:
            # Force SAFE_TTL regardless of settings
            cached = self.cache.get("flights", cache_params, self.SAFE_TTL)
            if cached is not None:
                logger.debug("Flight data loaded from cache (avoided API call)")
                return cached

        # Build URL
        params_str = f"?offsetMinutes={offset_minutes}&durationMinutes={duration_minutes}&direction=Both"
        if filters:
            for k, v in filters.items():
                params_str += f"&{k}={'true' if v else 'false'}"

        url = f"{self.BASE_URL}/flights/airports/iata/{airport}{params_str}"

        try:
            data = self._make_request(url)

            arrivals = data.get("arrivals", [])
            departures = data.get("departures", [])

            logger.debug(
                f"Fetched {len(arrivals)} arrivals, {len(departures)} departures "
                f"(offset={offset_minutes}, duration={duration_minutes})"
            )

            result = {"arrivals": arrivals, "departures": departures}

            # Cache the result (CRITICAL!)
            if use_cache:
                self.cache.set("flights", cache_params, result)

            return result

        except requests.RequestException as e:
            logger.error(f"Failed to fetch flight data: {e}")
            raise

    def calculate_lhr_count(self, data: Dict) -> int:
        """Calculate LHR_COUNT from flight data.

        LHR_COUNT = total flights (arrivals + departures) in 24h

        Args:
            data: Flight data dict with 'arrivals' and 'departures'

        Returns:
            Total flight count
        """
        arrivals = len(data.get("arrivals", []))
        departures = len(data.get("departures", []))
        total = arrivals + departures

        logger.debug(f"LHR_COUNT: {total} ({arrivals} arrivals + {departures} departures)")
        return total

    def calculate_lhr_index(self, data: Dict, interval_minutes: int = 30) -> float:
        """Calculate LHR_INDEX from flight data.

        LHR_INDEX = sum of |abs((arr - dep)/(arr + dep)) * 100| per interval

        Args:
            data: Flight data dict with 'arrivals' and 'departures'
            interval_minutes: Interval size in minutes (default: 30)

        Returns:
            LHR_INDEX value
        """
        from collections import defaultdict
        from utils.helpers import parse_iso_timestamp

        arrivals_list = data.get("arrivals", [])
        departures_list = data.get("departures", [])

        if not arrivals_list and not departures_list:
            logger.warning("No flights to calculate LHR_INDEX")
            return 0.0

        # Bin flights by time interval
        interval_counts = defaultdict(lambda: {"arrivals": 0, "departures": 0})

        def bin_flight(flight, flight_type):
            """Bin a flight into its time interval."""
            try:
                # Try to extract scheduled time (handle different API response formats)
                scheduled_time = None

                # Common field names in AeroDataBox API
                if "movement" in flight and "scheduledTimeLocal" in flight["movement"]:
                    scheduled_time = parse_iso_timestamp(flight["movement"]["scheduledTimeLocal"])
                elif "movement" in flight and "scheduledTime" in flight["movement"]:
                    scheduled_time = parse_iso_timestamp(flight["movement"]["scheduledTime"]["local"])
                elif "scheduledTimeLocal" in flight:
                    scheduled_time = parse_iso_timestamp(flight["scheduledTimeLocal"])

                if scheduled_time is None:
                    return None

                # Calculate interval bucket (floor to interval_minutes)
                interval_key = scheduled_time.replace(
                    minute=(scheduled_time.minute // interval_minutes) * interval_minutes,
                    second=0,
                    microsecond=0
                )
                return interval_key
            except Exception as e:
                logger.debug(f"Failed to parse flight time: {e}")
                return None

        # Bin all flights
        for arrival in arrivals_list:
            interval_key = bin_flight(arrival, "arrival")
            if interval_key:
                interval_counts[interval_key]["arrivals"] += 1

        for departure in departures_list:
            interval_key = bin_flight(departure, "departure")
            if interval_key:
                interval_counts[interval_key]["departures"] += 1

        # Calculate LHR_INDEX as sum of per-interval imbalances
        lhr_index = 0.0
        for interval_key, counts in sorted(interval_counts.items()):
            arr = counts["arrivals"]
            dep = counts["departures"]

            if arr + dep > 0:
                imbalance = abs((arr - dep) / (arr + dep)) * 100
                lhr_index += imbalance
                logger.debug(
                    f"  {interval_key.strftime('%H:%M')}: arr={arr}, dep={dep}, imbalance={imbalance:.2f}"
                )

        logger.debug(
            f"LHR_INDEX: {lhr_index:.2f} "
            f"(across {len(interval_counts)} intervals, "
            f"total arr={sum(c['arrivals'] for c in interval_counts.values())}, "
            f"dep={sum(c['departures'] for c in interval_counts.values())})"
        )

        return lhr_index

    def get_lhr_count_estimate(self, use_cache: bool = True) -> Optional[int]:
        """Get current estimate of LHR_COUNT settlement value.

        Args:
            use_cache: Whether to use cache (STRONGLY RECOMMENDED)

        Returns:
            Estimated LHR_COUNT or None
        """
        try:
            # Fetch last 12h + forecast next 12h (max window per call)
            # Need 2 calls to cover 24h
            data1 = self.fetch_flights_relative(
                offset_minutes=-720,
                duration_minutes=720,
                use_cache=use_cache,
            )
            data2 = self.fetch_flights_relative(
                offset_minutes=0,
                duration_minutes=720,
                use_cache=use_cache,
            )

            # Combine results
            combined = {
                "arrivals": data1.get("arrivals", []) + data2.get("arrivals", []),
                "departures": data1.get("departures", []) + data2.get("departures", []),
            }

            return self.calculate_lhr_count(combined)

        except Exception as e:
            logger.error(f"Failed to estimate LHR_COUNT: {e}")
            return None

    def get_lhr_index_estimate(self, use_cache: bool = True) -> Optional[float]:
        """Get current estimate of LHR_INDEX settlement value.

        Args:
            use_cache: Whether to use cache (STRONGLY RECOMMENDED)

        Returns:
            Estimated LHR_INDEX or None
        """
        try:
            # Fetch last 12h + forecast next 12h (max window per call)
            # Need 2 calls to cover 24h
            data1 = self.fetch_flights_relative(
                offset_minutes=-720,
                duration_minutes=720,
                use_cache=use_cache,
            )
            data2 = self.fetch_flights_relative(
                offset_minutes=0,
                duration_minutes=720,
                use_cache=use_cache,
            )

            # Combine results
            combined = {
                "arrivals": data1.get("arrivals", []) + data2.get("arrivals", []),
                "departures": data1.get("departures", []) + data2.get("departures", []),
            }

            return self.calculate_lhr_index(combined)

        except Exception as e:
            logger.error(f"Failed to estimate LHR_INDEX: {e}")
            return None


# Global client instance
_global_client: Optional[FlightsClient] = None


def get_flights_client() -> FlightsClient:
    """Get global flights client instance.

    Returns:
        Global FlightsClient instance
    """
    global _global_client
    if _global_client is None:
        _global_client = FlightsClient()
    return _global_client
