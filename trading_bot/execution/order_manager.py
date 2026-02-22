"""
Order Management Module.

Handles order placement, modification, and cancellation
through the MT5 MCP server.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime

from ..utils.logging import get_logger

logger = get_logger(__name__)


class OrderType(Enum):
    """MT5 order types."""
    MARKET_BUY = "buy"
    MARKET_SELL = "sell"
    LIMIT_BUY = "buy_limit"
    LIMIT_SELL = "sell_limit"
    STOP_BUY = "buy_stop"
    STOP_SELL = "sell_stop"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class OrderResult:
    """Result of an order operation."""
    success: bool
    order_id: Optional[int]
    ticket: Optional[int]
    status: OrderStatus
    message: str
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "ticket": self.ticket,
            "status": self.status.value,
            "message": self.message,
            "fill_price": self.fill_price,
            "fill_time": self.fill_time.isoformat() if self.fill_time else None
        }


@dataclass
class Order:
    """Represents a trading order."""
    symbol: str
    order_type: OrderType
    volume: float
    price: Optional[float] = None  # For limit/stop orders
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    magic_number: int = 12345
    comment: str = ""
    expiration: Optional[datetime] = None


class OrderManager:
    """
    Manages order operations through MT5 MCP.
    
    Handles:
    - Market order execution
    - Limit/stop order placement
    - Order modification
    - Order cancellation
    """
    
    def __init__(self, mt5_client=None):
        """
        Initialize the order manager.
        
        Args:
            mt5_client: MT5 MCP client instance
        """
        self.mt5_client = mt5_client
        self.pending_orders: Dict[int, Order] = {}
        
        logger.info("Order manager initialized")
    
    # Maximum spread thresholds (in price units, not pips)
    # RELAXED FOR DEMO TESTING - wider spreads allowed
    MAX_SPREAD_THRESHOLDS = {
        # Forex majors: ~3-5 pips (tightened for live)
        'EURUSD': 0.0005, 'GBPUSD': 0.0005, 'AUDUSD': 0.0005,
        'NZDUSD': 0.0005, 'USDCHF': 0.0005, 'USDCAD': 0.0005,
        'USDJPY': 0.05,
        # Forex crosses: ~5-8 pips (tightened for live)
        'EURGBP': 0.0008, 'EURJPY': 0.08, 'GBPJPY': 0.08,
        'AUDJPY': 0.08, 'EURAUD': 0.0008, 'GBPAUD': 0.0008,
        'AUDCAD': 0.0008, 'AUDCHF': 0.0008, 'EURCHF': 0.0008,
        'EURCAD': 0.0008, 'EURNZD': 0.0008, 'GBPCAD': 0.0008,
        'GBPCHF': 0.0008, 'GBPNZD': 0.0008, 'NZDJPY': 0.08,
        'NZDCAD': 0.0008, 'NZDCHF': 0.0008, 'CADJPY': 0.08,
        'CADCHF': 0.0008, 'CHFJPY': 0.08,
        # Metals (tightened for live)
        'XAUUSD': 0.80,   # Gold: max $0.80 spread
        'XAGUSD': 0.08,   # Silver: max $0.08 spread
        # Oil / Energy
        'USOIL': 0.10,    # WTI Crude: max $0.10 spread
        'WTIUSD': 0.10,
        'XTIUSD': 0.10,
        'BRENT': 0.10,
        'UKOIL': 0.10,
        'XBRUSD': 0.10,
        # Indices
        'US30': 5.0,      # Dow Jones: max 5 points spread
        'DJ30': 5.0,
        'NAS100': 3.0,    # Nasdaq 100: max 3 points spread
        'USTEC': 3.0,
        'US500': 1.5,     # S&P 500: max 1.5 points spread
        'SP500': 1.5,
    }
    
    async def _check_spread(self, symbol: str) -> tuple:
        """
        Check if current spread is acceptable for trading.
        
        Returns:
            (is_acceptable, current_spread, max_spread)
        """
        if not self.mt5_client:
            return True, 0, 0  # Can't check, allow through
        
        try:
            symbol_info = await self.mt5_client.get_symbol_info(symbol)
            if not symbol_info:
                return True, 0, 0
            
            current_spread = getattr(symbol_info, 'spread', 0) or 0
            ask = getattr(symbol_info, 'ask', 0) or 0
            bid = getattr(symbol_info, 'bid', 0) or 0
            
            if ask and bid:
                spread_price = ask - bid
            else:
                point = getattr(symbol_info, 'point', 0.00001) or 0.00001
                spread_price = current_spread * point
            
            # Get max threshold
            symbol_upper = symbol.upper()
            max_spread = self.MAX_SPREAD_THRESHOLDS.get(symbol_upper)
            
            if max_spread is None:
                # Crypto: max 0.5% of price
                if any(c in symbol_upper for c in ['BTC', 'ETH', 'XRP', 'ADA', 'LTC', 'DOGE', 'SOL', 'DOT', 'DASH']):
                    mid_price = (ask + bid) / 2 if ask and bid else 1.0
                    max_spread = mid_price * 0.005  # 0.5%
                else:
                    max_spread = 0.0005  # Default: 5 pips
            
            is_acceptable = spread_price <= max_spread
            
            if not is_acceptable:
                logger.warning(
                    f"SPREAD TOO WIDE for {symbol}: {spread_price:.5f} > max {max_spread:.5f}"
                )
            
            return is_acceptable, spread_price, max_spread
            
        except Exception as e:
            logger.warning(f"Could not check spread for {symbol}: {e}")
            return True, 0, 999  # Allow trade if spread can't be verified (demo mode)
    
    def set_mt5_client(self, client):
        """Set the MT5 client."""
        self.mt5_client = client
    
    async def place_market_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "ICT Bot"
    ) -> OrderResult:
        """
        Place a market order.
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            volume: Position size in lots
            stop_loss: Stop loss price
            take_profit: Take profit price
            comment: Order comment
            
        Returns:
            OrderResult with execution details
        """
        if not self.mt5_client:
            return self._error_result("MT5 client not connected")
        
        try:
            order_type = OrderType.MARKET_BUY if direction == 'long' else OrderType.MARKET_SELL
            
            # Check spread before entry
            spread_ok, current_spread, max_spread = await self._check_spread(symbol)
            if not spread_ok:
                return self._error_result(
                    f"Spread too wide for {symbol}: {current_spread:.5f} > max {max_spread:.5f}"
                )
            
            logger.info(f"Placing {direction} market order: {symbol} {volume} lots (spread: {current_spread:.5f})")
            
            # Execute through MT5 MCP
            result = await self.mt5_client.place_order(
                symbol=symbol,
                order_type=order_type.value,
                volume=volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                comment=comment
            )
            
            if result.get('success'):
                return OrderResult(
                    success=True,
                    order_id=result.get('order_id'),
                    ticket=result.get('ticket'),
                    status=OrderStatus.FILLED,
                    message="Order filled successfully",
                    fill_price=result.get('price'),
                    fill_time=datetime.now()
                )
            else:
                return self._error_result(result.get('error', 'Unknown error'))
                
        except Exception as e:
            logger.error(f"Error placing market order: {e}")
            return self._error_result(str(e))
    
    async def place_limit_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        expiration: Optional[datetime] = None,
        comment: str = "ICT Bot Limit"
    ) -> OrderResult:
        """
        Place a limit order.
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            volume: Position size in lots
            price: Limit price
            stop_loss: Stop loss price
            take_profit: Take profit price
            expiration: Order expiration time
            comment: Order comment
            
        Returns:
            OrderResult with order details
        """
        if not self.mt5_client:
            return self._error_result("MT5 client not connected")
        
        try:
            order_type = OrderType.LIMIT_BUY if direction == 'long' else OrderType.LIMIT_SELL
            
            logger.info(f"Placing {direction} limit order: {symbol} {volume} lots @ {price}")
            
            result = await self.mt5_client.place_order(
                symbol=symbol,
                order_type=order_type.value,
                volume=volume,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                expiration=expiration,
                comment=comment
            )
            
            if result.get('success'):
                order_id = result.get('order_id')
                
                # Track pending order
                order = Order(
                    symbol=symbol,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    comment=comment,
                    expiration=expiration
                )
                self.pending_orders[order_id] = order
                
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    ticket=result.get('ticket'),
                    status=OrderStatus.PENDING,
                    message="Limit order placed"
                )
            else:
                return self._error_result(result.get('error', 'Unknown error'))
                
        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            return self._error_result(str(e))
    
    async def place_pending_order(
        self,
        symbol: str,
        direction: str,
        order_type: str,
        volume: float,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        expiration_minutes: int = 120,
        comment: str = "ICT Bot Pending"
    ) -> OrderResult:
        """
        Place a pending order (limit or stop).
        
        This is the unified method for placing any pending order type:
        - buy_limit: Buy when price drops to specified level
        - sell_limit: Sell when price rises to specified level
        - buy_stop: Buy when price rises above specified level (breakout)
        - sell_stop: Sell when price drops below specified level (breakout)
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            order_type: 'buy_limit', 'sell_limit', 'buy_stop', 'sell_stop'
            volume: Position size in lots
            price: Entry price for the pending order
            stop_loss: Stop loss price
            take_profit: Take profit price
            expiration_minutes: Minutes until order expires (default 120 = 2 hours)
            comment: Order comment
            
        Returns:
            OrderResult with order details
        """
        if not self.mt5_client:
            return self._error_result("MT5 client not connected")
        
        try:
            # Validate order type
            valid_types = ['buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']
            if order_type not in valid_types:
                return self._error_result(f"Invalid order type: {order_type}. Must be one of {valid_types}")
            
            # Determine the correct OrderType enum
            order_type_map = {
                'buy_limit': OrderType.LIMIT_BUY,
                'sell_limit': OrderType.LIMIT_SELL,
                'buy_stop': OrderType.STOP_BUY,
                'sell_stop': OrderType.STOP_SELL
            }
            mt5_order_type = order_type_map[order_type]
            
            # Calculate expiration time — use UTC to match MT5 server time
            # (MT5 timestamps are in server time which is typically UTC or UTC+2/+3;
            #  datetime.now() uses local time which may differ significantly)
            from datetime import timedelta, timezone
            expiration = datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
            
            # Check spread before placing pending order
            spread_ok, current_spread, max_spread = await self._check_spread(symbol)
            if not spread_ok:
                return self._error_result(
                    f"Spread too wide for {symbol}: {current_spread:.5f} > max {max_spread:.5f}"
                )
            
            logger.info(
                f"Placing {order_type} pending order: {symbol} {volume} lots @ {price}, "
                f"SL={stop_loss}, TP={take_profit}, expires in {expiration_minutes}min"
            )
            
            # Place through MT5 client
            result = await self.mt5_client.place_order(
                symbol=symbol,
                order_type=mt5_order_type.value,
                volume=volume,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                expiration=expiration,
                comment=comment
            )
            
            if result.get('success'):
                order_id = result.get('order_id')
                ticket = result.get('ticket')
                
                # Track pending order locally
                order = Order(
                    symbol=symbol,
                    order_type=mt5_order_type,
                    volume=volume,
                    price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    comment=comment,
                    expiration=expiration
                )
                
                if order_id:
                    self.pending_orders[order_id] = order
                elif ticket:
                    self.pending_orders[ticket] = order
                
                logger.info(f"✓ Pending order placed: {order_type} {symbol} @ {price}, ticket: {ticket or order_id}")
                
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    ticket=ticket,
                    status=OrderStatus.PENDING,
                    message=f"Pending {order_type} order placed at {price}"
                )
            else:
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"Failed to place pending order: {error_msg}")
                return self._error_result(error_msg)
                
        except Exception as e:
            logger.error(f"Error placing pending order: {e}")
            import traceback
            traceback.print_exc()
            return self._error_result(str(e))
    
    async def modify_order(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        price: Optional[float] = None
    ) -> OrderResult:
        """
        Modify an existing order.
        
        Args:
            ticket: Order ticket number
            stop_loss: New stop loss (optional)
            take_profit: New take profit (optional)
            price: New price for pending orders (optional)
            
        Returns:
            OrderResult
        """
        if not self.mt5_client:
            return self._error_result("MT5 client not connected")
        
        try:
            logger.info(f"Modifying order {ticket}: SL={stop_loss}, TP={take_profit}")
            
            result = await self.mt5_client.modify_order(
                ticket=ticket,
                stop_loss=stop_loss,
                take_profit=take_profit,
                price=price
            )
            
            if result.get('success'):
                return OrderResult(
                    success=True,
                    order_id=None,
                    ticket=ticket,
                    status=OrderStatus.PENDING,
                    message="Order modified successfully"
                )
            else:
                return self._error_result(result.get('error', 'Unknown error'))
                
        except Exception as e:
            logger.error(f"Error modifying order: {e}")
            return self._error_result(str(e))
    
    async def cancel_order(self, ticket: int) -> OrderResult:
        """
        Cancel a pending order.
        
        Args:
            ticket: Order ticket number
            
        Returns:
            OrderResult
        """
        if not self.mt5_client:
            return self._error_result("MT5 client not connected")
        
        try:
            logger.info(f"Cancelling order {ticket}")
            
            result = await self.mt5_client.cancel_order(ticket=ticket)
            
            if result.get('success'):
                # Remove from pending orders
                self.pending_orders.pop(ticket, None)
                
                return OrderResult(
                    success=True,
                    order_id=None,
                    ticket=ticket,
                    status=OrderStatus.CANCELLED,
                    message="Order cancelled"
                )
            else:
                return self._error_result(result.get('error', 'Unknown error'))
                
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return self._error_result(str(e))
    
    async def close_position(
        self,
        ticket: int,
        volume: Optional[float] = None
    ) -> OrderResult:
        """
        Close a position.
        
        Args:
            ticket: Position ticket number
            volume: Volume to close (partial close if less than position)
            
        Returns:
            OrderResult
        """
        if not self.mt5_client:
            return self._error_result("MT5 client not connected")
        
        try:
            logger.info(f"Closing position {ticket}" + (f" ({volume} lots)" if volume else ""))
            
            result = await self.mt5_client.close_position(
                ticket=ticket,
                volume=volume
            )
            
            if result.get('success'):
                return OrderResult(
                    success=True,
                    order_id=None,
                    ticket=ticket,
                    status=OrderStatus.FILLED,
                    message="Position closed",
                    fill_price=result.get('price'),
                    fill_time=datetime.now()
                )
            else:
                return self._error_result(result.get('error', 'Unknown error'))
                
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return self._error_result(str(e))
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open/pending orders."""
        if not self.mt5_client:
            return []
        
        try:
            orders = await self.mt5_client.get_orders(symbol=symbol)
            return orders if orders else []
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    async def emergency_close_all(self) -> List[OrderResult]:
        """
        EMERGENCY: Close ALL open positions immediately.
        
        Use this to quickly exit all positions in case of emergency.
        
        Returns:
            List of OrderResults for each position closure attempt
        """
        if not self.mt5_client:
            logger.error("Cannot close positions - MT5 client not connected")
            return [self._error_result("MT5 client not connected")]
        
        results = []
        
        try:
            # Get all open positions
            positions = await self.mt5_client.get_positions()
            
            if not positions:
                logger.info("No open positions to close")
                return []
            
            logger.warning(f"EMERGENCY CLOSE: Closing {len(positions)} positions")
            
            for pos in positions:
                # MT5 Position is a dataclass - access attributes directly
                ticket = pos.ticket
                symbol = pos.symbol
                volume = pos.volume
                
                logger.info(f"Closing position {ticket}: {symbol} {volume} lots")
                
                result = await self.close_position(ticket=ticket)
                results.append(result)
                
                if result.success:
                    logger.info(f"  ✓ Position {ticket} closed")
                else:
                    logger.error(f"  ✗ Failed to close position {ticket}: {result.message}")
            
            # Also cancel any pending orders
            pending_orders = await self.mt5_client.get_orders()
            for order in pending_orders:
                order_ticket = order.get('ticket')
                cancel_result = await self.cancel_order(order_ticket)
                if cancel_result.success:
                    logger.info(f"  ✓ Pending order {order_ticket} cancelled")
            
            logger.warning(f"EMERGENCY CLOSE COMPLETE: {sum(1 for r in results if r.success)}/{len(results)} positions closed")
            
            return results
            
        except Exception as e:
            logger.error(f"Error during emergency close: {e}")
            results.append(self._error_result(str(e)))
            return results
    
    def _error_result(self, message: str) -> OrderResult:
        """Create an error result."""
        return OrderResult(
            success=False,
            order_id=None,
            ticket=None,
            status=OrderStatus.REJECTED,
            message=message
        )
