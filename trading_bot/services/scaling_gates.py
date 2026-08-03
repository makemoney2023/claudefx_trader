"""Scaling-mode gate helpers for the trade pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from .gate_outcome import GateOutcome


@dataclass
class FlipGuardSettings:
    cooldown_minutes: int = 15
    min_confidence: float = 0.80


def _as_utc(dt: datetime) -> datetime:
    """Normalize naive/aware datetimes to UTC-aware for safe arithmetic.

    Replay snapshot times are often tz-naive; live tracking uses UTC-aware.
    Treating naive values as UTC matches MT5 history / backtest convention.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate_flip_guard(
    *,
    symbol: str,
    direction: str,
    confidence: float,
    last_signal_direction: Dict[str, Tuple[str, datetime]],
    direction_flipped: bool = False,
    reversal_reentry: bool = False,
    settings: Optional[FlipGuardSettings] = None,
    as_of: Optional[datetime] = None,
) -> GateOutcome:
    """Block low-confidence direction flips within the cooldown window.

    ``as_of`` is the evaluation clock (replay snapshot time). Defaults to
    wall-clock UTC for live trading.
    """
    cfg = settings or FlipGuardSettings()
    if direction_flipped:
        return GateOutcome.pass_through("flip_guard_bypass_coherence")
    if reversal_reentry:
        return GateOutcome.pass_through("flip_guard_bypass_reversal")

    if symbol not in last_signal_direction:
        return GateOutcome.pass_through("flip_guard")

    last_dir, last_time = last_signal_direction[symbol]
    now = _as_utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    minutes_since = (now - _as_utc(last_time)).total_seconds() / 60
    if (
        last_dir == direction
        or last_dir == "no_trade"
        or minutes_since >= cfg.cooldown_minutes
    ):
        return GateOutcome.pass_through("flip_guard")

    if not confidence_meets_threshold(confidence, cfg.min_confidence):
        return GateOutcome.block(
            gate_id="direction_flip",
            reason=(
                f"Direction flip {last_dir.upper()} -> {direction.upper()} "
                f"({confidence:.0%} < {cfg.min_confidence:.0%} required, "
                f"{minutes_since:.0f}min since last signal)"
            ),
            stage="flip_guard",
            outcome_type="no_trade",
        )

    return GateOutcome.pass_through("flip_guard_high_confidence")


def confidence_meets_threshold(confidence: float, threshold: float) -> bool:
    """
    Compare confidence vs threshold at whole-percent precision.

    Prevents "60% below threshold 60%" blocks when the raw float is e.g. 0.595
    (formats as 60% with :.0%) but fails a strict ``< 0.60`` check.
    """
    try:
        c = float(confidence)
        t = float(threshold)
    except (TypeError, ValueError):
        return False
    return round(c * 100) >= round(t * 100)


def setup_grade_from_confidence(confidence: float) -> str:
    if confidence_meets_threshold(confidence, 0.85):
        return "A+"
    if confidence_meets_threshold(confidence, 0.75):
        return "A"
    if confidence_meets_threshold(confidence, 0.60):
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
    if not confidence_meets_threshold(confidence, min_confidence):
        return GateOutcome.block(
            gate_id="min_confidence",
            reason=f"Low confidence ({confidence:.2f} < {min_confidence})",
            stage="min_confidence_gate",
            outcome_type="no_trade",
        )
    return GateOutcome.pass_through("min_confidence_gate")
