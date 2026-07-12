"""Shared live-trading gate helpers used by main and replay for parity."""

from __future__ import annotations

from typing import Optional, Tuple

from ..execution.scaling_position_sizer import (
    ScalingPositionSizer,
    compute_actual_risk_dollars,
    verify_post_sizing_risk,
)
from ..utils.logging import get_logger

logger = get_logger(__name__)


def effective_max_daily_trades(
    equity: float,
    position_sizer: Optional[ScalingPositionSizer],
    scaling_manager=None,
    config_cap: Optional[int] = None,
    gate_override: Optional[int] = None,
) -> int:
    """
    Effective daily trade cap: min(tier limit, scaling-mode limit, config cap).

    Tier limits (2–5) are the primary guardrail; mode/config caps cannot exceed intent.
    """
    tier_limit = 5
    if position_sizer is not None:
        tier_limit = position_sizer.get_tier(equity).max_daily_trades

    mode_limit = tier_limit
    if scaling_manager is not None:
        mode_limit = scaling_manager.get_mode_config().max_daily_trades

    cap = min(tier_limit, mode_limit)
    if gate_override is not None:
        cap = min(cap, gate_override)
    if config_cap is not None:
        cap = min(cap, config_cap)
    return max(1, cap)


def compute_booked_risk_percent(
    lots: float,
    entry_price: float,
    stop_loss: float,
    symbol: str,
    account_balance: float,
) -> float:
    """Book daily risk using actual $ at SL, not nominal tier percentage."""
    if account_balance <= 0:
        return 0.0
    risk_dollars = compute_actual_risk_dollars(
        lots, entry_price, stop_loss, symbol
    )
    return risk_dollars / account_balance


def apply_post_sizing_verification(
    final_lots: float,
    target_lots: float,
    entry_price: float,
    stop_loss: float,
    symbol: str,
) -> Tuple[float, Optional[str]]:
    """
    Verify final lots do not exceed target risk tolerance after all multipliers.

    Returns (adjusted_lots, reject_reason).
    """
    adjusted, _actual, reason = verify_post_sizing_risk(
        final_lots=final_lots,
        target_lots=target_lots,
        entry_price=entry_price,
        stop_loss=stop_loss,
        symbol=symbol,
    )
    if reason:
        return 0.0, reason
    return adjusted, None


def news_allows_trading(news_service) -> Tuple[bool, str]:
    """Fail-closed calendar check — blocks when feed is stale/unreliable."""
    if news_service is None:
        return True, "no news service"
    if not news_service.should_trade():
        return False, "news calendar unavailable or stale — fail-closed"
    return True, "ok"


def symbol_edge_allows_trading(
    scaling_manager,
    symbol: str,
    session: str,
) -> Tuple[bool, str, float]:
    """
    Edge-health symbol blocking from scaling manager win-rate tracking.

    Returns (allowed, reason, size_multiplier).
    """
    if scaling_manager is None:
        return True, "no scaling manager", 1.0
    return scaling_manager.should_trade_symbol(symbol, session)
