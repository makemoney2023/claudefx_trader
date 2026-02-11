"""
Shared test fixtures for ICT Trading Bot tests.
"""

import pytest
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock


# Mock classes for testing without MT5 connection
@dataclass
class MockPosition:
    """Mock MT5 position for testing."""
    ticket: int
    symbol: str
    type: str  # 'buy' or 'sell'
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float = 0.0
    time: datetime = None


@dataclass 
class MockOrderResult:
    """Mock order execution result."""
    success: bool
    message: str = ""
    ticket: int = 0
    
    def __post_init__(self):
        if not self.message:
            self.message = "Success" if self.success else "Failed"


class MockOrderManager:
    """Mock order manager for testing."""
    
    def __init__(self):
        self.close_calls = []
        self.modify_calls = []
    
    async def close_position(self, ticket: int, volume: Optional[float] = None) -> MockOrderResult:
        self.close_calls.append({"ticket": ticket, "volume": volume})
        return MockOrderResult(success=True, ticket=ticket)
    
    async def modify_order(
        self, 
        ticket: int, 
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> MockOrderResult:
        self.modify_calls.append({
            "ticket": ticket,
            "stop_loss": stop_loss,
            "take_profit": take_profit
        })
        return MockOrderResult(success=True, ticket=ticket)


class MockMT5Client:
    """Mock MT5 client for testing."""
    
    def __init__(self):
        self._positions = []
    
    def set_positions(self, positions: list):
        """Set the positions to return."""
        self._positions = positions
    
    async def get_positions(self) -> list:
        """Return mock positions."""
        return self._positions


# Fixtures
@pytest.fixture
def mock_order_manager():
    """Create a mock order manager."""
    return MockOrderManager()


@pytest.fixture
def mock_mt5_client():
    """Create a mock MT5 client."""
    return MockMT5Client()


@pytest.fixture
def position_manager(mock_order_manager):
    """Create a PositionManager with mock order manager."""
    from trading_bot.execution.position_manager import PositionManager
    manager = PositionManager(
        order_manager=mock_order_manager,
        break_even_trigger_r=1.0,
        trailing_start_r=1.5,
        trailing_step_r=0.5,
        partial_close_r=1.0,
        partial_close_percent=0.5
    )
    return manager


@pytest.fixture
def sample_position():
    """Create a sample position for testing."""
    from trading_bot.execution.position_manager import Position
    return Position(
        ticket=12345,
        symbol="XAGUSD",
        direction="long",
        volume=0.01,
        entry_price=95.00,
        stop_loss=90.00,
        take_profit=120.00,
        open_time=datetime.now()
    )


@pytest.fixture
def sample_position_short():
    """Create a sample short position for testing."""
    from trading_bot.execution.position_manager import Position
    return Position(
        ticket=12346,
        symbol="EURUSD",
        direction="short",
        volume=0.02,
        entry_price=1.0850,
        stop_loss=1.0900,
        take_profit=1.0750,
        open_time=datetime.now()
    )


@pytest.fixture
def sample_mt5_position():
    """Create a mock MT5 position."""
    return MockPosition(
        ticket=99999,
        symbol="GBPUSD",
        type="buy",
        volume=0.05,
        price_open=1.2500,
        sl=1.2450,
        tp=1.2600,
        time=datetime.now()
    )


# API testing fixtures
@pytest.fixture
def test_app():
    """Create a test FastAPI app."""
    from trading_bot.api.main import app
    return app


# Risk Manager fixtures
@pytest.fixture
def risk_manager():
    """Create a RiskManager instance for testing."""
    from trading_bot.execution.risk_manager import RiskManager
    return RiskManager(
        risk_per_trade=0.01,  # 1% risk per trade - safe default
        max_risk_per_trade=0.10,
        max_daily_risk=0.15,
        min_risk_reward=2.0
    )


# News Service fixtures (for Phase 2)
@pytest.fixture
def news_service():
    """Create a NewsService instance for testing."""
    from trading_bot.services.news_service import NewsService
    return NewsService()


# Correlation Service fixtures (for Phase 4)
@pytest.fixture
def correlation_service():
    """Create a CorrelationService instance for testing."""
    from trading_bot.services.correlation_service import CorrelationService
    return CorrelationService()


# Silver Analysis fixtures (for Phase 5)
@pytest.fixture
def silver_analyzer():
    """Create a SilverAnalyzer instance for testing."""
    from trading_bot.analysis.silver_analysis import SilverAnalyzer
    return SilverAnalyzer()


# Goal Tracker fixtures (for Phase 3)
@pytest.fixture
def goal_tracker():
    """Create a GoalTracker instance for testing."""
    from trading_bot.services.goal_tracker import GoalTracker
    return GoalTracker(
        starting_equity=1000,
        target_equity=100000
    )
