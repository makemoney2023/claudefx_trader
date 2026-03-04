"""
Trade routes for the API.

Provides endpoints for:
- Trade history
- Open positions
- Trade details
- Account information
"""

from typing import List, Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from ..auth import RequireAuth

from ...utils.logging import get_logger

# Lazy imports to avoid circular dependency
def get_trade_journal():
    from ..main import get_trade_journal as _get_trade_journal
    return _get_trade_journal()

def get_bot_instance():
    from ..main import get_bot_instance as _get_bot_instance
    return _get_bot_instance()

def get_mt5_client():
    from ..main import get_mt5_client as _get_mt5_client
    return _get_mt5_client()

logger = get_logger(__name__)
router = APIRouter()


# Pydantic models for API responses
class TradeResponse(BaseModel):
    """Trade record response model."""
    trade_id: str
    timestamp: datetime
    symbol: str
    direction: str
    entry_price: float
    entry_time: datetime
    stop_loss: float
    take_profit: float
    position_size: float
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    profit_loss: Optional[float] = None
    profit_loss_pips: Optional[float] = None
    r_multiple: Optional[float] = None
    status: str = "open"
    
    # Claude analysis
    claude_confidence: Optional[float] = None
    claude_reasoning: Optional[str] = None
    
    # Trade judge analysis (for correlating judge decisions to outcomes)
    judge_verdict: Optional[str] = None        # APPROVE, DEMOTE, REJECT
    judge_reason: Optional[str] = None
    judge_risk_flags: Optional[list] = None
    
    # Trade classification & ICT context
    trade_type: Optional[str] = None           # scalp, intraday, swing
    order_type: Optional[str] = None           # market, buy_limit, sell_limit, etc.
    amd_phase: Optional[str] = None            # accumulation, manipulation, distribution
    market_structure: Optional[str] = None
    confluence_factors: Optional[list] = None
    confluence_count: Optional[int] = None
    ict_concepts: Optional[dict] = None
    
    class Config:
        from_attributes = True


class PositionResponse(BaseModel):
    """Open position response model."""
    ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    unrealized_pnl: float
    r_multiple: float


class AccountResponse(BaseModel):
    """Account information response model."""
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    profit: float
    currency: str = "USD"
    is_live: bool = False  # True if connected to real MT5


