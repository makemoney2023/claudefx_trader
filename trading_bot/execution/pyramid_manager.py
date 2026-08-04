"""Confirmation-based pyramid add evaluator (pure logic).

Places at most one same-direction add after the primary reaches +trigger_r
with adequate setup quality, under FINAL-RISK and primary-volume caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ..config import normalize_lots
from .scaling_position_sizer import enforce_final_risk_cap


@dataclass(frozen=True)
class PyramidAddRequest:
    parent_ticket: int
    symbol: str
    direction: str
    lots: float
    stop_loss: float
    take_profit: float
    trigger_r: float
    comment: str = "ICT_pyramid_add"


def _quality_ok(position: Any, min_confidence: float) -> bool:
    if bool(getattr(position, "a_plus", False)):
        return True
    conf = float(getattr(position, "confidence", 0.0) or 0.0)
    return conf + 1e-12 >= float(min_confidence)


def _resolve_take_profit(position: Any) -> float:
    tp3 = float(getattr(position, "tp3", 0.0) or 0.0)
    if tp3 > 0:
        return tp3
    return float(getattr(position, "take_profit", 0.0) or 0.0)


def evaluate_pyramid_add(
    position: Any,
    *,
    config: Any,
    account_equity: float,
    symbol_spec: Any,
    in_loss_cooldown: bool = False,
    has_opposite_position: bool = False,
    add_already_pending: bool = False,
) -> Tuple[Optional[PyramidAddRequest], str]:
    """Return ``(request, reason)``. ``reason == 'ok'`` only when request set."""
    if not bool(getattr(config, "enabled", False)):
        return None, "disabled"

    if not bool(getattr(position, "pyramid_eligible", True)):
        return None, "not_eligible"

    if getattr(position, "pyramid_parent_ticket", None) is not None:
        return None, "not_eligible"

    max_adds = int(getattr(config, "max_adds", 1) or 1)
    used = int(getattr(position, "pyramid_adds_used", 0) or 0)
    if used >= max_adds:
        return None, "max_adds"

    if in_loss_cooldown:
        return None, "loss_cooldown"

    if has_opposite_position:
        return None, "opposite_position"

    if add_already_pending:
        return None, "add_pending"

    trigger_r = float(getattr(config, "trigger_r", 1.0) or 1.0)
    current_r = float(getattr(position, "current_r_multiple", 0.0) or 0.0)
    if current_r + 1e-9 < trigger_r:
        return None, "below_trigger_r"

    min_conf = float(getattr(config, "min_confidence", 0.70) or 0.70)
    if not _quality_ok(position, min_conf):
        return None, "quality_gate"

    primary_vol = float(
        getattr(position, "initial_volume", 0.0)
        or getattr(position, "volume", 0.0)
        or 0.0
    )
    if primary_vol <= 0:
        return None, "undersized"

    size_fraction = min(1.0, max(0.0, float(getattr(config, "size_fraction", 1.0) or 1.0)))
    desired = primary_vol * size_fraction
    symbol = str(getattr(position, "symbol", "") or "")
    desired = normalize_lots(symbol, desired)

    volume_min = float(getattr(symbol_spec, "volume_min", 0.01) or 0.01)
    if desired < volume_min:
        return None, "undersized"

    stop_loss = float(getattr(position, "stop_loss", 0.0) or 0.0)
    take_profit = _resolve_take_profit(position)
    # Size add against current SL (often BE) using current market as entry proxy
    entry_proxy = float(getattr(position, "current_price", 0.0) or 0.0)
    if entry_proxy <= 0:
        entry_proxy = float(getattr(position, "entry_price", 0.0) or 0.0)

    risk_fraction = float(getattr(config, "risk_fraction", 0.01) or 0.01)
    allowed, _loss, reject = enforce_final_risk_cap(
        account_equity,
        risk_fraction,
        entry_proxy,
        stop_loss,
        desired,
        symbol_spec,
        symbol=symbol,
    )
    if reject or allowed < volume_min:
        return None, "final_risk_cap" if reject else "undersized"

    allowed = min(allowed, primary_vol)
    allowed = normalize_lots(symbol, allowed)
    if allowed < volume_min:
        return None, "undersized"

    direction = str(getattr(position, "direction", "") or "")
    return (
        PyramidAddRequest(
            parent_ticket=int(getattr(position, "ticket")),
            symbol=symbol,
            direction=direction,
            lots=float(allowed),
            stop_loss=stop_loss,
            take_profit=take_profit,
            trigger_r=trigger_r,
        ),
        "ok",
    )
