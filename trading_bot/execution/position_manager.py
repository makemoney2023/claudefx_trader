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
from datetime import datetime, timezone
from enum import Enum
import asyncio

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
    a_plus: bool = False  # High-confidence / HTF-aligned runner
    
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
    
    # Peak profit tracking (aggressive profit protection)
    peak_r_multiple: float = 0.0
    peak_unrealized_pnl: float = 0.0
    
    # Near-TP tracking
    near_tp_reached: bool = False
    
    # Close metadata (set before closing for reversal re-entry)
    close_reason: str = ""  # "tp_hit", "sl_hit", "giveback_protection", "near_tp_reversal", "trailing_stop", "claude_close", "manual"

    # Original order ticket (differs from position ticket for filled pending orders)
    order_ticket: Optional[int] = None
    reservation_id: Optional[str] = None

    # Authoritative close data from fast pending sync (broker deal history)
    closed_profit_loss: Optional[float] = None
    closed_exit_price: Optional[float] = None
    
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
            "peak_r_multiple": self.peak_r_multiple,
            "peak_unrealized_pnl": self.peak_unrealized_pnl,
            "near_tp_reached": self.near_tp_reached,
            "close_reason": self.close_reason,
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
        giveback_min_peak_r: float = 1.5,     # Arm giveback protection after this peak R
        a_plus_skip_tp1: bool = True,         # Skip TP1 partials for runners / A+ trades
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
        self.giveback_min_peak_r = giveback_min_peak_r
        self.a_plus_skip_tp1 = a_plus_skip_tp1
        
        self.positions: Dict[int, Position] = {}
        self._persist_tasks: set = set()
        self._delete_tasks: set = set()
        self.on_position_close = None  # Callback for when position closes
        self.on_reversal_close = None  # Callback for reversal-type closes (profit protection)
        
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
        self._schedule_persist(position)
    
    def _schedule_persist(self, position: Position):
        """Persist position state after management mutations (restart-safe)."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._persist_position_tracked(position))
            self._persist_tasks.add(task)
            task.add_done_callback(self._persist_tasks.discard)
        except RuntimeError:
            pass

    async def _persist_position_tracked(self, position: Position):
        try:
            await self._persist_position(position)
        except Exception as e:
            logger.error(f"Tracked persistence failed for {position.ticket}: {e}")

    async def _persist_and_wait(self, position: Position):
        """Await durable persistence before destructive transitions complete."""
        pending = [task for task in self._persist_tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending)
        await self._persist_position(position)

    async def flush_persistence(self):
        """Drain outstanding persistence and delete tasks (shutdown-safe)."""
        pending = list(self._persist_tasks) + list(self._delete_tasks)
        if not pending:
            return
        results = await asyncio.gather(*pending, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Persistence task failed during flush: {result}")
        self._persist_tasks.clear()
        self._delete_tasks.clear()
    
    def _skip_tp1_partial(self, position: Position) -> bool:
        """Skip early TP1 partial only for explicit A+ classification."""
        return bool(getattr(position, "a_plus", False))
    
    def remove_position(self, ticket: int):
        """Remove a position from tracking."""
        if ticket in self.positions:
            del self.positions[ticket]
            logger.info(f"Removed position {ticket} from tracking")
            self._schedule_delete(ticket)

    def _schedule_delete(self, ticket: int):
        """Delete position state asynchronously with tracked durability."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._delete_position_tracked(ticket))
            self._delete_tasks.add(task)
            task.add_done_callback(self._delete_tasks.discard)
        except RuntimeError:
            pass

    async def _delete_position_tracked(self, ticket: int):
        try:
            await self._delete_position_from_db(ticket)
        except Exception as e:
            logger.error(f"Tracked delete failed for {ticket}: {e}")
    
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
                    'peak_r_multiple': position.peak_r_multiple,
                    'peak_unrealized_pnl': position.peak_unrealized_pnl,
                    'near_tp_reached': position.near_tp_reached,
                    'close_reason': position.close_reason or None,
                    'a_plus': getattr(position, 'a_plus', False),
                    'reservation_id': getattr(position, 'reservation_id', None),
                    'remaining_volume': position.volume,
                })
                logger.debug(f"Persisted position {position.ticket} to database")
        except Exception as e:
            from sqlalchemy.exc import OperationalError

            if isinstance(e, OperationalError):
                logger.debug(
                    f"Skipping position persistence for {position.ticket} (database unavailable): {e}"
                )
                return
            logger.error(f"Error persisting position {position.ticket}: {e}")
            raise
    
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
                        a_plus=getattr(p, 'a_plus', False) or False,
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
                    # Restore peak profit tracking (survives bot restart)
                    position.peak_r_multiple = getattr(p, 'peak_r_multiple', 0.0) or 0.0
                    position.peak_unrealized_pnl = getattr(p, 'peak_unrealized_pnl', 0.0) or 0.0
                    position.near_tp_reached = getattr(p, 'near_tp_reached', False) or False
                    position.close_reason = getattr(p, 'close_reason', '') or ''
                    position.reservation_id = getattr(p, 'reservation_id', None)
                    remaining = getattr(p, 'remaining_volume', 0.0) or 0.0
                    if remaining > 0:
                        position.volume = remaining
                    positions.append(position)
                
                logger.info(f"Loaded {len(positions)} positions from database")
                return positions
        except Exception as e:
            logger.error(f"Error loading positions from database: {e}")
            return []
    
    async def cleanup_stale_db_records(self, mt5_client) -> int:
        """
        Remove DB position_states records for positions no longer in MT5.
        
        Call this once at startup AFTER load_from_db but BEFORE the main
        sync loop to silently prune stale records without triggering
        close callbacks or noisy "position closed" logs every restart.
        
        Returns:
            Number of stale records removed.
        """
        removed = 0
        try:
            mt5_positions = await mt5_client.get_positions()
            mt5_tickets = {p.ticket for p in mt5_positions}
            
            pending_orders = await mt5_client.get_orders()
            pending_tickets = set()
            if pending_orders:
                for order in pending_orders:
                    if isinstance(order, dict):
                        pending_tickets.add(order.get('ticket', 0))
                    else:
                        pending_tickets.add(getattr(order, 'ticket', 0))
            
            all_mt5_tickets = mt5_tickets | pending_tickets
            
            stale_tickets = [
                ticket for ticket in list(self.positions.keys())
                if ticket not in all_mt5_tickets
            ]
            
            for ticket in stale_tickets:
                pos = self.positions.get(ticket)
                symbol = pos.symbol if pos else "?"
                logger.info(
                    f"[STARTUP-CLEANUP] Removing stale DB record: "
                    f"ticket={ticket} symbol={symbol} (not in MT5)"
                )
                if ticket in self.positions:
                    del self.positions[ticket]
                await self._delete_position_from_db(ticket)
                removed += 1
            
            if removed > 0:
                logger.info(f"[STARTUP-CLEANUP] Removed {removed} stale position record(s) from DB")
        except Exception as e:
            logger.error(f"Error cleaning up stale DB records: {e}")
        
        return removed
    
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
            
            # Also get pending orders to avoid falsely closing them
            pending_tickets = set()
            try:
                pending_orders = await mt5_client.get_orders()
                if pending_orders:
                    for order in pending_orders:
                        if isinstance(order, dict):
                            pending_tickets.add(order.get('ticket', 0))
                        else:
                            pending_tickets.add(getattr(order, 'ticket', 0))
            except Exception as e:
                logger.debug(f"Could not fetch pending orders for sync: {e}")
            
            # Combined: all tickets that are still alive in MT5 (positions + pending)
            all_mt5_tickets = mt5_tickets | pending_tickets
            
            closed_positions = []
            
            # Check for positions that closed (not in open positions AND not pending)
            for ticket in list(self.positions.keys()):
                if ticket not in all_mt5_tickets:
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
                    _mt5_time = getattr(mt5_pos, 'time', None)
                    if isinstance(_mt5_time, datetime):
                        _open_time = _mt5_time
                    elif isinstance(_mt5_time, (int, float)):
                        _open_time = datetime.fromtimestamp(_mt5_time)
                    else:
                        _open_time = datetime.now(timezone.utc)
                    position = Position(
                        ticket=ticket,
                        symbol=mt5_pos.symbol,
                        direction='long' if mt5_pos.type == 'buy' else 'short',
                        volume=mt5_pos.volume,
                        entry_price=mt5_pos.price_open,
                        stop_loss=mt5_pos.sl,
                        take_profit=mt5_pos.tp,
                        open_time=_open_time,
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
                'closed': closed_positions,
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
            from ..config import calculate_pl
            if pos.direction == 'long':
                pos.unrealized_pnl = calculate_pl(pos.symbol, current_price - pos.entry_price, pos.volume)
            else:
                pos.unrealized_pnl = calculate_pl(pos.symbol, pos.entry_price - current_price, pos.volume)
    
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
            
            # Update peak profit tracking before any management decisions
            r_multiple = position.current_r_multiple
            if r_multiple > position.peak_r_multiple:
                position.peak_r_multiple = r_multiple
                position.peak_unrealized_pnl = position.unrealized_pnl
                self._schedule_persist(position)
            
            # Check for management actions
            action = await self._manage_position(position)
            if action:
                actions.append(action)
        
        return actions
    
    async def _manage_position(self, position: Position) -> Optional[Dict[str, Any]]:
        """
        Apply multi-TP management rules to a single position.
        
        Priority order:
          0. Aggressive profit protection (giveback + near-TP reversal detection)
          1. TP1 hit (1.0R) -> Partial close 40% + move to break-even
          1.5. Dynamic SL trailing between 1R-2R (fills the dead zone)
          2. TP2 hit (2.0R) -> Partial close 30% of original
          3. Trailing stop on runner (remaining 30%)
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
        
        # =============================================
        # AGGRESSIVE PROFIT PROTECTION (before TP stages)
        # Only active after BE triggered (position was in profit)
        # =============================================
        protection_action = await self._check_profit_protection(position, r_multiple)
        if protection_action:
            return protection_action
        
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
                if self._skip_tp1_partial(position):
                    logger.info(
                        f"RUNNER: Skipping TP1 partial on {position.ticket} ({position.symbol}) "
                        f"trade_type={position.trade_type} a_plus={getattr(position, 'a_plus', False)}"
                    )
                    position.tp1_hit = True
                    await self._persist_and_wait(position)
                    return await self._move_to_break_even(position)
                if can_partial:
                    return await self._execute_tp1(position)
                else:
                    # Micro position: just move to break-even, no partial close
                    logger.info(
                        f"TP1 HIT on {position.ticket} ({position.symbol}): "
                        f"Position too small for partial ({position.volume} lots) - moving to break-even only"
                    )
                    position.tp1_hit = True
                    await self._persist_and_wait(position)
                    return await self._move_to_break_even(position)
        
        # Stage 1.5: Dynamic SL trailing between 1R and 2R (fills the dead zone)
        dynamic_trail_action = await self._dynamic_trail_1r_to_2r(position, r_multiple)
        if dynamic_trail_action:
            return dynamic_trail_action
        
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
                    await self._persist_and_wait(position)
                    return await self._update_trailing_stop(position)
        
        # Stage 3: Trailing stop on runner (applies to all position sizes)
        if position.tp2_hit and r_multiple >= self.trailing_start_r:
            return await self._update_trailing_stop(position)
        
        return None
    
    async def _check_profit_protection(self, position: Position, r_multiple: float) -> Optional[Dict[str, Any]]:
        """
        Aggressive profit protection — detect reversals and protect gains.
        
        Two triggers:
        1. GIVEBACK: If peak R >= giveback_min_peak_r and current R gave back 55%+ from peak, auto-close.
        2. NEAR-TP REVERSAL: If price reached 85%+ of TP distance then pulls back to 50% of peak R.
        """
        peak_r = position.peak_r_multiple
        
        # Only activate protection when position reached meaningful peak R
        if peak_r < self.giveback_min_peak_r:
            return None
        
        # Must have break-even triggered (we're past 1R management)
        if not position.be_triggered:
            return None
        
        # --- Near-TP tracking ---
        # Calculate how far TP is in R terms
        if position.risk_pips > 0 and position.take_profit > 0:
            if position.direction == 'long':
                tp_distance = position.take_profit - position.entry_price
            else:
                tp_distance = position.entry_price - position.take_profit
            tp_r_multiple = tp_distance / position.risk_pips if position.risk_pips > 0 else 0
            
            # Mark near-TP reached if we hit 85%+ of the TP R distance
            if tp_r_multiple > 0 and peak_r >= 0.85 * tp_r_multiple:
                if not position.near_tp_reached:
                    position.near_tp_reached = True
                    self._schedule_persist(position)
                    logger.info(
                        f"[PROFIT-PROTECT] {position.ticket} ({position.symbol}): "
                        f"Near-TP reached! Peak {peak_r:.2f}R >= 85% of TP ({tp_r_multiple:.2f}R)"
                    )
        
        # --- Near-TP Reversal Auto-Close ---
        _is_crypto = any(c in position.symbol.upper() for c in ['BTC', 'ETH', 'XRP', 'SOL', 'ADA', 'DOGE'])
        _near_tp_giveback = 0.70 if _is_crypto else 0.60
        if position.near_tp_reached and peak_r > 0:
            giveback_from_peak = (peak_r - r_multiple) / peak_r if peak_r > 0 else 0
            if giveback_from_peak >= _near_tp_giveback and r_multiple > 0:
                logger.warning(
                    f"[PROFIT-PROTECT] NEAR-TP REVERSAL on {position.ticket} ({position.symbol}): "
                    f"Peaked at {peak_r:.2f}R (near TP), now at {r_multiple:.2f}R "
                    f"(gave back {giveback_from_peak:.0%}). Auto-closing to protect profit."
                )
                position.close_reason = "near_tp_reversal"
                return await self._protection_close(position, "near_tp_reversal")
        
        _giveback_threshold = 0.65 if _is_crypto else 0.55
        if peak_r >= self.giveback_min_peak_r and r_multiple > 0:
            giveback_pct = (peak_r - r_multiple) / peak_r
            if giveback_pct >= _giveback_threshold:
                logger.warning(
                    f"[PROFIT-PROTECT] GIVEBACK CLOSE on {position.ticket} ({position.symbol}): "
                    f"Peaked at {peak_r:.2f}R, now at {r_multiple:.2f}R "
                    f"(gave back {giveback_pct:.0%} >= {_giveback_threshold:.0%}). Auto-closing."
                )
                position.close_reason = "giveback_protection"
                return await self._protection_close(position, "giveback_protection")
        
        return None
    
    async def _protection_close(self, position: Position, reason: str) -> Dict[str, Any]:
        """
        Close an entire position due to profit protection trigger.
        Sets close_reason and fires the reversal callback.
        """
        result_action = {
            "action": f"protection_close_{reason}",
            "ticket": position.ticket,
            "symbol": position.symbol,
            "direction": position.direction,
            "peak_r": position.peak_r_multiple,
            "current_r": position.current_r_multiple,
            "close_reason": reason,
        }
        
        if self.order_manager:
            result = await self.order_manager.close_position(
                ticket=position.ticket,
                volume=position.volume  # Close full remaining volume
            )
            
            if result.success:
                logger.info(
                    f"[PROFIT-PROTECT] Successfully closed {position.ticket} ({position.symbol}) "
                    f"— reason: {reason}, volume: {position.volume}"
                )
                result_action["success"] = True
                
                self.remove_position(position.ticket)
                
                if self.on_position_close:
                    try:
                        await self.on_position_close(position)
                    except Exception as e:
                        logger.error(f"Error in position close callback for protection close: {e}")
                
                if self.on_reversal_close:
                    try:
                        await self.on_reversal_close(position)
                    except Exception as e:
                        logger.error(f"Error in reversal close callback: {e}")
            else:
                logger.warning(
                    f"[PROFIT-PROTECT] Failed to close {position.ticket}: "
                    f"{getattr(result, 'message', 'unknown error')}"
                )
                result_action["success"] = False
                position.close_reason = ""  # Reset since close failed
        else:
            result_action["success"] = False
            position.close_reason = ""
        
        return result_action
    
    async def _dynamic_trail_1r_to_2r(self, position: Position, r_multiple: float) -> Optional[Dict[str, Any]]:
        """
        Dynamic SL trailing between 1R and 2R — fills the dead zone.
        
        After TP1 hit and BE triggered, instead of leaving SL frozen at break-even
        until 2.0R, progressively lock 50% of profit above 1R.
        
        Formula: locked_profit_r = (r_multiple - 1.0) * 0.5
        At 1.5R -> SL locks 0.25R of profit (SL at entry + 0.25R)
        At 1.75R -> SL locks 0.375R  
        At 2.0R -> SL locks 0.5R (then standard trailing takes over)
        """
        if not (position.tp1_hit and position.be_triggered and not position.tp2_hit):
            return None
        
        if r_multiple <= 1.0:
            return None
        
        locked_profit_r = (r_multiple - 1.0) * 0.5
        
        if position.direction == 'long':
            new_sl = position.entry_price + locked_profit_r * position.risk_pips
            if new_sl <= position.stop_loss:
                return None  # SL hasn't improved
        else:
            new_sl = position.entry_price - locked_profit_r * position.risk_pips
            if new_sl >= position.stop_loss:
                return None  # SL hasn't improved
        
        # Check spread before modifying
        if self.order_manager and hasattr(self.order_manager, '_check_spread'):
            spread_ok, current_spread, max_spread = await self.order_manager._check_spread(position.symbol)
            if not spread_ok:
                return None  # Skip this cycle, will retry
        
        if self.order_manager:
            result = await self.order_manager.modify_order(
                ticket=position.ticket,
                stop_loss=new_sl
            )
            
            if result.success:
                old_sl = position.stop_loss
                position.stop_loss = new_sl
                self._schedule_persist(position)
                logger.info(
                    f"[DYNAMIC-TRAIL] {position.ticket} ({position.symbol}): "
                    f"SL {old_sl:.5f} -> {new_sl:.5f} (locking {locked_profit_r:.2f}R at {r_multiple:.2f}R)"
                )
                return {
                    "action": "dynamic_trail_1r_2r",
                    "ticket": position.ticket,
                    "old_sl": old_sl,
                    "new_sl": new_sl,
                    "locked_r": locked_profit_r,
                    "current_r": r_multiple,
                }
        
        return None
    
    async def _execute_tp1(self, position: Position) -> Dict[str, Any]:
        """
        Execute TP1: Close 40% of position and move SL to break-even.
        """
        from ..config import normalize_lots, get_symbol_spec
        _vol_min = get_symbol_spec(position.symbol).volume_min
        close_volume = normalize_lots(position.symbol, position.initial_volume * self.tp1_close_percent)
        
        # Don't close more than what's open — ALWAYS leave at least volume_min for runner
        if close_volume >= position.volume:
            remaining_after_close = normalize_lots(position.symbol, position.volume - _vol_min)
            if remaining_after_close >= _vol_min:
                close_volume = remaining_after_close
            else:
                close_volume = _vol_min  # Minimum possible close
        
        logger.info(
            f"TP1 HIT on {position.ticket} ({position.symbol}): "
            f"Closing {close_volume} lots ({self.tp1_close_percent*100:.0f}%) + moving to break-even"
        )
        
        if self.order_manager:
            old_volume = position.volume
            old_tp1_hit = position.tp1_hit
            old_partial_closed = position.partial_closed
            position.volume = round(position.volume - close_volume, 2)
            position.tp1_hit = True
            position.partial_closed = True
            try:
                await self._persist_and_wait(position)
            except Exception:
                position.volume = old_volume
                position.tp1_hit = old_tp1_hit
                position.partial_closed = old_partial_closed
                raise

            # First: partial close
            result = await self.order_manager.close_position(
                ticket=position.ticket,
                volume=close_volume
            )
            
            if result.success:
                _is_crypto_be = any(c in position.symbol.upper() for c in ['BTC', 'ETH', 'XRP', 'SOL', 'ADA', 'DOGE'])
                buffer = position.risk_pips * (0.30 if _is_crypto_be else 0.25)
                if position.direction == 'long':
                    new_sl = position.entry_price + buffer
                else:
                    new_sl = position.entry_price - buffer

                old_sl = position.stop_loss
                old_be_triggered = position.be_triggered
                old_status = position.status
                position.stop_loss = new_sl
                position.be_triggered = True
                position.status = PositionStatus.BREAK_EVEN
                try:
                    await self._persist_and_wait(position)
                except Exception:
                    position.stop_loss = old_sl
                    position.be_triggered = old_be_triggered
                    position.status = old_status
                    raise
                
                be_result = await self.order_manager.modify_order(
                    ticket=position.ticket,
                    stop_loss=new_sl
                )
                
                if be_result.success:
                    logger.info(f"  Break-even set at {new_sl:.5f}")
                else:
                    position.stop_loss = old_sl
                    position.be_triggered = old_be_triggered
                    position.status = old_status
                    await self._persist_and_wait(position)
                    logger.warning(
                        f"  Break-even modification FAILED for {position.ticket}: "
                        f"{be_result.message if hasattr(be_result, 'message') else 'unknown error'} - will retry next cycle"
                    )
            else:
                position.volume = old_volume
                position.tp1_hit = old_tp1_hit
                position.partial_closed = old_partial_closed
                await self._persist_and_wait(position)
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
        from ..config import normalize_lots, get_symbol_spec
        _vol_min = get_symbol_spec(position.symbol).volume_min
        close_volume = normalize_lots(position.symbol, position.initial_volume * self.tp2_close_percent)
        
        # Don't close more than what's open — ALWAYS leave at least volume_min runner
        if close_volume >= position.volume:
            remaining_after_close = normalize_lots(position.symbol, position.volume - _vol_min)
            if remaining_after_close >= _vol_min:
                close_volume = remaining_after_close
            else:
                # Position too small to split — skip TP2 partial, just activate trailing
                logger.info(
                    f"TP2: Position {position.ticket} too small to split ({position.volume} lots) "
                    f"— skipping partial close, activating trailing stop"
                )
                position.tp2_hit = True
                position.status = PositionStatus.TRAILING
                await self._persist_and_wait(position)
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
            old_volume = position.volume
            old_tp2_hit = position.tp2_hit
            old_status = position.status
            position.volume = round(position.volume - close_volume, 2)
            position.tp2_hit = True
            position.status = PositionStatus.TRAILING
            try:
                await self._persist_and_wait(position)
            except Exception:
                position.volume = old_volume
                position.tp2_hit = old_tp2_hit
                position.status = old_status
                raise

            result = await self.order_manager.close_position(
                ticket=position.ticket,
                volume=close_volume
            )
            
            if result.success:
                logger.info(f"  Runner remaining: {position.volume} lots")
            else:
                position.volume = old_volume
                position.tp2_hit = old_tp2_hit
                position.status = old_status
                await self._persist_and_wait(position)
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
        
        _is_crypto_be2 = any(c in position.symbol.upper() for c in ['BTC', 'ETH', 'XRP', 'SOL', 'ADA', 'DOGE'])
        buffer = position.risk_pips * (0.30 if _is_crypto_be2 else 0.25)
        
        if position.direction == 'long':
            new_sl = position.entry_price + buffer
        else:
            new_sl = position.entry_price - buffer
        
        if self.order_manager:
            old_sl = position.stop_loss
            old_be_triggered = position.be_triggered
            old_status = position.status
            position.stop_loss = new_sl
            position.be_triggered = True
            position.status = PositionStatus.BREAK_EVEN
            try:
                await self._persist_and_wait(position)
            except Exception:
                position.stop_loss = old_sl
                position.be_triggered = old_be_triggered
                position.status = old_status
                raise

            result = await self.order_manager.modify_order(
                ticket=position.ticket,
                stop_loss=new_sl
            )
            
            if result.success:
                return {
                    "action": "break_even",
                    "success": True,
                    "ticket": position.ticket,
                    "new_sl": new_sl
                }
            else:
                position.stop_loss = old_sl
                position.be_triggered = old_be_triggered
                position.status = old_status
                await self._persist_and_wait(position)
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
            old_sl = position.stop_loss
            old_trailing_active = position.trailing_active
            old_status = position.status
            position.stop_loss = new_sl
            position.trailing_active = True
            position.status = PositionStatus.TRAILING
            try:
                await self._persist_and_wait(position)
            except Exception:
                position.stop_loss = old_sl
                position.trailing_active = old_trailing_active
                position.status = old_status
                raise

            result = await self.order_manager.modify_order(
                ticket=position.ticket,
                stop_loss=new_sl
            )
            
            if not result.success:
                position.stop_loss = old_sl
                position.trailing_active = old_trailing_active
                position.status = old_status
                await self._persist_and_wait(position)
        
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
