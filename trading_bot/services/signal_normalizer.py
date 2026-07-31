"""Post-Claude signal price normalization (A5 checks)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


def recover_missing_entry_from_mechanical(
    *,
    direction: str,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    mechanical_setup: Optional[dict],
) -> Optional[float]:
    """Fill a missing Claude entry from mechanical when direction agrees.

    Keeps Claude's SL/TP authoritative. Returns None when mechanical is
    missing, disagrees on direction, or the mechanical entry is not
    coherent with Claude's stop/target.
    """
    direction = (direction or "").lower()
    if direction not in ("long", "short"):
        return None
    if not mechanical_setup or stop_loss is None or take_profit is None:
        return None

    mech_dir = str(mechanical_setup.get("direction") or "").lower()
    if mech_dir != direction:
        return None

    zone = mechanical_setup.get("entry_zone") or {}
    raw_entry = (
        zone.get("optimal")
        if isinstance(zone, dict)
        else None
    )
    if raw_entry is None:
        raw_entry = mechanical_setup.get("optimal_entry") or mechanical_setup.get(
            "entry_price"
        )
    try:
        entry = float(raw_entry)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None

    sl = float(stop_loss)
    tp = float(take_profit)
    if direction == "long" and not (sl < entry < tp):
        return None
    if direction == "short" and not (tp < entry < sl):
        return None
    return entry


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
    direction = (getattr(trade_signal, "direction", None) or "no_trade").lower()
    # no_trade is a valid outcome — never require SL/TP for it.
    if direction == "no_trade":
        return NormalizedSignal(
            entry=float(trade_signal.entry_price or current_price or 0.0),
            sl=float(trade_signal.stop_loss or 0.0),
            tp=float(trade_signal.take_profit or 0.0),
            direction="no_trade",
            rejected=False,
        )

    entry = trade_signal.entry_price or current_price
    sl = trade_signal.stop_loss
    tp = trade_signal.take_profit
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
