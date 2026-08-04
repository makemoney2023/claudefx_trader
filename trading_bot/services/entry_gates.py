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

# Lowest confidence the pipeline can ever accept: gate_min_confidence
# defaults to 0.60 and every scaling-mode threshold is >= 0.60, so any
# gate that caps confidence below this value is a guaranteed reject.
EXECUTION_CONFIDENCE_FLOOR = 0.60


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
    has_sweep: bool = False,
    has_displacement: bool = False,
) -> ZoneGateResult:
    """Zone-aware direction gate: sell premium, buy discount.

    Wrong-zone entries (short below 50% / long above 50%) hard-block unless
    both a directional sweep and displacement are present. Conf/RR alone no
    longer bypasses location — that was letting clean FVG fills fire the
    wrong way from discount shorts / premium longs.
    """
    if is_counter_trend_scalp:
        return ZoneGateResult(blocked=False, decision="skipped_counter_scalp")

    if settings.gate_mode not in ("active", "shadow"):
        return ZoneGateResult(blocked=False, decision="disabled")

    if symbol in settings.disabled_symbols:
        return ZoneGateResult(blocked=False, decision="symbol_disabled")

    # Pure location logic. Direction-vs-D1 policy (counter-trend, index
    # confirmation) lives in evaluate_direction_alignment_gate; the d1_bias
    # and is_index parameters are retained only for call-site compatibility.
    _dir = direction.lower()
    # confidence/actual_rr/d1_bias/is_index retained for call-site compatibility

    in_correct_zone = (
        (_dir == "short" and retrace >= 0.5)
        or (_dir == "long" and retrace <= 0.5)
    )

    blocked = False
    decision = "allowed_zone_aligned"
    reason = ""

    if not in_correct_zone:
        structure_ok = bool(has_sweep) and bool(has_displacement)
        if structure_ok:
            decision = "allowed_wrong_zone_confirmed"
            reason = (
                f"ZONE-GATE {direction.upper()} from {zone_str} "
                f"(retrace={retrace:.0%}) allowed — sweep+displacement confirmed"
            )
        else:
            blocked = True
            decision = "blocked_wrong_zone"
            reason = (
                f"ZONE-GATE {direction.upper()} from {zone_str} "
                f"(retrace={retrace:.0%}) — need sweep+displacement "
                f"(sweep={bool(has_sweep)}, displacement={bool(has_displacement)})"
            )

    if blocked and settings.gate_mode == "shadow":
        return ZoneGateResult(
            blocked=False,
            decision=f"shadow_{decision}",
            reason=reason,
            shadow_only=True,
        )

    return ZoneGateResult(blocked=blocked, decision=decision, reason=reason)


def evaluate_direction_alignment_gate(
    ctx: "TradeContext",
    *,
    scalp_rr_floor: float = 2.5,
) -> GateOutcome:
    """Single source of truth for direction-vs-D1 policy.

    Consolidates what used to live in five places: the legacy D1 gate, the
    zone gate's counter-trend and index-counter branches, and the
    post-Claude counter-trend-scalp cap + RR floor.

    Policy:
    - Aligned or neutral D1: pass (indices additionally need D1 support or
      60% conf + 2:1 RR when entering from the correct zone).
    - Counter-D1 scalp: confidence capped at 0.70, R:R must clear
      ``scalp_rr_floor``.
    - Counter-D1 non-scalp: needs 60% confidence + 3:1 R:R (the legacy-D1
      standard, now applied uniformly whether or not the zone gate is on).
    """
    _dir = ctx.direction
    _d1 = ctx.d1_bias
    counter_d1 = _d1 in ("bullish", "bearish") and (
        (_d1 == "bullish" and _dir == "short")
        or (_d1 == "bearish" and _dir == "long")
    )
    is_scalp = "scalp" in (ctx.trade_type or "").lower()

    if counter_d1:
        if is_scalp:
            if ctx.actual_rr < scalp_rr_floor:
                return GateOutcome.block(
                    gate_id="direction_alignment",
                    reason=(
                        f"Counter-D1 scalp {_dir.upper()} vs D1 {_d1}: R:R "
                        f"{ctx.actual_rr:.2f}:1 below {scalp_rr_floor:.1f}:1 floor."
                    ),
                    stage="direction_alignment",
                )
            if ctx.confidence > 0.70:
                return GateOutcome.cap_confidence(0.70, "direction_alignment_scalp_cap")
            return GateOutcome.pass_through("direction_alignment")
        if ctx.confidence < 0.60 or ctx.actual_rr < 3.0:
            return GateOutcome.block(
                gate_id="direction_alignment",
                reason=(
                    f"{_dir.upper()} vs D1 {_d1}: counter-trend needs 60% conf "
                    f"+ 3:1 RR, got {ctx.confidence:.0%} / {ctx.actual_rr:.1f}:1."
                ),
                stage="direction_alignment",
            )
        return GateOutcome.pass_through("direction_alignment")

    # Indices are trend instruments: entering from the "correct" zone
    # without explicit D1 support needs quality (old index_counter rule).
    if ctx.is_index and ctx.pd_analysis is not None:
        retrace = ctx.pd_analysis.retracement_percent
        in_correct_zone = (
            (_dir == "short" and retrace >= 0.5)
            or (_dir == "long" and retrace <= 0.5)
        )
        d1_supports = (
            (_d1 == "bullish" and _dir == "long")
            or (_d1 == "bearish" and _dir == "short")
        )
        if in_correct_zone and not d1_supports:
            if ctx.confidence < 0.60 or ctx.actual_rr < 2.0:
                return GateOutcome.block(
                    gate_id="direction_alignment",
                    reason=(
                        f"Index {_dir.upper()} without D1 support "
                        f"(D1={_d1 or 'neutral'}): needs 60% conf + 2:1 RR, "
                        f"got {ctx.confidence:.0%} / {ctx.actual_rr:.1f}:1."
                    ),
                    stage="direction_alignment",
                )
    return GateOutcome.pass_through("direction_alignment")


