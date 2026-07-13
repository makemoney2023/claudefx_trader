"""
Shared fail-closed trade judge adapter for regular and reversal entry paths.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class JudgeVerdict(str, Enum):
    APPROVE = "APPROVE"
    DEMOTE = "DEMOTE"
    REJECT = "REJECT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class JudgeOutcome:
    verdict: JudgeVerdict
    reason: str
    suggested_entry: Optional[float] = None
    risk_flags: List[str] = field(default_factory=list)

    def blocks_execution(self) -> bool:
        return self.verdict in (JudgeVerdict.REJECT, JudgeVerdict.UNAVAILABLE)

    def allows_demote_execution(self) -> bool:
        return self.verdict == JudgeVerdict.DEMOTE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "suggested_entry": self.suggested_entry,
            "risk_flags": list(self.risk_flags),
        }


def unavailable_outcome(reason: str, flags: Optional[List[str]] = None) -> JudgeOutcome:
    return JudgeOutcome(
        verdict=JudgeVerdict.UNAVAILABLE,
        reason=reason,
        suggested_entry=None,
        risk_flags=flags or ["judge_unavailable"],
    )


def normalize_judge_response(raw: Optional[Dict[str, Any]]) -> JudgeOutcome:
    if not raw or not isinstance(raw, dict):
        return unavailable_outcome("Judge returned malformed response")

    verdict_raw = str(raw.get("verdict", "")).upper().strip()
    if verdict_raw not in {v.value for v in JudgeVerdict if v != JudgeVerdict.UNAVAILABLE}:
        return unavailable_outcome(
            f"Judge returned invalid verdict: {verdict_raw or 'missing'}",
            flags=["judge_malformed"],
        )

    verdict = JudgeVerdict(verdict_raw)
    suggested = raw.get("suggested_entry")
    if suggested is not None:
        try:
            suggested = float(suggested)
            if suggested <= 0:
                suggested = None
        except (TypeError, ValueError):
            suggested = None

    if verdict == JudgeVerdict.DEMOTE and suggested is None:
        # Explicit DEMOTE still valid — caller may apply default offset policy.
        pass

    flags = raw.get("risk_flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]

    return JudgeOutcome(
        verdict=verdict,
        reason=str(raw.get("reason", "") or ""),
        suggested_entry=suggested,
        risk_flags=[str(f) for f in flags],
    )


async def run_trade_judge(
    claude_client: Any,
    signal_dict: Dict[str, Any],
    risk_metrics: Dict[str, Any],
    learning_context: str = "",
    *,
    timeout: float = 45.0,
) -> JudgeOutcome:
    # Timeout note: the judge runs on Opus 4.8 with adaptive thinking at high effort,
    # which takes ~10-20s in practice. The old 8s Sonnet-era budget would time out
    # on nearly every call and (fail-closed) block ALL trades.
    if not claude_client or not getattr(claude_client, "api_key", None):
        return unavailable_outcome("Judge client unavailable — no API key")

    if not getattr(claude_client, "async_client", None):
        return unavailable_outcome("Judge client unavailable — async client not configured")

    try:
        raw = await asyncio.wait_for(
            claude_client.judge_trade(signal_dict, risk_metrics, learning_context),
            timeout=timeout,
        )
        return normalize_judge_response(raw)
    except asyncio.TimeoutError:
        return unavailable_outcome("Judge timeout", flags=["judge_timeout"])
    except Exception as exc:
        return unavailable_outcome(f"Judge error: {exc}", flags=["judge_exception"])


def build_judge_signal_dict(
    symbol: str,
    trade_signal: Any,
    current_price: float,
) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "direction": trade_signal.direction,
        "confidence": trade_signal.confidence,
        "entry_price": trade_signal.entry_price or current_price,
        "stop_loss": trade_signal.stop_loss,
        "take_profit": trade_signal.take_profit,
        "order_type": getattr(trade_signal, "order_type", "market"),
        "trade_type": getattr(trade_signal, "trade_type", "intraday"),
        "reasoning": getattr(trade_signal, "reasoning", ""),
    }


def build_judge_risk_metrics(
    symbol: str,
    trade_signal: Any,
    current_price: float,
    *,
    session_name: str = "",
    account_equity: float = 2000.0,
    daily_trades: int = 0,
    daily_pnl: float = 0.0,
    lots: float = 0.05,
    max_daily_trades: int = 5,
) -> Dict[str, Any]:
    from ..config import get_symbol_spec, settings

    entry = trade_signal.entry_price or current_price
    sl = trade_signal.stop_loss or 0
    tp = trade_signal.take_profit or 0
    sl_distance = abs(entry - sl) if sl else 0
    tp_distance = abs(tp - entry) if tp else 0
    risk_reward = tp_distance / sl_distance if sl_distance > 0 else 0

    spec = get_symbol_spec(symbol)
    position_size_pct = 0.0
    at_broker_minimum = False
    if account_equity > 0 and sl_distance > 0:
        risk_amount = sl_distance * lots * spec.contract_size
        position_size_pct = risk_amount / account_equity
        at_broker_minimum = lots <= spec.volume_min

    return {
        "account_balance": account_equity,
        "daily_pnl": daily_pnl,
        "drawdown_pct": 0.0,
        "risk_reward": risk_reward,
        "position_size_pct": position_size_pct,
        "at_broker_minimum_lots": at_broker_minimum,
        "trades_today": daily_trades,
        "max_daily_trades": max_daily_trades,
        "session": session_name,
        "symbol_category": spec.category,
        "sl_distance": sl_distance,
        "tp_distance": tp_distance,
    }


async def run_replay_trade_judge(
    claude_client: Any,
    symbol: str,
    trade_signal: Any,
    current_price: float,
    *,
    session_name: str = "",
    account_equity: float = 2000.0,
    daily_trades: int = 0,
    daily_pnl: float = 0.0,
    lots: float = 0.05,
    max_daily_trades: int = 5,
    learning_service: Any = None,
    learning_context: str = "",
    timeout: float = 8.0,
) -> JudgeOutcome:
    """Invoke the same fail-closed judge adapter used by live trading."""
    signal_dict = build_judge_signal_dict(symbol, trade_signal, current_price)
    risk_metrics = build_judge_risk_metrics(
        symbol,
        trade_signal,
        current_price,
        session_name=session_name,
        account_equity=account_equity,
        daily_trades=daily_trades,
        daily_pnl=daily_pnl,
        lots=lots,
        max_daily_trades=max_daily_trades,
    )
    ctx = learning_context
    if not ctx and learning_service is not None:
        try:
            ctx = await learning_service.build_context_for_claude(symbol, session_name)
        except Exception:
            ctx = ""
    return await run_trade_judge(
        claude_client,
        signal_dict,
        risk_metrics,
        ctx,
        timeout=timeout,
    )
