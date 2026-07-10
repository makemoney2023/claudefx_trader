"""Symbol-specific execution cost helpers for backtests."""

from ..config import get_symbol_spec


def apply_symbol_execution_costs(symbol: str, r_multiple: float) -> float:
    """Spread/slippage cost adjustment in R units using symbol spec."""
    spec = get_symbol_spec(symbol)
    spread_r = 0.05
    if spec and spec.category in ("forex", "metal", "index", "crypto"):
        if spec.category == "crypto":
            spread_r = 0.08
        elif spec.category == "index":
            spread_r = 0.06
        elif spec.category == "metal":
            spread_r = 0.05
        else:
            spread_r = 0.04
    return r_multiple - spread_r
