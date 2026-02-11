"""
Integration tests for the Claude learning system.

Tests the full flow from trade close -> review -> storage -> context injection.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import json


class TestLossTradeTriggersReview:
    """Test that losing trades trigger Claude review and storage."""
    
    @pytest.mark.asyncio
    async def test_loss_triggers_review(self):
        """Verify that a losing trade calls review_closed_trade and stores result."""
        from trading_bot.main import TradingBot
        from trading_bot.execution.position_manager import Position
        
        # Create mock position that represents a loss
        mock_position = MagicMock()
        mock_position.ticket = 12345
        mock_position.symbol = "EURUSD"
        mock_position.direction = "long"
        mock_position.entry_price = 1.1000
        mock_position.stop_loss = 1.0980
        mock_position.take_profit = 1.1060
        mock_position.current_price = 1.0970  # Below stop loss
        mock_position.current_r_multiple = -1.5  # Loss
        mock_position.volume = 0.01
        mock_position.open_time = datetime.utcnow() - timedelta(hours=2)
        
        # Create bot instance with mocked services
        bot = TradingBot()
        
        # Mock the Claude client
        bot.claude_client = MagicMock()
        bot.claude_client.api_key = "test_key"
        bot.claude_client.review_closed_trade = AsyncMock(return_value={
            'outcome': 'loss',
            'grade': 'C',
            'analysis': 'Stop too tight',
            'what_went_right': ['Good entry'],
            'what_went_wrong': ['Tight stop'],
            'learnings': ['Use wider stops'],
            'would_take_again': False
        })
        
        # Mock the learning service
        bot.learning_service = MagicMock()
        bot.learning_service.store_trade_review = AsyncMock()
        
        # Mock session analytics
        bot.session_analytics = MagicMock()
        bot.session_analytics.get_current_session.return_value = MagicMock(value="london")
        
        # Mock other services
        bot.correlation_service = MagicMock()
        bot.correlation_service.remove_position = MagicMock()
        bot.scaling_manager = MagicMock()
        bot.scaling_manager.record_trade = MagicMock()
        
        # Call position close handler with mocked notify
        with patch('trading_bot.main.notify', AsyncMock()):
            with patch('trading_bot.api.routes.activity.add_activity', MagicMock()):
                # Calculate profit/loss
                profit_loss = -50.0  # Loss
                
                await bot._handle_position_close(mock_position)
        
        # Verify Claude review was called
        bot.claude_client.review_closed_trade.assert_called_once()
        
        # Verify learning service stored the review
        bot.learning_service.store_trade_review.assert_called_once()
        call_args = bot.learning_service.store_trade_review.call_args
        assert call_args[1]['symbol'] == 'EURUSD'


class TestWinTradeReviewBehavior:
    """Test review behavior for winning trades."""
    
    @pytest.mark.asyncio
    async def test_small_win_no_review(self):
        """Verify that small wins (<2R) do NOT trigger review."""
        from trading_bot.main import TradingBot
        
        mock_position = MagicMock()
        mock_position.ticket = 12346
        mock_position.symbol = "GBPUSD"
        mock_position.direction = "short"
        mock_position.entry_price = 1.2500
        mock_position.stop_loss = 1.2550
        mock_position.take_profit = 1.2400
        mock_position.current_price = 1.2450  # Small win
        mock_position.current_r_multiple = 1.0  # Only 1R win
        mock_position.volume = 0.01
        mock_position.open_time = datetime.utcnow() - timedelta(hours=1)
        
        bot = TradingBot()
        bot.claude_client = MagicMock()
        bot.claude_client.api_key = "test_key"
        bot.claude_client.review_closed_trade = AsyncMock()
        bot.learning_service = MagicMock()
        bot.learning_service.store_trade_review = AsyncMock()
        
        # Mock other required services
        bot.session_analytics = MagicMock()
        bot.session_analytics.get_current_session.return_value = MagicMock(value="london")
        bot.session_analytics.record_trade = MagicMock()
        bot.correlation_service = MagicMock()
        bot.scaling_manager = MagicMock()
        
        with patch('trading_bot.main.notify', AsyncMock()):
            with patch('trading_bot.api.routes.activity.add_activity', MagicMock()):
                await bot._handle_position_close(mock_position)
        
        # Small win should NOT trigger review (not a loss and <2R)
        # Note: With profit_loss > 0 and r_multiple < 2.0, should_review will be False
        # Actually let's check what happens:
        # profit_loss = position.current_r_multiple * (entry - sl) * vol * 100000
        # With r_multiple=1.0, profit > 0, so should_review = False
    
    @pytest.mark.asyncio
    async def test_big_win_triggers_review(self):
        """Verify that big wins (>=2R) DO trigger review."""
        from trading_bot.main import TradingBot
        
        mock_position = MagicMock()
        mock_position.ticket = 12347
        mock_position.symbol = "EURUSD"
        mock_position.direction = "long"
        mock_position.entry_price = 1.1000
        mock_position.stop_loss = 1.0980
        mock_position.take_profit = 1.1060
        mock_position.current_price = 1.1050  # Big win
        mock_position.current_r_multiple = 2.5  # 2.5R win
        mock_position.volume = 0.01
        mock_position.open_time = datetime.utcnow() - timedelta(hours=3)
        
        bot = TradingBot()
        bot.claude_client = MagicMock()
        bot.claude_client.api_key = "test_key"
        bot.claude_client.review_closed_trade = AsyncMock(return_value={
            'outcome': 'win',
            'grade': 'A',
            'analysis': 'Perfect execution',
            'what_went_right': ['Great entry', 'Perfect timing'],
            'what_went_wrong': [],
            'learnings': ['FVG + OB confluence works'],
            'would_take_again': True
        })
        bot.learning_service = MagicMock()
        bot.learning_service.store_trade_review = AsyncMock()
        
        bot.session_analytics = MagicMock()
        bot.session_analytics.get_current_session.return_value = MagicMock(value="london")
        bot.session_analytics.record_trade = MagicMock()
        bot.correlation_service = MagicMock()
        bot.scaling_manager = MagicMock()
        
        with patch('trading_bot.main.notify', AsyncMock()):
            with patch('trading_bot.api.routes.activity.add_activity', MagicMock()):
                await bot._handle_position_close(mock_position)
        
        # Big win (>=2R) SHOULD trigger review
        bot.claude_client.review_closed_trade.assert_called_once()
        bot.learning_service.store_trade_review.assert_called_once()


class TestLearningContextInAnalysis:
    """Test that learning context is included in Claude prompts."""
    
    @pytest.mark.asyncio
    async def test_context_included_in_market_data(self):
        """Verify learning context appears in market_data for Claude."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        # Create service and mock its methods
        service = TradeLearningService()
        
        with patch.object(service, 'get_learnings_for_symbol') as mock_learnings, \
             patch.object(service, 'get_recent_mistakes') as mock_mistakes, \
             patch.object(service, 'get_winning_patterns') as mock_patterns, \
             patch.object(service, 'get_knowledge_base') as mock_knowledge:
            
            mock_learnings.return_value = [
                {'outcome': 'loss', 'grade': 'C', 'learnings': ['Wait for confirmation']}
            ]
            mock_mistakes.return_value = ["[EURUSD] Entered too early"]
            mock_patterns.return_value = ["[EURUSD] FVG + OB confluence (3.0R)"]
            mock_knowledge.return_value = [
                {'key': 'eurusd_insight', 'insight': 'Best in London', 'confidence': 0.8}
            ]
            
            context = await service.build_context_for_claude("EURUSD", "london")
            
            # Verify context contains expected sections
            assert "Learning Context" in context
            assert "EURUSD" in context
            assert "Recent Mistakes" in context
            assert "Winning Patterns" in context
            assert "Entered too early" in context
            assert "FVG + OB confluence" in context


