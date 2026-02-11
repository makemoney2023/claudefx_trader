"""
Alert Configuration.

Customizable thresholds for trading alerts and notifications.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import os
import json
from pathlib import Path

from .logging import get_logger

logger = get_logger(__name__)

CONFIG_FILE = "data/alert_config.json"


@dataclass
class AlertThresholds:
    """Configurable alert thresholds."""
    
    # Profit/Loss Alerts
    profit_alert_usd: float = 100.0  # Alert when single trade profit exceeds this
    loss_alert_usd: float = -50.0    # Alert when single trade loss exceeds this
    daily_profit_alert: float = 500.0  # Alert when daily profit exceeds this
    daily_loss_alert: float = -200.0   # Alert when daily loss exceeds this
    
    # Position Alerts
    position_count_alert: int = 5      # Alert when open positions exceed this
    exposure_alert_lots: float = 0.5   # Alert when total exposure exceeds this
    
    # Performance Alerts
    win_streak_alert: int = 5          # Alert on winning streak
    loss_streak_alert: int = 3         # Alert on losing streak
    drawdown_warning_pct: float = 3.0  # Warning at this drawdown %
    drawdown_critical_pct: float = 5.0 # Critical alert at this drawdown %
    
    # Equity Alerts
    equity_high_alert: bool = True     # Alert on new equity high
    milestone_alerts: bool = True      # Alert on milestone achievements
    
    # Market Alerts
    volatility_alert_atr_multiple: float = 2.0  # Alert when ATR exceeds normal by this multiple
    spread_alert_pips: float = 5.0     # Alert when spread exceeds this
    
    # News Alerts
    news_blackout_alert: bool = True   # Alert when entering news blackout
    high_impact_news_alert: bool = True  # Alert for upcoming high-impact news
    
    # System Alerts
    connection_lost_alert: bool = True # Alert on MT5 disconnect
    error_alert: bool = True           # Alert on bot errors
    daily_summary_alert: bool = True   # Send daily summary
    weekly_review_alert: bool = True   # Send weekly review
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'profit_alert_usd': self.profit_alert_usd,
            'loss_alert_usd': self.loss_alert_usd,
            'daily_profit_alert': self.daily_profit_alert,
            'daily_loss_alert': self.daily_loss_alert,
            'position_count_alert': self.position_count_alert,
            'exposure_alert_lots': self.exposure_alert_lots,
            'win_streak_alert': self.win_streak_alert,
            'loss_streak_alert': self.loss_streak_alert,
            'drawdown_warning_pct': self.drawdown_warning_pct,
            'drawdown_critical_pct': self.drawdown_critical_pct,
            'equity_high_alert': self.equity_high_alert,
            'milestone_alerts': self.milestone_alerts,
            'volatility_alert_atr_multiple': self.volatility_alert_atr_multiple,
            'spread_alert_pips': self.spread_alert_pips,
            'news_blackout_alert': self.news_blackout_alert,
            'high_impact_news_alert': self.high_impact_news_alert,
            'connection_lost_alert': self.connection_lost_alert,
            'error_alert': self.error_alert,
            'daily_summary_alert': self.daily_summary_alert,
            'weekly_review_alert': self.weekly_review_alert
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AlertThresholds':
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


class AlertConfig:
    """
    Manages alert configuration with persistence.
    """
    
    def __init__(self, config_file: str = CONFIG_FILE):
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.thresholds = self._load()
    
    def _load(self) -> AlertThresholds:
        """Load config from file."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                logger.info("Loaded alert configuration")
                return AlertThresholds.from_dict(data)
            except Exception as e:
                logger.warning(f"Error loading alert config: {e}, using defaults")
        
        return AlertThresholds()
    
    def save(self):
        """Save config to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.thresholds.to_dict(), f, indent=2)
            logger.info("Alert configuration saved")
        except Exception as e:
            logger.error(f"Error saving alert config: {e}")
    
    def update(self, **kwargs):
        """Update threshold values."""
        for key, value in kwargs.items():
            if hasattr(self.thresholds, key):
                setattr(self.thresholds, key, value)
        self.save()
    
    def should_alert_profit(self, pnl: float) -> bool:
        """Check if profit should trigger alert."""
        return pnl >= self.thresholds.profit_alert_usd
    
    def should_alert_loss(self, pnl: float) -> bool:
        """Check if loss should trigger alert."""
        return pnl <= self.thresholds.loss_alert_usd
    
    def should_alert_streak(self, win_streak: int, loss_streak: int) -> Optional[str]:
        """Check if streak should trigger alert."""
        if win_streak >= self.thresholds.win_streak_alert:
            return f"🔥 {win_streak} consecutive wins!"
        if loss_streak >= self.thresholds.loss_streak_alert:
            return f"⚠️ {loss_streak} consecutive losses"
        return None
    
    def should_alert_drawdown(self, drawdown_pct: float) -> Optional[str]:
        """Check if drawdown should trigger alert."""
        if drawdown_pct >= self.thresholds.drawdown_critical_pct:
            return f"🚨 CRITICAL: {drawdown_pct:.1f}% drawdown"
        if drawdown_pct >= self.thresholds.drawdown_warning_pct:
            return f"⚠️ WARNING: {drawdown_pct:.1f}% drawdown"
        return None
    
    def should_alert_exposure(self, total_lots: float) -> bool:
        """Check if exposure should trigger alert."""
        return total_lots >= self.thresholds.exposure_alert_lots
    
    def should_alert_positions(self, count: int) -> bool:
        """Check if position count should trigger alert."""
        return count >= self.thresholds.position_count_alert


# Global instance
_config: Optional[AlertConfig] = None


def get_alert_config() -> AlertConfig:
    """Get the global alert config."""
    global _config
    if _config is None:
        _config = AlertConfig()
    return _config
