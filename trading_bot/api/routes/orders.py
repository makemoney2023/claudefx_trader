"""
Pending Orders API Routes.

Endpoints for managing pending orders (limit/stop orders).
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..auth import RequireAuth

router = APIRouter(prefix="/orders", tags=["orders"])

# Global reference to pending order manager (set by main app)
_pending_order_manager = None
_order_manager = None


def set_pending_order_manager(manager):
    """Set the pending order manager instance."""
    global _pending_order_manager
    _pending_order_manager = manager


def set_order_manager(manager):
    """Set the order manager instance."""
    global _order_manager
    _order_manager = manager


class PendingOrderRequest(BaseModel):
    """Request to place a pending order."""
    symbol: str = Field(..., description="Trading symbol")
    direction: str = Field(..., description="long or short")
    order_type: str = Field(..., description="buy_limit, sell_limit, buy_stop, sell_stop")
    volume: float = Field(..., description="Position size in lots")
    price: float = Field(..., description="Entry price")
    stop_loss: Optional[float] = Field(None, description="Stop loss price")
    take_profit: Optional[float] = Field(None, description="Take profit price")
    expiration_minutes: int = Field(120, description="Minutes until expiration")


class PendingOrderResponse(BaseModel):
    """Response for pending order operations."""
    success: bool
    ticket: Optional[int] = None
    message: str
    order: Optional[Dict[str, Any]] = None


class PendingOrdersListResponse(BaseModel):
    """Response for listing pending orders."""
    total: int
    active: int
    orders: List[Dict[str, Any]]


@router.get("/pending", response_model=PendingOrdersListResponse)
async def get_pending_orders():
    """
    Get all pending orders.
    
    Combines tracked orders from the bot with live MT5 orders
    to ensure nothing is missed (e.g., after a server restart).
    """
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    # Get tracked orders from the in-memory manager
    tracked_orders = _pending_order_manager.get_active_orders()
    tracked_tickets = {o.ticket for o in tracked_orders}
    
    # Also fetch live orders directly from MT5 to catch untracked orders
    mt5_orders_raw = []
    try:
        if _pending_order_manager.mt5_client:
            mt5_orders_raw = await _pending_order_manager.mt5_client.get_orders()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not fetch MT5 orders: {e}")
    
    # Build combined list — tracked orders first, then untracked MT5 orders
    combined = [o.to_dict() for o in tracked_orders]
    
    # Add MT5 orders not in our tracker
    # MT5 order types: 2=buy_limit, 3=sell_limit, 4=buy_stop, 5=sell_stop
    _type_map = {2: 'buy_limit', 3: 'sell_limit', 4: 'buy_stop', 5: 'sell_stop'}
    _dir_map = {2: 'long', 3: 'short', 4: 'long', 5: 'short'}
    
    for mt5_order in mt5_orders_raw:
        ticket = mt5_order.get('ticket')
        if ticket and ticket not in tracked_tickets:
            order_type_int = mt5_order.get('type', 0)
            combined.append({
                "ticket": ticket,
                "symbol": mt5_order.get('symbol', ''),
                "order_type": _type_map.get(order_type_int, f"type_{order_type_int}"),
                "direction": _dir_map.get(order_type_int, 'unknown'),
                "volume": mt5_order.get('volume', 0),
                "price": mt5_order.get('price_open', 0),
                "stop_loss": mt5_order.get('sl'),
                "take_profit": mt5_order.get('tp'),
                "created_at": datetime.fromtimestamp(mt5_order['time_setup']).isoformat() if mt5_order.get('time_setup') else datetime.now().isoformat(),
                "expiration": None,
                "status": "active",
                "minutes_remaining": None,
                "fill_price": None,
                "fill_time": None,
                "cancel_reason": None,
                "source": "mt5"  # Flag that this came from MT5, not tracker
            })
    
    return {
        "total": len(combined),
        "active": len(combined),
        "orders": combined
    }


@router.get("/pending/{ticket}", response_model=Dict[str, Any])
async def get_pending_order(ticket: int):
    """Get a specific pending order by ticket."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    order = _pending_order_manager.get_order(ticket)
    
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {ticket} not found")
    
    return order.to_dict()


