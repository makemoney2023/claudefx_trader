"""
Shared entry gates used by live orchestration and Claude replay.

Keeps zone, legacy D1, time-of-day, and regime gate logic in one place
to prevent replay/live parity drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from .trade_context import TradeContext

from .gate_outcome import GateOutcome


@dataclass
class ZoneGateSettings:
    gate_mode: str = "active"
    misaligned_min_confidence: float = 0.60
    misaligned_min_rr: float = 2.0
    equilibrium_min_confidence: float = 0.60
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
    min_confidence: float = 0.60,
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
    min_confidence: float = 0.60,
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
    min_confidence: float = 0.60,
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


def evaluate_m15_gate(ctx: "TradeContext") -> GateOutcome:
    """M15 execution timeframe structure gate."""
    _dir = ctx.direction
    _m15 = ctx.m15_bias
    _amd = ctx.amd_phase
    m15_opposes = (
        (_m15 == "bearish" and _dir == "long")
        or (_m15 == "bullish" and _dir == "short")
    )
    ctx.m15_opposes = m15_opposes
    if not m15_opposes or _amd == "manipulation":
        return GateOutcome.pass_through("m15_gate")

    d1_supports = (
        (ctx.d1_bias == "bullish" and _dir == "long")
        or (ctx.d1_bias == "bearish" and _dir == "short")
    )
    h4_supports = (
        (ctx.h4_bias == "bullish" and _dir == "long")
        or (ctx.h4_bias == "bearish" and _dir == "short")
    )
    is_pending_limit = ctx.order_type in ("buy_limit", "sell_limit")
    is_pullback = d1_supports and h4_supports and is_pending_limit

    if is_pullback:
        return GateOutcome.cap_confidence(0.55, "m15_pullback")

    return GateOutcome.block(
        gate_id="m15_structure",
        reason=(
            f"{_dir.upper()} contradicts M15 bias ({_m15}). "
            f"Execution TF must confirm direction."
        ),
        stage="m15_gate",
    )


def evaluate_htf_alignment_gate(ctx: "TradeContext") -> GateOutcome:
    """HTF D1+H4 alignment gate."""
    _dir = ctx.direction
    d1_opposes = (
        (ctx.d1_bias == "bearish" and _dir == "long")
        or (ctx.d1_bias == "bullish" and _dir == "short")
    )
    h4_opposes = (
        (ctx.h4_bias == "bearish" and _dir == "long")
        or (ctx.h4_bias == "bullish" and _dir == "short")
    )
    is_scalp = "scalp" in (ctx.trade_type or "").lower()

    if d1_opposes and h4_opposes:
        if (
            is_scalp
            and not ctx.m15_opposes
            and ctx.actual_rr >= 2.0
            and ctx.confidence >= 0.60
        ):
            return GateOutcome.cap_confidence(0.55, "htf_counter_scalp")
        return GateOutcome.block(
            gate_id="htf_both_oppose",
            reason=(
                f"{_dir.upper()} opposes BOTH D1 ({ctx.d1_bias}) and H4 ({ctx.h4_bias}). "
                f"HTF alignment required."
            ),
            stage="htf_gate",
        )
    if d1_opposes or h4_opposes:
        if ctx.confidence > 0.60:
            return GateOutcome.cap_confidence(0.60, "htf_single_oppose")
    return GateOutcome.pass_through("htf_gate")


def evaluate_amd_distribution_gate(ctx: "TradeContext") -> GateOutcome:
    """AMD distribution phase gate."""
    bot_amd = ""
    if ctx.analysis_results.get("amd_cycle"):
        bot_amd = (ctx.analysis_results["amd_cycle"].get("phase") or "").lower()
    effective_amd = bot_amd or ctx.amd_phase
    if effective_amd != "distribution":
        return GateOutcome.pass_through("amd_distribution")

    outcome = GateOutcome.pass_through("amd_distribution")
    if ctx.confidence > 0.60:
        outcome.confidence_cap = 0.60
    if ctx.actual_rr < 2.0:
        return GateOutcome.block(
            gate_id="amd_distribution_rr",
            reason=(
                f"Distribution phase + R:R {ctx.actual_rr:.2f}:1 below 2.0:1 minimum."
            ),
            stage="amd_distribution",
        )
    return outcome


def evaluate_off_hours_gate(ctx: "TradeContext") -> GateOutcome:
    if not ctx.off_hours_mode:
        return GateOutcome.pass_through("off_hours")
    outcome = GateOutcome.pass_through("off_hours")
    if ctx.confidence > 0.50:
        outcome.confidence_cap = 0.50
    if ctx.actual_rr < 3.0:
        return GateOutcome.block(
            gate_id="off_hours_rr",
            reason=f"Off-hours R:R {ctx.actual_rr:.2f}:1 < 3.0 minimum.",
            stage="off_hours",
        )
    return outcome


def evaluate_post_cooldown_gate(ctx: "TradeContext") -> GateOutcome:
    if not ctx.post_cooldown:
        return GateOutcome.pass_through("post_cooldown")
    if ctx.confidence < 0.60:
        return GateOutcome.block(
            gate_id="post_cooldown_confidence",
            reason=(
                f"First signal after loss cooldown needs 60%+ confidence, "
                f"got {ctx.confidence:.0%}."
            ),
            stage="post_cooldown",
            outcome_type="no_trade",
        )
    return GateOutcome.pass_through("post_cooldown")


def evaluate_session_penalty(
    *,
    session_name: str,
    is_kill_zone: bool,
    off_hours_mode: bool,
    asian_penalty: float = 0.05,
) -> GateOutcome:
    if off_hours_mode:
        return GateOutcome.pass_through("session_penalty")
    penalty = 0.0
    name = (session_name or "").lower()
    if not is_kill_zone:
        # Soft haircut outside kill zones (was 0.15 for non-Asian, which
        # collapsed 66-70% Claude signals under the 60% confluence override).
        penalty = asian_penalty if "asian" in name else 0.05
    elif "london close" in name or "london_close" in name:
        penalty = 0.05
    if penalty <= 0:
        return GateOutcome.pass_through("session_penalty")
    outcome = GateOutcome.pass_through("session_penalty")
    outcome.confidence_delta = penalty
    return outcome


def evaluate_volume_gate(ctx: "TradeContext", rel_vol: float) -> GateOutcome:
    ctx.relative_volume = rel_vol
    if rel_vol < 0.3:
        return GateOutcome.block(
            gate_id="volume_dead_market",
            reason=f"Relative volume {rel_vol:.2f}x < 0.3 — dead market.",
            stage="volume_gate",
        )
    if rel_vol < 0.5:
        return GateOutcome.cap_confidence(0.70, "volume_cap")
    return GateOutcome.pass_through("volume_gate")


def evaluate_confluence_gate(
    ctx: "TradeContext",
    *,
    confluence_count: int,
    min_confluence: int,
    confidence_override: float,
) -> GateOutcome:
    ctx.confluence_count = confluence_count
    if confluence_count >= min_confluence:
        return GateOutcome.pass_through("confluence_gate")
    if ctx.confidence < confidence_override:
        return GateOutcome.block(
            gate_id="low_confluence",
            reason=(
                f"Only {confluence_count} confluence factors (min {min_confluence}), "
                f"confidence {ctx.confidence:.0%} < {confidence_override:.0%}."
            ),
            stage="confluence_gate",
            outcome_type="no_trade",
        )
    return GateOutcome.pass_through("confluence_gate")

