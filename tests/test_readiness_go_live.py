"""
Go-live P0/P1 readiness — behavioral tests for production safety fixes.
"""

import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from trading_bot.config import TradingSettings, get_config_risk_warnings
from trading_bot.execution.position_manager import Position, PositionManager
from trading_bot.main import TradingBot
from trading_bot.mt5.client import AccountInfo, MT5Client, Position as MT5Position
from trading_bot.services.scaling_manager import ScalingManager, TradingMode


# ---------------------------------------------------------------------------
# P0-1 — AGGRESSIVE mode gated to simulation / explicit demo flag
# ---------------------------------------------------------------------------


class TestAggressiveModeGating:
    def test_live_mt5_without_demo_flag_stays_normal(self):
        bot = TradingBot.__new__(TradingBot)
        bot.mt5_client = MagicMock(is_simulation=False)
        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.demo_data_collection_mode = False
            assert bot._should_use_aggressive_data_collection() is False

    def test_simulation_enables_aggressive(self):
        bot = TradingBot.__new__(TradingBot)
        bot.mt5_client = MagicMock(is_simulation=True)
        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.demo_data_collection_mode = False
            assert bot._should_use_aggressive_data_collection() is True

    def test_demo_data_collection_flag_enables_aggressive_on_live(self):
        bot = TradingBot.__new__(TradingBot)
        bot.mt5_client = MagicMock(is_simulation=False)
        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.demo_data_collection_mode = True
            assert bot._should_use_aggressive_data_collection() is True


# ---------------------------------------------------------------------------
# P0-2 — Runtime sync skips non-ICT_Bot positions
# ---------------------------------------------------------------------------


class TestRuntimeSyncICTBotFilter:
    @pytest.mark.asyncio
    async def test_sync_skips_manual_mt5_positions(self):
        pm = PositionManager()
        mt5_client = AsyncMock()
        mt5_client.get_positions = AsyncMock(
            return_value=[
                MT5Position(
                    ticket=111,
                    symbol="EURUSD",
                    type="buy",
                    volume=0.10,
                    price_open=1.0850,
                    price_current=1.0860,
                    sl=1.0800,
                    tp=1.0950,
                    profit=10.0,
                    magic=0,
                    comment="Manual trade",
                    time=datetime.now(timezone.utc),
                ),
                MT5Position(
                    ticket=222,
                    symbol="GBPUSD",
                    type="sell",
                    volume=0.05,
                    price_open=1.2700,
                    price_current=1.2690,
                    sl=1.2750,
                    tp=1.2600,
                    profit=5.0,
                    magic=12345,
                    comment="ICT_Bot",
                    time=datetime.now(timezone.utc),
                ),
            ]
        )
        mt5_client.get_orders = AsyncMock(return_value=[])

        result = await pm.sync_with_mt5(mt5_client)

        assert result["new_positions"] == [222]
        assert 111 not in pm.positions
        assert 222 in pm.positions


# ---------------------------------------------------------------------------
# P0-3 — MT5 account login validation
# ---------------------------------------------------------------------------


class TestMT5AccountLoginValidation:
    @pytest.mark.asyncio
    async def test_connect_fails_when_terminal_account_differs_from_config(self, monkeypatch):
        import sys

        wrong_account = SimpleNamespace(
            login=999999,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            margin_level=0.0,
            profit=0.0,
            currency="USD",
            leverage=100,
            server="Wrong-Server",
        )
        mock_mt5 = MagicMock()
        mock_mt5.initialize = MagicMock(return_value=True)
        mock_mt5.account_info = MagicMock(return_value=wrong_account)
        mock_mt5.shutdown = MagicMock(return_value=True)
        mock_mt5.last_error = MagicMock(return_value=(0, ""))
        monkeypatch.setitem(sys.modules, "MetaTrader5", mock_mt5)

        client = MT5Client(login=111111, password="pw", server="Test-Server")
        with patch("trading_bot.mt5.client.settings") as mock_settings:
            mock_settings.mt5.login = 111111
            ok = await client.connect()

        assert ok is False
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_succeeds_when_terminal_matches_config(self, monkeypatch):
        import sys

        matching_account = SimpleNamespace(
            login=111111,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            margin_level=0.0,
            profit=0.0,
            currency="USD",
            leverage=100,
            server="Test-Server",
        )
        mock_mt5 = MagicMock()
        mock_mt5.initialize = MagicMock(return_value=True)
        mock_mt5.account_info = MagicMock(return_value=matching_account)
        monkeypatch.setitem(sys.modules, "MetaTrader5", mock_mt5)

        client = MT5Client(login=111111, password="pw", server="Test-Server")
        with patch("trading_bot.mt5.client.settings") as mock_settings:
            mock_settings.mt5.login = 111111
            ok = await client.connect()

        assert ok is True
        assert client.is_connected is True


