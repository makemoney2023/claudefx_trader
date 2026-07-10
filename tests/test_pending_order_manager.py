"""
Tests for Pending Order Manager - tracking and managing limit/stop orders.

Updated to match current PendingOrderManager API which uses:
- add_order() to track orders (not place_pending_order)
- get_active_orders() to retrieve active orders
- cancel_order() to cancel orders
- check_expirations() to find expired orders
- PendingOrder dataclass objects (not plain dicts)
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


class TestPendingOrderManager:
    """Tests for pending order management."""
    
    @pytest.mark.asyncio
    async def test_add_buy_limit_order(self):
        """Test adding (tracking) a buy limit order."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_order_manager = AsyncMock()
        
        manager = PendingOrderManager(
            mt5_client=mock_mt5,
            order_manager=mock_order_manager,
            kill_zone_checker=None
        )
        
        result = await manager.add_order(
            ticket=67890,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            expiration_minutes=60
        )
        
        assert result.ticket == 67890
        assert result.symbol == "EURUSD"
        assert result.order_type == "buy_limit"
        assert result.is_active == True
        assert result.status == PendingOrderStatus.ACTIVE
        assert 67890 in manager.pending_orders
    
    @pytest.mark.asyncio
    async def test_add_sell_stop_order(self):
        """Test adding (tracking) a sell stop order."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=AsyncMock(),
            kill_zone_checker=None
        )
        
        result = await manager.add_order(
            ticket=67891,
            symbol="GBPUSD",
            order_type="sell_stop",
            direction="short",
            volume=0.03,
            price=1.2400,
            stop_loss=1.2500,
            take_profit=1.2300,
            expiration_minutes=120
        )
        
        assert result.ticket == 67891
        assert result.symbol == "GBPUSD"
        assert result.order_type == "sell_stop"
        assert result.is_active == True
    
    @pytest.mark.asyncio
    async def test_cancel_pending_order(self):
        """Test cancelling a pending order."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_order_manager = AsyncMock()
        mock_order_manager.cancel_order.return_value = MagicMock(
            success=True,
            message="Order cancelled"
        )
        
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=mock_order_manager,
            kill_zone_checker=None
        )
        
        # First add an order to track
        await manager.add_order(
            ticket=67890,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            expiration_minutes=60
        )
        
        result = await manager.cancel_order(67890)
        
        assert result == True
        assert 67890 not in manager.pending_orders
    
    @pytest.mark.asyncio
    async def test_check_expirations_finds_expired_orders(self):
        """Test that expired orders are detected."""
        from trading_bot.services.pending_order_manager import (
            PendingOrderManager, PendingOrder, PendingOrderStatus
        )
        
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=AsyncMock(),
            kill_zone_checker=None
        )
        
        # Add an expired order using PendingOrder dataclass
        expired_order = PendingOrder(
            ticket=11111,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expiration=datetime.now(timezone.utc) - timedelta(minutes=5),  # Expired
            status=PendingOrderStatus.ACTIVE
        )
        manager.pending_orders[11111] = expired_order
        
        # Add a valid order
        valid_order = PendingOrder(
            ticket=22222,
            symbol="GBPUSD",
            order_type="sell_limit",
            direction="short",
            volume=0.03,
            price=1.2500,
            stop_loss=1.2600,
            take_profit=1.2400,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),  # Still valid
            status=PendingOrderStatus.ACTIVE
        )
        manager.pending_orders[22222] = valid_order
        
        expired = await manager.check_expirations()
        
        # Expired order should be detected
        assert 11111 in expired
        # Valid order should not be in expired list
        assert 22222 not in expired
    
    @pytest.mark.asyncio
    async def test_get_active_orders(self):
        """Test retrieving all active pending orders."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=AsyncMock(),
            kill_zone_checker=None
        )
        
        # Add some orders
        await manager.add_order(
            ticket=11111, symbol="EURUSD", order_type="buy_limit",
            direction="long", volume=0.05, price=1.0800
        )
        await manager.add_order(
            ticket=22222, symbol="GBPUSD", order_type="sell_stop",
            direction="short", volume=0.03, price=1.2400
        )
        
        orders = manager.get_active_orders()
        
        assert len(orders) == 2
        assert any(o.ticket == 11111 for o in orders)
        assert any(o.ticket == 22222 for o in orders)
    
    @pytest.mark.asyncio
    async def test_get_active_orders_by_symbol(self):
        """Test filtering active pending orders by symbol."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=AsyncMock(),
            kill_zone_checker=None
        )
        
        await manager.add_order(
            ticket=11111, symbol="EURUSD", order_type="buy_limit",
            direction="long", volume=0.05, price=1.0800
        )
        await manager.add_order(
            ticket=22222, symbol="EURUSD", order_type="sell_limit",
            direction="short", volume=0.03, price=1.0900
        )
        await manager.add_order(
            ticket=33333, symbol="GBPUSD", order_type="buy_stop",
            direction="long", volume=0.02, price=1.2600
        )
        
        eur_orders = manager.get_active_orders(symbol="EURUSD")
        
        assert len(eur_orders) == 2
        assert all(o.symbol == "EURUSD" for o in eur_orders)


