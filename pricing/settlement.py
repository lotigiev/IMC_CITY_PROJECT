"""Settlement value calculations for all products
Implements the exact settlement formulas for each of the 8 products as defined in the competition specification
"""


def calculate_option_payoff(
    underlying: float,
    strike: float,
    option_type: str,
) -> float:
    """Calculate option payoff at settlement.
    """
    if option_type == "call":
        return max(0, underlying - strike)
    elif option_type == "put":
        return max(0, strike - underlying)
    else:
        raise ValueError(f"Invalid option type: {option_type}")
