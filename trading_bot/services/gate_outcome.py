"""Shared gate evaluation result for live and replay pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GateOutcome:
    blocked: bool = False
    gate_id: str = ""
    reason: str = ""
    outcome_type: str = "mechanical_reject"
    confidence_cap: Optional[float] = None
    confidence_delta: Optional[float] = None
    gate_path: list[str] = field(default_factory=list)
    continue_pipeline: bool = True

    @classmethod
    def pass_through(cls, stage: str) -> "GateOutcome":
        return cls(blocked=False, gate_path=[stage])

    @classmethod
    def block(
        cls,
        *,
        gate_id: str,
        reason: str,
        stage: str,
        outcome_type: str = "mechanical_reject",
    ) -> "GateOutcome":
        return cls(
            blocked=True,
            gate_id=gate_id,
            reason=reason,
            outcome_type=outcome_type,
            gate_path=[stage],
            continue_pipeline=False,
        )

    @classmethod
    def cap_confidence(cls, cap: float, stage: str) -> "GateOutcome":
        return cls(
            blocked=False,
            confidence_cap=cap,
            gate_path=[stage],
        )
