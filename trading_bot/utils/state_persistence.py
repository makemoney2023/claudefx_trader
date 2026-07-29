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
    
    def save_daily_stats(self, daily_trades: int, daily_pnl: float, date: str,
                         daily_risk_used: float = 0.0):
        """Save daily statistics including risk accumulator."""
        self.set('daily_stats', {
            'trades': daily_trades,
            'pnl': daily_pnl,
            'date': date,
            'daily_risk_used': daily_risk_used,
        })
    
    def load_daily_stats(self) -> Dict[str, Any]:
        """Load daily statistics."""
        return self.get('daily_stats', {
            'trades': 0, 'pnl': 0.0, 'date': '', 'daily_risk_used': 0.0
        })
    
    def save_signal_hashes(self, hashes_with_expiry: Dict[str, str]):
        """Save signal dedup hashes with their expiry timestamps (ISO format)."""
        self.set('signal_hashes', hashes_with_expiry)
    
    def load_signal_hashes(self) -> Dict[str, str]:
        """Load signal dedup hashes. Returns {hash: expiry_iso_string}."""
        return self.get('signal_hashes', {})
    
    def save_reversal_cooldowns(self, cooldowns: Dict[str, str]):
        """Save reversal cooldowns. Keys=symbols, values=ISO timestamp strings."""
        self.set('reversal_cooldowns', cooldowns)
    
    def load_reversal_cooldowns(self) -> Dict[str, str]:
        """Load reversal cooldowns."""
        return self.get('reversal_cooldowns', {})
    
    def save_pending_order_metadata(self, metadata: Dict[str, Any]):
        """Save pending order metadata (ticket -> expiration, etc.) for restart recovery."""
        self.set('pending_orders', metadata)
    
    def load_pending_order_metadata(self) -> Dict[str, Any]:
        """Load pending order metadata."""
        return self.get('pending_orders', {})


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
        
        # Save daily stats (including daily_risk_used)
        daily_risk = 0.0
        if hasattr(bot, 'risk_manager') and bot.risk_manager:
            daily_risk = bot.risk_manager.daily_risk_used
        persistence.save_daily_stats(
            bot.daily_trades,
            bot.daily_pnl,
            bot.last_reset_date.isoformat() if bot.last_reset_date else '',
            daily_risk_used=daily_risk,
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
        
        # Session analytics: No longer persisted to JSON.
        # Data is loaded from the SQLite database on startup via
        # SessionAnalytics._load_from_database() which is the single source of truth.
        
        # Save goal tracker snapshots
        if bot.goal_tracker:
            persistence.save_goal_tracker({
                'snapshots': [
                    {'equity': s.equity, 'timestamp': s.timestamp.isoformat()}
                    for s in bot.goal_tracker.equity_history[-100:]  # Keep last 100
                ]
            })
        
        # Save signal dedup hashes with expiry
        if hasattr(bot, '_signal_hash_expiry') and bot._signal_hash_expiry:
            hashes = {
                h: ts.isoformat()
                for h, ts in bot._signal_hash_expiry.items()
            }
            persistence.save_signal_hashes(hashes)
        
        # Save reversal cooldowns
        if hasattr(bot, '_reversal_cooldowns') and bot._reversal_cooldowns:
            cooldowns = {
                symbol: ts.isoformat()
                for symbol, ts in bot._reversal_cooldowns.items()
            }
            persistence.save_reversal_cooldowns(cooldowns)
        
        # Save loss cooldowns
        if hasattr(bot, '_symbol_loss_cooldowns') and bot._symbol_loss_cooldowns:
            loss_cds = {
                symbol: ts.isoformat()
                for symbol, ts in bot._symbol_loss_cooldowns.items()
            }
            persistence.set('loss_cooldowns', loss_cds)
        
        # Save same-direction circuit breaker streaks
        if getattr(bot, '_direction_loss_tracker', None) is not None:
            persistence.set(
                'direction_loss_streaks',
                bot._direction_loss_tracker.to_dict(),
            )
        
        # Save pending order metadata (expiration times)
        if hasattr(bot, 'pending_order_manager') and bot.pending_order_manager:
            po_meta = {}
            for ticket, order in bot.pending_order_manager.pending_orders.items():
                if order.is_active:
                    po_meta[str(ticket)] = {
                        'expiration': order.expiration.isoformat(),
                        'symbol': order.symbol,
                        'direction': order.direction,
                        'order_type': order.order_type,
                        'price': order.price,
                        'risk_percent': getattr(order, 'risk_percent', None),
                        'reservation_id': getattr(order, 'reservation_id', None),
                    }
            persistence.save_pending_order_metadata(po_meta)
        
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
        
        # Load daily stats (including daily_risk_used)
        daily_stats = persistence.load_daily_stats()
        if daily_stats.get('date'):
            from datetime import date
            try:
                saved_date = date.fromisoformat(daily_stats['date'])
                bot.last_reset_date = saved_date
                # Only restore if same day -- risk resets daily
                if saved_date == date.today():
                    bot.daily_trades = daily_stats.get('trades', 0)
                    bot.daily_pnl = daily_stats.get('pnl', 0.0)
                    # Restore daily risk used
                    saved_risk = daily_stats.get('daily_risk_used', 0.0)
                    if hasattr(bot, 'risk_manager') and bot.risk_manager and saved_risk > 0:
                        bot.risk_manager.daily_risk_used = saved_risk
                        logger.info(
                            f"Restored daily_risk_used: {saved_risk*100:.1f}% "
                            f"(from same-day state)"
                        )
                else:
                    logger.info(
                        f"State date {saved_date} != today {date.today()}, "
                        f"daily stats reset to zero"
                    )
            except Exception:
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
            saved_mode = scaling_data.get('current_mode')
            if saved_mode:
                try:
                    from ..services.scaling_manager import TradingMode
                    bot.scaling_manager.current_mode = TradingMode(saved_mode.lower())
                    logger.info(f"Restored trading mode: {saved_mode}")
                except ValueError:
                    logger.warning(f"Unknown persisted trading mode '{saved_mode}', keeping default")
        
        # Session analytics: Do NOT restore from JSON.
        # SessionAnalytics._load_from_database() is the single source of truth —
        # it reads closed bot trades directly from the SQLite database on init.
        # The old JSON state contained corrupted data from MT5 history sync
        # (e.g. inflated P/L from synced deals that weren't bot trades).
        if bot.session_analytics:
            logger.info("Session analytics loaded from database (not from JSON state file)")
        
        # Load signal dedup hashes (filter expired)
        if hasattr(bot, '_recent_signal_hashes') and hasattr(bot, '_signal_hash_expiry'):
            saved_hashes = persistence.load_signal_hashes()
            if saved_hashes:
                now = datetime.now()
                restored = 0
                for h, ts_str in saved_hashes.items():
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        # Keep hashes from the last 30 minutes only
                        if (now - ts).total_seconds() < 1800:
                            bot._recent_signal_hashes.add(h)
                            bot._signal_hash_expiry[h] = ts
                            restored += 1
                    except (ValueError, TypeError):
                        pass
                if restored > 0:
                    logger.info(f"Restored {restored} signal dedup hashes")
        
        # Load reversal cooldowns (filter expired >1 hour)
        if hasattr(bot, '_reversal_cooldowns'):
            saved_cooldowns = persistence.load_reversal_cooldowns()
            if saved_cooldowns:
                now = datetime.now()
                for symbol, ts_str in saved_cooldowns.items():
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if (now - ts).total_seconds() < 3600:
                            bot._reversal_cooldowns[symbol] = ts
                    except (ValueError, TypeError):
                        pass
                if bot._reversal_cooldowns:
                    logger.info(
                        f"Restored reversal cooldowns for: "
                        f"{list(bot._reversal_cooldowns.keys())}"
                    )
        
        # Load loss cooldowns (filter expired)
        if hasattr(bot, '_symbol_loss_cooldowns'):
            saved_loss_cds = persistence.get('loss_cooldowns', {})
            if saved_loss_cds:
                now = datetime.now()
                for symbol, ts_str in saved_loss_cds.items():
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts > now:
                            bot._symbol_loss_cooldowns[symbol] = ts
                    except (ValueError, TypeError):
                        pass
                if bot._symbol_loss_cooldowns:
                    logger.info(
                        f"Restored loss cooldowns for: "
                        f"{list(bot._symbol_loss_cooldowns.keys())}"
                    )
        
        # Load same-direction circuit breaker streaks (stale days are ignored
        # by the tracker's own date check, so no filtering needed here)
        if hasattr(bot, '_direction_loss_tracker'):
            saved_streaks = persistence.get('direction_loss_streaks', {})
            if saved_streaks:
                from ..services.direction_circuit_breaker import DirectionLossTracker
                bot._direction_loss_tracker = DirectionLossTracker.from_dict(
                    saved_streaks
                )
                logger.info(
                    f"Restored direction loss streaks: {list(saved_streaks.keys())}"
                )
        
        logger.info("Bot state loaded successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error loading bot state: {e}")
        return False
