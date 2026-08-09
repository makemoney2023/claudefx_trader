"""Stable ICT setup fingerprints for expectancy segmentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _as_dictish(obj: Any) -> Any:
    return obj


def _get_sweeps(liquidity: Any) -> List[Any]:
    if liquidity is None:
        return []
    if isinstance(liquidity, dict):
        return list(liquidity.get("recent_sweeps") or [])
    return list(getattr(liquidity, "recent_sweeps", None) or [])


def _sweep_type(sweep: Any) -> str:
    if isinstance(sweep, dict):
        return str(sweep.get("type") or "").lower()
    pool = getattr(sweep, "liquidity_pool", None)
    if pool is not None:
        t = getattr(pool, "type", None)
        if t is not None:
            return str(getattr(t, "value", t)).lower()
    return str(getattr(sweep, "type", "") or "").lower()


def _sweep_index(sweep: Any) -> Optional[int]:
    if isinstance(sweep, dict):
        idx = sweep.get("sweep_index")
    else:
        idx = getattr(sweep, "sweep_index", None)
    if idx is None:
        return None
    try:
        return int(idx)
    except (TypeError, ValueError):
        return None


# M15 bars: ~3 hours. Stale sweeps must not unlock counter-HTF lean fades.
LEAN_SWEEP_MAX_AGE_BARS = 12


def _sweep_reversal_ok(sweep: Any) -> bool:
    """Accept sweep unless reversal_detected is explicitly False."""
    if isinstance(sweep, dict):
        if "reversal_detected" in sweep and not sweep.get("reversal_detected"):
            return False
        return True
    if hasattr(sweep, "reversal_detected") and sweep.reversal_detected is False:
        return False
    return True


def _is_ssl_sweep_type(st: str) -> bool:
    """Long-fade liquidity: SSL / equal lows (live enum values, not 'ssl' substring)."""
    s = (st or "").lower()
    return s in (
        "ssl",
        "sell_side_liquidity",
        "equal_lows",
        "eql",
    ) or "sell_side" in s or "equal_low" in s


def _is_bsl_sweep_type(st: str) -> bool:
    """Short-fade liquidity: BSL / equal highs."""
    s = (st or "").lower()
    return s in (
        "bsl",
        "buy_side_liquidity",
        "equal_highs",
        "eqh",
    ) or "buy_side" in s or "equal_high" in s


def has_directional_sweep(
    direction: str,
    liquidity: Any,
    *,
    max_age_bars: Optional[int] = None,
    reference_bar_index: Optional[int] = None,
) -> bool:
    """True when a recent sweep aligns with trade direction.

    Longs want SSL / equal lows swept; shorts want BSL / equal highs swept.
    Matches live ``LiquidityType`` values (``sell_side_liquidity`` etc.) used by
    ``LiquiditySweep.to_dict`` and ``ICTStrategy._check_liquidity_sweep``.

    When ``max_age_bars`` is set, only the last 3 sweeps are considered and each
    candidate must be within ``max_age_bars`` of ``reference_bar_index`` (or of
    the newest sweep index in the liquidity list when reference is omitted).
    """
    d = (direction or "").lower()
    matcher = _is_ssl_sweep_type if d == "long" else _is_bsl_sweep_type
    sweeps = _get_sweeps(liquidity)
    if max_age_bars is not None:
        sweeps = sweeps[-3:]
        if reference_bar_index is None:
            idxs = [i for i in (_sweep_index(s) for s in _get_sweeps(liquidity)) if i is not None]
            reference_bar_index = max(idxs) if idxs else None
    for sweep in sweeps:
        if not matcher(_sweep_type(sweep)):
            continue
        if not _sweep_reversal_ok(sweep):
            continue
        if max_age_bars is not None and reference_bar_index is not None:
            idx = _sweep_index(sweep)
            if idx is not None and (reference_bar_index - idx) > max_age_bars:
                continue
        return True
    return False


def lean_continues_outside_kill_zone() -> bool:
    """Main cycle may analyze non-crypto outside KZ when lean flag is active."""
    return is_liquidity_reversal_lean_active()


def _lean_reference_bar_index(analysis_results: Optional[Dict[str, Any]]) -> Optional[int]:
    ar = analysis_results or {}
    if ar.get("reference_bar_index") is not None:
        try:
            return int(ar["reference_bar_index"])
        except (TypeError, ValueError):
            pass
    raw = ar.get("_raw_bar_count")
    if raw:
        try:
            n = int(raw)
            return max(0, n - 1)
        except (TypeError, ValueError):
            return None
    return None


def _structure_breaks(ms: Any) -> Sequence[Any]:
    if ms is None:
        return []
    if isinstance(ms, dict):
        return list(ms.get("structure_breaks") or ms.get("recent_breaks") or [])
    return list(getattr(ms, "structure_breaks", None) or [])


def _break_value(br: Any) -> str:
    if isinstance(br, dict):
        return str(br.get("type") or br.get("structure_type") or "").lower()
    t = getattr(br, "type", None)
    if t is None:
        return ""
    return str(getattr(t, "value", t)).lower()


def _break_bullish(br: Any) -> bool:
    if hasattr(br, "is_bullish"):
        return bool(br.is_bullish)
    v = _break_value(br)
    return "bullish" in v


def has_mss_or_choch(direction: str, market_structure: Any) -> bool:
    want_bull = (direction or "").lower() == "long"
    for br in _structure_breaks(market_structure):
        v = _break_value(br)
        is_shift = any(k in v for k in ("mss", "choch", "bos"))
        if not is_shift:
            continue
        bull = _break_bullish(br) or "bullish" in v
        bear = (hasattr(br, "is_bearish") and br.is_bearish) or "bearish" in v
        if want_bull and bull:
            return True
        if not want_bull and bear:
            return True
    return False


def has_displacement(
    analysis_results: Dict[str, Any],
    direction: str = "",
) -> bool:
    """True when a directional impulse exists for the trade.

    Accepts ``distribution_confirmed`` (legacy) OR a recent displacement candle
    in the trade direction (``last_bullish`` / ``last_bearish`` / recent list).
    Also accepts ``fresh_displacement_direction`` stamped by the metals M5
    continuation path (execution-TF displacement often lags the impulse).
    Direction-agnostic callers still succeed on any confirmed/recent impulse.
    """
    d = (direction or "").lower()
    want = "bullish" if d == "long" else ("bearish" if d == "short" else "")
    fresh = (analysis_results.get("fresh_displacement_direction") or "").lower()
    if want and fresh == want:
        return True

    disp = analysis_results.get("displacement")
    if disp is None:
        return False

    if isinstance(disp, dict):
        if disp.get("distribution_confirmed"):
            dist_dir = (disp.get("distribution_direction") or "").lower()
            if not want or not dist_dir or dist_dir == want:
                return True
        last_bull = disp.get("last_bullish")
        last_bear = disp.get("last_bearish")
        if want == "bullish" and last_bull:
            return True
        if want == "bearish" and last_bear:
            return True
        if not want and (last_bull or last_bear):
            return True
        recent = disp.get("recent_displacements") or []
        if want:
            for item in recent:
                item_dir = (
                    item.get("direction")
                    if isinstance(item, dict)
                    else getattr(item, "direction", "")
                )
                if (item_dir or "").lower() == want:
                    return True
            return False
        return bool(recent)

    if getattr(disp, "distribution_confirmed", False):
        dist_dir = (getattr(disp, "distribution_direction", None) or "").lower()
        if not want or not dist_dir or dist_dir == want:
            return True
    last_bull = getattr(disp, "last_bullish", None)
    last_bear = getattr(disp, "last_bearish", None)
    if want == "bullish" and last_bull:
        return True
    if want == "bearish" and last_bear:
        return True
    if not want and (last_bull or last_bear):
        return True
    recent = getattr(disp, "recent_displacements", None) or []
    if want:
        for item in recent:
            item_dir = (
                item.get("direction")
                if isinstance(item, dict)
                else getattr(item, "direction", "")
            )
            if (item_dir or "").lower() == want:
                return True
        return False
    return bool(recent)


def htf_aligned(direction: str, d1_bias: str, h4_bias: str) -> bool:
    d = (direction or "").lower()
    aligned = "bullish" if d == "long" else "bearish"
    return (d1_bias or "").lower() == aligned and (h4_bias or "").lower() == aligned


def is_htf_displacement_continuation(
    *,
    htf_aligned: bool,
    has_displacement: bool,
) -> bool:
    """HTF-aligned impulse: same predicate as zone-gate continuation bypass."""
    return bool(htf_aligned) and bool(has_displacement)


def is_liquidity_reversal_lean_active() -> bool:
    """True when TRADING_LIQUIDITY_REVERSAL_LEAN_MODE=active."""
    try:
        from ..config import settings

        mode = getattr(settings.trading, "liquidity_reversal_lean_mode", "off") or "off"
    except Exception:
        return False
    return str(mode).lower() == "active"


def is_lean_sweep_fade(
    direction: str,
    analysis_results: Optional[Dict[str, Any]],
    *,
    d1_bias: str = "",
    h4_bias: str = "",
    reference_bar_index: Optional[int] = None,
) -> bool:
    """Lean-eligible sweep fade (not HTF+displacement continuation)."""
    if not is_liquidity_reversal_lean_active():
        return False
    ar = analysis_results or {}
    ref = (
        reference_bar_index
        if reference_bar_index is not None
        else _lean_reference_bar_index(ar)
    )
    if not has_directional_sweep(
        direction,
        ar.get("liquidity"),
        max_age_bars=LEAN_SWEEP_MAX_AGE_BARS,
        reference_bar_index=ref,
    ):
        return False
    aligned = htf_aligned(direction, d1_bias, h4_bias) if (d1_bias or h4_bias) else False
    # When biases omitted, infer alignment from ar if present
    if not (d1_bias or h4_bias):
        d1_bias = str(ar.get("d1_bias") or "")
        h4_bias = str(ar.get("h4_bias") or "")
        aligned = htf_aligned(direction, d1_bias, h4_bias)
    disp = has_displacement(ar, direction=direction)
    if is_htf_displacement_continuation(htf_aligned=aligned, has_displacement=disp):
        return False
    return True


def classify_setup_family(
    *,
    direction: str,
    order_type: str,
    d1_bias: str,
    h4_bias: str,
    sweep: bool,
    mss: bool,
    displacement: bool,
    lean: Optional[bool] = None,
) -> str:
    ot = (order_type or "market").lower()
    lean_on = is_liquidity_reversal_lean_active() if lean is None else bool(lean)
    aligned = htf_aligned(direction, d1_bias, h4_bias)
    # Under lean: directional sweep that is not HTF+disp continuation → fade family
    # (includes limits so demoted fades keep liquidity_reversal ICT, not passive).
    if lean_on and sweep and not (aligned and displacement):
        return "liquidity_reversal"
    if ot in ("buy_limit", "sell_limit", "buy_stop", "sell_stop") and "limit" in ot:
        return "passive_retracement"
    if ot.endswith("_limit"):
        return "passive_retracement"
    if sweep and (mss or displacement) and not aligned:
        return "liquidity_reversal"
    if aligned and (mss or displacement):
        return "continuation"
    if sweep and mss:
        return "liquidity_reversal"
    if ot != "market":
        return "passive_retracement"
    return "continuation" if aligned else "liquidity_reversal"


@dataclass(frozen=True)
class SetupFingerprint:
    family: str
    tags: Tuple[str, ...]
    key: str
    has_sweep: bool = False
    has_mss: bool = False
    has_displacement: bool = False
    htf_aligned: bool = False
    zone_valid: bool = True
    direction: str = ""
    session: str = ""
    regime: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "tags": list(self.tags),
            "key": self.key,
            "has_sweep": self.has_sweep,
            "has_mss": self.has_mss,
            "has_displacement": self.has_displacement,
            "htf_aligned": self.htf_aligned,
            "zone_valid": self.zone_valid,
            "direction": self.direction,
            "session": self.session,
            "regime": self.regime,
        }


def build_setup_fingerprint(
    *,
    direction: str,
    order_type: str = "market",
    session: str = "",
    regime: str = "",
    d1_bias: str = "",
    h4_bias: str = "",
    analysis_results: Optional[Dict[str, Any]] = None,
    zone_valid: bool = True,
) -> SetupFingerprint:
    ar = analysis_results or {}
    lean_on = is_liquidity_reversal_lean_active()
    # Under lean, fingerprint sweep must match gate age (≤12 bars) so ICT /
    # sb_lean tags agree with is_lean_sweep_fade eligibility.
    if lean_on:
        sweep = has_directional_sweep(
            direction,
            ar.get("liquidity"),
            max_age_bars=LEAN_SWEEP_MAX_AGE_BARS,
            reference_bar_index=_lean_reference_bar_index(ar),
        )
    else:
        sweep = has_directional_sweep(direction, ar.get("liquidity"))
    mss = has_mss_or_choch(direction, ar.get("market_structure"))
    disp = has_displacement(ar, direction=direction)
    aligned = htf_aligned(direction, d1_bias, h4_bias)

    # Feature tags for persistence / analytics
    tags: List[str] = []
    if sweep:
        tags.append("sweep")
    if mss:
        # Prefer finer label when available
        ms = ar.get("market_structure")
        labeled = False
        for br in _structure_breaks(ms):
            v = _break_value(br)
            if "mss" in v or "choch" in v:
                tags.append("mss")
                labeled = True
                break
            if "bos" in v:
                tags.append("bos")
                labeled = True
                break
        if not labeled:
            tags.append("mss")
    if disp:
        tags.append("disp")
    if aligned:
        tags.append("htf")
    fvg = ar.get("fvg")
    if fvg:
        if direction == "long" and (
            getattr(fvg, "bullish_fvgs", None)
            or (isinstance(fvg, dict) and fvg.get("bullish_fvgs"))
        ):
            tags.append("fvg")
        elif direction == "short" and (
            getattr(fvg, "bearish_fvgs", None)
            or (isinstance(fvg, dict) and fvg.get("bearish_fvgs"))
        ):
            tags.append("fvg")
    ob = ar.get("order_blocks")
    if ob:
        if direction == "long" and (
            getattr(ob, "bullish_obs", None)
            or (isinstance(ob, dict) and ob.get("bullish_obs"))
        ):
            tags.append("ob")
        elif direction == "short" and (
            getattr(ob, "bearish_obs", None)
            or (isinstance(ob, dict) and ob.get("bearish_obs"))
        ):
            tags.append("ob")
    if zone_valid:
        tags.append("zone")

    family = classify_setup_family(
        direction=direction,
        order_type=order_type,
        d1_bias=d1_bias,
        h4_bias=h4_bias,
        sweep=sweep,
        mss=mss,
        displacement=disp,
        lean=lean_on,
    )
    if lean_on and family == "liquidity_reversal" and sweep:
        tags.append("sb_lean")
    sess = (session or "unknown").lower().replace(" ", "_")[:16]
    reg = (regime or "unknown").lower().replace(" ", "_")[:16]
    tag_part = "+".join(tags) if tags else "none"
    key = f"{family}|{(direction or '?')[:5]}|{(order_type or 'market')[:10]}|{sess}|{reg}|{tag_part}"
    if len(key) > 120:
        key = key[:120]

    return SetupFingerprint(
        family=family,
        tags=tuple(tags),
        key=key,
        has_sweep=sweep,
        has_mss=mss,
        has_displacement=disp,
        htf_aligned=aligned,
        zone_valid=zone_valid,
        direction=(direction or "").lower(),
        session=sess,
        regime=reg,
    )
