"""
Position Management Module.

Monitors and manages open positions including:
- Position tracking
- Trailing stops
- Break-even management
- Partial profit taking
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime
from enum import Enum

from ..utils.logging import get_logger

logger = get_logger(__name__)


class PositionStatus(Enum):
    """Position status."""
    OPEN = "open"
    BREAK_EVEN = "break_even"
    TRAILING = "trailing"
    PARTIAL_CLOSE = "partial_close"
    CLOSED = "closed"


@dataclass
class Position:
    """Represents an open trading position."""
    ticket: int
    symbol: str
    direction: str  # 'long' or 'short'
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    open_time: datetime
    status: PositionStatus = PositionStatus.OPEN
    
    # Profit tracking
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    
    # Trade classification
    trade_type: str = "intraday"  # scalp, intraday, swing
    
    # Management
    initial_sl: float = 0.0
    be_triggered: bool = False
    trailing_active: bool = False
    partial_closed: bool = False
    
    # Multi-TP levels and tracking
    tp1: float = 0.0          # First target (40% close)
    tp2: float = 0.0          # Second target (30% close)  
    tp3: float = 0.0          # Runner target (trailing stop)
    tp1_hit: bool = False     # TP1 partial close executed
    tp2_hit: bool = False     # TP2 partial close executed
    initial_volume: float = 0.0  # Original volume before partial closes
    
    def __post_init__(self):
        self.initial_sl = self.stop_loss
        if self.initial_volume == 0.0:
            self.initial_volume = self.volume
    
    @property
    def risk_pips(self) -> float:
        """Calculate risk in pips from entry to initial SL."""
        return abs(self.entry_price - self.initial_sl)
    
    @property
    def current_r_multiple(self) -> float:
        """Calculate current profit as R multiple."""
        if self.risk_pips == 0:
            return 0.0
        
        if self.direction == 'long':
            pnl_pips = self.current_price - self.entry_price
        else:
            pnl_pips = self.entry_price - self.current_price
        
        return pnl_pips / self.risk_pips
    
    def to_dict(self) -> dict:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "direction": self.direction,
            "trade_type": self.trade_type,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "status": self.status.value,
            "r_multiple": self.current_r_multiple,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "tp1_hit": self.tp1_hit,
            "tp2_hit": self.tp2_hit,
            "initial_volume": self.initial_volume,
        }


class PositionManager:
    """
    Manages open trading positions.
    
    Features:
    - Position monitoring
    - Break-even stop management
    - Trailing stop implementation
    - Partial profit taking
    """
    
    def __init__(
        self,
        order_manager=None,
        tp1_close_percent: float = 0.40,      # Close 40% at TP1
        tp2_close_percent: float = 0.30,      # Close 30% at TP2 (of original)
        break_even_trigger_r: float = 1.0,    # Move to BE after TP1 hit
        trailing_start_r: float = 2.0,        # Start trailing after TP2
        trailing_step_r: float = 0.5,         # Trail in 0.5R steps
        # Legacy compatibility
        partial_close_r: float = 1.0,
        partial_close_percent: float = 0.5
    ):
        """
        Initialize the position manager.
        
        Args:
            order_manager: OrderManager instance for modifications
            break_even_trigger_r: R multiple to trigger break-even
            trailing_start_r: R multiple to start trailing
            trailing_step_r: R multiple step for trailing
            partial_close_r: R multiple to trigger partial close
            partial_close_percent: Percentage to close (0.5 = 50%)
        """
        self.order_manager = order_manager
        self.tp1_close_percent = tp1_close_percent
        self.tp2_close_percent = tp2_close_percent
        self.break_even_trigger_r = break_even_trigger_r
        self.trailing_start_r = trailing_start_r
        self.trailing_step_r = trailing_step_r
        self.partial_close_r = partial_close_r
        self.partial_close_percent = partial_close_percent
        
        self.positions: Dict[int, Position] = {}
        self.on_position_close = None  # Callback for when position closes
        
        logger.info("Position manager initialized")
    
    def set_order_manager(self, order_manager):
        """Set the order manager."""
        self.order_manager = order_manager
    
    def set_on_position_close(self, callback):
        """Set callback for position close events."""
        self.on_position_close = callback
    
    def add_position(self, position: Position):
        """Add a position to track."""
        self.positions[position.ticket] = position
        logger.info(f"Tracking position {position.ticket}: {position.symbol} {position.direction}")
        # Persist to database asynchronously (only if event loop is running)
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_position(position))
        except RuntimeError:
            # No running event loop - skip persistence (e.g., in tests)
            pass
    
    def remove_position(self, ticket: int):
        """Remove a position from tracking."""
        if ticket in self.positions:
            del self.positions[ticket]
            logger.info(f"Removed position {ticket} from tracking")
            # Remove from database asynchronously (only if event loop is running)
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._delete_position_from_db(ticket))
            except RuntimeError:
                # No running event loop - skip persistence (e.g., in tests)
                pass
    
    async def _persist_position(self, position: Position):
        """Persist position to database."""
        try:
            from ..api.database import async_session_maker, PositionStateRepository
            
            async with async_session_maker() as session:
                repo = PositionStateRepository(session)
                await repo.save_position({
                    'ticket': position.ticket,
                    'symbol': position.symbol,
                    'direction': position.direction,
                    'trade_type': position.trade_type,
                    'volume': position.volume,
                    'entry_price': position.entry_price,
                    'stop_loss': position.stop_loss,
                    'take_profit': position.take_profit,
                    'open_time': position.open_time,
                    'status': position.status.value,
                    'initial_sl': position.initial_sl,
                    'be_triggered': position.be_triggered,
                    'trailing_active': position.trailing_active,
                    'partial_closed': position.partial_closed,
                    'tp1': position.tp1,
                    'tp2': position.tp2,
                    'tp3': position.tp3,
                    'tp1_hit': position.tp1_hit,
                    'tp2_hit': position.tp2_hit,
                    'initial_volume': position.initial_volume,
                })
                logger.debug(f"Persisted position {position.ticket} to database")
        except Exception as e:
            logger.error(f"Error persisting position {position.ticket}: {e}")
    
    async def _delete_position_from_db(self, ticket: int):
        """Delete position from database."""
        try:
            from ..api.database import async_session_maker, PositionStateRepository
            
            async with async_session_maker() as session:
                repo = PositionStateRepository(session)
                await repo.delete_position(ticket)
                logger.debug(f"Deleted position {ticket} from database")
        except Exception as e:
            logger.error(f"Error deleting position {ticket} from database: {e}")
    
    async def load_from_db(self) -> List[Position]:
        """Load positions from database (for recovery after restart)."""
        try:
            from ..api.database import async_session_maker, PositionStateRepository
            
            async with async_session_maker() as session:
                repo = PositionStateRepository(session)
                db_positions = await repo.get_all_open()
                
                positions = []
                for p in db_positions:
                    position = Position(
                        ticket=p.ticket,
                        symbol=p.symbol,
                        direction=p.direction,
                        volume=p.volume,
                        entry_price=p.entry_price,
                        stop_loss=p.stop_loss,
                        take_profit=p.take_profit,
                        open_time=p.open_time,
                        status=PositionStatus(p.status) if p.status else PositionStatus.OPEN,
                        trade_type=getattr(p, 'trade_type', 'intraday') or 'intraday',
                    )
                    position.initial_sl = p.initial_sl
                    position.be_triggered = p.be_triggered
                    position.trailing_active = p.trailing_active
                    position.partial_closed = p.partial_closed
                    # Restore multi-TP state (prevents TP1/TP2 retriggering after restart)
                    position.tp1 = getattr(p, 'tp1', 0.0) or 0.0
                    position.tp2 = getattr(p, 'tp2', 0.0) or 0.0
                    position.tp3 = getattr(p, 'tp3', 0.0) or 0.0
                    position.tp1_hit = getattr(p, 'tp1_hit', False) or False
                    position.tp2_hit = getattr(p, 'tp2_hit', False) or False
                    position.initial_volume = getattr(p, 'initial_volume', 0.0) or position.volume
                    positions.append(position)
                
                logger.info(f"Loaded {len(positions)} positions from database")
                return positions
        except Exception as e:
            logger.error(f"Error loading positions from database: {e}")
            return []
    
    async def sync_with_mt5(self, mt5_client) -> dict:
        """
        Sync positions with MT5 - detect closed positions.
        
        Args:
            mt5_client: MT5 client instance
            
        Returns:
            Dict with sync results
        """
        try:
            mt5_positions = await mt5_client.get_positions()
            # MT5 Position is a dataclass - access attributes directly
            mt5_tickets = {p.ticket for p in mt5_positions}
            
            closed_positions = []
            
            # Check for positions that closed
            for ticket in list(self.positions.keys()):
                if ticket not in mt5_tickets:
                    closed_pos = self.positions[ticket]
                    closed_positions.append(closed_pos)
                    logger.info(f"Position {ticket} closed (detected via MT5 sync)")
                    
                    # Call close callback if set
                    if self.on_position_close:
                        await self.on_position_close(closed_pos)
                    
                    self.remove_position(ticket)
            
            # Update existing tracked positions from MT5 live data
            for mt5_pos in mt5_positions:
                ticket = mt5_pos.ticket
                if ticket in self.positions:
                    pos = self.positions[ticket]
                    # Sync SL/TP/volume/price from MT5 reality
                    if mt5_pos.sl and mt5_pos.sl > 0:
                        pos.stop_loss = mt5_pos.sl
                    if mt5_pos.tp and mt5_pos.tp > 0:
                        pos.take_profit = mt5_pos.tp
                    pos.volume = mt5_pos.volume
                    pos.current_price = mt5_pos.price_current
            
            # Check for positions in MT5 not tracked locally
            new_positions = []
            for mt5_pos in mt5_positions:
                ticket = mt5_pos.ticket
                if ticket not in self.positions:
                    logger.warning(f"Position {ticket} in MT5 but not tracked - adding to tracking")
                    from datetime import datetime
                    position = Position(
                        ticket=ticket,
                        symbol=mt5_pos.symbol,
                        direction='long' if mt5_pos.type == 'buy' else 'short',
                        volume=mt5_pos.volume,
                        entry_price=mt5_pos.price_open,
                        stop_loss=mt5_pos.sl,
                        take_profit=mt5_pos.tp,
                        open_time=datetime.now()
                    )
                    
                    # Auto-calculate multi-TP levels from SL/TP if available
                    if mt5_pos.sl and mt5_pos.sl > 0:
                        _sl_dist = abs(mt5_pos.price_open - mt5_pos.sl)
                        _dir = 'long' if mt5_pos.type == 'buy' else 'short'
                        if _dir == 'long':
                            position.tp1 = mt5_pos.price_open + (_sl_dist * 1.0)
                            position.tp2 = mt5_pos.price_open + (_sl_dist * 2.0)
                            position.tp3 = mt5_pos.price_open + (_sl_dist * 3.0)
                        else:
                            position.tp1 = mt5_pos.price_open - (_sl_dist * 1.0)
                            position.tp2 = mt5_pos.price_open - (_sl_dist * 2.0)
                            position.tp3 = mt5_pos.price_open - (_sl_dist * 3.0)
                        logger.info(f"  Auto-TP: TP1={position.tp1:.5f}, TP2={position.tp2:.5f}, TP3={position.tp3:.5f}")
                    
                    self.add_position(position)
                    new_positions.append(ticket)
            
            return {
                'synced': True,
                'mt5_positions': len(mt5_positions),
                'tracked_positions': len(self.positions),
                'closed_count': len(closed_positions),
                'closed': [p.ticket for p in closed_positions],
                'new_positions': new_positions
            }
        except Exception as e:
            logger.error(f"Error syncing with MT5: {e}")
            return {'synced': False, 'error': str(e)}
    
    def update_price(self, ticket: int, current_price: float):
        """Update the current price for a position."""
        if ticket in self.positions:
            pos = self.positions[ticket]
            pos.current_price = current_price
            
            # Calculate unrealized P&L using symbol-aware contract size
            from ..config import get_symbol_spec
            _spec = get_symbol_spec(pos.symbol)
            if pos.direction == 'long':
                pos.unrealized_pnl = (current_price - pos.entry_price) * pos.volume * _spec.contract_size
            else:
                pos.unrealized_pnl = (pos.entry_price - current_price) * pos.volume * _spec.contract_size
    
    async def manage_positions(self, price_data: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Check and manage all tracked positions.
        
        Args:
            price_data: Dict of symbol -> current price
            
        Returns:
            List of actions taken
        """
        actions = []
        
        for ticket, position in list(self.positions.items()):
            current_price = price_data.get(position.symbol)
            if not current_price:
                continue
            
            self.update_price(ticket, current_price)
            
            # Check for management actions
            action = await self._manage_position(position)
            if action:
                actions.append(action)
        
        return actions
    
    async def _manage_position(self, position: Position) -> Optional[Dict[str, Any]]:
        """
        Apply multi-TP management rules to a single position.
        
        For positions large enough for partials (>= 0.03 lots):
          1. TP1 hit (1.0R) -> Partial close 40% + move to break-even
          2. TP2 hit (2.0R) -> Partial close 30% of original
          3. Trailing stop on runner (remaining 30%)
        
        For micro positions (< 0.03 lots, can't partial close):
          1. 1.0R hit -> Move to break-even (no partial close)
          2. 2.0R hit -> Start trailing stop (no partial close)
          3. Let the trailing stop or MT5 TP handle the exit
        """
        r_multiple = position.current_r_multiple
        can_partial = position.volume >= 0.03  # Need at least 0.03 to split meaningfully
        
        # SCALP: No partial close management — MT5 TP will close full position.
        # Just move to break-even at 0.5R for protection.
        if getattr(position, 'trade_type', 'intraday') == 'scalp':
            if not position.be_triggered and r_multiple >= 0.5:
                logger.info(f"SCALP: Moving {position.ticket} to break-even at {r_multiple:.1f}R")
                return await self._move_to_break_even(position)
            return None  # Let MT5 TP handle the full close
        
        # Stage 0.5: If TP1 fired (partial close done) but break-even modification failed,
        # retry the break-even move. Without this, tp1_hit=True prevents re-entering Stage 1.
        if position.tp1_hit and not position.be_triggered and r_multiple >= self.break_even_trigger_r:
            logger.info(f"Retrying break-even modification for {position.ticket} (TP1 already done)")
            return await self._move_to_break_even(position)
        
        # Stage 1: TP1 - Partial close (if possible) + move to break-even
        if not position.tp1_hit and r_multiple >= self.break_even_trigger_r:
            tp1_reached = False
            if position.tp1 > 0:
                if position.direction == 'long' and position.current_price >= position.tp1:
                    tp1_reached = True
                elif position.direction == 'short' and position.current_price <= position.tp1:
                    tp1_reached = True
            if r_multiple >= self.break_even_trigger_r:
                tp1_reached = True
            
            if tp1_reached:
                if can_partial:
                    return await self._execute_tp1(position)
                else:
                    # Micro position: just move to break-even, no partial close
                    logger.info(
                        f"TP1 HIT on {position.ticket} ({position.symbol}): "
                        f"Position too small for partial ({position.volume} lots) - moving to break-even only"
                    )
                    position.tp1_hit = True
                    return await self._move_to_break_even(position)
        
        # Stage 2: TP2 - Partial close (if possible) or start trailing
        if position.tp1_hit and not position.tp2_hit:
            tp2_reached = False
            if position.tp2 > 0:
                if position.direction == 'long' and position.current_price >= position.tp2:
                    tp2_reached = True
                elif position.direction == 'short' and position.current_price <= position.tp2:
                    tp2_reached = True
            if r_multiple >= self.trailing_start_r:
                tp2_reached = True
            
            if tp2_reached:
                if can_partial:
                    return await self._execute_tp2(position)
                else:
                    # Micro position: skip partial, activate trailing stop directly
                    logger.info(
                        f"TP2 HIT on {position.ticket} ({position.symbol}): "
                        f"Position too small for partial ({position.volume} lots) - activating trailing stop"
                    )
                    position.tp2_hit = True
                    position.status = PositionStatus.TRAILING
                    return await self._update_trailing_stop(position)
        
        # Stage 3: Trailing stop on runner (applies to all position sizes)
        if position.tp2_hit and r_multiple >= self.trailing_start_r:
            return await self._update_trailing_stop(position)
        
        return None
    
    async def _execute_tp1(self, position: Position) -> Dict[str, Any]:
        """
        Execute TP1: Close 40% of position and move SL to break-even.
        """
        close_volume = round(position.initial_volume * self.tp1_close_percent, 2)
        close_volume = max(0.01, close_volume)  # Minimum lot
        
        # Don't close more than what's open — ALWAYS leave at least 0.01 for runner
        if close_volume >= position.volume:
            remaining_after_close = round(position.volume - 0.01, 2)
            if remaining_after_close >= 0.01:
                close_volume = remaining_after_close
            else:
                close_volume = 0.01  # Minimum possible close
        
        logger.info(
            f"TP1 HIT on {position.ticket} ({position.symbol}): "
            f"Closing {close_volume} lots ({self.tp1_close_percent*100:.0f}%) + moving to break-even"
        )
        
        if self.order_manager:
            # First: partial close
            result = await self.order_manager.close_position(
                ticket=position.ticket,
                volume=close_volume
            )
            
            if result.success:
                position.volume = round(position.volume - close_volume, 2)
                position.tp1_hit = True
                position.partial_closed = True
                
                # Second: move to break-even
                buffer = position.risk_pips * 0.1
                if position.direction == 'long':
                    new_sl = position.entry_price + buffer
                else:
                    new_sl = position.entry_price - buffer
                
                be_result = await self.order_manager.modify_order(
                    ticket=position.ticket,
                    stop_loss=new_sl
                )
                
                if be_result.success:
                    position.stop_loss = new_sl
                    position.be_triggered = True
                    position.status = PositionStatus.BREAK_EVEN
                    logger.info(f"  Break-even set at {new_sl:.5f}")
                else:
                    # BE modification failed - log warning, will retry next cycle
                    # Do NOT mark be_triggered so we retry
                    logger.warning(
                        f"  Break-even modification FAILED for {position.ticket}: "
                        f"{be_result.message if hasattr(be_result, 'message') else 'unknown error'} - will retry next cycle"
                    )
            else:
                # Partial close failed - revert tp1_hit so we retry
                logger.warning(f"  TP1 partial close FAILED for {position.ticket} - will retry next cycle")
                return None
        
        return {
            "action": "tp1_partial_close_and_be",
            "ticket": position.ticket,
            "closed_volume": close_volume,
            "remaining_volume": position.volume,
            "r_multiple": position.current_r_multiple
        }
    
    async def _execute_tp2(self, position: Position) -> Dict[str, Any]:
        """
        Execute TP2: Close 30% of original volume (next partial).
        """
        close_volume = round(position.initial_volume * self.tp2_close_percent, 2)
        close_volume = max(0.01, close_volume)
        
        # Don't close more than what's open — ALWAYS leave at least 0.01 runner
        if close_volume >= position.volume:
            remaining_after_close = round(position.volume - 0.01, 2)
            if remaining_after_close >= 0.01:
                close_volume = remaining_after_close
            else:
                # Position too small to split — skip TP2 partial, just activate trailing
                logger.info(
                    f"TP2: Position {position.ticket} too small to split ({position.volume} lots) "
                    f"— skipping partial close, activating trailing stop"
                )
                position.tp2_hit = True
                position.status = PositionStatus.TRAILING
                return {
                    "action": "tp2_skip_trailing",
                    "ticket": position.ticket,
                    "remaining_volume": position.volume,
                    "r_multiple": position.current_r_multiple
                }
        
        logger.info(
            f"TP2 HIT on {position.ticket} ({position.symbol}): "
            f"Closing {close_volume} lots ({self.tp2_close_percent*100:.0f}% of original)"
        )
        
        if self.order_manager:
            result = await self.order_manager.close_position(
                ticket=position.ticket,
                volume=close_volume
            )
            
            if result.success:
                position.volume = round(position.volume - close_volume, 2)
                position.tp2_hit = True
                position.status = PositionStatus.TRAILING
                logger.info(f"  Runner remaining: {position.volume} lots")
            else:
                # Partial close failed - do NOT mark tp2_hit so we retry next cycle
                logger.warning(f"  TP2 partial close FAILED for {position.ticket} - will retry next cycle")
                return None
        
        return {
            "action": "tp2_partial_close",
            "ticket": position.ticket,
            "closed_volume": close_volume,
            "remaining_volume": position.volume,
            "r_multiple": position.current_r_multiple
        }
    
    async def _move_to_break_even(self, position: Position) -> Dict[str, Any]:
        """Move stop loss to break-even (with spread check)."""
        logger.info(f"Moving position {position.ticket} to break-even")
        
        # Check spread before modifying (T2-2: delay if spread is 2x+ normal)
        if self.order_manager and hasattr(self.order_manager, '_check_spread'):
            spread_ok, current_spread, max_spread = await self.order_manager._check_spread(position.symbol)
            if not spread_ok:
                logger.warning(
                    f"Spread too wide for BE move on {position.ticket} ({position.symbol}): "
                    f"{current_spread:.5f} > {max_spread:.5f} - delaying to next cycle"
                )
                return {
                    "action": "break_even_delayed",
                    "ticket": position.ticket,
                    "reason": "spread_too_wide"
                }
        
        # Add a small buffer (spread consideration)
        buffer = position.risk_pips * 0.1
        
        if position.direction == 'long':
            new_sl = position.entry_price + buffer
        else:
            new_sl = position.entry_price - buffer
        
        if self.order_manager:
            result = await self.order_manager.modify_order(
                ticket=position.ticket,
                stop_loss=new_sl
            )
            
            if result.success:
                position.stop_loss = new_sl
                position.be_triggered = True
                position.status = PositionStatus.BREAK_EVEN
                return {
                    "action": "break_even",
                    "success": True,
                    "ticket": position.ticket,
                    "new_sl": new_sl
                }
            else:
                logger.warning(
                    f"Break-even modification FAILED for {position.ticket}: "
                    f"{getattr(result, 'message', 'unknown error')}"
                )
                return {
                    "action": "break_even",
                    "success": False,
                    "ticket": position.ticket,
                    "new_sl": new_sl,
                    "error": getattr(result, 'message', 'MT5 rejected modification')
                }
        
        return {
            "action": "break_even",
            "success": False,
            "ticket": position.ticket,
            "new_sl": new_sl,
            "error": "No order manager available"
        }
    
    async def _partial_close(self, position: Position) -> Dict[str, Any]:
        """Close partial position."""
        close_volume = round(position.volume * self.partial_close_percent, 2)
        
        # Ensure minimum lot size
        if close_volume < 0.01:
            logger.warning(f"Partial close volume {close_volume} below minimum 0.01 for {position.ticket}")
            close_volume = 0.01
        
        logger.info(f"Partial close position {position.ticket}: {close_volume} lots")
        
        if self.order_manager:
            result = await self.order_manager.close_position(
                ticket=position.ticket,
                volume=close_volume
            )
            
            if result.success:
                position.volume = round(position.volume - close_volume, 2)
                position.partial_closed = True
                position.status = PositionStatus.PARTIAL_CLOSE
        
        return {
            "action": "partial_close",
            "ticket": position.ticket,
            "closed_volume": close_volume,
            "remaining_volume": position.volume
        }
    
    async def _update_trailing_stop(self, position: Position) -> Optional[Dict[str, Any]]:
        """Update trailing stop (with spread check)."""
        # Check spread before modifying (T2-2: delay if spread is 2x+ normal)
        if self.order_manager and hasattr(self.order_manager, '_check_spread'):
            spread_ok, current_spread, max_spread = await self.order_manager._check_spread(position.symbol)
            if not spread_ok:
                logger.warning(
                    f"Spread too wide for trailing stop update on {position.ticket}: "
                    f"{current_spread:.5f} - delaying to next cycle"
                )
                return None  # Skip this cycle
        
        r_multiple = position.current_r_multiple
        
        # Calculate new SL level based on R multiple
        trail_r = int(r_multiple / self.trailing_step_r) * self.trailing_step_r
        
        if position.direction == 'long':
            new_sl = position.entry_price + (trail_r - 0.5) * position.risk_pips
            if new_sl <= position.stop_loss:
                return None  # SL hasn't moved up
        else:
            new_sl = position.entry_price - (trail_r - 0.5) * position.risk_pips
            if new_sl >= position.stop_loss:
                return None  # SL hasn't moved down
        
        logger.info(f"Trailing stop update for {position.ticket}: {new_sl}")
        
        if self.order_manager:
            result = await self.order_manager.modify_order(
                ticket=position.ticket,
                stop_loss=new_sl
            )
            
            if result.success:
                position.stop_loss = new_sl
                position.trailing_active = True
                position.status = PositionStatus.TRAILING
        
        return {
            "action": "trailing_stop",
            "ticket": position.ticket,
            "new_sl": new_sl,
            "locked_r": trail_r - 0.5
        }
    
    def get_position(self, ticket: int) -> Optional[Position]:
        """Get a position by ticket."""
        return self.positions.get(ticket)
    
    def get_all_positions(self) -> List[Position]:
        """Get all tracked positions."""
        return list(self.positions.values())
    
    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """Get positions for a specific symbol."""
        return [p for p in self.positions.values() if p.symbol == symbol]
    
    def get_total_exposure(self) -> Dict[str, float]:
        """Get total volume exposure per symbol."""
        exposure = {}
        for position in self.positions.values():
            if position.symbol not in exposure:
                exposure[position.symbol] = 0.0
            exposure[position.symbol] += position.volume
        return exposure
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all positions."""
        total_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        
        return {
            "total_positions": len(self.positions),
            "total_unrealized_pnl": total_pnl,
            "positions": [p.to_dict() for p in self.positions.values()],
            "exposure": self.get_total_exposure()
        }
