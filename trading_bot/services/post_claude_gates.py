"""Shared post-Claude gate chain for live and replay parity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import get_symbol_spec, settings
from ..utils.candle_utils import calculate_atr
from .confidence_modifiers import (
    SecondaryModifierContext,
    apply_secondary_modifiers,
    confidence_decision_to_dict,
)
from .entry_gates import ZoneGateSettings
from .gate_outcome import GateOutcome
from .gate_pipeline import evaluate_entry_gates, evaluate_trade_permission_gates
from .direction_circuit_breaker import (
    DirectionCircuitBreakerSettings,
    evaluate_direction_circuit_breaker,
)
from .scaling_gates import FlipGuardSettings, evaluate_flip_guard
from .signal_normalizer import NormalizedSignal
from .trade_context import TradeContext

PostClaudePhase = Literal["price", "entry", "permission", "flip", "complete"]


@dataclass
class PostClaudeGateSettings:
    gate_min_confidence: float = 0.60
    asian_penalty: float = 0.05
    counter_trend_rr_floor: float = 2.0
    flip_guard: FlipGuardSettings = field(default_factory=FlipGuardSettings)
    circuit_breaker: DirectionCircuitBreakerSettings = field(
        default_factory=DirectionCircuitBreakerSettings
    )


@dataclass
class SecondaryModifierInput:
    retail_contrarian: Optional[str] = None
    vix_risk_mode: Optional[str] = None
    breaker_blocks: Optional[list] = None
    silver_bullet_ready: bool = False


@dataclass
class PostClaudeGateInput:
    symbol: str
    trade_signal: Any
    norm: NormalizedSignal
    market_data: Dict[str, Any]
    analysis_results: Dict[str, Any]
    pd_analysis: Any = None
    current_price: float = 0.0
    is_crypto: bool = False
    is_aggressive: bool = False
    df: Optional[pd.DataFrame] = None
    snapshot_time: Optional[datetime] = None
    session_name: str = ""
    is_kill_zone: bool = False
    zone_settings: Optional[ZoneGateSettings] = None
    use_zone_gate: bool = False
    scaling_manager: Any = None
    daily_trades: int = 0
    scaling_aggressive: bool = False
    correlation_check: Optional[Callable[[], Tuple[bool, str]]] = None
    last_signal_direction: Optional[Dict] = None
    direction_flipped: bool = False
    direction_loss_streak: int = 0
    apply_secondary_modifiers: bool = False
    modifier_input: Optional[SecondaryModifierInput] = None
    build_pipeline_context: Optional[Callable[..., Tuple[Any, ZoneGateSettings, bool]]] = None


@dataclass
class PostClaudeGateResult:
    blocked: bool = False
    gate_id: str = ""
    reason: str = ""
    outcome_type: str = "mechanical_reject"
    gate_path: List[str] = field(default_factory=list)
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    direction: str = ""
    confidence: float = 0.0
    actual_rr: float = 0.0
    is_counter_trend_scalp: bool = False
    pipeline_ctx: Optional[TradeContext] = None
    confidence_components: Optional[Dict] = None
    min_rr: float = 0.0


def resolve_min_rr(symbol: str, trade_type: str) -> float:
    spec = get_symbol_spec(symbol)
    if spec.category == "crypto":
        rr_by_type = {"scalp": 2.0, "intraday": 2.5, "swing": 3.5}
    else:
        rr_by_type = {"scalp": 1.5, "intraday": 2.0, "swing": 3.0}
    return rr_by_type.get(trade_type, settings.trading.min_risk_reward)


def compute_actual_rr(entry: float, sl: float, tp: float) -> float:
    sl_dist = abs(entry - sl) if sl and entry else 0
    tp_dist = abs(tp - entry) if tp and entry else 0
    return tp_dist / sl_dist if sl_dist > 0 else 0.0


def apply_atr_sl_adjustment(
    *,
    entry: float,
    sl: float,
    tp: float,
    direction: str,
    atr_val: Optional[float],
    trade_signal: Any,
) -> Tuple[float, float, Optional[GateOutcome]]:
    if not atr_val or atr_val <= 0 or not sl or not entry:
        return sl, compute_actual_rr(entry, sl, tp), None

    min_sl_dist = atr_val * 1.5
    current_sl_dist = abs(entry - sl)
    if current_sl_dist >= min_sl_dist:
        return sl, compute_actual_rr(entry, sl, tp), None

    if direction == "long":
        new_sl = entry - min_sl_dist
    else:
        new_sl = entry + min_sl_dist
    trade_signal.stop_loss = new_sl

    new_tp_dist = abs(tp - entry) if tp else 0
    new_rr = new_tp_dist / min_sl_dist if min_sl_dist > 0 else 0
    if new_rr < 1.5:
        return (
            new_sl,
            new_rr,
            GateOutcome.block(
                gate_id="atr_sl_block",
                reason=f"After ATR SL widen, R:R={new_rr:.2f} < 1.5",
                stage="atr_sl_gate",
            ),
        )
    return new_sl, new_rr, None


def evaluate_rr_hard_floor(
    actual_rr: float,
    min_rr: float,
    *,
    is_aggressive: bool,
) -> Optional[GateOutcome]:
    if actual_rr >= min_rr:
        return None
    hard_floor = 1.0 if is_aggressive else 1.5
    if actual_rr < hard_floor:
        return GateOutcome.block(
            gate_id="rr_hard_floor",
            reason=f"R:R {actual_rr:.2f}:1 below hard floor {hard_floor:.1f}:1",
            stage="rr_hard_floor",
        )
    return None


def is_counter_trend_scalp_signal(
    *,
    trade_type: str,
    d1_bias: str,
    direction: str,
) -> bool:
    """Flag only — enforcement (0.70 cap + RR floor) lives in the pipeline's
    evaluate_direction_alignment_gate."""
    return (
        trade_type == "scalp"
        and d1_bias in ("bullish", "bearish")
        and (
            (d1_bias == "bullish" and direction == "short")
            or (d1_bias == "bearish" and direction == "long")
        )
    )


def resolve_session_at_time(
    snapshot_time: Optional[datetime],
    kill_zone_checker: Any = None,
) -> Tuple[str, bool]:
    if kill_zone_checker is None:
        return "", False
    if snapshot_time is not None:
        session = kill_zone_checker.get_current_session(snapshot_time)
    else:
        session = kill_zone_checker.get_current_session()
    if session:
        return (session.session_name or "").lower(), bool(session.is_kill_zone)
    return "", False


def build_reject_details(
    *,
    gate_path: List[str],
    direction: str = "",
    entry: float = 0.0,
    sl: float = 0.0,
    tp: float = 0.0,
    confidence: float = 0.0,
    extra: Optional[Dict] = None,
) -> Dict:
    details = {
        "gate_path": list(gate_path),
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": round(confidence, 4) if confidence else 0.0,
    }
    if extra:
        details.update(extra)
    return details


def _blocked_result(
    outcome: GateOutcome,
    *,
    entry: float,
    sl: float,
    tp: float,
    direction: str,
    confidence: float,
    gate_path: List[str],
    pipeline_ctx: Optional[TradeContext] = None,
    actual_rr: float = 0.0,
    confidence_components: Optional[Dict] = None,
    min_rr: float = 0.0,
    is_counter_trend_scalp: bool = False,
) -> PostClaudeGateResult:
    path = list(gate_path)
    if pipeline_ctx is not None and pipeline_ctx.gate_path:
        path = list(pipeline_ctx.gate_path) + [p for p in path if p not in pipeline_ctx.gate_path]
    path.extend(outcome.gate_path)
    return PostClaudeGateResult(
        blocked=True,
        gate_id=outcome.gate_id,
        reason=outcome.reason,
        outcome_type=outcome.outcome_type,
        gate_path=path,
        entry=entry,
        sl=sl,
        tp=tp,
        direction=direction,
        confidence=confidence,
        actual_rr=actual_rr,
        pipeline_ctx=pipeline_ctx,
        confidence_components=confidence_components,
        min_rr=min_rr,
        is_counter_trend_scalp=is_counter_trend_scalp,
    )


def _pass_result(
    *,
    gate_path: List[str],
    entry: float,
    sl: float,
    tp: float,
    direction: str,
    confidence: float,
    actual_rr: float,
    pipeline_ctx: Optional[TradeContext],
    confidence_components: Optional[Dict],
    min_rr: float,
    is_counter_trend_scalp: bool,
) -> PostClaudeGateResult:
    return PostClaudeGateResult(
        blocked=False,
        gate_path=gate_path,
        entry=entry,
        sl=sl,
        tp=tp,
        direction=direction,
        confidence=confidence,
        actual_rr=actual_rr,
        pipeline_ctx=pipeline_ctx,
        confidence_components=confidence_components,
        min_rr=min_rr,
        is_counter_trend_scalp=is_counter_trend_scalp,
    )


def _build_or_reuse_context(
    inp: PostClaudeGateInput,
    *,
    actual_rr: float,
    is_counter: bool,
    ctx: Optional[TradeContext],
) -> Tuple[TradeContext, ZoneGateSettings, bool]:
    if ctx is not None:
        zone_settings = inp.zone_settings or ZoneGateSettings(gate_mode="disabled")
        return ctx, zone_settings, inp.use_zone_gate

    if inp.build_pipeline_context is not None:
        built_ctx, zone_settings, use_zone = inp.build_pipeline_context(
            symbol=inp.symbol,
            trade_signal=inp.trade_signal,
            market_data=inp.market_data,
            analysis_results=inp.analysis_results,
            pd_analysis=inp.pd_analysis,
            current_price=inp.current_price,
            actual_rr=actual_rr,
            is_crypto=inp.is_crypto,
            is_counter_trend_scalp=is_counter,
        )
        return built_ctx, zone_settings, use_zone

    d1_bias = (inp.market_data.get("d1_bias") or "").lower()
    built_ctx = TradeContext.from_signal(
        symbol=inp.symbol,
        trade_signal=inp.trade_signal,
        market_data={
            "d1_bias": d1_bias,
            "h4_bias": (inp.market_data.get("h4_bias") or "").lower(),
            "m15_bias": (inp.market_data.get("m15_bias") or "").lower(),
            "regime": inp.analysis_results.get("regime", {}),
        },
        analysis_results=inp.analysis_results,
        current_price=inp.norm.entry or inp.current_price,
        pd_analysis=inp.pd_analysis,
        utc_hour=(inp.snapshot_time.hour if inp.snapshot_time else 0),
        weak_hours=tuple(settings.trading.weak_hours_by_symbol.get(inp.symbol, [])),
        is_index=(get_symbol_spec(inp.symbol).category == "index"),
        actual_rr=actual_rr,
        is_crypto=inp.is_crypto,
        is_counter_trend_scalp=is_counter,
    )
    zone_settings = inp.zone_settings or ZoneGateSettings(gate_mode="disabled")
    return built_ctx, zone_settings, inp.use_zone_gate


def _run_price_gates(
    inp: PostClaudeGateInput,
    cfg: PostClaudeGateSettings,
    gate_path: List[str],
) -> Tuple[PostClaudeGateResult, float, float, float, str, float, bool, Optional[Dict]]:
    signal = inp.trade_signal
    entry = inp.norm.entry
    sl = inp.norm.sl
    tp = inp.norm.tp
    direction = inp.norm.direction
    confidence_components = None

    gate_path.append("signal_normalized")

    atr_val = None
    if inp.df is not None and not inp.df.empty:
        try:
            atr_s = calculate_atr(inp.df, period=14)
            if not atr_s.empty and not np.isnan(atr_s.iloc[-1]):
                atr_val = float(atr_s.iloc[-1])
        except Exception:
            pass

    sl, actual_rr, atr_block = apply_atr_sl_adjustment(
        entry=entry,
        sl=sl,
        tp=tp,
        direction=direction,
        atr_val=atr_val,
        trade_signal=signal,
    )
    entry = signal.entry_price or entry
    tp = signal.take_profit or tp
    gate_path.append("atr_sl_check")
    if atr_block is not None:
        gate_path.append("atr_sl_block")
        return (
            _blocked_result(
                atr_block,
                entry=entry,
                sl=sl,
                tp=tp,
                direction=direction,
                confidence=signal.confidence,
                gate_path=gate_path,
                actual_rr=actual_rr,
            ),
            entry,
            sl,
            tp,
            direction,
            actual_rr,
            False,
            confidence_components,
        )

    trade_type = getattr(signal, "trade_type", "intraday") or "intraday"
    min_rr = resolve_min_rr(inp.symbol, trade_type)
    actual_rr = compute_actual_rr(entry, sl, tp)

    rr_block = evaluate_rr_hard_floor(actual_rr, min_rr, is_aggressive=inp.is_aggressive)
    gate_path.append("rr_ok" if rr_block is None else "rr_check")
    if rr_block is not None:
        gate_path.append("rr_hard_floor")
        return (
            _blocked_result(
                rr_block,
                entry=entry,
                sl=sl,
                tp=tp,
                direction=direction,
                confidence=signal.confidence,
                gate_path=gate_path,
                actual_rr=actual_rr,
                min_rr=min_rr,
            ),
            entry,
            sl,
            tp,
            direction,
            actual_rr,
            False,
            confidence_components,
        )

    d1_bias = (inp.market_data.get("d1_bias") or "").lower()
    is_counter = is_counter_trend_scalp_signal(
        trade_type=trade_type,
        d1_bias=d1_bias,
        direction=direction,
    )

    if inp.apply_secondary_modifiers and inp.modifier_input is not None:
        mi = inp.modifier_input
        at_breaker = False
        if mi.breaker_blocks:
            chk = entry or inp.current_price
            for bb in mi.breaker_blocks:
                if bb.bottom <= chk <= bb.top:
                    at_breaker = True
                    break
        conf_decision = apply_secondary_modifiers(
            signal.confidence,
            SecondaryModifierContext(
                direction=direction,
                symbol=inp.symbol,
                retail_contrarian=mi.retail_contrarian,
                vix_risk_mode=mi.vix_risk_mode,
                social_sentiment=inp.analysis_results.get("social_sentiment"),
                options_flow=inp.analysis_results.get("options_flow"),
                intermarket=inp.analysis_results.get("intermarket"),
                seasonal=inp.analysis_results.get("seasonal_pattern"),
                bond_yields=inp.analysis_results.get("bond_yields"),
                btc_dominance=inp.analysis_results.get("btc_dominance"),
                silver_bullet_ready=mi.silver_bullet_ready,
                at_breaker_block=at_breaker,
                current_price=inp.current_price,
            ),
        )
        signal.confidence = conf_decision.final
        confidence_components = confidence_decision_to_dict(conf_decision)
        gate_path.append("secondary_modifiers")

    return (
        _pass_result(
            gate_path=gate_path,
            entry=entry,
            sl=sl,
            tp=tp,
            direction=direction,
            confidence=signal.confidence,
            actual_rr=actual_rr,
            pipeline_ctx=None,
            confidence_components=confidence_components,
            min_rr=min_rr,
            is_counter_trend_scalp=is_counter,
        ),
        entry,
        sl,
        tp,
        direction,
        actual_rr,
        is_counter,
        confidence_components,
    )


def run_post_claude_gates(
    inp: PostClaudeGateInput,
    *,
    gate_settings: Optional[PostClaudeGateSettings] = None,
    kill_zone_checker: Any = None,
    start_at: PostClaudePhase = "price",
    stop_after: PostClaudePhase = "complete",
    ctx: Optional[TradeContext] = None,
    gate_path: Optional[List[str]] = None,
    carry: Optional[PostClaudeGateResult] = None,
) -> PostClaudeGateResult:
    """Run shared post-Claude gates; use start_at/stop_after for live scaling refresh."""
    cfg = gate_settings or PostClaudeGateSettings(
        gate_min_confidence=settings.trading.gate_min_confidence,
        asian_penalty=settings.trading.gate_session_penalty_asian,
        counter_trend_rr_floor=settings.trading.gate_counter_trend_rr_floor,
    )
    path = list(gate_path or [])
    signal = inp.trade_signal

    if start_at == "price":
        price_result, entry, sl, tp, direction, actual_rr, is_counter, conf_components = _run_price_gates(
            inp, cfg, path
        )
        if price_result.blocked or stop_after == "price":
            return price_result
    else:
        carried = carry or _pass_result(
            gate_path=path,
            entry=inp.norm.entry,
            sl=inp.norm.sl,
            tp=inp.norm.tp,
            direction=inp.norm.direction,
            confidence=signal.confidence,
            actual_rr=compute_actual_rr(inp.norm.entry, inp.norm.sl, inp.norm.tp),
            pipeline_ctx=ctx,
            confidence_components=None,
            min_rr=resolve_min_rr(
                inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
            ),
            is_counter_trend_scalp=False,
        )
        entry = carried.entry
        sl = carried.sl
        tp = carried.tp
        direction = carried.direction
        actual_rr = carried.actual_rr
        is_counter = carried.is_counter_trend_scalp
        conf_components = carried.confidence_components
        if start_at in ("permission", "flip", "complete") and ctx is None:
            ctx = carried.pipeline_ctx

    if start_at in ("price", "entry"):
        pipeline_ctx, zone_settings, use_zone = _build_or_reuse_context(
            inp, actual_rr=actual_rr, is_counter=is_counter, ctx=ctx
        )
        pipeline_ctx.scaling_aggressive = inp.scaling_aggressive

        session_name = inp.session_name
        is_kill = inp.is_kill_zone
        if not session_name:
            session_name, is_kill = resolve_session_at_time(inp.snapshot_time, kill_zone_checker)

        entry_outcome = evaluate_entry_gates(
            pipeline_ctx,
            zone_settings=zone_settings,
            use_zone_gate=use_zone,
            session_name=session_name,
            is_kill_zone=is_kill,
            asian_penalty=cfg.asian_penalty,
            scalp_rr_floor=cfg.counter_trend_rr_floor,
        )
        path.extend(pipeline_ctx.gate_path)
        signal.confidence = pipeline_ctx.confidence
        if entry_outcome.blocked:
            return _blocked_result(
                entry_outcome,
                entry=entry,
                sl=sl,
                tp=tp,
                direction=direction,
                confidence=pipeline_ctx.confidence,
                gate_path=path,
                pipeline_ctx=pipeline_ctx,
                actual_rr=actual_rr,
                confidence_components=conf_components,
                min_rr=resolve_min_rr(
                    inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
                ),
                is_counter_trend_scalp=is_counter,
            )
        ctx = pipeline_ctx
        if stop_after == "entry":
            return _pass_result(
                gate_path=path,
                entry=entry,
                sl=sl,
                tp=tp,
                direction=direction,
                confidence=pipeline_ctx.confidence,
                actual_rr=actual_rr,
                pipeline_ctx=pipeline_ctx,
                confidence_components=conf_components,
                min_rr=resolve_min_rr(
                    inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
                ),
                is_counter_trend_scalp=is_counter,
            )

    pipeline_ctx = ctx
    if pipeline_ctx is None:
        pipeline_ctx, _, _ = _build_or_reuse_context(
            inp, actual_rr=actual_rr, is_counter=is_counter, ctx=None
        )
    pipeline_ctx.scaling_aggressive = inp.scaling_aggressive
    signal.confidence = pipeline_ctx.confidence

    perm_outcome = evaluate_trade_permission_gates(
        pipeline_ctx,
        scaling_manager=inp.scaling_manager,
        daily_trades=inp.daily_trades,
        gate_min_confidence=cfg.gate_min_confidence,
        correlation_check=inp.correlation_check,
    )
    path.extend(perm_outcome.gate_path)
    signal.confidence = pipeline_ctx.confidence
    if perm_outcome.blocked:
        return _blocked_result(
            perm_outcome,
            entry=entry,
            sl=sl,
            tp=tp,
            direction=direction,
            confidence=pipeline_ctx.confidence,
            gate_path=path,
            pipeline_ctx=pipeline_ctx,
            actual_rr=actual_rr,
            confidence_components=conf_components,
            min_rr=resolve_min_rr(
                inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
            ),
            is_counter_trend_scalp=is_counter,
        )
    if stop_after == "permission":
        return _pass_result(
            gate_path=path,
            entry=entry,
            sl=sl,
            tp=tp,
            direction=direction,
            confidence=pipeline_ctx.confidence,
            actual_rr=actual_rr,
            pipeline_ctx=pipeline_ctx,
            confidence_components=conf_components,
            min_rr=resolve_min_rr(
                inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
            ),
            is_counter_trend_scalp=is_counter,
        )

    cb_outcome = evaluate_direction_circuit_breaker(
        symbol=inp.symbol,
        direction=direction,
        consecutive_losses=inp.direction_loss_streak,
        settings=cfg.circuit_breaker,
    )
    pipeline_ctx.gate_path.extend(cb_outcome.gate_path)
    path.extend(cb_outcome.gate_path)
    if cb_outcome.blocked:
        return _blocked_result(
            cb_outcome,
            entry=entry,
            sl=sl,
            tp=tp,
            direction=direction,
            confidence=signal.confidence,
            gate_path=path,
            pipeline_ctx=pipeline_ctx,
            actual_rr=actual_rr,
            confidence_components=conf_components,
            min_rr=resolve_min_rr(
                inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
            ),
            is_counter_trend_scalp=is_counter,
        )

    if inp.last_signal_direction is not None:
        flip_outcome = evaluate_flip_guard(
            symbol=inp.symbol,
            direction=direction,
            confidence=signal.confidence,
            last_signal_direction=inp.last_signal_direction,
            direction_flipped=inp.direction_flipped,
            reversal_reentry=getattr(signal, "reversal_reentry", False),
            settings=cfg.flip_guard,
            as_of=inp.snapshot_time,
        )
        pipeline_ctx.gate_path.extend(flip_outcome.gate_path)
        path.extend(flip_outcome.gate_path)
        if flip_outcome.blocked:
            return _blocked_result(
                flip_outcome,
                entry=entry,
                sl=sl,
                tp=tp,
                direction=direction,
                confidence=signal.confidence,
                gate_path=path,
                pipeline_ctx=pipeline_ctx,
                actual_rr=actual_rr,
                confidence_components=conf_components,
                min_rr=resolve_min_rr(
                    inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
                ),
                is_counter_trend_scalp=is_counter,
            )

    path.append("post_claude_gates_complete")
    return _pass_result(
        gate_path=path,
        entry=entry,
        sl=sl,
        tp=tp,
        direction=direction,
        confidence=signal.confidence,
        actual_rr=actual_rr,
        pipeline_ctx=pipeline_ctx,
        confidence_components=conf_components,
        min_rr=resolve_min_rr(
            inp.symbol, getattr(signal, "trade_type", "intraday") or "intraday"
        ),
        is_counter_trend_scalp=is_counter,
    )