class TestOrderTypeProperties:
    """Tests for PendingOrder properties and order types."""
    
    def test_pending_order_is_active_property(self):
        """Test PendingOrder is_active property."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        
        order = PendingOrder(
            ticket=12345,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE
        )
        
        assert order.is_active == True
        
        # After cancellation
        order.status = PendingOrderStatus.CANCELLED
        assert order.is_active == False
    
    def test_pending_order_is_expired_property(self):
        """Test PendingOrder is_expired property."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        
        # Active order with past expiration => expired
        order = PendingOrder(
            ticket=12345,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expiration=datetime.now(timezone.utc) - timedelta(minutes=5),
            status=PendingOrderStatus.ACTIVE
        )
        
        assert order.is_expired == True
    
    def test_buy_limit_order_type(self):
        """Test buy limit order creation and properties."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        
        order = PendingOrder(
            ticket=12345,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,  # Below current price for buy limit
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        
        assert order.order_type == "buy_limit"
        assert order.direction == "long"
    
    def test_sell_stop_order_type(self):
        """Test sell stop order has correct properties."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        
        order = PendingOrder(
            ticket=12346,
            symbol="GBPUSD",
            order_type="sell_stop",
            direction="short",
            volume=0.03,
            price=1.0800,  # Below current for sell stop
            stop_loss=1.0900,
            take_profit=1.0700,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        
        assert order.order_type == "sell_stop"
        assert order.direction == "short"
        
        # Sell stop price should be below current (simulated check)
        current_price = 1.0850
        assert order.price < current_price  # Sell stop enters below


class TestExpirationHandling:
    """Tests for order expiration handling."""
    
    def test_time_remaining_calculation(self):
        """Test time remaining calculation for pending orders."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        
        order = PendingOrder(
            ticket=12345,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(minutes=60)
        )
        
        # Should have about 60 minutes remaining (with some tolerance)
        assert order.minutes_remaining > 58
        assert order.minutes_remaining <= 60
    
    @pytest.mark.asyncio
    async def test_expiration_uses_kill_zone_if_available(self):
        """Test expiration uses kill zone remaining time when available."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_kz_checker = MagicMock()
        # Use MagicMock with attributes (not a dict) because source code uses
        # getattr(session, 'minutes_remaining', 0) which expects a dataclass/object
        mock_session = MagicMock()
        mock_session.active = True
        mock_session.name = "London Open"
        mock_session.minutes_remaining = 45
        mock_kz_checker.get_current_session.return_value = mock_session
        
        manager = PendingOrderManager(
            mt5_client=AsyncMock(),
            order_manager=AsyncMock(),
            kill_zone_checker=mock_kz_checker
        )
        
        order = await manager.add_order(
            ticket=12345,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            expiration_minutes=120  # 120 min default, but KZ has 45 min
        )
        
        # Should use the smaller of kill zone remaining (45) and expiration_minutes (120)
        assert order.minutes_remaining <= 46  # Allow 1 min tolerance
    
    def test_to_dict_serialization(self):
        """Test PendingOrder serialization to dict."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        
        order = PendingOrder(
            ticket=12345,
            symbol="EURUSD",
            order_type="buy_limit",
            direction="long",
            volume=0.05,
            price=1.0800,
            stop_loss=1.0750,
            take_profit=1.0900,
            created_at=datetime.now(timezone.utc),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        
        data = order.to_dict()
        
        assert data["ticket"] == 12345
        assert data["symbol"] == "EURUSD"
        assert data["order_type"] == "buy_limit"
        assert data["direction"] == "long"
        assert data["volume"] == 0.05
        assert data["price"] == 1.0800
        assert "expiration" in data
        assert "minutes_remaining" in data


class TestSyncWithMT5:
    """Tests for sync_with_mt5() including filled-then-closed detection."""
    
    def _make_order(self, ticket=12345, symbol="BTCUSD", price=67500.0):
        """Helper to create a PendingOrder for testing."""
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        return PendingOrder(
            ticket=ticket,
            symbol=symbol,
            order_type="sell_limit",
            direction="short",
            volume=0.01,
            price=price,
            stop_loss=price + 100,
            take_profit=price - 500,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE
        )
    
    @pytest.mark.asyncio
    async def test_sync_order_still_pending(self):
        """Order still in MT5 pending list -> stays active."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = [{'ticket': 12345}]
        mock_mt5.get_positions.return_value = []
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager.pending_orders[12345] = self._make_order(12345)
        
        result = await manager.sync_with_mt5()
        
        assert result['active'] == 1
        assert result['filled'] == 0
        assert result['cancelled'] == 0
        assert 12345 in manager.pending_orders
    
    @pytest.mark.asyncio
    async def test_sync_order_filled_position_open(self):
        """Order filled and position still open -> marked as filled."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []  # No longer pending
        
        # Position is open with matching symbol and price
        mock_position = MagicMock()
        mock_position.ticket = 99999  # Different ticket
        mock_position.symbol = "BTCUSD"
        mock_position.price_open = 67500.0
        mock_mt5.get_positions.return_value = [mock_position]
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager.pending_orders[12345] = self._make_order(12345)
        
        result = await manager.sync_with_mt5()
        
        assert result['filled'] == 1
        assert 12345 not in manager.pending_orders
        assert len(manager.order_history) == 1
        assert manager.order_history[0].status == PendingOrderStatus.FILLED
    
    @pytest.mark.asyncio
    async def test_sync_order_filled_then_closed_detected_via_deals(self):
        """Order filled then position closed (SL/TP) before sync -> detected via deal history."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []  # No longer pending
        mock_mt5.get_positions.return_value = []  # No open position
        
        # Deal history shows the order filled then closed
        mock_mt5.get_history.return_value = [
            {
                'ticket': 90001,
                'order': 12345,  # Matches our pending order ticket
                'entry': 0,  # IN (opening deal)
                'position_id': 55555,
                'symbol': 'BTCUSD',
                'price': 67500.0,
                'volume': 0.01,
                'profit': 0.0,
                'commission': -0.5,
                'swap': 0.0,
                'time': datetime.now(timezone.utc) - timedelta(minutes=20),
                'type': 'sell',
            },
            {
                'ticket': 90002,
                'order': 88888,  # Different order ticket for close
                'entry': 1,  # OUT (closing deal)
                'position_id': 55555,  # Same position_id
                'symbol': 'BTCUSD',
                'price': 67600.0,  # Closed at SL
                'volume': 0.01,
                'profit': -10.0,
                'commission': -0.5,
                'swap': 0.0,
                'time': datetime.now(timezone.utc) - timedelta(minutes=10),
                'type': 'buy',
            },
        ]
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager.pending_orders[12345] = self._make_order(12345)
        
        result = await manager.sync_with_mt5()
        
        assert result['filled_closed'] == 1
        assert result['cancelled'] == 0
        assert 12345 not in manager.pending_orders
        assert len(manager.order_history) == 1
        assert manager.order_history[0].status == PendingOrderStatus.FILLED
        assert manager.order_history[0].fill_price == 67500.0
        
        events = result.get('closed_trade_events', [])
        assert len(events) == 1
        assert events[0].order_ticket == 12345
        assert events[0].exit_price == 67600.0
        assert events[0].profit_loss == -10.0 + (-0.5) + (-0.5) + 0.0
    
    @pytest.mark.asyncio
    async def test_sync_order_truly_cancelled(self):
        """Order truly cancelled externally -> marked as cancelled."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []
        mock_mt5.get_positions.return_value = []
        mock_mt5.get_history.return_value = []  # No deals at all
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager.pending_orders[12345] = self._make_order(12345)
        
        result = await manager.sync_with_mt5()
        
        assert result['cancelled'] == 1
        assert result['filled'] == 0
        assert result['filled_closed'] == 0
        assert 12345 not in manager.pending_orders
        assert manager.order_history[0].status == PendingOrderStatus.CANCELLED
        assert manager.order_history[0].cancel_reason == "external"
    
    @pytest.mark.asyncio
    async def test_sync_order_cancelled_deals_exist_but_no_match(self):
        """Deals exist for the symbol but none match our order -> cancelled."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []
        mock_mt5.get_positions.return_value = []
        
        # Deals exist but for a different order ticket
        mock_mt5.get_history.return_value = [
            {
                'ticket': 90001,
                'order': 99999,  # Different order, not ours
                'entry': 0,
                'position_id': 55555,
                'symbol': 'BTCUSD',
                'price': 67000.0,
                'volume': 0.02,
                'profit': 0.0,
                'commission': 0.0,
                'swap': 0.0,
                'time': datetime.now(timezone.utc) - timedelta(minutes=20),
                'type': 'sell',
            },
        ]
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager.pending_orders[12345] = self._make_order(12345)
        
        result = await manager.sync_with_mt5()
        
        assert result['cancelled'] == 1
        assert result['filled_closed'] == 0
    
    @pytest.mark.asyncio
    async def test_sync_filled_but_not_yet_closed(self):
        """Order filled (deal exists) but no closing deal -> treated as filled open position."""
        from trading_bot.services.pending_order_manager import PendingOrderManager, PendingOrderStatus
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_orders.return_value = []
        mock_mt5.get_positions.return_value = []  # Position not found (maybe different ticket format)
        
        # Opening deal exists but no closing deal
        mock_mt5.get_history.return_value = [
            {
                'ticket': 90001,
                'order': 12345,
                'entry': 0,  # IN
                'position_id': 55555,
                'symbol': 'BTCUSD',
                'price': 67500.0,
                'volume': 0.01,
                'profit': 0.0,
                'commission': -0.5,
                'swap': 0.0,
                'time': datetime.now(timezone.utc) - timedelta(minutes=5),
                'type': 'sell',
            },
        ]
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager.pending_orders[12345] = self._make_order(12345)
        
        # Mock DB update
        manager._update_trade_db_for_filled_closed = AsyncMock()
        
        result = await manager.sync_with_mt5()
        
        # Should be detected as filled-closed path (with closed=False in deal_result)
        # The order is removed from pending and marked filled
        assert result['filled_closed'] == 1
        assert 12345 not in manager.pending_orders
        assert manager.order_history[0].status == PendingOrderStatus.FILLED
    
    @pytest.mark.asyncio
    async def test_sync_multiple_orders_mixed_scenarios(self):
        """Multiple orders with different outcomes in a single sync."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_mt5 = AsyncMock()
        # Order 11111 still pending
        mock_mt5.get_orders.return_value = [{'ticket': 11111}]
        # Order 22222 filled and position open
        mock_pos = MagicMock()
        mock_pos.ticket = 22222
        mock_pos.symbol = "XRPUSD"
        mock_pos.price_open = 1.4685
        mock_mt5.get_positions.return_value = [mock_pos]
        
        # Order 33333 filled then closed (deal history)
        # Order 44444 truly cancelled (no deals)
        def mock_get_history(start, end, symbol=None):
            if symbol == "BTCUSD":
                return [
                    {'ticket': 90001, 'order': 33333, 'entry': 0,
                     'position_id': 55555, 'symbol': 'BTCUSD', 'price': 67500.0,
                     'volume': 0.01, 'profit': 0.0, 'commission': 0.0, 'swap': 0.0,
                     'time': datetime.now(timezone.utc), 'type': 'sell'},
                    {'ticket': 90002, 'order': 88888, 'entry': 1,
                     'position_id': 55555, 'symbol': 'BTCUSD', 'price': 67600.0,
                     'volume': 0.01, 'profit': -10.0, 'commission': 0.0, 'swap': 0.0,
                     'time': datetime.now(timezone.utc), 'type': 'buy'},
                ]
            return []  # No deals for EURUSD (order 44444)
        
        mock_mt5.get_history = AsyncMock(side_effect=mock_get_history)
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        manager._update_trade_db_for_filled_closed = AsyncMock()
        
        manager.pending_orders[11111] = self._make_order(11111, "EURUSD", 1.0800)
        manager.pending_orders[22222] = self._make_order(22222, "XRPUSD", 1.4685)
        manager.pending_orders[33333] = self._make_order(33333, "BTCUSD", 67500.0)
        manager.pending_orders[44444] = self._make_order(44444, "EURUSD", 1.0900)
        
        result = await manager.sync_with_mt5()
        
        assert result['active'] == 1       # 11111
        assert result['filled'] == 1       # 22222
        assert result['filled_closed'] == 1  # 33333
        assert result['cancelled'] == 1    # 44444


class TestCheckDealHistoryForOrder:
    """Tests for _check_deal_history_for_order helper."""
    
    def _make_order(self, ticket=12345, symbol="BTCUSD", price=67500.0):
        from trading_bot.services.pending_order_manager import PendingOrder, PendingOrderStatus
        return PendingOrder(
            ticket=ticket, symbol=symbol, order_type="sell_limit",
            direction="short", volume=0.01, price=price,
            stop_loss=price + 100, take_profit=price - 500,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
            status=PendingOrderStatus.ACTIVE
        )
    
    @pytest.mark.asyncio
    async def test_no_deals_returns_none(self):
        """No deals in history -> returns None."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_history.return_value = []
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        order = self._make_order()
        
        result = await manager._check_deal_history_for_order(12345, order)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_opening_and_closing_deal_found(self):
        """Both opening and closing deals found -> returns full result."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_mt5 = AsyncMock()
        now = datetime.now(timezone.utc)
        mock_mt5.get_history.return_value = [
            {
                'ticket': 90001, 'order': 12345, 'entry': 0,
                'position_id': 55555, 'symbol': 'BTCUSD',
                'price': 67500.0, 'volume': 0.01,
                'profit': 0.0, 'commission': -0.5, 'swap': 0.0,
                'time': now - timedelta(minutes=20), 'type': 'sell',
            },
            {
                'ticket': 90002, 'order': 88888, 'entry': 1,
                'position_id': 55555, 'symbol': 'BTCUSD',
                'price': 67600.0, 'volume': 0.01,
                'profit': -10.0, 'commission': -0.5, 'swap': 0.0,
                'time': now - timedelta(minutes=10), 'type': 'buy',
            },
        ]
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        order = self._make_order()
        
        result = await manager._check_deal_history_for_order(12345, order)
        
        assert result is not None
        assert result['filled'] == True
        assert result['closed'] == True
        assert result['fill_price'] == 67500.0
        assert result['close_price'] == 67600.0
        assert result['total_pnl'] == -10.0 + (-0.5) + (-0.5) + 0.0
        assert result['position_id'] == 55555
    
    @pytest.mark.asyncio
    async def test_only_opening_deal_found(self):
        """Opening deal found but no closing -> filled but not closed."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_history.return_value = [
            {
                'ticket': 90001, 'order': 12345, 'entry': 0,
                'position_id': 55555, 'symbol': 'BTCUSD',
                'price': 67500.0, 'volume': 0.01,
                'profit': 0.0, 'commission': -0.5, 'swap': 0.0,
                'time': datetime.now(timezone.utc), 'type': 'sell',
            },
        ]
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        order = self._make_order()
        
        result = await manager._check_deal_history_for_order(12345, order)
        
        assert result is not None
        assert result['filled'] == True
        assert result['closed'] == False
    
    @pytest.mark.asyncio
    async def test_deal_history_error_returns_none(self):
        """Exception in get_history -> returns None gracefully."""
        from trading_bot.services.pending_order_manager import PendingOrderManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_history.side_effect = Exception("MT5 connection lost")
        
        manager = PendingOrderManager(mt5_client=mock_mt5)
        order = self._make_order()
        
        result = await manager._check_deal_history_for_order(12345, order)
        assert result is None


# Fixtures
@pytest.fixture
def mock_mt5_client():
    """Create mock MT5 client."""
    client = AsyncMock()
    client.get_symbol_info.return_value = {
        "bid": 1.0850,
        "ask": 1.0852,
        "volume_min": 0.01,
        "volume_max": 100.0
    }
    return client


@pytest.fixture
def mock_order_manager():
    """Create mock order manager."""
    manager = AsyncMock()
    manager.cancel_order.return_value = MagicMock(
        success=True,
        message="Order cancelled"
    )
    return manager
