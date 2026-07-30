"""Cost-adjusted (net) R:R — shared by live gates and replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..backtesting.costs import spread_cost_r
from .gate_outcome import GateOutcome


@dataclass
class NetRrResult:
    raw_rr: float
    net_rr: float
    cost_r: float
    sl_dist: float


def compute_net_rr(
    *,
    entry: float,
    sl: float,
    tp: float,
    symbol: str,
    spread: Optional[float] = None,
    commission_r: float = 0.0,
    slippage_r: float = 0.0,
) -> NetRrResult:
    """Nominal R:R minus spread/commission/slippage drag in R units.

    When a live ``spread`` (price units) is provided, the larger of
    (spread / SL distance) and the category default from ``spread_cost_r``
    is used so backtest/live stay conservative and comparable.
    """
    sl_dist = abs(entry - sl) if sl and entry else 0.0
    tp_dist = abs(tp - entry) if tp and entry else 0.0
    raw = tp_dist / sl_dist if sl_dist > 0 else 0.0

    category_cost = spread_cost_r(symbol) if symbol else 0.04
    live_spread_r = 0.0
    if spread is not None and spread > 0 and sl_dist > 0:
        live_spread_r = spread / sl_dist

    cost = max(category_cost, live_spread_r) + max(0.0, commission_r) + max(0.0, slippage_r)
    net = max(0.0, raw - cost) if raw > 0 else 0.0
    return NetRrResult(raw_rr=raw, net_rr=net, cost_r=cost, sl_dist=sl_dist)


def evaluate_net_rr_floor(
    net_rr: float,
    min_rr: float,
    *,
    is_aggressive: bool = False,
) -> Optional[GateOutcome]:
    """Block when cost-adjusted R:R is below the hard floor / target."""
    if net_rr >= min_rr:
        return None
    hard_floor = 1.0 if is_aggressive else 1.5
    if net_rr < hard_floor:
        return GateOutcome.block(
            gate_id="net_rr_floor",
            reason=(
                f"Net R:R {net_rr:.2f}:1 below hard floor {hard_floor:.1f}:1 "
                f"(after spread/commission)"
            ),
            stage="net_rr_floor",
        )
    # Below target but above hard floor — warn via soft block only when
    # aggressively below target (keep parity with evaluate_rr_hard_floor).
    return None
