"""Live trade execution coordinator — position conflicts, order normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from ..services.gate_outcome import GateOutcome


def check_position_conflicts(
    existing_positions: List[Any],
    direction: str,
) -> GateOutcome:
    """Block opposite-direction conflict or same-direction stacking."""
    if not existing_positions:
        return GateOutcome.pass_through("position_check")

    opposite_dir = "short" if direction == "long" else "long"
    conflicting = [p for p in existing_positions if p.direction == opposite_dir]
    if conflicting:
        return GateOutcome.block(
            gate_id="position_conflict",
            reason=f"Opposite {opposite_dir} position open (ticket={conflicting[0].ticket})",
            stage="position_conflict",
        )

    same_dir = [p for p in existing_positions if p.direction == direction]
    if same_dir:
        return GateOutcome.block(
            gate_id="position_stacking",
            reason="Same-direction position already open",
            stage="position_stacking",
        )

    return GateOutcome.pass_through("position_check")


def auto_convert_to_pending(
    order_type: str,
    direction: str,
    entry_price: float,
    current_price: float,
) -> str:
    """Convert market to limit/stop when entry differs from market."""
    if order_type != "market" or not entry_price or current_price <= 0:
        return order_type
    price_diff_pct = abs(entry_price - current_price) / current_price
    if price_diff_pct <= 0.001:
        return order_type
    if direction == "long":
        return "buy_limit" if entry_price < current_price else "buy_stop"
    return "sell_limit" if entry_price > current_price else "sell_stop"


def fix_limit_stop_labels(
    order_type: str,
    entry_price: float,
    current_price: float,
) -> str:
    """Fix mislabeled limit/stop relative to current price."""
    if order_type not in ("buy_limit", "sell_limit", "buy_stop", "sell_stop"):
        return order_type
    if not entry_price or current_price <= 0:
        return order_type
    if order_type == "buy_limit" and entry_price > current_price * 1.001:
        return "buy_stop"
    if order_type == "sell_limit" and entry_price < current_price * 0.999:
        return "sell_stop"
    if order_type == "buy_stop" and entry_price < current_price * 0.999:
        return "buy_limit"
    if order_type == "sell_stop" and entry_price > current_price * 1.001:
        return "sell_limit"
    return order_type


def validate_limit_zone(
    order_type: str,
    retrace_pct: Optional[float],
) -> GateOutcome:
    """ICT zone validation for limit orders."""
    if order_type not in ("buy_limit", "sell_limit") or retrace_pct is None:
        return GateOutcome.pass_through("limit_zone")

    if order_type == "buy_limit" and retrace_pct > 0.70:
        return GateOutcome.block(
            gate_id="zone_block",
            reason=f"buy_limit in premium zone ({retrace_pct:.0%})",
            stage="limit_zone",
        )
    if order_type == "sell_limit" and retrace_pct < 0.30:
        return GateOutcome.block(
            gate_id="zone_block",
            reason=f"sell_limit in discount zone ({retrace_pct:.0%})",
            stage="limit_zone",
        )
    outcome = GateOutcome.pass_through("limit_zone")
    if order_type == "buy_limit" and retrace_pct > 0.55:
        outcome.confidence_cap = 0.60
    elif order_type == "sell_limit" and retrace_pct < 0.45:
        outcome.confidence_cap = 0.60
    return outcome


def resolve_premium_discount(analysis_results: dict) -> Tuple[Optional[str], Optional[float]]:
    try:
        pd_data = analysis_results.get("premium_discount", {})
        if isinstance(pd_data, dict):
            return pd_data.get("current_zone"), pd_data.get("retracement_percent")
        if hasattr(pd_data, "current_zone"):
            zone = (
                pd_data.current_zone.value
                if hasattr(pd_data.current_zone, "value")
                else str(pd_data.current_zone)
            )
            return zone, getattr(pd_data, "retracement_percent", None)
    except Exception:
        pass
    return None, None


@dataclass
class ExecutionPrepResult:
    order_type: str
    entry_price: float
    blocked: bool = False
    gate_id: str = ""
    reason: str = ""
    confidence_cap: Optional[float] = None


class ExecutionCoordinator:
    """Prepares order type and pre-flight execution checks."""

    def prepare_order(
        self,
        *,
        trade_signal: Any,
        current_price: float,
        existing_positions: Optional[List[Any]],
        analysis_results: dict,
    ) -> ExecutionPrepResult:
        direction = trade_signal.direction
        entry_price = trade_signal.entry_price or current_price
        order_type = getattr(trade_signal, "order_type", "market") or "market"

        if existing_positions:
            conflict = check_position_conflicts(existing_positions, direction)
            if conflict.blocked:
                return ExecutionPrepResult(
                    order_type=order_type,
                    entry_price=entry_price,
                    blocked=True,
                    gate_id=conflict.gate_id,
                    reason=conflict.reason,
                )

        order_type = auto_convert_to_pending(
            order_type, direction, trade_signal.entry_price or 0, current_price
        )
        order_type = fix_limit_stop_labels(order_type, entry_price, current_price)
        trade_signal.order_type = order_type

        _, retrace = resolve_premium_discount(analysis_results)
        zone_outcome = validate_limit_zone(order_type, retrace)
        if zone_outcome.blocked:
            return ExecutionPrepResult(
                order_type=order_type,
                entry_price=entry_price,
                blocked=True,
                gate_id=zone_outcome.gate_id,
                reason=zone_outcome.reason,
            )

        cap = zone_outcome.confidence_cap
        if cap is not None:
            trade_signal.confidence = min(float(trade_signal.confidence), cap)

        return ExecutionPrepResult(
            order_type=order_type,
            entry_price=entry_price,
            confidence_cap=cap,
        )
