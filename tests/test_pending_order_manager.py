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
from datetime import datetime, timedelta
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
            created_at=datetime.now() - timedelta(hours=2),
            expiration=datetime.now() - timedelta(minutes=5),  # Expired
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
            created_at=datetime.now(),
            expiration=datetime.now() + timedelta(hours=1),  # Still valid
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
            created_at=datetime.now(),
            expiration=datetime.now() + timedelta(hours=1),
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
            created_at=datetime.now() - timedelta(hours=2),
            expiration=datetime.now() - timedelta(minutes=5),
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
            created_at=datetime.now(),
            expiration=datetime.now() + timedelta(hours=1)
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
            created_at=datetime.now(),
            expiration=datetime.now() + timedelta(hours=1)
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
            created_at=datetime.now(),
            expiration=datetime.now() + timedelta(minutes=60)
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
            created_at=datetime.now(),
            expiration=datetime.now() + timedelta(hours=1)
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
