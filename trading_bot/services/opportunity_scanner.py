"""
Mechanical opportunity scanner — universe filter, scoring, hot-list TTL.

No Claude calls. Promotes top setups into a temporary hot list that the
trading cycle merges with TRADING_SYMBOLS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ..utils.logging import get_logger

logger = get_logger(__name__)

_METALS = frozenset({"XAUUSD", "XAGUSD", "GOLD", "SILVER"})


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce to a JSON-safe finite Python float (nan/inf → default)."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def _safe_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _as_utc(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def filter_scan_universe(
    symbols: Sequence[str],
    *,
    blocked_pairs: Set[str],
    crypto_symbols: Set[str],
    max_universe: int = 40,
) -> List[str]:
    """Keep metals, USD crypto, and *USD forex; drop blocked / BTC-quote pairs."""
    out: List[str] = []
    seen: Set[str] = set()
    blocked_upper = {b.upper() for b in blocked_pairs}
    crypto_upper = {c.upper() for c in crypto_symbols}

    for raw in symbols:
        sym = (raw or "").upper().strip()
        if not sym or sym in seen:
            continue
        if sym in blocked_upper:
            continue
        if sym.endswith("BTC") or sym.endswith("BIT"):
            continue
        if sym in _METALS or sym in crypto_upper:
            out.append(sym)
            seen.add(sym)
        elif len(sym) >= 6 and sym.endswith("USD") and sym.isalnum():
            # USD-quoted forex / crypto not already classified
            out.append(sym)
            seen.add(sym)
        if len(out) >= max_universe:
            break
    return out


def direction_zone_ok(direction: str, retrace_pct: Optional[float]) -> bool:
    """Buy discount / sell premium — hard filter for promotion."""
    if retrace_pct is None:
        return False
    d = (direction or "").lower()
    if d == "long":
        return retrace_pct <= 0.50
    if d == "short":
        return retrace_pct >= 0.50
    return False


def compute_opportunity_score(
    *,
    confluence_count: int,
    confidence: float,
    risk_reward: float,
    in_kill_zone: bool,
    is_crypto: bool,
) -> float:
    return (
        confluence_count * 0.25
        + confidence * 0.35
        + min(risk_reward, 4.0) / 4.0 * 0.25
        + (0.15 if (in_kill_zone or is_crypto) else 0.0)
    )


def htf_direction_aligned(direction: str, market_structure: Optional[str]) -> bool:
    """Require mechanical direction to match HTF trend (reject ranging / opposed)."""
    d = (direction or "").lower()
    ms = (market_structure or "").lower()
    if d == "long":
        return ms in ("bullish", "bull")
    if d == "short":
        return ms in ("bearish", "bear")
    return False


def is_promotable(
    *,
    has_setup: bool,
    zone_ok: bool,
    spread_ok: bool,
    risk_reward: float,
    min_rr: float,
    confidence: float = 0.0,
    min_confidence: float = 0.65,
    htf_aligned: bool = True,
) -> bool:
    if not has_setup or not zone_ok or not spread_ok:
        return False
    if not htf_aligned:
        return False
    if _safe_float(confidence) < min_confidence:
        return False
    return risk_reward >= min_rr


def pre_judge_zone_block_reason(
    *,
    order_type: str,
    direction: str,
    retrace_pct: Optional[float],
    htf_aligned: bool = False,
    has_displacement: bool = False,
) -> Optional[str]:
    """
    Shared hard zone check used before the trade judge.

    Mirrors validate_limit_zone for limits and direction_zone_ok for market.
    HTF+displacement continuation exempts market extreme-zone blocks.
    """
    if retrace_pct is None:
        return None
    ot = (order_type or "market").lower()
    d = (direction or "").lower()

    if ot == "buy_limit" and retrace_pct > 0.70:
        return f"buy_limit in premium zone ({retrace_pct:.0%})"
    if ot == "sell_limit" and retrace_pct < 0.30:
        return f"sell_limit in discount zone ({retrace_pct:.0%})"

    if ot in ("market", "buy", "sell", ""):
        from .setup_fingerprint import is_htf_displacement_continuation

        if is_htf_displacement_continuation(
            htf_aligned=htf_aligned, has_displacement=has_displacement
        ):
            return None
        if d == "long" and retrace_pct >= 0.618:
            return f"ZONE-GATE LONG from premium (retrace={retrace_pct:.0%})"
        if d == "short" and retrace_pct <= 0.382:
            return f"ZONE-GATE SHORT from discount (retrace={retrace_pct:.0%})"
    return None


@dataclass
class HotEntry:
    symbol: str
    score: float
    direction: str
    reason: str
    promoted_at: datetime
    expires_at: datetime


@dataclass
class HotList:
    max_size: int = 3
    ttl_minutes: int = 60
    _entries: Dict[str, HotEntry] = field(default_factory=dict)

    def promote(
        self,
        symbol: str,
        *,
        score: float,
        direction: str,
        reason: str,
        now: Optional[datetime] = None,
    ) -> None:
        now_utc = _as_utc(now)
        sym = symbol.upper()
        self._entries[sym] = HotEntry(
            symbol=sym,
            score=score,
            direction=direction,
            reason=reason,
            promoted_at=now_utc,
            expires_at=now_utc + timedelta(minutes=self.ttl_minutes),
        )
        self._trim(now_utc)

    def remove(self, symbol: str) -> bool:
        return self._entries.pop(symbol.upper(), None) is not None

    def _trim(self, now: datetime) -> None:
        # Drop expired
        expired = [s for s, e in self._entries.items() if e.expires_at <= now]
        for s in expired:
            del self._entries[s]
        if len(self._entries) <= self.max_size:
            return
        # Keep highest scores
        ranked = sorted(
            self._entries.values(), key=lambda e: e.score, reverse=True
        )
        keep = {e.symbol for e in ranked[: self.max_size]}
        for s in list(self._entries):
            if s not in keep:
                del self._entries[s]

    def active_symbols(self, now: Optional[datetime] = None) -> List[str]:
        now_utc = _as_utc(now)
        self._trim(now_utc)
        return [
            e.symbol
            for e in sorted(self._entries.values(), key=lambda x: x.score, reverse=True)
        ]

    def to_list(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now_utc = _as_utc(now)
        self._trim(now_utc)
        rows = []
        for e in sorted(self._entries.values(), key=lambda x: x.score, reverse=True):
            remaining = max(0, int((e.expires_at - now_utc).total_seconds() // 60))
            rows.append(
                {
                    "symbol": e.symbol,
                    "score": e.score,
                    "direction": e.direction,
                    "reason": e.reason,
                    "promoted_at": e.promoted_at.isoformat(),
                    "expires_at": e.expires_at.isoformat(),
                    "ttl_minutes_remaining": remaining,
                }
            )
        return rows

    def to_persist_dict(self) -> Dict[str, Any]:
        return {
            e.symbol: {
                "score": e.score,
                "direction": e.direction,
                "reason": e.reason,
                "promoted_at": e.promoted_at.isoformat(),
                "expires_at": e.expires_at.isoformat(),
            }
            for e in self._entries.values()
        }

    def load_persist_dict(self, data: Optional[Dict[str, Any]]) -> None:
        self._entries.clear()
        now = _as_utc()
        for sym, val in (data or {}).items():
            try:
                expires = datetime.fromisoformat(val["expires_at"])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= now:
                    continue
                promoted = datetime.fromisoformat(val["promoted_at"])
                if promoted.tzinfo is None:
                    promoted = promoted.replace(tzinfo=timezone.utc)
                self._entries[sym.upper()] = HotEntry(
                    symbol=sym.upper(),
                    score=float(val["score"]),
                    direction=str(val.get("direction", "")),
                    reason=str(val.get("reason", "")),
                    promoted_at=promoted,
                    expires_at=expires,
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._trim(now)


def merge_cycle_symbols(
    base_symbols: Sequence[str],
    hot: HotList,
    *,
    now: Optional[datetime] = None,
) -> List[str]:
    base = [s.upper() for s in base_symbols]
    seen = set(base)
    merged = list(base)
    for sym in hot.active_symbols(now):
        if sym not in seen:
            merged.append(sym)
            seen.add(sym)
    return merged


@dataclass
class Opportunity:
    symbol: str
    has_setup: bool
    direction: str = ""
    confluence_count: int = 0
    confluence_factors: List[str] = field(default_factory=list)
    confidence: float = 0.0
    risk_reward: float = 0.0
    zone_ok: bool = False
    retrace_pct: Optional[float] = None
    in_kill_zone: bool = False
    is_crypto: bool = False
    spread_ok: bool = True
    htf_aligned: bool = False
    market_structure: str = ""
    score: float = 0.0
    promotable: bool = False
    reason: str = ""
    session_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "has_setup": bool(self.has_setup),
            "direction": str(self.direction or ""),
            "confluence_count": int(self.confluence_count or 0),
            "confluence_factors": [str(f) for f in (self.confluence_factors or [])],
            "confidence": _safe_float(self.confidence),
            "risk_reward": _safe_float(self.risk_reward),
            "zone_ok": bool(self.zone_ok),
            "retrace_pct": _safe_optional_float(self.retrace_pct),
            "in_kill_zone": bool(self.in_kill_zone),
            "is_crypto": bool(self.is_crypto),
            "spread_ok": bool(self.spread_ok),
            "htf_aligned": bool(self.htf_aligned),
            "market_structure": str(self.market_structure or ""),
            "score": _safe_float(self.score),
            "promotable": bool(self.promotable),
            "reason": str(self.reason or ""),
            "session_name": str(self.session_name or ""),
        }


class OpportunityScanner:
    """Orchestrates mechanical scans and hot-list promotion."""

    def __init__(
        self,
        *,
        strategy: Any = None,
        data_fetcher: Any = None,
        mt5_client: Any = None,
        kill_zone_checker: Any = None,
        blocked_pairs: Optional[Iterable[str]] = None,
        crypto_symbols: Optional[Iterable[str]] = None,
        max_universe: int = 40,
        hot_list_size: int = 3,
        hot_ttl_minutes: int = 60,
        min_rr: float = 1.5,
        min_confidence: float = 0.65,
        execution_tf: str = "M15",
        execution_tf_candles: int = 200,
        htf: str = "H4",
        htf_candles: int = 100,
    ):
        self.strategy = strategy
        self.data_fetcher = data_fetcher
        self.mt5_client = mt5_client
        self.kill_zone_checker = kill_zone_checker
        self.blocked_pairs = set(blocked_pairs or [])
        self.crypto_symbols = set(crypto_symbols or [])
        self.max_universe = max_universe
        self.min_rr = min_rr
        self.min_confidence = min_confidence
        self.execution_tf = execution_tf
        self.execution_tf_candles = execution_tf_candles
        self.htf = htf
        self.htf_candles = htf_candles
        self.hot = HotList(max_size=hot_list_size, ttl_minutes=hot_ttl_minutes)
        self.last_results: List[Opportunity] = []
        self.last_scan_at: Optional[datetime] = None
        self.scan_in_progress: bool = False

    def build_universe(self, base_symbols: Sequence[str], market_watch: Sequence[str]) -> List[str]:
        combined = list(market_watch) + list(base_symbols)
        return filter_scan_universe(
            combined,
            blocked_pairs=self.blocked_pairs,
            crypto_symbols=self.crypto_symbols,
            max_universe=self.max_universe,
        )

    def score_setup(
        self,
        symbol: str,
        setup: Optional[dict],
        *,
        retrace_pct: Optional[float],
        in_kill_zone: bool,
        session_name: str,
        spread_ok: bool = True,
    ) -> Opportunity:
        is_crypto = symbol.upper() in {c.upper() for c in self.crypto_symbols}
        if not setup:
            return Opportunity(
                symbol=symbol.upper(),
                has_setup=False,
                in_kill_zone=in_kill_zone,
                is_crypto=is_crypto,
                spread_ok=spread_ok,
                session_name=session_name,
                reason="no mechanical setup",
            )

        direction = str(setup.get("direction") or "").lower()
        conf = _safe_float(setup.get("confidence"))
        rr = _safe_float(setup.get("risk_reward"))
        retrace_pct = _safe_optional_float(retrace_pct)
        market_structure = str(setup.get("market_structure") or "")
        aligned = htf_direction_aligned(direction, market_structure)
        confluence = setup.get("confluence") or {}
        factors = []
        count = 0
        if isinstance(confluence, dict):
            for key, label in (
                ("fvg", "FVG"),
                ("order_block", "OB"),
                ("liquidity_swept", "Sweep"),
                ("kill_zone", "KZ"),
            ):
                if confluence.get(key):
                    factors.append(label)
                    count += 1
        else:
            count = int(setup.get("confluence_count") or 0)

        zone_ok = direction_zone_ok(direction, retrace_pct)
        score = compute_opportunity_score(
            confluence_count=count,
            confidence=conf,
            risk_reward=rr,
            in_kill_zone=in_kill_zone,
            is_crypto=is_crypto,
        )
        # Prefer HTF-aligned setups in ranking
        if aligned:
            score += 0.10
        promotable = is_promotable(
            has_setup=True,
            zone_ok=zone_ok,
            spread_ok=spread_ok,
            risk_reward=rr,
            min_rr=self.min_rr,
            confidence=conf,
            min_confidence=self.min_confidence,
            htf_aligned=aligned,
        )
        reason = "promotable" if promotable else "filtered"
        if not zone_ok:
            reason = "zone_misaligned"
        elif not spread_ok:
            reason = "spread_blocked"
        elif not aligned:
            reason = f"htf_misaligned ({market_structure or 'unknown'} vs {direction})"
        elif conf < self.min_confidence:
            reason = f"conf_below_floor ({conf:.2f}<{self.min_confidence})"
        elif rr < self.min_rr:
            reason = f"rr_below_floor ({rr:.2f}<{self.min_rr})"

        return Opportunity(
            symbol=symbol.upper(),
            has_setup=True,
            direction=direction,
            confluence_count=count,
            confluence_factors=factors,
            confidence=conf,
            risk_reward=rr,
            zone_ok=zone_ok,
            retrace_pct=retrace_pct,
            in_kill_zone=in_kill_zone,
            is_crypto=is_crypto,
            spread_ok=spread_ok,
            htf_aligned=aligned,
            market_structure=market_structure,
            score=score,
            promotable=promotable,
            reason=reason,
            session_name=session_name,
        )

    def apply_promotions(
        self,
        opportunities: Sequence[Opportunity],
        *,
        base_symbols: Sequence[str],
        now: Optional[datetime] = None,
    ) -> List[str]:
        """Promote top promotable symbols not already in base list."""
        now_utc = _as_utc(now)
        base = {s.upper() for s in base_symbols}
        candidates = [
            o for o in opportunities if o.promotable and o.symbol not in base
        ]
        candidates.sort(key=lambda o: o.score, reverse=True)
        promoted = []
        for opp in candidates[: self.hot.max_size]:
            self.hot.promote(
                opp.symbol,
                score=opp.score,
                direction=opp.direction,
                reason=opp.reason,
                now=now_utc,
            )
            promoted.append(opp.symbol)
        # Refresh TTL for still-ranked hot symbols already in list
        for opp in candidates:
            if opp.symbol in self.hot._entries and opp.symbol not in promoted:
                if opp.symbol in self.hot.active_symbols(now_utc):
                    self.hot.promote(
                        opp.symbol,
                        score=opp.score,
                        direction=opp.direction,
                        reason=opp.reason,
                        now=now_utc,
                    )
        return promoted

    async def scan_once(
        self,
        *,
        base_symbols: Sequence[str],
    ) -> List[Opportunity]:
        """Fetch MW + base universe, score mechanically, update hot list."""
        if self.scan_in_progress:
            logger.info("[SCAN] skipped — scan already in progress")
            return list(self.last_results)
        self.scan_in_progress = True
        try:
            return await self._scan_once_impl(base_symbols=base_symbols)
        finally:
            self.scan_in_progress = False

    async def _scan_once_impl(
        self,
        *,
        base_symbols: Sequence[str],
    ) -> List[Opportunity]:
        from ..utils.market_hours import is_market_open
        from ..mt5.ohlcv_quality import validate_ohlcv

        mw_names: List[str] = []
        if self.mt5_client:
            try:
                mw = await self.mt5_client.get_market_watch_symbols()
                mw_names = [s.get("name", "") for s in (mw or []) if s.get("name")]
            except Exception as e:
                logger.warning(f"[SCAN] Market Watch fetch failed: {e}")

        universe = self.build_universe(base_symbols, mw_names)
        session_name = "Unknown"
        in_kz = False
        if self.kill_zone_checker:
            try:
                sess = self.kill_zone_checker.get_current_session()
                session_name = getattr(sess, "session_name", "Unknown") or "Unknown"
                in_kz = bool(getattr(sess, "is_kill_zone", False))
            except Exception:
                pass

        results: List[Opportunity] = []
        for symbol in universe:
            open_ok, open_reason = is_market_open(symbol)
            if not open_ok:
                results.append(
                    Opportunity(
                        symbol=symbol,
                        has_setup=False,
                        reason=f"market_closed: {open_reason}",
                        session_name=session_name,
                        is_crypto=symbol in self.crypto_symbols,
                    )
                )
                continue

            if not self.data_fetcher or not self.strategy:
                results.append(
                    Opportunity(
                        symbol=symbol,
                        has_setup=False,
                        reason="scanner_not_wired",
                        session_name=session_name,
                    )
                )
                continue

            try:
                ltf = await self.data_fetcher.get_ohlcv(
                    symbol=symbol,
                    timeframe=self.execution_tf,
                    count=self.execution_tf_candles,
                    use_cache=False,
                )
                htf = await self.data_fetcher.get_ohlcv(
                    symbol=symbol,
                    timeframe=self.htf,
                    count=self.htf_candles,
                    use_cache=False,
                )
            except Exception as e:
                results.append(
                    Opportunity(
                        symbol=symbol,
                        has_setup=False,
                        reason=f"fetch_error: {e}",
                        session_name=session_name,
                    )
                )
                continue

            quality = validate_ohlcv(
                ltf,
                symbol=symbol,
                timeframe=self.execution_tf,
                expected_count=self.execution_tf_candles,
            )
            if not quality.valid:
                results.append(
                    Opportunity(
                        symbol=symbol,
                        has_setup=False,
                        reason=f"bad_data: {quality.reason}",
                        session_name=session_name,
                    )
                )
                continue

            spread_ok = True
            try:
                from .spread_policy import evaluate_spread_state

                mid = float(ltf["close"].iloc[-1]) if ltf is not None and len(ltf) else 0.0
                spread_val = None
                if self.mt5_client and getattr(self.mt5_client, "is_connected", False):
                    info = await self.mt5_client.get_symbol_info(symbol)
                    if info and getattr(info, "ask", None) and getattr(info, "bid", None):
                        spread_val = float(info.ask) - float(info.bid)
                st = evaluate_spread_state(
                    symbol,
                    spread=spread_val,
                    mid_price=mid,
                    unavailable=spread_val is None,
                )
                spread_ok = bool(st.allows_trading)
            except Exception:
                spread_ok = True  # fail open on spread probe in scan

            retrace_pct = None
            try:
                from ..analysis.premium_discount import PremiumDiscountAnalyzer

                pda = PremiumDiscountAnalyzer()
                pd_res = pda.analyze(ltf) if hasattr(pda, "analyze") else None
                if pd_res is not None:
                    retrace_pct = getattr(pd_res, "retracement_percent", None)
                    if retrace_pct is None and isinstance(pd_res, dict):
                        retrace_pct = pd_res.get("retracement_percent")
            except Exception:
                retrace_pct = None

            setup_dict = None
            try:
                ltf.attrs["symbol"] = symbol
                setup = self.strategy.analyze(
                    htf_data=htf,
                    ltf_data=ltf,
                    symbol=symbol,
                    htf_name=self.htf,
                    ltf_name=self.execution_tf,
                    require_tradeable_session=False,
                )
                if setup is not None:
                    setup_dict = setup.to_dict() if hasattr(setup, "to_dict") else dict(setup)
            except Exception as e:
                logger.debug(f"[SCAN] mechanical analyze failed {symbol}: {e}")

            opp = self.score_setup(
                symbol,
                setup_dict,
                retrace_pct=retrace_pct,
                in_kill_zone=in_kz,
                session_name=session_name,
                spread_ok=spread_ok,
            )
            results.append(opp)

        results.sort(key=lambda o: o.score, reverse=True)
        self.last_results = results
        self.last_scan_at = _as_utc()
        promoted = self.apply_promotions(results, base_symbols=base_symbols)
        logger.info(
            f"[SCAN] universe={len(universe)} scored={len(results)} "
            f"promotable={sum(1 for r in results if r.promotable)} "
            f"promoted={promoted}"
        )
        print(
            f"[SCAN] universe={len(universe)} scored={len(results)} promoted={promoted}",
            flush=True,
        )
        return results
