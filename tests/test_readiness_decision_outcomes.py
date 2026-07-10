"""
Wave 3 Task 8 — decision telemetry and outcome worker.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from trading_bot.services.gate_funnel import (
    GateFunnel,
    TERMINAL_OUTCOMES,
    evaluate_hypothetical_outcome,
    resolve_same_bar_tp_sl,
)
from trading_bot.services.trade_learning_service import TradeLearningService
from trading_bot.api.database import SignalDecisionModel


@pytest.fixture
async def temp_db(monkeypatch):
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
def funnel(temp_db):
    return GateFunnel(session_maker=temp_db.async_session_maker)


class TestTerminalDecisionRecording:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome_type", sorted(TERMINAL_OUTCOMES))
    async def test_every_terminal_outcome_creates_row(self, funnel, temp_db, outcome_type):
        did = await funnel.record_decision(
            outcome_type,
            "EURUSD",
            gate_id="test_gate" if outcome_type == "mechanical_reject" else None,
            direction="long",
            entry=1.0850,
            sl=1.0800,
            tp=1.0950,
            confidence=0.72,
            reason=f"test {outcome_type}",
            judge_verdict="REJECT" if outcome_type.startswith("judge") else None,
        )
        assert did is not None

        async with temp_db.async_session_maker() as session:
            result = await session.execute(
                select(SignalDecisionModel).where(SignalDecisionModel.decision_id == did)
            )
            row = result.scalar_one()
            assert row.outcome_type == outcome_type
            assert row.symbol == "EURUSD"
            assert row.entry == pytest.approx(1.0850)

    @pytest.mark.asyncio
    async def test_mechanical_reject_records_gate_id(self, funnel, temp_db):
        did = await funnel.record_decision(
            "mechanical_reject",
            "XAUUSD",
            gate_id="final_rr_below_1",
            direction="short",
            entry=2000.0,
            sl=2010.0,
            tp=1980.0,
            confidence=0.65,
            reason="R:R below floor",
        )
        async with temp_db.async_session_maker() as session:
            row = (
                await session.execute(
                    select(SignalDecisionModel).where(SignalDecisionModel.decision_id == did)
                )
            ).scalar_one()
            assert row.gate_id == "final_rr_below_1"
            assert row.outcome_worker_status == "pending"


class TestHypotheticalOutcomeWorker:
    def test_same_bar_sl_closer_to_open_wins(self):
        sl_hit, tp_hit = resolve_same_bar_tp_sl("long", 1.0850, 1.0900, 1.0800, 1.0800, 1.0900)
        assert sl_hit is True
        assert tp_hit is False

    def test_same_bar_tp_closer_to_open_wins(self):
        sl_hit, tp_hit = resolve_same_bar_tp_sl("long", 1.0870, 1.0900, 1.0800, 1.0800, 1.0900)
        assert sl_hit is False
        assert tp_hit is True

    def test_evaluate_hypothetical_tp_hit(self):
        bars = [
            {"open": 1.0850, "high": 1.0860, "low": 1.0840, "close": 1.0855},
            {"open": 1.0855, "high": 1.0960, "low": 1.0850, "close": 1.0950},
        ]
        outcome = evaluate_hypothetical_outcome("long", 1.0850, 1.0800, 1.0950, bars, spread_cost_r=0.05)
        assert outcome.hypothetical_result == "would_have_won"
        assert outcome.hypothetical_exit == "tp_first"
        assert outcome.mfe_r > 0
        assert outcome.data_complete is True

    @pytest.mark.asyncio
    async def test_worker_updates_mfe_mae_idempotently(self, funnel, temp_db):
        did = await funnel.record_decision(
            "judge_reject",
            "EURUSD",
            direction="long",
            entry=1.0850,
            sl=1.0800,
            tp=1.0950,
            confidence=0.7,
            judge_verdict="REJECT",
        )

        bars = [
            {"open": 1.0850, "high": 1.0860, "low": 1.0840, "close": 1.0855},
            {"open": 1.0855, "high": 1.0960, "low": 1.0850, "close": 1.0950},
        ]
        mt5 = AsyncMock()
        mt5.get_ohlcv_data = AsyncMock(return_value=bars)

        service = TradeLearningService(gate_funnel=funnel)

        async with temp_db.async_session_maker() as session:
            row = (
                await session.execute(
                    select(SignalDecisionModel).where(SignalDecisionModel.decision_id == did)
                )
            ).scalar_one()
            row.timestamp = datetime.now(timezone.utc) - timedelta(hours=10)
            await session.commit()

        updated = await service.process_decision_outcomes(mt5, lookback_hours=24, horizon_hours=8)
        assert updated == 1

        async with temp_db.async_session_maker() as session:
            row = (
                await session.execute(
                    select(SignalDecisionModel).where(SignalDecisionModel.decision_id == did)
                )
            ).scalar_one()
            assert row.mfe_r is not None
            assert row.mae_r is not None
            assert row.hypothetical_result == "would_have_won"
            assert row.outcome_worker_status == "complete"

        updated_again = await service.process_decision_outcomes(mt5, lookback_hours=24, horizon_hours=8)
        assert updated_again == 0


class TestFalseRejectionAnalytics:
    @pytest.mark.asyncio
    async def test_analytics_cover_extended_categories(self, funnel):
        for outcome, result in [
            ("judge_reject", "would_have_won"),
            ("judge_demote", "would_have_lost"),
            ("mechanical_reject", "would_have_won"),
            ("pending_expired", "would_have_lost"),
            ("pending_cancelled", "would_have_won"),
        ]:
            did = await funnel.record_decision(
                outcome,
                "EURUSD",
                gate_id="low_confidence" if outcome == "mechanical_reject" else None,
                direction="long",
                entry=1.0850,
                sl=1.0800,
                tp=1.0950,
                confidence=0.7,
            )
            hypo = evaluate_hypothetical_outcome(
                "long", 1.0850, 1.0800, 1.0950,
                [{"open": 1.0850, "high": 1.0960, "low": 1.0840, "close": 1.0950}]
                if result == "would_have_won"
                else [{"open": 1.0850, "high": 1.0860, "low": 1.0790, "close": 1.0800}],
            )
            await funnel.update_hypothetical_outcome(did, hypo)

        analytics = await funnel.get_aggregate_analytics(days_back=30)
        assert analytics["total_decisions"] == 5
        assert analytics["mfe_coverage"]["evaluated"] == 5
        assert analytics["mfe_coverage"]["coverage_pct"] == pytest.approx(100.0)
        for cat in ("judge_reject", "judge_demote", "mechanical_reject", "pending_expired", "pending_cancelled"):
            assert cat in analytics["false_rejection"]
            assert analytics["false_rejection"][cat]["total"] == 1


class TestGateAnalyticsAPI:
    @pytest.mark.asyncio
    async def test_gate_analytics_endpoint(self, funnel, temp_db):
        await funnel.record_decision(
            "judge_reject",
            "GBPUSD",
            direction="short",
            entry=1.2700,
            sl=1.2750,
            tp=1.2600,
            confidence=0.68,
            judge_verdict="REJECT",
        )

        service = TradeLearningService(gate_funnel=funnel)
        payload = await service.get_gate_decision_analytics(days_back=7)

        assert "gate_expectancy" in payload
        assert "mfe_coverage" in payload
        assert payload["total_decisions"] >= 1


class TestMainDecisionWiring:
    @pytest.mark.asyncio
    async def test_record_terminal_decision_on_bot(self, funnel, temp_db):
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.gate_funnel = funnel

        did = await bot._record_terminal_decision(
            "execution_failure",
            "EURUSD",
            direction="long",
            entry=1.0850,
            sl=1.0800,
            tp=1.0950,
            confidence=0.8,
            reason="order rejected by broker",
        )
        assert did is not None

        async with temp_db.async_session_maker() as session:
            row = (
                await session.execute(
                    select(SignalDecisionModel).where(SignalDecisionModel.decision_id == did)
                )
            ).scalar_one()
            assert row.outcome_type == "execution_failure"


class TestOrchestratorDecisionWiring:
    @pytest.fixture
    def bot(self, funnel, temp_db):
        from trading_bot.execution.risk_manager import RiskManager
        from trading_bot.main import TradingBot
        from trading_bot.services.trade_reservations import TradeReservationLedger

        risk_manager = RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)
        counters = {"daily_trades": 0}
        bot = TradingBot.__new__(TradingBot)
        bot.gate_funnel = funnel
        bot.risk_manager = risk_manager
        bot.daily_trades = 0
        bot.reservation_ledger = TradeReservationLedger(
            risk_manager=risk_manager,
            get_daily_trades=lambda: counters["daily_trades"],
            set_daily_trades=lambda v: counters.update(daily_trades=v),
        )
        bot._trading_mode = "normal"
        return bot

    async def _row_for(self, temp_db, decision_id):
        async with temp_db.async_session_maker() as session:
            return (
                await session.execute(
                    select(SignalDecisionModel).where(
                        SignalDecisionModel.decision_id == decision_id
                    )
                )
            ).scalar_one()

    @pytest.mark.asyncio
    async def test_reject_and_record_creates_mechanical_reject(self, bot, temp_db):
        reservation = bot.reservation_ledger.reserve("EURUSD", risk_percent=0.01)
        bot.reservation_ledger.commit_risk(reservation)

        did = await bot._reject_and_record(
            reservation,
            "mechanical_reject",
            "EURUSD",
            gate_id="final_risk_block",
            direction="long",
            entry=1.0850,
            sl=1.0800,
            tp=1.0950,
            confidence=0.75,
            reason="final risk cap exceeded",
        )
        assert did is False

        async with temp_db.async_session_maker() as session:
            row = (
                await session.execute(
                    select(SignalDecisionModel).where(
                        SignalDecisionModel.gate_id == "final_risk_block"
                    )
                )
            ).scalar_one()
        assert row.outcome_type == "mechanical_reject"
        assert row.gate_id == "final_risk_block"
        assert bot.daily_trades == 0

    @pytest.mark.asyncio
    async def test_no_trade_records_terminal_decision(self, bot, temp_db):
        did = await bot._record_terminal_decision(
            "no_trade",
            "EURUSD",
            direction="no_trade",
            entry=1.0850,
            confidence=0.0,
            reason="No structural setup",
        )
        row = await self._row_for(temp_db, did)
        assert row.outcome_type == "no_trade"

    @pytest.mark.asyncio
    async def test_market_filled_records_terminal_decision(self, bot, temp_db):
        did = await bot._record_terminal_decision(
            "market_filled",
            "GBPUSD",
            direction="long",
            entry=1.2700,
            sl=1.2650,
            tp=1.2800,
            confidence=0.82,
            reason="market order filled",
        )
        row = await self._row_for(temp_db, did)
        assert row.outcome_type == "market_filled"

    @pytest.mark.asyncio
    async def test_pending_placed_records_terminal_decision(self, bot, temp_db):
        did = await bot._record_terminal_decision(
            "pending_placed",
            "XAUUSD",
            direction="short",
            entry=2350.0,
            sl=2360.0,
            tp=2320.0,
            confidence=0.78,
            reason="pending limit placed",
        )
        row = await self._row_for(temp_db, did)
        assert row.outcome_type == "pending_placed"

    @pytest.mark.asyncio
    async def test_pending_cancelled_via_manager_callback(self, bot, temp_db):
        from trading_bot.services.pending_order_manager import (
            PendingOrder,
            PendingOrderManager,
            PendingOrderStatus,
        )

        recorded = []

        async def recorder(outcome_type, order, reason):
            did = await bot._record_terminal_decision(
                outcome_type,
                order.symbol,
                direction=order.direction,
                entry=order.price,
                sl=order.stop_loss or 0.0,
                tp=order.take_profit or 0.0,
                reason=reason,
            )
            recorded.append(did)

        manager = PendingOrderManager(mt5_client=AsyncMock(), order_manager=AsyncMock())
        manager.order_manager.cancel_order = AsyncMock(
            return_value=MagicMock(success=True, message="ok")
        )
        manager.set_decision_recorder(recorder)

        order = PendingOrder(
            ticket=777,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
        )
        manager.pending_orders[777] = order

        assert await manager.cancel_order(777, reason="manual") is True
        assert len(recorded) == 1
        row = await self._row_for(temp_db, recorded[0])
        assert row.outcome_type == "pending_cancelled"

    @pytest.mark.asyncio
    async def test_pending_expired_via_manager_callback(self, bot, temp_db):
        from trading_bot.services.pending_order_manager import (
            PendingOrder,
            PendingOrderManager,
            PendingOrderStatus,
        )

        recorded = []

        async def recorder(outcome_type, order, reason):
            did = await bot._record_terminal_decision(
                outcome_type,
                order.symbol,
                direction=order.direction,
                entry=order.price,
                sl=order.stop_loss or 0.0,
                tp=order.take_profit or 0.0,
                reason=reason,
            )
            recorded.append((outcome_type, did))

        manager = PendingOrderManager(mt5_client=AsyncMock(), order_manager=AsyncMock())
        manager.order_manager.cancel_order = AsyncMock(
            return_value=MagicMock(success=True, message="ok")
        )
        manager.set_decision_recorder(recorder)

        order = PendingOrder(
            ticket=888,
            symbol="EURUSD",
            order_type="sell_limit",
            direction="short",
            volume=0.05,
            price=1.0900,
            stop_loss=1.0950,
            take_profit=1.0800,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expiration=datetime.now(timezone.utc) - timedelta(minutes=5),
            status=PendingOrderStatus.ACTIVE,
        )
        manager.pending_orders[888] = order

        assert await manager.cancel_order(888, reason="expired") is True
        assert recorded[0][0] == "pending_expired"
        row = await self._row_for(temp_db, recorded[0][1])
        assert row.outcome_type == "pending_expired"

    @pytest.mark.asyncio
    async def test_pending_filled_on_fill_transfer(self, bot, temp_db):
        from trading_bot.services.pending_order_manager import (
            FilledPositionEvent,
            PendingOrder,
            PendingOrderStatus,
        )

        bot.position_manager = MagicMock()
        bot.position_manager.get_position = MagicMock(return_value=MagicMock())
        bot.position_manager._persist_and_wait = AsyncMock()
        bot.pending_order_manager = MagicMock()
        bot.pending_order_manager.order_history = [
            PendingOrder(
                ticket=555,
                symbol="EURUSD",
                order_type="buy_limit",
                direction="long",
                volume=0.05,
                price=1.0800,
                stop_loss=1.0750,
                take_profit=1.0900,
                created_at=datetime.now(timezone.utc),
                expiration=datetime.now(timezone.utc) + timedelta(hours=1),
                status=PendingOrderStatus.FILLED,
                fill_price=1.0800,
            )
        ]

        await bot._apply_pending_fill_transfers(
            [
                FilledPositionEvent(
                    order_ticket=555,
                    position_ticket=556,
                    reservation_id=None,
                )
            ]
        )

        async with temp_db.async_session_maker() as session:
            rows = (await session.execute(select(SignalDecisionModel))).scalars().all()
        assert any(row.outcome_type == "pending_filled" for row in rows)

    @pytest.mark.asyncio
    async def test_execution_failure_records_on_broker_reject(self, bot, temp_db):
        did = await bot._record_terminal_decision(
            "execution_failure",
            "EURUSD",
            direction="long",
            entry=1.0850,
            sl=1.0800,
            tp=1.0950,
            confidence=0.8,
            reason="broker rejected order",
        )
        row = await self._row_for(temp_db, did)
        assert row.outcome_type == "execution_failure"
