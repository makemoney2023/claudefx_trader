"""
API routes for Session Analytics.
"""

from fastapi import APIRouter
from typing import Optional, List, Dict, Any

from ...services.session_analytics import SessionAnalytics, TradingSession
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global instance (will be set by main.py)
_session_analytics: Optional[SessionAnalytics] = None


def set_session_analytics(analytics: SessionAnalytics):
    """Set the session analytics instance."""
    global _session_analytics
    _session_analytics = analytics


def get_analytics() -> SessionAnalytics:
    """Get or create session analytics instance."""
    global _session_analytics
    if _session_analytics is None:
        _session_analytics = SessionAnalytics()
    return _session_analytics


@router.get("/current")
async def get_current_session():
    """
    Get the current trading session.
    """
    analytics = get_analytics()
    session = analytics.get_current_session()
    
    return {
        "session": session.value,
        "is_overlap": session == TradingSession.LONDON_NY_OVERLAP,
        "is_off_hours": session == TradingSession.OFF_HOURS
    }


@router.get("/stats")
async def get_session_stats():
    """
    Get performance statistics for all sessions.
    """
    analytics = get_analytics()
    return analytics.get_all_stats()


@router.get("/stats/{session_name}")
async def get_specific_session_stats(session_name: str):
    """
    Get stats for a specific session.
    """
    analytics = get_analytics()
    
    session_map = {
        'asian': TradingSession.ASIAN,
        'london': TradingSession.LONDON,
        'new_york': TradingSession.NEW_YORK,
        'overlap': TradingSession.LONDON_NY_OVERLAP,
        'off_hours': TradingSession.OFF_HOURS
    }
    
    session = session_map.get(session_name.lower())
    if not session:
        return {"error": f"Unknown session: {session_name}"}
    
    stats = analytics.get_session_stats(session)
    return stats.to_dict()


@router.get("/best")
async def get_best_session():
    """
    Get the best performing session.
    """
    analytics = get_analytics()
    best = analytics.get_best_session()
    
    if not best:
        return {
            "session": None,
            "message": "Not enough data to determine best session"
        }
    
    stats = analytics.get_session_stats(best)
    return {
        "session": best.value,
        "stats": stats.to_dict()
    }


@router.get("/worst")
async def get_worst_session():
    """
    Get the worst performing session.
    """
    analytics = get_analytics()
    worst = analytics.get_worst_session()
    
    if not worst:
        return {
            "session": None,
            "message": "Not enough data to determine worst session"
        }
    
    stats = analytics.get_session_stats(worst)
    return {
        "session": worst.value,
        "stats": stats.to_dict()
    }


@router.get("/matrix")
async def get_symbol_session_matrix():
    """
    Get performance matrix: symbol x session.
    """
    analytics = get_analytics()
    return {
        "matrix": analytics.get_symbol_session_matrix()
    }


@router.get("/recommendations")
async def get_session_recommendations():
    """
    Get trading recommendations based on session performance.
    """
    analytics = get_analytics()
    return {
        "recommendations": analytics.get_recommendations()
    }


@router.get("/summary")
async def get_session_summary():
    """
    Get comprehensive session analytics summary.
    """
    analytics = get_analytics()
    return analytics.get_summary()
