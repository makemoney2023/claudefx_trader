"""
Unit tests for TradeLearningService.

Tests:
- Store trade review
- Retrieve learnings by symbol
- Get recent mistakes
- Get winning patterns
- Build context for Claude
- Weekly consolidation
- Documentation update
- Prune expired knowledge
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import json

from trading_bot.services.trade_learning_service import TradeLearningService


@pytest.fixture
def learning_service():
    """Create a test learning service."""
    return TradeLearningService()


@pytest.fixture
def sample_review():
    """Sample Claude trade review."""
    return {
        'outcome': 'loss',
        'grade': 'C',
        'analysis': 'Entry was premature before FVG fill completed.',
        'what_went_right': ['Correct direction identification', 'Good risk-reward setup'],
        'what_went_wrong': ['Early entry', 'Stop too tight for volatility'],
        'learnings': ['Wait for full FVG fill', 'Add 5 pips to stops during high volatility'],
        'improvement_suggestions': ['Use limit orders at FVG 50% level', 'Check ATR before setting stops'],
        'would_take_again': False
    }


@pytest.fixture
def sample_win_review():
    """Sample Claude review for a big win."""
    return {
        'outcome': 'win',
        'grade': 'A',
        'analysis': 'Perfect execution with FVG + OB confluence.',
        'what_went_right': ['Perfect entry timing', 'Waited for confluence', 'Good session selection'],
        'what_went_wrong': [],
        'learnings': ['FVG + OB confluence provides high probability entries'],
        'improvement_suggestions': ['Consider scaling out at 1.5R'],
        'would_take_again': True
    }


class TestStoreTradeReview:
    """Tests for store_trade_review method."""
    
    @pytest.mark.asyncio
    async def test_store_trade_review_success(self, learning_service, sample_review):
        """Test successful storage of a trade review."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()
            
            result = await learning_service.store_trade_review(
                trade_id="12345",
                symbol="EURUSD",
                direction="long",
                profit_loss=-50.0,
                r_multiple=-1.0,
                review=sample_review,
                session="london",
                setup_type="FVG"
            )
            
            # Verify add was called
            assert mock_db.add.called
            assert mock_db.commit.called
    
    @pytest.mark.asyncio
    async def test_store_trade_review_invalid_data(self, learning_service):
        """Test handling of missing/malformed review data."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()
            
            # Empty review - should use defaults
            result = await learning_service.store_trade_review(
                trade_id="empty_review",
                symbol="GBPUSD",
                direction="short",
                profit_loss=-25.0,
                r_multiple=-0.5,
                review={}
            )
            
            assert mock_db.add.called
    
    @pytest.mark.asyncio
    async def test_store_trade_review_exception(self, learning_service, sample_review):
        """Test handling of database exceptions."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_session.return_value.__aenter__.side_effect = Exception("Database error")
            
            result = await learning_service.store_trade_review(
                trade_id="error_test",
                symbol="EURUSD",
                direction="long",
                profit_loss=-50.0,
                r_multiple=-1.0,
                review=sample_review
            )
            
            assert result is None