# ---------------------------------------------------------------------------
# P0-4 — Daily kill-switch uses peak equity via scaling_manager
# ---------------------------------------------------------------------------


class TestDailyKillSwitchPeakEquity:
    @pytest.mark.asyncio
    async def test_drawdown_uses_scaling_manager_peak_not_morning_balance(self):
        bot = TradingBot.__new__(TradingBot)
        bot.mt5_client = AsyncMock()
        bot.mt5_client.get_account_info = AsyncMock(
            return_value=AccountInfo(
                login=1,
                balance=9700.0,
                equity=9700.0,
                margin=0.0,
                free_margin=9700.0,
                margin_level=0.0,
                profit=0.0,
                currency="USD",
                leverage=100,
            )
        )
        bot.scaling_manager = ScalingManager(
            starting_equity=10000.0,
            target_equity=20000.0,
            max_daily_drawdown=0.03,
            max_weekly_drawdown=0.06,
        )
        bot.scaling_manager.daily_high_equity = 10000.0
        bot._weekly_kill_switch_active = False
        bot._daily_kill_switch_active = False

        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.max_daily_drawdown = 0.03
            mock_settings.trading.max_weekly_drawdown = 0.06
            with patch("trading_bot.main.notify", AsyncMock()):
                triggered = await bot._check_drawdown_circuit_breaker()

        # 3% drawdown from peak (10000 -> 9700) should trigger kill-switch
        assert triggered is True
        assert bot.scaling_manager.calculate_daily_drawdown(9700.0) == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# P0-5 — Bot crash alert
# ---------------------------------------------------------------------------


class TestBotCrashAlert:
    @pytest.mark.asyncio
    async def test_background_task_notifies_on_exception(self):
        import trading_bot.api.main as api_main

        mock_bot = MagicMock()
        mock_bot.initialize = AsyncMock(side_effect=RuntimeError("init exploded"))
        mock_bot.shutdown = AsyncMock()
        mock_bot._position_mgr_task = None

        with patch.object(api_main, "_bot_instance", None):
            with patch.object(api_main, "_mt5_client", None):
                with patch("trading_bot.main.TradingBot", return_value=mock_bot):
                    with patch(
                        "trading_bot.utils.notifications.notify",
                        AsyncMock(),
                    ) as notify_mock:
                        await api_main._run_bot_background()

        notify_mock.assert_awaited()
        call_args = notify_mock.await_args[0]
        assert "init exploded" in str(call_args[1])


# ---------------------------------------------------------------------------
# P0-6 — auto_start_bot default False
# ---------------------------------------------------------------------------


class TestAutoStartBotDefault:
    def test_auto_start_bot_defaults_false(self):
        settings = TradingSettings()
        assert settings.auto_start_bot is False


# ---------------------------------------------------------------------------
# P0-7 — Protect /api/debug/mt5
# ---------------------------------------------------------------------------


@pytest.fixture
def debug_mt5_client(monkeypatch):
    monkeypatch.setenv("BOT_API_KEY", "go-live-test-key")
    import trading_bot.api.auth as auth_module

    auth_module._API_KEY = None
    key = auth_module.get_api_key()
    from trading_bot.api.main import app

    return TestClient(app), key


class TestDebugMT5Auth:
    def test_debug_mt5_requires_api_key(self, debug_mt5_client):
        client, _ = debug_mt5_client
        response = client.get("/api/debug/mt5")
        assert response.status_code == 401

    def test_debug_mt5_allows_valid_api_key(self, debug_mt5_client):
        client, key = debug_mt5_client
        response = client.get("/api/debug/mt5", headers={"X-API-Key": key})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# P1-8 — Reversal bypasses kill-switch
# ---------------------------------------------------------------------------


class TestReversalKillSwitch:
    @pytest.mark.asyncio
    async def test_reversal_skips_when_kill_switch_active(self):
        bot = TradingBot.__new__(TradingBot)
        bot._check_drawdown_circuit_breaker = AsyncMock(return_value=True)
        bot.data_fetcher = AsyncMock()

        closed = SimpleNamespace(
            symbol="EURUSD",
            direction="long",
            trade_type="intraday",
        )

        await TradingBot._analyze_reversal_entry(bot, closed)

        bot._check_drawdown_circuit_breaker.assert_awaited_once()
        bot.data_fetcher.get_ohlcv.assert_not_called()


# ---------------------------------------------------------------------------
# P1-9 — Position replacement runs close lifecycle
# ---------------------------------------------------------------------------


