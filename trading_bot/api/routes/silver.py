"""
Silver Analysis API routes.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from ...analysis.silver_analysis import SilverAnalyzer
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global silver analyzer
silver_analyzer = SilverAnalyzer()


class SilverAnalysisRequest(BaseModel):
    """Request for silver analysis."""
    current_price: float
    prices: Optional[List[float]] = None
    volume: Optional[List[float]] = None
    physical_premium: float = 0.0


class SilverKeyLevelsResponse(BaseModel):
    """Silver key price levels."""
    recent_low: float
    recent_high: float
    target_1: float
    target_2: float
    euphoria: float
    invalidation: float
    entry_zone_low: float
    entry_zone_high: float


@router.get("/levels", response_model=SilverKeyLevelsResponse)
async def get_key_levels():
    """Get silver key price levels."""
    levels = silver_analyzer.key_levels
    return SilverKeyLevelsResponse(
        recent_low=levels.recent_low,
        recent_high=levels.recent_high,
        target_1=levels.target_1,
        target_2=levels.target_2,
        euphoria=levels.euphoria,
        invalidation=levels.invalidation,
        entry_zone_low=levels.entry_zone_low,
        entry_zone_high=levels.entry_zone_high
    )


@router.post("/analyze")
async def analyze_silver(request: SilverAnalysisRequest):
    """
    Run comprehensive silver analysis.
    
    Includes:
    - Entry zone status
    - RSI calculation
    - 1979 pattern match
    - Volume analysis
    - Paper/physical disconnect
    - Targets and stop loss
    - Risk assessment
    """
    market_data = {
        'current_price': request.current_price,
        'prices': request.prices or [request.current_price],
        'volume': request.volume or [],
        'physical_premium': request.physical_premium
    }
    
    analysis = silver_analyzer.analyze(market_data)
    return analysis


@router.get("/entry-check/{price}")
async def check_entry_zone(price: float):
    """Check if price is in optimal entry zone."""
    in_zone = silver_analyzer.is_in_entry_zone(price)
    levels = silver_analyzer.key_levels
    
    return {
        'price': price,
        'in_entry_zone': in_zone,
        'entry_zone': {
            'low': levels.entry_zone_low,
            'high': levels.entry_zone_high
        },
        'recommendation': 'ENTRY_ZONE' if in_zone else 'OUTSIDE_ZONE'
    }


@router.get("/pattern-1979")
async def get_1979_pattern():
    """Get 1979 pattern information and current match."""
    pattern_match = silver_analyzer.match_1979_pattern({
        'january_2026_gain': 0.65,
        'current_price': 95.0
    })
    
    return {
        'pattern_1979': {
            'december_gain': '65%',
            'january_continuation': '35-40%',
            'peak': '$50 (then crash)',
            'total_move': 'Hunt Brothers silver squeeze'
        },
        'current_match': pattern_match,
        'phases': ['accumulation', 'surge', 'continuation', 'euphoria', 'crash'],
        'current_phase': 'surge'
    }


@router.get("/targets/{entry_price}")
async def get_targets(entry_price: float):
    """Get target levels for a given entry price."""
    targets = silver_analyzer.calculate_silver_targets(entry_price)
    stop_loss = silver_analyzer.calculate_silver_stop_loss(entry_price)
    
    return {
        'entry_price': entry_price,
        'stop_loss': stop_loss,
        'targets': targets,
        'risk_reward': {
            'to_tp1': round((targets['tp1'] - entry_price) / (entry_price - stop_loss), 2),
            'to_tp2': round((targets['tp2'] - entry_price) / (entry_price - stop_loss), 2),
            'to_tp3': round((targets['tp3'] - entry_price) / (entry_price - stop_loss), 2),
        }
    }


@router.get("/exit-check")
async def check_exit_conditions(
    rsi_weekly: float = 50,
    sentiment: str = 'neutral',
    price: float = 100
):
    """Check if exit conditions are met."""
    should_exit = silver_analyzer.check_exit_conditions({
        'rsi_weekly': rsi_weekly,
        'public_sentiment': sentiment,
        'pattern_phase': 'surge',
        'price': price
    })
    
    exit_reasons = []
    if rsi_weekly >= 85:
        exit_reasons.append(f"RSI extreme: {rsi_weekly}")
    if sentiment in ['extremely_bullish', 'euphoric']:
        exit_reasons.append(f"Sentiment: {sentiment}")
    if price >= silver_analyzer.key_levels.euphoria:
        exit_reasons.append(f"Price at euphoria: ${price}")
    
    return {
        'should_exit': should_exit,
        'rsi_weekly': rsi_weekly,
        'sentiment': sentiment,
        'price': price,
        'exit_reasons': exit_reasons
    }