# Elevated floors sit above EXECUTION_CONFIDENCE_FLOOR so these gates
# actually raise the bar instead of duplicating the 60% reject.
WEAK_HOUR_MIN_CONFIDENCE = 0.70
VOLATILE_REGIME_MIN_CONFIDENCE = 0.70
M15_PULLBACK_MIN_CONFIDENCE = 0.68


def evaluate_tod_gate(
    *,
    utc_hour: int,
    weak_hours: tuple,
    confidence: float,
    min_confidence: float = WEAK_HOUR_MIN_CONFIDENCE,
) -> Tuple[bool, str]:
    """Time-of-day gate: weak hours need elevated confidence (default 70%)."""
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
    min_confidence: float = VOLATILE_REGIME_MIN_CONFIDENCE,
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


def _fresh_displacement_aligned(ctx: "TradeContext") -> bool:
    """True when stamped fresh displacement matches trade direction."""
    ar = ctx.analysis_results or {}
    fresh = (ar.get("fresh_displacement_direction") or "").lower()
    if not fresh:
        return False
    want = "bullish" if (ctx.direction or "").lower() == "long" else "bearish"
    return fresh == want


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
    if (
        not m15_opposes
        or _amd == "manipulation"
        or _fresh_displacement_aligned(ctx)
    ):
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
        # HTF-aligned pending limits against opposing M15 are classic pullbacks.
        # Require elevated quality; soft-cap confidence but stay above the floor.
        if ctx.confidence < M15_PULLBACK_MIN_CONFIDENCE or ctx.actual_rr < 2.0:
            return GateOutcome.block(
                gate_id="m15_pullback_quality",
                reason=(
                    f"{_dir.upper()} pullback vs M15 {_m15}: need "
                    f"{M15_PULLBACK_MIN_CONFIDENCE:.0%} conf + 2:1 RR, "
                    f"got {ctx.confidence:.0%} / {ctx.actual_rr:.1f}:1."
                ),
                stage="m15_gate",
            )
        return GateOutcome.cap_confidence(
            M15_PULLBACK_MIN_CONFIDENCE, "m15_pullback_cap"
        )

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
            # Old counter-trend-scalp path capped to 0.55 — always below the
            # 0.60 floor, so it never traded. Reject honestly at this gate.
            return GateOutcome.block(
                gate_id="htf_oppose_cap",
                reason=(
                    f"Counter-trend scalp {_dir.upper()} vs D1+H4 {ctx.d1_bias}: "
                    f"capped at 0.55, below the "
                    f"{EXECUTION_CONFIDENCE_FLOOR:.2f} execution floor."
                ),
                stage="htf_gate",
            )
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


def _amd_phase_value(amd_raw) -> str:
    """Normalize AMD phase from dict or AMDCycleState object."""
    if amd_raw is None:
        return ""
    if isinstance(amd_raw, dict):
        return str(amd_raw.get("phase") or "").lower()
    phase = getattr(amd_raw, "phase", None)
    if phase is None:
        return ""
    return str(getattr(phase, "value", phase) or "").lower()


