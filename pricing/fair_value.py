"""Fair value estimation for all products.

Estimates theoretical fair value based on current data and forecasts.
"""

from typing import Optional, Dict
import time

from data.weather import get_weather_client
from data.tides import get_tides_client
from data.flights import get_flights_client
from utils.logger import get_logger

logger = get_logger(__name__)


class FairValueEstimator:
    """Estimates fair value for all products based on current data."""

    def __init__(self):
        self.weather_client = get_weather_client()
        self.tides_client = get_tides_client()
        self.flights_client = get_flights_client()

        # Throttling for get_all_fair_values()
        self._last_calculation_time: Optional[float] = None
        self._cached_fair_values: Optional[Dict[str, Optional[float]]] = None
        self._throttle_interval_seconds = 30.0  # Recalculate at most once per 30 seconds

    def get_tide_spot_fair_value(self, use_cache: bool = True) -> Optional[float]:
        try:
            return self.tides_client.get_current_tide_spot(use_cache=use_cache)
        except Exception as e:
            logger.error(f"Failed to get TIDE_SPOT fair value: {e}")
            return None

    def get_tide_swing_fair_value(self, use_cache: bool = True) -> Optional[float]:
        try:
            return self.tides_client.get_tide_swing_estimate(use_cache=use_cache)
        except Exception as e:
            logger.error(f"Failed to get TIDE_SWING fair value: {e}")
            return None

    def get_wx_spot_fair_value(self, use_cache: bool = True) -> Optional[float]:
        try:
            return self.weather_client.get_current_wx_spot(use_cache=use_cache)
        except Exception as e:
            logger.error(f"Failed to get WX_SPOT fair value: {e}")
            return None

    def get_wx_sum_fair_value(self, use_cache: bool = True) -> Optional[float]:
        try:
            return self.weather_client.get_wx_sum_estimate(use_cache=use_cache)
        except Exception as e:
            logger.error(f"Failed to get WX_SUM fair value: {e}")
            return None

    def get_lhr_count_fair_value(self, use_cache: bool = True) -> Optional[int]:
        try:
            return self.flights_client.get_lhr_count_estimate(use_cache=use_cache)
        except Exception as e:
            logger.error(f"Failed to get LHR_COUNT fair value: {e}")
            return None

    def get_lhr_index_fair_value(self, use_cache: bool = True) -> Optional[float]:
        try:
            return self.flights_client.get_lhr_index_estimate(use_cache=use_cache)
        except Exception as e:
            logger.error(f"Failed to get LHR_INDEX fair value: {e}")
            return None

    def get_lon_etf_fair_value(
        self,
        tide_spot_fv: Optional[float] = None,
        wx_spot_fv: Optional[float] = None,
        lhr_count_fv: Optional[int] = None,
        use_cache: bool = True,
    ) -> Optional[float]:
        """Estimate fair value for LON_ETF.

        LON_ETF = TIDE_SPOT + WX_SPOT + LHR_COUNT
        """
        try:
            # Fetch component fair values if not provided
            if tide_spot_fv is None:
                tide_spot_fv = self.get_tide_spot_fair_value(use_cache=use_cache)
            if wx_spot_fv is None:
                wx_spot_fv = self.get_wx_spot_fair_value(use_cache=use_cache)
            if lhr_count_fv is None:
                lhr_count_fv = self.get_lhr_count_fair_value(use_cache=use_cache)

            # Check if all components available
            if tide_spot_fv is None or wx_spot_fv is None or lhr_count_fv is None:
                logger.warning("Missing component fair values for LON_ETF")
                return None

            etf_fv = tide_spot_fv + wx_spot_fv + lhr_count_fv

            logger.debug(
                f"LON_ETF fair value: {etf_fv:.2f} "
                f"(TIDE: {tide_spot_fv:.2f}, WX: {wx_spot_fv:.2f}, LHR: {lhr_count_fv:.2f})"
            )

            return etf_fv

        except Exception as e:
            logger.error(f"Failed to get LON_ETF fair value: {e}")
            return None

    def get_lon_fly_fair_value(
        self,
        etf_fv: Optional[float] = None,
        use_cache: bool = True,
    ) -> Optional[float]:
        """Estimate fair value for LON_FLY.

        Uses option pricing on LON_ETF fair value."""


        try:
            if etf_fv is None:
                etf_fv = self.get_lon_etf_fair_value(use_cache=use_cache)

            if etf_fv is None:
                logger.warning("Missing ETF fair value for LON_FLY")
                return None

            # Use options pricing module
            from pricing.options import price_lon_fly
            return price_lon_fly(etf_fv)

        except Exception as e:
            logger.error(f"Failed to get LON_FLY fair value: {e}")
            return None

    def get_all_fair_values(self, use_cache: bool = True) -> Dict[str, Optional[float]]:
        
        # Check if we can use cached calculation (throttling)
        current_time = time.time()
        if (
            self._cached_fair_values is not None
            and self._last_calculation_time is not None
            and (current_time - self._last_calculation_time) < self._throttle_interval_seconds
        ):
            logger.debug(
                f"Using cached fair values (last calculated "
                f"{current_time - self._last_calculation_time:.2f}s ago)"
            )
            return self._cached_fair_values

        logger.debug("Calculating fair values for all products...")

        # Fetch base products
        tide_spot_fv = self.get_tide_spot_fair_value(use_cache=use_cache)
        tide_swing_fv = self.get_tide_swing_fair_value(use_cache=use_cache)
        wx_spot_fv = self.get_wx_spot_fair_value(use_cache=use_cache)
        wx_sum_fv = self.get_wx_sum_fair_value(use_cache=use_cache)
        lhr_count_fv = self.get_lhr_count_fair_value(use_cache=use_cache)
        lhr_index_fv = self.get_lhr_index_fair_value(use_cache=use_cache)

        # Derived products
        etf_fv = self.get_lon_etf_fair_value(
            tide_spot_fv=tide_spot_fv,
            wx_spot_fv=wx_spot_fv,
            lhr_count_fv=lhr_count_fv,
            use_cache=use_cache,
        )
        fly_fv = self.get_lon_fly_fair_value(etf_fv=etf_fv, use_cache=use_cache)

        fair_values = {
            "TIDE_SPOT": tide_spot_fv,
            "TIDE_SWING": tide_swing_fv,
            "WX_SPOT": wx_spot_fv,
            "WX_SUM": wx_sum_fv,
            "LHR_COUNT": lhr_count_fv,
            "LHR_INDEX": lhr_index_fv,
            "LON_ETF": etf_fv,
            "LON_FLY": fly_fv,
        }

        logger.debug("Fair values calculated:")
        for product, fv in fair_values.items():
            if fv is not None:
                logger.debug(f"  {product:12s}: {fv:.2f}")
            else:
                logger.debug(f"  {product:12s}: N/A")

        # Update cache
        self._cached_fair_values = fair_values
        self._last_calculation_time = current_time

        return fair_values


# Global estimator instance
_global_estimator: Optional[FairValueEstimator] = None


def get_fair_value_estimator() -> FairValueEstimator:
    """Get global fair value estimator instance.

    Returns:
        Global FairValueEstimator instance
    """
    global _global_estimator
    if _global_estimator is None:
        _global_estimator = FairValueEstimator()
    return _global_estimator
