"""
API routes for Scaling Manager and Position Sizing.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from ...services.scaling_manager import ScalingManager, TradingMode
from ...execution.scaling_position_sizer import ScalingPositionSizer, SetupGrade
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global instances (will be set by main.py)
_scaling_manager: Optional[ScalingManager] = None
_position_sizer: Optional[ScalingPositionSizer] = None


def set_scaling_manager(manager: ScalingManager):
    """Set the scaling manager instance."""
    global _scaling_manager
    _scaling_manager = manager


def set_position_sizer(sizer: ScalingPositionSizer):
    """Set the position sizer instance."""
    global _position_sizer
    _position_sizer = sizer


class PositionSizeRequest(BaseModel):
    """Request for position size calculation."""
    equity: float
    entry_price: float
    stop_loss: float
    symbol: str
    confidence: float = 0.7
    setup_grade: str = "B"


class PositionSizeResponse(BaseModel):
    """Response with position size details."""
    lots: float
    risk_amount: float
    risk_percent: float
    tier_name: str
    adjustments: List[str]


@router.get("/status")
async def get_scaling_status(current_equity: float = 1000):
    """
    Get current scaling status including mode, risk settings, and performance.
    """
    if not _scaling_manager:
        # Create temporary instance for status
        manager = ScalingManager(starting_equity=current_equity)
        return manager.get_status(current_equity)
    
    return _scaling_manager.get_status(current_equity)


@router.get("/mode")
async def get_current_mode(current_equity: float = 1000):
    """
    Get the current trading mode based on performance.
    """
    if not _scaling_manager:
        return {
            "mode": "normal",
            "description": "Standard operation",
            "risk_multiplier": 1.0
        }
    
    mode = _scaling_manager.determine_mode(current_equity)
    config = _scaling_manager.get_mode_config(mode)
    
    return {
        "mode": mode.value,
        "description": config.description,
        "risk_multiplier": config.risk_multiplier,
        "confidence_threshold": config.confidence_threshold,
        "setup_filter": config.setup_filter,
        "max_daily_trades": config.max_daily_trades
    }


@router.get("/tier")
async def get_current_tier(equity: float = 1000):
    """
    Get the current scaling tier based on equity.
    """
    sizer = _position_sizer or ScalingPositionSizer()
    return sizer.get_tier_info(equity)


@router.post("/calculate-size", response_model=PositionSizeResponse)
async def calculate_position_size(request: PositionSizeRequest):
    """
    Calculate position size with all adjustments.
    """
    sizer = _position_sizer or ScalingPositionSizer()
    
    # Map setup grade string to enum
    grade_map = {
        "A+": SetupGrade.A_PLUS,
        "A": SetupGrade.A,
        "B": SetupGrade.B,
        "C": SetupGrade.C
    }
    setup_grade = grade_map.get(request.setup_grade.upper(), SetupGrade.B)
    
    result = sizer.calculate_position_size(
        equity=request.equity,
        entry_price=request.entry_price,
        stop_loss=request.stop_loss,
        symbol=request.symbol,
        confidence=request.confidence,
        setup_grade=setup_grade
    )
    
    return PositionSizeResponse(
        lots=result.lots,
        risk_amount=result.risk_amount,
        risk_percent=result.risk_percent,
        tier_name=result.tier_name,
        adjustments=result.adjustments
    )


@router.get("/projection")
async def get_growth_projection(
    starting_equity: float = 1000,
    target_equity: float = 100000,
    monthly_return: float = 0.15,
    win_rate: float = 0.55
):
    """
    Get projected growth trajectory to reach target.
    """
    sizer = _position_sizer or ScalingPositionSizer()
    
    projections = sizer.simulate_growth(
        starting_equity=starting_equity,
        target_equity=target_equity,
        avg_r_per_trade=1.5,
        win_rate=win_rate,
        trades_per_month=40
    )
    
    return {
        "starting_equity": starting_equity,
        "target_equity": target_equity,
        "projections": projections,
        "estimated_months": len(projections) if projections else 0,
        "final_equity": projections[-1]["equity"] if projections else starting_equity
    }


@router.get("/tiers")
async def get_all_tiers():
    """
    Get all scaling tier definitions.
    """
    from ...execution.scaling_position_sizer import SCALING_TIERS
    
    return {
        "tiers": [
            {
                "name": f"${tier.equity_min:,.0f}-${tier.equity_max:,.0f}" if tier.equity_max != float('inf') else f"${tier.equity_min:,.0f}+",
                "equity_min": tier.equity_min,
                "equity_max": tier.equity_max if tier.equity_max != float('inf') else None,
                "base_lots": tier.base_lots,
                "max_lots": tier.max_lots,
                "risk_percent": tier.risk_percent * 100,
                "max_daily_trades": tier.max_daily_trades
            }
            for tier in SCALING_TIERS
        ]
    }
