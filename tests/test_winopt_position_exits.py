"""
WIN #4 A+ exit policy and E2E #9 multi-TP persistence tests.

TDD coverage for:
- Re-persist position state after TP1/TP2/BE mutations
- Restart cannot re-fire the same partial
- A+ / runner exit policy (skip TP1 partials, giveback gating)
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from trading_bot.execution.position_manager import (
    PositionManager,
    Position,
    PositionStatus,
)


@pytest.fixture
def order_manager():
    om = MagicMock()
    om.close_position = AsyncMock(return_value=MagicMock(success=True))
    om.modify_order = AsyncMock(return_value=MagicMock(success=True))
    om._check_spread = AsyncMock(return_value=(True, 0.0001, 0.001))
    return om


@pytest.fixture
def pm(order_manager):
    return PositionManager(order_manager=order_manager)


@pytest.fixture
def runner_position():
    """Intraday runner: entry=100, SL=95, risk=5, volume large enough to partial."""
    return Position(
        ticket=5001,
        symbol="XAUUSD",
        direction="long",
        volume=0.10,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        open_time=datetime.now(),
        trade_type="intraday",
        tp1=105.0,
        tp2=110.0,
        tp3=115.0,
    )


# ================================================================
# E2E #9 — Multi-TP persistence
# ================================================================


class TestMultiTPPersistence:
    """Position state must re-persist after partial closes and BE moves."""

    @pytest.mark.asyncio
    async def test_tp1_partial_triggers_persist(self, pm, runner_position, order_manager):
        pm.a_plus_skip_tp1 = False  # exercise legacy partial path
        pm.add_position(runner_position)
        runner_position.current_price = 105.0  # 1R

        with patch.object(pm, "_persist_and_wait") as mock_schedule:
            await pm._execute_tp1(runner_position)

        mock_schedule.assert_called()
        saved = mock_schedule.call_args.args[0]
        assert saved.tp1_hit is True
        assert saved.be_triggered is True
        assert saved.volume < 0.10

    @pytest.mark.asyncio
    async def test_tp2_partial_triggers_persist(self, pm, runner_position, order_manager):
        pm.add_position(runner_position)
        runner_position.tp1_hit = True
        runner_position.be_triggered = True
        runner_position.volume = 0.06
        runner_position.current_price = 110.0  # 2R

        with patch.object(pm, "_persist_and_wait") as mock_schedule:
            await pm._execute_tp2(runner_position)

        mock_schedule.assert_called()
        saved = mock_schedule.call_args.args[0]
        assert saved.tp2_hit is True
        assert saved.volume < 0.06

    @pytest.mark.asyncio
    async def test_be_move_triggers_persist(self, pm, runner_position, order_manager):
        pm.add_position(runner_position)

        with patch.object(pm, "_persist_and_wait") as mock_schedule:
            await pm._move_to_break_even(runner_position)

        mock_schedule.assert_awaited()
        saved = mock_schedule.call_args.args[0]
        assert saved.be_triggered is True

    @pytest.mark.asyncio
    async def test_peak_r_update_triggers_persist(self, pm, runner_position):
        pm.add_position(runner_position)

        with patch.object(pm, "_schedule_persist") as mock_schedule:
            await pm.manage_positions({"XAUUSD": 107.5})  # 1.5R

        mock_schedule.assert_called()
        assert runner_position.peak_r_multiple == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_restart_after_tp1_does_not_refire_partial(self, pm, runner_position, order_manager):
        """Loaded tp1_hit state must block a second TP1 partial on restart."""
        runner_position.tp1_hit = True
        runner_position.be_triggered = True
        runner_position.partial_closed = True
        runner_position.volume = 0.06
        runner_position.initial_volume = 0.10
        pm.add_position(runner_position)

        await pm.manage_positions({"XAUUSD": 105.0})

        order_manager.close_position.assert_not_called()


# ================================================================
# WIN #4 — A+ exit policy
# ================================================================


class TestAPlusExitPolicy:
    """High-quality runners skip early TP1; giveback arms only after 1.5R peak."""

    def test_position_manager_exposes_new_params(self, pm):
        assert hasattr(pm, "giveback_min_peak_r")
        assert hasattr(pm, "a_plus_skip_tp1")
        assert pm.giveback_min_peak_r == pytest.approx(1.5)
        assert pm.a_plus_skip_tp1 is True

    @pytest.mark.asyncio
    async def test_intraday_takes_tp1_partial_by_default(self, pm, runner_position, order_manager):
        pm.a_plus_skip_tp1 = False
        pm.add_position(runner_position)

        await pm.manage_positions({"XAUUSD": 105.0})  # 1R

        order_manager.close_position.assert_called_once()
        assert runner_position.tp1_hit is True

    @pytest.mark.asyncio
    async def test_swing_takes_tp1_partial(self, pm, runner_position, order_manager):
        runner_position.trade_type = "swing"
        pm.a_plus_skip_tp1 = False
        pm.add_position(runner_position)

        await pm.manage_positions({"XAUUSD": 105.0})

        order_manager.close_position.assert_called_once()
        assert runner_position.tp1_hit is True

    @pytest.mark.asyncio
    async def test_scalp_keeps_tighter_exits(self, pm, runner_position, order_manager):
        runner_position.trade_type = "scalp"
        pm.add_position(runner_position)

        await pm.manage_positions({"XAUUSD": 102.5})  # 0.5R
        assert runner_position.be_triggered is True
        order_manager.close_position.assert_not_called()

        order_manager.close_position.reset_mock()
        await pm.manage_positions({"XAUUSD": 105.0})  # 1R — still no partial for scalp
        order_manager.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_plus_flag_skips_tp1_partial(self, pm, runner_position, order_manager):
        runner_position.a_plus = True
        pm.a_plus_skip_tp1 = False  # flag alone should still skip when a_plus set
        pm.add_position(runner_position)

        await pm.manage_positions({"XAUUSD": 105.0})

        order_manager.close_position.assert_not_called()
        assert runner_position.tp1_hit is True

    @pytest.mark.asyncio
    async def test_legacy_partial_when_skip_disabled(self, pm, runner_position, order_manager):
        pm.a_plus_skip_tp1 = False
        pm.add_position(runner_position)

        await pm.manage_positions({"XAUUSD": 105.0})

        order_manager.close_position.assert_called_once()
        assert runner_position.tp1_hit is True
        assert runner_position.partial_closed is True

    @pytest.mark.asyncio
    async def test_giveback_does_not_arm_below_1_5r_peak(self, pm, runner_position):
        pm.add_position(runner_position)
        runner_position.be_triggered = True
        runner_position.tp1_hit = True
        runner_position.peak_r_multiple = 1.3

        result = await pm._check_profit_protection(runner_position, 0.5)
        assert result is None

    @pytest.mark.asyncio
    async def test_giveback_arms_at_1_5r_peak(self, pm, runner_position, order_manager):
        pm.add_position(runner_position)
        runner_position.be_triggered = True
        runner_position.tp1_hit = True
        runner_position.peak_r_multiple = 1.6
        runner_position.current_price = 100.8  # ~0.16R — >55% giveback from 1.6R

        result = await pm._check_profit_protection(runner_position, 0.16)

        assert result is not None
        assert "giveback" in result["action"]
        order_manager.close_position.assert_called_once()
