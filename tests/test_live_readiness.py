"""
Tests for Live Trading Readiness Fixes.

Covers:
- Ticket fallback lookup (order_ticket field on Position)
- WAL mode enabled on init_db
- Database backup function
- session.rollback on errors
- PositionStateRepository atomic upsert via merge
- datetime.utcnow replaced with datetime.now(timezone.utc)
"""

import asyncio
import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch


# =============================================================================
# Position dataclass: order_ticket field
# =============================================================================

class TestPositionOrderTicket:

    def test_order_ticket_defaults_to_none(self):
        from trading_bot.execution.position_manager import Position
        pos = Position(
            ticket=12345,
            symbol="XAUUSD",
            direction="long",
            volume=0.1,
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            open_time=datetime.now(),
        )
        assert pos.order_ticket is None

    def test_order_ticket_can_be_set(self):
        from trading_bot.execution.position_manager import Position
        pos = Position(
            ticket=99999,
            symbol="BTCUSD",
            direction="short",
            volume=0.05,
            entry_price=50000.0,
            stop_loss=51000.0,
            take_profit=48000.0,
            open_time=datetime.now(),
            order_ticket=88888,
        )
        assert pos.order_ticket == 88888

    def test_order_ticket_mutable(self):
        from trading_bot.execution.position_manager import Position
        pos = Position(
            ticket=12345,
            symbol="XAUUSD",
            direction="long",
            volume=0.1,
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            open_time=datetime.now(),
        )
        pos.order_ticket = 77777
        assert pos.order_ticket == 77777


# =============================================================================
# Database backup function
# =============================================================================

class TestDatabaseBackup:

    def test_backup_returns_none_when_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from trading_bot.api.database import backup_database
        result = backup_database()
        assert result is None

    def test_backup_creates_copy(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_file = tmp_path / "trading_bot.db"
        db_file.write_text("test data")
        from trading_bot.api.database import backup_database
        result = backup_database()
        assert result is not None
        assert result.exists()
        assert result.read_text() == "test data"

    def test_backup_prunes_old_copies(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_file = tmp_path / "trading_bot.db"
        db_file.write_text("data")
        backup_dir = tmp_path / "backups" / "db"
        backup_dir.mkdir(parents=True)
        for i in range(10):
            (backup_dir / f"trading_bot_2026010{i}_000000.db").write_text(f"old_{i}")
        from trading_bot.api.database import backup_database
        backup_database(max_backups=3)
        remaining = list(backup_dir.glob("trading_bot_*.db"))
        assert len(remaining) == 3


# =============================================================================
# WAL mode in init_db
# =============================================================================

class TestWALMode:

    def test_init_db_enables_wal(self):
        """Verify that init_db source code calls PRAGMA journal_mode=WAL."""
        import inspect
        from trading_bot.api.database import init_db
        source = inspect.getsource(init_db)
        assert "PRAGMA journal_mode=WAL" in source
        assert "PRAGMA synchronous=NORMAL" in source


# =============================================================================
# Rollback presence verification
# =============================================================================

class TestRollbackPresence:

    def test_save_trade_to_db_has_rollback(self):
        import inspect
        from trading_bot.main import save_trade_to_db
        source = inspect.getsource(save_trade_to_db)
        assert "rollback" in source

    def test_save_signal_to_db_has_rollback(self):
        import inspect
        from trading_bot.main import save_signal_to_db
        source = inspect.getsource(save_signal_to_db)
        assert "rollback" in source


# =============================================================================
# PendingOrderManager filled_order_map
# =============================================================================

class TestFilledOrderMap:

    def test_filled_order_map_exists(self):
        from trading_bot.services.pending_order_manager import PendingOrderManager
        mgr = PendingOrderManager(mt5_client=None)
        assert hasattr(mgr, 'filled_order_map')
        assert isinstance(mgr.filled_order_map, dict)
        assert len(mgr.filled_order_map) == 0


# =============================================================================
# MT5 client ensure_connected before critical ops
# =============================================================================

class TestMT5EnsureConnected:

    def test_place_order_source_has_ensure_connected(self):
        import inspect
        from trading_bot.mt5.client import MT5Client
        source = inspect.getsource(MT5Client.place_order)
        assert "ensure_connected" in source

    def test_modify_position_source_has_ensure_connected(self):
        import inspect
        from trading_bot.mt5.client import MT5Client
        source = inspect.getsource(MT5Client.modify_position)
        assert "ensure_connected" in source


# =============================================================================
# No datetime.utcnow() remaining
# =============================================================================

class TestNoUtcnow:

    def _check_file_no_utcnow(self, filepath: str):
        content = Path(filepath).read_text(encoding="utf-8")
        occurrences = content.count("datetime.utcnow()")
        assert occurrences == 0, f"{filepath} still has {occurrences} datetime.utcnow() calls"

    def test_main_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/main.py")

    def test_database_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/api/database.py")

    def test_trade_learning_service_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/services/trade_learning_service.py")

    def test_backtest_routes_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/api/routes/backtest.py")

    def test_market_hours_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/utils/market_hours.py")

    def test_session_analytics_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/services/session_analytics.py")

    def test_trades_routes_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/api/routes/trades.py")

    def test_performance_routes_no_utcnow(self):
        self._check_file_no_utcnow("trading_bot/api/routes/performance.py")


# =============================================================================
# PositionStateRepository uses merge
# =============================================================================

class TestPositionStateRepoMerge:

    def test_save_position_uses_merge(self):
        import inspect
        from trading_bot.api.database import PositionStateRepository
        source = inspect.getsource(PositionStateRepository.save_position)
        assert "merge" in source
        assert "get_by_ticket" not in source
