"""Shared live/replay parity gates: zone conversion, DXY haircut, displacement.

These were historically live-only in analyze_and_trade_runner. Pure decision
logic lives here so replay and live share one path (zero-mismatch parity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..utils.win_optimization import (
    displacement_gate_action,
    ote_pullback_entry,
    rebase_sl_tp_for_new_entry,
)

FX_DXY_SYMBOLS = frozenset(
    {"EURUSD", "GBPUSD", "USDCHF", "USDJPY", "AUDUSD", "NZDUSD"}
)


@dataclass
class ParityGateOutcome:
    blocked: bool = False
    gate_id: str = ""
    reason: str = ""
    action: str = "unchanged"
    size_multiplier: float = 1.0
    new_order_type: Optional[str] = None
    new_entry: Optional[float] = None
    new_sl: Optional[float] = None
    new_tp: Optional[float] = None
    gate_path: List[str] = field(default_factory=list)


def evaluate_dxy_parity(
    *,
    symbol: str,
    direction: str,
    dxy_confirmation: Optional[str],
) -> ParityGateOutcome:
    """Haircut size 50% on FX/DXY conflict; never hard-blocks."""
    if not dxy_confirmation:
        return ParityGateOutcome(action="unchanged", gate_path=["dxy_skip"])
    if symbol not in FX_DXY_SYMBOLS:
        return ParityGateOutcome(action="unchanged", gate_path=["dxy_non_fx"])
    if (direction or "").lower() == (dxy_confirmation or "").lower():
        return ParityGateOutcome(
            action="aligned",
            size_multiplier=1.0,
            gate_path=["dxy_aligned"],
        )
    return ParityGateOutcome(
        blocked=False,
        gate_id="dxy_conflict",
        reason=(
            f"{symbol} {direction} conflicts with DXY confirming {dxy_confirmation}"
        ),
        action="size_haircut",
        size_multiplier=0.5,
        gate_path=["dxy_conflict"],
    )


def evaluate_zone_conversion(
    *,
    zone_valid: bool,
    zone_reason: str,
    order_type: str,
    direction: str,
    current_entry: float,
    current_price: float,
    pd_analysis: Any,
    htf_aligned: bool = False,
    has_displacement: bool = False,
    lean_sweep_fade: bool = False,
) -> ParityGateOutcome:
    """
    When premium/discount zone is invalid for a market order, convert to OTE
    limit or block if OTE cannot be resolved.

    HTF-aligned displacement continuation and lean sweep-fade keep the market
    order (do not demote into a pullback limit).
    """
    if zone_valid:
        return ParityGateOutcome(
            action="unchanged",
            gate_path=["zone_ok"],
            reason=zone_reason or "zone valid",
        )

    from .setup_fingerprint import is_htf_displacement_continuation

    if is_htf_displacement_continuation(
        htf_aligned=htf_aligned, has_displacement=has_displacement
    ):
        return ParityGateOutcome(
            action="unchanged",
            gate_path=["zone_continuation_market"],
            reason=(
                zone_reason
                or "zone invalid — HTF+displacement continuation keeps market"
            ),
        )

    if lean_sweep_fade:
        return ParityGateOutcome(
            action="unchanged",
            gate_path=["zone_lean_market"],
            reason=zone_reason or "zone invalid — lean sweep-fade keeps market",
        )

    ot = (order_type or "").lower()
    if ot != "market":
        # Already pending — do not hard-block; zone was advisory for market only
        return ParityGateOutcome(
            action="unchanged",
            gate_path=["zone_invalid_non_market"],
            reason=zone_reason or "zone invalid but non-market",
        )

    if pd_analysis is None:
        return ParityGateOutcome(
            blocked=True,
            gate_id="zone_conversion_failed",
            reason=(
                f"Zone invalid and no OTE entry available: {zone_reason}"
            ),
            action="reject",
            gate_path=["zone_conversion_failed"],
        )

    swing_high = float(getattr(pd_analysis, "swing_high", 0) or 0)
    swing_low = float(getattr(pd_analysis, "swing_low", 0) or 0)
    ote = ote_pullback_entry(direction, swing_high, swing_low)
    if ote <= 0:
        return ParityGateOutcome(
            blocked=True,
            gate_id="zone_conversion_failed",
            reason=(
                f"Zone invalid and no OTE entry available: {zone_reason}"
            ),
            action="reject",
            gate_path=["zone_conversion_failed"],
        )

    old_entry = current_entry or current_price
    new_ot = "buy_limit" if direction == "long" else "sell_limit"
    # Caller supplies current SL/TP via apply helper; here we only resolve entry.
    return ParityGateOutcome(
        blocked=False,
        gate_id="zone_converted",
        reason=zone_reason or "zone invalid — converted to OTE limit",
        action="convert_pending",
        new_order_type=new_ot,
        new_entry=ote,
        gate_path=["zone_converted"],
    )


def apply_zone_conversion_levels(
    outcome: ParityGateOutcome,
    *,
    stop_loss: float,
    take_profit: float,
    old_entry: float,
) -> ParityGateOutcome:
    """Attach rebased SL/TP when zone conversion produced a new entry."""
    if outcome.blocked or outcome.action != "convert_pending" or not outcome.new_entry:
        return outcome
    new_sl, new_tp = rebase_sl_tp_for_new_entry(
        stop_loss=stop_loss or 0.0,
        take_profit=take_profit or 0.0,
        old_entry=old_entry,
        new_entry=outcome.new_entry,
    )
    outcome.new_sl = new_sl
    outcome.new_tp = new_tp
    return outcome


def evaluate_displacement_parity(
    *,
    order_type: str,
    distribution_confirmed: Optional[bool],
    amd_phase: Optional[str],
    htf_aligned: bool = False,
    has_displacement: bool = False,
    lean_sweep_fade: bool = False,
) -> ParityGateOutcome:
    """Shared displacement decision for market orders."""
    from .setup_fingerprint import is_htf_displacement_continuation

    if (order_type or "").lower() == "market" and is_htf_displacement_continuation(
        htf_aligned=htf_aligned, has_displacement=has_displacement
    ):
        return ParityGateOutcome(
            action="allow_market",
            gate_path=["displacement_continuation_ok"],
        )

    if (order_type or "").lower() == "market" and lean_sweep_fade:
        return ParityGateOutcome(
            action="allow_market",
            gate_path=["liquidity_reversal_lean_ok"],
        )

    action = displacement_gate_action(
        order_type,
        distribution_confirmed=distribution_confirmed,
        amd_phase=amd_phase,
    )
    if action == "allow_market":
        return ParityGateOutcome(
            action="allow_market",
            gate_path=["displacement_ok"],
        )
    if action == "unchanged":
        return ParityGateOutcome(
            action="unchanged",
            gate_path=["displacement_skip"],
        )
    if action == "reject":
        return ParityGateOutcome(
            blocked=True,
            gate_id="no_displacement",
            reason=(
                "Market order blocked: displacement not confirmed "
                f"(AMD phase={amd_phase or 'n/a'})"
            ),
            action="reject",
            gate_path=["no_displacement"],
        )
    # convert_pending — entry resolution is separate (needs FVG/OTE context)
    return ParityGateOutcome(
        blocked=False,
        gate_id="displacement_convert",
        reason=f"No displacement — convert market to pending (AMD={amd_phase})",
        action="convert_pending",
        gate_path=["displacement_convert"],
    )


def resolve_displacement_entry(
    *,
    direction: str,
    current_price: float,
    fvg_analysis: Any,
    pd_analysis: Any,
) -> float:
    """Pick FVG midpoint or OTE when converting market → pending for displacement."""
    if direction == "long":
        if hasattr(fvg_analysis, "bullish_fvgs") and fvg_analysis.bullish_fvgs:
            nearest = min(
                fvg_analysis.bullish_fvgs,
                key=lambda x: abs(x.midpoint - current_price),
            )
            return float(nearest.midpoint)
        if pd_analysis is not None:
            return ote_pullback_entry(
                "long",
                float(getattr(pd_analysis, "swing_high", 0) or 0),
                float(getattr(pd_analysis, "swing_low", 0) or 0),
            )
    else:
        if hasattr(fvg_analysis, "bearish_fvgs") and fvg_analysis.bearish_fvgs:
            nearest = min(
                fvg_analysis.bearish_fvgs,
                key=lambda x: abs(x.midpoint - current_price),
            )
            return float(nearest.midpoint)
        if pd_analysis is not None:
            return ote_pullback_entry(
                "short",
                float(getattr(pd_analysis, "swing_high", 0) or 0),
                float(getattr(pd_analysis, "swing_low", 0) or 0),
            )
    return 0.0


def finalize_displacement_conversion(
    outcome: ParityGateOutcome,
    *,
    direction: str,
    current_entry: float,
    current_price: float,
    stop_loss: float,
    take_profit: float,
    fvg_analysis: Any,
    pd_analysis: Any,
) -> ParityGateOutcome:
    """Resolve entry + rebase SL/TP, or block if conversion cannot complete."""
    if outcome.action != "convert_pending":
        return outcome
    new_entry = resolve_displacement_entry(
        direction=direction,
        current_price=current_price,
        fvg_analysis=fvg_analysis,
        pd_analysis=pd_analysis,
    )
    if new_entry <= 0:
        return ParityGateOutcome(
            blocked=True,
            gate_id="displacement_conversion_failed",
            reason="Displacement missing and no FVG/OTE entry available",
            action="reject",
            gate_path=["displacement_conversion_failed"],
        )
    old_entry = current_entry or current_price
    new_sl, new_tp = rebase_sl_tp_for_new_entry(
        stop_loss=stop_loss or 0.0,
        take_profit=take_profit or 0.0,
        old_entry=old_entry,
        new_entry=new_entry,
    )
    return ParityGateOutcome(
        blocked=False,
        gate_id="displacement_converted",
        reason=outcome.reason,
        action="convert_pending",
        new_order_type="buy_limit" if direction == "long" else "sell_limit",
        new_entry=new_entry,
        new_sl=new_sl,
        new_tp=new_tp,
        gate_path=["displacement_converted"],
    )
