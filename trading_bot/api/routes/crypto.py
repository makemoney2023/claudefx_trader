"""
Crypto Analysis API routes for XRP and ADA.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from ...analysis.crypto_analysis import CryptoAnalyzer
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global crypto analyzer
crypto_analyzer = CryptoAnalyzer()


class CryptoAnalysisRequest(BaseModel):
    """Request for crypto analysis."""
    current_price: float
    prices: Optional[List[float]] = None
    volume: Optional[List[float]] = None


@router.get("/summary")
async def get_crypto_summary():
    """
    Get summary of all configured cryptocurrencies (XRP, ADA).
    """
    return crypto_analyzer.get_crypto_summary()


@router.get("/{symbol}/levels")
async def get_crypto_levels(symbol: str):
    """
    Get key price levels for a cryptocurrency.
    
    Args:
        symbol: Crypto symbol (XRP, ADA, XRPUSD, ADAUSD)
    """
    levels = crypto_analyzer.get_key_levels(symbol)
    config = crypto_analyzer.get_config(symbol)
    
    if not levels or not config:
        return {"error": f"Symbol {symbol} not configured"}
    
    return {
        "symbol": symbol,
        "name": config["name"],
        "levels": {
            "support_1": levels.support_1,
            "support_2": levels.support_2,
            "resistance_1": levels.resistance_1,
            "resistance_2": levels.resistance_2,
            "recent_low": levels.recent_low,
            "recent_high": levels.recent_high,
            "all_time_high": levels.all_time_high
        }
    }


@router.post("/{symbol}/analyze")
async def analyze_crypto(symbol: str, request: CryptoAnalysisRequest):
    """
    Run comprehensive analysis for a cryptocurrency.
    
    Includes:
    - RSI analysis
    - Support/resistance proximity
    - Volatility assessment
    - Regulatory risk check
    - Position size adjustment
    """
    market_data = {
        'current_price': request.current_price,
        'prices': request.prices or [request.current_price],
        'volume': request.volume or []
    }
    
    analysis = crypto_analyzer.analyze(symbol, market_data)
    return analysis


@router.get("/{symbol}/regulatory-risk")
async def get_regulatory_risk(symbol: str):
    """
    Get regulatory risk assessment for a cryptocurrency.
    
    XRP has SEC lawsuit history and is flagged as regulatory-sensitive.
    ADA has no major regulatory concerns.
    """
    return crypto_analyzer.check_regulatory_risk(symbol)


@router.get("/{symbol}/position-size")
async def get_position_size_adjustment(symbol: str, base_size: float = 0.01):
    """
    Get adjusted position size for crypto volatility.
    
    Crypto is more volatile, so position sizes are reduced.
    
    Args:
        symbol: Crypto symbol
        base_size: Base position size in lots
    """
    adjusted = crypto_analyzer.get_position_size_adjustment(symbol, base_size)
    config = crypto_analyzer.get_config(symbol)
    
    multiplier = config.get('volatility_multiplier', 1.0) if config else 1.0
    
    return {
        "symbol": symbol,
        "base_size": base_size,
        "adjusted_size": adjusted,
        "volatility_multiplier": multiplier,
        "reduction_percent": round((1 - adjusted / base_size) * 100, 1)
    }


@router.get("/{symbol}/check-levels")
async def check_price_levels(symbol: str, price: float):
    """
    Check if price is near support or resistance levels.
    
    Args:
        symbol: Crypto symbol
        price: Price to check
    """
    near_support, support_level = crypto_analyzer.is_near_support(symbol, price)
    near_resistance, resistance_level = crypto_analyzer.is_near_resistance(symbol, price)
    levels = crypto_analyzer.get_key_levels(symbol)
    
    return {
        "symbol": symbol,
        "price": price,
        "near_support": {
            "is_near": near_support,
            "level_name": support_level,
            "level_price": getattr(levels, support_level, None) if support_level else None
        },
        "near_resistance": {
            "is_near": near_resistance,
            "level_name": resistance_level,
            "level_price": getattr(levels, resistance_level, None) if resistance_level else None
        },
        "recommendation": "BUY" if near_support else "SELL" if near_resistance else "NEUTRAL"
    }
