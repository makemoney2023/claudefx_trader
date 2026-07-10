"""
Wave 1 Task 3 — durable position state migrations and restart roundtrip.
"""

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from trading_bot.execution.position_manager import Position, PositionManager, PositionStatus


@pytest.fixture
async def temp_db(monkeypatch):
    """Isolated SQLite database for migration and persistence tests."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_trading.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"

        import trading_bot.api.database as db_module

        old_engine = db_module.engine
        old_maker = db_module.async_session_maker
        old_url = db_module.DATABASE_URL

        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

        engine = create_async_engine(db_url, echo=False)
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        monkeypatch.setattr(db_module, "DATABASE_URL", db_url)
        monkeypatch.setattr(db_module, "engine", engine)
        monkeypatch.setattr(db_module, "async_session_maker", session_maker)
        monkeypatch.setattr(db_module, "AsyncSessionLocal", session_maker)
        monkeypatch.setattr(db_module, "async_session", session_maker)

        await db_module.init_db()
        yield db_module

        await engine.dispose()
        monkeypatch.setattr(db_module, "engine", old_engine)
        monkeypatch.setattr(db_module, "async_session_maker", old_maker)
        monkeypatch.setattr(db_module, "DATABASE_URL", old_url)


@pytest.fixture
def order_manager():
    om = MagicMock()
    om.close_position = AsyncMock(return_value=MagicMock(success=True))
    om.modify_order = AsyncMock(return_value=MagicMock(success=True))
    om._check_spread = AsyncMock(return_value=(True, 0.0001, 0.001))
    return om


@pytest.fixture
def runner_position():
    return Position(
        ticket=61001,
        symbol="XAUUSD",
        direction="long",
        volume=0.10,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        open_time=datetime.now(timezone.utc),
        trade_type="intraday",
        tp1=105.0,
        tp2=110.0,
        tp3=115.0,
        a_plus=False,
    )


class TestPositionStateMigrations:
    @pytest.mark.asyncio
    async def test_position_states_has_required_columns(self, temp_db):
        async with temp_db.engine.connect() as conn:
            result = await conn.execute(text("PRAGMA table_info(position_states)"))
            columns = {row[1] for row in result.fetchall()}

        required = {
            "ticket",
            "symbol",
            "direction",
            "volume",
            "entry_price",
            "stop_loss",
            "take_profit",
            "open_time",
            "status",
            "trade_type",
            "initial_sl",
            "be_triggered",
            "trailing_active",
            "partial_closed",
            "tp1",
            "tp2",
            "tp3",
            "tp1_hit",
            "tp2_hit",
            "initial_volume",
            "peak_r_multiple",
            "peak_unrealized_pnl",
            "near_tp_reached",
            "close_reason",
            "a_plus",
            "reservation_id",
            "remaining_volume",
        }
        missing = required - columns
        assert not missing, f"Missing columns: {sorted(missing)}"

    @pytest.mark.asyncio
    async def test_legacy_position_schema_is_upgraded_for_every_orm_field(
        self, monkeypatch, tmp_path
    ):
        import trading_bot.api.database as db_module
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        db_path = tmp_path / "legacy.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        session_maker = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr(db_module, "engine", engine)
        monkeypatch.setattr(db_module, "async_session_maker", session_maker)
        monkeypatch.setattr(db_module, "async_session", session_maker)

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE position_states (
                        ticket INTEGER PRIMARY KEY,
                        symbol VARCHAR(20) NOT NULL,
                        direction VARCHAR(10) NOT NULL,
                        volume FLOAT NOT NULL,
                        entry_price FLOAT NOT NULL,
                        stop_loss FLOAT NOT NULL,
                        take_profit FLOAT NOT NULL,
                        open_time DATETIME NOT NULL,
                        status VARCHAR(20)
                    )
                    """
                )
            )

        await db_module.init_db()

        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA table_info(position_states)"))
            columns = {row[1] for row in result.fetchall()}

        orm_columns = set(db_module.PositionStateModel.__table__.columns.keys())
        assert orm_columns <= columns
        assert {"be_triggered", "trailing_active", "partial_closed"} <= columns
        await engine.dispose()


