"""
Pure helpers for WIN optimization orchestrator fixes.

Extracted from main.py for unit testing without booting the full bot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple


def is_friday_weekend_close_time(now_est: datetime) -> bool:
    """True when Friday at or after 16:30 US/Eastern."""
    if now_est.weekday() != 4:
        return False
    return now_est.hour > 16 or (now_est.hour == 16 and now_est.minute >= 30)


def is_friday_afternoon_entry_block(now_est: datetime) -> bool:
    """True when Friday noon or later — block new forex entries."""
    return now_est.weekday() == 4 and now_est.hour >= 12


@dataclass
class FridaySessionDecision:
    """Friday close vs entry-block decisions (independent gates)."""

    close_forex: bool
    entry_symbols: List[str]


def apply_friday_session_gates(
    now_est: datetime,
    symbols: List[str],
    crypto_symbols: Set[str],
) -> FridaySessionDecision:
    """
    Apply Friday weekend-close and afternoon entry block independently.

    - close_forex: True at/after 16:30 ET on Friday
    - entry_symbols: forex removed from noon ET Friday onward (crypto kept)
    """
    close_forex = is_friday_weekend_close_time(now_est)
    entry_symbols = list(symbols)
    if is_friday_afternoon_entry_block(now_est):
        entry_symbols = [s for s in symbols if s in crypto_symbols]
    return FridaySessionDecision(close_forex=close_forex, entry_symbols=entry_symbols)


@dataclass
class PreClaudeViability:
    """Outcome of the cheap pre-LLM viability check."""

    proceed: bool
    reasons: List[str] = field(default_factory=list)


def _direction_structurally_blocked(
    direction: str,
    d1_bias: str,
    h4_bias: str,
    m15_bias: str,
    amd_phase: str,
) -> Optional[str]:
    """
    Return a reason string when the entry-gate stack guarantees rejection
    for this direction, else None.

    Mirrors evaluate_m15_gate / evaluate_htf_alignment_gate. Their
    0.55-capped exception paths (pullback, counter-trend scalp) land below
    the 0.60 execution floor, so they are dead ends and count as blocked.
    Anything ambiguous (neutral/unknown biases) counts as viable.
    """
    opposing = "bearish" if direction == "long" else "bullish"

    if m15_bias == opposing and amd_phase != "manipulation":
        return f"{direction}: M15 {m15_bias} opposes (no manipulation phase)"

    if d1_bias == opposing and h4_bias == opposing:
        return f"{direction}: D1+H4 both {opposing}"

    return None


# Matches trade_execution.validate_limit_zone hard thresholds.
_EXTREME_DISCOUNT_RETRACE = 0.30
_EXTREME_PREMIUM_RETRACE = 0.70


def pre_claude_viability(
    *,
    d1_bias: str,
    h4_bias: str,
    m15_bias: str,
    amd_phase: str = "unknown",
    relative_volume: float = 1.0,
    in_kill_zone: bool = False,
    silver_bullet_window: bool = False,
    retrace_pct: Optional[float] = None,
) -> PreClaudeViability:
    """
    Decide whether calling Claude can possibly produce an executable trade.

    Skips the LLM when the mechanical gate stack already guarantees
    rejection — including extreme premium/discount vs the only-viable
    direction (limit_zone hard-blocks sell_limit in discount / buy_limit
    in premium). Kill zones still bypass soft structural/volume skips,
    but not extreme-zone conflicts (those burn API spend for a guaranteed
    post-judge block).
    """
    reasons: List[str] = []

    long_block = _direction_structurally_blocked(
        "long", d1_bias, h4_bias, m15_bias, amd_phase
    )
    short_block = _direction_structurally_blocked(
        "short", d1_bias, h4_bias, m15_bias, amd_phase
    )

    # Hard zone conflict — applies even inside kill zones.
    if retrace_pct is not None:
        if short_block is None and long_block is not None and retrace_pct < _EXTREME_DISCOUNT_RETRACE:
            reasons.append(
                f"short-only path but extreme discount (retrace={retrace_pct:.0%}) — "
                "sell_limit blocked by zone gate; wait for premium pullback"
            )
        elif long_block is None and short_block is not None and retrace_pct > _EXTREME_PREMIUM_RETRACE:
            reasons.append(
                f"long-only path but extreme premium (retrace={retrace_pct:.0%}) — "
                "buy_limit blocked by zone gate; wait for discount"
            )

    if reasons:
        return PreClaudeViability(proceed=False, reasons=reasons)

    if in_kill_zone or silver_bullet_window:
        return PreClaudeViability(proceed=True)

    if relative_volume < 0.3:
        reasons.append(
            f"volume {relative_volume:.2f}x < 0.3 — dead market blocks all entries"
        )

    if long_block and short_block:
        reasons.append(long_block)
        reasons.append(short_block)

    if reasons:
        return PreClaudeViability(proceed=False, reasons=reasons)
    return PreClaudeViability(proceed=True)


def order_type_matches_direction(order_type: str, direction: str) -> bool:
    """True when order side is coherent with long/short direction."""
    ot = (order_type or "market").lower()
    direction = (direction or "").lower()
    if ot == "market" or ot in ("", "none"):
        return True
    if ot.startswith("buy"):
        return direction == "long"
    if ot.startswith("sell"):
        return direction == "short"
    return True


def displacement_gate_action(
    order_type: str,
    *,
    distribution_confirmed: Optional[bool],
    amd_phase: Optional[str],
) -> str:
    """
    Decide market vs convert vs reject for displacement gate.

    Returns: 'allow_market' | 'convert_pending' | 'reject' | 'unchanged'
    """
    if (order_type or "").lower() != "market":
        return "unchanged"
    if distribution_confirmed is None:
        return "unchanged"
    if distribution_confirmed:
        return "allow_market"
    phase = (amd_phase or "").lower()
    if phase in ("manipulation", "accumulation"):
        return "convert_pending"
    return "reject"


def entry_deviation_pct(entry: float, current_price: float) -> float:
    if not entry or current_price <= 0:
        return 0.0
    return abs(entry - current_price) / current_price


def is_intentional_structural_limit(order_type: str, direction: str, entry: float, current_price: float) -> bool:
    ot = (order_type or "").lower()
    if ot == "buy_limit" and direction == "long" and entry < current_price:
        return True
    if ot == "sell_limit" and direction == "short" and entry > current_price:
        return True
    return False


def should_reject_entry_deviation(
    order_type: str,
    direction: str,
    entry: float,
    current_price: float,
    *,
    market_max_pct: float = 0.02,
    limit_max_pct: float = 0.05,
) -> Tuple[bool, float, str]:
    deviation = entry_deviation_pct(entry, current_price)
    if deviation <= 0:
        return False, deviation, ""

    if is_intentional_structural_limit(order_type, direction, entry, current_price):
        if deviation <= limit_max_pct:
            return False, deviation, ""
        return True, deviation, f"structural limit deviates {deviation:.1%} (max {limit_max_pct:.0%})"

    if deviation > market_max_pct:
        return True, deviation, f"entry deviates {deviation:.1%} (max {market_max_pct:.0%})"
    return False, deviation, ""


def apply_confidence_caps(base: float, caps: List[float]) -> float:
    if not caps:
        return base
    return min(base, *caps)


@dataclass
class ConfidenceDecision:
    """Immutable confidence pass: caps apply after boosts and cannot be undone."""

    base: float
    boosts: List[Tuple[str, float]] = field(default_factory=list)
    penalties: List[Tuple[str, float]] = field(default_factory=list)
    caps: List[Tuple[str, float]] = field(default_factory=list)
    final: float = 0.0

    def compute(self) -> "ConfidenceDecision":
        value = self.base
        for name, delta in self.boosts:
            value += delta
        for name, delta in self.penalties:
            value += delta
        for name, cap in self.caps:
            value = min(value, cap)
        self.final = max(0.0, min(1.0, value))
        return self


def build_confidence_decision(
    base: float,
    *,
    boosts: Optional[List[Tuple[str, float]]] = None,
    penalties: Optional[List[Tuple[str, float]]] = None,
    caps: Optional[List[Tuple[str, float]]] = None,
) -> ConfidenceDecision:
    decision = ConfidenceDecision(
        base=base,
        boosts=list(boosts or []),
        penalties=list(penalties or []),
        caps=list(caps or []),
    )
    return decision.compute()


def classify_a_plus(
    setup_grade: str,
    confluence_count: int,
    *,
    min_confluence: int = 4,
) -> bool:
    """Explicit A+ classification from setup grade and confluence — not trade_type alone."""
    grade = (setup_grade or "").strip().upper().replace(" ", "")
    if grade in {"A+", "APLUS"}:
        return True
    return grade == "A" and confluence_count >= min_confluence


def cap_confidence_once(
    current: float,
    cap: float,
    applied_categories: Set[str],
    category: str,
) -> float:
    if category in applied_categories:
        return current
    applied_categories.add(category)
    return min(current, cap)


def _fill_distance(direction: str, entry: float, market: float) -> float:
    if direction == "long":
        return max(0.0, market - entry)
    return max(0.0, entry - market)


def _rebase_level(level: float, old_entry: float, new_entry: float) -> float:
    if not level or not old_entry:
        return level
    return new_entry + (level - old_entry)


def ote_pullback_entry(direction: str, swing_high: float, swing_low: float) -> float:
    """
    Direction-aware OTE pullback entry price.

    Longs buy the 79% pullback of an up-move (discount side of the range);
    shorts sell the 62% pullback of a down-move (premium side). Returns 0.0
    when the swing range is degenerate.
    """
    range_size = swing_high - swing_low
    if range_size <= 0:
        return 0.0
    if direction == "long":
        return swing_high - (range_size * 0.786)
    return swing_low + (range_size * 0.618)


def rebase_sl_tp_for_new_entry(
    *,
    stop_loss: float,
    take_profit: float,
    old_entry: float,
    new_entry: float,
) -> Tuple[float, float]:
    """Preserve SL/TP offsets when an entry is moved (keeps R:R and SL side)."""
    return (
        _rebase_level(stop_loss, old_entry, new_entry),
        _rebase_level(take_profit, old_entry, new_entry),
    )


def apply_demote_policy(
    direction: str,
    current_price: float,
    original_entry: float,
    stop_loss: float,
    take_profit: float,
    order_type: str,
    suggested_entry: Optional[float],
    *,
    at_zone_pct: float = 0.0005,
    default_offset_pct: float = 0.001,
) -> Dict[str, Any]:
    entry = original_entry or current_price
    ot = (order_type or "market").lower()
    zone_dist = entry_deviation_pct(entry, current_price)

    if zone_dist <= at_zone_pct and ot == "market":
        return {
            "action": "size_reduce",
            "demoted_entry": entry,
            "order_type": "market",
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "size_multiplier": 0.5,
            "reason": "at_zone_size_reduce",
        }

    if ot in ("buy_limit", "sell_limit"):
        candidate = entry
        if suggested_entry and suggested_entry > 0:
            if _fill_distance(direction, suggested_entry, current_price) < _fill_distance(
                direction, entry, current_price
            ):
                candidate = suggested_entry
        return {
            "action": "keep_limit",
            "demoted_entry": candidate,
            "order_type": ot,
            "stop_loss": _rebase_level(stop_loss, entry, candidate),
            "take_profit": _rebase_level(take_profit, entry, candidate),
            "size_multiplier": 0.75,
            "reason": "keep_existing_limit",
        }

    if suggested_entry and suggested_entry > 0:
        demoted_entry = suggested_entry
        reason = "judge_suggested_entry"
    else:
        if direction == "long":
            demoted_entry = round(current_price * (1 - default_offset_pct), 5)
        else:
            demoted_entry = round(current_price * (1 + default_offset_pct), 5)
        reason = "default_offset_limit"

    if _fill_distance(direction, demoted_entry, current_price) > _fill_distance(
        direction, entry, current_price
    ):
        demoted_entry = entry
        reason = "kept_closer_entry"

    new_ot = "buy_limit" if direction == "long" else "sell_limit"
    return {
        "action": "limit",
        "demoted_entry": demoted_entry,
        "order_type": new_ot,
        "stop_loss": _rebase_level(stop_loss, entry, demoted_entry),
        "take_profit": _rebase_level(take_profit, entry, demoted_entry),
        "size_multiplier": 0.75,
        "reason": reason,
    }


def _market_to_pending(direction: str, entry: float, market: float) -> str:
    if direction == "long":
        return "buy_limit" if entry < market else "buy_stop"
    return "sell_limit" if entry > market else "sell_stop"


def resolve_order_type_for_fill(
    order_type: str,
    direction: str,
    entry_price: float,
    current_price: float,
    *,
    at_zone_pct: float = 0.001,
    pending_threshold_pct: float = 0.001,
) -> Tuple[str, str]:
    ot = (order_type or "market").lower()
    if not entry_price or current_price <= 0:
        return ot, "unchanged"

    diff_pct = entry_deviation_pct(entry_price, current_price)
    if ot == "market":
        if diff_pct <= at_zone_pct:
            return "market", "at_zone_keep_market"
        if diff_pct > pending_threshold_pct:
            return _market_to_pending(direction, entry_price, current_price), "converted_to_pending"
        return "market", "within_threshold"
    return ot, "explicit_pending"


def resolve_trading_mode_from_state(
    persisted_mode: Optional[str],
    configured_mode: Optional[str] = None,
) -> str:
    for candidate in (persisted_mode, configured_mode):
        if candidate:
            normalized = candidate.strip().lower()
            if normalized in ("aggressive", "normal", "conservative", "defensive"):
                return normalized
    return "normal"


def validate_signal_coherence(
    entry: float,
    sl: Optional[float],
    tp: Optional[float],
    direction: str,
    *,
    sl_entry_tolerance: float = 0.0001,
) -> Tuple[bool, str]:
    """
    Reject incoherent Claude signals instead of auto-fixing (REC#9).

    Mechanical broker-distance clamps (e.g. ATR min SL) belong in main.py, not here.
    """
    if not entry or entry <= 0:
        return False, "missing or invalid entry price"

    _dir = (direction or "").lower()
    if _dir not in ("long", "short"):
        return False, f"invalid direction: {direction}"

    if sl and abs(sl - entry) < entry * sl_entry_tolerance:
        return False, f"SL equals entry ({sl}) — zero risk distance"

    if sl and tp:
        levels_say_long = sl < entry and tp > entry
        levels_say_short = sl > entry and tp < entry

        if _dir == "short" and levels_say_long:
            return (
                False,
                f"direction SHORT incoherent with levels (SL={sl} < entry={entry} < TP={tp})",
            )
        if _dir == "long" and levels_say_short:
            return (
                False,
                f"direction LONG incoherent with levels (TP={tp} < entry={entry} < SL={sl})",
            )

    if _dir == "long":
        if sl and sl >= entry:
            return False, f"SL ({sl}) on wrong side of entry ({entry}) for long"
        if tp and tp <= entry:
            return False, f"TP ({tp}) on wrong side of entry ({entry}) for long"
    else:
        if sl and sl <= entry:
            return False, f"SL ({sl}) on wrong side of entry ({entry}) for short"
        if tp and tp >= entry:
            return False, f"TP ({tp}) on wrong side of entry ({entry}) for short"

    return True, ""