class TestGetLearningsForSymbol:
    """Tests for get_learnings_for_symbol method."""
    
    @pytest.mark.asyncio
    async def test_get_learnings_for_symbol_success(self, learning_service):
        """Test retrieving learnings for a specific symbol."""
        mock_learning = MagicMock()
        mock_learning.trade_id = "123"
        mock_learning.timestamp = datetime.utcnow()
        mock_learning.direction = "long"
        mock_learning.session = "london"
        mock_learning.outcome = "loss"
        mock_learning.grade = "C"
        mock_learning.r_multiple = -1.0
        mock_learning.analysis = "Test analysis"
        mock_learning.learnings = ["Learning 1"]
        mock_learning.would_take_again = False
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_learning]
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            learnings = await learning_service.get_learnings_for_symbol("EURUSD", limit=5)
            
            assert len(learnings) == 1
            assert learnings[0]['trade_id'] == "123"
            assert learnings[0]['grade'] == "C"
    
    @pytest.mark.asyncio
    async def test_get_learnings_for_symbol_empty(self, learning_service):
        """Test when no learnings exist for symbol."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            learnings = await learning_service.get_learnings_for_symbol("UNKNOWN")
            
            assert learnings == []
    
    @pytest.mark.asyncio
    async def test_get_learnings_respects_limit(self, learning_service):
        """Test that limit parameter is respected."""
        mock_learnings = []
        for i in range(10):
            m = MagicMock()
            m.trade_id = str(i)
            m.timestamp = datetime.utcnow()
            m.direction = "long"
            m.session = "london"
            m.outcome = "loss"
            m.grade = "C"
            m.r_multiple = -1.0
            m.analysis = f"Analysis {i}"
            m.learnings = []
            m.would_take_again = True
            mock_learnings.append(m)
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            # Simulate limit being applied
            mock_result.scalars.return_value.all.return_value = mock_learnings[:5]
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            learnings = await learning_service.get_learnings_for_symbol("EURUSD", limit=5)
            
            assert len(learnings) == 5


class TestGetRecentMistakes:
    """Tests for get_recent_mistakes method."""
    
    @pytest.mark.asyncio
    async def test_get_recent_mistakes_success(self, learning_service):
        """Test retrieving recent mistakes."""
        mock_learning = MagicMock()
        mock_learning.symbol = "EURUSD"
        mock_learning.what_went_wrong = ["Early entry", "Stop too tight"]
        
        mock_learning2 = MagicMock()
        mock_learning2.symbol = "GBPUSD"
        mock_learning2.what_went_wrong = ["Chased entry"]
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_learning, mock_learning2]
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            mistakes = await learning_service.get_recent_mistakes(limit=5)
            
            assert len(mistakes) >= 2
            assert "[EURUSD] Early entry" in mistakes
            assert "[GBPUSD] Chased entry" in mistakes
    
    @pytest.mark.asyncio
    async def test_get_recent_mistakes_empty(self, learning_service):
        """Test when no mistakes exist."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            mistakes = await learning_service.get_recent_mistakes()
            
            assert mistakes == []


class TestGetWinningPatterns:
    """Tests for get_winning_patterns method."""
    
    @pytest.mark.asyncio
    async def test_get_winning_patterns_success(self, learning_service):
        """Test retrieving winning patterns from big wins."""
        mock_learning = MagicMock()
        mock_learning.symbol = "EURUSD"
        mock_learning.r_multiple = 3.5
        mock_learning.what_went_right = ["FVG + OB confluence", "Perfect timing"]
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_learning]
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            patterns = await learning_service.get_winning_patterns(limit=5)
            
            assert len(patterns) >= 1
            assert "FVG + OB confluence" in patterns[0]
            assert "3.5R" in patterns[0]


class TestBuildContextForClaude:
    """Tests for build_context_for_claude method."""
    
    @pytest.mark.asyncio
    async def test_build_context_full(self, learning_service):
        """Test building context with all data available."""
        with patch.object(learning_service, 'get_learnings_for_symbol') as mock_learnings, \
             patch.object(learning_service, 'get_recent_mistakes') as mock_mistakes, \
             patch.object(learning_service, 'get_winning_patterns') as mock_patterns, \
             patch.object(learning_service, 'get_knowledge_base') as mock_knowledge:
            
            mock_learnings.return_value = [
                {
                    'outcome': 'loss',
                    'grade': 'C',
                    'learnings': ['Wait for fill']
                }
            ]
            mock_mistakes.return_value = ["[EURUSD] Early entry"]
            mock_patterns.return_value = ["[EURUSD] FVG confluence (3.0R)"]
            mock_knowledge.return_value = [
                {
                    'key': 'eurusd_london',
                    'insight': 'Best in London session',
                    'confidence': 0.8
                }
            ]
            
            context = await learning_service.build_context_for_claude("EURUSD", "london")
            
            assert "Learning Context" in context
            assert "EURUSD" in context
            assert "Recent Mistakes" in context
            assert "Winning Patterns" in context
    
    @pytest.mark.asyncio
    async def test_build_context_no_data(self, learning_service):
        """Test building context with no data available."""
        with patch.object(learning_service, 'get_learnings_for_symbol') as mock_learnings, \
             patch.object(learning_service, 'get_recent_mistakes') as mock_mistakes, \
             patch.object(learning_service, 'get_winning_patterns') as mock_patterns, \
             patch.object(learning_service, 'get_knowledge_base') as mock_knowledge:
            
            mock_learnings.return_value = []
            mock_mistakes.return_value = []
            mock_patterns.return_value = []
            mock_knowledge.return_value = []
            
            context = await learning_service.build_context_for_claude("NEWPAIR")
            
            # Should return minimal or empty context
            assert isinstance(context, str)