class TestPositionRestartRoundtrip:
    @pytest.mark.asyncio
    async def test_save_reload_manage_tp1_fires_once(self, temp_db, order_manager, runner_position):
        pm1 = PositionManager(order_manager=order_manager, a_plus_skip_tp1=False)
        pm1.add_position(runner_position)
        await pm1.flush_persistence()

        pm2 = PositionManager(order_manager=order_manager, a_plus_skip_tp1=False)
        loaded = await pm2.load_from_db()
        assert len(loaded) == 1
        for pos in loaded:
            pm2.positions[pos.ticket] = pos

        loaded[0].current_price = 105.0
        await pm2.manage_positions({"XAUUSD": 105.0})
        order_manager.close_position.assert_called_once()

        order_manager.close_position.reset_mock()
        loaded[0].current_price = 105.0
        await pm2.manage_positions({"XAUUSD": 105.0})
        order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_flushes_queued_persistence(self, temp_db, order_manager, runner_position):
        pm = PositionManager(order_manager=order_manager)
        pm.add_position(runner_position)

        await asyncio.sleep(0.05)
        await pm.flush_persistence()

        pm2 = PositionManager(order_manager=order_manager)
        loaded = await pm2.load_from_db()
        assert len(loaded) == 1
        assert loaded[0].ticket == runner_position.ticket
        assert loaded[0].symbol == runner_position.symbol


class TestDurablePositionTransitions:
    @pytest.mark.asyncio
    async def test_tp1_persistence_failure_prevents_partial_close(
        self, order_manager, runner_position
    ):
        pm = PositionManager(order_manager=order_manager, a_plus_skip_tp1=False)
        pm._persist_and_wait = AsyncMock(side_effect=RuntimeError("db unavailable"))

        with pytest.raises(RuntimeError, match="db unavailable"):
            await pm._execute_tp1(runner_position)

        order_manager.close_position.assert_not_called()
        assert runner_position.tp1_hit is False
        assert runner_position.partial_closed is False
        assert runner_position.volume == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_tp2_persistence_failure_prevents_partial_close(
        self, order_manager, runner_position
    ):
        pm = PositionManager(order_manager=order_manager)
        runner_position.tp1_hit = True
        runner_position.be_triggered = True
        runner_position.volume = 0.06
        pm._persist_and_wait = AsyncMock(side_effect=RuntimeError("db unavailable"))

        with pytest.raises(RuntimeError, match="db unavailable"):
            await pm._execute_tp2(runner_position)

        order_manager.close_position.assert_not_called()
        assert runner_position.tp2_hit is False
        assert runner_position.status == PositionStatus.OPEN
        assert runner_position.volume == pytest.approx(0.06)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("skip_tp1,volume", [(True, 0.10), (False, 0.02)])
    async def test_tp1_skip_and_micro_paths_await_durability(
        self, order_manager, runner_position, skip_tp1, volume
    ):
        pm = PositionManager(
            order_manager=order_manager,
            a_plus_skip_tp1=skip_tp1,
        )
        runner_position.volume = volume
        pm.positions[runner_position.ticket] = runner_position
        pm._persist_and_wait = AsyncMock()

        await pm.manage_positions({"XAUUSD": 105.0})

        assert runner_position.tp1_hit is True
        assert pm._persist_and_wait.await_count >= 1

    @pytest.mark.asyncio
    async def test_tp2_micro_path_awaits_durability(
        self, order_manager, runner_position
    ):
        pm = PositionManager(order_manager=order_manager)
        runner_position.volume = 0.02
        runner_position.tp1_hit = True
        runner_position.be_triggered = True
        pm._persist_and_wait = AsyncMock()

        runner_position.current_price = 110.0
        await pm._execute_tp2(runner_position)

        assert runner_position.tp2_hit is True
        assert runner_position.status == PositionStatus.TRAILING
        assert pm._persist_and_wait.await_count >= 1

    @pytest.mark.asyncio
    async def test_trailing_transition_persistence_failure_prevents_broker_modify(
        self, order_manager, runner_position
    ):
        pm = PositionManager(order_manager=order_manager)
        runner_position.current_price = 110.0
        runner_position.tp1_hit = True
        runner_position.tp2_hit = True
        runner_position.be_triggered = True
        pm._persist_and_wait = AsyncMock(side_effect=RuntimeError("db unavailable"))

        with pytest.raises(RuntimeError, match="db unavailable"):
            await pm._update_trailing_stop(runner_position)

        order_manager.modify_order.assert_not_called()
        assert runner_position.trailing_active is False
        assert runner_position.status == PositionStatus.OPEN
