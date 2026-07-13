"""Post-Claude signal price normalization (A5 checks)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class NormalizedSignal:
    entry: float
    sl: float
    tp: float
    direction: str
    rejected: bool = False
    reject_reason: str = ""
    direction_flipped: bool = False
    audit_log: List[str] = field(default_factory=list)


def _fix_sl_at_entry(
    entry: float,
    sl: float,
    direction: str,
    key_levels: dict,
) -> float:
    if not sl or not entry or abs(sl - entry) >= entry * 0.0001:
        return sl
    support = key_levels.get("support_1")
    resistance = key_levels.get("resistance_1")
    if direction == "long":
        return support if support and support < entry else entry * 0.99
    if direction == "short":
        return resistance if resistance and resistance > entry else entry * 1.01
    return sl


def normalize_signal_prices(
    trade_signal: Any,
    claude_result: Any,
    current_price: float,
    symbol: str = "",
) -> NormalizedSignal:
    """Apply SL/TP sanity checks and direction coherence (A5)."""
    entry = trade_signal.entry_price or current_price
    sl = trade_signal.stop_loss
    tp = trade_signal.take_profit
    direction = trade_signal.direction
    audit: List[str] = []
    key_levels = getattr(claude_result, "key_levels", {}) or {}

    sl = _fix_sl_at_entry(entry, sl, direction, key_levels)
    if sl != trade_signal.stop_loss:
        trade_signal.stop_loss = sl
        audit.append(f"SL=entry auto-fix -> {sl}")

    direction_flipped = False
    if sl and tp and entry:
        levels_long = sl < entry and tp > entry
        levels_short = sl > entry and tp < entry
        if direction == "short" and levels_long:
            direction = "long"
            trade_signal.direction = "long"
            direction_flipped = True
            audit.append("Direction flipped SHORT->LONG")
        elif direction == "long" and levels_short:
            direction = "short"
            trade_signal.direction = "short"
            direction_flipped = True
            audit.append("Direction flipped LONG->SHORT")

    sl_wrong = (direction == "long" and sl and sl >= entry) or (
        direction == "short" and sl and sl <= entry
    )
    tp_wrong = (direction == "long" and tp and tp <= entry) or (
        direction == "short" and tp and tp >= entry
    )

    if sl_wrong and tp_wrong and sl and tp:
        sl, tp = tp, sl
        trade_signal.stop_loss = sl
        trade_signal.take_profit = tp
        sl_wrong = tp_wrong = False
        audit.append("SL/TP swapped")
    elif (sl_wrong or tp_wrong) and sl and tp:
        sl, tp = tp, sl
        trade_signal.stop_loss = sl
        trade_signal.take_profit = tp
        sl_wrong = (direction == "long" and sl >= entry) or (
            direction == "short" and sl <= entry
        )
        tp_wrong = (direction == "long" and tp <= entry) or (
            direction == "short" and tp >= entry
        )
        audit.append("SL/TP swap fix")

    sl = _fix_sl_at_entry(entry, sl, direction, key_levels)
    if sl != trade_signal.stop_loss:
        trade_signal.stop_loss = sl
        audit.append(f"Post-swap SL fix -> {sl}")

    if (direction == "long" and sl and sl >= entry) or (
        direction == "short" and sl and sl <= entry
    ):
        return NormalizedSignal(
            entry, sl, tp, direction, True, f"Invalid SL for {direction}", direction_flipped, audit
        )
    if (direction == "long" and tp and tp <= entry) or (
        direction == "short" and tp and tp >= entry
    ):
        return NormalizedSignal(
            entry, sl, tp, direction, True, f"Invalid TP for {direction}", direction_flipped, audit
        )

    if entry and current_price > 0:
        deviation = abs(entry - current_price) / current_price
        if deviation > 0.02:
            return NormalizedSignal(
                entry,
                sl,
                tp,
                direction,
                True,
                f"Entry deviates {deviation:.1%} from market",
                direction_flipped,
                audit,
            )

    if not sl or not tp:
        return NormalizedSignal(
            entry, sl, tp, direction, True, "Missing SL or TP", direction_flipped, audit
        )

    return NormalizedSignal(
        entry, sl, tp, direction, False, "", direction_flipped, audit
    )


def compute_actual_rr(entry: float, sl: float, tp: float) -> float:
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    return tp_dist / sl_dist if sl_dist > 0 else 0.0


def min_rr_for_symbol(symbol: str, trade_type: str, default_min_rr: float) -> float:
    from ..config import get_symbol_spec

    spec = get_symbol_spec(symbol)
    if spec.category == "crypto":
        by_type = {"scalp": 2.0, "intraday": 2.5, "swing": 3.5}
    else:
        by_type = {"scalp": 1.5, "intraday": 2.0, "swing": 3.0}
    return by_type.get(trade_type or "intraday", default_min_rr)