class TestConsolidateWeekly:
    """Tests for consolidate_weekly method."""
    
    @pytest.mark.asyncio
    async def test_consolidate_weekly_success(self, learning_service):
        """Test weekly consolidation creates report."""
        mock_claude = AsyncMock()
        mock_claude.generate_weekly_insights = AsyncMock(return_value={
            'performance_grade': 'B',
            'summary': 'Good week',
            'patterns_identified': ['Pattern 1'],
            'recurring_mistakes': ['Mistake 1'],
            'winning_patterns': ['Win pattern 1'],
            'recommendations': ['Rec 1'],
            'symbol_insights': {'EURUSD': 'Good'},
            'session_insights': {'london': '72%'},
            'focus_area': 'Entry timing',
            'best_setup': 'FVG + OB'
        })
        
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
             patch.object(learning_service, '_update_knowledge_from_insights') as mock_update_kb, \
             patch.object(learning_service, 'update_learnings_documentation') as mock_update_docs, \
             patch.object(learning_service, 'send_weekly_notification') as mock_notify:
            
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            # First call - get learnings
            mock_result1 = MagicMock()
            mock_result1.scalars.return_value.all.return_value = [mock_learning]
            
            # Second call - create review
            mock_db.execute = AsyncMock(return_value=mock_result1)
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()
            mock_db.refresh = AsyncMock()
            
            mock_update_kb.return_value = None
            mock_update_docs.return_value = None
            mock_notify.return_value = None
            
            result = await learning_service.consolidate_weekly(mock_claude)
            
            # Verify Claude was called
            mock_claude.generate_weekly_insights.assert_called_once()
            mock_update_docs.assert_called_once()
            mock_notify.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_consolidate_weekly_no_learnings(self, learning_service):
        """Test consolidation with no learnings this week."""
        mock_claude = AsyncMock()
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            result = await learning_service.consolidate_weekly(mock_claude)
            
            assert result is None
            mock_claude.generate_weekly_insights.assert_not_called()


class TestPruneExpiredKnowledge:
    """Tests for prune_expired_knowledge method."""
    
    @pytest.mark.asyncio
    async def test_prune_expired_removes_old(self, learning_service):
        """Test that expired entries are removed."""
        mock_expired1 = MagicMock()
        mock_expired2 = MagicMock()
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_expired1, mock_expired2]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_db.delete = AsyncMock()
            mock_db.commit = AsyncMock()
            
            count = await learning_service.prune_expired_knowledge()
            
            assert count == 2
            assert mock_db.delete.call_count == 2
    
    @pytest.mark.asyncio
    async def test_prune_keeps_recent(self, learning_service):
        """Test that non-expired entries are kept."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            count = await learning_service.prune_expired_knowledge()
            
            assert count == 0


class TestGetLatestWeeklyReport:
    """Tests for get_latest_weekly_report method."""
    
    @pytest.mark.asyncio
    async def test_get_latest_weekly_report_success(self, learning_service):
        """Test retrieving the latest weekly report."""
        mock_review = MagicMock()
        mock_review.week_start = datetime(2026, 1, 27)
        mock_review.week_end = datetime(2026, 1, 31)
        mock_review.performance_grade = "B"
        mock_review.summary = "Good week"
        mock_review.total_trades = 10
        mock_review.wins = 6
        mock_review.losses = 4
        mock_review.total_pnl = 250.0
        mock_review.total_r = 5.0
        mock_review.patterns_identified = ["Pattern 1"]
        mock_review.recurring_mistakes = ["Mistake 1"]
        mock_review.winning_patterns = ["Win 1"]
        mock_review.recommendations = ["Rec 1"]
        mock_review.symbol_insights = {"EURUSD": "Good"}
        mock_review.session_insights = {"london": "72%"}
        mock_review.focus_area = "Entry timing"
        mock_review.best_setup = "FVG + OB"
        mock_review.created_at = datetime.utcnow()
        
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_review
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            report = await learning_service.get_latest_weekly_report()
            
            assert report is not None
            assert report['performance_grade'] == "B"
            assert report['total_trades'] == 10
    
    @pytest.mark.asyncio
    async def test_get_latest_weekly_report_none(self, learning_service):
        """Test when no weekly report exists."""
        with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db
            
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute = AsyncMock(return_value=mock_result)
            
            report = await learning_service.get_latest_weekly_report()
            
            assert report is None
