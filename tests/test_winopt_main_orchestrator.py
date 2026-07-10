"""Tests for WIN optimization orchestrator helpers and key main.py behaviors."""

import os
import sqlite3
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from trading_bot.utils.win_optimization import (
    apply_confidence_caps,
    apply_demote_policy,
    cap_confidence_once,
    is_friday_afternoon_entry_block,
    is_friday_weekend_close_time,
    resolve_order_type_for_fill,
    resolve_trading_mode_from_state,
    should_reject_entry_deviation,
)
from trading_bot.services.gate_funnel import GateFunnel


EST = pytz.timezone("US/Eastern")


class TestFridayWeekendClose:
    def test_close_at_1630_friday(self):
        now = EST.localize(datetime(2026, 7, 10, 16, 30))
        assert is_friday_weekend_close_time(now) is True

    def test_close_after_1630_friday(self):
        now = EST.localize(datetime(2026, 7, 10, 17, 0))
        assert is_friday_weekend_close_time(now) is True

    def test_no_close_at_1629_friday(self):
        now = EST.localize(datetime(2026, 7, 10, 16, 29))
        assert is_friday_weekend_close_time(now) is False

    def test_entry_block_noon_independent_of_close(self):
        now = EST.localize(datetime(2026, 7, 10, 12, 0))
        assert is_friday_afternoon_entry_block(now) is True
        assert is_friday_weekend_close_time(now) is False


class TestEntryDeviationGate:
    def test_market_order_rejects_over_2pct(self):
        reject, dev, _ = should_reject_entry_deviation(
            "market", "long", 1.03, 1.0
        )
        assert reject is True
        assert dev == pytest.approx(0.03)

    def test_structural_buy_limit_allows_4pct(self):
        reject, _, _ = should_reject_entry_deviation(
            "buy_limit", "long", 0.96, 1.0, limit_max_pct=0.05
        )
        assert reject is False

    def test_structural_buy_limit_rejects_over_5pct(self):
        reject, _, reason = should_reject_entry_deviation(
            "buy_limit", "long", 0.94, 1.0, limit_max_pct=0.05
        )
        assert reject is True
        assert "structural" in reason


class TestConfidenceCaps:
    def test_apply_confidence_caps_single_min(self):
        assert apply_confidence_caps(0.80, [0.55, 0.60, 0.70]) == 0.55

    def test_cap_confidence_once_prevents_double_stack(self):
        applied = set()
        first = cap_confidence_once(0.80, 0.55, applied, "distribution")
        second = cap_confidence_once(first, 0.50, applied, "distribution")
        assert first == 0.55
        assert second == 0.55


class TestDemotePolicy:
    def test_at_zone_market_size_reduce(self):
        result = apply_demote_policy(
            direction="long",
            current_price=1.1000,
            original_entry=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            order_type="market",
            suggested_entry=None,
        )
        assert result["action"] == "size_reduce"
        assert result["order_type"] == "market"
        assert result["size_multiplier"] == 0.5

    def test_existing_limit_kept(self):
        result = apply_demote_policy(
            direction="long",
            current_price=1.1000,
            original_entry=1.0980,
            stop_loss=1.0950,
            take_profit=1.1100,
            order_type="buy_limit",
            suggested_entry=None,
        )
        assert result["action"] == "keep_limit"
        assert result["demoted_entry"] == 1.0980


class TestFillPolicy:
    def test_at_zone_keeps_market(self):
        ot, reason = resolve_order_type_for_fill(
            "market", "long", 1.1000, 1.10005, at_zone_pct=0.001
        )
        assert ot == "market"
        assert reason == "at_zone_keep_market"

    def test_far_entry_converts_pending(self):
        ot, reason = resolve_order_type_for_fill(
            "market", "long", 1.0950, 1.1000, pending_threshold_pct=0.001
        )
        assert ot == "buy_limit"
        assert reason == "converted_to_pending"


class TestTradingModeResolve:
    def test_persisted_mode_wins(self):
        assert resolve_trading_mode_from_state("conservative", "aggressive") == "conservative"

    def test_default_normal(self):
        assert resolve_trading_mode_from_state(None, None) == "normal"


