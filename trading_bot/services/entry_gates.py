"""
Shared entry gates used by live orchestration and Claude replay.

Keeps zone, legacy D1, time-of-day, and regime gate logic in one place
to prevent replay/live parity drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ZoneGateSettings:
    gate_mode: str = "active"
    misaligned_min_confidence: float = 0.75
    misaligned_min_rr: float = 3.0
    equilibrium_min_confidence: float = 0.65
    disabled_symbols: tuple = ()


@dataclass
class ZoneGateResult:
    blocked: bool
    decision: str
    reason: str = ""
    shadow_only: bool = False


def evaluate_zone_gate(
    *,
    direction: str,
    confidence: float,
    actual_rr: float,
    retrace: float,
    zone_str: str,
    d1_bias: str,
    is_index: bool,
    settings: ZoneGateSettings,
    symbol: str,
    is_counter_trend_scalp: bool = False,
) -> ZoneGateResult:
    """Zone-aware direction gate: sell premium, buy discount."""
    if is_counter_trend_scalp:
        return ZoneGateResult(blocked=False, decision="skipped_counter_scalp")

    if settings.gate_mode not in ("active", "shadow"):
        return ZoneGateResult(blocked=False, decision="disabled")

    if symbol in settings.disabled_symbols:
        return ZoneGateResult(blocked=False, decision="symbol_disabled")

    _dir = direction.lower()
    _d1 = (d1_bias or "").lower()

    in_correct_zone = (
        (_dir == "short" and retrace >= 0.5)
        or (_dir == "long" and retrace <= 0.5)
    )
    counter_trend = (
        (_d1 == "bullish" and _dir == "short")
        or (_d1 == "bearish" and _dir == "long")
    )
    index_counter = False
    if is_index and in_correct_zone:
        index_counter = (
            (_dir == "short" and _d1 != "bearish")
            or (_dir == "long" and _d1 != "bullish")
        )

    if in_correct_zone and (counter_trend or index_counter) and is_index:
        zone_aligned = False
        zone_misaligned = True
    elif in_correct_zone and counter_trend:
        zone_aligned = False
        zone_misaligned = False
    else:
        zone_aligned = in_correct_zone
        zone_misaligned = (
            (_dir == "long" and retrace >= 0.618)
            or (_dir == "short" and retrace <= 0.382)
        )

    blocked = False
    decision = "allowed_zone_aligned"
    reason = ""

    if zone_misaligned:
        if confidence < settings.misaligned_min_confidence or actual_rr < settings.misaligned_min_rr:
            blocked = True
            decision = "blocked_misaligned"
            reason = (
                f"ZONE-GATE {direction.upper()} from {zone_str} "
                f"(retrace={retrace:.0%}, conf={confidence:.0%}, RR={actual_rr:.1f})"
            )
        else:
            decision = "allowed_misaligned_high_conf"
    elif not zone_aligned:
        if confidence < settings.equilibrium_min_confidence:
            blocked = True
            decision = "blocked_equilibrium"
            reason = (
                f"ZONE-GATE {direction.upper()} from {zone_str} "
                f"(equilibrium, conf={confidence:.0%})"
            )
        else:
            decision = "allowed_equilibrium"

    if blocked and settings.gate_mode == "shadow":
        return ZoneGateResult(
            blocked=False,
            decision=f"shadow_{decision}",
            reason=reason,
            shadow_only=True,
        )

    return ZoneGateResult(blocked=blocked, decision=decision, reason=reason)


def evaluate_legacy_d1_gate(
    *,
    direction: str,
    confidence: float,
    actual_rr: float,
    d1_bias: str,
    min_confidence: float = 0.70,
    min_rr: float = 3.0,
) -> Tuple[bool, str]:
    """Legacy counter-D1 gate when zone gate is inactive."""
    _dir = direction.lower()
    _d1 = (d1_bias or "").lower()
    is_counter = _d1 in ("bullish", "bearish") and (
        (_d1 == "bullish" and _dir == "short")
        or (_d1 == "bearish" and _dir == "long")
    )
    if not is_counter:
        return False, ""
    if confidence < min_confidence or actual_rr < min_rr:
        return True, (
            f"DIRECTION-GATE {direction.upper()} vs D1 {_d1}: "
            f"need {min_confidence:.0%} conf + {min_rr:.0f}:1 RR, "
            f"got {confidence:.0%} / {actual_rr:.1f}:1"
        )
    return False, ""


def evaluate_tod_gate(
    *,
    utc_hour: int,
    weak_hours: tuple,
    confidence: float,
    min_confidence: float = 0.68,
) -> Tuple[bool, str]:
    """Time-of-day gate: weak hours need elevated confidence."""
    if utc_hour not in weak_hours:
        return False, ""
    if confidence >= min_confidence:
        return False, ""
    return True, (
        f"TOD-GATE hour {utc_hour:02d}:00 UTC weak — "
        f"need {min_confidence:.0%}, got {confidence:.0%}"
    )


def evaluate_volatile_regime_gate(
    *,
    regime_type: str,
    confidence: float,
    min_confidence: float = 0.70,
) -> Tuple[bool, str]:
    if (regime_type or "").lower() != "volatile_ranging":
        return False, ""
    if confidence >= min_confidence:
        return False, ""
    return True, (
        f"REGIME-GATE volatile ranging — need {min_confidence:.0%}, got {confidence:.0%}"
    )


def should_use_zone_gate(
    pd_analysis_present: bool,
    gate_mode: str,
    symbol: str,
    disabled_symbols: tuple,
    is_counter_trend_scalp: bool = False,
) -> bool:
    return (
        pd_analysis_present
        and gate_mode in ("active", "shadow")
        and symbol not in disabled_symbols
        and not is_counter_trend_scalp
    )


@dataclass
class ReplayGateResult:
    blocked: bool
    zone_decision: str = "no_gate"
    reason: str = ""


def evaluate_replay_pre_execution_gates(
    *,
    direction: str,
    confidence: float,
    rr: float,
    d1_bias: str,
    pd_result,
    symbol: str,
    is_index: bool,
    utc_hour: int,
    weak_hours: tuple,
    zone_settings: ZoneGateSettings,
) -> ReplayGateResult:
    """Shared pre-execution gates for Claude replay (zone, legacy D1, TOD)."""
    if pd_result is not None and should_use_zone_gate(
        True, zone_settings.gate_mode, symbol, zone_settings.disabled_symbols
    ):
        zg = evaluate_zone_gate(
            direction=direction,
            confidence=confidence,
            actual_rr=rr,
            retrace=pd_result.retracement_percent,
            zone_str=pd_result.current_zone.value,
            d1_bias=d1_bias,
            is_index=is_index,
            settings=zone_settings,
            symbol=symbol,
        )
        if zg.blocked:
            return ReplayGateResult(True, zg.decision, zg.reason)
        return ReplayGateResult(False, zg.decision)

    if zone_settings.gate_mode != "active" or pd_result is None:
        blocked, reason = evaluate_legacy_d1_gate(
            direction=direction,
            confidence=confidence,
            actual_rr=rr,
            d1_bias=d1_bias,
        )
        if blocked:
            return ReplayGateResult(True, "legacy_d1_blocked", reason)

    blocked, reason = evaluate_tod_gate(
        utc_hour=utc_hour,
        weak_hours=weak_hours,
        confidence=confidence,
    )
    if blocked:
        return ReplayGateResult(True, "tod_blocked", reason)

    return ReplayGateResult(False, "allowed")

