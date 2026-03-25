import math
from datetime import datetime
from typing import Optional

import pytz


def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")
    return round(price / tick_size) * tick_size


def floor_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")
    return math.floor(price / tick_size) * tick_size


def ceil_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")
    return math.ceil(price / tick_size) * tick_size


def calculate_mid(best_bid: Optional[float], best_ask: Optional[float]) -> Optional[float]:
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / 2


def calculate_spread(best_bid: Optional[float], best_ask: Optional[float]) -> Optional[float]:
    if best_bid is None or best_ask is None:
        return None
    return best_ask - best_bid


def get_london_time() -> datetime:
    london_tz = pytz.timezone("Europe/London")
    return datetime.now(london_tz)



def parse_iso_timestamp(timestamp_str: str) -> datetime:
    # Handle both with and without fractional seconds
    # Remove 'Z' and parse
    if timestamp_str.endswith("Z"):
        timestamp_str = timestamp_str[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        # Fallback for non-standard fractional seconds
        # Truncate to 6 digits (microseconds)
        if "." in timestamp_str:
            parts = timestamp_str.split(".")
            if len(parts[1]) > 6:
                # Extract fractional seconds and timezone separately
                frac_part = parts[1]
                # Find timezone marker (+ or -)
                tz_idx = max(frac_part.rfind('+'), frac_part.rfind('-'))
                if tz_idx > 0:
                    # Has timezone - keep first 6 digits of fractional + timezone
                    timestamp_str = f"{parts[0]}.{frac_part[:6]}{frac_part[tz_idx:]}"
                else:
                    # No timezone - just keep first 6 digits
                    timestamp_str = f"{parts[0]}.{frac_part[:6]}"
        return datetime.fromisoformat(timestamp_str)


def celsius_to_fahrenheit(celsius: float) -> float:
    return (celsius * 9/5) + 32