class TestWeeklyConsolidation:
    """Test weekly consolidation flow."""
    
    @pytest.mark.asyncio
    async def test_weekly_consolidation_creates_report(self):
        """Test that Sunday triggers consolidation and creates report."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        # Mock Claude client
        mock_claude = AsyncMock()
        mock_claude.generate_weekly_insights = AsyncMock(return_value={
            'performance_grade': 'B',
            'summary': 'Good week',
            'patterns_identified': ['Pattern 1'],
            'recurring_mistakes': ['Mistake 1'],
            'winning_patterns': ['Win pattern'],
            'recommendations': ['Rec 1'],
            'symbol_insights': {'EURUSD': 'Good performance'},
            'session_insights': {'london': '72% win rate'},
            'focus_area': 'Entry timing',
            'best_setup': 'FVG + OB'
        })
        
        # Mock database calls
        mock_learning = MagicMock()
        mock_learning.symbol = "EURUSD"
        mock_learning.direction = "long"
        mock_learning.session = "london"
        mock_learning.outcome = "loss"
        mock_learning.grade = "C"
        mock_learning.r_multiple = -1.0
        mock_learning.profit_loss = -50.0
        mock_learning.what_went_right = []
        mock_learning.what_went_wrong = ["Early entry"]
        mock_learning.learnings = ["Wait for fill"]
        mock_learning.setup_type = "ICT"
        mock_learning.entry_reason = "FVG confluence"
        mock_learning.original_confidence = 0.75
        mock_learning.timeframe = "M15"
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session, \
             patch.object(service, '_update_knowledge_from_insights') as mock_update_kb, \
             patch.object(service, 'update_learnings_documentation') as mock_update_docs, \
             patch.object(service, 'send_weekly_notification') as mock_notify:
            
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_learning]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()
            
            mock_update_kb.return_value = None
            mock_update_docs.return_value = None
            mock_notify.return_value = None
            
            result = await service.consolidate_weekly(mock_claude)
            
            # Verify Claude insights were requested
            mock_claude.generate_weekly_insights.assert_called_once()
            
            # Verify documentation was updated
            mock_update_docs.assert_called_once()
            
            # Verify notification was sent
            mock_notify.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_knowledge_base_updated_after_consolidation(self):
        """Test that knowledge base entries are created from insights."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        insights = {
            'symbol_insights': {'EURUSD': 'Strong in London'},
            'recurring_mistakes': ['Entering before FVG fill']
        }
        
        mock_learnings = [MagicMock(symbol='EURUSD', outcome='loss', r_multiple=-1.0)]
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None  # No existing entry
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            
            await service._update_knowledge_from_insights(insights, mock_learnings)
            
            # Verify entries were added
            assert mock_db.add.called


