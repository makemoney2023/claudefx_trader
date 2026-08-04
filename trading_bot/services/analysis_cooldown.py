"""Per-symbol analysis cooldown + M5 displacement early wakeup.

Gold/silver need faster re-analysis in kill zones so M5 impulses are not
missed while Claude is still on the default ~4.5 minute throttle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Tuple

# Default Claude throttle (warm prompt-cache window).
DEFAULT_ANALYSIS_COOLDOWN_SECONDS = 270

# Precious metals: snappier inside KZ, still throttled outside.
METAL_KILL_ZONE_COOLDOWN_SECONDS = 90
METAL_OFF_ZONE_COOLDOWN_SECONDS = 180

# Even with displacement wakeup, never re-fire Claude faster than this.
DISPLACEMENT_WAKEUP_MIN_ELAPSED = 60

PRECIOUS_METALS = frozenset({"XAUUSD", "XAGUSD"})
CRYPTO_MARKERS = ("BTC", "ETH", "XRP", "SOL", "ADA", "DOGE")


def _is_precious_metal(symbol: str) -> bool:
    return (symbol or "").upper() in PRECIOUS_METALS


def _is_crypto(symbol: str) -> bool:
    sym = (symbol or "").upper()
    return any(m in sym for m in CRYPTO_MARKERS)


def resolve_analysis_cooldown_seconds(
    symbol: str,
    *,
    is_kill_zone: bool,
    base_seconds: int = DEFAULT_ANALYSIS_COOLDOWN_SECONDS,
) -> int:
    """Return Claude analysis cooldown for ``symbol`` given kill-zone state."""
    if _is_precious_metal(symbol):
        if is_kill_zone:
            return METAL_KILL_ZONE_COOLDOWN_SECONDS
        return METAL_OFF_ZONE_COOLDOWN_SECONDS
    return int(base_seconds)


def should_run_analysis(
    *,
    last_run: Optional[datetime],
    now: datetime,
    cooldown_seconds: int,
    displacement_wakeup: bool = False,
) -> Tuple[bool, str]:
    """Decide whether a symbol may call Claude now.

    Returns ``(run, reason)`` where reason is one of:
    ``ready``, ``cooldown``, ``m5_displacement_wakeup``.
    """
    if last_run is None:
        return True, "ready"

    elapsed = (now - last_run).total_seconds()
    if elapsed >= cooldown_seconds:
        return True, "ready"

    if (
        displacement_wakeup
        and elapsed >= DISPLACEMENT_WAKEUP_MIN_ELAPSED
    ):
        return True, "m5_displacement_wakeup"

    return False, "cooldown"


def has_recent_m5_displacement(
    analysis: Any,
    *,
    last_bar_index: int,
    max_age_bars: int = 3,
    min_atr_multiple: float = 1.5,
) -> bool:
    """True when a recent M5 displacement candle is still near the last bar."""
    if analysis is None:
        return False
    recent = getattr(analysis, "recent_displacements", None) or []
    if not recent:
        return False

    for disp in recent:
        idx = getattr(disp, "index", None)
        if idx is None:
            continue
        try:
            age = int(last_bar_index) - int(idx)
        except (TypeError, ValueError):
            continue
        if age < 0 or age > max_age_bars:
            continue
        atr_m = float(getattr(disp, "atr_multiple", 0) or 0)
        if atr_m >= min_atr_multiple:
            return True
        if bool(getattr(disp, "is_strong", False)):
            return True
    return False


def resolve_loss_cooldown_minutes(symbol: str) -> int:
    """Post-loss entry block duration. Metals/crypto 15m; forex 30m."""
    if _is_precious_metal(symbol) or _is_crypto(symbol):
        return 15
    return 30
