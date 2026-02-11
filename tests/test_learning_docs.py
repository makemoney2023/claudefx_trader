"""
Tests for learning system documentation updates.

Tests that trading_learnings.md is written correctly during consolidation.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import os


class TestDocumentationUpdate:
    """Tests for documentation update during consolidation."""
    
    @pytest.mark.asyncio
    async def test_update_creates_file(self):
        """Test that update_learnings_documentation creates the file."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        # Create a temp docs directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "trading_learnings.md"
            service._docs_path = temp_path
            
            # Mock database calls
            with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
                mock_db = AsyncMock()
                mock_session.return_value.__aenter__.return_value = mock_db
                
                # Return empty high-confidence knowledge
                mock_result = MagicMock()
                mock_result.scalars.return_value.all.return_value = []
                mock_result.scalar_one_or_none.return_value = None
                mock_db.execute = AsyncMock(return_value=mock_result)
                
                await service.update_learnings_documentation()
            
            # Verify file was created
            assert temp_path.exists()
            content = temp_path.read_text()
            assert "Trading Learnings" in content
            assert "Auto-Generated" in content
    
    @pytest.mark.asyncio
    async def test_update_includes_high_confidence(self):
        """Test that high-confidence knowledge is written."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        from trading_bot.api.database import KnowledgeBaseModel
        
        service = TradeLearningService()
        
        # Create mock knowledge entry
        mock_knowledge = MagicMock()
        mock_knowledge.category = "symbol_pattern"
        mock_knowledge.key = "EURUSD_london"
        mock_knowledge.insight = "Best performance in London session"
        mock_knowledge.confidence = 0.85
        mock_knowledge.sample_size = 25
        mock_knowledge.win_rate = 0.72
        mock_knowledge.avg_r = 1.5
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "trading_learnings.md"
            service._docs_path = temp_path
            
            with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
                mock_db = AsyncMock()
                mock_session.return_value.__aenter__.return_value = mock_db
                
                mock_result = MagicMock()
                mock_result.scalars.return_value.all.return_value = [mock_knowledge]
                mock_result.scalar_one_or_none.return_value = None
                mock_db.execute = AsyncMock(return_value=mock_result)
                
                await service.update_learnings_documentation()
            
            content = temp_path.read_text()
            assert "Best performance in London" in content
            assert "85%" in content  # confidence
    
    @pytest.mark.asyncio
    async def test_update_includes_weekly_review(self):
        """Test that weekly review is included."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        from trading_bot.api.database import WeeklyReviewModel
        
        service = TradeLearningService()
        
        mock_review = MagicMock()
        mock_review.week_start = datetime(2026, 1, 27)
        mock_review.week_end = datetime(2026, 1, 31)
        mock_review.performance_grade = "B"
        mock_review.summary = "Good week overall"
        mock_review.focus_area = "Entry timing"
        mock_review.best_setup = "FVG + OB"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "trading_learnings.md"
            service._docs_path = temp_path
            
            with patch('trading_bot.services.trade_learning_service.async_session_maker') as mock_session:
                mock_db = AsyncMock()
                mock_session.return_value.__aenter__.return_value = mock_db
                
                mock_result1 = MagicMock()
                mock_result1.scalars.return_value.all.return_value = []
                
                mock_result2 = MagicMock()
                mock_result2.scalar_one_or_none.return_value = mock_review
                
                # Return different results for different queries
                mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
                
                await service.update_learnings_documentation()
            
            content = temp_path.read_text()
            assert "Grade:" in content
            assert "B" in content
            assert "Entry timing" in content or "focus" in content.lower()


class TestMarkdownGeneration:
    """Tests for markdown content generation."""
    
    def test_generate_learnings_markdown_empty(self):
        """Test generating markdown with no data."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        content = service._generate_learnings_markdown([], None)
        
        assert "Trading Learnings" in content
        assert "Auto-Generated" in content
        assert "Last Updated:" in content
    
    def test_generate_learnings_markdown_with_knowledge(self):
        """Test generating markdown with knowledge entries."""
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        service = TradeLearningService()
        
        mock_knowledge = MagicMock()
        mock_knowledge.category = "mistake"
        mock_knowledge.key = "early_entry"
        mock_knowledge.insight = "Entering before FVG fill leads to stop outs"
        mock_knowledge.confidence = 0.9
        mock_knowledge.sample_size = 15
        mock_knowledge.win_rate = 0.0
        mock_knowledge.avg_r = -1.0
        
        content = service._generate_learnings_markdown([mock_knowledge], None)
        
        assert "Entering before FVG" in content
        assert "90%" in content  # confidence


class TestContextBuilderIntegration:
    """Tests that context_builder loads trading_learnings.md."""
    
    def test_context_builder_includes_learnings(self):
        """Test that ContextBuilder loads trading_learnings.md."""
        from trading_bot.llm.context_builder import ContextBuilder
        
        # Create a temporary docs directory with trading_learnings.md
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create the trading_learnings.md file
            learnings_path = Path(temp_dir) / "trading_learnings.md"
            learnings_path.write_text("# Test Learning\nThis is a test learning.")
            
            # Also need ict_strategy.md or similar for priority docs
            ict_path = Path(temp_dir) / "ict_strategy.md"
            ict_path.write_text("# ICT Strategy\nBasic ICT concepts.")
            
            # Create context builder with temp directory
            builder = ContextBuilder(docs_dir=temp_dir)
            
            # Verify trading_learnings is in available documents
            assert 'trading_learnings' in builder.available_documents
            
            # Verify it's loaded in the cache
            assert builder.get_document('trading_learnings') is not None
            
            # Verify it's included in full ICT context
            full_context = builder.get_ict_context()
            assert "Test Learning" in full_context