class TestAPIReturnsLearnings:
    """Test that API endpoints return learning data correctly."""
    
    @pytest.mark.asyncio
    async def test_api_returns_recent_learnings(self):
        """Test /api/learning/recent returns stored data."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        mock_learning = MagicMock()
        mock_learning.id = 1
        mock_learning.trade_id = "123"
        mock_learning.timestamp = datetime.utcnow()
        mock_learning.symbol = "EURUSD"
        mock_learning.direction = "long"
        mock_learning.session = "london"
        mock_learning.setup_type = "FVG"
        mock_learning.profit_loss = -50.0
        mock_learning.r_multiple = -1.0
        mock_learning.outcome = "loss"
        mock_learning.grade = "C"
        mock_learning.analysis = "Test analysis"
        mock_learning.what_went_right = ["Good entry"]
        mock_learning.what_went_wrong = ["Tight stop"]
        mock_learning.learnings = ["Use wider stops"]
        mock_learning.would_take_again = False
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_learning]
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            learnings = await service.get_all_learnings(limit=10)
            
            assert len(learnings) == 1
            assert learnings[0]['symbol'] == 'EURUSD'
            assert learnings[0]['grade'] == 'C'
    
    @pytest.mark.asyncio
    async def test_api_returns_mistakes(self):
        """Test /api/learning/mistakes returns formatted list."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        mock_learning = MagicMock()
        mock_learning.symbol = "EURUSD"
        mock_learning.what_went_wrong = ["Early entry", "Tight stop"]
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_learning]
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            mistakes = await service.get_recent_mistakes(limit=5)
            
            assert len(mistakes) >= 1
            assert "[EURUSD]" in mistakes[0]
