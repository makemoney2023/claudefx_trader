"""
Market Intelligence API Routes.

Endpoints for accessing Firecrawl-powered market intelligence.
"""

import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

# Global reference to intelligence service (set by main app)
_firecrawl_service = None


def set_firecrawl_service(service):
    """Set the Firecrawl intelligence service instance."""
    global _firecrawl_service
    _firecrawl_service = service


class IntelligenceStatus(BaseModel):
    """Intelligence service status."""
    enabled: bool
    available: bool
    firecrawl_sdk: bool
    api_key_configured: bool
    refresh_minutes: int
    cached_keys: List[str]
    cache_status: Dict[str, Any]


class DXYAnalysis(BaseModel):
    """DXY analysis result."""
    trend: str
    bias: Optional[str] = None
    error: Optional[str] = None


class COTPositioning(BaseModel):
    """COT positioning result."""
    positioning: str
    sentiment: Optional[str] = None
    error: Optional[str] = None


class NewsItem(BaseModel):
    """Breaking news item."""
    title: str
    url: str
    source: str
    timestamp: str


class CBSentiment(BaseModel):
    """Central bank sentiment."""
    fed: str
    ecb: str


@router.get("/status", response_model=IntelligenceStatus)
async def get_intelligence_status():
    """Get current intelligence service status."""
    if not _firecrawl_service:
        return {
            "enabled": False,
            "available": False,
            "firecrawl_sdk": False,
            "api_key_configured": False,
            "refresh_minutes": 0,
            "cached_keys": [],
            "cache_status": {}
        }
    
    return _firecrawl_service.get_status()


@router.get("/dxy", response_model=DXYAnalysis)
async def get_dxy_analysis():
    """Get current DXY trend analysis."""
    if not _firecrawl_service:
        raise HTTPException(status_code=503, detail="Intelligence service not initialized")
    
    if not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Firecrawl not available")
    
    result = await _firecrawl_service.get_dxy_analysis()
    return result


@router.get("/cot/{currency}", response_model=COTPositioning)
async def get_cot_positioning(currency: str):
    """Get COT positioning for a currency."""
    if not _firecrawl_service:
        raise HTTPException(status_code=503, detail="Intelligence service not initialized")
    
    if not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Firecrawl not available")
    
    currency = currency.upper()
    if currency not in ['EUR', 'GBP', 'JPY', 'AUD', 'NZD', 'CAD', 'CHF']:
        raise HTTPException(status_code=400, detail=f"Invalid currency: {currency}")
    
    result = await _firecrawl_service.get_cot_positioning(currency)
    return result


@router.get("/news/breaking", response_model=List[NewsItem])
async def get_breaking_news(symbols: str = "EURUSD,GBPUSD,XAUUSD"):
    """
    Get breaking market news.
    
    Args:
        symbols: Comma-separated list of symbols to search news for
    """
    if not _firecrawl_service:
        raise HTTPException(status_code=503, detail="Intelligence service not initialized")
    
    if not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Firecrawl not available")
    
    symbol_list = [s.strip() for s in symbols.split(',')]
    result = await _firecrawl_service.get_breaking_news(symbol_list)
    return result


@router.get("/geopolitical")
async def get_geopolitical_news():
    """
    Get geopolitical news and risk assessment from major news sources.
    
    Returns:
        Risk level, headlines, and analysis of current geopolitical situation
    """
    import asyncio
    
    if not _firecrawl_service:
        raise HTTPException(status_code=503, detail="Intelligence service not initialized")
    
    if not _firecrawl_service.is_available:
        return {
            "risk_level": "unknown",
            "headlines": [],
            "total_found": 0,
            "error": "Firecrawl not available - check API key"
        }
    
    try:
        # Set a 15 second timeout to avoid blocking
        result = await asyncio.wait_for(
            _firecrawl_service.get_geopolitical_news(),
            timeout=15.0
        )
        return result
    except asyncio.TimeoutError:
        return {
            "risk_level": "unknown",
            "headlines": [],
            "total_found": 0,
            "error": "Request timed out - try again later"
        }


