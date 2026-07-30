"""Post-fill protection and partial-fill policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProtectionCheck:
    ok: bool
    reason: str = ""


@dataclass
class PartialFillResult:
    accept: bool
    filled_lots: float = 0.0
    gate_id: str = ""
    reason: str = ""


def validate_broker_protections(
    *,
    direction: str,
    intended_sl: float,
    intended_tp: float,
    broker_sl: Optional[float],
    broker_tp: Optional[float],
    tolerance_price: float = 0.0,
) -> ProtectionCheck:
    """Ensure broker SL/TP are within tolerance of intended levels.

    Missing broker values are treated as ok (MT5 sometimes omits until
    modify settles); present-but-drifted values fail.
    """
    tol = max(0.0, tolerance_price)
    if broker_sl is not None and intended_sl:
        if abs(float(broker_sl) - float(intended_sl)) > tol:
            return ProtectionCheck(
                ok=False,
                reason=(
                    f"SL drift: broker={broker_sl} intended={intended_sl} "
                    f"tol={tol}"
                ),
            )
    if broker_tp is not None and intended_tp:
        if abs(float(broker_tp) - float(intended_tp)) > tol:
            return ProtectionCheck(
                ok=False,
                reason=(
                    f"TP drift: broker={broker_tp} intended={intended_tp} "
                    f"tol={tol}"
                ),
            )
    # Direction sanity: long SL below entry is assumed already validated upstream
    _ = direction
    return ProtectionCheck(ok=True)


def evaluate_partial_fill(
    *,
    requested_lots: float,
    filled_lots: float,
    min_fill_ratio: float = 0.5,
) -> PartialFillResult:
    """Reject partial fills below min_fill_ratio of requested size."""
    if requested_lots <= 0:
        return PartialFillResult(
            accept=False,
            gate_id="partial_fill",
            reason="requested_lots <= 0",
        )
    ratio = filled_lots / requested_lots
    if filled_lots <= 0:
        return PartialFillResult(
            accept=False,
            gate_id="partial_fill",
            reason="zero fill volume",
        )
    if ratio + 1e-9 < min_fill_ratio:
        return PartialFillResult(
            accept=False,
            filled_lots=filled_lots,
            gate_id="partial_fill",
            reason=(
                f"partial fill {filled_lots:.2f}/{requested_lots:.2f} lots "
                f"({ratio:.0%}) below {min_fill_ratio:.0%} minimum"
            ),
        )
    return PartialFillResult(accept=True, filled_lots=filled_lots)
