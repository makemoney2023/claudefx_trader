"""
Services module for ICT Trading Bot.

Contains external service integrations:
- NewsService: Economic calendar and geopolitical news
- CorrelationService: Symbol correlation tracking
- GoalTracker: Equity goal monitoring
- ScalingManager: Automated risk adjustment
- SessionAnalytics: Session performance tracking
- TradeLearningService: Claude trade learning system
- ClaudeTradeManager: Centralized AI trade management
"""

# Core services that don't have circular dependencies
from .news_service import NewsService
from .correlation_service import CorrelationService
from .goal_tracker import GoalTracker
from .scaling_manager import ScalingManager, TradingMode
from .session_analytics import SessionAnalytics, TradingSession
from .pending_order_manager import PendingOrderManager, PendingOrder, PendingOrderStatus
from .firecrawl_intelligence import FirecrawlIntelligenceService

# Lazy imports to avoid circular dependency with api.database
def get_trade_learning_service():
    """Lazy import to avoid circular dependency."""
    from .trade_learning_service import TradeLearningService
    return TradeLearningService

def get_claude_trade_manager():
    """Lazy import to avoid circular dependency."""
    from .claude_trade_manager import ClaudeTradeManager, TradePrecheck, MarginValidation, TradeDecision
    return ClaudeTradeManager, TradePrecheck, MarginValidation, TradeDecision

__all__ = [
    'NewsService', 
    'CorrelationService', 
    'GoalTracker',
    'ScalingManager',
    'TradingMode',
    'SessionAnalytics',
    'TradingSession',
    'get_trade_learning_service',
    'get_claude_trade_manager',
    'PendingOrderManager',
    'PendingOrder',
    'PendingOrderStatus',
    'FirecrawlIntelligenceService'
]
