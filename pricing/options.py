"""Option pricing for LON_FLY
"""

from typing import Optional

from config.products import LON_FLY_STRIKES, LON_FLY_STRUCTURE
from pricing.settlement import calculate_option_payoff
from utils.logger import get_logger

logger = get_logger(__name__)


def price_lon_fly(
    etf_value: float,
    volatility: Optional[float] = None,
    time_to_settlement: Optional[float] = None,
) -> float:
    strikes = LON_FLY_STRIKES
    structure = LON_FLY_STRUCTURE

    # Calculate intrinsic value of each leg
    put_6200_value = calculate_option_payoff(etf_value, strikes["put_6200"], "put")
    call_6200_value = calculate_option_payoff(etf_value, strikes["call_6200"], "call")
    call_6600_value = calculate_option_payoff(etf_value, strikes["call_6600"], "call")
    call_7000_value = calculate_option_payoff(etf_value, strikes["call_7000"], "call")

    # Apply structure coefficients
    lon_fly_value = (
        structure["put_6200"] * put_6200_value +
        structure["call_6200"] * call_6200_value +
        structure["call_6600"] * call_6600_value +
        structure["call_7000"] * call_7000_value
    )

    logger.debug(
        f"LON_FLY pricing: {lon_fly_value:.2f} (ETF: {etf_value:.2f})"
    )

    return lon_fly_value
