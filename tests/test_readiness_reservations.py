"""
Wave 1 Task 1 — exactly-once trade reservation accounting.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot.execution.risk_manager import RiskManager
from trading_bot.services.pending_order_manager import (
    PendingOrder,
    PendingOrderManager,
    PendingOrderStatus,
)
from trading_bot.services.trade_reservations import (
    ReservationState,
    TradeReservationLedger,
)


@pytest.fixture
def counters():
    return {"daily_trades": 0}


@pytest.fixture
def risk_manager():
    return RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)


@pytest.fixture
def ledger(risk_manager, counters):
    return TradeReservationLedger(
        risk_manager=risk_manager,
        get_daily_trades=lambda: counters["daily_trades"],
        set_daily_trades=lambda v: counters.update(daily_trades=v),
    )


class TestTradeReservationLedger:
    def test_reserve_increments_slot_once(self, ledger, counters):
        reservation = ledger.reserve("EURUSD", signal_id="sig-1", risk_percent=0.015)
        assert counters["daily_trades"] == 1
        assert reservation.state == ReservationState.RESERVED
        assert reservation._slot_applied is True

    def test_release_is_idempotent(self, ledger, counters, risk_manager):
        reservation = ledger.reserve("EURUSD", risk_percent=0.015)
        ledger.commit_risk(reservation)

        assert ledger.release(reservation) is True
        assert counters["daily_trades"] == 0
        assert risk_manager.daily_risk_used == pytest.approx(0.0)

        assert ledger.release(reservation) is False
        assert counters["daily_trades"] == 0
        assert risk_manager.daily_risk_used == pytest.approx(0.0)

    def test_transfer_to_pending_retains_slot_and_risk(self, ledger, counters, risk_manager):
        reservation = ledger.reserve("GBPUSD", risk_percent=0.012)
        ledger.commit_risk(reservation)
        ledger.transfer_to_pending(reservation, ticket=111)

        assert reservation.state == ReservationState.TRANSFERRED
        assert counters["daily_trades"] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.012)
        assert ledger.get_for_ticket(111) is reservation

    def test_transfer_to_position_retains_ownership(self, ledger, counters, risk_manager):
        reservation = ledger.reserve("XAUUSD", risk_percent=0.01)
        ledger.commit_risk(reservation)
        ledger.transfer_to_position(reservation, ticket=222)

        assert reservation.state == ReservationState.TRANSFERRED
        assert counters["daily_trades"] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.01)
        assert ledger.get_for_ticket(222) is reservation

    def test_mark_closed_reclaims_risk_but_not_slot(self, ledger, counters, risk_manager):
        reservation = ledger.reserve("XAUUSD", risk_percent=0.01)
        ledger.commit_risk(reservation)
        ledger.transfer_to_position(reservation, ticket=333)

        assert ledger.mark_closed(reservation) is True
        assert reservation.state == ReservationState.CLOSED
        assert counters["daily_trades"] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.0)

        assert ledger.mark_closed(reservation) is False
        assert counters["daily_trades"] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.0)

    def test_post_reservation_rejection_restores_original_totals(self, ledger, counters, risk_manager):
        reservation = ledger.reserve("EURUSD", risk_percent=0.015)
        ledger.release(reservation)

        assert counters["daily_trades"] == 0
        assert risk_manager.daily_risk_used == pytest.approx(0.0)

    def test_imported_order_without_reservation_does_not_reclaim_bot_budget(self, ledger, counters, risk_manager):
        counters["daily_trades"] = 2
        risk_manager.update_daily_risk(0.02)

        manager = PendingOrderManager(mt5_client=None, order_manager=None)
        manager.set_budget_reclaim(
            risk_manager=risk_manager,
            reservation_ledger=ledger,
            get_daily_trades=lambda: counters["daily_trades"],
            set_daily_trades=lambda v: counters.update(daily_trades=v),
        )

        imported = PendingOrder(
            ticket=999001,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.015,
            reservation_id=None,
        )
        manager.pending_orders[imported.ticket] = imported

        manager._reclaim_reserved_budget(imported, "external_cancel")

        assert counters["daily_trades"] == 2
        assert risk_manager.daily_risk_used == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_owned_pending_cancel_reclaims_once(self, ledger, counters, risk_manager):
        from unittest.mock import AsyncMock, MagicMock

        counters["daily_trades"] = 0

        reservation = ledger.reserve("EURUSD", risk_percent=0.015)
        ledger.commit_risk(reservation)
        ledger.transfer_to_pending(reservation, ticket=555)

        mock_order_manager = AsyncMock()
        mock_order_manager.cancel_order.return_value = MagicMock(success=True, message="ok")

        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=mock_order_manager,
        )
        manager.set_budget_reclaim(
            risk_manager=risk_manager,
            reservation_ledger=ledger,
            get_daily_trades=lambda: counters["daily_trades"],
            set_daily_trades=lambda v: counters.update(daily_trades=v),
        )

        order = PendingOrder(
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
            status=PendingOrderStatus.ACTIVE,
            risk_percent=0.015,
            reservation_id=reservation.reservation_id,
        )
        manager.pending_orders[555] = order

        assert await manager.cancel_order(555, reason="manual") is True
        assert counters["daily_trades"] == 0
        assert risk_manager.daily_risk_used == pytest.approx(0.0)
        assert reservation.state == ReservationState.RELEASED


class TestProductionReservationOwnership:
    def _bot(self, risk_manager, counters):
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.daily_trades = counters["daily_trades"]
        bot.risk_manager = risk_manager
        bot.reservation_ledger = TradeReservationLedger(
            risk_manager=risk_manager,
            get_daily_trades=lambda: counters["daily_trades"],
            set_daily_trades=lambda value: counters.update(daily_trades=value),
        )
        return bot

    def test_pending_manager_is_wired_to_production_ledger(self, risk_manager, counters):
        bot = self._bot(risk_manager, counters)
        bot.pending_order_manager = MagicMock()

        bot._wire_pending_reservation_accounting()

        bot.pending_order_manager.set_budget_reclaim.assert_called_once()
        kwargs = bot.pending_order_manager.set_budget_reclaim.call_args.kwargs
        assert kwargs["risk_manager"] is risk_manager
        assert kwargs["reservation_ledger"] is bot.reservation_ledger

    @pytest.mark.parametrize(
        "decision,args",
        [
            ("_accept_nonzero_lots", (0.0,)),
            ("_accept_precheck", (SimpleNamespace(can_execute=False),)),
            ("_accept_final_rr", (100.0, 95.0, 101.0)),
            ("_accept_execution_mode", (True,)),
            ("_accept_tick_refine", (False,)),
        ],
    )
    def test_production_rejection_decisions_release_attempt(
        self, decision, args, risk_manager, counters
    ):
        bot = self._bot(risk_manager, counters)
        reservation = bot.reservation_ledger.reserve(
            "EURUSD", signal_id=decision, risk_percent=0.015
        )

        accepted = getattr(bot, decision)(reservation, *args)

        assert accepted is False
        assert counters["daily_trades"] == 0
        assert risk_manager.daily_risk_used == pytest.approx(0.0)
        assert reservation.state == ReservationState.RELEASED

    def test_success_without_position_rolls_back_committed_risk(
        self, risk_manager, counters
    ):
        bot = self._bot(risk_manager, counters)
        reservation = bot.reservation_ledger.reserve(
            "EURUSD", signal_id="unverified", risk_percent=0.015
        )
        bot.reservation_ledger.commit_risk(reservation)

        accepted = bot._accept_verified_position(reservation, 901, [])

        assert accepted is False
        assert counters["daily_trades"] == 0
        assert risk_manager.daily_risk_used == pytest.approx(0.0)
        assert reservation.state == ReservationState.RELEASED

    @pytest.mark.asyncio
    async def test_pending_replacement_releases_old_not_incoming_reservation(
        self, risk_manager, counters
    ):
        bot = self._bot(risk_manager, counters)
        order_manager = AsyncMock()
        order_manager.cancel_order.return_value = SimpleNamespace(success=True)
        bot.pending_order_manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=order_manager,
        )
        bot._wire_pending_reservation_accounting()

        old_reservation = bot.reservation_ledger.reserve(
            "EURUSD", signal_id="old", risk_percent=0.01
        )
        bot.reservation_ledger.commit_risk(old_reservation)
        bot.reservation_ledger.transfer_to_pending(old_reservation, ticket=1001)
        old_order = PendingOrder(
            ticket=1001,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.08,
            stop_loss=1.075,
            take_profit=1.09,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            reservation_id=old_reservation.reservation_id,
            risk_percent=0.01,
        )
        bot.pending_order_manager.pending_orders[old_order.ticket] = old_order

        incoming = bot.reservation_ledger.reserve(
            "EURUSD", signal_id="incoming", risk_percent=0.02
        )

        assert await bot._cancel_pending_for_replacement(old_order) is True

        assert old_reservation.state == ReservationState.RELEASED
        assert incoming.state == ReservationState.RESERVED
        assert counters["daily_trades"] == 1
        assert risk_manager.daily_risk_used == pytest.approx(0.0)

    def test_restore_pending_ownership_does_not_increment_existing_totals(
        self, risk_manager, counters
    ):
        counters["daily_trades"] = 3
        risk_manager.daily_risk_used = 0.025
        ledger = TradeReservationLedger(
            risk_manager=risk_manager,
            get_daily_trades=lambda: counters["daily_trades"],
            set_daily_trades=lambda value: counters.update(daily_trades=value),
        )

        restored = ledger.restore_pending(
            reservation_id="persisted-reservation",
            symbol="EURUSD",
            ticket=777,
            risk_percent=0.01,
        )

        assert counters["daily_trades"] == 3
        assert risk_manager.daily_risk_used == pytest.approx(0.025)
        assert ledger.get_for_ticket(777) is restored

        assert ledger.release(restored) is True
        assert counters["daily_trades"] == 2
        assert risk_manager.daily_risk_used == pytest.approx(0.015)
