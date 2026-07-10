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
    timeout: float = 8.0,
) -> JudgeOutcome:
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
