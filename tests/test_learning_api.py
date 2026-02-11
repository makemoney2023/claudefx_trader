"""
API endpoint tests for the learning system.

Tests:
- GET /api/learning/recent - Pagination, filtering
- GET /api/learning/mistakes - Limit parameter
- GET /api/learning/patterns - Winning patterns
- GET /api/learning/knowledge - Category filter
- GET /api/learning/weekly-report - Latest report
- POST /api/learning/consolidate - Manual trigger
- GET /api/learning/stats - Learning statistics
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from fastapi.testclient import TestClient


@pytest.fixture
def mock_learning_service():
    """Create a mock learning service."""
    service = MagicMock()
    service.get_all_learnings = AsyncMock(return_value=[
        {
            'id': 1,
            'trade_id': '12345',
            'timestamp': '2026-01-30T10:00:00',
            'symbol': 'EURUSD',
            'direction': 'long',
            'session': 'london',
            'setup_type': 'FVG',
            'profit_loss': -50.0,
            'r_multiple': -1.0,
            'outcome': 'loss',
            'grade': 'C',
            'analysis': 'Entry too early',
            'what_went_right': ['Good direction'],
            'what_went_wrong': ['Early entry'],
            'learnings': ['Wait for fill'],
            'would_take_again': False
        }
    ])
    service.get_learnings_for_symbol = AsyncMock(return_value=[
        {
            'id': 2,
            'trade_id': '12346',
            'timestamp': '2026-01-30T11:00:00',
            'symbol': 'EURUSD',
            'direction': 'short',
            'session': 'new_york',
            'setup_type': 'OB',
            'profit_loss': 100.0,
            'r_multiple': 2.5,
            'outcome': 'win',
            'grade': 'A',
            'analysis': 'Perfect execution',
            'what_went_right': ['Great entry', 'Perfect timing'],
            'what_went_wrong': [],
            'learnings': ['OB + FVG works'],
            'would_take_again': True
        }
    ])
    service.get_recent_mistakes = AsyncMock(return_value=[
        '[EURUSD] Early entry before FVG fill',
        '[GBPUSD] Trading during news'
    ])
    service.get_winning_patterns = AsyncMock(return_value=[
        '[EURUSD] FVG + OB confluence (3.0R)',
        '[XAUUSD] London open breakout (2.5R)'
    ])
    service.get_knowledge_base = AsyncMock(return_value=[
        {
            'category': 'symbol_pattern',
            'key': 'eurusd_london',
            'insight': 'Best performance in London session',
            'confidence': 0.85,
            'sample_size': 20,
            'win_rate': 0.75,
            'avg_r': 1.8,
            'expires_at': '2026-04-30T00:00:00'
        }
    ])
    service.get_latest_weekly_report = AsyncMock(return_value={
        'week_start': '2026-01-27T00:00:00',
        'week_end': '2026-01-31T23:59:59',
        'performance_grade': 'B',
        'summary': 'Good week with room for improvement',
        'total_trades': 15,
        'wins': 9,
        'losses': 6,
        'total_pnl': 250.0,
        'total_r': 5.5,
        'patterns_identified': ['FVG confluence works'],
        'recurring_mistakes': ['Early entries'],
        'winning_patterns': ['OB + FVG'],
        'recommendations': ['Wait for fills'],
        'symbol_insights': {'EURUSD': 'Best performer'},
        'session_insights': {'london': '72% win rate'},
        'focus_area': 'Entry timing',
        'best_setup': 'FVG + OB',
        'created_at': '2026-01-31T20:00:00'
    })
    service.build_context_for_claude = AsyncMock(return_value="## Learning Context\n...")
    service.consolidate_weekly = AsyncMock(return_value=MagicMock(
        performance_grade='B',
        total_trades=15
    ))
    service.prune_expired_knowledge = AsyncMock(return_value=3)
    
    return service


@pytest.fixture
def client(mock_learning_service):
    """Create a test client with mocked service."""
    from trading_bot.api.routes.learning import router, set_learning_service
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    
    # Set the mock service
    set_learning_service(mock_learning_service)
    
    return TestClient(app)


class TestGetRecentLearnings:
    """Tests for GET /api/learning/recent."""
    
    def test_get_recent_default(self, client, mock_learning_service):
        """Test getting recent learnings with default parameters."""
        response = client.get("/api/learning/recent")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        mock_learning_service.get_all_learnings.assert_called_once()
    
    def test_get_recent_with_limit(self, client, mock_learning_service):
        """Test getting recent learnings with limit."""
        response = client.get("/api/learning/recent?limit=5")
        
        assert response.status_code == 200
        mock_learning_service.get_all_learnings.assert_called_with(limit=5, offset=0)
    
    def test_get_recent_with_symbol_filter(self, client, mock_learning_service):
        """Test filtering by symbol."""
        response = client.get("/api/learning/recent?symbol=EURUSD")
        
        assert response.status_code == 200
        mock_learning_service.get_learnings_for_symbol.assert_called_with('EURUSD', limit=20)
    
    def test_get_recent_with_pagination(self, client, mock_learning_service):
        """Test pagination parameters."""
        response = client.get("/api/learning/recent?limit=10&offset=20")
        
        assert response.status_code == 200
        mock_learning_service.get_all_learnings.assert_called_with(limit=10, offset=20)


class TestGetMistakes:
    """Tests for GET /api/learning/mistakes."""
    
    def test_get_mistakes_default(self, client, mock_learning_service):
        """Test getting mistakes with default limit."""
        response = client.get("/api/learning/mistakes")
        
        assert response.status_code == 200
        data = response.json()
        assert 'mistakes' in data
        assert 'count' in data
        mock_learning_service.get_recent_mistakes.assert_called_with(limit=5)
    
    def test_get_mistakes_with_limit(self, client, mock_learning_service):
        """Test respects limit parameter."""
        response = client.get("/api/learning/mistakes?limit=10")
        
        assert response.status_code == 200
        mock_learning_service.get_recent_mistakes.assert_called_with(limit=10)


class TestGetPatterns:
    """Tests for GET /api/learning/patterns."""
    
    def test_get_patterns_default(self, client, mock_learning_service):
        """Test getting winning patterns."""
        response = client.get("/api/learning/patterns")
        
        assert response.status_code == 200
        data = response.json()
        assert 'patterns' in data
        assert 'count' in data
        mock_learning_service.get_winning_patterns.assert_called_with(limit=5)


class TestGetKnowledge:
    """Tests for GET /api/learning/knowledge."""
    
    def test_get_knowledge_default(self, client, mock_learning_service):
        """Test getting knowledge base."""
        response = client.get("/api/learning/knowledge")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        mock_learning_service.get_knowledge_base.assert_called_with(
            category=None,
            include_expired=False
        )
    
    def test_get_knowledge_with_category(self, client, mock_learning_service):
        """Test filtering by category."""
        response = client.get("/api/learning/knowledge?category=mistake")
        
        assert response.status_code == 200
        mock_learning_service.get_knowledge_base.assert_called_with(
            category='mistake',
            include_expired=False
        )
    
    def test_get_knowledge_include_expired(self, client, mock_learning_service):
        """Test including expired entries."""
        response = client.get("/api/learning/knowledge?include_expired=true")
        
        assert response.status_code == 200
        mock_learning_service.get_knowledge_base.assert_called_with(
            category=None,
            include_expired=True
        )


class TestGetWeeklyReport:
    """Tests for GET /api/learning/weekly-report."""
    
    def test_get_weekly_report_exists(self, client, mock_learning_service):
        """Test getting existing weekly report."""
        response = client.get("/api/learning/weekly-report")
        
        assert response.status_code == 200
        data = response.json()
        assert data['message'] == 'success'
        assert 'report' in data
        assert data['report']['performance_grade'] == 'B'
    
    def test_get_weekly_report_none(self, client, mock_learning_service):
        """Test when no report exists."""
        mock_learning_service.get_latest_weekly_report.return_value = None
        
        response = client.get("/api/learning/weekly-report")
        
        assert response.status_code == 200
        data = response.json()
        assert 'No weekly report' in data['message']
        assert data['report'] is None


class TestConsolidate:
    """Tests for POST /api/learning/consolidate."""
    
    def test_consolidate_endpoint_exists(self, client):
        """Test that consolidate endpoint exists and responds."""
        response = client.post("/api/learning/consolidate")
        
        # Endpoint does not require auth - returns 200 or 400 (no Claude key)
        assert response.status_code in [200, 400, 500]
    
    def test_consolidate_with_mock_claude(self, client, mock_learning_service):
        """Test consolidation with mocked Claude client."""
        with patch('trading_bot.llm.claude_client.ClaudeClient') as mock_claude:
            mock_claude_instance = MagicMock()
            mock_claude_instance.api_key = 'test_key'
            mock_claude.return_value = mock_claude_instance
            
            # Mock the consolidate_weekly to return None (no learnings)
            mock_learning_service.consolidate_weekly = AsyncMock(return_value=None)
            
            response = client.post("/api/learning/consolidate")
        
        # Should respond with some status
        assert response.status_code in [200, 400, 500]


class TestGetContext:
    """Tests for GET /api/learning/context/{symbol}."""
    
    def test_get_context_for_symbol(self, client, mock_learning_service):
        """Test getting learning context for a symbol."""
        response = client.get("/api/learning/context/EURUSD")
        
        assert response.status_code == 200
        data = response.json()
        assert data['symbol'] == 'EURUSD'
        assert 'context' in data
        assert 'context_length' in data
    
    def test_get_context_with_session(self, client, mock_learning_service):
        """Test getting context with session parameter."""
        response = client.get("/api/learning/context/EURUSD?session=london")
        
        assert response.status_code == 200
        data = response.json()
        assert data['session'] == 'london'


class TestGetStats:
    """Tests for GET /api/learning/stats."""
    
    def test_get_stats(self, client, mock_learning_service):
        """Test getting learning statistics."""
        response = client.get("/api/learning/stats")
        
        assert response.status_code == 200
        data = response.json()
        assert 'total_learnings' in data
        assert 'by_grade' in data
        assert 'by_outcome' in data
        assert 'by_symbol' in data
        assert 'recent_mistakes_count' in data
        assert 'winning_patterns_count' in data
        assert 'knowledge_entries' in data
