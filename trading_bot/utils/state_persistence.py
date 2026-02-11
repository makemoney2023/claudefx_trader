"""
State Persistence for Trading Bot.

Saves and loads bot state to survive restarts:
- Session analytics
- Scaling manager stats
- Goal tracker snapshots
- Win/loss streaks
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .logging import get_logger

logger = get_logger(__name__)

STATE_FILE = "data/bot_state.json"


class StatePersistence:
    """
    Persist bot state to JSON file.
    
    Automatically saves on changes and loads on startup.
    """
    
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state: Dict[str, Any] = {}
        self._load()
    
    def _load(self):
        """Load state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    self._state = json.load(f)
                logger.info(f"Loaded bot state from {self.state_file}")
            except Exception as e:
                logger.error(f"Error loading state: {e}")
                self._state = {}
        else:
            logger.info("No existing state file, starting fresh")
            self._state = {}
    
    def _save(self):
        """Save state to file."""
        try:
            self._state['last_saved'] = datetime.now().isoformat()
            with open(self.state_file, 'w') as f:
                json.dump(self._state, f, indent=2, default=str)
            logger.debug("State saved")
        except Exception as e:
            logger.error(f"Error saving state: {e}")
    
    def set(self, key: str, value: Any):
        """Set a state value and save."""
        self._state[key] = value
        self._save()
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self._state.get(key, default)
    
    def save_session_analytics(self, analytics_data: Dict[str, Any]):
        """Save session analytics state."""
        self.set('session_analytics', analytics_data)
    
    def load_session_analytics(self) -> Optional[Dict[str, Any]]:
        """Load session analytics state."""
        return self.get('session_analytics')
    
    def save_scaling_manager(self, scaling_data: Dict[str, Any]):
        """Save scaling manager state."""
        self.set('scaling_manager', scaling_data)
    
    def load_scaling_manager(self) -> Optional[Dict[str, Any]]:
        """Load scaling manager state."""
        return self.get('scaling_manager')
    
    def save_goal_tracker(self, goal_data: Dict[str, Any]):
        """Save goal tracker state."""
        self.set('goal_tracker', goal_data)
    
    def load_goal_tracker(self) -> Optional[Dict[str, Any]]:
        """Load goal tracker state."""
        return self.get('goal_tracker')
    
    def save_streaks(self, win_streak: int, loss_streak: int):
        """Save win/loss streaks."""
        self.set('streaks', {
            'win_streak': win_streak,
            'loss_streak': loss_streak
        })
    
    def load_streaks(self) -> tuple:
        """Load win/loss streaks."""
        streaks = self.get('streaks', {'win_streak': 0, 'loss_streak': 0})
        return streaks['win_streak'], streaks['loss_streak']
    
    def save_notified_milestones(self, milestones: set):
        """Save notified milestones."""
        self.set('notified_milestones', list(milestones))
    
    def load_notified_milestones(self) -> set:
        """Load notified milestones."""
        return set(self.get('notified_milestones', []))
    
    def save_daily_stats(self, daily_trades: int, daily_pnl: float, date: str):
        """Save daily statistics."""
        self.set('daily_stats', {
            'trades': daily_trades,
            'pnl': daily_pnl,
            'date': date
        })
    
    def load_daily_stats(self) -> Dict[str, Any]:
        """Load daily statistics."""
        return self.get('daily_stats', {'trades': 0, 'pnl': 0.0, 'date': ''})


# Global instance
_persistence: Optional[StatePersistence] = None


def get_persistence() -> StatePersistence:
    """Get the global persistence instance."""
    global _persistence
    if _persistence is None:
        _persistence = StatePersistence()
    return _persistence


def save_full_state(bot) -> bool:
    """
    Save complete bot state for graceful shutdown.
    
    Args:
        bot: TradingBot instance
        
    Returns:
        True if successful
    """
    try:
        persistence = get_persistence()
        
        # Save streaks
        persistence.save_streaks(bot.win_streak, bot.loss_streak)
        
        # Save daily stats
        persistence.save_daily_stats(
            bot.daily_trades,
            bot.daily_pnl,
            bot.last_reset_date.isoformat() if bot.last_reset_date else ''
        )
        
        # Save notified milestones
        if hasattr(bot, '_notified_milestones'):
            persistence.save_notified_milestones(bot._notified_milestones)
        
        # Save scaling manager state
        if bot.scaling_manager:
            persistence.save_scaling_manager({
                'recent_trades': bot.scaling_manager.recent_trades,
                'daily_high_equity': bot.scaling_manager.daily_high_equity,
                'weekly_high_equity': bot.scaling_manager.weekly_high_equity,
                'current_mode': bot.scaling_manager.current_mode.value
            })
        
        # Save session analytics
        if bot.session_analytics:
            persistence.save_session_analytics({
                'trade_history': bot.session_analytics.trade_history,
                'session_stats': {
                    session.value: {
                        'total_trades': stats.total_trades,
                        'wins': stats.wins,
                        'losses': stats.losses,
                        'total_pnl': stats.total_pnl,
                        'total_r': stats.total_r,
                        'best_trade_r': stats.best_trade_r,
                        'worst_trade_r': stats.worst_trade_r,
                        'symbols_traded': stats.symbols_traded
                    }
                    for session, stats in bot.session_analytics.session_stats.items()
                }
            })
        
        # Save goal tracker snapshots
        if bot.goal_tracker:
            persistence.save_goal_tracker({
                'snapshots': [
                    {'equity': s.equity, 'timestamp': s.timestamp.isoformat()}
                    for s in bot.goal_tracker.equity_history[-100:]  # Keep last 100
                ]
            })
        
        logger.info("Bot state saved successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error saving bot state: {e}")
        return False


