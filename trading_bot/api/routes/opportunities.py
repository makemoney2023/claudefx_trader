"""Opportunity scanner API — mechanical rankings and hot list."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import RequireAuth

router = APIRouter(prefix="/opportunities", tags=["Opportunities"])

_scanner = None
_bot_ref = None


def set_opportunity_scanner(scanner, bot=None):
    global _scanner, _bot_ref
    _scanner = scanner
    _bot_ref = bot


def get_opportunity_scanner():
    return _scanner


class OpportunitiesResponse(BaseModel):
    enabled: bool
    last_scan_at: Optional[str] = None
    results: List[Dict[str, Any]] = []
    total: int = 0


class HotListResponse(BaseModel):
    hot: List[Dict[str, Any]] = []
    total: int = 0


@router.get("", response_model=OpportunitiesResponse)
async def list_opportunities():
    from ...config import settings

    enabled = bool(settings.trading.opportunity_scanner_enabled)
    if not _scanner:
        return OpportunitiesResponse(enabled=enabled, results=[], total=0)
    results = [o.to_dict() for o in (_scanner.last_results or [])]
    return OpportunitiesResponse(
        enabled=enabled,
        last_scan_at=_scanner.last_scan_at.isoformat() if _scanner.last_scan_at else None,
        results=results,
        total=len(results),
    )


@router.get("/hot", response_model=HotListResponse)
async def list_hot():
    if not _scanner:
        return HotListResponse(hot=[], total=0)
    hot = _scanner.hot.to_list()
    return HotListResponse(hot=hot, total=len(hot))


@router.post("/scan", dependencies=[Depends(RequireAuth())])
async def force_scan():
    from ...config import settings

    if not _scanner:
        raise HTTPException(503, "Opportunity scanner not initialized")
    results = await _scanner.scan_once(base_symbols=settings.trading.symbols)
    return {
        "success": True,
        "total": len(results),
        "promotable": sum(1 for r in results if r.promotable),
        "hot": _scanner.hot.to_list(),
        "results": [r.to_dict() for r in results[:20]],
    }


@router.post("/promote/{symbol}", dependencies=[Depends(RequireAuth())])
async def promote_symbol(symbol: str):
    if not _scanner:
        raise HTTPException(503, "Opportunity scanner not initialized")
    sym = symbol.upper().strip()
    match = next((o for o in _scanner.last_results if o.symbol == sym), None)
    score = match.score if match else 0.5
    direction = match.direction if match else ""
    reason = match.reason if match else "manual_promote"
    _scanner.hot.promote(sym, score=score, direction=direction, reason=reason)
    return {"success": True, "symbol": sym, "hot": _scanner.hot.to_list()}


@router.delete("/hot/{symbol}", dependencies=[Depends(RequireAuth())])
async def remove_hot(symbol: str):
    if not _scanner:
        raise HTTPException(503, "Opportunity scanner not initialized")
    removed = _scanner.hot.remove(symbol)
    if not removed:
        raise HTTPException(404, f"{symbol} not in hot list")
    return {"success": True, "hot": _scanner.hot.to_list()}