def evaluate_amd_distribution_gate(ctx: "TradeContext") -> GateOutcome:
    """AMD distribution phase gate."""
    bot_amd = _amd_phase_value(ctx.analysis_results.get("amd_cycle"))
    effective_amd = bot_amd or (ctx.amd_phase or "")
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
    # Off-hours used to cap confidence at 0.50 — below the 0.60 execution
    # floor, so no off-hours signal has ever been able to trade. Make the
    # existing behavior explicit instead of dying later at min_confidence.
    return GateOutcome.block(
        gate_id="off_hours_cap",
        reason=(
            f"Off-hours entries capped at 0.50 confidence, below the "
            f"{EXECUTION_CONFIDENCE_FLOOR:.2f} execution floor."
        ),
        stage="off_hours",
    )


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


@dataclass
class IctConfirmationResult:
    """Outcome of setup-family ICT confirmation (shadow or active)."""

    blocked: bool = False
    would_block: bool = False
    shadow_only: bool = False
    gate_id: str = "ict_confirmation"
    decision: str = "ok"
    reason: str = ""

    def to_gate_outcome(self) -> GateOutcome:
        if self.blocked:
            return GateOutcome.block(
                gate_id=self.gate_id,
                reason=self.reason,
                stage="ict_confirmation_gate",
            )
        if self.shadow_only:
            out = GateOutcome.pass_through("ict_confirmation_shadow")
            out.gate_id = self.gate_id
            out.reason = self.reason
            return out
        return GateOutcome.pass_through(f"ict_confirmation_{self.decision}")


def evaluate_ict_confirmation_gate(
    *,
    fingerprint: Any,
    order_type: str = "market",
    mode: str = "shadow",
) -> IctConfirmationResult:
    """
    Setup-specific confirmation.

    Families:
      - liquidity_reversal (market/stop): sweep + MSS/CHoCH + displacement
      - continuation (market/stop): HTF + MSS/BOS + displacement (sweep optional)
      - passive_retracement (limit): HTF + displacement-origin zone; no post-entry
        confirmation forced (limit cannot know fill-time structure)

    mode='shadow' never hard-blocks; mode='active' blocks missing confirms;
    mode='disabled' skips.
    """
    if mode not in ("shadow", "active"):
        return IctConfirmationResult(decision="disabled")

    family = getattr(fingerprint, "family", "") or ""
    ot = (order_type or getattr(fingerprint, "order_type", None) or "market").lower()
    has_sweep = bool(getattr(fingerprint, "has_sweep", False))
    has_mss = bool(getattr(fingerprint, "has_mss", False))
    has_disp = bool(getattr(fingerprint, "has_displacement", False))
    htf = bool(getattr(fingerprint, "htf_aligned", False))
    zone_ok = bool(getattr(fingerprint, "zone_valid", True))

    missing: list[str] = []
    decision = "ok"

    if family == "passive_retracement" or ot.endswith("_limit"):
        if not htf:
            missing.append("htf_alignment")
        if not has_disp:
            missing.append("displacement_origin")
        if not zone_ok:
            missing.append("valid_zone")
        # Do not require sweep/MSS after a limit is working — unknown until fill
        if missing:
            decision = "passive_limit_incomplete"
            reason = (
                "Passive retracement missing: " + ", ".join(missing)
            )
            would = True
        else:
            decision = "passive_limit_ok"
            reason = "Passive limit: HTF + displacement-origin zone OK"
            would = False
    elif family == "liquidity_reversal":
        if not has_sweep:
            missing.append("directional_sweep")
        if not has_mss:
            missing.append("mss_choch")
        if not has_disp:
            missing.append("displacement")
        would = bool(missing)
        decision = "reversal_incomplete" if would else "reversal_ok"
        reason = (
            ("Liquidity reversal missing: " + ", ".join(missing))
            if would
            else "Liquidity reversal confirmed (sweep+MSS+displacement)"
        )
    else:  # continuation (default)
        if not htf:
            missing.append("htf_alignment")
        if not has_mss:
            missing.append("mss_bos")
        if not has_disp:
            missing.append("displacement")
        would = bool(missing)
        decision = "continuation_incomplete" if would else "continuation_ok"
        reason = (
            ("Continuation missing: " + ", ".join(missing))
            if would
            else "Continuation confirmed (HTF+MSS+displacement)"
        )

    if not would:
        return IctConfirmationResult(
            blocked=False,
            would_block=False,
            decision=decision,
            reason=reason,
        )

    if mode == "shadow":
        return IctConfirmationResult(
            blocked=False,
            would_block=True,
            shadow_only=True,
            decision=f"shadow_{decision}",
            reason=reason,
        )

    return IctConfirmationResult(
        blocked=True,
        would_block=True,
        decision=decision,
        reason=reason,
    )