def load_full_state(bot) -> bool:
    """
    Load complete bot state on startup.
    
    Args:
        bot: TradingBot instance
        
    Returns:
        True if state was loaded
    """
    try:
        persistence = get_persistence()
        
        # Load streaks
        win_streak, loss_streak = persistence.load_streaks()
        bot.win_streak = win_streak
        bot.loss_streak = loss_streak
        
        # Load daily stats
        daily_stats = persistence.load_daily_stats()
        if daily_stats.get('date'):
            from datetime import date
            try:
                bot.last_reset_date = date.fromisoformat(daily_stats['date'])
                bot.daily_trades = daily_stats.get('trades', 0)
                bot.daily_pnl = daily_stats.get('pnl', 0.0)
            except:
                pass
        
        # Load notified milestones
        bot._notified_milestones = persistence.load_notified_milestones()
        
        # Load scaling manager state
        scaling_data = persistence.load_scaling_manager()
        if scaling_data and bot.scaling_manager:
            bot.scaling_manager.recent_trades = scaling_data.get('recent_trades', [])
            
            # Sanity-check high watermarks against current equity to prevent
            # stale peaks from causing permanent drawdown kill-switches.
            # If persisted high is >10% above current equity, reset to current.
            # This ensures we never restore a watermark that would immediately
            # trigger a drawdown circuit breaker (6% weekly limit).
            current_eq = bot.scaling_manager.weekly_high_equity  # Set during __init__ from live equity
            saved_daily = scaling_data.get('daily_high_equity', 0) or 0
            saved_weekly = scaling_data.get('weekly_high_equity', 0) or 0
            
            # If saved values are 0 or invalid, use current equity from live account
            if saved_daily <= 0:
                saved_daily = current_eq
            if saved_weekly <= 0:
                saved_weekly = current_eq
            
            # Cap: if saved high is >5% above current equity, reset to current.
            # The weekly drawdown limit is 6%, so a saved peak >5% above current
            # would immediately trigger a circuit breaker on startup.
            if current_eq > 0:
                if saved_daily > current_eq * 1.05:
                    logger.warning(
                        f"Resetting stale daily_high: {saved_daily:.2f} -> {current_eq:.2f} "
                        f"(was {((saved_daily/current_eq)-1)*100:.1f}% above current)"
                    )
                    saved_daily = current_eq
                if saved_weekly > current_eq * 1.05:
                    logger.warning(
                        f"Resetting stale weekly_high: {saved_weekly:.2f} -> {current_eq:.2f} "
                        f"(was {((saved_weekly/current_eq)-1)*100:.1f}% above current)"
                    )
                    saved_weekly = current_eq
            
            bot.scaling_manager.daily_high_equity = saved_daily
            bot.scaling_manager.weekly_high_equity = saved_weekly
        
        # Load session analytics
        session_data = persistence.load_session_analytics()
        if session_data and bot.session_analytics:
            bot.session_analytics.trade_history = session_data.get('trade_history', [])
            # Restore session stats
            for session_name, stats in session_data.get('session_stats', {}).items():
                from ..services.session_analytics import TradingSession
                try:
                    session = TradingSession(session_name)
                    if session in bot.session_analytics.session_stats:
                        s = bot.session_analytics.session_stats[session]
                        s.total_trades = stats.get('total_trades', 0)
                        s.wins = stats.get('wins', 0)
                        s.losses = stats.get('losses', 0)
                        s.total_pnl = stats.get('total_pnl', 0.0)
                        s.total_r = stats.get('total_r', 0.0)
                        s.best_trade_r = stats.get('best_trade_r', 0.0)
                        s.worst_trade_r = stats.get('worst_trade_r', 0.0)
                        s.symbols_traded = stats.get('symbols_traded', {})
                except:
                    pass
        
        logger.info("Bot state loaded successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error loading bot state: {e}")
        return False
