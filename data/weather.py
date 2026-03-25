"""Weather data client for Open-Meteo API.

Fetches London weather data with 15-minute resolution.
Includes historical observations and forecasts.
"""

from typing import Optional

import pandas as pd
import requests

from config.settings import LONDON_LAT, LONDON_LON, LONDON_TIMEZONE, WEATHER_CACHE_TTL_SECONDS
from config.products import celsius_to_fahrenheit
from data.cache import get_cache
from utils.logger import get_logger

logger = get_logger(__name__)


class WeatherClient:
    """Client for Open-Meteo weather API."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self):
        """Initialize weather client."""
        self.cache = get_cache()

    def fetch_weather(
        self,
        past_steps: int = 96,
        forecast_steps: int = 96,
        lat: float = LONDON_LAT,
        lon: float = LONDON_LON,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch 15-minute weather data for London.

        Args:
            past_steps: Number of 15-min steps to fetch from past (96 = 24h)
            forecast_steps: Number of 15-min steps to forecast (96 = 24h)
            lat: Latitude
            lon: Longitude
            use_cache: Whether to use cache

        Returns:
            DataFrame with columns:
                - time: Timestamp (London timezone)
                - temperature_c: Temperature in Celsius
                - temperature_f: Temperature in Fahrenheit
                - apparent_temperature: Feels-like temperature (Celsius)
                - humidity: Relative humidity (%)
                - precipitation: Precipitation (mm)
                - wind_speed: Wind speed (m/s)
                - cloud_cover: Cloud cover (%)
                - visibility: Visibility (meters)
                - wx_spot_value: temp_F * humidity (for WX_SPOT)
        """
        # Check cache
        cache_params = {
            "past_steps": past_steps,
            "forecast_steps": forecast_steps,
            "lat": lat,
            "lon": lon,
        }

        if use_cache:
            cached = self.cache.get("weather", cache_params, WEATHER_CACHE_TTL_SECONDS)
            if cached is not None:
                logger.debug("Weather data loaded from cache")
                df = pd.DataFrame(cached)
                # Convert time strings back to datetime
                df["time"] = pd.to_datetime(df["time"])
                return df

        # Fetch from API
        logger.debug(f"Fetching weather: past={past_steps}, forecast={forecast_steps}")

        variables = (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,wind_speed_10m,cloud_cover,visibility"
        )

        params = {
            "latitude": lat,
            "longitude": lon,
            "minutely_15": variables,
            "past_minutely_15": past_steps,
            "forecast_minutely_15": forecast_steps,
            "timezone": LONDON_TIMEZONE,
        }

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Parse response
            minutely = data["minutely_15"]
            df = pd.DataFrame({
                "time": pd.to_datetime(minutely["time"]).tz_localize(LONDON_TIMEZONE),
                "temperature_c": minutely["temperature_2m"],
                "apparent_temperature": minutely["apparent_temperature"],
                "humidity": minutely["relative_humidity_2m"],
                "precipitation": minutely["precipitation"],
                "wind_speed": minutely["wind_speed_10m"],
                "cloud_cover": minutely["cloud_cover"],
                "visibility": minutely["visibility"],
            })

            # Add derived fields
            df["temperature_f"] = df["temperature_c"].apply(celsius_to_fahrenheit)
            df["wx_spot_value"] = df["temperature_f"] * df["humidity"]

            logger.debug(f"Fetched {len(df)} weather records")

            # Cache the result (convert timestamps to strings for JSON serialization)
            if use_cache:
                df_cache = df.copy()
                df_cache["time"] = df_cache["time"].astype(str)
                self.cache.set("weather", cache_params, df_cache.to_dict("records"))

            return df

        except requests.RequestException as e:
            logger.error(f"Failed to fetch weather data: {e}")
            raise

    def get_current_wx_spot(self, use_cache: bool = True) -> Optional[float]:
        """Get current WX_SPOT value (temp_F * humidity).

        Args:
            use_cache: Whether to use cache

        Returns:
            Current WX_SPOT value or None if unavailable
        """
        try:
            df = self.fetch_weather(past_steps=1, forecast_steps=0, use_cache=use_cache)
            if df.empty:
                return None
            return df.iloc[-1]["wx_spot_value"]
        except Exception as e:
            logger.error(f"Failed to get current WX_SPOT: {e}")
            return None

    def get_wx_sum_estimate(self, use_cache: bool = True) -> Optional[float]:
        """Estimate WX_SUM settlement value based on current forecast.

        WX_SUM = sum(temp_F * humidity) over 96 intervals / 100

        Args:
            use_cache: Whether to use cache

        Returns:
            Estimated WX_SUM settlement value or None
        """
        try:
            # Fetch full 24h forecast
            df = self.fetch_weather(past_steps=96, forecast_steps=96, use_cache=use_cache)
            if df.empty or len(df) < 96:
                logger.warning(f"Insufficient weather data: {len(df)} records")
                return None

            # Take last 96 intervals (most recent + forecast)
            recent_df = df.tail(96)

            # Calculate sum
            wx_sum = recent_df["wx_spot_value"].sum() / 100
            logger.debug(f"WX_SUM estimate: {wx_sum:.2f}")

            return wx_sum

        except Exception as e:
            logger.error(f"Failed to estimate WX_SUM: {e}")
            return None


# Global client instance
_global_client: Optional[WeatherClient] = None


def get_weather_client() -> WeatherClient:
    """Get global weather client instance.

    Returns:
        Global WeatherClient instance
    """
    global _global_client
    if _global_client is None:
        _global_client = WeatherClient()
    return _global_client
