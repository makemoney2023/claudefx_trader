"""
Wave 2 Task 7 — deterministic confidence and explicit A+ exits.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot.execution.position_manager import Position, PositionManager
from trading_bot.utils.win_optimization import (
    ConfidenceDecision,
    build_confidence_decision,
    classify_a_plus,
)


class TestConfidenceDecision:
    def test_caps_cannot_be_undone_by_later_boosts(self):
        decision = build_confidence_decision(
            base=0.80,
            boosts=[("htf_align", 0.10), ("late_boost", 0.15)],
            caps=[("distribution", 0.55)],
        )
        assert decision.final == pytest.approx(0.55)

    def test_caps_apply_after_all_modifiers(self):
        decision = build_confidence_decision(
            base=0.75,
            boosts=[("flow", 0.05)],
            penalties=[("news", -0.03)],
            caps=[("session", 0.70)],
        )
        assert decision.final == pytest.approx(0.70)
        assert len(decision.caps) == 1

    def test_decision_object_exposes_components(self):
        decision = build_confidence_decision(
            base=0.72,
            boosts=[("confluence", 0.04)],
            penalties=[("spread", -0.02)],
            caps=[("off_hours", 0.68)],
        )
        assert decision.base == 0.72
        assert decision.boosts[0][0] == "confluence"
        assert decision.penalties[0][0] == "spread"
        assert decision.caps[0][0] == "off_hours"
        assert 0.0 <= decision.final <= 1.0


class TestExplicitAPlusClassification:
    def test_a_plus_from_setup_grade_and_confluence(self):
        assert classify_a_plus("A+", 3) is True
        assert classify_a_plus("A", 4) is True
        assert classify_a_plus("A", 2) is False
        assert classify_a_plus("B", 5) is False

    def test_intraday_does_not_inherit_a_plus_behavior(self):
        pm = PositionManager(order_manager=MagicMock())
        pm.a_plus_skip_tp1 = True
        pos = Position(
            ticket=1,
            symbol="XAUUSD",
            direction="long",
            volume=0.10,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=115.0,
            open_time=datetime.now(),
            trade_type="intraday",
            a_plus=False,
            tp1=105.0,
        )
        assert pm._skip_tp1_partial(pos) is False

    def test_swing_does_not_inherit_a_plus_behavior(self):
        pm = PositionManager(order_manager=MagicMock())
        pos = Position(
            ticket=2,
            symbol="EURUSD",
            direction="long",
            volume=0.10,
            entry_price=1.08,
            stop_loss=1.07,
            take_profit=1.10,
            open_time=datetime.now(),
            trade_type="swing",
            a_plus=False,
        )
        assert pm._skip_tp1_partial(pos) is False

    def test_only_explicit_a_plus_skips_tp1(self):
        pm = PositionManager(order_manager=MagicMock())
        pos = Position(
            ticket=3,
            symbol="XAUUSD",
            direction="long",
            volume=0.10,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=115.0,
            open_time=datetime.now(),
            trade_type="intraday",
            a_plus=True,
            tp1=105.0,
        )
        assert pm._skip_tp1_partial(pos) is True

    @pytest.mark.asyncio
    async def test_a_plus_persists_and_reloads(self):
        pm = PositionManager(order_manager=MagicMock())
        pos = Position(
            ticket=99,
            symbol="XAUUSD",
            direction="long",
            volume=0.05,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=115.0,
            open_time=datetime.now(),
            trade_type="intraday",
            a_plus=True,
        )
        pm.add_position(pos)

        with pytest.MonkeyPatch.context() as mp:
            saved = {}

            async def fake_persist(position):
                saved.update({"a_plus": position.a_plus, "ticket": position.ticket})

            mp.setattr(pm, "_persist_position", fake_persist)
            await pm._persist_and_wait(pos)

        assert saved["a_plus"] is True

    @pytest.mark.asyncio
    async def test_tp1_partial_runs_for_ordinary_intraday(self):
        om = MagicMock()
        om.close_position = AsyncMock(return_value=MagicMock(success=True))
        om.modify_order = AsyncMock(return_value=MagicMock(success=True))
        om._check_spread = AsyncMock(return_value=(True, 0.0001, 0.001))

        pm = PositionManager(order_manager=om)
        pm.a_plus_skip_tp1 = False
        pos = Position(
            ticket=10,
            symbol="XAUUSD",
            direction="long",
            volume=0.10,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=115.0,
            open_time=datetime.now(),
            trade_type="intraday",
            a_plus=False,
            tp1=105.0,
        )
        pm.add_position(pos)
        pos.current_price = 105.0

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(pm, "_persist_and_wait", AsyncMock())
            await pm.manage_positions({"XAUUSD": 105.0})

        om.close_position.assert_called_once()
        assert pos.tp1_hit is True
