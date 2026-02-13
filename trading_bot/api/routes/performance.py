"""
Performance routes for the API.

Provides endpoints for:
- Trading statistics
- Performance metrics
- Historical analysis
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...utils.logging import get_logger

# Lazy imports to avoid circular dependency
def get_trade_journal():
    from ..main import get_trade_journal as _get_trade_journal
    return _get_trade_journal()

def get_mt5_client():
    from ..main import get_mt5_client as _get_mt5_client
    return _get_mt5_client()

logger = get_logger(__name__)
router = APIRouter()


# Response Models
class PerformanceStats(BaseModel):
    """Overall performance statistics."""
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_profit: float
    total_r: float
    avg_r: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    largest_win: float
    largest_loss: float


class DailySummary(BaseModel):
    """Daily trading summary."""
    date: str
    trades_opened: int
    trades_closed: int
    daily_pnl: float
    daily_r: float


class ICTConceptStats(BaseModel):
    """Statistics for ICT concepts."""
    concept: str
    trades: int
    wins: int
    win_rate: float
    avg_r: float


class EquityPoint(BaseModel):
    """Equity curve data point."""
    timestamp: datetime
    equity: float
    drawdown: float


class PerformanceResponse(BaseModel):
    """Complete performance response."""
    stats: PerformanceStats
    daily_summaries: List[DailySummary]
    ict_concept_stats: List[ICTConceptStats]
    equity_curve: List[EquityPoint]


class AccountSummary(BaseModel):
    """Real-time account summary from MT5."""
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    profit: float
    currency: str
    is_live: bool  # True if connected to real MT5, False if simulation


@router.get("/account", response_model=AccountSummary)
async def get_account_summary():
    """
    Get real-time account summary from MT5.
    
    Returns current balance, equity, margin, and P&L.
    """
    mt5_client = get_mt5_client()
    
    if mt5_client and mt5_client.is_connected:
        try:
            account = await mt5_client.get_account_info()
            if account:
                return AccountSummary(
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
            logger.warning(f"Could not get account info: {e}")
    
    # Return simulation data
    return AccountSummary(
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        free_margin=10000.0,
        margin_level=0.0,
        profit=0.0,
        currency="USD",
        is_live=False
    )


@router.get("", response_model=PerformanceStats)
async def get_performance_stats(
    period_days: Optional[int] = Query(None, description="Limit to last N days")
):
    """
    Get overall trading performance statistics from database.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select, func
        
        async with AsyncSessionLocal() as session:
            # Build query for closed trades (those with profit_loss)
            query = select(TradeModel).where(TradeModel.profit_loss.isnot(None))
            
            if period_days:
                cutoff = datetime.utcnow() - timedelta(days=period_days)
                query = query.where(TradeModel.timestamp >= cutoff)
            
            result = await session.execute(query)
            trades = result.scalars().all()
            
            if not trades:
                return PerformanceStats(
                    total_trades=0, wins=0, losses=0, win_rate=0.0,
                    total_profit=0.0, total_r=0.0, avg_r=0.0,
                    avg_win=0.0, avg_loss=0.0, profit_factor=0.0,
                    largest_win=0.0, largest_loss=0.0
                )
            
            total_trades = len(trades)
            wins = [t for t in trades if t.profit_loss and t.profit_loss > 0]
            losses = [t for t in trades if t.profit_loss and t.profit_loss < 0]
            decided_trades = len(wins) + len(losses)  # Exclude breakeven/scratch trades from win rate
            
            total_profit = sum(t.profit_loss or 0 for t in trades)
            # Sanitize R-multiples: cap unreasonable values (bad SL data) to 0
            def _sanitize_r(r) -> float:
                val = r or 0.0
                return val if abs(val) <= 10 else 0.0
            total_r = sum(_sanitize_r(t.r_multiple) for t in trades)
            
            win_profits = [t.profit_loss for t in wins if t.profit_loss]
            loss_profits = [t.profit_loss for t in losses if t.profit_loss]
            
            return PerformanceStats(
                total_trades=total_trades,
                wins=len(wins),
                losses=len(losses),
                win_rate=(len(wins) / decided_trades) if decided_trades > 0 else 0.0,  # Return as decimal (0.52), frontend multiplies by 100 — excludes breakeven trades
                total_profit=total_profit,
                total_r=total_r,
                avg_r=(total_r / total_trades) if total_trades > 0 else 0.0,
                avg_win=(sum(win_profits) / len(win_profits)) if win_profits else 0.0,
                avg_loss=(sum(loss_profits) / len(loss_profits)) if loss_profits else 0.0,
                profit_factor=(sum(win_profits) / abs(sum(loss_profits))) if loss_profits and sum(loss_profits) != 0 else 0.0,
                largest_win=max(win_profits) if win_profits else 0.0,
                largest_loss=min(loss_profits) if loss_profits else 0.0
            )
    except Exception as e:
        logger.error(f"Error getting performance stats: {e}")
        # Fall back to journal
        journal = get_trade_journal()
        stats = journal.get_statistics(period_days)
        
        return PerformanceStats(
            total_trades=stats.get("total_trades", 0),
            wins=stats.get("wins", 0),
            losses=stats.get("losses", 0),
            win_rate=stats.get("win_rate", 0),
            total_profit=stats.get("total_profit", 0),
            total_r=stats.get("total_r", 0),
            avg_r=stats.get("avg_r", 0),
            avg_win=stats.get("avg_win", 0),
            avg_loss=stats.get("avg_loss", 0),
            profit_factor=stats.get("profit_factor", 0),
            largest_win=stats.get("largest_win", 0),
            largest_loss=stats.get("largest_loss", 0)
        )


