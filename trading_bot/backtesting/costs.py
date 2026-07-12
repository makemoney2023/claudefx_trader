"""Symbol-specific execution cost helpers for backtests."""

from ..config import get_symbol_spec

# Approximate overnight swap cost in R-units per 5 held bars (~5 hours on M15)
SWAP_R_PER_5_BARS = {
    "forex": 0.01,
    "metal": 0.015,
    "index": 0.02,
    "crypto": 0.0,
}


def spread_cost_r(symbol: str) -> float:
    """Spread/slippage cost in R units at entry."""
    spec = get_symbol_spec(symbol)
    if spec and spec.category == "crypto":
        return 0.08
    if spec and spec.category == "index":
        return 0.06
    if spec and spec.category == "metal":
        return 0.05
    return 0.04


def swap_cost_r(symbol: str, bars_held: int) -> float:
    """Overnight carry modeled as R drag proportional to hold time."""
    spec = get_symbol_spec(symbol)
    category = spec.category if spec else "forex"
    rate = SWAP_R_PER_5_BARS.get(category, 0.01)
    return rate * max(0, bars_held // 5)


def apply_symbol_execution_costs(
    symbol: str,
    r_multiple: float,
    *,
    bars_held: int = 0,
) -> float:
    """Spread/slippage at entry plus optional swap drag for held bars."""
    return r_multiple - spread_cost_r(symbol) - swap_cost_r(symbol, bars_held)
