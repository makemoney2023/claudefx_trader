"""
Pending Order Manager Service.

Tracks and manages pending orders (limit/stop orders) with:
- Expiration handling tied to kill zone endings
- Sync with MT5 to detect filled/cancelled orders
- Automatic cancellation of expired orders
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from enum import Enum

from ..utils.logging import get_logger

logger = get_logger(__name__)


class PendingOrderStatus(Enum):
    """Status of a pending order."""
    ACTIVE = "active"           # Order is live in MT5
    FILLED = "filled"           # Order was executed
    CANCELLED = "cancelled"     # Order was cancelled
    EXPIRED = "expired"         # Order expired without being filled
    UNKNOWN = "unknown"         # Status not yet determined


@dataclass
class PendingOrder:
    """Represents a tracked pending order."""
    ticket: int
    symbol: str
    order_type: str  # buy_limit, sell_limit, buy_stop, sell_stop
    direction: str   # long or short
    volume: float
    price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    created_at: datetime
    expiration: datetime
    status: PendingOrderStatus = PendingOrderStatus.ACTIVE
    
    # Tracking fields
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    
    @property
    def is_active(self) -> bool:
        return self.status == PendingOrderStatus.ACTIVE
    
    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.expiration and self.status == PendingOrderStatus.ACTIVE
    
    @property
    def time_remaining(self) -> timedelta:
        if self.status != PendingOrderStatus.ACTIVE:
            return timedelta(0)
        remaining = self.expiration - datetime.now()
        return max(remaining, timedelta(0))
    
    @property
    def minutes_remaining(self) -> float:
        return self.time_remaining.total_seconds() / 60
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "order_type": self.order_type,
            "direction": self.direction,
            "volume": self.volume,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "created_at": self.created_at.isoformat(),
            "expiration": self.expiration.isoformat(),
            "status": self.status.value,
            "minutes_remaining": self.minutes_remaining,
            "fill_price": self.fill_price,
            "fill_time": self.fill_time.isoformat() if self.fill_time else None,
            "cancel_reason": self.cancel_reason
        }


class PendingOrderManager:
    """
    Manages pending orders with expiration and sync capabilities.
    
    Features:
    - Track all pending orders placed by the bot
    - Automatic expiration based on kill zone timing
    - Sync with MT5 to detect filled/cancelled orders
    - Cancel expired orders
    """
    
    def __init__(self, mt5_client, order_manager=None, kill_zone_checker=None):
        """
        Initialize the Pending Order Manager.
        
        Args:
            mt5_client: MT5 client for order operations
            order_manager: OrderManager for cancellation
            kill_zone_checker: KillZoneChecker for session-based expiration
        """
        self.mt5_client = mt5_client
        self.order_manager = order_manager
        self.kill_zone_checker = kill_zone_checker
        
        # Track pending orders by ticket
        self.pending_orders: Dict[int, PendingOrder] = {}
        
        # History of completed orders
        self.order_history: List[PendingOrder] = []
        
        logger.info("PendingOrderManager initialized")
    
    def set_order_manager(self, order_manager):
        """Set the order manager."""
        self.order_manager = order_manager
    
    def set_kill_zone_checker(self, checker):
        """Set the kill zone checker."""
        self.kill_zone_checker = checker
    
    async def add_order(
        self,
        ticket: int,
        symbol: str,
        order_type: str,
        direction: str,
        volume: float,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        expiration_minutes: int = 120
    ) -> PendingOrder:
        """
        Add a pending order to track.
        
        Args:
            ticket: MT5 order ticket
            symbol: Trading symbol
            order_type: buy_limit, sell_limit, buy_stop, sell_stop
            direction: long or short
            volume: Position size in lots
            price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            expiration_minutes: Minutes until expiration
            
        Returns:
            PendingOrder object
        """
        # Calculate expiration time
        # Crypto symbols trade 24/7 - always use full expiration, don't shorten by session
        CRYPTO_SYMBOLS = {'BTCUSD', 'ETHUSD', 'XRPUSD', 'ADAUSD', 'SOLUSD', 'DOGEUSD'}
        is_crypto = symbol in CRYPTO_SYMBOLS
        
        if not is_crypto and self.kill_zone_checker:
            try:
                session = self.kill_zone_checker.get_current_session()
                # SessionInfo is a dataclass — use getattr, not .get()
                session_remaining = getattr(session, 'minutes_remaining', 0) if session else 0
                if session_remaining > 30:  # Only shorten if session has meaningful time left
                    expiration = datetime.now() + timedelta(minutes=min(session_remaining, expiration_minutes))
                else:
                    # Session ending soon — use full expiration to survive into next session
                    expiration = datetime.now() + timedelta(minutes=expiration_minutes)
            except Exception as e:
                logger.warning(f"Could not get session for expiration: {e}")
                expiration = datetime.now() + timedelta(minutes=expiration_minutes)
        else:
            expiration = datetime.now() + timedelta(minutes=expiration_minutes)
        
        order = PendingOrder(
            ticket=ticket,
            symbol=symbol,
            order_type=order_type,
            direction=direction,
            volume=volume,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            created_at=datetime.now(),
            expiration=expiration,
            status=PendingOrderStatus.ACTIVE
        )
        
        self.pending_orders[ticket] = order
        logger.info(
            f"Tracking pending order: {order_type} {symbol} @ {price}, "
            f"expires in {order.minutes_remaining:.0f}min"
        )
        
        return order
    
    async def check_expirations(self) -> List[int]:
        """
        Check for expired orders.
        
        Returns:
            List of expired ticket numbers
        """
        expired_tickets = []
        
        for ticket, order in list(self.pending_orders.items()):
            if order.is_expired:
                expired_tickets.append(ticket)
                logger.info(f"Order {ticket} ({order.symbol}) has expired")
        
        return expired_tickets
    
    async def cancel_expired_orders(self) -> Dict[str, Any]:
        """
        Cancel all expired pending orders.
        
        Returns:
            Summary of cancellation results
        """
        expired = await self.check_expirations()
        
        if not expired:
            return {"cancelled": 0, "failed": 0, "orders": []}
        
        cancelled = []
        failed = []
        
        for ticket in expired:
            order = self.pending_orders.get(ticket)
            if order:
                print(f"[PENDING-EXPIRE] Cancelling expired order {ticket} ({order.symbol} {order.order_type} @ {order.price}, expired {order.minutes_remaining:.0f}min ago)", flush=True)
            result = await self.cancel_order(ticket, reason="expired")
            if result:
                cancelled.append(ticket)
            else:
                failed.append(ticket)
        
        logger.info(f"Expired orders processed: {len(cancelled)} cancelled, {len(failed)} failed")
        
        return {
            "cancelled": len(cancelled),
            "failed": len(failed),
            "orders": cancelled
        }
    
    async def cancel_order(self, ticket: int, reason: str = "manual") -> bool:
        """
        Cancel a pending order.
        
        Args:
            ticket: Order ticket to cancel
            reason: Reason for cancellation
            
        Returns:
            True if cancelled successfully
        """
        if ticket not in self.pending_orders:
            logger.warning(f"Order {ticket} not found in pending orders")
            return False
        
        order = self.pending_orders[ticket]
        
        if not order.is_active:
            logger.info(f"Order {ticket} is not active (status: {order.status.value})")
            return True  # Already not active
        
        try:
            # Cancel in MT5 if order manager available
            if self.order_manager:
                result = await self.order_manager.cancel_order(ticket)
                if not result.success:
                    print(f"[PENDING-CANCEL] MT5 cancel failed for #{ticket}: {result.message}", flush=True)
                    logger.error(f"Failed to cancel order {ticket}: {result.message}")
                    return False
            else:
                print(f"[PENDING-CANCEL] No order_manager — cannot cancel #{ticket} on MT5", flush=True)
                return False
            
            # Update local tracking
            order.status = PendingOrderStatus.CANCELLED if reason != "expired" else PendingOrderStatus.EXPIRED
            order.cancel_reason = reason
            
            # Move to history
            self.order_history.append(order)
            del self.pending_orders[ticket]
            
            logger.info(f"Order {ticket} cancelled: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Error cancelling order {ticket}: {e}")
            return False
    
    async def sync_with_mt5(self) -> Dict[str, Any]:
        """
        Sync pending orders with MT5 to detect filled/cancelled orders.
        
        Returns:
            Sync results with filled, cancelled, and active counts
        """
        if not self.mt5_client:
            return {"error": "MT5 client not available"}
        
        try:
            # Get current orders from MT5
            mt5_orders = await self.mt5_client.get_orders()
            mt5_tickets = {o.get('ticket') for o in mt5_orders} if mt5_orders else set()
            
            # Get current positions (filled orders become positions)
            mt5_positions = await self.mt5_client.get_positions()
            
            filled = []
            cancelled = []
            still_active = []
            
            for ticket, order in list(self.pending_orders.items()):
                if not order.is_active:
                    continue
                
                if ticket in mt5_tickets:
                    # Order still pending in MT5
                    still_active.append(ticket)
                else:
                    # Order no longer in MT5 - check if it became a position
                    position_found = any(
                        p.ticket == ticket or 
                        (p.symbol == order.symbol and 
                         abs(p.price_open - order.price) < 0.0001)
                        for p in mt5_positions
                    )
                    
                    if position_found:
                        # Order was filled
                        order.status = PendingOrderStatus.FILLED
                        order.fill_time = datetime.now()
                        # Try to get fill price from position
                        for p in mt5_positions:
                            if p.symbol == order.symbol:
                                order.fill_price = p.price_open
                                break
                        
                        filled.append(ticket)
                        self.order_history.append(order)
                        del self.pending_orders[ticket]
                        logger.info(f"Order {ticket} filled at {order.fill_price}")
                    else:
                        # Order was cancelled externally
                        order.status = PendingOrderStatus.CANCELLED
                        order.cancel_reason = "external"
                        
                        cancelled.append(ticket)
                        self.order_history.append(order)
                        del self.pending_orders[ticket]
                        print(f"[PENDING-SYNC] Order {ticket} ({order.symbol}) not found in MT5 orders or positions — marked as externally cancelled", flush=True)
                        logger.info(f"Order {ticket} was cancelled externally")
            
            return {
                "filled": len(filled),
                "cancelled": len(cancelled),
                "active": len(still_active),
                "filled_tickets": filled,
                "cancelled_tickets": cancelled
            }
            
        except Exception as e:
            logger.error(f"Error syncing with MT5: {e}")
            return {"error": str(e)}
    
    async def import_from_mt5(self) -> Dict[str, Any]:
        """
        Import existing MT5 pending orders into the tracker on startup.
        
        This ensures orders placed in previous sessions survive restarts
        and can be re-evaluated by the pending order re-eval logic.
        
        Returns:
            Summary with count of imported orders
        """
        if not self.mt5_client:
            return {"imported": 0, "error": "MT5 client not available"}
        
        try:
            mt5_orders = await self.mt5_client.get_orders()
            if not mt5_orders:
                logger.info("No MT5 pending orders to import")
                return {"imported": 0}
            
            # MT5 order types: 2=buy_limit, 3=sell_limit, 4=buy_stop, 5=sell_stop
            _type_map = {2: 'buy_limit', 3: 'sell_limit', 4: 'buy_stop', 5: 'sell_stop'}
            _dir_map = {2: 'long', 3: 'short', 4: 'long', 5: 'short'}
            
            imported = 0
            skipped = 0
            for o in mt5_orders:
                ticket = o.get('ticket')
                if not ticket or ticket in self.pending_orders:
                    continue  # Already tracked
                
                # Only import orders placed by this bot (comment contains "ICT_Bot")
                comment = o.get('comment', '')
                if 'ICT_Bot' not in comment:
                    skipped += 1
                    continue
                
                order_type_int = o.get('type', -1)
                if order_type_int not in _type_map:
                    continue  # Not a pending order type
                
                # Parse creation time
                # MT5 time_setup is a UTC epoch — but the broker server clock may
                # differ from local time, so clamp to no later than now.
                time_setup = o.get('time_setup')
                if time_setup:
                    try:
                        created_at = datetime.fromtimestamp(time_setup)
                        # Clamp: if MT5 server is ahead of local clock, use now()
                        if created_at > datetime.now():
                            created_at = datetime.now()
                    except Exception:
                        created_at = datetime.now()
                else:
                    created_at = datetime.now()
                
                # Default expiration: 8 hours from creation (max kill-zone window)
                default_expiry = created_at + timedelta(hours=8)
                # If already past that, set expiry to 2 hours from now
                # so Claude re-eval has a chance to evaluate before they auto-expire
                if default_expiry < datetime.now():
                    default_expiry = datetime.now() + timedelta(hours=2)
                
                order = PendingOrder(
                    ticket=ticket,
                    symbol=o.get('symbol', ''),
                    order_type=_type_map[order_type_int],
                    direction=_dir_map[order_type_int],
                    volume=o.get('volume', 0.01),
                    price=o.get('price_open', 0.0),
                    stop_loss=o.get('sl'),
                    take_profit=o.get('tp'),
                    created_at=created_at,
                    expiration=default_expiry,
                    status=PendingOrderStatus.ACTIVE
                )
                
                self.pending_orders[ticket] = order
                imported += 1
                print(
                    f"[PENDING-IMPORT] #{ticket} {order.symbol} {order.order_type} "
                    f"@ {order.price} (age: {(datetime.now() - created_at).total_seconds()/60:.0f}min, "
                    f"expires in {order.minutes_remaining:.0f}min)",
                    flush=True
                )
            
            if skipped > 0:
                logger.info(f"Skipped {skipped} non-bot pending orders (no ICT_Bot comment)")
            logger.info(f"Imported {imported} pending orders from MT5")
            return {"imported": imported, "skipped": skipped}
            
        except Exception as e:
            logger.error(f"Error importing orders from MT5: {e}")
            return {"imported": 0, "error": str(e)}
    
    def get_active_orders(self, symbol: Optional[str] = None) -> List[PendingOrder]:
        """Get all active pending orders."""
        orders = [o for o in self.pending_orders.values() if o.is_active]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders
    
    def get_order(self, ticket: int) -> Optional[PendingOrder]:
        """Get a specific order by ticket."""
        return self.pending_orders.get(ticket)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of pending orders."""
        active = [o for o in self.pending_orders.values() if o.is_active]
        expiring_soon = [o for o in active if o.minutes_remaining < 30]
        
        return {
            "total_active": len(active),
            "expiring_soon": len(expiring_soon),
            "total_tracked": len(self.pending_orders),
            "history_count": len(self.order_history),
            "by_symbol": self._group_by_symbol(active),
            "orders": [o.to_dict() for o in active]
        }
    
    def _group_by_symbol(self, orders: List[PendingOrder]) -> Dict[str, int]:
        """Group orders by symbol."""
        result = {}
        for order in orders:
            result[order.symbol] = result.get(order.symbol, 0) + 1
        return result
    
    async def cancel_all_for_symbol(self, symbol: str, reason: str = "symbol_cancel") -> int:
        """Cancel all pending orders for a symbol."""
        cancelled = 0
        for ticket, order in list(self.pending_orders.items()):
            if order.symbol == symbol and order.is_active:
                if await self.cancel_order(ticket, reason):
                    cancelled += 1
        return cancelled
    
    async def cancel_all(self, reason: str = "cancel_all") -> int:
        """Cancel all pending orders."""
        cancelled = 0
        for ticket in list(self.pending_orders.keys()):
            if self.pending_orders[ticket].is_active:
                if await self.cancel_order(ticket, reason):
                    cancelled += 1
        return cancelled
