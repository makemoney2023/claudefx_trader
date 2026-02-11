"""
Activity feed routes for the API.

Provides endpoints for:
- Getting recent activities
- Adding activities (internal use)
- Activity counts for notification bell
"""

from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


# In-memory activity storage
_activity_feed: List[dict] = []
MAX_ACTIVITIES = 100


class Activity(BaseModel):
    """Activity record."""
    id: str
    timestamp: str
    type: str  # 'trade_opened', 'trade_closed', 'signal_generated', 'error', 'info', 'warning'
    symbol: Optional[str] = None
    message: str
    details: Optional[dict] = None


class ActivityCountResponse(BaseModel):
    """Response for unread activity count."""
    count: int
    recent_count: int  # Activities in last hour


def add_activity(
    activity_type: str,
    message: str,
    symbol: str = None,
    details: dict = None
):
    """
    Add an activity to the feed.
    
    Args:
        activity_type: Type of activity ('trade_opened', 'trade_closed', 'signal_generated', 'error', 'info')
        message: Human-readable message
        symbol: Trading symbol (optional)
        details: Additional details dict (optional)
    """
    global _activity_feed
    
    activity = {
        "id": str(len(_activity_feed) + 1),
        "timestamp": datetime.now().isoformat(),
        "type": activity_type,
        "symbol": symbol,
        "message": message,
        "details": details or {}
    }
    
    _activity_feed.insert(0, activity)
    
    # Trim to max size
    if len(_activity_feed) > MAX_ACTIVITIES:
        _activity_feed = _activity_feed[:MAX_ACTIVITIES]
    
    print(f"[ACTIVITY] #{activity['id']} [{activity_type}] {message} (feed size: {len(_activity_feed)})", flush=True)
    logger.debug(f"Activity added: [{activity_type}] {message}")


def get_activities(limit: int = 20) -> List[dict]:
    """Get recent activities."""
    return _activity_feed[:limit]


@router.get("/activities", response_model=List[Activity])
async def get_activities_endpoint(
    limit: int = Query(20, ge=1, le=100, description="Number of activities to return"),
    activity_type: Optional[str] = Query(None, description="Filter by activity type")
):
    """
    Get recent activities for the activity feed.
    
    Activities include:
    - trade_opened: A new trade was opened
    - trade_closed: A trade was closed
    - signal_generated: Claude generated a trading signal
    - error: An error occurred
    - warning: A warning condition
    - info: Informational message
    """
    activities = _activity_feed[:limit]
    
    if activity_type:
        activities = [a for a in activities if a.get('type') == activity_type]
    
    return [
        Activity(
            id=a.get('id', ''),
            timestamp=a.get('timestamp', ''),
            type=a.get('type', 'info'),
            symbol=a.get('symbol'),
            message=a.get('message', ''),
            details=a.get('details')
        )
        for a in activities
    ]


@router.get("/activities/count", response_model=ActivityCountResponse)
async def get_activity_count():
    """
    Get count of activities (for notification badge).
    
    Returns total count and count from last hour.
    """
    one_hour_ago = datetime.now() - timedelta(hours=1)
    
    recent_count = 0
    for a in _activity_feed:
        try:
            ts = datetime.fromisoformat(a.get('timestamp', ''))
            if ts > one_hour_ago:
                recent_count += 1
        except (ValueError, TypeError):
            pass
    
    return ActivityCountResponse(
        count=len(_activity_feed),
        recent_count=recent_count
    )


@router.delete("/activities")
async def clear_activities():
    """Clear all activities."""
    global _activity_feed
    _activity_feed = []
    return {"message": "Activities cleared"}
