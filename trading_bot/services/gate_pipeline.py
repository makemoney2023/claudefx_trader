"""Ordered pre-execution gate chain shared by live and replay."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .entry_gates import (
    ZoneGateSettings,
    evaluate_amd_distribution_gate,
    evaluate_confluence_gate,
    evaluate_direction_alignment_gate,
    evaluate_htf_alignment_gate,
    evaluate_ict_confirmation_gate,
    evaluate_m15_gate,
    evaluate_off_hours_gate,
    evaluate_post_cooldown_gate,
    evaluate_session_penalty,
    evaluate_tod_gate,
    evaluate_volatile_regime_gate,
    evaluate_volume_gate,
    evaluate_zone_gate,
    should_use_zone_gate,
)
from .gate_outcome import GateOutcome
from .scaling_gates import (
    evaluate_min_confidence_gate,
    evaluate_scaling_gate,
    resolve_min_confidence,
    setup_grade_from_confidence,
)
from .trade_context import TradeContext


def _merge_outcome(accumulated: GateOutcome, step: GateOutcome) -> GateOutcome:
    accumulated.gate_path.extend(step.gate_path)
    if step.blocked:
        accumulated.blocked = True
        accumulated.gate_id = step.gate_id
        accumulated.reason = step.reason
        accumulated.outcome_type = step.outcome_type
        accumulated.continue_pipeline = False
    if step.confidence_cap is not None:
        if accumulated.confidence_cap is None:
            accumulated.confidence_cap = step.confidence_cap
        else:
            accumulated.confidence_cap = min(accumulated.confidence_cap, step.confidence_cap)
    if step.confidence_delta is not None:
        accumulated.confidence_delta = (accumulated.confidence_delta or 0.0) + step.confidence_delta
    return accumulated


def _trust_soft_pass(ctx: TradeContext, step: GateOutcome) -> GateOutcome:
    if not step.blocked or not getattr(ctx, "claude_signal_trust", False):
        return step
    gid = step.gate_id or (step.gate_path[-1] if step.gate_path else "gate")
    return GateOutcome.pass_through(f"claude_trust_bypass:{gid}")


def apply_gate_outcomes(ctx: TradeContext, outcome: GateOutcome) -> None:
    """Apply cumulative gate outcome mutations to context."""
    if outcome.confidence_cap is not None:
        ctx.apply_outcome(GateOutcome(confidence_cap=outcome.confidence_cap, gate_path=[]))
    if outcome.confidence_delta is not None:
        ctx.apply_outcome(
            GateOutcome(confidence_delta=outcome.confidence_delta, gate_path=[])
        )


def _truthy_factor(data: Any, *, attr: str, key: str) -> bool:
    """Support live detector objects and replay dict summaries."""
    if data is None:
        return False
    if isinstance(data, dict):
        return bool(data.get(key))
    return bool(hasattr(data, attr) and getattr(data, attr))


def _zone_valid_for_direction(ctx: TradeContext) -> bool:
    """Sell premium / buy discount — used by ICT fingerprint zone_valid."""
    pd = ctx.pd_analysis
    if pd is None:
        return True
    retrace = float(getattr(pd, "retracement_percent", 0.5) or 0.5)
    _dir = (ctx.direction or "").lower()
    return (
        (_dir == "short" and retrace >= 0.5)
        or (_dir == "long" and retrace <= 0.5)
    )


def _evaluate_ict_confirmation_for_ctx(ctx: TradeContext) -> GateOutcome:
    """Build fingerprint from context and run shadow/active ICT confirmation."""
    from ..config import settings
    from .setup_fingerprint import build_setup_fingerprint

    mode = getattr(settings.trading, "ict_confirmation_mode", "disabled") or "disabled"
    regime = ""
    reg = ctx.analysis_results.get("regime") or {}
    if isinstance(reg, dict):
        regime = str(reg.get("type") or reg.get("regime") or ctx.regime_type or "")
    else:
        regime = str(getattr(reg, "type", None) or ctx.regime_type or "")

    from .setup_fingerprint import is_lean_sweep_fade

    fp = build_setup_fingerprint(
        direction=ctx.direction,
        order_type=ctx.order_type or "market",
        session="",
        regime=regime,
        d1_bias=ctx.d1_bias,
        h4_bias=ctx.h4_bias,
        analysis_results=ctx.analysis_results,
        zone_valid=_zone_valid_for_direction(ctx),
    )
    _lean_fade = is_lean_sweep_fade(
        ctx.direction,
        ctx.analysis_results,
        d1_bias=ctx.d1_bias,
        h4_bias=ctx.h4_bias,
    )
    # Stash for persistence / telemetry
    ctx.analysis_results = dict(ctx.analysis_results or {})
    ctx.analysis_results["setup_fingerprint"] = fp.to_dict()

    result = evaluate_ict_confirmation_gate(
        fingerprint=fp,
        order_type=ctx.order_type or "market",
        mode=mode,
        lean_sweep_fade=_lean_fade,
    )
    return result.to_gate_outcome()


def count_confluence(ctx: TradeContext) -> tuple[int, list[str]]:
    """Count ICT confluence factors (mirrors main.py E3 block)."""
    _dir = ctx.direction
    count = 0
    factors: list[str] = []
    ar = ctx.analysis_results

    fvg_data = ar.get("fvg")
    if fvg_data:
        if _dir == "long" and _truthy_factor(
            fvg_data, attr="bullish_fvgs", key="bullish"
        ):
            count += 1
            factors.append("Bullish FVG")
        elif _dir == "short" and _truthy_factor(
            fvg_data, attr="bearish_fvgs", key="bearish"
        ):
            count += 1
            factors.append("Bearish FVG")

    ob_data = ar.get("order_blocks")
    if ob_data:
        if _dir == "long" and _truthy_factor(
            ob_data, attr="bullish_obs", key="bullish"
        ):
            count += 1
            factors.append("Bullish OB")
        elif _dir == "short" and _truthy_factor(
            ob_data, attr="bearish_obs", key="bearish"
        ):
            count += 1
            factors.append("Bearish OB")

    # Credit only a directionally valid recent sweep — not merely nearby liquidity
    liq_data = ar.get("liquidity")
    if liq_data:
        from .setup_fingerprint import has_directional_sweep

        if has_directional_sweep(_dir, liq_data):
            count += 1
            factors.append("Directional Sweep")

    amd = ar.get("amd_cycle")
    amd_phase = None
    amd_expected = None
    if isinstance(amd, dict):
        amd_phase = amd.get("phase")
        amd_expected = amd.get("expected_direction")
    elif amd is not None:
        phase_obj = getattr(amd, "phase", None)
        amd_phase = getattr(phase_obj, "value", phase_obj)
        amd_expected = getattr(amd, "expected_direction", None)
    if amd_phase == "distribution" and amd_expected == _dir:
        count += 1
        factors.append("AMD Distribution")

    disp = ar.get("displacement")
    if isinstance(disp, dict) and disp.get("distribution_confirmed"):
        count += 1
        factors.append("Displacement")

    pd = ar.get("premium_discount")
    if isinstance(pd, dict) and pd.get("in_ote"):
        count += 1
        factors.append("OTE Zone")
    elif pd is not None and not isinstance(pd, dict) and getattr(pd, "in_ote", False):
        count += 1
        factors.append("OTE Zone")

    return count, factors


def resolve_relative_volume(analysis_results: dict) -> float:
    try:
        vol_data = analysis_results.get("volume", {})
        if isinstance(vol_data, dict):
            return float(vol_data.get("relative_volume", 1.0) or 1.0)
        if hasattr(vol_data, "relative_volume"):
            return float(getattr(vol_data, "relative_volume", 1.0) or 1.0)
    except Exception:
        pass
    return 1.0


def evaluate_zone_and_regime_gates(
    ctx: TradeContext,
    *,
    zone_settings: ZoneGateSettings,
    use_zone_gate: bool,
    scalp_rr_floor: float = 2.5,
) -> GateOutcome:
    accumulated = GateOutcome.pass_through("zone_regime_start")

    # Direction-vs-D1 policy: single consolidated gate (replaces legacy D1,
    # zone-gate counter-trend branches, and post-Claude counter-scalp check).
    dir_step = evaluate_direction_alignment_gate(ctx, scalp_rr_floor=scalp_rr_floor)
    dir_step = _trust_soft_pass(ctx, dir_step)
    accumulated = _merge_outcome(accumulated, dir_step)
    if dir_step.blocked:
        return accumulated
    apply_gate_outcomes(ctx, dir_step)

    if use_zone_gate and ctx.pd_analysis is not None:
        from .setup_fingerprint import (
            has_directional_sweep,
            has_displacement,
            htf_aligned,
            is_lean_sweep_fade,
        )

        _ar = ctx.analysis_results or {}
        _has_sweep = has_directional_sweep(ctx.direction, _ar.get("liquidity"))
        _has_disp = has_displacement(_ar, direction=ctx.direction)
        _htf = htf_aligned(ctx.direction, ctx.d1_bias, ctx.h4_bias)
        _lean_fade = is_lean_sweep_fade(
            ctx.direction,
            _ar,
            d1_bias=ctx.d1_bias,
            h4_bias=ctx.h4_bias,
        )
        zg = evaluate_zone_gate(
            direction=ctx.direction,
            confidence=ctx.confidence,
            actual_rr=ctx.actual_rr,
            retrace=ctx.pd_analysis.retracement_percent,
            zone_str=ctx.pd_analysis.current_zone.value,
            d1_bias=ctx.d1_bias,
            is_index=ctx.is_index,
            settings=zone_settings,
            symbol=ctx.symbol,
            is_counter_trend_scalp=ctx.is_counter_trend_scalp,
            has_sweep=_has_sweep,
            has_displacement=_has_disp,
            htf_aligned=_htf,
            lean_sweep_fade=_lean_fade,
        )
        if zg.blocked:
            step = GateOutcome.block(
                gate_id="zone_gate",
                reason=zg.reason,
                stage="zone_gate",
            )
            step = _trust_soft_pass(ctx, step)
            if step.blocked:
                return step
            accumulated = _merge_outcome(accumulated, step)
        if zg.shadow_only:
            accumulated.gate_path.append("zone_gate_shadow")

    blocked, reason = evaluate_volatile_regime_gate(
        regime_type=ctx.regime_type,
        confidence=ctx.confidence,
    )
    if blocked:
        step = GateOutcome.block(
            gate_id="volatile_regime",
            reason=reason,
            stage="regime_gate",
        )
        step = _trust_soft_pass(ctx, step)
        if step.blocked:
            return step
        accumulated = _merge_outcome(accumulated, step)

    blocked, reason = evaluate_tod_gate(
        utc_hour=ctx.utc_hour,
        weak_hours=ctx.weak_hours,
        confidence=ctx.confidence,
    )
    if blocked:
        step = GateOutcome.block(
            gate_id="tod_gate",
            reason=reason,
            stage="tod_gate",
        )
        step = _trust_soft_pass(ctx, step)
        if step.blocked:
            return step
        accumulated = _merge_outcome(accumulated, step)

    return accumulated


def evaluate_structure_and_quality_gates(
    ctx: TradeContext,
    *,
    session_name: str = "",
    is_kill_zone: bool = False,
    asian_penalty: float = 0.05,
) -> GateOutcome:
    accumulated = GateOutcome.pass_through("structure_start")

    for step_fn in (
        evaluate_m15_gate,
        evaluate_htf_alignment_gate,
        evaluate_amd_distribution_gate,
        evaluate_off_hours_gate,
        evaluate_post_cooldown_gate,
    ):
        step = step_fn(ctx)
        step = _trust_soft_pass(ctx, step)
        accumulated = _merge_outcome(accumulated, step)
        if step.blocked:
            return accumulated
        apply_gate_outcomes(ctx, step)

    session_step = evaluate_session_penalty(
        session_name=session_name,
        is_kill_zone=is_kill_zone,
        off_hours_mode=ctx.off_hours_mode,
        asian_penalty=asian_penalty,
    )
    accumulated = _merge_outcome(accumulated, session_step)
    apply_gate_outcomes(ctx, session_step)

    rel_vol = resolve_relative_volume(ctx.analysis_results)
    vol_step = evaluate_volume_gate(ctx, rel_vol)
    vol_step = _trust_soft_pass(ctx, vol_step)
    accumulated = _merge_outcome(accumulated, vol_step)
    if vol_step.blocked:
        return accumulated
    apply_gate_outcomes(ctx, vol_step)

    count, _ = count_confluence(ctx)
    min_conf = 1 if ctx.scaling_aggressive else 2
    conf_override = 0.60
    conf_step = evaluate_confluence_gate(
        ctx,
        confluence_count=count,
        min_confluence=min_conf,
        confidence_override=conf_override,
    )
    conf_step = _trust_soft_pass(ctx, conf_step)
    accumulated = _merge_outcome(accumulated, conf_step)
    if conf_step.blocked:
        return accumulated

    # Setup-family ICT confirmation (default disabled; active blocks incomplete confirms)
    ict_step = _evaluate_ict_confirmation_for_ctx(ctx)
    ict_step = _trust_soft_pass(ctx, ict_step)
    return _merge_outcome(accumulated, ict_step)


def evaluate_scaling_gates(
    ctx: TradeContext,
    *,
    scaling_manager,
    daily_trades: int,
    gate_min_confidence: float,
) -> GateOutcome:
    grade = setup_grade_from_confidence(ctx.confidence)
    scale_step = evaluate_scaling_gate(
        setup_grade=grade,
        confidence=ctx.confidence,
        daily_trades=daily_trades,
        scaling_manager=scaling_manager,
    )
    if scale_step.blocked:
        return scale_step
    min_conf = resolve_min_confidence(scaling_manager, gate_min_confidence)
    return evaluate_min_confidence_gate(ctx.confidence, min_conf)


def evaluate_entry_gates(
    ctx: TradeContext,
    *,
    zone_settings: ZoneGateSettings,
    use_zone_gate: bool,
    session_name: str = "",
    is_kill_zone: bool = False,
    asian_penalty: float = 0.05,
    scalp_rr_floor: float = 2.5,
) -> GateOutcome:
    """Zone through confluence gates (before scaling mode refresh)."""
    zone_outcome = evaluate_zone_and_regime_gates(
        ctx,
        zone_settings=zone_settings,
        use_zone_gate=use_zone_gate,
        scalp_rr_floor=scalp_rr_floor,
    )
    if zone_outcome.blocked:
        ctx.gate_path.extend(zone_outcome.gate_path)
        return zone_outcome

    struct_outcome = evaluate_structure_and_quality_gates(
        ctx,
        session_name=session_name,
        is_kill_zone=is_kill_zone,
        asian_penalty=asian_penalty,
    )
    if struct_outcome.blocked:
        ctx.gate_path.extend(struct_outcome.gate_path)
        return struct_outcome
    apply_gate_outcomes(ctx, struct_outcome)
    ctx.gate_path.extend(zone_outcome.gate_path + struct_outcome.gate_path)
    return GateOutcome.pass_through("entry_gates_complete")


def evaluate_trade_permission_gates(
    ctx: TradeContext,
    *,
    scaling_manager=None,
    daily_trades: int = 0,
    gate_min_confidence: float = 0.60,
    correlation_check: Optional[Callable[[], tuple[bool, str]]] = None,
) -> GateOutcome:
    """Scaling manager and correlation gates (after mode refresh)."""
    scale_outcome = evaluate_scaling_gates(
        ctx,
        scaling_manager=scaling_manager,
        daily_trades=daily_trades,
        gate_min_confidence=gate_min_confidence,
    )
    scale_outcome = _trust_soft_pass(ctx, scale_outcome)
    if scale_outcome.blocked:
        ctx.gate_path.extend(scale_outcome.gate_path)
        return scale_outcome

    if correlation_check is not None:
        should_block, reason = correlation_check()
        if should_block:
            outcome = GateOutcome.block(
                gate_id="correlation",
                reason=reason,
                stage="correlation_gate",
            )
            outcome = _trust_soft_pass(ctx, outcome)
            if outcome.blocked:
                ctx.gate_path.extend(outcome.gate_path)
                return outcome
            ctx.gate_path.extend(scale_outcome.gate_path)
            ctx.gate_path.extend(outcome.gate_path)
            return GateOutcome.pass_through("trade_permission_complete")

    ctx.gate_path.extend(scale_outcome.gate_path)
    return GateOutcome.pass_through("trade_permission_complete")


def evaluate_pre_execution_gates(
    ctx: TradeContext,
    *,
    zone_settings: ZoneGateSettings,
    use_zone_gate: bool,
    session_name: str = "",
    is_kill_zone: bool = False,
    asian_penalty: float = 0.05,
    scalp_rr_floor: float = 2.5,
    scaling_manager=None,
    daily_trades: int = 0,
    gate_min_confidence: float = 0.60,
    correlation_check: Optional[Callable[[], tuple[bool, str]]] = None,
) -> GateOutcome:
    """Full ordered gate chain matching live main.py."""
    entry = evaluate_entry_gates(
        ctx,
        zone_settings=zone_settings,
        use_zone_gate=use_zone_gate,
        session_name=session_name,
        is_kill_zone=is_kill_zone,
        asian_penalty=asian_penalty,
        scalp_rr_floor=scalp_rr_floor,
    )
    if entry.blocked:
        return entry
    return evaluate_trade_permission_gates(
        ctx,
        scaling_manager=scaling_manager,
        daily_trades=daily_trades,
        gate_min_confidence=gate_min_confidence,
        correlation_check=correlation_check,
    )