@router.get("/central-banks", response_model=CBSentiment)
async def get_central_bank_sentiment():
    """Get central bank sentiment (Fed and ECB)."""
    if not _firecrawl_service:
        raise HTTPException(status_code=503, detail="Intelligence service not initialized")
    
    if not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Firecrawl not available")
    
    result = await _firecrawl_service.get_central_bank_sentiment()
    return result


@router.post("/refresh")
async def refresh_intelligence(symbols: str = "EURUSD,GBPUSD,XAUUSD"):
    """
    Force refresh all intelligence data.
    
    Args:
        symbols: Comma-separated list of symbols for news search
    """
    if not _firecrawl_service:
        raise HTTPException(status_code=503, detail="Intelligence service not initialized")
    
    if not _firecrawl_service.is_available:
        return {
            "success": False,
            "message": "Firecrawl not available - no API key or SDK"
        }
    
    symbol_list = [s.strip() for s in symbols.split(',')]
    await _firecrawl_service.refresh_all(symbol_list)
    
    return {
        "success": True,
        "message": "Intelligence data refreshed",
        "cached_keys": list(_firecrawl_service._cache.keys())
    }


@router.get("/context")
async def get_claude_context(symbol: str = "EURUSD"):
    """Get the market context string for Claude."""
    if not _firecrawl_service:
        return {"context": ""}
    
    context = _firecrawl_service.get_market_context_for_claude(symbol)
    return {"context": context}


# =========================================================================
# NEW: Advanced Intelligence Endpoints
# =========================================================================

@router.get("/vix")
async def get_vix_sentiment():
    """Get VIX (Fear Index) sentiment and risk mode."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_vix_sentiment()


@router.get("/retail/{symbol}")
async def get_retail_sentiment(symbol: str):
    """Get retail trader sentiment (use as CONTRARIAN indicator)."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_retail_sentiment(symbol.upper())


@router.get("/currency-strength")
async def get_currency_strength():
    """Get currency strength rankings."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_currency_strength()


@router.get("/tradingview/{symbol}")
async def get_tradingview_technical(symbol: str):
    """Get TradingView technical consensus for a symbol."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_tradingview_technical(symbol.upper())


@router.get("/rates")
async def get_rate_expectations():
    """Get interest rate expectations from Fed funds futures."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_rate_expectations()


@router.get("/commodity/{commodity}")
async def get_commodity_correlation(commodity: str):
    """
    Get commodity trend and currency implications.
    
    Args:
        commodity: 'oil' or 'gold'
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    if commodity.lower() not in ['oil', 'gold']:
        raise HTTPException(status_code=400, detail="Commodity must be 'oil' or 'gold'")
    
    return await _firecrawl_service.get_commodity_correlation(commodity.lower())


@router.get("/social/{symbol}")
async def get_social_sentiment(symbol: str):
    """Get social media (Twitter/X) sentiment for a symbol."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_twitter_forex_sentiment(symbol.upper())


@router.get("/options/{symbol}")
async def get_options_flow(symbol: str):
    """Get FX options flow and magnet levels for a symbol."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_options_flow(symbol.upper())


@router.get("/yields")
async def get_bond_yields():
    """Get bond yield spread (US-DE) and EUR/USD implications."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_bond_yield_spread()


@router.get("/btc-dominance")
async def get_btc_dominance():
    """Get Bitcoin dominance for crypto pair analysis."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_btc_dominance()


@router.get("/economic-surprise")
async def get_economic_surprise():
    """Get economic surprise index (actual vs expectations)."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_economic_surprise_index()


@router.get("/seasonal/{symbol}")
async def get_seasonal_pattern(symbol: str):
    """Get historical seasonal pattern for a symbol."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_seasonal_pattern(symbol.upper())


@router.get("/intermarket")
async def get_intermarket_analysis():
    """Get intermarket correlations (SPX, VIX, DXY, Gold)."""
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_intermarket_analysis()


