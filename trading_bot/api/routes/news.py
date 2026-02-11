"""
News and Economic Calendar API routes.
"""

from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ...services.news_service import NewsService
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global news service instance
news_service = NewsService()

# Reference to Firecrawl service (set by main app)
_firecrawl_service = None

def set_firecrawl_service(service):
    """Set the Firecrawl service for geopolitical news."""
    global _firecrawl_service
    _firecrawl_service = service


class NewsEvent(BaseModel):
    """News event model."""
    title: str
    datetime: str
    impact: str
    currency: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None


class CalendarResponse(BaseModel):
    """Economic calendar response."""
    events: List[NewsEvent]
    total: int
    is_blackout: bool
    blackout_reason: str


class BlackoutStatusResponse(BaseModel):
    """Blackout status response."""
    is_blackout: bool
    reason: str
    should_trade: bool
    next_event: Optional[dict]


class GeopoliticalRiskResponse(BaseModel):
    """Geopolitical risk response."""
    risk_level: str
    news_items: List[str]


@router.get("/calendar", response_model=CalendarResponse)
async def get_economic_calendar(days: int = 7, currency: Optional[str] = None):
    """
    Get economic calendar events.
    
    Args:
        days: Number of days to fetch
        currency: Filter by currency (e.g., USD, EUR)
    """
    events = await news_service.fetch_economic_calendar(days_ahead=days)
    
    if currency:
        events = news_service.get_events_for_currency(currency)
    
    is_blackout, reason = news_service.is_blackout_period()
    
    return CalendarResponse(
        events=[NewsEvent(**e) for e in events if all(k in e for k in ['title', 'datetime', 'impact', 'currency'])],
        total=len(events),
        is_blackout=is_blackout,
        blackout_reason=reason
    )


@router.get("/blackout", response_model=BlackoutStatusResponse)
async def get_blackout_status():
    """
    Get current blackout status.
    
    Returns whether trading should be paused due to upcoming/recent high-impact events.
    """
    is_blackout, reason = news_service.is_blackout_period()
    next_event = news_service.get_countdown_to_next_event()
    
    return BlackoutStatusResponse(
        is_blackout=is_blackout,
        reason=reason,
        should_trade=news_service.should_trade(),
        next_event=next_event
    )


@router.get("/upcoming")
async def get_upcoming_events(hours: int = 24):
    """
    Get upcoming high-impact events.
    
    Args:
        hours: Hours ahead to look
    """
    events = await news_service.get_upcoming_events(hours=hours)
    high_impact = [e for e in events if e.get('impact', '').lower() in ['high', 'red'] or e.get('impact') == 3]
    
    countdown = news_service.get_countdown_to_next_event()
    
    return {
        'events': high_impact,
        'total': len(high_impact),
        'countdown_to_next': countdown
    }


@router.get("/geopolitical", response_model=GeopoliticalRiskResponse)
async def get_geopolitical_risk():
    """
    Get current geopolitical risk assessment.
    Uses cached Firecrawl data when available, falls back to local news service.
    This endpoint is optimized for fast response - actual Firecrawl fetching 
    happens in background refresh.
    """
    # First check cache for instant response
    if _firecrawl_service and _firecrawl_service.is_available:
        # Check if we have cached geopolitical data
        cached = _firecrawl_service._cache.get("geopolitical_news")
        if cached and not cached.is_expired() and cached.data:
            headlines = [h.get('title', '') for h in cached.data.get('headlines', [])]
            if headlines:
                return GeopoliticalRiskResponse(
                    risk_level=cached.data.get('risk_level', 'low'),
                    news_items=headlines[:10]
                )
    
    # Fallback to local cached news (always fast)
    risk_level = news_service.get_geopolitical_risk_level()
    
    return GeopoliticalRiskResponse(
        risk_level=risk_level,
        news_items=news_service._geopolitical_news
    )


@router.post("/events")
async def add_event(event: NewsEvent):
    """
    Manually add an event to the calendar.
    
    Useful for testing or adding custom events.
    """
    current_events = news_service._events
    current_events.append({
        'title': event.title,
        'datetime': event.datetime,
        'impact': event.impact,
        'currency': event.currency,
        'forecast': event.forecast,
        'previous': event.previous,
        'actual': event.actual
    })
    news_service.set_events(current_events)
    
    return {'message': 'Event added', 'total_events': len(current_events)}


@router.post("/geopolitical")
async def add_geopolitical_news(headlines: List[str]):
    """
    Add geopolitical news headlines for risk assessment.
    """
    news_service.add_geopolitical_news(headlines)
    
    return {
        'message': f'Added {len(headlines)} headlines',
        'risk_level': news_service.get_geopolitical_risk_level()
    }


@router.get("/status")
async def get_news_service_status():
    """
    Get full news service status.
    """
    return news_service.get_status()


def get_news_service() -> NewsService:
    """Get the global news service instance."""
    return news_service
