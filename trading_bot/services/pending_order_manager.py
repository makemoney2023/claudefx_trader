"""
Pending Order Manager Service.

Tracks and manages pending orders (limit/stop orders) with:
- Expiration handling tied to kill zone endings
- Sync with MT5 to detect filled/cancelled orders
- Automatic cancellation of expired orders
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone
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
    
    # Risk tracking (for accurate daily risk reclaim on cancel)
    risk_percent: Optional[float] = None
    
    # Tracking fields
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    
    @property
    def is_active(self) -> bool:
        return self.status == PendingOrderStatus.ACTIVE
    
    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expiration and self.status == PendingOrderStatus.ACTIVE
    
    @property
    def time_remaining(self) -> timedelta:
        if self.status != PendingOrderStatus.ACTIVE:
            return timedelta(0)
        remaining = self.expiration - datetime.now(timezone.utc)
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

        # Maps position ticket -> original order ticket for filled pending orders.
        # Consumed by position_manager to set order_ticket on Position objects.
        self.filled_order_map: Dict[int, int] = {}
        
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
        expiration_minutes: int = 120,
        risk_percent: Optional[float] = None
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
                    expiration = datetime.now(timezone.utc) + timedelta(minutes=min(session_remaining, expiration_minutes))
                else:
                    # Session ending soon — use full expiration to survive into next session
                    expiration = datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
            except Exception as e:
                logger.warning(f"Could not get session for expiration: {e}")
                expiration = datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
        else:
            expiration = datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
        
        order = PendingOrder(
            ticket=ticket,
            symbol=symbol,
            order_type=order_type,
            direction=direction,
            volume=volume,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            created_at=datetime.now(timezone.utc),
            expiration=expiration,
            status=PendingOrderStatus.ACTIVE,
            risk_percent=risk_percent,
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
        
        Checks three sources in order:
        1. Current pending orders (still active)
        2. Current open positions (filled and still open)
        3. Deal history (filled then already closed -- e.g. hit SL/TP fast)
        
        Returns:
            Sync results with filled, cancelled, filled_closed, and active counts
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
            filled_closed = []
            cancelled = []
            still_active = []
            
            for ticket, order in list(self.pending_orders.items()):
                if not order.is_active:
                    continue
                
                if ticket in mt5_tickets:
                    # Order still pending in MT5
                    still_active.append(ticket)
                    continue
                
                # Order no longer in MT5 pending list -- check if it became a position
                # Priority 1: MT5 links positions to originating orders via the 'order' field
                # Priority 2: Match by symbol + percentage-based price tolerance (0.1%)
                position_found = False
                for p in mt5_positions:
                    if getattr(p, 'ticket', None) == ticket:
                        position_found = True
                        break
                    mt5_order_link = getattr(p, 'identifier', None) or getattr(p, 'order', None)
                    if mt5_order_link and mt5_order_link == ticket:
                        position_found = True
                        break
                    if p.symbol == order.symbol and order.price > 0:
                        price_tol = abs(p.price_open - order.price) / order.price
                        if price_tol < 0.001:
                            position_found = True
                            break
                
                if position_found:
                    # Order was filled and position is still open
                    order.status = PendingOrderStatus.FILLED
                    order.fill_time = datetime.now(timezone.utc)
                    for p in mt5_positions:
                        if p.symbol == order.symbol:
                            order.fill_price = p.price_open
                            pos_ticket = getattr(p, 'ticket', None)
                            if pos_ticket and pos_ticket != ticket:
                                self.filled_order_map[pos_ticket] = ticket
                            break
                    
                    filled.append(ticket)
                    self.order_history.append(order)
                    del self.pending_orders[ticket]
                    logger.info(f"Order {ticket} filled at {order.fill_price}")
                    continue
                
                # Not in pending orders, not in open positions.
                # Check deal history -- the order may have filled AND closed
                # (e.g. hit SL/TP before our next sync cycle)
                deal_result = await self._check_deal_history_for_order(ticket, order)
                
                if deal_result:
                    # Order filled then closed
                    order.status = PendingOrderStatus.FILLED
                    order.fill_time = deal_result.get('fill_time', datetime.now(timezone.utc))
                    order.fill_price = deal_result.get('fill_price', order.price)
                    
                    filled_closed.append(ticket)
                    self.order_history.append(order)
                    del self.pending_orders[ticket]
                    
                    pnl = deal_result.get('total_pnl', 0)
                    close_price = deal_result.get('close_price', 0)
                    print(
                        f"[PENDING-SYNC] Order {ticket} ({order.symbol}) filled then closed "
                        f"— entry: {order.fill_price}, exit: {close_price}, P/L: ${pnl:.2f} "
                        f"(detected via deal history)",
                        flush=True
                    )
                    logger.info(
                        f"Order {ticket} filled at {order.fill_price} then closed at "
                        f"{close_price} — P/L: ${pnl:.2f}"
                    )
                    
                    # Update the DB trade record with real P/L
                    await self._update_trade_db_for_filled_closed(ticket, order, deal_result)
                else:
                    # Truly cancelled externally
                    order.status = PendingOrderStatus.CANCELLED
                    order.cancel_reason = "external"
                    
                    cancelled.append(ticket)
                    self.order_history.append(order)
                    del self.pending_orders[ticket]
                    print(
                        f"[PENDING-SYNC] Order {ticket} ({order.symbol}) not found in MT5 "
                        f"orders, positions, or deal history — marked as externally cancelled",
                        flush=True
                    )
                    logger.info(f"Order {ticket} was cancelled externally")
            
            return {
                "filled": len(filled),
                "filled_closed": len(filled_closed),
                "cancelled": len(cancelled),
                "active": len(still_active),
                "filled_tickets": filled,
                "filled_closed_tickets": filled_closed,
                "cancelled_tickets": cancelled
            }
            
        except Exception as e:
            logger.error(f"Error syncing with MT5: {e}")
            return {"error": str(e)}
    
    async def _check_deal_history_for_order(
        self, ticket: int, order: 'PendingOrder'
    ) -> Optional[Dict[str, Any]]:
        """
        Check MT5 deal history to see if a pending order was filled then closed.
        
        In MT5, when a pending order fills:
        - A deal with entry=0 (IN) is created, with deal.order == original_order_ticket
        - If the position then closes (SL/TP/manual), a deal with entry=1 (OUT)
          is created with the same position_id
        
        Args:
            ticket: The original pending order ticket
            order: The PendingOrder object
            
        Returns:
            Dict with fill/close details if found, None if not filled
        """
        try:
            # Search deals from when the order was created
            start_time = order.created_at - timedelta(minutes=5)
            end_time = datetime.now(timezone.utc)
            
            deals = await self.mt5_client.get_history(
                start_time, end_time, symbol=order.symbol
            )
            
            if not deals:
                return None
            
            # Look for the opening deal: entry=0 (IN) and order ticket matches
            opening_deal = None
            for deal in deals:
                if deal.get('entry') == 0 and deal.get('order') == ticket:
                    opening_deal = deal
                    break
            
            if not opening_deal:
                return None
            
            # Found the fill. Now look for the closing deal with same position_id
            position_id = opening_deal.get('position_id')
            closing_deal = None
            
            if position_id:
                for deal in deals:
                    if (deal.get('entry') == 1 and 
                        deal.get('position_id') == position_id):
                        closing_deal = deal
                        break
            
            result = {
                'filled': True,
                'fill_price': opening_deal.get('price', order.price),
                'fill_time': opening_deal.get('time', datetime.now(timezone.utc)),
                'position_id': position_id,
                'opening_deal_ticket': opening_deal.get('ticket'),
            }
            
            if closing_deal:
                profit = float(closing_deal.get('profit', 0))
                commission = float(closing_deal.get('commission', 0))
                swap = float(closing_deal.get('swap', 0))
                # Also add commission from opening deal (some brokers split it)
                open_commission = float(opening_deal.get('commission', 0))
                
                result.update({
                    'closed': True,
                    'close_price': closing_deal.get('price', 0),
                    'close_time': closing_deal.get('time', datetime.now(timezone.utc)),
                    'profit': profit,
                    'commission': commission + open_commission,
                    'swap': swap,
                    'total_pnl': profit + commission + open_commission + swap,
                    'closing_deal_ticket': closing_deal.get('ticket'),
                })
            else:
                # Filled but no closing deal found -- position may still be open
                # under a different ticket. This shouldn't normally happen since
                # we already checked open positions, but handle gracefully.
                result['closed'] = False
            
            return result
            
        except Exception as e:
            logger.error(f"Error checking deal history for order {ticket}: {e}")
            return None
    
    async def _update_trade_db_for_filled_closed(
        self, ticket: int, order: 'PendingOrder', deal_result: Dict[str, Any]
    ) -> None:
        """
        Update the TradeModel in the DB when we detect a filled-then-closed order.
        
        This prevents the main.py trade sync from later marking it as
        cancelled with $0 P/L.
        """
        try:
            from ..api.database import async_session, TradeModel
            from sqlalchemy import select
            
            trade_id = str(ticket)
            
            async with async_session() as session:
                result = await session.execute(
                    select(TradeModel).where(TradeModel.trade_id == trade_id)
                )
                trade = result.scalar_one_or_none()
                
                if not trade:
                    logger.warning(
                        f"No TradeModel found for ticket {ticket} to update "
                        f"with filled-then-closed data"
                    )
                    return
                
                close_price = deal_result.get('close_price', 0)
                total_pnl = deal_result.get('total_pnl', 0)
                close_time = deal_result.get('close_time', datetime.now(timezone.utc))
                fill_price = deal_result.get('fill_price', order.price)
                
                trade.entry_price = fill_price
                trade.exit_price = close_price
                trade.profit_loss = total_pnl
                trade.exit_time = close_time if isinstance(close_time, datetime) else datetime.now(timezone.utc)
                trade.exit_reason = "SL/TP hit (filled-then-closed, detected via deal history)"
                
                await session.commit()
                
                print(
                    f"[PENDING-SYNC] Updated DB trade {trade_id} ({order.symbol}): "
                    f"entry={fill_price}, exit={close_price}, P/L=${total_pnl:.2f}",
                    flush=True
                )
                logger.info(
                    f"Updated TradeModel {trade_id} with filled-then-closed data: "
                    f"P/L=${total_pnl:.2f}"
                )
                
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.error(
                f"Error updating trade DB for filled-then-closed order {ticket}: {e}"
            )
            import traceback
            traceback.print_exc()
    
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
                        created_at = datetime.fromtimestamp(time_setup, tz=timezone.utc)
                        if created_at > datetime.now(timezone.utc):
                            created_at = datetime.now(timezone.utc)
                    except Exception:
                        created_at = datetime.now(timezone.utc)
                else:
                    created_at = datetime.now(timezone.utc)
                
                # Default expiration: 8 hours from creation (max kill-zone window)
                default_expiry = created_at + timedelta(hours=8)
                # If already past that, set expiry to 2 hours from now
                # so Claude re-eval has a chance to evaluate before they auto-expire
                if default_expiry < datetime.now(timezone.utc):
                    default_expiry = datetime.now(timezone.utc) + timedelta(hours=2)
                
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
                    f"@ {order.price} (age: {(datetime.now(timezone.utc) - created_at).total_seconds()/60:.0f}min, "
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