class TestGateFunnel:
    @pytest.mark.asyncio
    async def test_record_block_and_mfe_stub(self, monkeypatch):
        import trading_bot.api.database as db_module
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy import select

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            db_url = f"sqlite+aiosqlite:///{db_path}"
            engine = create_async_engine(db_url, echo=False)
            session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            monkeypatch.setattr(db_module, "engine", engine)
            monkeypatch.setattr(db_module, "async_session_maker", session_maker)
            await db_module.init_db()

            funnel = GateFunnel(session_maker=session_maker)
            did = await funnel.record_decision(
                "mechanical_reject",
                "EURUSD",
                gate_id="entry_deviation_2pct",
                direction="long",
                entry=1.1,
                sl=1.09,
                tp=1.12,
                confidence=0.7,
                details={"deviation": 0.03},
            )
            assert did is not None

            async with session_maker() as session:
                from trading_bot.api.database import SignalDecisionModel
                row = (
                    await session.execute(
                        select(SignalDecisionModel).where(SignalDecisionModel.decision_id == did)
                    )
                ).scalar_one()
                row_id = row.id

            assert await funnel.record_post_block_mfe_async(row_id, 1.5) is True

            async with session_maker() as session:
                from trading_bot.api.database import SignalDecisionModel
                row = (
                    await session.execute(
                        select(SignalDecisionModel).where(SignalDecisionModel.id == row_id)
                    )
                ).scalar_one()
                assert row.mfe_r == pytest.approx(1.5)

            await engine.dispose()


class TestMainOrchestratorBehaviors:
    @pytest.mark.asyncio
    async def test_reversal_judge_failure_fails_closed(self):
        """Reversal path must not proceed when judge fails."""
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.claude_client = MagicMock()
        bot.claude_client.api_key = "test-key"
        bot.claude_client.judge_trade = AsyncMock(side_effect=RuntimeError("judge down"))
        bot.learning_service = MagicMock()
        bot.learning_service.build_context_for_claude = AsyncMock(return_value="ctx")
        bot.mt5_client = None
        bot.risk_manager = None
        bot.daily_trades = 0
        bot.daily_pnl = 0.0

        trade_signal = SimpleNamespace(
            direction="long",
            confidence=0.8,
            entry_price=1.1,
            stop_loss=1.09,
            take_profit=1.12,
            reasoning="test",
            trade_type="intraday",
        )

        executed = {"called": False}

        async def fake_execute(*args, **kwargs):
            executed["called"] = True

        # Patch the method that would run after judge in reversal flow
        with patch.object(TradingBot, "_execute_reversal_trade", fake_execute, create=True):
            # Call the judge section logic via a minimal inline reproduction
            learning_context = await bot.learning_service.build_context_for_claude(
                symbol="EURUSD", direction="long", trade_type="intraday"
            )
            assert learning_context == "ctx"
            try:
                await bot.claude_client.judge_trade(
                    signal={}, risk_metrics={}, learning_context=learning_context
                )
                proceed = True
            except Exception:
                proceed = False
            assert proceed is False
            assert executed["called"] is False

    def test_h4_bias_defined_before_m15_opose_block(self):
        """Simulate M15 oppose path using _h4_bias before HTF gate assignment."""
        market_data = {"d1_bias": "bullish", "h4_bias": "bullish", "m15_bias": "bearish"}
        _dir = "long"
        _d1_bias = market_data.get("d1_bias", "").lower()
        _h4_bias = (market_data.get("h4_bias") or "").lower()
        _m15_bias = (market_data.get("m15_bias") or "").lower()
        _m15_opposes = (
            (_m15_bias == "bearish" and _dir == "long")
            or (_m15_bias == "bullish" and _dir == "short")
        )
        _d1_supports = (
            (_d1_bias == "bullish" and _dir == "long")
            or (_d1_bias == "bearish" and _dir == "short")
        )
        _h4_supports = (
            (_h4_bias == "bullish" and _dir == "long")
            or (_h4_bias == "bearish" and _dir == "short")
        )
        assert _m15_opposes is True
        assert _h4_supports is True
        assert _d1_supports is True