@router.get("/complete/{symbol}")
async def get_complete_analysis(symbol: str):
    """
    Get COMPLETE analysis combining ALL intelligence sources.
    
    This is the master endpoint that returns everything:
    - Overall bias (bullish/bearish) with signal counts
    - DXY, VIX, retail sentiment, currency strength
    - TradingView technical, rate expectations
    - Commodities (oil, gold)
    - Social sentiment, options flow, bond yields
    - Economic surprise, seasonal patterns, intermarket
    - BTC dominance (for crypto)
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        raise HTTPException(status_code=503, detail="Intelligence service not available")
    
    return await _firecrawl_service.get_complete_analysis(symbol.upper())


@router.get("/all")
async def get_all_intelligence():
    """Get all available intelligence data in one call (legacy endpoint)."""
    if not _firecrawl_service:
        return {
            "available": False,
            "dxy": None,
            "cot": {},
            "news": [],
            "central_banks": None,
            "context": ""
        }
    
    if not _firecrawl_service.is_available:
        return {
            "available": False,
            "message": "Firecrawl not configured",
            "dxy": None,
            "cot": {},
            "news": [],
            "central_banks": None,
            "context": ""
        }
    
    # Get all data
    dxy = await _firecrawl_service.get_dxy_analysis()
    vix = await _firecrawl_service.get_vix_sentiment()
    eur_cot = await _firecrawl_service.get_cot_positioning("EUR")
    gbp_cot = await _firecrawl_service.get_cot_positioning("GBP")
    news = await _firecrawl_service.get_breaking_news(["EURUSD", "GBPUSD", "XAUUSD"])
    cb_sentiment = await _firecrawl_service.get_central_bank_sentiment()
    intermarket = await _firecrawl_service.get_intermarket_analysis()
    context = _firecrawl_service.get_market_context_for_claude()
    
    return {
        "available": True,
        "timestamp": datetime.now().isoformat(),
        "dxy": dxy,
        "vix": vix,
        "cot": {
            "EUR": eur_cot,
            "GBP": gbp_cot
        },
        "news": news,
        "central_banks": cb_sentiment,
        "intermarket": intermarket,
        "context": context
    }


@router.post("/refresh/quick")
async def refresh_quick(symbol: str = "EURUSD"):
    """
    Quick refresh for time-sensitive data (DXY, VIX, retail sentiment).
    
    Call this every 3-5 minutes.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"success": False, "message": "Intelligence service not available"}
    
    await _firecrawl_service.refresh_quick(symbol.upper())
    
    return {
        "success": True,
        "message": f"Quick refresh complete for {symbol}",
        "cached_keys": list(_firecrawl_service._cache.keys())
    }


@router.post("/refresh/intermarket")
async def refresh_intermarket():
    """
    Refresh intermarket correlations (VIX, SPX, Gold).
    
    Call when market conditions change rapidly.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"success": False, "message": "Intelligence service not available"}
    
    await _firecrawl_service.refresh_intermarket()
    
    return {
        "success": True,
        "message": "Intermarket refresh complete"
    }


@router.get("/calendar")
async def get_economic_calendar(days: int = 90):
    """
    Get economic calendar events via Firecrawl.
    
    Args:
        days: Number of days to fetch (default 90 = 3 months)
    
    Returns:
        List of economic events with impact ratings
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {
            "events": [],
            "total": 0,
            "source": "unavailable",
            "message": "Firecrawl intelligence service not available"
        }
    
    events = await _firecrawl_service.get_economic_calendar(days=days)
    
    return {
        "events": events,
        "total": len(events),
        "source": "firecrawl",
        "days_requested": days
    }


# =========================================================================
# DEEP RESEARCH (AGENT) ENDPOINTS - AI-Powered Analysis
# =========================================================================