@router.get("/daily", response_model=List[DailySummary])
async def get_daily_summaries(
    days: int = Query(30, ge=1, le=365, description="Number of days to include")
):
    """
    Get daily trading summaries.
    """
    journal = get_trade_journal()
    summaries = []
    
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        summary = journal.get_daily_summary(date)
        
        # Only include days with activity
        if summary["trades_opened"] > 0 or summary["trades_closed"] > 0:
            summaries.append(DailySummary(
                date=summary["date"],
                trades_opened=summary["trades_opened"],
                trades_closed=summary["trades_closed"],
                daily_pnl=summary["daily_pnl"],
                daily_r=summary["daily_r"]
            ))
    
    return summaries


@router.get("/ict-concepts", response_model=List[ICTConceptStats])
async def get_ict_concept_stats():
    """
    Get performance breakdown by ICT concept.
    """
    journal = get_trade_journal()
    stats = journal.get_statistics()
    
    ict_stats = stats.get("ict_concept_stats", {})
    
    return [
        ICTConceptStats(
            concept=concept,
            trades=data["trades"],
            wins=data["wins"],
            win_rate=data["win_rate"],
            avg_r=data["avg_r"]
        )
        for concept, data in ict_stats.items()
    ]


@router.get("/equity-curve", response_model=List[EquityPoint])
async def get_equity_curve(
    days: int = Query(90, ge=1, le=365, description="Number of days")
):
    """
    Get equity curve data for charting.
    
    Shows real-time account equity from MT5, plus historical equity
    based on closed trades from the journal.
    """
    # Get current account info from MT5
    mt5_client = get_mt5_client()
    current_equity = 10000.0  # Default fallback
    current_balance = 10000.0
    
    if mt5_client and mt5_client.is_connected:
        try:
            account = await mt5_client.get_account_info()
            if account:
                current_equity = float(account.equity)
                current_balance = float(account.balance)
        except Exception as e:
            logger.warning(f"Could not get account info: {e}")
    
    journal = get_trade_journal()
    closed_trades = journal.get_closed_trades()
    
    cutoff = datetime.now() - timedelta(days=days)
    
    # If no closed trades, just return current equity point
    if not closed_trades:
        return [
            EquityPoint(
                timestamp=cutoff,
                equity=current_balance,
                drawdown=0.0
            ),
            EquityPoint(
                timestamp=datetime.now(),
                equity=current_equity,
                drawdown=0.0 if current_equity >= current_balance else (current_balance - current_equity) / current_balance
            )
        ]
    
    # Sort by exit time
    sorted_trades = sorted(
        [t for t in closed_trades if t.exit_time],
        key=lambda x: x.exit_time
    )
    
    # Filter by date range
    sorted_trades = [t for t in sorted_trades if t.exit_time >= cutoff]
    
    # Calculate starting equity by working backwards from current balance
    total_pnl = sum(t.profit_loss or 0 for t in sorted_trades)
    starting_equity = current_balance - total_pnl
    
    equity = starting_equity
    peak_equity = equity
    
    equity_points = [
        EquityPoint(
            timestamp=cutoff,
            equity=starting_equity,
            drawdown=0.0
        )
    ]
    
    for trade in sorted_trades:
        equity += trade.profit_loss or 0
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        
        equity_points.append(EquityPoint(
            timestamp=trade.exit_time,
            equity=equity,
            drawdown=drawdown
        ))
    
    # Add current real-time equity point
    peak_equity = max(peak_equity, current_equity)
    current_drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0
    
    equity_points.append(EquityPoint(
        timestamp=datetime.now(),
        equity=current_equity,
        drawdown=current_drawdown
    ))
    
    return equity_points


