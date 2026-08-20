"""Telegram proactive alerts: pending lifecycle, halt, and connection cards."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.utils.notifications import (
    NotificationType,
    format_connection_alert,
    format_pending_cancelled,
    format_pending_filled,
    format_pending_placed,
    format_trading_halted,
    notify,
    safe_notify,
)


class TestFormatters:
    def test_pending_placed_includes_levels_and_expiry(self):
        text = format_pending_placed(
            symbol="XAUUSD",
            direction="long",
            order_type="buy_limit",
            entry_price=3385.20,
            stop_loss=3378.10,
            take_profit=3402.40,
            lots=0.05,
            ticket=12345,
            expires_min=47.2,
        )
        assert "⏳" in text
        assert "Pending Placed" in text
        assert "XAUUSD" in text
        assert "BUY LIMIT" in text
        assert "LONG" in text
        assert "3385.20" in text
        assert "3378.10" in text
        assert "3402.40" in text
        assert "1:2.4" in text
        assert "0.05 lots" in text
        assert "~47 min" in text
        assert "12345" in text

    def test_pending_filled_includes_tickets(self):
        text = format_pending_filled(
            symbol="EURUSD",
            direction="short",
            fill_price=1.08421,
            ticket=11,
            position_ticket=22,
            lots=0.10,
        )
        assert "✅" in text
        assert "Pending Filled" in text
        assert "EURUSD" in text
        assert "SHORT" in text
        assert "1.08421" in text
        assert "11" in text
        assert "22" in text

    def test_cancelled_reason_labels(self):
        expired = format_pending_cancelled(
            "GBPUSD", "long", "buy_stop", 1.27000, ticket=9, reason="expired"
        )
        assert "expired (not filled)" in expired
        assert "🚫" in expired

        replaced = format_pending_cancelled(
            "GBPUSD", "short", "sell_limit", 1.28000, reason="replaced_by_newer"
        )
        assert "replaced by newer signal" in replaced

        spike = format_pending_cancelled(
            "XAUUSD", "long", "buy_limit", 3300.0, reason="volatility_spike"
        )
        assert "volatility spike" in spike

        external = format_pending_cancelled(
            "EURUSD", "long", "buy_limit", 1.08, reason="external"
        )
        assert "cancelled on broker" in external

    def test_halt_and_connection_cards(self):
        halt = format_trading_halted("DEFENSIVE mode (severe drawdown)")
        assert "🛑" in halt
        assert "DEFENSIVE mode" in halt
        assert "Position management continues" in halt

        down = format_connection_alert(reconnected=False)
        assert "MT5 Disconnected" in down
        up = format_connection_alert(reconnected=True)
        assert "MT5 Reconnected" in up


class TestNotifyRouting:
    @pytest.mark.asyncio
    async def test_routes_pending_and_halt_types(self):
        sent = []

        async def fake_send(text, parse_mode="HTML"):
            sent.append(text)
            return True

        notifier = SimpleNamespace(
            send_message=fake_send,
            notify_trade_opened=AsyncMock(),
            notify_trade_closed=AsyncMock(),
            notify_error=AsyncMock(),
            notify_daily_summary=AsyncMock(),
        )
        with patch("trading_bot.utils.notifications.get_notifier", return_value=notifier):
            await notify(
                NotificationType.PENDING_PLACED,
                "x",
                symbol="XAUUSD",
                direction="long",
                order_type="buy_limit",
                entry_price=2000.0,
                stop_loss=1990.0,
                take_profit=2030.0,
                lots=0.02,
                ticket=7,
            )
            await notify(
                NotificationType.PENDING_FILLED,
                "x",
                symbol="XAUUSD",
                direction="long",
                fill_price=2001.5,
            )
            await notify(
                NotificationType.PENDING_CANCELLED,
                "x",
                symbol="XAUUSD",
                direction="long",
                reason="expired",
            )
            await notify(
                NotificationType.TRADING_HALTED,
                "ignored",
                reason="Daily profit lock (+5.1%)",
            )
            await notify(NotificationType.CONNECTION, "x", reconnected=False)
            await notify(NotificationType.ALERT, "🚨 EMERGENCY CLOSE")
            await notify(NotificationType.WARNING, "Daily drawdown at 2.3%")

        assert any("Pending Placed" in t for t in sent)
        assert any("Pending Filled" in t for t in sent)
        assert any("Pending Cancelled" in t for t in sent)
        assert any("Trading Halted" in t for t in sent)
        assert any("MT5 Disconnected" in t for t in sent)
        assert sent[-2] == "🚨 EMERGENCY CLOSE"
        assert sent[-1].startswith("⚠️ ")

    @pytest.mark.asyncio
    async def test_safe_notify_swallows_errors(self):
        with patch(
            "trading_bot.utils.notifications.notify",
            AsyncMock(side_effect=RuntimeError("telegram down")),
        ):
            ok = await safe_notify(NotificationType.PENDING_PLACED, "x")
        assert ok is False

    def test_alert_is_a_real_enum_member(self):
        assert NotificationType.ALERT.value == "alert"
        assert NotificationType.PENDING_PLACED.value == "pending_placed"
        assert NotificationType.CONNECTION.value == "connection"


class TestPendingLifecycleNotify:
    @pytest.mark.asyncio
    async def test_add_order_notifies_placed(self):
        from trading_bot.services.pending_order_manager import PendingOrderManager

        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=AsyncMock(),
            kill_zone_checker=None,
        )
        with patch(
            "trading_bot.utils.notifications.safe_notify", new_callable=AsyncMock
        ) as mocked:
            await manager.add_order(
                ticket=67890,
                symbol="EURUSD",
                order_type="buy_limit",
                direction="long",
                volume=0.05,
                price=1.0800,
                stop_loss=1.0750,
                take_profit=1.0900,
                expiration_minutes=60,
            )
        mocked.assert_awaited()
        args, kwargs = mocked.await_args
        assert args[0] == NotificationType.PENDING_PLACED
        assert kwargs["symbol"] == "EURUSD"
        assert kwargs["ticket"] == 67890
        assert kwargs["entry_price"] == 1.0800

    @pytest.mark.asyncio
    async def test_cancel_order_notifies_cancelled(self):
        from trading_bot.services.pending_order_manager import PendingOrderManager

        mock_order_manager = AsyncMock()
        mock_order_manager.cancel_order.return_value = MagicMock(
            success=True, message="ok"
        )
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=mock_order_manager,
            kill_zone_checker=None,
        )
        with patch(
            "trading_bot.utils.notifications.safe_notify", new_callable=AsyncMock
        ):
            await manager.add_order(
                ticket=1,
                symbol="GBPUSD",
                order_type="sell_limit",
                direction="short",
                volume=0.02,
                price=1.2500,
            )
        with patch(
            "trading_bot.utils.notifications.safe_notify", new_callable=AsyncMock
        ) as mocked:
            await manager.cancel_order(1, reason="replaced_by_newer")
        args, kwargs = mocked.await_args
        assert args[0] == NotificationType.PENDING_CANCELLED
        assert kwargs["reason"] == "replaced_by_newer"

    @pytest.mark.asyncio
    async def test_sync_fill_notifies_filled(self):
        from trading_bot.services.pending_order_manager import PendingOrderManager

        pos = SimpleNamespace(
            ticket=999,
            symbol="XAUUSD",
            price_open=2650.50,
            identifier=42,
            order=42,
        )
        mt5 = AsyncMock()
        mt5.get_orders = AsyncMock(return_value=[])
        mt5.get_positions = AsyncMock(return_value=[pos])
        manager = PendingOrderManager(
            mt5_client=mt5, order_manager=AsyncMock(), kill_zone_checker=None
        )
        with patch(
            "trading_bot.utils.notifications.safe_notify", new_callable=AsyncMock
        ):
            await manager.add_order(
                ticket=42,
                symbol="XAUUSD",
                order_type="buy_limit",
                direction="long",
                volume=0.03,
                price=2650.00,
            )
        with patch(
            "trading_bot.utils.notifications.safe_notify", new_callable=AsyncMock
        ) as mocked:
            result = await manager.sync_with_mt5()
        assert result["filled"] == 1
        args, kwargs = mocked.await_args
        assert args[0] == NotificationType.PENDING_FILLED
        assert kwargs["fill_price"] == 2650.50
        assert kwargs["position_ticket"] == 999


class TestMt5DisconnectDebounce:
    @pytest.mark.asyncio
    async def test_disconnect_once_then_reconnect(self):
        from trading_bot.main import TradingBot

        sent = []

        async def fake_safe_notify(ntype, message="", **kwargs):
            sent.append((ntype, kwargs.get("reconnected")))
            return True

        bot = SimpleNamespace(_mt5_disconnect_notified=False)
        with patch("trading_bot.main.safe_notify", fake_safe_notify):
            await TradingBot._notify_mt5_disconnected(bot, "Reconnection failed")
            await TradingBot._notify_mt5_disconnected(bot, "Reconnection failed")
            await TradingBot._notify_mt5_reconnected(bot)
            await TradingBot._notify_mt5_reconnected(bot)

        assert sent == [
            (NotificationType.CONNECTION, False),
            (NotificationType.CONNECTION, True),
        ]
        assert bot._mt5_disconnect_notified is False
