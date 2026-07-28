"""Ordered pre-execution gate chain shared by live and replay."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .entry_gates import (
    ZoneGateSettings,
    evaluate_amd_distribution_gate,
    evaluate_confluence_gate,
    evaluate_htf_alignment_gate,
    evaluate_legacy_d1_gate,
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

    liq_data = ar.get("liquidity")
    if liq_data:
        if _dir == "long" and _truthy_factor(
            liq_data, attr="nearest_ssl", key="nearest_ssl"
        ):
            count += 1
            factors.append("SSL Liquidity")
        elif _dir == "short" and _truthy_factor(
            liq_data, attr="nearest_bsl", key="nearest_bsl"
        ):
            count += 1
            factors.append("BSL Liquidity")

    amd = ar.get("amd_cycle")
    if isinstance(amd, dict) and amd.get("phase") == "distribution" and amd.get("expected_direction") == _dir:
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
) -> GateOutcome:
    accumulated = GateOutcome.pass_through("zone_regime_start")

    if use_zone_gate and ctx.pd_analysis is not None:
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
        )
        if zg.blocked:
            return GateOutcome.block(
                gate_id="zone_gate",
                reason=zg.reason,
                stage="zone_gate",
            )
        if zg.shadow_only:
            accumulated.gate_path.append("zone_gate_shadow")
    elif not ctx.is_counter_trend_scalp:
        blocked, reason = evaluate_legacy_d1_gate(
            direction=ctx.direction,
            confidence=ctx.confidence,
            actual_rr=ctx.actual_rr,
            d1_bias=ctx.d1_bias,
        )
        if blocked:
            return GateOutcome.block(
                gate_id="legacy_d1",
                reason=reason,
                stage="legacy_d1_gate",
            )

    blocked, reason = evaluate_volatile_regime_gate(
        regime_type=ctx.regime_type,
        confidence=ctx.confidence,
    )
    if blocked:
        return GateOutcome.block(
            gate_id="volatile_regime",
            reason=reason,
            stage="regime_gate",
        )

    blocked, reason = evaluate_tod_gate(
        utc_hour=ctx.utc_hour,
        weak_hours=ctx.weak_hours,
        confidence=ctx.confidence,
    )
    if blocked:
        return GateOutcome.block(
            gate_id="tod_gate",
            reason=reason,
            stage="tod_gate",
        )

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
    return _merge_outcome(accumulated, conf_step)


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
) -> GateOutcome:
    """Zone through confluence gates (before scaling mode refresh)."""
    zone_outcome = evaluate_zone_and_regime_gates(
        ctx, zone_settings=zone_settings, use_zone_gate=use_zone_gate
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
            ctx.gate_path.extend(outcome.gate_path)
            return outcome

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
