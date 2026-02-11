"""
Integration Tests for Trading Bot.

Tests the interaction between multiple components working together.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import os


class TestBotInitialization:
    """Test bot initialization and startup."""
    
    def test_instance_lock_acquired(self):
        """Test that instance lock is acquired on startup."""
        from trading_bot.utils.instance_lock import InstanceLock
        
        lock_file = "data/test_lock.lock"
        lock = InstanceLock(lock_file)
        
        try:
            # First acquisition should succeed
            assert lock.acquire() == True
            
            # Release
            lock.release()
        finally:
            if os.path.exists(lock_file):
                os.remove(lock_file)
    
    def test_state_persistence_roundtrip(self):
        """Test saving and loading state."""
        from trading_bot.utils.state_persistence import StatePersistence
        
        test_file = "data/test_state.json"
        
        try:
            persistence = StatePersistence(test_file)
            
            # Save some data
            persistence.save_streaks(5, 2)
            persistence.save_daily_stats(10, 150.50, "2026-01-30")
            
            # Create new instance to test loading
            persistence2 = StatePersistence(test_file)
            
            # Verify data loaded
            win, loss = persistence2.load_streaks()
            assert win == 5
            assert loss == 2
            
            daily = persistence2.load_daily_stats()
            assert daily['trades'] == 10
            assert daily['pnl'] == 150.50
            
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)


class TestMarketHours:
    """Test market hours detection."""
    
    def test_forex_closed_saturday(self):
        """Test forex market is closed on Saturday."""
        from trading_bot.utils.market_hours import is_market_open
        
        # Saturday at noon UTC
        saturday = datetime(2026, 1, 31, 12, 0, 0)  # Saturday
        
        is_open, reason = is_market_open("EURUSD", saturday)
        assert is_open == False
        assert "Saturday" in reason
    
    def test_crypto_always_open(self):
        """Test crypto markets are always open."""
        from trading_bot.utils.market_hours import is_market_open
        
        # Saturday at noon UTC
        saturday = datetime(2026, 1, 31, 12, 0, 0)
        
        is_open, reason = is_market_open("BTCUSD", saturday)
        assert is_open == True
        assert "24/7" in reason
    
    def test_forex_open_weekday(self):
        """Test forex market is open on weekday."""
        from trading_bot.utils.market_hours import is_market_open
        
        # Wednesday at noon UTC
        wednesday = datetime(2026, 1, 28, 12, 0, 0)
        
        is_open, reason = is_market_open("EURUSD", wednesday)
        assert is_open == True
    
    def test_avoid_friday_evening_trades(self):
        """Test that new trades are avoided on Friday evening."""
        from trading_bot.utils.market_hours import should_avoid_new_trades
        
        # Friday 8pm UTC
        friday_evening = datetime(2026, 1, 30, 20, 0, 0)
        
        should_avoid, reason = should_avoid_new_trades("EURUSD", friday_evening)
        assert should_avoid == True
        assert "Friday" in reason


class TestServiceIntegration:
    """Test integration between services."""
    
    def test_news_service_blackout_detection(self):
        """Test that news service can detect blackout periods."""
        from trading_bot.services.news_service import NewsService
        
        news_service = NewsService()
        
        # Test the blackout detection method exists and returns tuple
        is_blackout, reason = news_service.is_blackout_period()
        
        assert isinstance(is_blackout, bool)
        assert isinstance(reason, str)
    
    def test_correlation_service_initialization(self):
        """Test correlation service initializes correctly."""
        from trading_bot.services.correlation_service import CorrelationService
        
        corr_service = CorrelationService()
        
        # Test it has expected method
        assert hasattr(corr_service, 'should_block_trade')
    
    def test_scaling_manager_initialization(self):
        """Test scaling manager initializes correctly."""
        from trading_bot.services.scaling_manager import ScalingManager, TradingMode
        
        manager = ScalingManager(starting_equity=1000)
        
        # Initial mode should be set
        assert manager.current_mode in TradingMode
        
        # Record a trade using correct API
        manager.record_trade({'profit_loss': -50, 'r_multiple': -1.0, 'symbol': 'EURUSD', 'direction': 'long'})
        
        # Get status and check performance tracking
        status = manager.get_status(950)
        assert 'current_mode' in status
        assert 'recent_performance' in status
    
    def test_session_analytics_initialization(self):
        """Test session analytics initializes correctly."""
        from trading_bot.services.session_analytics import SessionAnalytics, TradingSession
        
        analytics = SessionAnalytics()
        
        # Record a trade using correct API
        analytics.record_trade(
            symbol='EURUSD',
            direction='long',
            profit_loss=100.0,
            r_multiple=2.0
        )
        
        # Check it was recorded in current session
        current = analytics.get_current_session()
        stats = analytics.get_session_stats(current)
        assert stats is not None
    
    def test_goal_tracker_initialization(self):
        """Test goal tracker initializes correctly."""
        from trading_bot.services.goal_tracker import GoalTracker
        
        tracker = GoalTracker(starting_equity=1000, target_equity=100000)
        
        # Add equity snapshot
        tracker.add_snapshot(2500)
        
        # Check progress
        progress = tracker.calculate_progress(2500)
        assert progress['percent'] > 0


class TestPositionManagement:
    """Test position management integration."""
    
    def test_position_manager_initialization(self):
        """Test position manager initializes correctly."""
        from trading_bot.execution.position_manager import PositionManager, Position
        
        pm = PositionManager()
        
        # Add a position
        position = Position(
            ticket=12345,
            symbol='EURUSD',
            direction='long',
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.1000,
            volume=0.1,
            open_time=datetime.now()
        )
        pm.add_position(position)
        
        # Get the position
        retrieved = pm.get_position(12345)
        assert retrieved is not None
        assert retrieved.symbol == 'EURUSD'
    
    def test_position_pnl_tracking(self):
        """Test position P&L tracking."""
        from trading_bot.execution.position_manager import PositionManager, Position
        
        pm = PositionManager()
        
        position = Position(
            ticket=12345,
            symbol='EURUSD',
            direction='long',
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.1000,
            volume=0.1,
            open_time=datetime.now()
        )
        pm.add_position(position)
        
        # Update price
        pm.update_price(12345, 1.0900)
        
        # Position should have updated current price
        updated = pm.get_position(12345)
        assert updated.current_price == 1.0900


class TestScalingPositionSizer:
    """Test dynamic position sizing integration."""
    
    def test_position_sizer_initialization(self):
        """Test position sizer initializes correctly."""
        from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer
        
        sizer = ScalingPositionSizer()
        
        # Get tier info
        tier_info = sizer.get_tier_info(1000)
        
        # Should have current_tier
        assert 'current_tier' in tier_info
    
    def test_position_size_calculation(self):
        """Test position size calculation."""
        from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer
        
        sizer = ScalingPositionSizer()
        
        # Calculate a position size using correct API
        result = sizer.calculate_position_size(
            equity=1000,
            entry_price=1.0850,
            stop_loss=1.0800,
            symbol='EURUSD'
        )
        
        # Should return PositionSizeResult
        assert hasattr(result, 'lots')
        assert result.lots > 0


class TestCryptoAnalysis:
    """Test crypto analysis integration."""
    
    def test_crypto_analyzer_initialization(self):
        """Test crypto analyzer initializes correctly."""
        from trading_bot.analysis.crypto_analysis import CryptoAnalyzer
        
        analyzer = CryptoAnalyzer()
        
        # Check it has expected methods
        assert hasattr(analyzer, 'get_key_levels')
        assert hasattr(analyzer, 'get_config')
    
    def test_crypto_key_levels(self):
        """Test crypto key levels retrieval."""
        from trading_bot.analysis.crypto_analysis import CryptoAnalyzer
        
        analyzer = CryptoAnalyzer()
        
        levels = analyzer.get_key_levels('XRPUSD')
        
        # Should have support and resistance fields
        assert hasattr(levels, 'support_1')
        assert hasattr(levels, 'resistance_1')


class TestSilverAnalysis:
    """Test silver analysis integration."""
    
    def test_silver_analyzer_initialization(self):
        """Test silver analyzer initializes correctly."""
        from trading_bot.analysis.silver_analysis import SilverAnalyzer
        
        analyzer = SilverAnalyzer()
        
        # Check it has expected attributes
        assert hasattr(analyzer, 'key_levels')
    
    def test_silver_key_levels(self):
        """Test silver key levels are defined."""
        from trading_bot.analysis.silver_analysis import SilverAnalyzer, SilverKeyLevels
        
        analyzer = SilverAnalyzer()
        
        # Key levels should be SilverKeyLevels dataclass
        assert isinstance(analyzer.key_levels, SilverKeyLevels)
        assert analyzer.key_levels.target_1 > 0


class TestAlertConfiguration:
    """Test alert configuration."""
    
    def test_alert_config_initialization(self):
        """Test alert config initializes correctly."""
        from trading_bot.utils.alert_config import AlertConfig, AlertThresholds
        
        test_file = "data/test_alert_config.json"
        
        try:
            config = AlertConfig(test_file)
            
            # Should have default thresholds
            assert config.thresholds.profit_alert_usd == 100.0
            assert config.thresholds.loss_alert_usd == -50.0
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)
    
    def test_alert_threshold_update(self):
        """Test updating alert thresholds."""
        from trading_bot.utils.alert_config import AlertConfig
        
        test_file = "data/test_alert_config2.json"
        
        try:
            config = AlertConfig(test_file)
            
            # Update a threshold
            config.update(profit_alert_usd=200.0)
            
            # Verify update
            assert config.thresholds.profit_alert_usd == 200.0
            
            # Reload and verify persistence
            config2 = AlertConfig(test_file)
            assert config2.thresholds.profit_alert_usd == 200.0
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)
    
    def test_should_alert_functions(self):
        """Test alert condition checking."""
        from trading_bot.utils.alert_config import AlertConfig
        
        test_file = "data/test_alert_config3.json"
        
        try:
            config = AlertConfig(test_file)
            
            # Test profit alert
            assert config.should_alert_profit(150.0) == True
            assert config.should_alert_profit(50.0) == False
            
            # Test loss alert
            assert config.should_alert_loss(-100.0) == True
            assert config.should_alert_loss(-25.0) == False
            
            # Test streak alert
            result = config.should_alert_streak(5, 0)
            assert result is not None  # Should alert for 5 wins
            
            result = config.should_alert_streak(0, 3)
            assert result is not None  # Should alert for 3 losses
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)


class TestEndToEndScenarios:
    """End-to-end scenario tests."""
    
    def test_trade_lifecycle_services(self):
        """Test services work together for trade lifecycle."""
        from trading_bot.execution.position_manager import PositionManager, Position
        from trading_bot.services.session_analytics import SessionAnalytics
        from trading_bot.services.scaling_manager import ScalingManager
        from trading_bot.services.goal_tracker import GoalTracker
        
        # Initialize services
        pm = PositionManager()
        session_analytics = SessionAnalytics()
        scaling_manager = ScalingManager(starting_equity=1000)
        goal_tracker = GoalTracker(starting_equity=1000, target_equity=100000)
        
        # 1. Open position
        position = Position(
            ticket=99999,
            symbol='EURUSD',
            direction='long',
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            volume=0.1,
            open_time=datetime.now()
        )
        pm.add_position(position)
        
        # 2. Position exists
        assert pm.get_position(99999) is not None
        
        # 3. Record in analytics
        session_analytics.record_trade(
            symbol='EURUSD',
            direction='long',
            profit_loss=100.0,
            r_multiple=2.0
        )
        
        # 4. Update scaling manager
        scaling_manager.record_trade({
            'profit_loss': 100.0, 
            'r_multiple': 2.0, 
            'symbol': 'EURUSD', 
            'direction': 'long'
        })
        
        # 5. Update goal tracker
        goal_tracker.add_snapshot(1100)
        
        # Verify scaling manager recorded trade
        status = scaling_manager.get_status(1100)
        assert 'recent_performance' in status
        
        # Verify goal tracker updated
        progress = goal_tracker.calculate_progress(1100)
        assert progress['percent'] > 0
        
        # Remove position
        pm.remove_position(99999)
        assert pm.get_position(99999) is None
    
    def test_drawdown_tracking(self):
        """Test drawdown is tracked correctly."""
        from trading_bot.services.scaling_manager import ScalingManager
        
        manager = ScalingManager(starting_equity=1000)
        
        # Simulate losing trades
        manager.record_trade({'profit_loss': -30, 'r_multiple': -1.0, 'symbol': 'EURUSD', 'direction': 'long'})
        manager.record_trade({'profit_loss': -30, 'r_multiple': -1.0, 'symbol': 'GBPUSD', 'direction': 'long'})
        
        # Get status - should track drawdown
        status = manager.get_status(940)
        assert 'current_mode' in status
        assert 'daily_drawdown' in status
        # Daily drawdown should be positive (we're down)
        assert status['daily_drawdown'] >= 0
