"""
Goal Tracker API routes.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from ...services.goal_tracker import GoalTracker
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Global goal tracker - will sync with bot's instance
_goal_tracker = None


def set_goal_tracker(tracker: GoalTracker):
    """Set the goal tracker instance from the main bot."""
    global _goal_tracker
    _goal_tracker = tracker
    logger.info("Goal tracker synced from bot")


def get_goal_tracker() -> GoalTracker:
    """Get the goal tracker instance."""
    global _goal_tracker
    if _goal_tracker is None:
        _goal_tracker = GoalTracker(starting_equity=1000, target_equity=100000)
    return _goal_tracker


# For backwards compatibility
goal_tracker = property(lambda self: get_goal_tracker())


class GoalProgressResponse(BaseModel):
    """Goal progress response."""
    percent: float
    current: float
    remaining: float
    multiple_achieved: float


class MilestoneResponse(BaseModel):
    """Milestone information."""
    achieved: List[int]
    next: Optional[int]
    all: List[int]


class ProjectionResponse(BaseModel):
    """Projection information."""
    days: int
    months: float
    date: Optional[str]
    achieved: bool


@router.get("/progress")
async def get_progress(current_equity: float = 1000):
    """
    Get progress toward equity goal.
    
    Args:
        current_equity: Current account equity
    """
    tracker = get_goal_tracker()
    progress = tracker.calculate_progress(current_equity)
    return progress


@router.get("/summary")
async def get_summary(current_equity: float = 1000):
    """
    Get complete goal tracking summary.
    
    Includes progress, milestones, and projections.
    """
    tracker = get_goal_tracker()
    return tracker.get_summary(current_equity)


@router.get("/milestones")
async def get_milestones(current_equity: float = 1000):
    """Get milestone status."""
    tracker = get_goal_tracker()
    status = tracker.get_milestone_status(current_equity)
    next_milestone = tracker.get_next_milestone(current_equity)
    
    achieved = [m for m, s in status.items() if s]
    
    return {
        'achieved': achieved,
        'next': next_milestone,
        'all': tracker.get_milestones(),
        'status': status
    }


@router.get("/projection")
async def get_projection(
    current_equity: float = 1000,
    monthly_return: float = 0.10
):
    """
    Get projected completion date.
    
    Args:
        current_equity: Current equity
        monthly_return: Expected monthly return (default 10%)
    """
    tracker = get_goal_tracker()
    projection = tracker.project_completion(current_equity, monthly_return)
    
    return {
        'current_equity': current_equity,
        'monthly_return_percent': monthly_return * 100,
        'projection': {
            'days': projection['days'],
            'months': projection['months'],
            'date': projection['date'].isoformat() if projection['date'] else None,
            'achieved': projection['achieved']
        }
    }


@router.get("/required-return")
async def get_required_return(
    current_equity: float = 1000,
    target_days: int = 365
):
    """
    Get required return rate to hit target in given time.
    
    Args:
        current_equity: Current equity
        target_days: Days to reach target
    """
    tracker = get_goal_tracker()
    required = tracker.calculate_required_return(current_equity, target_days)
    
    return {
        'current_equity': current_equity,
        'target_equity': tracker.target_equity,
        'target_days': target_days,
        'required': {
            'daily_percent': round(required['daily_percent'], 3),
            'monthly_percent': round(required['monthly_percent'], 2)
        }
    }


@router.post("/snapshot")
async def add_equity_snapshot(equity: float):
    """Add an equity snapshot for tracking."""
    from datetime import datetime
    tracker = get_goal_tracker()
    tracker.add_snapshot(equity)
    
    return {
        'message': 'Snapshot added',
        'equity': equity,
        'timestamp': datetime.now().isoformat(),
        'total_snapshots': len(tracker.get_history())
    }


@router.get("/history")
async def get_equity_history():
    """Get equity history."""
    tracker = get_goal_tracker()
    history = tracker.get_history()
    
    return {
        'history': [
            {'equity': s.equity, 'timestamp': s.timestamp.isoformat()}
            for s in history
        ],
        'total': len(history),
        'returns': tracker.calculate_returns() if len(history) >= 2 else None
    }


@router.get("/compound-growth")
async def calculate_compound_growth(
    starting: float = 1000,
    monthly_return: float = 0.10,
    months: int = 24
):
    """
    Calculate compound growth projection.
    
    Args:
        starting: Starting equity
        monthly_return: Monthly return rate
        months: Number of months
    """
    tracker = get_goal_tracker()
    final = tracker.calculate_compound_growth(starting, monthly_return, months)
    
    # Calculate monthly equity curve
    curve = []
    current = starting
    for month in range(months + 1):
        curve.append({
            'month': month,
            'equity': round(current, 2)
        })
        current *= (1 + monthly_return)
    
    return {
        'starting': starting,
        'monthly_return': monthly_return * 100,
        'months': months,
        'final': round(final, 2),
        'total_return': round((final / starting - 1) * 100, 1),
        'curve': curve
    }
