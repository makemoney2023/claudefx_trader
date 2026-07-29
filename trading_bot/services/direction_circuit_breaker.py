"""Same-direction daily circuit breaker (shared by live and replay).

Run 16 showed the worst drawdowns came from repeatedly re-entering the same
direction after consecutive losses (the -8R max DD was one such streak).
After ``max_consecutive_losses`` same-direction losses on a symbol within a
single UTC day, further trades in that direction are blocked until the next
UTC day. A win (or the opposite direction) is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, Optional, Tuple

from .gate_outcome import GateOutcome


@dataclass
class DirectionCircuitBreakerSettings:
    max_consecutive_losses: int = 2  # 0 disables the breaker


def _as_utc(dt: datetime) -> datetime:
    """Treat naive timestamps (replay snapshots, MT5 history) as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_direction(direction: str) -> str:
    d = (direction or "").lower()
    if d == "buy":
        return "long"
    if d == "sell":
        return "short"
    return d


@dataclass
class DirectionLossTracker:
    """Consecutive same-direction loss streaks per symbol per UTC day."""

    _state: Dict[Tuple[str, str], Tuple[date, int]] = field(default_factory=dict)

    def record(
        self, symbol: str, direction: str, outcome: str, when: datetime
    ) -> None:
        """Record a closed trade outcome ('win'/'loss'; others are ignored)."""
        direction = _norm_direction(direction)
        day = _as_utc(when).date()
        key = (symbol, direction)
        if outcome == "win":
            self._state[key] = (day, 0)
        elif outcome == "loss":
            prev_day, prev_streak = self._state.get(key, (day, 0))
            streak = prev_streak + 1 if prev_day == day else 1
            self._state[key] = (day, streak)

    def consecutive_losses(
        self, symbol: str, direction: str, when: datetime
    ) -> int:
        direction = _norm_direction(direction)
        day = _as_utc(when).date()
        stored_day, streak = self._state.get((symbol, direction), (day, 0))
        return streak if stored_day == day else 0

    def to_dict(self) -> Dict[str, Dict[str, object]]:
        """JSON-serializable snapshot for bot state persistence."""
        return {
            f"{symbol}|{direction}": {"date": day.isoformat(), "streak": streak}
            for (symbol, direction), (day, streak) in self._state.items()
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "DirectionLossTracker":
        """Restore from a persisted snapshot; malformed entries are skipped."""
        tracker = cls()
        for key, val in (data or {}).items():
            try:
                symbol, direction = key.split("|", 1)
                tracker._state[(symbol, direction)] = (
                    date.fromisoformat(val["date"]),
                    int(val["streak"]),
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        return tracker


def evaluate_direction_circuit_breaker(
    *,
    symbol: str,
    direction: str,
    consecutive_losses: int,
    settings: Optional[DirectionCircuitBreakerSettings] = None,
) -> GateOutcome:
    """Block a direction for the rest of the UTC day after repeated losses."""
    cfg = settings or DirectionCircuitBreakerSettings()
    if cfg.max_consecutive_losses <= 0:
        return GateOutcome.pass_through("direction_circuit_breaker_disabled")
    if consecutive_losses >= cfg.max_consecutive_losses:
        return GateOutcome.block(
            gate_id="direction_circuit_breaker",
            reason=(
                f"{_norm_direction(direction).upper()} circuit breaker tripped on "
                f"{symbol}: {consecutive_losses} consecutive same-direction "
                f"losses today (max {cfg.max_consecutive_losses}); blocked "
                f"until next UTC day"
            ),
            stage="direction_circuit_breaker",
            outcome_type="no_trade",
        )
    return GateOutcome.pass_through("direction_circuit_breaker_pass")
