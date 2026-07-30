"""
Order Management Module.

Handles order placement, modification, and cancellation
through the MT5 MCP server.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime, timezone

from ..utils.logging import get_logger
from ..services.spread_policy import MAX_SPREAD_THRESHOLDS as _SPREAD_THRESHOLDS

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
    fill_volume: Optional[float] = None
    converted_to_market: bool = False
    final_order_type: Optional[str] = None
    broker_price: Optional[float] = None
    broker_sl: Optional[float] = None
    broker_tp: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "ticket": self.ticket,
            "status": self.status.value,
            "message": self.message,
            "fill_price": self.fill_price,
            "fill_time": self.fill_time.isoformat() if self.fill_time else None,
            "fill_volume": self.fill_volume,
            "converted_to_market": self.converted_to_market,
            "final_order_type": self.final_order_type,
            "broker_price": self.broker_price,
            "broker_sl": self.broker_sl,
            "broker_tp": self.broker_tp,
        }

    @staticmethod
    def from_broker_dict(result: dict, *, default_status: OrderStatus) -> "OrderResult":
        """Build OrderResult from MT5Client.place_order success/failure dict."""
        converted = bool(result.get("converted_to_market"))
        final_ot = result.get("final_order_type")
        # A PRICE-FIX conversion means the broker filled a market DEAL,
        # so the result is FILLED even when a pending order was requested.
        status = OrderStatus.FILLED if converted else default_status
        return OrderResult(
            success=True,
            order_id=result.get("order_id"),
            ticket=result.get("ticket"),
            status=status,
            message=(
                f"Converted to market {final_ot}" if converted
                else result.get("message") or "Order accepted"
            ),
            fill_price=result.get("price"),
            fill_time=datetime.now(timezone.utc) if status == OrderStatus.FILLED else None,
            fill_volume=result.get("volume"),
            converted_to_market=converted,
            final_order_type=final_ot,
            broker_price=result.get("price"),
            broker_sl=result.get("sl"),
            broker_tp=result.get("tp"),
        )


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
    
    MAX_SPREAD_THRESHOLDS = _SPREAD_THRESHOLDS

    async def _check_spread(self, symbol: str) -> tuple:
        """
        Check if current spread is acceptable for trading.
        
        Returns:
            (is_acceptable, current_spread, max_spread)
        """
        from ..services.spread_policy import evaluate_spread_state
        from ..config import settings

        live_mode = not bool(getattr(settings.trading, "dry_run", False)) and not bool(
            getattr(settings.mt5, "allow_simulation_trades", False)
            if hasattr(settings, "mt5")
            else False
        )

        if not self.mt5_client:
            state = evaluate_spread_state(
                symbol, spread=None, unavailable=True, live_mode=live_mode
            )
            return state.allows_trading, 0, 0
        
        try:
            symbol_info = await self.mt5_client.get_symbol_info(symbol)
            if not symbol_info:
                state = evaluate_spread_state(
                    symbol, spread=None, unavailable=True, live_mode=live_mode
                )
                return state.allows_trading, 0, 0
            
            current_spread = getattr(symbol_info, 'spread', 0) or 0
            ask = getattr(symbol_info, 'ask', 0) or 0
            bid = getattr(symbol_info, 'bid', 0) or 0
            
            if ask and bid:
                spread_price = ask - bid
                mid = (ask + bid) / 2
            else:
                point = getattr(symbol_info, 'point', 0.00001) or 0.00001
                spread_price = current_spread * point
                mid = 0.0

            state = evaluate_spread_state(
                symbol,
                spread=spread_price,
                mid_price=mid,
                live_mode=live_mode,
            )
            if not state.allows_trading:
                logger.warning(f"SPREAD BLOCK {symbol}: {state.reason}")
            return state.allows_trading, spread_price, state.max_spread
            
        except Exception as e:
            logger.warning(f"Could not check spread for {symbol}: {e}")
            state = evaluate_spread_state(
                symbol, spread=None, unavailable=True, live_mode=live_mode
            )
            return state.allows_trading, 0, 999
    
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
                out = OrderResult.from_broker_dict(
                    result, default_status=OrderStatus.FILLED
                )
                out.message = "Order filled successfully"
                return out
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
                out = OrderResult.from_broker_dict(
                    result, default_status=OrderStatus.PENDING
                )
                if out.converted_to_market:
                    logger.info(
                        f"✓ Pending {order_type} PRICE-FIX converted to market "
                        f"{out.final_order_type} {symbol}, ticket: {ticket or order_id}"
                    )
                    out.message = (
                        f"Pending {order_type} converted to market "
                        f"{out.final_order_type}"
                    )
                    return out

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

                logger.info(
                    f"✓ Pending order placed: {order_type} {symbol} @ {price}, "
                    f"ticket: {ticket or order_id}"
                )
                out.message = f"Pending {order_type} order placed at {price}"
                return out
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
                    fill_time=datetime.now(timezone.utc)
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
