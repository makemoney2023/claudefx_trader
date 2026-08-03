"""Opportunity scanner API — mechanical rankings and hot list."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import RequireAuth

router = APIRouter(prefix="/opportunities", tags=["Opportunities"])

_scanner = None
_bot_ref = None
_bg_scan_task: Optional[asyncio.Task] = None


def set_opportunity_scanner(scanner, bot=None):
    global _scanner, _bot_ref
    _scanner = scanner
    _bot_ref = bot


def get_opportunity_scanner():
    return _scanner


class OpportunitiesResponse(BaseModel):
    enabled: bool
    last_scan_at: Optional[str] = None
    scanning: bool = False
    results: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class HotListResponse(BaseModel):
    hot: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


def _safe_results(scanner) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for o in getattr(scanner, "last_results", None) or []:
        try:
            rows.append(o.to_dict())
        except Exception:
            rows.append(
                {
                    "symbol": str(getattr(o, "symbol", "?")),
                    "has_setup": False,
                    "reason": "serialize_error",
                    "score": 0.0,
                    "promotable": False,
                }
            )
    return rows


@router.get("", response_model=OpportunitiesResponse)
async def list_opportunities():
    from ...config import settings

    enabled = bool(settings.trading.opportunity_scanner_enabled)
    if not _scanner:
        return OpportunitiesResponse(enabled=enabled, results=[], total=0)
    results = _safe_results(_scanner)
    last = getattr(_scanner, "last_scan_at", None)
    return OpportunitiesResponse(
        enabled=enabled,
        last_scan_at=last.isoformat() if last else None,
        scanning=bool(getattr(_scanner, "scan_in_progress", False)),
        results=results,
        total=len(results),
    )


@router.get("/hot", response_model=HotListResponse)
async def list_hot():
    if not _scanner:
        return HotListResponse(hot=[], total=0)
    hot = _scanner.hot.to_list()
    return HotListResponse(hot=hot, total=len(hot))


async def _background_scan(base_symbols: List[str]) -> None:
    if not _scanner:
        return
    try:
        await _scanner.scan_once(base_symbols=base_symbols)
    except Exception as e:
        from ...utils.logging import get_logger

        get_logger(__name__).error(f"[SCAN] background scan failed: {e}")


@router.post("/scan", dependencies=[Depends(RequireAuth())])
async def force_scan():
    """Start a mechanical scan in the background (avoids HTTP timeouts)."""
    global _bg_scan_task
    from ...config import settings

    if not _scanner:
        raise HTTPException(503, "Opportunity scanner not initialized — start the bot first")

    if getattr(_scanner, "scan_in_progress", False) or (
        _bg_scan_task is not None and not _bg_scan_task.done()
    ):
        return {
            "success": True,
            "status": "already_running",
            "scanning": True,
            "total": len(_scanner.last_results or []),
            "last_scan_at": (
                _scanner.last_scan_at.isoformat() if _scanner.last_scan_at else None
            ),
        }

    base = list(settings.trading.symbols)
    _bg_scan_task = asyncio.create_task(_background_scan(base))
    return {
        "success": True,
        "status": "started",
        "scanning": True,
        "message": "Scan started in background — refresh in ~30–90s",
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