@router.get("/report")
async def get_performance_report():
    """
    Get a text-based performance report.
    """
    journal = get_trade_journal()
    report = journal.generate_report()
    
    return {
        "report": report,
        "generated_at": datetime.now().isoformat()
    }


@router.get("/by-symbol")
async def get_performance_by_symbol():
    """
    Get performance breakdown by trading symbol.
    """
    journal = get_trade_journal()
    closed_trades = journal.get_closed_trades()
    
    symbol_stats: Dict[str, Dict[str, Any]] = {}
    
    for trade in closed_trades:
        symbol = trade.symbol
        
        if symbol not in symbol_stats:
            symbol_stats[symbol] = {
                "trades": 0,
                "wins": 0,
                "total_pnl": 0,
                "total_r": 0
            }
        
        symbol_stats[symbol]["trades"] += 1
        if trade.profit_loss and trade.profit_loss > 0:
            symbol_stats[symbol]["wins"] += 1
        symbol_stats[symbol]["total_pnl"] += trade.profit_loss or 0
        symbol_stats[symbol]["total_r"] += trade.r_multiple or 0
    
    # Calculate win rates
    result = []
    for symbol, data in symbol_stats.items():
        result.append({
            "symbol": symbol,
            "trades": data["trades"],
            "wins": data["wins"],
            "win_rate": data["wins"] / data["trades"] if data["trades"] > 0 else 0,
            "total_pnl": data["total_pnl"],
            "avg_r": data["total_r"] / data["trades"] if data["trades"] > 0 else 0
        })
    
    return sorted(result, key=lambda x: x["total_pnl"], reverse=True)


@router.get("/by-session")
async def get_performance_by_session():
    """
    Get performance breakdown by trading session.
    """
    journal = get_trade_journal()
    closed_trades = journal.get_closed_trades()
    
    session_stats: Dict[str, Dict[str, Any]] = {}
    
    for trade in closed_trades:
        session = trade.session or "unknown"
        
        if session not in session_stats:
            session_stats[session] = {
                "trades": 0,
                "wins": 0,
                "total_pnl": 0,
                "total_r": 0
            }
        
        session_stats[session]["trades"] += 1
        if trade.profit_loss and trade.profit_loss > 0:
            session_stats[session]["wins"] += 1
        session_stats[session]["total_pnl"] += trade.profit_loss or 0
        session_stats[session]["total_r"] += trade.r_multiple or 0
    
    result = []
    for session, data in session_stats.items():
        result.append({
            "session": session,
            "trades": data["trades"],
            "wins": data["wins"],
            "win_rate": data["wins"] / data["trades"] if data["trades"] > 0 else 0,
            "total_pnl": data["total_pnl"],
            "avg_r": data["total_r"] / data["trades"] if data["trades"] > 0 else 0
        })
    
    return sorted(result, key=lambda x: x["total_pnl"], reverse=True)