@router.get("/deep-research/geopolitical")
async def get_deep_geopolitical_research():
    """
    Get AI-powered deep geopolitical risk analysis.
    
    Uses Firecrawl Agent for autonomous web research to assess:
    - Current geopolitical risk level (low/medium/high/extreme)
    - Key events affecting forex markets
    - Trading recommendations based on risk environment
    - Safe haven demand assessment
    
    Cached for 30 minutes (deep research is expensive).
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {
            "available": False,
            "message": "Intelligence service not available"
        }
    
    # First try cached data (non-blocking)
    cached = _firecrawl_service.get_cached_geopolitical()
    if cached:
        return {
            "available": True,
            "source": "agent_deep_research",
            "data": cached.model_dump(),
            "cached": True
        }
    
    # If no cache, trigger research (this can take 30-60s)
    try:
        result = await asyncio.wait_for(
            _firecrawl_service.research_geopolitical_risk(),
            timeout=90.0
        )
        if result:
            return {
                "available": True,
                "source": "agent_deep_research",
                "data": result.model_dump(),
                "cached": False
            }
    except asyncio.TimeoutError:
        return {
            "available": False,
            "message": "Deep research timed out (90s)"
        }
    except Exception as e:
        return {
            "available": False,
            "message": str(e)
        }
    
    return {"available": False, "message": "Research returned no data"}


@router.get("/deep-research/central-banks")
async def get_deep_central_bank_research():
    """
    Get AI-powered deep central bank policy analysis.
    
    Uses Firecrawl Agent to research:
    - Fed, ECB, BOE, BOJ policy stances
    - Expected rate actions
    - Policy divergence opportunities
    - Currency implications
    
    Cached for 30 minutes.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"available": False, "message": "Intelligence service not available"}
    
    cached = _firecrawl_service.get_cached_central_bank()
    if cached:
        return {
            "available": True,
            "source": "agent_deep_research",
            "data": cached.model_dump(),
            "cached": True
        }
    
    try:
        result = await asyncio.wait_for(
            _firecrawl_service.research_central_bank_policy(),
            timeout=90.0
        )
        if result:
            return {
                "available": True,
                "source": "agent_deep_research",
                "data": result.model_dump(),
                "cached": False
            }
    except asyncio.TimeoutError:
        return {"available": False, "message": "Deep research timed out (90s)"}
    except Exception as e:
        return {"available": False, "message": str(e)}
    
    return {"available": False, "message": "Research returned no data"}


@router.get("/deep-research/intermarket")
async def get_deep_intermarket_research():
    """
    Get AI-powered deep intermarket correlation analysis.
    
    Uses Firecrawl Agent to analyze:
    - SPX, VIX, DXY, Gold, Oil trends
    - Risk environment (risk-on/risk-off)
    - Correlation anomalies
    - Trading implications for forex
    
    Cached for 30 minutes.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"available": False, "message": "Intelligence service not available"}
    
    cached = _firecrawl_service.get_cached_intermarket()
    if cached:
        return {
            "available": True,
            "source": "agent_deep_research",
            "data": cached.model_dump(),
            "cached": True
        }
    
    try:
        result = await asyncio.wait_for(
            _firecrawl_service.research_intermarket_correlations(),
            timeout=90.0
        )
        if result:
            return {
                "available": True,
                "source": "agent_deep_research",
                "data": result.model_dump(),
                "cached": False
            }
    except asyncio.TimeoutError:
        return {"available": False, "message": "Deep research timed out (90s)"}
    except Exception as e:
        return {"available": False, "message": str(e)}
    
    return {"available": False, "message": "Research returned no data"}


@router.get("/deep-research/fundamentals/{symbol}")
async def get_deep_symbol_fundamentals(symbol: str):
    """
    Get AI-powered deep fundamental analysis for a specific symbol.
    
    Uses Firecrawl Agent to research:
    - Fundamental bias (bullish/bearish/neutral)
    - Key fundamental drivers
    - Rate differentials
    - Trade recommendations
    
    Cached for 30 minutes per symbol.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"available": False, "message": "Intelligence service not available"}
    
    symbol = symbol.upper()
    cached = _firecrawl_service.get_cached_symbol_fundamentals(symbol)
    if cached:
        return {
            "available": True,
            "source": "agent_deep_research",
            "data": cached.model_dump(),
            "cached": True
        }
    
    try:
        result = await asyncio.wait_for(
            _firecrawl_service.research_symbol_fundamentals(symbol),
            timeout=90.0
        )
        if result:
            return {
                "available": True,
                "source": "agent_deep_research",
                "data": result.model_dump(),
                "cached": False
            }
    except asyncio.TimeoutError:
        return {"available": False, "message": "Deep research timed out (90s)"}
    except Exception as e:
        return {"available": False, "message": str(e)}
    
    return {"available": False, "message": "Research returned no data"}


