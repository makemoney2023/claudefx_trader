"""
Precious Metals API routes.

Combined API for Gold and Silver analysis.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from ...analysis.precious_metals_analysis import PreciousMetalsAnalyzer, GoldKeyLevels, GoldSilverRatio
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global precious metals analyzer
_precious_metals_analyzer: Optional[PreciousMetalsAnalyzer] = None


def get_precious_metals_analyzer() -> PreciousMetalsAnalyzer:
    """Get or create the precious metals analyzer instance."""
    global _precious_metals_analyzer
    if _precious_metals_analyzer is None:
        _precious_metals_analyzer = PreciousMetalsAnalyzer()
    return _precious_metals_analyzer


def set_precious_metals_analyzer(analyzer: PreciousMetalsAnalyzer):
    """Set the precious metals analyzer instance (for bot integration)."""
    global _precious_metals_analyzer
    _precious_metals_analyzer = analyzer


# Request/Response Models
class GoldAnalysisRequest(BaseModel):
    """Request for gold analysis."""
    current_price: float
    prices: Optional[List[float]] = None


class CombinedAnalysisRequest(BaseModel):
    """Request for combined precious metals analysis."""
    gold_price: float
    silver_price: float
    gold_prices: Optional[List[float]] = None
    silver_prices: Optional[List[float]] = None
    geopolitical_risk: str = "normal"


class GoldKeyLevelsResponse(BaseModel):
    """Gold key price levels."""
    recent_low: float
    recent_high: float
    all_time_high: float
    support_1: float
    support_2: float
    resistance_1: float
    resistance_2: float
    invalidation: float
    entry_zone_low: float
    entry_zone_high: float


class RatioResponse(BaseModel):
    """Gold/Silver ratio response."""
    current_ratio: float
    historical_avg: float
    normal_low: float
    normal_high: float
    interpretation: str
    trade_bias: str


class SafeHavenResponse(BaseModel):
    """Safe haven demand assessment."""
    level: str
    score: int
    factors: List[str]
    recommendation: str


class SummaryResponse(BaseModel):
    """Combined precious metals summary."""
    timestamp: str
    gold_price: float
    silver_price: float
    gold_recommendation: str
    silver_recommendation: str
    ratio: float
    ratio_interpretation: str
    primary_metal: str
    primary_reasoning: str
    safe_haven_level: str


@router.get("/live-prices")
async def get_live_prices():
    """
    Get live gold and silver prices from MT5.
    
    Falls back to zeros if MT5 is unavailable or markets are closed.
    """
    import asyncio
    
    gold_price = 0.0
    silver_price = 0.0
    source = "unavailable"
    
    try:
        import MetaTrader5 as mt5
        
        def _fetch_prices():
            g_price = 0.0
            s_price = 0.0
            
            gold_tick = mt5.symbol_info_tick("XAUUSD")
            if gold_tick and gold_tick.bid > 0:
                g_price = gold_tick.bid
                
            silver_tick = mt5.symbol_info_tick("XAGUSD")
            if silver_tick and silver_tick.bid > 0:
                s_price = silver_tick.bid
            
            return g_price, s_price
        
        gold_price, silver_price = await asyncio.to_thread(_fetch_prices)
        if gold_price > 0 or silver_price > 0:
            source = "mt5"
    except Exception as e:
        logger.warning(f"Could not fetch live precious metals prices: {e}")
    
    return {
        "gold_price": gold_price,
        "silver_price": silver_price,
        "source": source
    }


# Routes
@router.get("/summary")
async def get_precious_metals_summary(
    gold_price: float = Query(..., description="Current gold price"),
    silver_price: float = Query(..., description="Current silver price"),
    geopolitical_risk: str = Query("normal", description="Geopolitical risk level: low, normal, medium, high, extreme")
) -> SummaryResponse:
    """
    Get combined precious metals summary.
    
    Quick overview of both gold and silver with cross-metal analysis.
    """
    analyzer = get_precious_metals_analyzer()
    
    analysis = analyzer.analyze_combined(
        gold_price=gold_price,
        silver_price=silver_price,
        geopolitical_risk=geopolitical_risk
    )
    
    return SummaryResponse(
        timestamp=analysis['timestamp'],
        gold_price=gold_price,
        silver_price=silver_price,
        gold_recommendation=analysis['gold'].get('recommendation', 'HOLD'),
        silver_recommendation=analysis['silver'].get('recommendation', 'HOLD'),
        ratio=analysis['ratio']['current'],
        ratio_interpretation=analysis['ratio']['interpretation'],
        primary_metal=analysis['primary_recommendation']['metal'],
        primary_reasoning=analysis['primary_recommendation']['reasoning'],
        safe_haven_level=analysis['safe_haven']['level']
    )


@router.get("/gold/levels", response_model=GoldKeyLevelsResponse)
async def get_gold_key_levels():
    """Get gold key price levels."""
    analyzer = get_precious_metals_analyzer()
    levels = analyzer.gold_levels
    
    return GoldKeyLevelsResponse(
        recent_low=levels.recent_low,
        recent_high=levels.recent_high,
        all_time_high=levels.all_time_high,
        support_1=levels.support_1,
        support_2=levels.support_2,
        resistance_1=levels.resistance_1,
        resistance_2=levels.resistance_2,
        invalidation=levels.invalidation,
        entry_zone_low=levels.entry_zone_low,
        entry_zone_high=levels.entry_zone_high
    )


@router.post("/gold/analyze")
async def analyze_gold(request: GoldAnalysisRequest) -> Dict[str, Any]:
    """
    Run comprehensive gold analysis.
    
    Includes:
    - Entry zone status
    - RSI calculation
    - Key levels analysis
    - Targets and stop loss
    - Risk assessment
    """
    analyzer = get_precious_metals_analyzer()
    
    market_data = {
        'current_price': request.current_price,
        'prices': request.prices or [request.current_price]
    }
    
    return analyzer.analyze_gold(market_data)


@router.get("/ratio", response_model=RatioResponse)
async def get_gold_silver_ratio(
    gold_price: float = Query(..., description="Current gold price"),
    silver_price: float = Query(..., description="Current silver price")
):
    """
    Calculate and analyze the gold/silver ratio.
    
    The ratio is a key indicator for relative value between the metals:
    - Historical average: ~70
    - Normal range: 60-80
    - High ratio (>80): Silver undervalued
    - Low ratio (<60): Silver may be overextended
    """
    analyzer = get_precious_metals_analyzer()
    ratio = analyzer.calculate_gold_silver_ratio(gold_price, silver_price)
    
    return RatioResponse(
        current_ratio=ratio.current_ratio,
        historical_avg=ratio.historical_avg,
        normal_low=ratio.normal_low,
        normal_high=ratio.normal_high,
        interpretation=ratio.interpretation,
        trade_bias=ratio.trade_bias
    )


@router.get("/safe-haven", response_model=SafeHavenResponse)
async def get_safe_haven_demand(
    gold_price: float = Query(..., description="Current gold price"),
    silver_price: float = Query(..., description="Current silver price"),
    geopolitical_risk: str = Query("normal", description="Geopolitical risk level")
):
    """
    Assess safe haven demand for precious metals.
    
    Based on:
    - Geopolitical risk levels
    - Gold/silver ratio signals
    - Market conditions
    """
    analyzer = get_precious_metals_analyzer()
    ratio = analyzer.calculate_gold_silver_ratio(gold_price, silver_price)
    safe_haven = analyzer._assess_safe_haven_demand(geopolitical_risk, ratio)
    
    return SafeHavenResponse(
        level=safe_haven['level'],
        score=safe_haven['score'],
        factors=safe_haven['factors'],
        recommendation=safe_haven['recommendation']
    )


@router.post("/analyze")
async def analyze_combined(request: CombinedAnalysisRequest) -> Dict[str, Any]:
    """
    Run comprehensive combined precious metals analysis.
    
    Includes:
    - Individual gold and silver analysis
    - Gold/silver ratio analysis
    - Cross-metal signals
    - Safe haven assessment
    - Primary recommendation
    """
    analyzer = get_precious_metals_analyzer()
    
    return analyzer.analyze_combined(
        gold_price=request.gold_price,
        silver_price=request.silver_price,
        gold_prices=request.gold_prices,
        silver_prices=request.silver_prices,
        geopolitical_risk=request.geopolitical_risk
    )


@router.get("/correlation")
async def get_metals_correlation():
    """
    Get precious metals correlation information.
    
    Gold and silver are highly correlated but have different characteristics.
    """
    return {
        "gold_silver_correlation": 0.90,
        "characteristics": {
            "gold": {
                "volatility": "lower",
                "safe_haven_strength": "stronger",
                "typical_daily_range_percent": 1.0,
                "description": "Primary safe haven, institutional preference"
            },
            "silver": {
                "volatility": "higher",
                "safe_haven_strength": "moderate",
                "typical_daily_range_percent": 2.0,
                "description": "More volatile, higher beta to gold moves"
            }
        },
        "trading_implications": [
            "When risk-off: Gold typically leads, silver follows with larger move",
            "High ratio (>80): Consider silver over gold for new longs",
            "Low ratio (<60): Consider gold over silver",
            "Both rising together: Strong precious metals bid"
        ]
    }


@router.get("/context")
async def get_claude_context(
    gold_price: float = Query(..., description="Current gold price"),
    silver_price: float = Query(..., description="Current silver price"),
    geopolitical_risk: str = Query("normal", description="Geopolitical risk level")
) -> Dict[str, str]:
    """
    Get precious metals context string for Claude prompts.
    
    This endpoint provides formatted context that can be included
    in Claude analysis prompts for gold or silver trades.
    """
    analyzer = get_precious_metals_analyzer()
    context = analyzer.get_context_for_claude(gold_price, silver_price, geopolitical_risk)
    
    return {
        "context": context,
        "format": "markdown",
        "use_case": "Include in Claude analysis prompt when analyzing XAUUSD or XAGUSD"
    }
