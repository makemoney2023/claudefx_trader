"""Shared spread state policy for entry, exit, and pre-Claude gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Max spread thresholds in price units (shared with OrderManager historically).
MAX_SPREAD_THRESHOLDS = {
    "EURUSD": 0.0005, "GBPUSD": 0.0005, "AUDUSD": 0.0005,
    "NZDUSD": 0.0005, "USDCHF": 0.0005, "USDCAD": 0.0005,
    "USDJPY": 0.05,
    "EURGBP": 0.0008, "EURJPY": 0.08, "GBPJPY": 0.08,
    "AUDJPY": 0.08, "EURAUD": 0.0008, "GBPAUD": 0.0008,
    "AUDCAD": 0.0008, "AUDCHF": 0.0008, "EURCHF": 0.0008,
    "EURCAD": 0.0008, "EURNZD": 0.0008, "GBPCAD": 0.0008,
    "GBPCHF": 0.0008, "GBPNZD": 0.0008, "NZDJPY": 0.08,
    "NZDCAD": 0.0008, "NZDCHF": 0.0008, "CADJPY": 0.08,
    "CADCHF": 0.0008, "CHFJPY": 0.08,
    "XAUUSD": 0.80,
    "XAGUSD": 0.08,
    "USOIL": 0.10, "WTIUSD": 0.10, "XTIUSD": 0.10,
    "BRENT": 0.10, "UKOIL": 0.10, "XBRUSD": 0.10,
    "US30": 5.0, "DJ30": 5.0,
    "NAS100": 3.0, "USTEC": 3.0,
    "US500": 1.5, "SP500": 1.5,
}

_CRYPTO_FRAGMENTS = (
    "BTC", "ETH", "XRP", "ADA", "LTC", "DOGE", "SOL", "DOT", "DASH",
)


@dataclass
class SpreadState:
    state: str  # normal | elevated | blocked | unavailable
    allows_trading: bool
    spread: float = 0.0
    max_spread: float = 0.0
    reason: str = ""


def max_spread_for_symbol(symbol: str, mid_price: float = 0.0) -> float:
    upper = (symbol or "").upper()
    if upper in MAX_SPREAD_THRESHOLDS:
        return MAX_SPREAD_THRESHOLDS[upper]
    if any(c in upper for c in _CRYPTO_FRAGMENTS):
        return (mid_price or 1.0) * 0.005
    return 0.0005


def evaluate_spread_state(
    symbol: str,
    *,
    spread: Optional[float],
    mid_price: float = 0.0,
    unavailable: bool = False,
    live_mode: bool = False,
    elevated_ratio: float = 0.70,
) -> SpreadState:
    """Classify spread into normal/elevated/blocked/unavailable."""
    if unavailable or spread is None:
        if live_mode:
            return SpreadState(
                state="unavailable",
                allows_trading=False,
                reason=f"{symbol}: spread unavailable — fail-closed in live mode",
            )
        return SpreadState(
            state="unavailable",
            allows_trading=True,
            reason=f"{symbol}: spread unavailable — fail-open (non-live)",
        )

    max_spread = max_spread_for_symbol(symbol, mid_price=mid_price)
    if spread > max_spread:
        return SpreadState(
            state="blocked",
            allows_trading=False,
            spread=spread,
            max_spread=max_spread,
            reason=(
                f"{symbol}: spread {spread:.5f} > max {max_spread:.5f}"
            ),
        )
    if max_spread > 0 and spread >= max_spread * elevated_ratio:
        return SpreadState(
            state="elevated",
            allows_trading=True,
            spread=spread,
            max_spread=max_spread,
            reason=f"{symbol}: elevated spread {spread:.5f}",
        )
    return SpreadState(
        state="normal",
        allows_trading=True,
        spread=spread,
        max_spread=max_spread,
    )