@router.get("/deep-research/comprehensive")
async def get_comprehensive_intelligence(symbol: str = "EURUSD"):
    """
    Get comprehensive market intelligence from all deep research sources.
    
    Combines:
    - Geopolitical analysis
    - Central bank policy
    - Intermarket correlations
    - Overall risk assessment
    - Trading environment rating
    - Key themes and warnings
    
    This is the master endpoint for deep research data.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {
            "available": False,
            "message": "Intelligence service not available"
        }
    
    intel = _firecrawl_service.get_comprehensive_intelligence(symbol)
    
    return {
        "available": True,
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "overall_risk_level": intel.overall_risk_level,
        "trading_environment": intel.trading_environment,
        "key_themes": intel.key_themes,
        "warnings": intel.warnings,
        "geopolitical": intel.geopolitical.model_dump() if intel.geopolitical else None,
        "central_banks": intel.central_banks.model_dump() if intel.central_banks else None,
        "intermarket": intel.intermarket.model_dump() if intel.intermarket else None,
        "claude_context": intel.to_claude_context()
    }


# =========================================================================
# EXTRACT ENDPOINTS - Structured Data Extraction
# =========================================================================

@router.get("/extract/calendar")
async def get_extracted_calendar():
    """
    Get structured economic calendar data via Firecrawl Extract.
    
    Returns parsed calendar events with:
    - Event datetime
    - Currency affected
    - Impact level (high/medium/low)
    - Forecast and previous values
    
    Cached for 15 minutes.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"available": False, "message": "Intelligence service not available"}
    
    cached = _firecrawl_service.get_cached_economic_calendar()
    if cached:
        return {
            "available": True,
            "source": "firecrawl_extract",
            "data": cached.model_dump(),
            "cached": True
        }
    
    try:
        result = await _firecrawl_service.extract_economic_calendar()
        if result:
            return {
                "available": True,
                "source": "firecrawl_extract",
                "data": result.model_dump(),
                "cached": False
            }
    except Exception as e:
        return {"available": False, "message": str(e)}
    
    return {"available": False, "message": "Extraction returned no data"}


@router.get("/extract/cot")
async def get_extracted_cot():
    """
    Get structured COT (Commitment of Traders) data via Firecrawl Extract.
    
    Returns institutional positioning for major currencies.
    Cached for 60 minutes (COT data updates weekly).
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"available": False, "message": "Intelligence service not available"}
    
    cached = _firecrawl_service.get_cached_cot()
    if cached:
        return {
            "available": True,
            "source": "firecrawl_extract",
            "data": cached.model_dump(),
            "cached": True
        }
    
    try:
        result = await _firecrawl_service.extract_cot_positioning()
        if result:
            return {
                "available": True,
                "source": "firecrawl_extract",
                "data": result.model_dump(),
                "cached": False
            }
    except Exception as e:
        return {"available": False, "message": str(e)}
    
    return {"available": False, "message": "Extraction returned no data"}


@router.get("/extract/rates")
async def get_extracted_rates():
    """
    Get structured interest rate expectations via Firecrawl Extract.
    
    Returns rate forecasts from FedWatch tool and other sources.
    Cached for 30 minutes.
    """
    if not _firecrawl_service or not _firecrawl_service.is_available:
        return {"available": False, "message": "Intelligence service not available"}
    
    cached = _firecrawl_service.get_cached_rate_expectations()
    if cached:
        return {
            "available": True,
            "source": "firecrawl_extract",
            "data": cached.model_dump(),
            "cached": True
        }
    
    try:
        result = await _firecrawl_service.extract_rate_expectations()
        if result:
            return {
                "available": True,
                "source": "firecrawl_extract",
                "data": result.model_dump(),
                "cached": False
            }
    except Exception as e:
        return {"available": False, "message": str(e)}
    
    return {"available": False, "message": "Extraction returned no data"}
