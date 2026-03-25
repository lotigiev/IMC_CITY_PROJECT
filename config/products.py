"""Product specifications and settlement formulas. Contains detailed information about all 8 products traded on the CMI Exchange,
including settlement formulas, strikes, and product relationships.
"""

from dataclasses import dataclass
from enum import Enum

from utils.helpers import celsius_to_fahrenheit


class ProductType(Enum):
    """Product classification."""
    SPOT = "spot"  # Direct measurement
    DERIVATIVE = "derivative"  # Derived from underlying data
    BASKET = "basket"  # Combination of other products
    OPTION = "option"  # Options structure


@dataclass(frozen=True)
class ProductSpec:
    """Specification for a single product."""
    symbol: str
    product_type: ProductType
    tick_size: float
    starting_price: int
    contract_size: int
    description: str
    settlement_formula: str
    underlying_data: list[str]  # Data sources required for settlement


# Product Specifications
TIDE_SPOT = ProductSpec(
    symbol="TIDE_SPOT",
    product_type=ProductType.SPOT,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Absolute tidal height at Westminster at settlement time",
    settlement_formula="abs(tidal_level_mAOD) * 1000 (mm)",
    underlying_data=["thames_tidal_level"],
)

TIDE_SWING = ProductSpec(
    symbol="TIDE_SWING",
    product_type=ProductType.DERIVATIVE,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Sum of strangle payoffs on 15-min tidal changes",
    settlement_formula="Σ[max(0, 20 - |Δ|*100) + max(0, |Δ|*100 - 25)] over 24h",
    underlying_data=["thames_tidal_level"],
)

WX_SPOT = ProductSpec(
    symbol="WX_SPOT",
    product_type=ProductType.SPOT,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Temperature (°F) × Humidity (%) at settlement time",
    settlement_formula="temp_F * humidity_%",
    underlying_data=["weather"],
)

WX_SUM = ProductSpec(
    symbol="WX_SUM",
    product_type=ProductType.DERIVATIVE,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Sum of (temp_F × humidity_%) over 24h / 100",
    settlement_formula="Σ(temp_F * humidity_%) / 100 over 24h",
    underlying_data=["weather"],
)

LHR_COUNT = ProductSpec(
    symbol="LHR_COUNT",
    product_type=ProductType.SPOT,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Total flights (arrivals + departures) in 24h",
    settlement_formula="count(arrivals) + count(departures)",
    underlying_data=["flights"],
)

LHR_INDEX = ProductSpec(
    symbol="LHR_INDEX",
    product_type=ProductType.DERIVATIVE,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Flight imbalance metric per 30-min interval",
    settlement_formula="Σ|abs((arr - dep)/(arr + dep)) * 100| per 30-min",
    underlying_data=["flights"],
)

LON_ETF = ProductSpec(
    symbol="LON_ETF",
    product_type=ProductType.BASKET,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Basket of TIDE_SPOT + WX_SPOT + LHR_COUNT",
    settlement_formula="TIDE_SPOT + WX_SPOT + LHR_COUNT",
    underlying_data=["thames_tidal_level", "weather", "flights"],
)

LON_FLY = ProductSpec(
    symbol="LON_FLY",
    product_type=ProductType.OPTION,
    tick_size=1.0,
    starting_price=10,
    contract_size=1,
    description="Options structure: 2×Put(6200) + Call(6200) - 2×Call(6600) + 3×Call(7000)",
    settlement_formula="2*max(0,6200-ETF) + max(0,ETF-6200) - 2*max(0,ETF-6600) + 3*max(0,ETF-7000)",
    underlying_data=["thames_tidal_level", "weather", "flights"],
)

# Product Registry
PRODUCT_SPECS = {
    "TIDE_SPOT": TIDE_SPOT,
    "TIDE_SWING": TIDE_SWING,
    "WX_SPOT": WX_SPOT,
    "WX_SUM": WX_SUM,
    "LHR_COUNT": LHR_COUNT,
    "LHR_INDEX": LHR_INDEX,
    "LON_ETF": LON_ETF,
    "LON_FLY": LON_FLY,
}

# Option Strikes for LON_FLY
LON_FLY_STRIKES = {
    "put_6200": 6200,
    "call_6200": 6200,
    "call_6600": 6600,
    "call_7000": 7000,
}

# LON_FLY Structure (coefficients)
LON_FLY_STRUCTURE = {
    "put_6200": 2,  # 2× Put(6200)
    "call_6200": 1,  # 1× Call(6200)
    "call_6600": -2,  # -2× Call(6600) [short]
    "call_7000": 3,  # 3× Call(7000)
}

# ETF Components
LON_ETF_COMPONENTS = ["TIDE_SPOT", "WX_SPOT", "LHR_COUNT"]

# TIDE_SWING Strangle Parameters
TIDE_SWING_LOWER_STRIKE = 20  # cm
TIDE_SWING_UPPER_STRIKE = 25  # cm
TIDE_SWING_INTERVAL_MINUTES = 15
TIDE_SWING_NUM_INTERVALS = 96  # 24h / 15min

# WX_SUM Parameters
WX_SUM_INTERVAL_MINUTES = 15
WX_SUM_NUM_INTERVALS = 96  # 24h / 15min
WX_SUM_DIVISOR = 100

# LHR_INDEX Parameters
LHR_INDEX_INTERVAL_MINUTES = 30
LHR_INDEX_NUM_INTERVALS = 48  # 24h / 30min


def get_product_spec(symbol: str) -> ProductSpec:
    """Get product specification by symbol."""
    if symbol not in PRODUCT_SPECS:
        raise ValueError(f"Unknown product symbol: {symbol}")
    return PRODUCT_SPECS[symbol]


