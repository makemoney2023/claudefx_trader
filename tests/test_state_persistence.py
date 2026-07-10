"""Tests for state persistence -- verifying all 5 new persistence gaps are covered."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
from trading_bot.utils.state_persistence import StatePersistence, save_full_state, load_full_state


class TestStatePersistenceMethods:
    """Test individual persistence methods."""
    
    def _make_persistence(self):
        tf = os.path.join(tempfile.gettempdir(), f'test_state_{id(self)}.json')
        sp = StatePersistence(tf)
        return sp, tf
    
    def test_daily_risk_used_persisted(self):
        sp, tf = self._make_persistence()
        try:
            sp.save_daily_stats(3, -5.50, '2026-02-16', daily_risk_used=0.06)
            loaded = sp.load_daily_stats()
            assert loaded['daily_risk_used'] == 0.06
            assert loaded['trades'] == 3
            assert loaded['pnl'] == -5.50
            assert loaded['date'] == '2026-02-16'
        finally:
            os.remove(tf)
    
    def test_daily_risk_defaults_to_zero(self):
        sp, tf = self._make_persistence()
        try:
            loaded = sp.load_daily_stats()
            assert loaded['daily_risk_used'] == 0.0
        finally:
            if os.path.exists(tf):
                os.remove(tf)
    
    def test_signal_hashes_persistence(self):
        sp, tf = self._make_persistence()
        try:
            now = datetime.now()
            hashes = {
                'hash_abc': now.isoformat(),
                'hash_def': (now - timedelta(minutes=10)).isoformat(),
            }
            sp.save_signal_hashes(hashes)
            loaded = sp.load_signal_hashes()
            assert len(loaded) == 2
            assert 'hash_abc' in loaded
            assert 'hash_def' in loaded
        finally:
            os.remove(tf)
    
    def test_signal_hashes_default_empty(self):
        sp, tf = self._make_persistence()
        try:
            loaded = sp.load_signal_hashes()
            assert loaded == {}
        finally:
            if os.path.exists(tf):
                os.remove(tf)
    
    def test_reversal_cooldowns_persistence(self):
        sp, tf = self._make_persistence()
        try:
            now = datetime.now()
            cooldowns = {
                'BTCUSD': now.isoformat(),
                'XAUUSD': (now - timedelta(minutes=30)).isoformat(),
            }
            sp.save_reversal_cooldowns(cooldowns)
            loaded = sp.load_reversal_cooldowns()
            assert 'BTCUSD' in loaded
            assert 'XAUUSD' in loaded
        finally:
            os.remove(tf)
    
    def test_pending_order_metadata_persistence(self):
        sp, tf = self._make_persistence()
        try:
            now = datetime.now()
            metadata = {
                '12345': {
                    'expiration': (now + timedelta(hours=2)).isoformat(),
                    'symbol': 'BTCUSD',
                    'direction': 'short',
                    'order_type': 'sell_limit',
                    'price': 67500.0,
                },
                '67890': {
                    'expiration': (now + timedelta(hours=4)).isoformat(),
                    'symbol': 'XAUUSD',
                    'direction': 'long',
                    'order_type': 'buy_limit',
                    'price': 4950.0,
                },
            }
            sp.save_pending_order_metadata(metadata)
            loaded = sp.load_pending_order_metadata()
            assert '12345' in loaded
            assert loaded['12345']['symbol'] == 'BTCUSD'
            assert '67890' in loaded
            assert loaded['67890']['price'] == 4950.0
        finally:
            os.remove(tf)


class TestSaveFullState:
    """Test save_full_state includes new fields."""
    
    def test_save_captures_daily_risk(self):
        bot = MagicMock()
        bot.win_streak = 2
        bot.loss_streak = 0
        bot.daily_trades = 3
        bot.daily_pnl = -5.0
        bot.last_reset_date = datetime.now().date()
        bot._notified_milestones = set()
        bot.scaling_manager = None
        bot.goal_tracker = None
        bot._signal_hash_expiry = {}
        bot._reversal_cooldowns = {}
        bot.pending_order_manager = MagicMock()
        bot.pending_order_manager.pending_orders = {}
        
        # Set up risk manager with daily_risk_used
        bot.risk_manager = MagicMock()
        bot.risk_manager.daily_risk_used = 0.04
        
        tf = os.path.join(tempfile.gettempdir(), 'test_save_full.json')
        from trading_bot.utils.state_persistence import _persistence, StatePersistence
        import trading_bot.utils.state_persistence as sp_module
        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        
        try:
            result = save_full_state(bot)
            assert result is True
            
            # Verify daily_risk_used was saved
            loaded = sp_module._persistence.load_daily_stats()
            assert loaded['daily_risk_used'] == 0.04
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)
    
    def test_save_captures_signal_hashes(self):
        bot = MagicMock()
        bot.win_streak = 0
        bot.loss_streak = 0
        bot.daily_trades = 0
        bot.daily_pnl = 0.0
        bot.last_reset_date = None
        bot._notified_milestones = set()
        bot.scaling_manager = None
        bot.goal_tracker = None
        bot.risk_manager = None
        bot.pending_order_manager = MagicMock()
        bot.pending_order_manager.pending_orders = {}
        
        now = datetime.now()
        bot._signal_hash_expiry = {
            'hash_123': now,
            'hash_456': now - timedelta(minutes=5),
        }
        bot._reversal_cooldowns = {
            'BTCUSD': now - timedelta(minutes=20),
        }
        
        tf = os.path.join(tempfile.gettempdir(), 'test_save_hashes.json')
        import trading_bot.utils.state_persistence as sp_module
        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        
        try:
            result = save_full_state(bot)
            assert result is True
            
            loaded_hashes = sp_module._persistence.load_signal_hashes()
            assert len(loaded_hashes) == 2
            
            loaded_cooldowns = sp_module._persistence.load_reversal_cooldowns()
            assert 'BTCUSD' in loaded_cooldowns
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)

    def test_save_captures_pending_reservation_ownership(self):
        bot = MagicMock()
        bot.win_streak = 0
        bot.loss_streak = 0
        bot.daily_trades = 1
        bot.daily_pnl = 0.0
        bot.last_reset_date = datetime.now().date()
        bot._notified_milestones = set()
        bot.scaling_manager = None
        bot.goal_tracker = None
        bot.risk_manager = MagicMock(daily_risk_used=0.012)
        bot._signal_hash_expiry = {}
        bot._reversal_cooldowns = {}
        bot.pending_order_manager = MagicMock()
        order = PendingOrder(
            ticket=40401,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.08,
            stop_loss=1.075,
            take_profit=1.09,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.012,
            reservation_id="reservation-40401",
        )
        bot.pending_order_manager.pending_orders = {order.ticket: order}

        tf = os.path.join(tempfile.gettempdir(), "test_save_pending_ownership.json")
        import trading_bot.utils.state_persistence as sp_module

        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        try:
            assert save_full_state(bot) is True
            metadata = sp_module._persistence.load_pending_order_metadata()
            assert metadata["40401"]["reservation_id"] == "reservation-40401"
            assert metadata["40401"]["risk_percent"] == 0.012
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)


class TestLoadFullState:
    """Test load_full_state restores new fields."""
    
    def test_load_restores_daily_risk_same_day(self):
        from datetime import date
        import trading_bot.utils.state_persistence as sp_module
        
        tf = os.path.join(tempfile.gettempdir(), 'test_load_risk.json')
        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        
        try:
            # Save state with today's date
            sp_module._persistence.save_daily_stats(
                2, -3.0, date.today().isoformat(), daily_risk_used=0.04
            )
            
            bot = MagicMock()
            bot.win_streak = 0
            bot.loss_streak = 0
            bot._notified_milestones = set()
            bot.scaling_manager = None
            bot.session_analytics = None
            bot._recent_signal_hashes = set()
            bot._signal_hash_expiry = {}
            bot._reversal_cooldowns = {}
            bot.risk_manager = MagicMock()
            bot.risk_manager.daily_risk_used = 0.0
            
            result = load_full_state(bot)
            assert result is True
            assert bot.risk_manager.daily_risk_used == 0.04
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)
    
    def test_load_does_not_restore_risk_different_day(self):
        from datetime import date
        import trading_bot.utils.state_persistence as sp_module
        
        tf = os.path.join(tempfile.gettempdir(), 'test_load_risk_old.json')
        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        
        try:
            # Save state with yesterday's date
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            sp_module._persistence.save_daily_stats(
                2, -3.0, yesterday, daily_risk_used=0.04
            )
            
            bot = MagicMock()
            bot.win_streak = 0
            bot.loss_streak = 0
            bot._notified_milestones = set()
            bot.scaling_manager = None
            bot.session_analytics = None
            bot._recent_signal_hashes = set()
            bot._signal_hash_expiry = {}
            bot._reversal_cooldowns = {}
            bot.risk_manager = MagicMock()
            bot.risk_manager.daily_risk_used = 0.0
            
            result = load_full_state(bot)
            assert result is True
            # Should NOT have restored because date differs
            assert bot.risk_manager.daily_risk_used == 0.0
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)
    
    def test_load_restores_signal_hashes_filters_expired(self):
        import trading_bot.utils.state_persistence as sp_module
        
        tf = os.path.join(tempfile.gettempdir(), 'test_load_hashes.json')
        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        
        try:
            now = datetime.now()
            # One recent hash (5 min ago), one expired (2 hours ago)
            sp_module._persistence.save_signal_hashes({
                'recent_hash': (now - timedelta(minutes=5)).isoformat(),
                'old_hash': (now - timedelta(hours=2)).isoformat(),
            })
            
            bot = MagicMock()
            bot.win_streak = 0
            bot.loss_streak = 0
            bot._notified_milestones = set()
            bot.scaling_manager = None
            bot.session_analytics = None
            bot.risk_manager = None
            bot._recent_signal_hashes = set()
            bot._signal_hash_expiry = {}
            bot._reversal_cooldowns = {}
            
            result = load_full_state(bot)
            assert result is True
            # Only the recent hash should be restored (30 min window)
            assert 'recent_hash' in bot._recent_signal_hashes
            assert 'old_hash' not in bot._recent_signal_hashes
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)
    
    def test_load_restores_reversal_cooldowns_filters_expired(self):
        import trading_bot.utils.state_persistence as sp_module
        
        tf = os.path.join(tempfile.gettempdir(), 'test_load_cooldowns.json')
        old = sp_module._persistence
        sp_module._persistence = StatePersistence(tf)
        
        try:
            now = datetime.now()
            sp_module._persistence.save_reversal_cooldowns({
                'BTCUSD': (now - timedelta(minutes=30)).isoformat(),  # Within 1hr
                'XAUUSD': (now - timedelta(hours=2)).isoformat(),  # Expired
            })
            
            bot = MagicMock()
            bot.win_streak = 0
            bot.loss_streak = 0
            bot._notified_milestones = set()
            bot.scaling_manager = None
            bot.session_analytics = None
            bot.risk_manager = None
            bot._recent_signal_hashes = set()
            bot._signal_hash_expiry = {}
            bot._reversal_cooldowns = {}
            
            result = load_full_state(bot)
            assert result is True
            assert 'BTCUSD' in bot._reversal_cooldowns
            assert 'XAUUSD' not in bot._reversal_cooldowns
        finally:
            sp_module._persistence = old
            if os.path.exists(tf):
                os.remove(tf)


class TestPositionCloseReason:
    """Test close_reason field on PositionStateModel."""
    
    def test_position_state_model_has_close_reason(self):
        from trading_bot.api.database import PositionStateModel
        assert hasattr(PositionStateModel, 'close_reason')
    
    def test_close_reason_in_migration_list(self):
        """Verify close_reason is in the DB migration list."""
        import inspect
        from trading_bot.api.database import init_db
        source = inspect.getsource(init_db)
        assert 'close_reason' in source
        assert 'position_states' in source