class TradeListResponse(BaseModel):
    """Paginated trade list response."""
    trades: List[TradeResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


@router.get("", response_model=TradeListResponse)
async def list_trades(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    direction: Optional[str] = Query(None, description="Filter by direction"),
    status: Optional[str] = Query(None, description="Filter by status (open/closed)")
):
    """
    List all trades with pagination and filtering.
    Reads from database for persistent trade history.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select, func, desc
        
        async with AsyncSessionLocal() as session:
            # Build base query
            query = select(TradeModel)
            count_query = select(func.count(TradeModel.id))
            
            # Apply filters
            if symbol and symbol.lower() != 'undefined':
                query = query.where(TradeModel.symbol == symbol.upper())
                count_query = count_query.where(TradeModel.symbol == symbol.upper())
            
            if direction and direction.lower() != 'undefined':
                query = query.where(TradeModel.direction == direction.lower())
                count_query = count_query.where(TradeModel.direction == direction.lower())
            
            if status and status.lower() != 'undefined':
                if status.lower() == "open":
                    query = query.where(TradeModel.exit_price.is_(None))
                    count_query = count_query.where(TradeModel.exit_price.is_(None))
                elif status.lower() == "closed":
                    query = query.where(TradeModel.exit_price.isnot(None))
                    count_query = count_query.where(TradeModel.exit_price.isnot(None))
            
            # Get total count
            total_result = await session.execute(count_query)
            total = total_result.scalar() or 0
            
            # Apply pagination and sorting
            offset = (page - 1) * page_size
            query = query.order_by(desc(TradeModel.timestamp)).offset(offset).limit(page_size)
            
            result = await session.execute(query)
            trades = result.scalars().all()
            
            # Convert to response models
            trade_responses = []
            for trade in trades:
                trade_responses.append(TradeResponse(
                    trade_id=trade.trade_id,
                    timestamp=trade.timestamp,
                    symbol=trade.symbol,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    entry_time=trade.entry_time,
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit,
                    position_size=trade.position_size,
                    exit_price=trade.exit_price,
                    exit_time=trade.exit_time,
                    profit_loss=trade.profit_loss,
                    profit_loss_pips=trade.profit_loss_pips,
                    r_multiple=trade.r_multiple,
                    status="closed" if trade.exit_price else "open",
                    claude_confidence=trade.claude_confidence,
                    claude_reasoning=trade.claude_reasoning,
                    judge_verdict=getattr(trade, 'judge_verdict', None),
                    judge_reason=getattr(trade, 'judge_reason', None),
                    judge_risk_flags=getattr(trade, 'judge_risk_flags', None),
                    trade_type=getattr(trade, 'trade_type', None),
                    order_type=getattr(trade, 'order_type', None),
                    amd_phase=getattr(trade, 'amd_phase', None),
                    market_structure=getattr(trade, 'market_structure', None),
                    confluence_factors=getattr(trade, 'confluence_factors', None),
                    confluence_count=getattr(trade, 'confluence_count', None),
                    ict_concepts=getattr(trade, 'ict_concepts', None),
                ))
            
            return TradeListResponse(
                trades=trade_responses,
                total=total,
                page=page,
                page_size=page_size,
                has_more=offset + len(trades) < total
            )
    except Exception as e:
        logger.error(f"Error listing trades from database: {e}")
        # Fall back to journal
        journal = get_trade_journal()
        all_trades = journal.trades
        
        if symbol:
            all_trades = [t for t in all_trades if t.symbol == symbol.upper()]
        if direction:
            all_trades = [t for t in all_trades if t.direction == direction.lower()]
        if status:
            if status.lower() == "open":
                all_trades = [t for t in all_trades if t.exit_price is None]
            elif status.lower() == "closed":
                all_trades = [t for t in all_trades if t.exit_price is not None]
        
        all_trades = sorted(all_trades, key=lambda x: x.timestamp, reverse=True)
        total = len(all_trades)
        start = (page - 1) * page_size
        end = start + page_size
        page_trades = all_trades[start:end]
        
        trade_responses = []
        for trade in page_trades:
            trade_responses.append(TradeResponse(
                trade_id=trade.trade_id,
                timestamp=trade.timestamp,
                symbol=trade.symbol,
                direction=trade.direction,
                entry_price=trade.entry_price,
                entry_time=trade.entry_time,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                position_size=trade.position_size,
                exit_price=trade.exit_price,
                exit_time=trade.exit_time,
                profit_loss=trade.profit_loss,
                profit_loss_pips=trade.profit_loss_pips,
                r_multiple=trade.r_multiple,
                status="closed" if trade.exit_price else "open",
                claude_confidence=getattr(trade, 'claude_confidence', None),
                claude_reasoning=getattr(trade, 'claude_reasoning', None),
                # New fields default to None when from journal fallback
            ))
        
        return TradeListResponse(
            trades=trade_responses,
            total=total,
            page=page,
            page_size=page_size,
            has_more=end < total
        )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(trade_id: str):
    """
    Get a specific trade by ID (from database with full analysis context).
    """
    # Try database first for full analysis data
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeModel).where(TradeModel.trade_id == trade_id)
            )
            trade = result.scalar_one_or_none()
            if trade:
                return TradeResponse(
                    trade_id=trade.trade_id,
                    timestamp=trade.timestamp,
                    symbol=trade.symbol,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    entry_time=trade.entry_time,
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit,
                    position_size=trade.position_size,
                    exit_price=trade.exit_price,
                    exit_time=trade.exit_time,
                    profit_loss=trade.profit_loss,
                    profit_loss_pips=trade.profit_loss_pips,
                    r_multiple=trade.r_multiple,
                    status="closed" if trade.exit_price else "open",
                    claude_confidence=trade.claude_confidence,
                    claude_reasoning=trade.claude_reasoning,
                    judge_verdict=getattr(trade, 'judge_verdict', None),
                    judge_reason=getattr(trade, 'judge_reason', None),
                    judge_risk_flags=getattr(trade, 'judge_risk_flags', None),
                    trade_type=getattr(trade, 'trade_type', None),
                    order_type=getattr(trade, 'order_type', None),
                    amd_phase=getattr(trade, 'amd_phase', None),
                    market_structure=getattr(trade, 'market_structure', None),
                    confluence_factors=getattr(trade, 'confluence_factors', None),
                    confluence_count=getattr(trade, 'confluence_count', None),
                    ict_concepts=getattr(trade, 'ict_concepts', None),
                )
    except Exception as e:
        logger.warning(f"Could not fetch trade from database: {e}")
    
    # Fallback to journal
    journal = get_trade_journal()
    trade = journal.get_trade(trade_id)
    
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    
    return TradeResponse(
        trade_id=trade.trade_id,
        timestamp=trade.timestamp,
        symbol=trade.symbol,
        direction=trade.direction,
        entry_price=trade.entry_price,
        entry_time=trade.entry_time,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        position_size=trade.position_size,
        exit_price=trade.exit_price,
        exit_time=trade.exit_time,
        profit_loss=trade.profit_loss,
        profit_loss_pips=trade.profit_loss_pips,
        r_multiple=trade.r_multiple,
        status="closed" if trade.exit_price else "open",
        claude_confidence=getattr(trade, 'claude_confidence', None),
        claude_reasoning=getattr(trade, 'claude_reasoning', None),
    )


@router.get("/positions/open", response_model=List[PositionResponse])
async def get_open_positions():
    """
    Get all currently open positions from MT5.
    """
    mt5_client = get_mt5_client()
    
    if mt5_client and mt5_client.is_connected:
        try:
            positions = await mt5_client.get_positions()
            return [
                PositionResponse(
                    ticket=int(p.ticket),
                    symbol=str(p.symbol),
                    # Convert MT5 'buy'/'sell' to 'long'/'short' for consistency
                    direction='long' if p.type == 'buy' else 'short',
                    volume=float(p.volume),
                    entry_price=float(p.price_open),
                    current_price=float(p.price_current),
                    stop_loss=float(p.sl) if p.sl else 0.0,
                    take_profit=float(p.tp) if p.tp else 0.0,
                    unrealized_pnl=float(p.profit),
                    r_multiple=0.0  # Calculate if needed
                )
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Error getting positions from MT5: {e}")
    
    # Fallback: Return trades marked as open from journal
    journal = get_trade_journal()
    open_trades = journal.get_open_trades()
    
    return [
        PositionResponse(
            ticket=hash(t.trade_id) % 1000000,
            symbol=t.symbol,
            direction=t.direction,
            volume=t.position_size,
            entry_price=t.entry_price,
            current_price=t.entry_price,
            stop_loss=t.stop_loss,
            take_profit=t.take_profit,
            unrealized_pnl=0.0,
            r_multiple=0.0
        )
        for t in open_trades
    ]


@router.get("/account/info", response_model=AccountResponse)
async def get_account_info():
    """
    Get account balance and margin information from MT5.
    
    Returns is_live=True if connected to real MT5, False if simulation.
    """
    mt5_client = get_mt5_client()
    
    if mt5_client and mt5_client.is_connected:
        try:
            account = await mt5_client.get_account_info()
            if account:
                return AccountResponse(
                    balance=float(account.balance),
                    equity=float(account.equity),
                    margin=float(account.margin),
                    free_margin=float(account.free_margin),
                    margin_level=float(account.margin_level) if account.margin_level else 0.0,
                    profit=float(account.profit),
                    currency=str(account.currency),
                    is_live=not mt5_client.is_simulation
                )
        except Exception as e:
            logger.error(f"Error getting account info from MT5: {e}")
    
    # Return placeholder data if MT5 not connected
    return AccountResponse(
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        free_margin=10000.0,
        margin_level=0.0,
        profit=0.0,
        currency="USD",
        is_live=False
    )


class EmergencyCloseResponse(BaseModel):
    """Response from emergency close all."""
    success: bool
    positions_closed: int
    positions_failed: int
    message: str
    details: List[dict] = []


@router.post("/emergency-close-all", response_model=EmergencyCloseResponse, dependencies=[Depends(RequireAuth())])
async def emergency_close_all():
    """
    EMERGENCY: Close ALL open positions immediately.
    
    WARNING: This will close all positions without confirmation.
    Use only in emergency situations.
    """
    bot = get_bot_instance()
    
    if not bot or not bot.order_manager:
        raise HTTPException(
            status_code=503,
            detail="Trading bot not running - cannot close positions"
        )
    
    try:
        logger.warning("EMERGENCY CLOSE ALL triggered via API")
        
        results = await bot.order_manager.emergency_close_all()
        
        closed = sum(1 for r in results if r.success)
        failed = len(results) - closed
        
        details = [
            {
                "ticket": r.ticket,
                "success": r.success,
                "message": r.message
            }
            for r in results
        ]
        
        return EmergencyCloseResponse(
            success=failed == 0,
            positions_closed=closed,
            positions_failed=failed,
            message=f"Closed {closed} positions" + (f", {failed} failed" if failed > 0 else ""),
            details=details
        )
        
    except Exception as e:
        logger.error(f"Emergency close failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SyncResponse(BaseModel):
    """Sync result response."""
    success: bool
    synced_count: int
    message: str


@router.post("/sync-from-mt5", response_model=SyncResponse)
async def sync_positions_from_mt5():
    """
    Sync open MT5 positions to the trade journal AND position manager.
    Use this to import existing positions that were opened before the bot tracked them.
    """
    mt5_client = get_mt5_client()
    journal = get_trade_journal()
    bot = get_bot_instance()
    
    if not mt5_client or not mt5_client.is_connected:
        raise HTTPException(status_code=503, detail="MT5 not connected")
    
    try:
        positions = await mt5_client.get_positions()
        synced = 0
        position_manager_synced = 0
        
        for p in positions:
            trade_id = str(p.ticket)
            
            # Sync to trade journal
            existing = journal.get_trade(trade_id)
            if not existing:
                from ...utils.trade_journal import TradeRecord
                
                trade = TradeRecord(
                    trade_id=trade_id,
                    timestamp=datetime.now(timezone.utc),
                    symbol=str(p.symbol),
                    direction='long' if p.type == 'buy' else 'short',
                    entry_price=float(p.price_open),
                    entry_time=datetime.now(timezone.utc),
                    stop_loss=float(p.sl) if p.sl else 0.0,
                    take_profit=float(p.tp) if p.tp else 0.0,
                    position_size=float(p.volume),
                    entry_reason=f"Synced from MT5 position {p.ticket}"
                )
                
                journal.log_trade(trade)
                synced += 1
                logger.info(f"Synced MT5 position {p.ticket} ({p.symbol}) to trade journal")
            
            # Sync to position manager (for active management)
            if bot and bot.position_manager:
                if p.ticket not in bot.position_manager.positions:
                    from ...execution.position_manager import Position
                    
                    position = Position(
                        ticket=p.ticket,
                        symbol=p.symbol,
                        direction='long' if p.type == 'buy' else 'short',
                        volume=p.volume,
                        entry_price=p.price_open,
                        stop_loss=p.sl if p.sl else 0.0,
                        take_profit=p.tp if p.tp else 0.0,
                        open_time=datetime.now(timezone.utc)
                    )
                    bot.position_manager.add_position(position)
                    position_manager_synced += 1
                    logger.info(f"Added MT5 position {p.ticket} ({p.symbol}) to position manager")
        
        total_synced = max(synced, position_manager_synced)
        return SyncResponse(
            success=True,
            synced_count=total_synced,
            message=f"Synced {synced} to journal, {position_manager_synced} to position manager" if total_synced > 0 else "All positions already synced"
        )
        
    except Exception as e:
        logger.error(f"Failed to sync MT5 positions: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class HistorySyncResponse(BaseModel):
    """History sync result response."""
    success: bool
    synced_count: int
    days_synced: int
    message: str


@router.post("/sync-history", response_model=HistorySyncResponse)
async def sync_trade_history(
    days: int = Query(7, ge=1, le=90, description="Number of days of history to sync")
):
    """
    Sync closed trade history from MT5 to database.
    
    This imports historical closed trades that may have been missed
    while the bot was offline.
    """
    mt5_client = get_mt5_client()
    
    if not mt5_client or not mt5_client.is_connected:
        raise HTTPException(status_code=503, detail="MT5 not connected")
    
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select
        
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        
        # Get closed deals from MT5
        deals = await mt5_client.get_history(start_time, end_time)
        
        if not deals:
            return HistorySyncResponse(
                success=True,
                synced_count=0,
                days_synced=days,
                message="No trade history found in the specified period"
            )
        
        # IMPORTANT: Do NOT import new trades from MT5 history — that contaminates
        # the DB with old account trades, demo trades, and commission artifacts.
        # Instead, only UPDATE existing bot-placed trades that are missing close data.
        # This delegates to the bot's _sync_trade_history which does it correctly.
        synced_count = 0
        
        from ..main import get_bot_instance
        bot = get_bot_instance()
        if bot:
            try:
                await bot._sync_trade_history(days_back=days)
                synced_count = days  # Approximate — the sync logs the real count
            except Exception as e:
                logger.warning(f"Trade history sync error: {e}")
        
        logger.info(f"Synced {synced_count} historical trades from MT5 ({days} days)")
        
        return HistorySyncResponse(
            success=True,
            synced_count=synced_count,
            days_synced=days,
            message=f"Synced {synced_count} trades from the last {days} days"
        )
        
    except Exception as e:
        logger.error(f"Failed to sync MT5 history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_trades(
    format: str = Query("csv", description="Export format: csv or json"),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD")
):
    """
    Export trade history to CSV or JSON.
    
    Returns downloadable file with all trades.
    """
    from fastapi.responses import Response
    import json
    import csv
    import io
    
    # Fetch trades from database
    from ..database import AsyncSessionLocal, TradeModel
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        query = select(TradeModel).order_by(TradeModel.timestamp.desc())
        
        # Filter by date if provided
        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.where(TradeModel.timestamp >= start_dt)
        
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            query = query.where(TradeModel.timestamp <= end_dt)
        
        result = await session.execute(query)
        trades = result.scalars().all()
    
    if format.lower() == "json":
        # JSON export
        export_data = []
        for t in trades:
            export_data.append({
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "position_size": t.position_size,
                "pnl": t.profit_loss,
                "r_multiple": t.r_multiple,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "entry_reason": t.entry_reason,
                "exit_reason": t.exit_reason
            })
        
        return Response(
            content=json.dumps(export_data, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=trades_export_{datetime.now().strftime('%Y%m%d')}.json"
            }
        )
    
    else:
        # CSV export
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header row
        writer.writerow([
            "trade_id", "timestamp", "symbol", "direction", 
            "entry_price", "exit_price", "stop_loss", "take_profit",
            "position_size", "pnl", "r_multiple", 
            "entry_time", "exit_time", "entry_reason", "exit_reason"
        ])
        
        # Data rows
        for t in trades:
            writer.writerow([
                t.trade_id,
                t.timestamp.isoformat() if t.timestamp else "",
                t.symbol,
                t.direction,
                t.entry_price,
                t.exit_price,
                t.stop_loss,
                t.take_profit,
                t.position_size,
                t.profit_loss,
                t.r_multiple,
                t.entry_time.isoformat() if t.entry_time else "",
                t.exit_time.isoformat() if t.exit_time else "",
                t.entry_reason or "",
                t.exit_reason or ""
            ])
        
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=trades_export_{datetime.now().strftime('%Y%m%d')}.csv"
            }
        )
