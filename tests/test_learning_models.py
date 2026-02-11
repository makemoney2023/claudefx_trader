"""
Tests for the learning system database models.

Tests:
- TradeLearningModel
- KnowledgeBaseModel  
- WeeklyReviewModel
"""

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from trading_bot.api.database import (
    Base,
    TradeLearningModel,
    KnowledgeBaseModel,
    WeeklyReviewModel
)


# Test database setup
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    """Create a test database session."""
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


class TestTradeLearningModel:
    """Tests for TradeLearningModel."""
    
    def test_create_trade_learning(self, db_session):
        """Test creating a trade learning record with all fields."""
        learning = TradeLearningModel(
            trade_id="12345",
            symbol="EURUSD",
            direction="long",
            session="london",
            setup_type="FVG",
            profit_loss=-50.0,
            r_multiple=-1.0,
            outcome="loss",
            grade="C",
            analysis="Entry was premature, should have waited for FVG fill.",
            what_went_right=["Good direction identification"],
            what_went_wrong=["Early entry", "Stop too tight"],
            learnings=["Wait for full FVG fill before entry"],
            improvement_suggestions=["Use limit orders at FVG midpoint"],
            would_take_again=False
        )
        
        db_session.add(learning)
        db_session.commit()
        
        # Retrieve and verify
        result = db_session.execute(
            select(TradeLearningModel).where(TradeLearningModel.trade_id == "12345")
        ).scalar_one()
        
        assert result.trade_id == "12345"
        assert result.symbol == "EURUSD"
        assert result.outcome == "loss"
        assert result.grade == "C"
        assert result.would_take_again is False
        assert "Early entry" in result.what_went_wrong
    
    def test_trade_learning_json_fields(self, db_session):
        """Test JSON fields serialize and deserialize correctly."""
        learnings_list = ["Learning 1", "Learning 2", "Learning 3"]
        
        learning = TradeLearningModel(
            trade_id="json_test",
            symbol="GBPUSD",
            direction="short",
            outcome="win",
            grade="A",
            learnings=learnings_list,
            what_went_right={"timing": "perfect", "direction": "correct"},
            what_went_wrong=[]
        )
        
        db_session.add(learning)
        db_session.commit()
        
        result = db_session.execute(
            select(TradeLearningModel).where(TradeLearningModel.trade_id == "json_test")
        ).scalar_one()
        
        assert result.learnings == learnings_list
        assert result.what_went_right["timing"] == "perfect"
        assert result.what_went_wrong == []
    
    def test_trade_learning_timestamp_default(self, db_session):
        """Test that timestamp defaults to now."""
        before = datetime.utcnow()
        
        learning = TradeLearningModel(
            trade_id="timestamp_test",
            symbol="USDJPY",
            direction="long",
            outcome="breakeven",
            grade="B"
        )
        
        db_session.add(learning)
        db_session.commit()
        
        after = datetime.utcnow()
        
        result = db_session.execute(
            select(TradeLearningModel).where(TradeLearningModel.trade_id == "timestamp_test")
        ).scalar_one()
        
        assert before <= result.timestamp <= after
        assert before <= result.created_at <= after
    
    def test_trade_learning_symbol_index(self, db_session):
        """Test querying by symbol works (index exists)."""
        # Create multiple learnings for different symbols
        for symbol in ["EURUSD", "EURUSD", "GBPUSD", "EURUSD"]:
            learning = TradeLearningModel(
                trade_id=f"idx_{symbol}_{datetime.utcnow().timestamp()}",
                symbol=symbol,
                direction="long",
                outcome="loss",
                grade="C"
            )
            db_session.add(learning)
        
        db_session.commit()
        
        # Query by symbol
        eurusd_learnings = db_session.execute(
            select(TradeLearningModel).where(TradeLearningModel.symbol == "EURUSD")
        ).scalars().all()
        
        assert len(eurusd_learnings) == 3


class TestKnowledgeBaseModel:
    """Tests for KnowledgeBaseModel."""
    
    def test_create_knowledge_entry(self, db_session):
        """Test creating a knowledge base entry."""
        expires = datetime.utcnow() + timedelta(days=90)
        
        knowledge = KnowledgeBaseModel(
            category="symbol_pattern",
            key="EURUSD_london_fvg",
            insight="FVG entries in London session have 72% win rate",
            confidence=0.8,
            sample_size=25,
            win_rate=0.72,
            avg_r=1.5,
            expires_at=expires
        )
        
        db_session.add(knowledge)
        db_session.commit()
        
        result = db_session.execute(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.key == "EURUSD_london_fvg")
        ).scalar_one()
        
        assert result.category == "symbol_pattern"
        assert result.confidence == 0.8
        assert result.sample_size == 25
        assert result.win_rate == 0.72
    
    def test_knowledge_unique_key(self, db_session):
        """Test that key must be unique."""
        expires = datetime.utcnow() + timedelta(days=90)
        
        knowledge1 = KnowledgeBaseModel(
            category="mistake",
            key="chasing_entries",
            insight="Don't chase after missed entries",
            confidence=0.9,
            sample_size=15,
            expires_at=expires
        )
        
        db_session.add(knowledge1)
        db_session.commit()
        
        knowledge2 = KnowledgeBaseModel(
            category="mistake",
            key="chasing_entries",  # Same key
            insight="Different insight",
            confidence=0.5,
            sample_size=5,
            expires_at=expires
        )
        
        db_session.add(knowledge2)
        
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
    
    def test_knowledge_expiry_query(self, db_session):
        """Test querying for expired knowledge."""
        now = datetime.utcnow()
        
        # Create expired knowledge
        expired = KnowledgeBaseModel(
            category="session_insight",
            key="expired_insight",
            insight="Old insight",
            confidence=0.5,
            sample_size=5,
            expires_at=now - timedelta(days=1)
        )
        
        # Create valid knowledge
        valid = KnowledgeBaseModel(
            category="session_insight",
            key="valid_insight",
            insight="Fresh insight",
            confidence=0.8,
            sample_size=20,
            expires_at=now + timedelta(days=30)
        )
        
        db_session.add_all([expired, valid])
        db_session.commit()
        
        # Query for non-expired
        active = db_session.execute(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.expires_at > now)
        ).scalars().all()
        
        assert len(active) == 1
        assert active[0].key == "valid_insight"
    
    def test_knowledge_category_filter(self, db_session):
        """Test filtering by category."""
        expires = datetime.utcnow() + timedelta(days=90)
        
        categories = ["symbol_pattern", "symbol_pattern", "mistake", "best_setup"]
        for i, cat in enumerate(categories):
            knowledge = KnowledgeBaseModel(
                category=cat,
                key=f"test_{cat}_{i}",
                insight=f"Insight for {cat}",
                confidence=0.7,
                sample_size=10,
                expires_at=expires
            )
            db_session.add(knowledge)
        
        db_session.commit()
        
        patterns = db_session.execute(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.category == "symbol_pattern")
        ).scalars().all()
        
        assert len(patterns) == 2