class TestPositionReplacementCloseLifecycle:
    @pytest.mark.asyncio
    async def test_replace_weakest_invokes_close_handler(self):
        bot = TradingBot.__new__(TradingBot)
        pos = SimpleNamespace(
            ticket=5001,
            symbol="XAUUSD",
            direction="long",
            volume=0.05,
            entry_price=2350.0,
            stop_loss=2340.0,
            take_profit=2370.0,
            open_time=datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=3),
            unrealized_pnl=-5.0,
            current_r_multiple=0.1,
        )
        bot.position_manager = MagicMock()
        bot.position_manager.get_all_positions = MagicMock(return_value=[pos])
        bot.order_manager = AsyncMock()
        bot.order_manager.close_position = AsyncMock(
            return_value=SimpleNamespace(success=True, error=None)
        )
        bot._handle_position_close = AsyncMock()
        bot.position_manager.remove_position = MagicMock()

        with patch("trading_bot.main.notify", AsyncMock()):
            with patch("trading_bot.api.routes.activity.add_activity"):
                replaced = await bot._try_replace_weakest_position(
                    new_symbol="EURUSD",
                    new_confidence=0.85,
                    new_direction="short",
                )

        assert replaced is True
        assert pos.closed_profit_loss == -5.0
        bot._handle_position_close.assert_awaited_once_with(pos)
        bot.position_manager.remove_position.assert_called_once_with(5001)


# ---------------------------------------------------------------------------
# P1-10 — Ghost position after ambiguous order timeout
# ---------------------------------------------------------------------------


class TestGhostPositionReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_finds_orphan_fill_and_tracks_position(self):
        bot = TradingBot.__new__(TradingBot)
        bot.mt5_client = AsyncMock()
        bot.mt5_client.get_positions = AsyncMock(
            return_value=[
                MT5Position(
                    ticket=9001,
                    symbol="EURUSD",
                    type="buy",
                    volume=0.10,
                    price_open=1.0850,
                    price_current=1.0855,
                    sl=1.0800,
                    tp=1.0950,
                    profit=5.0,
                    magic=12345,
                    comment="ICT_Bot",
                    time=datetime.now(timezone.utc),
                )
            ]
        )
        bot.position_manager = PositionManager()
        bot.reservation_ledger = MagicMock()
        reservation = SimpleNamespace(reservation_id="res-1")
        bot.reservation_ledger.transfer_to_position = MagicMock()

        ticket = await bot._reconcile_fill_after_ambiguous_order(
            symbol="EURUSD",
            direction="long",
            lots=0.10,
            reservation=reservation,
            stop_loss=1.0800,
            take_profit=1.0950,
        )

        assert ticket == 9001
        assert 9001 in bot.position_manager.positions
        bot.reservation_ledger.transfer_to_position.assert_called_once_with(
            reservation, 9001
        )

    @pytest.mark.asyncio
    async def test_reconcile_returns_none_when_no_matching_position(self):
        bot = TradingBot.__new__(TradingBot)
        bot.mt5_client = AsyncMock()
        bot.mt5_client.get_positions = AsyncMock(return_value=[])
        bot.position_manager = PositionManager()

        ticket = await bot._reconcile_fill_after_ambiguous_order(
            symbol="EURUSD",
            direction="long",
            lots=0.10,
            reservation=None,
        )

        assert ticket is None


# ---------------------------------------------------------------------------
# P1-11 — get_config_risk_warnings logged at startup
# ---------------------------------------------------------------------------


class TestStartupRiskWarnings:
    def test_initialize_logs_config_risk_warnings(self):
        source = inspect.getsource(TradingBot.initialize)
        assert "get_config_risk_warnings" in source or "format_startup_config_banner" in source

    def test_api_lifespan_logs_config_risk_warnings(self):
        import trading_bot.api.main as api_main

        source = inspect.getsource(api_main.lifespan)
        assert "get_config_risk_warnings" in source or "format_startup_config_banner" in source


# ---------------------------------------------------------------------------
# P1-12 — Database path absolute (project root, not CWD)
# ---------------------------------------------------------------------------


class TestDatabasePathAbsolute:
    def test_database_url_uses_project_root_not_cwd(self):
        from trading_bot.api import database as db_module

        db_path = db_module.get_database_path()
        project_root = Path(db_module.__file__).resolve().parent.parent.parent
        assert db_path == project_root / "trading_bot.db"
        assert "sqlite+aiosqlite:///" in db_module.DATABASE_URL
        assert str(db_path) in db_module.DATABASE_URL

    def test_backup_database_uses_project_root_path(self):
        from trading_bot.api import database as db_module

        source = inspect.getsource(db_module.backup_database)
        assert "get_database_path" in source
