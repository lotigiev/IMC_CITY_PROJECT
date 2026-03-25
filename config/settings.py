"""Configuration settings for the trading bot.
Centralizes exchange URLs, credentials, rate limits, + trading parameters.
"""

import os
from pathlib import Path
from typing import Literal

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, will use system env vars

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Exchange URLs
TEST_EXCHANGE_URL = "http://ec2-52-49-69-152.eu-west-1.compute.amazonaws.com/"
CHALLENGE_EXCHANGE_URL = "http://ec2-52-19-74-159.eu-west-1.compute.amazonaws.com/"

# Default exchange (can be overridden)
DEFAULT_EXCHANGE: Literal["test", "challenge"] = "test"


def get_exchange_url(exchange: Literal["test", "challenge"] = DEFAULT_EXCHANGE) -> str:
    """Get the exchange URL based on environment."""
    if exchange == "test":
        return TEST_EXCHANGE_URL
    elif exchange == "challenge":
        return CHALLENGE_EXCHANGE_URL
    else:
        raise ValueError(f"Unknown exchange: {exchange}")


# Credentials (read from environment variables or override here)
USERNAME = os.getenv("CMI_USERNAME", "Brestttt")
PASSWORD = os.getenv("CMI_PASSWORD", "IMC_cmi01*")

# API Rate Limits (from competition rules)
MAX_REQUESTS_PER_SECOND = 1.0  # 1 request per second (excluding SSE)
# Risk Parameters
MAX_POSITION_PER_PRODUCT = 100  # Maximum absolute position per product
MAX_TOTAL_EXPOSURE = 500  # Maximum total exposure across all products
MAX_LOSS_THRESHOLD = -100000  # Stop trading if P&L drops below this

# Data Caching
CACHE_DIR = PROJECT_ROOT / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
WEATHER_CACHE_TTL_SECONDS = 900  # 15 minutes (matches API resolution)
TIDES_CACHE_TTL_SECONDS = 900  # 15 minutes
FLIGHTS_CACHE_TTL_SECONDS = 3600  # 1 hour (API is rate-limited)

# Logging
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# External API Keys
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "YOUR_API_KEY")  # For AeroDataBox flights API

# Geography
LONDON_LAT = 51.5074
LONDON_LON = -0.1278
LONDON_TIMEZONE = "Europe/London"
HEATHROW_IATA = "LHR"
THAMES_MEASURE_ID = "0006-level-tidal_level-i-15_min-mAOD"

# Product Symbols
PRODUCTS = [
    "TIDE_SPOT",
    "TIDE_SWING",
    "WX_SPOT",
    "WX_SUM",
    "LHR_COUNT",
    "LHR_INDEX",
    "LON_ETF",
    "LON_FLY",
]
