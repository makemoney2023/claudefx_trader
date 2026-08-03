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


def has_directional_sweep(direction: str, liquidity: Any) -> bool:
    """True when a recent sweep aligns with trade direction.

    Longs want SSL (sell-side) swept; shorts want BSL (buy-side) swept.
    """
    want = "ssl" if (direction or "").lower() == "long" else "bsl"
    for sweep in _get_sweeps(liquidity):
        st = _sweep_type(sweep)
        if want in st or st == want:
            return True
    return False


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
    Direction-agnostic callers still succeed on any confirmed/recent impulse.
    """
    disp = analysis_results.get("displacement")
    if disp is None:
        return False

    d = (direction or "").lower()
    want = "bullish" if d == "long" else ("bearish" if d == "short" else "")

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


def classify_setup_family(
    *,
    direction: str,
    order_type: str,
    d1_bias: str,
    h4_bias: str,
    sweep: bool,
    mss: bool,
    displacement: bool,
) -> str:
    ot = (order_type or "market").lower()
    if ot in ("buy_limit", "sell_limit", "buy_stop", "sell_stop") and "limit" in ot:
        return "passive_retracement"
    if ot.endswith("_limit"):
        return "passive_retracement"
    if sweep and (mss or displacement) and not htf_aligned(direction, d1_bias, h4_bias):
        return "liquidity_reversal"
    if htf_aligned(direction, d1_bias, h4_bias) and (mss or displacement):
        return "continuation"
    if sweep and mss:
        return "liquidity_reversal"
    if ot != "market":
        return "passive_retracement"
    return "continuation" if htf_aligned(direction, d1_bias, h4_bias) else "liquidity_reversal"


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
    )
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
