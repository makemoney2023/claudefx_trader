"""
Tests for Goal Tracker Service.

Following TDD - tests for equity goal tracking ($1K to $100K).
"""

import pytest
from datetime import datetime, timedelta


class TestGoalTracker:
    """Tests for GoalTracker class."""
    
    def test_initialization(self, goal_tracker):
        """Test goal tracker initialization."""
        assert goal_tracker is not None
        assert goal_tracker.starting_equity == 1000
        assert goal_tracker.target_equity == 100000
    
    def test_calculate_progress(self, goal_tracker):
        """Test progress calculation."""
        # At starting equity
        progress = goal_tracker.calculate_progress(current_equity=1000)
        assert progress['percent'] == 0.0
        
        # Halfway in log scale (geometric)
        # From 1000 to 100000 is 100x, sqrt(100) = 10x = $10,000
        progress = goal_tracker.calculate_progress(current_equity=10000)
        assert progress['percent'] > 0 and progress['percent'] < 100
        
        # At target
        progress = goal_tracker.calculate_progress(current_equity=100000)
        assert progress['percent'] == 100.0
    
    def test_milestone_tracking(self, goal_tracker):
        """Test milestone identification."""
        milestones = goal_tracker.get_milestones()
        
        # Should have key milestones
        assert 5000 in milestones  # $5K
        assert 10000 in milestones  # $10K
        assert 25000 in milestones  # $25K
        assert 50000 in milestones  # $50K
        assert 100000 in milestones  # $100K
    
    def test_milestone_status(self, goal_tracker):
        """Test which milestones are achieved."""
        status = goal_tracker.get_milestone_status(current_equity=12000)
        
        # Should show 5K and 10K as achieved
        assert status[5000] == True
        assert status[10000] == True
        assert status[25000] == False


class TestGoalProjections:
    """Tests for goal projection calculations."""
    
    def test_projected_completion_date(self, goal_tracker):
        """Test projected completion date calculation."""
        # With 10% monthly returns, how long to reach $100K?
        projection = goal_tracker.project_completion(
            current_equity=5000,
            monthly_return=0.10  # 10%
        )
        
        assert 'days' in projection
        assert 'date' in projection
        assert projection['days'] > 0
    
    def test_required_daily_gain(self, goal_tracker):
        """Test required daily gain to meet target."""
        # Given current equity, what daily gain needed?
        required = goal_tracker.calculate_required_return(
            current_equity=5000,
            target_days=365  # 1 year
        )
        
        assert 'daily_percent' in required
        assert 'monthly_percent' in required
        assert required['daily_percent'] > 0
    
    def test_compound_growth_calculation(self, goal_tracker):
        """Test compound growth projection."""
        # Starting at $1000 with 10% monthly
        final = goal_tracker.calculate_compound_growth(
            starting=1000,
            monthly_return=0.10,
            months=24  # 2 years
        )
        
        # Should be around $1000 * 1.1^24 = ~$9,850
        assert final > 9000
        assert final < 11000


class TestEquityTracking:
    """Tests for equity history tracking."""
    
    def test_add_equity_snapshot(self, goal_tracker):
        """Test adding equity snapshots."""
        goal_tracker.add_snapshot(equity=1000, timestamp=datetime.now() - timedelta(days=30))
        goal_tracker.add_snapshot(equity=1100, timestamp=datetime.now() - timedelta(days=20))
        goal_tracker.add_snapshot(equity=1200, timestamp=datetime.now())
        
        history = goal_tracker.get_history()
        assert len(history) == 3
    
    def test_calculate_returns(self, goal_tracker):
        """Test return calculation from history."""
        goal_tracker.add_snapshot(equity=1000, timestamp=datetime.now() - timedelta(days=30))
        goal_tracker.add_snapshot(equity=1100, timestamp=datetime.now())
        
        returns = goal_tracker.calculate_returns()
        
        assert 'total_return' in returns
        assert returns['total_return'] == pytest.approx(0.10, rel=0.01)  # 10%
