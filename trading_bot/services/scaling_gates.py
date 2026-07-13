"""Scaling-mode gate helpers for the trade pipeline."""

from __future__ import annotations

from typing import Optional, Tuple

from .gate_outcome import GateOutcome


def setup_grade_from_confidence(confidence: float) -> str:
    if confidence >= 0.85:
        return "A+"
    if confidence >= 0.75:
        return "A"
    if confidence >= 0.65:
        return "B"
    return "C"


def resolve_min_confidence(
    scaling_manager,
    gate_min_confidence: float,
) -> float:
    if scaling_manager is None:
        return gate_min_confidence
    return max(
        gate_min_confidence,
        scaling_manager.get_mode_config().confidence_threshold,
    )


def evaluate_scaling_gate(
    *,
    setup_grade: str,
    confidence: float,
    daily_trades: int,
    scaling_manager,
) -> GateOutcome:
    if scaling_manager is None:
        return GateOutcome.pass_through("scaling_gate")
    should_trade, reason = scaling_manager.should_take_trade(
        setup_grade=setup_grade,
        confidence=confidence,
        daily_trades=daily_trades,
    )
    if not should_trade:
        return GateOutcome.block(
            gate_id="scaling_manager",
            reason=reason,
            stage="scaling_gate",
            outcome_type="no_trade",
        )
    return GateOutcome.pass_through("scaling_gate")


def evaluate_min_confidence_gate(
    confidence: float,
    min_confidence: float,
) -> GateOutcome:
    if confidence < min_confidence:
        return GateOutcome.block(
            gate_id="min_confidence",
            reason=f"Low confidence ({confidence:.2f} < {min_confidence})",
            stage="min_confidence_gate",
            outcome_type="no_trade",
        )
    return GateOutcome.pass_through("min_confidence_gate")