@router.post("/pending", response_model=PendingOrderResponse, dependencies=[Depends(RequireAuth())])
async def place_pending_order(request: PendingOrderRequest):
    """Place a new pending order."""
    if not _order_manager:
        raise HTTPException(status_code=503, detail="Order manager not initialized")
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    # Validate order type
    valid_types = ['buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']
    if request.order_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order type. Must be one of: {valid_types}"
        )
    
    # Basic risk validation for manual orders
    if request.volume <= 0:
        raise HTTPException(status_code=400, detail="Volume must be > 0")
    if request.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be > 0")
    if request.stop_loss is not None and request.stop_loss <= 0:
        raise HTTPException(status_code=400, detail="Stop loss must be > 0")
    if request.take_profit is not None and request.take_profit <= 0:
        raise HTTPException(status_code=400, detail="Take profit must be > 0")
    
    # Validate SL/TP direction
    is_buy = request.direction.lower() == 'long'
    if request.stop_loss is not None:
        if is_buy and request.stop_loss >= request.price:
            raise HTTPException(status_code=400, detail=f"Long SL ({request.stop_loss}) must be below entry ({request.price})")
        if not is_buy and request.stop_loss <= request.price:
            raise HTTPException(status_code=400, detail=f"Short SL ({request.stop_loss}) must be above entry ({request.price})")
    if request.take_profit is not None:
        if is_buy and request.take_profit <= request.price:
            raise HTTPException(status_code=400, detail=f"Long TP ({request.take_profit}) must be above entry ({request.price})")
        if not is_buy and request.take_profit >= request.price:
            raise HTTPException(status_code=400, detail=f"Short TP ({request.take_profit}) must be below entry ({request.price})")
    
    # Enforce max position size
    from ...config import settings as _settings
    max_pos = getattr(_settings.trading, 'max_position_size', 1.0)
    if request.volume > max_pos:
        raise HTTPException(status_code=400, detail=f"Volume {request.volume} exceeds max ({max_pos})")
    
    try:
        # Place order through order manager
        result = await _order_manager.place_pending_order(
            symbol=request.symbol,
            direction=request.direction,
            order_type=request.order_type,
            volume=request.volume,
            price=request.price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            expiration_minutes=request.expiration_minutes,
            comment="API_Pending"
        )
        
        if result.success:
            # Track in pending order manager
            order = await _pending_order_manager.add_order(
                ticket=result.ticket or result.order_id,
                symbol=request.symbol,
                order_type=request.order_type,
                direction=request.direction,
                volume=request.volume,
                price=request.price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                expiration_minutes=request.expiration_minutes
            )
            
            return {
                "success": True,
                "ticket": result.ticket or result.order_id,
                "message": "Pending order placed successfully",
                "order": order.to_dict()
            }
        else:
            return {
                "success": False,
                "ticket": None,
                "message": result.message
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pending/{ticket}", response_model=PendingOrderResponse, dependencies=[Depends(RequireAuth())])
async def cancel_pending_order(ticket: int, reason: str = "manual"):
    """Cancel a specific pending order."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    success = await _pending_order_manager.cancel_order(ticket, reason)
    
    return {
        "success": success,
        "ticket": ticket if success else None,
        "message": f"Order {ticket} cancelled" if success else f"Failed to cancel order {ticket}"
    }


@router.delete("/pending/symbol/{symbol}", response_model=PendingOrderResponse, dependencies=[Depends(RequireAuth())])
async def cancel_pending_orders_for_symbol(symbol: str):
    """Cancel all pending orders for a specific symbol."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    cancelled = await _pending_order_manager.cancel_all_for_symbol(symbol)
    
    return {
        "success": cancelled > 0,
        "ticket": None,
        "message": f"Cancelled {cancelled} pending orders for {symbol}"
    }


@router.delete("/pending/all", response_model=PendingOrderResponse, dependencies=[Depends(RequireAuth())])
async def cancel_all_pending_orders():
    """Cancel all pending orders."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    cancelled = await _pending_order_manager.cancel_all()
    
    return {
        "success": True,
        "ticket": None,
        "message": f"Cancelled {cancelled} pending orders"
    }


@router.post("/pending/sync")
async def sync_pending_orders():
    """Sync pending orders with MT5."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    result = await _pending_order_manager.sync_with_mt5()
    
    return result


@router.post("/pending/expire")
async def expire_pending_orders():
    """Check and cancel expired pending orders."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    result = await _pending_order_manager.cancel_expired_orders()
    
    return result


@router.get("/pending/summary")
async def get_pending_orders_summary():
    """Get summary of pending orders."""
    if not _pending_order_manager:
        raise HTTPException(status_code=503, detail="Pending order manager not initialized")
    
    return _pending_order_manager.get_summary()
