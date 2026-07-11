"""
Wave 1 Task 2 — fast pending fill→close routes through unified close lifecycle once.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.execution.position_manager import Position
from trading_bot.execution.position_manager import PositionManager
from trading_bot.execution.risk_manager import RiskManager
from trading_bot.services.pending_order_manager import (
    PendingOrder,
    PendingOrderManager,
    PendingOrderStatus,
)
from trading_bot.services.trade_reservations import TradeReservationLedger


def _make_bot_stub():
    """Minimal bot surface for close lifecycle integration tests."""
    from trading_bot.main import TradingBot

    bot = TradingBot.__new__(TradingBot)
    bot.daily_pnl = 0.0
    bot.daily_trades = 1
    bot.win_streak = 0
    bot.loss_streak = 0
    bot._symbol_loss_cooldowns = {}
    bot._last_learnings_update = None
    bot.mt5_client = MagicMock()
    bot.mt5_client.is_simulation = True
    bot.mt5_client.get_history = AsyncMock(return_value=[])
    bot.risk_manager = RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)
    bot.risk_manager.update_daily_risk(0.01)
    bot.session_analytics = MagicMock()
    bot.session_analytics.record_trade = MagicMock()
    bot.scaling_manager = MagicMock()
    bot.scaling_manager.record_trade = MagicMock()
    bot.correlation_service = None
    bot.learning_service = None
    bot.claude_client = None
    bot._reversal_cooldowns = {}
    counters = {"daily_trades": bot.daily_trades}
    bot.reservation_ledger = TradeReservationLedger(
        risk_manager=bot.risk_manager,
        get_daily_trades=lambda: counters["daily_trades"],
        set_daily_trades=lambda v: counters.update(daily_trades=v),
    )
    bot._processed_pending_close_deals = set()
    bot._daily_trade_counters = counters
    bot._close_handler_calls = 0

    async def _track_close(position):
        bot._close_handler_calls += 1
        await TradingBot._handle_position_close(bot, position)

    bot._handle_position_close = _track_close
    return bot


class TestFastPendingCloseLifecycle:
    @pytest.mark.asyncio
    async def test_filled_then_closed_emits_single_close_event(self):
        from trading_bot.services.pending_order_manager import ClosedTradeEvent

        now = datetime.now(timezone.utc)
        order_ticket = 70001
        position_id = 80001
        closing_deal = 90001

        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []
        mock_mt5.get_positions.return_value = []
        mock_mt5.get_history.return_value = [
            {
                "entry": 0,
                "order": order_ticket,
                "position_id": position_id,
                "price": 1.0850,
                "time": now - timedelta(seconds=30),
                "ticket": 91001,
                "commission": -0.10,
            },
            {
                "entry": 1,
                "order": order_ticket,
                "position_id": position_id,
                "price": 1.0900,
                "time": now,
                "ticket": closing_deal,
                "profit": 25.0,
                "commission": -0.10,
                "swap": 0.0,
            },
        ]

        manager = PendingOrderManager(mt5_client=mock_mt5, order_manager=None)
        order = PendingOrder(
            ticket=order_ticket,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.10,
            price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            created_at=now - timedelta(minutes=5),
            expiration=now + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.01,
        )
        manager.pending_orders[order_ticket] = order

        result = await manager.sync_with_mt5()

        assert result["filled_closed"] == 1
        events = result.get("closed_trade_events", [])
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, ClosedTradeEvent)
        assert event.order_ticket == order_ticket
        assert event.position_ticket == position_id
        assert event.closing_deal_ticket == closing_deal
        assert event.profit_loss == pytest.approx(24.8)

    @pytest.mark.asyncio
    async def test_close_event_routes_through_position_close_once(self):
        bot = _make_bot_stub()
        now = datetime.now(timezone.utc)

        from trading_bot.services.pending_order_manager import ClosedTradeEvent

        event = ClosedTradeEvent(
            order_ticket=70002,
            position_ticket=80002,
            symbol="EURUSD",
            direction="long",
            volume=0.10,
            entry_price=1.0850,
            exit_price=1.0900,
            profit_loss=24.8,
            close_time=now,
            close_reason="sl_tp_fast_close",
            closing_deal_ticket=90002,
            reservation_id=None,
        )

        with patch("trading_bot.main.DB_AVAILABLE", False), patch(
            "trading_bot.main.notify", new=AsyncMock()
        ), patch("trading_bot.main.broadcast_trade_update", new=AsyncMock()), patch(
            "trading_bot.api.routes.activity.add_activity"
        ):
            await bot._process_pending_closed_trade_events([event])

        assert bot._close_handler_calls == 1
        assert bot.daily_pnl == pytest.approx(24.8)
        assert bot.win_streak == 1
        assert bot.loss_streak == 0
        # Event has no reservation_id: imported/manual closes cannot reclaim
        # risk budget they do not own.
        assert bot.risk_manager.daily_risk_used == pytest.approx(0.01)
        assert bot._daily_trade_counters["daily_trades"] == 1

    @pytest.mark.asyncio
    async def test_duplicate_close_deal_is_ignored(self):
        bot = _make_bot_stub()
        now = datetime.now(timezone.utc)

        from trading_bot.services.pending_order_manager import ClosedTradeEvent

        event = ClosedTradeEvent(
            order_ticket=70003,
            position_ticket=80003,
            symbol="EURUSD",
            direction="long",
            volume=0.10,
            entry_price=1.0850,
            exit_price=1.0900,
            profit_loss=24.8,
            close_time=now,
            close_reason="sl_tp_fast_close",
            closing_deal_ticket=90003,
            reservation_id=None,
        )

        with patch("trading_bot.main.DB_AVAILABLE", False), patch(
            "trading_bot.main.notify", new=AsyncMock()
        ), patch("trading_bot.main.broadcast_trade_update", new=AsyncMock()), patch(
            "trading_bot.api.routes.activity.add_activity"
        ):
            await bot._process_pending_closed_trade_events([event])
            await bot._process_pending_closed_trade_events([event])

        assert bot._close_handler_calls == 1
        assert bot.daily_pnl == pytest.approx(24.8)

    @pytest.mark.asyncio
    async def test_sync_to_normal_close_lifecycle_runs_every_effect_once(self):
        bot = _make_bot_stub()
        now = datetime.now(timezone.utc)
        order_ticket = 70100
        position_ticket = 80100
        closing_deal = 90100

        bot.risk_manager.daily_risk_used = 0.0
        reservation = bot.reservation_ledger.reserve(
            "EURUSD", signal_id="fast-close", risk_percent=0.01
        )
        bot.reservation_ledger.commit_risk(reservation)
        bot.reservation_ledger.transfer_to_pending(reservation, order_ticket)
        bot._daily_trade_counters["daily_trades"] = 1
        bot.daily_trades = 1

        mt5 = AsyncMock()
        mt5.get_orders.return_value = []
        mt5.get_positions.return_value = []
        mt5.get_history.return_value = [
            {
                "entry": 0,
                "order": order_ticket,
                "position_id": position_ticket,
                "price": 1.0850,
                "time": now - timedelta(seconds=30),
                "ticket": 91100,
                "commission": -0.10,
            },
            {
                "entry": 1,
                "order": order_ticket,
                "position_id": position_ticket,
                "price": 1.0800,
                "time": now,
                "ticket": closing_deal,
                "profit": -25.0,
                "commission": -0.10,
                "swap": 0.0,
            },
        ]
        manager = PendingOrderManager(mt5_client=mt5)
        manager.set_budget_reclaim(
            risk_manager=bot.risk_manager,
            reservation_ledger=bot.reservation_ledger,
            get_daily_trades=lambda: bot._daily_trade_counters["daily_trades"],
            set_daily_trades=lambda value: bot._daily_trade_counters.update(
                daily_trades=value
            ),
        )
        manager.pending_orders[order_ticket] = PendingOrder(
            ticket=order_ticket,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.10,
            price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            created_at=now - timedelta(minutes=5),
            expiration=now + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.01,
            reservation_id=reservation.reservation_id,
        )

        bot.claude_client = MagicMock()
        bot.claude_client.api_key = "test-key"
        bot.claude_client.review_closed_trade = AsyncMock(
            return_value={"grade": "C", "analysis": "reviewed"}
        )
        bot.learning_service = MagicMock()
        bot.learning_service.update_learnings_documentation = AsyncMock()
        bot.learning_service.store_trade_review = AsyncMock()
        bot.session_analytics.get_current_session.return_value = None
        notify_mock = AsyncMock()
        broadcast_mock = AsyncMock()

        sync_result = await manager.sync_with_mt5()
        events = sync_result["closed_trade_events"]

        with patch("trading_bot.main.DB_AVAILABLE", False), patch(
            "trading_bot.main.notify", new=notify_mock
        ), patch(
            "trading_bot.main.broadcast_trade_update", new=broadcast_mock
        ), patch(
            "trading_bot.api.routes.activity.add_activity"
        ):
            await bot._process_pending_closed_trade_events(events)
            await bot._process_pending_closed_trade_events(events)

        assert bot._close_handler_calls == 1
        assert bot.daily_pnl == pytest.approx(-25.2)
        assert bot.win_streak == 0
        assert bot.loss_streak == 1
        assert "EURUSD" in bot._symbol_loss_cooldowns
        assert bot.risk_manager.daily_risk_used == pytest.approx(0.0)
        assert bot._daily_trade_counters["daily_trades"] == 1
        assert reservation.state.value == "closed"
        bot.session_analytics.record_trade.assert_called_once()
        bot.scaling_manager.record_trade.assert_called_once()
        notify_mock.assert_awaited_once()
        bot.learning_service.store_trade_review.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_normal_fill_transfers_identity_then_later_close_reclaims_once(self):
        bot = _make_bot_stub()
        bot.risk_manager.daily_risk_used = 0.0
        bot._daily_trade_counters["daily_trades"] = 0
        order_ticket = 70200
        position_ticket = 80200

        reservation = bot.reservation_ledger.reserve(
            "EURUSD", signal_id="normal-fill", risk_percent=0.01
        )
        bot.reservation_ledger.commit_risk(reservation)
        bot.reservation_ledger.transfer_to_pending(reservation, order_ticket)

        mt5_position = MagicMock(
            ticket=position_ticket,
            identifier=order_ticket,
            order=order_ticket,
            symbol="EURUSD",
            type="buy",
            volume=0.10,
            price_open=1.0850,
            price_current=1.0860,
            sl=1.0800,
            tp=1.0950,
            time=datetime.now(timezone.utc),
            comment="ICT_Bot",
        )
        mt5 = AsyncMock()
        mt5.is_simulation = True
        mt5.get_orders.return_value = []
        mt5.get_positions.side_effect = [
            [mt5_position],
            [mt5_position],
            [],
            [],
        ]
        mt5.get_history.return_value = []
        bot.mt5_client = mt5

        bot.position_manager = PositionManager()
        bot.position_manager.set_on_position_close(bot._handle_position_close)
        bot.position_manager._persist_and_wait = AsyncMock()
        bot.pending_order_manager = PendingOrderManager(mt5_client=mt5)
        bot.pending_order_manager.set_budget_reclaim(
            risk_manager=bot.risk_manager,
            reservation_ledger=bot.reservation_ledger,
            get_daily_trades=lambda: bot._daily_trade_counters["daily_trades"],
            set_daily_trades=lambda value: bot._daily_trade_counters.update(
                daily_trades=value
            ),
        )
        bot.pending_order_manager.pending_orders[order_ticket] = PendingOrder(
            ticket=order_ticket,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.10,
            price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            risk_percent=0.01,
            reservation_id=reservation.reservation_id,
        )

        await bot.position_manager.sync_with_mt5(mt5)
        pending_result = await bot.pending_order_manager.sync_with_mt5()
        await bot._apply_pending_fill_transfers(
            pending_result.get("filled_position_events", [])
        )

        tracked = bot.position_manager.get_position(position_ticket)
        assert tracked.order_ticket == order_ticket
        assert tracked.reservation_id == reservation.reservation_id
        assert bot.reservation_ledger.get_for_ticket(position_ticket) is reservation
        bot.position_manager._persist_and_wait.assert_awaited_once_with(tracked)

        with patch("trading_bot.main.DB_AVAILABLE", False), patch(
            "trading_bot.main.notify", new=AsyncMock()
        ), patch(
            "trading_bot.main.broadcast_trade_update", new=AsyncMock()
        ), patch(
            "trading_bot.api.routes.activity.add_activity"
        ):
            await bot.position_manager.sync_with_mt5(mt5)
            await bot.position_manager.sync_with_mt5(mt5)

        assert bot.risk_manager.daily_risk_used == pytest.approx(0.0)
        assert reservation.state.value == "closed"
        assert bot._close_handler_calls == 1
