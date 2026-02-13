"""
Goal Tracker Service.

Tracks progress from starting equity to target ($1K to $100K).
Provides projections, milestones, and motivation metrics.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import math

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EquitySnapshot:
    """Equity snapshot at a point in time."""
    equity: float
    timestamp: datetime


class GoalTracker:
    """
    Tracks equity goal progress toward target equity.
    
    Features:
    - Progress calculation
    - Milestone tracking
    - Completion projections
    - Compound growth calculations
    """
    
    DEFAULT_MILESTONES = [250, 500, 750, 1000, 2500, 5000, 10000]
    
    def __init__(
        self,
        starting_equity: float = 1000,
        target_equity: float = 10000
    ):
        """
        Initialize goal tracker.
        
        Args:
            starting_equity: Starting equity (default $1,000)
            target_equity: Target equity (default $100,000)
        """
        self.starting_equity = starting_equity
        self.target_equity = target_equity
        
        # Equity history
        self._history: List[EquitySnapshot] = []
        self.equity_history: List[EquitySnapshot] = self._history  # Alias for state_persistence
        
        logger.info(f"Goal tracker initialized: ${starting_equity:,.0f} -> ${target_equity:,.0f}")
    
    def add_equity_snapshot(self, equity: float, timestamp: Optional[datetime] = None):
        """Add an equity snapshot to the history."""
        snapshot = EquitySnapshot(
            equity=equity,
            timestamp=timestamp or datetime.now()
        )
        self._history.append(snapshot)
        
        # Keep last 1000 snapshots
        if len(self._history) > 1000:
            self._history[:] = self._history[-1000:]
    
    def calculate_progress(self, current_equity: float) -> Dict[str, Any]:
        """
        Calculate progress toward goal.
        
        Uses logarithmic scale for compound growth representation.
        """
        if current_equity <= self.starting_equity:
            return {
                'percent': 0.0,
                'current': current_equity,
                'remaining': self.target_equity - current_equity,
                'multiple_achieved': 1.0
            }
        
        if current_equity >= self.target_equity:
            return {
                'percent': 100.0,
                'current': current_equity,
                'remaining': 0,
                'multiple_achieved': current_equity / self.starting_equity
            }
        
        # Logarithmic progress (better for compound growth)
        log_start = math.log(self.starting_equity)
        log_target = math.log(self.target_equity)
        log_current = math.log(current_equity)
        
        percent = ((log_current - log_start) / (log_target - log_start)) * 100
        
        return {
            'percent': percent,
            'current': current_equity,
            'remaining': self.target_equity - current_equity,
            'multiple_achieved': current_equity / self.starting_equity
        }
    
    def get_milestones(self) -> List[int]:
        """Get milestone levels."""
        return self.DEFAULT_MILESTONES.copy()
    
    def get_milestone_status(self, current_equity: float) -> Dict[int, bool]:
        """Get status of each milestone (achieved or not)."""
        return {
            milestone: current_equity >= milestone
            for milestone in self.DEFAULT_MILESTONES
        }
    
    def get_next_milestone(self, current_equity: float) -> Optional[int]:
        """Get the next milestone to achieve."""
        for milestone in self.DEFAULT_MILESTONES:
            if current_equity < milestone:
                return milestone
        return None
    
    def project_completion(
        self,
        current_equity: float,
        monthly_return: float
    ) -> Dict[str, Any]:
        """
        Project completion date based on monthly return rate.
        
        Args:
            current_equity: Current equity
            monthly_return: Monthly return rate (e.g., 0.10 for 10%)
            
        Returns:
            Projection with days and date
        """
        if current_equity >= self.target_equity:
            return {
                'days': 0,
                'months': 0,
                'date': datetime.now(),
                'achieved': True
            }
        
        if monthly_return <= 0:
            return {
                'days': float('inf'),
                'months': float('inf'),
                'date': None,
                'achieved': False
            }
        
        # Calculate months needed: current * (1 + r)^n = target
        # n = log(target/current) / log(1 + r)
        multiple_needed = self.target_equity / current_equity
        months_needed = math.log(multiple_needed) / math.log(1 + monthly_return)
        days_needed = int(months_needed * 30)
        
        completion_date = datetime.now() + timedelta(days=days_needed)
        
        return {
            'days': days_needed,
            'months': round(months_needed, 1),
            'date': completion_date,
            'achieved': False
        }
    
    def calculate_required_return(
        self,
        current_equity: float,
        target_days: int
    ) -> Dict[str, float]:
        """
        Calculate required return rate to hit target in given days.
        
        Args:
            current_equity: Current equity
            target_days: Days to reach target
            
        Returns:
            Required daily and monthly returns
        """
        if current_equity >= self.target_equity:
            return {'daily_percent': 0.0, 'monthly_percent': 0.0}
        
        multiple_needed = self.target_equity / current_equity
        
        # daily_rate^days = multiple_needed
        # daily_rate = multiple_needed^(1/days)
        daily_rate = math.pow(multiple_needed, 1 / target_days) - 1
        monthly_rate = math.pow(1 + daily_rate, 30) - 1
        
        return {
            'daily_percent': daily_rate * 100,
            'monthly_percent': monthly_rate * 100
        }
    
    def calculate_compound_growth(
        self,
        starting: float,
        monthly_return: float,
        months: int
    ) -> float:
        """
        Calculate compound growth over time.
        
        Args:
            starting: Starting equity
            monthly_return: Monthly return rate
            months: Number of months
            
        Returns:
            Final equity
        """
        return starting * math.pow(1 + monthly_return, months)
    
    def add_snapshot(self, equity: float, timestamp: Optional[datetime] = None):
        """Add an equity snapshot to history."""
        if timestamp is None:
            timestamp = datetime.now()
        
        self._history.append(EquitySnapshot(equity=equity, timestamp=timestamp))
        self._history.sort(key=lambda x: x.timestamp)
    
    def get_history(self) -> List[EquitySnapshot]:
        """Get equity history."""
        return self._history.copy()
    
    def calculate_returns(self) -> Dict[str, float]:
        """Calculate returns from equity history."""
        if len(self._history) < 2:
            return {'total_return': 0.0, 'annualized_return': 0.0}
        
        first = self._history[0]
        last = self._history[-1]
        
        total_return = (last.equity - first.equity) / first.equity
        
        # Annualize
        days = (last.timestamp - first.timestamp).days or 1
        annualized = math.pow(1 + total_return, 365 / days) - 1
        
        return {
            'total_return': total_return,
            'annualized_return': annualized,
            'days': days
        }
    
    def get_summary(self, current_equity: float) -> Dict[str, Any]:
        """Get comprehensive goal summary."""
        progress = self.calculate_progress(current_equity)
        milestone_status = self.get_milestone_status(current_equity)
        next_milestone = self.get_next_milestone(current_equity)
        
        # Projection with 10% monthly (moderate estimate)
        projection_10 = self.project_completion(current_equity, 0.10)
        projection_15 = self.project_completion(current_equity, 0.15)
        
        achieved_milestones = [m for m, achieved in milestone_status.items() if achieved]
        
        return {
            'starting_equity': self.starting_equity,
            'target_equity': self.target_equity,
            'current_equity': current_equity,
            'progress': progress,
            'milestones': {
                'achieved': achieved_milestones,
                'next': next_milestone,
                'all': self.get_milestones()
            },
            'projections': {
                'conservative_10pct': projection_10,
                'aggressive_15pct': projection_15
            }
        }
