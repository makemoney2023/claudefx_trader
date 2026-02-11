"""
API routes for the Claude learning system.

Provides endpoints for:
- Viewing trade learnings
- Viewing knowledge base
- Viewing weekly reports
- Manually triggering consolidation
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from ...services.trade_learning_service import TradeLearningService
from ...utils.logging import get_logger
from ..auth import RequireAuth

logger = get_logger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])

# Service instance - will be set by main.py via set_learning_service()
_learning_service: Optional[TradeLearningService] = None


def set_learning_service(service: TradeLearningService):
    """Set the learning service instance (called from main.py)."""
    global _learning_service
    _learning_service = service
    logger.info("Learning service registered with API")


def get_learning_service() -> TradeLearningService:
    """Get the learning service instance."""
    global _learning_service
    if _learning_service is None:
        _learning_service = TradeLearningService()
        logger.warning("Learning service created on-demand (not synced from bot)")
    return _learning_service


# ============================================================================
# Pydantic Models for Responses
# ============================================================================

class TradeLearningResponse(BaseModel):
    """Response model for a single trade learning."""
    id: int
    trade_id: str
    timestamp: str
    symbol: str
    direction: str
    session: str
    setup_type: str
    profit_loss: float
    r_multiple: float
    outcome: str
    grade: str
    analysis: str
    what_went_right: Optional[List[str]] = None
    what_went_wrong: Optional[List[str]] = None
    learnings: Optional[List[str]] = None
    would_take_again: bool


class KnowledgeEntryResponse(BaseModel):
    """Response model for a knowledge base entry."""
    category: str
    key: str
    insight: str
    confidence: float
    sample_size: int
    win_rate: float
    avg_r: float
    expires_at: str


class WeeklyReportResponse(BaseModel):
    """Response model for a weekly report."""
    week_start: str
    week_end: str
    performance_grade: str
    summary: str
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    total_r: float
    patterns_identified: Optional[List[str]] = None
    recurring_mistakes: Optional[List[str]] = None
    winning_patterns: Optional[List[str]] = None
    recommendations: Optional[List[str]] = None
    symbol_insights: Optional[Dict[str, str]] = None
    session_insights: Optional[Dict[str, str]] = None
    focus_area: str
    best_setup: str
    created_at: str


class LearningStatsResponse(BaseModel):
    """Response model for learning statistics."""
    total_learnings: int
    by_grade: Dict[str, int]
    by_outcome: Dict[str, int]
    by_symbol: Dict[str, int]
    recent_mistakes_count: int
    winning_patterns_count: int
    knowledge_entries: int


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("/recent", response_model=List[TradeLearningResponse])
async def get_recent_learnings(
    limit: int = Query(20, ge=1, le=100, description="Maximum learnings to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
):
    """
    Get recent trade learnings.
    
    Returns trade reviews with Claude's analysis, grades, and learnings.
    """
    try:
        service = get_learning_service()
        
        if symbol:
            learnings = await service.get_learnings_for_symbol(symbol, limit=limit)
        else:
            learnings = await service.get_all_learnings(limit=limit, offset=offset)
        
        return learnings
        
    except Exception as e:
        logger.error(f"Error getting recent learnings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mistakes")
async def get_recent_mistakes(
    limit: int = Query(5, ge=1, le=20, description="Maximum mistakes to return")
):
    """
    Get recent mistakes from losing trades.
    
    Returns formatted list of mistakes with symbol context.
    """
    try:
        service = get_learning_service()
        mistakes = await service.get_recent_mistakes(limit=limit)
        return {"mistakes": mistakes, "count": len(mistakes)}
        
    except Exception as e:
        logger.error(f"Error getting recent mistakes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patterns")
async def get_winning_patterns(
    limit: int = Query(5, ge=1, le=20, description="Maximum patterns to return")
):
    """
    Get winning patterns from big wins (>=2R).
    
    Returns patterns that have led to successful trades.
    """
    try:
        service = get_learning_service()
        patterns = await service.get_winning_patterns(limit=limit)
        return {"patterns": patterns, "count": len(patterns)}
        
    except Exception as e:
        logger.error(f"Error getting winning patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge", response_model=List[KnowledgeEntryResponse])
async def get_knowledge_base(
    category: Optional[str] = Query(None, description="Filter by category"),
    include_expired: bool = Query(False, description="Include expired entries")
):
    """
    Get knowledge base entries.
    
    Categories: symbol_pattern, session_insight, mistake, best_setup
    """
    try:
        service = get_learning_service()
        knowledge = await service.get_knowledge_base(
            category=category,
            include_expired=include_expired
        )
        return knowledge
        
    except Exception as e:
        logger.error(f"Error getting knowledge base: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly-report")
async def get_weekly_report():
    """
    Get the latest weekly learning report.
    
    Contains Claude's consolidated insights from the week.
    """
    try:
        service = get_learning_service()
        report = await service.get_latest_weekly_report()
        
        if not report:
            return {"message": "No weekly report available yet", "report": None}
        
        return {"message": "success", "report": report}
        
    except Exception as e:
        logger.error(f"Error getting weekly report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/consolidate")
async def trigger_consolidation():
    """
    Manually trigger weekly consolidation.
    
    Normally runs automatically on Sundays.
    """
    try:
        service = get_learning_service()
        
        # Need Claude client for consolidation
        from ...llm.claude_client import ClaudeClient
        claude_client = ClaudeClient()
        
        if not claude_client.api_key:
            raise HTTPException(
                status_code=400, 
                detail="Claude API key not configured. Set ANTHROPIC_API_KEY in your .env file"
            )
        
        report = await service.consolidate_weekly(claude_client)
        
        if not report:
            return {"message": "No learnings to consolidate", "success": False}
        
        return {
            "message": "Consolidation completed",
            "success": True,
            "grade": report.performance_grade,
            "trades_reviewed": report.total_trades
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during consolidation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review-history")
async def review_historical_trades(
    limit: int = Query(50, ge=1, le=200, description="Number of trades to review"),
    min_loss: float = Query(-10.0, description="Minimum loss to review (e.g., -10 for trades with >$10 loss)")
):
    """
    Have Claude review historical closed trades to generate learnings.
    
    This populates the learning system with insights from past trades.
    Focuses on losses and significant wins for maximum learning value.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select, or_
        from ...llm.claude_client import ClaudeClient
        
        service = get_learning_service()
        claude_client = ClaudeClient()
        
        if not claude_client.api_key:
            raise HTTPException(
                status_code=400, 
                detail="Claude API key not configured. Set ANTHROPIC_API_KEY in your .env file"
            )
        
        async with AsyncSessionLocal() as session:
            # Get closed trades with losses or significant wins (>$20)
            query = select(TradeModel).where(
                TradeModel.profit_loss.isnot(None),
                or_(
                    TradeModel.profit_loss < min_loss,  # Losses
                    TradeModel.profit_loss > 20  # Significant wins
                )
            ).order_by(TradeModel.timestamp.desc()).limit(limit)
            
            result = await session.execute(query)
            trades = result.scalars().all()
            
            if not trades:
                return {
                    "message": "No trades found matching criteria",
                    "success": False,
                    "reviewed": 0
                }
            
            reviewed_count = 0
            errors = []
            
            for trade in trades:
                try:
                    # Build trade data for Claude review
                    trade_data = {
                        "symbol": trade.symbol,
                        "direction": trade.direction,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "stop_loss": trade.stop_loss,
                        "take_profit": trade.take_profit,
                        "profit_loss": trade.profit_loss,
                        "position_size": trade.position_size,
                        "entry_time": trade.entry_time.isoformat() if trade.entry_time else None,
                        "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                        "entry_reason": trade.entry_reason or "Historical trade",
                        "exit_reason": trade.exit_reason or "Unknown"
                    }
                    
                    # Have Claude review the trade
                    review = await claude_client.review_closed_trade(trade_data)
                    
                    if review:
                        # Calculate R-multiple if we have stop loss
                        r_multiple = 0.0
                        if trade.stop_loss and trade.entry_price and trade.profit_loss:
                            risk = abs(trade.entry_price - trade.stop_loss)
                            if risk > 0:
                                r_multiple = trade.profit_loss / (risk * trade.position_size * 100000)  # Approximate
                        
                        # Store the learning
                        await service.store_trade_review(
                            trade_id=trade.trade_id,
                            symbol=trade.symbol,
                            direction=trade.direction,
                            profit_loss=trade.profit_loss or 0,
                            r_multiple=r_multiple,
                            review=review,
                            session=trade.session or "",
                            setup_type="Historical"
                        )
                        reviewed_count += 1
                        logger.info(f"Reviewed trade {trade.trade_id} ({trade.symbol}): {trade.profit_loss}")
                    
                except Exception as e:
                    errors.append(f"{trade.trade_id}: {str(e)}")
                    logger.error(f"Error reviewing trade {trade.trade_id}: {e}")
            
            return {
                "message": f"Reviewed {reviewed_count} historical trades",
                "success": True,
                "reviewed": reviewed_count,
                "total_found": len(trades),
                "errors": errors[:5] if errors else []  # Return first 5 errors
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reviewing historical trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context/{symbol}")
async def get_learning_context(
    symbol: str,
    session: Optional[str] = Query(None, description="Trading session")
):
    """
    Get the learning context that would be passed to Claude for a symbol.
    
    Useful for debugging and understanding what context Claude sees.
    """
    try:
        service = get_learning_service()
        context = await service.build_context_for_claude(symbol, session or "")
        
        return {
            "symbol": symbol,
            "session": session,
            "context": context,
            "context_length": len(context)
        }
        
    except Exception as e:
        logger.error(f"Error getting learning context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_learning_stats():
    """
    Get statistics about the learning system.
    
    Provides counts and breakdowns of learnings.
    """
    try:
        service = get_learning_service()
        
        # Get all learnings for stats
        all_learnings = await service.get_all_learnings(limit=1000)
        
        # Count by grade
        by_grade = {}
        by_outcome = {}
        by_symbol = {}
        
        for learning in all_learnings:
            grade = learning.get('grade', 'N/A')
            by_grade[grade] = by_grade.get(grade, 0) + 1
            
            outcome = learning.get('outcome', 'unknown')
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            
            symbol = learning.get('symbol', 'Unknown')
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
        
        # Get counts
        mistakes = await service.get_recent_mistakes(limit=100)
        patterns = await service.get_winning_patterns(limit=100)
        knowledge = await service.get_knowledge_base()
        
        return {
            "total_learnings": len(all_learnings),
            "by_grade": by_grade,
            "by_outcome": by_outcome,
            "by_symbol": by_symbol,
            "recent_mistakes_count": len(mistakes),
            "winning_patterns_count": len(patterns),
            "knowledge_entries": len(knowledge)
        }
        
    except Exception as e:
        logger.error(f"Error getting learning stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/prune", dependencies=[Depends(RequireAuth())])
async def prune_expired_knowledge():
    """
    Remove expired knowledge base entries.
    
    Requires API key. Entries older than 90 days are removed.
    """
    try:
        service = get_learning_service()
        count = await service.prune_expired_knowledge()
        
        return {
            "message": f"Pruned {count} expired entries",
            "count": count
        }
        
    except Exception as e:
        logger.error(f"Error pruning knowledge: {e}")
        raise HTTPException(status_code=500, detail=str(e))
