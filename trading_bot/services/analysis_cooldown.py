"""Per-symbol analysis cooldown + M5 displacement early wakeup.

Gold/silver need faster re-analysis in kill zones so M5 impulses are not
missed while Claude is still on the default ~4.5 minute throttle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Tuple

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


def last_closed_bar_index(raw_bar_count: int) -> int:
    """Index of the last fully closed bar on a raw MT5 OHLCV frame.

    ``DisplacementDetector.detect`` strips the forming candle via
    ``exclude_forming_candle``, so displacement candle indexes are relative
    to the stripped frame. Callers that still hold the raw frame must pass
    ``len(df) - 2`` (clamped) — never ``len(df) - 1``.
    """
    try:
        n = int(raw_bar_count)
    except (TypeError, ValueError):
        return 0
    if n <= 1:
        return 0
    return n - 2


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


def _iter_recent_qualifying_displacements(
    analysis: Any,
    *,
    last_bar_index: int,
    max_age_bars: int = 3,
    min_atr_multiple: float = 1.5,
) -> List[Any]:
    """Return recent displacements that meet strength/age thresholds."""
    if analysis is None:
        return []
    recent = getattr(analysis, "recent_displacements", None) or []
    if not recent:
        return []

    qualifying: List[Any] = []
    for disp in recent:
        idx = getattr(disp, "index", None)
        if idx is None and isinstance(disp, dict):
            idx = disp.get("index")
        if idx is None:
            continue
        try:
            age = int(last_bar_index) - int(idx)
        except (TypeError, ValueError):
            continue
        if age < 0 or age > max_age_bars:
            continue
        if isinstance(disp, dict):
            atr_m = float(disp.get("atr_multiple", 0) or 0)
            is_strong = bool(disp.get("is_strong", False))
        else:
            atr_m = float(getattr(disp, "atr_multiple", 0) or 0)
            is_strong = bool(getattr(disp, "is_strong", False))
        if atr_m >= min_atr_multiple or is_strong:
            qualifying.append(disp)
    return qualifying


def has_recent_m5_displacement(
    analysis: Any,
    *,
    last_bar_index: int,
    max_age_bars: int = 3,
    min_atr_multiple: float = 1.5,
) -> bool:
    """True when a recent M5 displacement candle is still near the last bar."""
    return bool(
        _iter_recent_qualifying_displacements(
            analysis,
            last_bar_index=last_bar_index,
            max_age_bars=max_age_bars,
            min_atr_multiple=min_atr_multiple,
        )
    )


def _disp_index(disp: Any) -> int:
    if isinstance(disp, dict):
        return int(disp.get("index") or -1)
    return int(getattr(disp, "index", -1) or -1)


def recent_m5_displacement_candle(
    analysis: Any,
    *,
    last_bar_index: int,
    max_age_bars: int = 3,
    min_atr_multiple: float = 1.5,
) -> Optional[Any]:
    """Return the newest qualifying displacement candle (object or dict)."""
    qualifying = _iter_recent_qualifying_displacements(
        analysis,
        last_bar_index=last_bar_index,
        max_age_bars=max_age_bars,
        min_atr_multiple=min_atr_multiple,
    )
    if not qualifying:
        return None
    return max(qualifying, key=_disp_index)


def recent_m5_displacement_direction(
    analysis: Any,
    *,
    last_bar_index: int,
    max_age_bars: int = 3,
    min_atr_multiple: float = 1.5,
) -> Optional[str]:
    """Return ``bullish``/``bearish`` for the newest qualifying M5 displacement."""
    newest = recent_m5_displacement_candle(
        analysis,
        last_bar_index=last_bar_index,
        max_age_bars=max_age_bars,
        min_atr_multiple=min_atr_multiple,
    )
    if newest is None:
        return None
    if isinstance(newest, dict):
        direction = (newest.get("direction") or "").lower()
    else:
        direction = (getattr(newest, "direction", None) or "").lower()
    if direction in ("bullish", "bearish"):
        return direction
    return None


def should_check_metal_displacement_wakeup(
    symbol: str,
    *,
    on_cooldown: bool,
) -> bool:
    """True when metals should scan M5 displacement to break cooldown.

    Kill-zone and session independent — London/NY/Asian/off-hours impulses
    all wake XAU/XAG the same way.
    """
    return bool(on_cooldown) and _is_precious_metal(symbol)


def resolve_loss_cooldown_minutes(symbol: str) -> int:
    """Post-loss entry block duration. Metals/crypto 15m; forex 30m."""
    if _is_precious_metal(symbol) or _is_crypto(symbol):
        return 15
    return 30
