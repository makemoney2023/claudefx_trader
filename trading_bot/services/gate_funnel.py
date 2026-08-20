"""Unified gate decision telemetry and hypothetical outcome helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func, and_

from ..api.database import async_session_maker, SignalDecisionModel
from ..utils.logging import get_logger

logger = get_logger(__name__)

TERMINAL_OUTCOMES = frozenset({
    "no_trade",
    "mechanical_reject",
    "judge_reject",
    "judge_demote",
    "judge_failure",
    "pending_placed",
    "pending_filled",
    "pending_expired",
    "pending_cancelled",
    "market_filled",
    "execution_failure",
    # Shadow-mode gates that would have blocked (telemetry only; trade continues)
    "shadow_would_block",
})

HYPOTHETICAL_OUTCOME_TYPES = frozenset({
    "mechanical_reject",
    "judge_reject",
    "judge_demote",
    "judge_failure",
    "pending_expired",
    "pending_cancelled",
})

DEFAULT_OUTCOME_HORIZON_HOURS = 8


@dataclass
class HypotheticalOutcome:
    mfe_r: float
    mae_r: float
    hypothetical_result: str
    hypothetical_exit: Optional[str]
    hypothetical_r: float
    spread_cost_r: float
    data_complete: bool


def resolve_same_bar_tp_sl(
    direction: str,
    bar_open: float,
    high: float,
    low: float,
    sl: float,
    tp: float,
) -> Tuple[bool, bool]:
    """Deterministic same-bar TP/SL resolution using distance from bar open."""
    is_long = direction == "long"
    sl_hit = (is_long and low <= sl) or (not is_long and high >= sl)
    tp_hit = (is_long and high >= tp) or (not is_long and low <= tp)

    if sl_hit and tp_hit:
        dist_to_sl = abs(bar_open - sl)
        dist_to_tp = abs(bar_open - tp)
        if dist_to_sl <= dist_to_tp:
            return True, False
        return False, True
    return sl_hit, tp_hit


def evaluate_hypothetical_outcome(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    bars: List[Dict[str, Any]],
    *,
    spread_cost_r: float = 0.05,
    max_bars: int = 200,
) -> HypotheticalOutcome:
    """Evaluate MFE/MAE and deterministic TP/SL outcome from OHLCV bars."""
    if not bars or not entry or not sl or not tp:
        return HypotheticalOutcome(
            mfe_r=0.0,
            mae_r=0.0,
            hypothetical_result="unknown",
            hypothetical_exit=None,
            hypothetical_r=0.0,
            spread_cost_r=0.0,
            data_complete=False,
        )

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return HypotheticalOutcome(
            mfe_r=0.0,
            mae_r=0.0,
            hypothetical_result="unknown",
            hypothetical_exit=None,
            hypothetical_r=0.0,
            spread_cost_r=0.0,
            data_complete=False,
        )

    is_long = direction == "long"
    mfe_r = 0.0
    mae_r = 0.0

    for i, bar in enumerate(bars[:max_bars]):
        high = float(bar["high"])
        low = float(bar["low"])
        bar_open = float(bar.get("open", bar["close"]))

        if is_long:
            fav = (high - entry) / sl_dist
            adv = (entry - low) / sl_dist
        else:
            fav = (entry - low) / sl_dist
            adv = (high - entry) / sl_dist

        mfe_r = max(mfe_r, fav)
        mae_r = max(mae_r, adv)

        sl_hit, tp_hit = resolve_same_bar_tp_sl(direction, bar_open, high, low, sl, tp)

        if sl_hit:
            net_r = -1.0 - spread_cost_r
            return HypotheticalOutcome(
                mfe_r=mfe_r,
                mae_r=mae_r,
                hypothetical_result="would_have_lost",
                hypothetical_exit="sl_first",
                hypothetical_r=net_r,
                spread_cost_r=spread_cost_r,
                data_complete=True,
            )

        if tp_hit:
            raw_r = abs(tp - entry) / sl_dist
            net_r = raw_r - spread_cost_r
            return HypotheticalOutcome(
                mfe_r=mfe_r,
                mae_r=mae_r,
                hypothetical_result="would_have_won",
                hypothetical_exit="tp_first",
                hypothetical_r=net_r,
                spread_cost_r=spread_cost_r,
                data_complete=True,
            )

    return HypotheticalOutcome(
        mfe_r=mfe_r,
        mae_r=mae_r,
        hypothetical_result="unknown",
        hypothetical_exit=None,
        hypothetical_r=0.0,
        spread_cost_r=spread_cost_r,
        data_complete=True,
    )


class GateFunnel:
    """Persist and query terminal gate decisions via the application database."""

    def __init__(self, session_maker=None):
        self._session_maker = session_maker or async_session_maker

    async def record_decision(
        self,
        outcome_type: str,
        symbol: str,
        *,
        gate_id: Optional[str] = None,
        direction: str = "",
        entry: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        confidence: float = 0.0,
        session: str = "",
        mode: str = "",
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
        judge_verdict: Optional[str] = None,
        market_snapshot_ref: Optional[str] = None,
        confidence_components: Optional[Dict[str, Any]] = None,
        decision_id: Optional[str] = None,
    ) -> Optional[str]:
        if outcome_type not in TERMINAL_OUTCOMES:
            logger.warning(f"Unknown outcome_type {outcome_type} for {symbol}")
            return None

        did = decision_id or str(uuid.uuid4())
        needs_worker = outcome_type in HYPOTHETICAL_OUTCOME_TYPES

        try:
            async with self._session_maker() as db_session:
                row = SignalDecisionModel(
                    decision_id=did,
                    outcome_type=outcome_type,
                    gate_id=gate_id,
                    symbol=symbol,
                    direction=direction,
                    entry=entry or None,
                    sl=sl or None,
                    tp=tp or None,
                    confidence=confidence or None,
                    session=session,
                    mode=mode,
                    reason=reason,
                    details=details,
                    judge_verdict=judge_verdict,
                    market_snapshot_ref=market_snapshot_ref,
                    confidence_components=confidence_components,
                    outcome_horizon_hours=DEFAULT_OUTCOME_HORIZON_HOURS if needs_worker else None,
                    outcome_worker_status="pending" if needs_worker else "skipped",
                )
                db_session.add(row)
                await db_session.commit()
                return did
        except Exception as exc:
            logger.warning(f"Failed to record decision {outcome_type} {symbol}: {exc}")
            return None

    async def update_hypothetical_outcome(
        self,
        decision_id: str,
        outcome: HypotheticalOutcome,
    ) -> bool:
        try:
            async with self._session_maker() as db_session:
                result = await db_session.execute(
                    select(SignalDecisionModel).where(
                        SignalDecisionModel.decision_id == decision_id
                    )
                )
                row = result.scalar_one_or_none()
                if not row:
                    return False

                if row.outcome_worker_status == "complete":
                    return False

                row.mfe_r = outcome.mfe_r
                row.mae_r = outcome.mae_r
                row.hypothetical_result = outcome.hypothetical_result
                row.hypothetical_exit = outcome.hypothetical_exit
                row.hypothetical_r = outcome.hypothetical_r
                row.spread_cost_r = outcome.spread_cost_r
                row.outcome_worker_status = "complete" if outcome.data_complete else "data_missing"
                row.outcome_evaluated_at = datetime.now(timezone.utc)
                await db_session.commit()
                return True
        except Exception as exc:
            logger.warning(f"Failed to update hypothetical outcome {decision_id}: {exc}")
            return False

    async def get_aggregate_analytics(self, days_back: int = 30) -> Dict[str, Any]:
        """Aggregate gate expectancy and MFE coverage for read-only API."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        try:
            async with self._session_maker() as db_session:
                result = await db_session.execute(
                    select(SignalDecisionModel).where(
                        SignalDecisionModel.timestamp >= cutoff
                    )
                )
                rows = list(result.scalars().all())

            if not rows:
                return {
                    "period_days": days_back,
                    "total_decisions": 0,
                    "gate_expectancy": {},
                    "mfe_coverage": {"eligible": 0, "evaluated": 0, "coverage_pct": 0.0},
                    "false_rejection": {},
                }

            eligible = [r for r in rows if r.outcome_type in HYPOTHETICAL_OUTCOME_TYPES]
            evaluated = [r for r in eligible if r.outcome_worker_status == "complete"]
            coverage_pct = len(evaluated) / len(eligible) * 100 if eligible else 0.0

            gate_stats: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                key = row.gate_id or row.outcome_type
                if key not in gate_stats:
                    gate_stats[key] = {
                        "count": 0,
                        "hypothetical_wins": 0,
                        "hypothetical_losses": 0,
                        "avg_hypothetical_r": 0.0,
                        "r_sum": 0.0,
                        "r_count": 0,
                    }
                gate_stats[key]["count"] += 1
                if row.hypothetical_result == "would_have_won":
                    gate_stats[key]["hypothetical_wins"] += 1
                    if row.hypothetical_r is not None:
                        gate_stats[key]["r_sum"] += row.hypothetical_r
                        gate_stats[key]["r_count"] += 1
                elif row.hypothetical_result == "would_have_lost":
                    gate_stats[key]["hypothetical_losses"] += 1
                    if row.hypothetical_r is not None:
                        gate_stats[key]["r_sum"] += row.hypothetical_r
                        gate_stats[key]["r_count"] += 1

            gate_expectancy = {}
            for key, stats in gate_stats.items():
                resolved = stats["hypothetical_wins"] + stats["hypothetical_losses"]
                gate_expectancy[key] = {
                    "count": stats["count"],
                    "false_rejection_rate": (
                        stats["hypothetical_wins"] / resolved if resolved else None
                    ),
                    "avg_hypothetical_r": (
                        stats["r_sum"] / stats["r_count"] if stats["r_count"] else None
                    ),
                }

            false_rejection: Dict[str, Dict[str, int]] = {}
            for category in ("judge_reject", "judge_demote", "judge_failure", "mechanical_reject", "pending_expired", "pending_cancelled"):
                cat_rows = [r for r in rows if r.outcome_type == category]
                false_rejection[category] = {
                    "total": len(cat_rows),
                    "would_have_won": len([r for r in cat_rows if r.hypothetical_result == "would_have_won"]),
                    "would_have_lost": len([r for r in cat_rows if r.hypothetical_result == "would_have_lost"]),
                    "unknown": len([r for r in cat_rows if r.hypothetical_result not in ("would_have_won", "would_have_lost")]),
                }

            from .edge_policies import build_gate_tuning_recommendations

            return {
                "period_days": days_back,
                "total_decisions": len(rows),
                "gate_expectancy": gate_expectancy,
                "mfe_coverage": {
                    "eligible": len(eligible),
                    "evaluated": len(evaluated),
                    "coverage_pct": round(coverage_pct, 2),
                },
                "false_rejection": false_rejection,
                "tuning_recommendations": build_gate_tuning_recommendations(
                    gate_expectancy
                ),
            }
        except Exception as exc:
            logger.error(f"Failed to compute gate analytics: {exc}")
            return {}

    async def get_latest_decision(
        self,
        symbol: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent terminal decision, optionally filtered by symbol."""
        try:
            async with self._session_maker() as db_session:
                query = select(SignalDecisionModel).order_by(
                    SignalDecisionModel.timestamp.desc()
                )
                if symbol:
                    query = query.where(SignalDecisionModel.symbol == symbol.upper())
                result = await db_session.execute(query.limit(1))
                row = result.scalar_one_or_none()
            if not row:
                return None
            return {
                "symbol": row.symbol,
                "outcome_type": row.outcome_type,
                "gate_id": row.gate_id,
                "reason": row.reason,
                "judge_verdict": row.judge_verdict,
                "direction": row.direction,
                "timestamp": row.timestamp,
                "confidence": row.confidence,
                "entry": row.entry,
                "sl": row.sl,
                "tp": row.tp,
            }
        except Exception as exc:
            logger.debug(f"get_latest_decision failed: {exc}")
            return None

    async def record_post_block_mfe_async(self, gate_block_id: int, mfe_r: float) -> bool:
        try:
            async with self._session_maker() as conn:
                result = await conn.execute(
                    select(SignalDecisionModel).where(SignalDecisionModel.id == gate_block_id)
                )
                row = result.scalar_one_or_none()
                if not row:
                    return False
                row.mfe_r = mfe_r
                row.outcome_worker_status = "complete"
                row.outcome_evaluated_at = datetime.now(timezone.utc)
                await conn.commit()
            return True
        except Exception as exc:
            logger.warning(f"Failed to record post-block MFE id={gate_block_id}: {exc}")
            return False

    # Backward-compatible sync helpers for legacy callers/tests
    def record_block(
        self,
        gate_id: str,
        symbol: str,
        direction: str = "",
        entry: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        confidence: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        import asyncio

        async def _run():
            did = await self.record_decision(
                "mechanical_reject",
                symbol,
                gate_id=gate_id,
                direction=direction,
                entry=entry,
                sl=sl,
                tp=tp,
                confidence=confidence,
                details=details,
            )
            if not did:
                return None
            async with self._session_maker() as db_session:
                result = await db_session.execute(
                    select(SignalDecisionModel).where(
                        SignalDecisionModel.decision_id == did
                    )
                )
                row = result.scalar_one_or_none()
                return row.id if row else None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        else:
            return loop.run_until_complete(_run())

    def record_post_block_mfe(self, gate_block_id: int, mfe_r: float) -> bool:
        import asyncio

        async def _run():
            async with self._session_maker() as db_session:
                result = await db_session.execute(
                    select(SignalDecisionModel).where(SignalDecisionModel.id == gate_block_id)
                )
                row = result.scalar_one_or_none()
                if not row:
                    return False
                row.mfe_r = mfe_r
                row.outcome_worker_status = "complete"
                row.outcome_evaluated_at = datetime.now(timezone.utc)
                await db_session.commit()
                return True

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        else:
            return loop.run_until_complete(_run())


_default_funnel: Optional[GateFunnel] = None


def get_gate_funnel() -> GateFunnel:
    global _default_funnel
    if _default_funnel is None:
        _default_funnel = GateFunnel()
    return _default_funnel