class TestWeeklyReviewModel:
    """Tests for WeeklyReviewModel."""
    
    def test_create_weekly_review(self, db_session):
        """Test creating a weekly review."""
        week_start = datetime(2026, 1, 27)
        week_end = datetime(2026, 1, 31)
        
        review = WeeklyReviewModel(
            week_start=week_start,
            week_end=week_end,
            performance_grade="B",
            summary="Good week overall with room for improvement in entry timing.",
            total_trades=15,
            wins=9,
            losses=6,
            total_pnl=250.0,
            total_r=5.5,
            patterns_identified=["FVG entries working well", "London session outperforming"],
            recurring_mistakes=["Entering before FVG fill", "Trading Asian session"],
            winning_patterns=["Order block + FVG confluence"],
            recommendations=["Focus on London only", "Use limit orders"],
            symbol_insights={"EURUSD": "Best performer", "GBPJPY": "Avoid"},
            session_insights={"london": "72% win rate", "asian": "30% win rate"},
            focus_area="Entry timing - wait for full FVG fill",
            best_setup="Order block with FVG confluence in London"
        )
        
        db_session.add(review)
        db_session.commit()
        
        result = db_session.execute(
            select(WeeklyReviewModel).where(WeeklyReviewModel.week_start == week_start)
        ).scalar_one()
        
        assert result.performance_grade == "B"
        assert result.total_trades == 15
        assert result.wins == 9
        assert "FVG entries" in result.patterns_identified[0]
        assert result.symbol_insights["EURUSD"] == "Best performer"
    
    def test_weekly_review_json_insights(self, db_session):
        """Test JSON fields for insights."""
        review = WeeklyReviewModel(
            week_start=datetime(2026, 1, 20),
            week_end=datetime(2026, 1, 24),
            performance_grade="A",
            summary="Excellent week",
            total_trades=10,
            wins=8,
            losses=2,
            patterns_identified=["Pattern 1", "Pattern 2"],
            recommendations=[
                {"priority": "high", "action": "Continue current strategy"},
                {"priority": "medium", "action": "Add more confluence"}
            ]
        )
        
        db_session.add(review)
        db_session.commit()
        
        result = db_session.execute(
            select(WeeklyReviewModel).where(WeeklyReviewModel.performance_grade == "A")
        ).scalar_one()
        
        assert len(result.patterns_identified) == 2
        assert result.recommendations[0]["priority"] == "high"
    
    def test_weekly_review_date_range(self, db_session):
        """Test querying reviews by date range."""
        # Create reviews for different weeks
        for i in range(4):
            start = datetime(2026, 1, 6 + i*7)
            review = WeeklyReviewModel(
                week_start=start,
                week_end=start + timedelta(days=4),
                performance_grade="B",
                summary=f"Week {i+1} review",
                total_trades=10,
                wins=6,
                losses=4
            )
            db_session.add(review)
        
        db_session.commit()
        
        # Query for reviews in January
        jan_start = datetime(2026, 1, 1)
        jan_end = datetime(2026, 1, 31)
        
        jan_reviews = db_session.execute(
            select(WeeklyReviewModel).where(
                WeeklyReviewModel.week_start >= jan_start,
                WeeklyReviewModel.week_start <= jan_end
            ).order_by(WeeklyReviewModel.week_start)
        ).scalars().all()
        
        assert len(jan_reviews) == 4
    
    def test_weekly_review_statistics(self, db_session):
        """Test that statistics are stored correctly."""
        review = WeeklyReviewModel(
            week_start=datetime(2026, 1, 27),
            week_end=datetime(2026, 1, 31),
            performance_grade="C",
            summary="Average week",
            total_trades=20,
            wins=10,
            losses=10,
            total_pnl=-25.50,
            total_r=-0.5
        )
        
        db_session.add(review)
        db_session.commit()
        
        result = db_session.execute(
            select(WeeklyReviewModel).where(WeeklyReviewModel.total_trades == 20)
        ).scalar_one()
        
        assert result.total_pnl == -25.50
        assert result.total_r == -0.5
        assert result.wins == result.losses == 10
