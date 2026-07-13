"""
Bot status and activity routes.

Provides real-time visibility into what the bot is doing.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from enum import Enum

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..auth import verify_api_key, RequireAuth

from ...config import settings
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


# In-memory bot state (updated by the main bot loop)
class BotState:
    """Global bot state tracker."""
    
    def __init__(self):
        self.is_running = False
        self.last_cycle_time: Optional[datetime] = None
        self.current_symbol: Optional[str] = None
        self.current_action: str = "idle"
        self.cycle_count: int = 0
        self.symbols_analyzed_this_cycle: List[str] = []
        self.last_error: Optional[str] = None
        self.session_name: str = "Unknown"
        self.is_tradeable: bool = False
        
        # Analysis logs (per symbol)
        self.analysis_logs: List[Dict[str, Any]] = []
        self.max_logs = 100
    
    def start_cycle(self, session_name: str, is_tradeable: bool):
        """Called when a new trading cycle starts."""
        self.is_running = True
        self.last_cycle_time = datetime.now()
        self.current_action = "starting_cycle"
        self.cycle_count += 1
        self.symbols_analyzed_this_cycle = []
        self.session_name = session_name
        self.is_tradeable = is_tradeable
    
    def analyzing_symbol(self, symbol: str):
        """Called when analyzing a symbol."""
        self.current_symbol = symbol
        self.current_action = f"analyzing_{symbol}"
        self.symbols_analyzed_this_cycle.append(symbol)
        self._add_log("analyzing", symbol, f"Starting analysis for {symbol}")
    
    def fetching_data(self, symbol: str):
        """Called when fetching OHLCV data."""
        self.current_action = f"fetching_data_{symbol}"
        self._add_log("fetching", symbol, f"Fetching M15 execution data for {symbol}")
    
    def running_technical_analysis(self, symbol: str):
        """Called when running technical analysis."""
        self.current_action = f"technical_analysis_{symbol}"
        self._add_log("technical", symbol, f"Running ICT analysis on M15 for {symbol} (HTF context: D1/H4/H1)")
    
    def mtf_analysis_complete(self, symbol: str, bias: str, alignment: bool, 
                              can_long: str, can_short: str, details: dict = None):
        """Called when multi-timeframe analysis completes."""
        align_str = "✅ Aligned" if alignment else "❌ Conflicting"
        directions = []
        if can_long == 'preferred':
            directions.append("LONG ✅")
        elif can_long == 'counter_trend':
            directions.append("LONG ⚠️CT")
        if can_short == 'preferred':
            directions.append("SHORT ✅")
        elif can_short == 'counter_trend':
            directions.append("SHORT ⚠️CT")
        dir_str = " | ".join(directions) if directions else "NO DATA"
        self._add_log(
            "mtf", symbol, 
            f"MTF Analysis (D1->M1): {bias.upper()} bias, {align_str}, Directions: {dir_str}",
            details or {}
        )
    
    def fibonacci_analysis_complete(self, symbol: str, zone: str, in_ote: bool, 
                                     optimal_entry: float = None, details: dict = None):
        """Called when Fibonacci/OTE analysis completes."""
        ote_str = "✅ IN OTE ZONE" if in_ote else "Outside OTE"
        entry_str = f", Entry: {optimal_entry:.5f}" if optimal_entry else ""
        self._add_log(
            "fibonacci", symbol,
            f"Fibonacci: {zone.upper()} zone, {ote_str}{entry_str}",
            details or {}
        )
    
    def volume_analysis_complete(self, symbol: str, relative_volume: float, 
                                  trend: str, spike_count: int, is_low_volume: bool):
        """Called when volume analysis completes for a symbol."""
        vol_str = f"{relative_volume:.1f}x avg"
        low_str = " (LOW VOLUME WARNING)" if is_low_volume else ""
        self._add_log(
            "volume", symbol,
            f"Volume: {vol_str}, Trend: {trend}, Spikes: {spike_count}{low_str}",
            {
                "relative_volume": relative_volume,
                "volume_trend": trend,
                "spike_count": spike_count,
                "is_low_volume": is_low_volume
            }
        )
    
    def calling_claude(self, symbol: str):
        """Called when calling Claude API."""
        self.current_action = f"claude_analysis_{symbol}"
        self._add_log("claude", symbol, f"Sending M15 chart + D1/H4/H1 context to Claude for {symbol}")
    
    def claude_response(self, symbol: str, direction: str, confidence: float, reasoning: str = ""):
        """Called when Claude responds."""
        self._add_log("signal", symbol, f"Claude signal: {direction} ({confidence*100:.0f}%)", {
            "direction": direction,
            "confidence": confidence,
            "reasoning": reasoning if reasoning else ""
        })
    
    def trade_decision(self, symbol: str, action: str, reason: str):
        """Called when making a trade decision."""
        self._add_log("decision", symbol, f"Trade decision: {action} - {reason}")
    
    def trade_executed(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        """Called when a trade is executed."""
        self._add_log("trade", symbol, f"Trade executed: {direction} @ {entry}, SL: {sl}, TP: {tp}", {
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "take_profit": tp
        })
    
    def symbol_complete(self, symbol: str, result: str):
        """Called when symbol analysis is complete."""
        self._add_log("complete", symbol, f"Analysis complete: {result}")
        self.current_symbol = None
        self.current_action = "idle"
    
    def cycle_complete(self):
        """Called when trading cycle completes."""
        self.current_action = "waiting"
        self.current_symbol = None
        self._add_log("cycle", None, f"Cycle {self.cycle_count} complete. Analyzed {len(self.symbols_analyzed_this_cycle)} symbols.")
    
    def error(self, symbol: Optional[str], error: str):
        """Called on error."""
        self.last_error = error
        self._add_log("error", symbol, f"Error: {error}")
    
    @staticmethod
    def _sanitize_value(val):
        """Convert numpy/non-native types to JSON-serializable Python types."""
        if val is None:
            return val
        # Handle numpy scalar types
        type_name = type(val).__module__
        if type_name == 'numpy':
            import numpy as np
            if isinstance(val, (np.bool_,)):
                return bool(val)
            elif isinstance(val, (np.integer,)):
                return int(val)
            elif isinstance(val, (np.floating,)):
                return float(val)
            elif isinstance(val, np.ndarray):
                return val.tolist()
        if isinstance(val, dict):
            return {k: BotState._sanitize_value(v) for k, v in val.items()}
        if isinstance(val, (list, tuple)):
            return [BotState._sanitize_value(v) for v in val]
        return val

    def _add_log(self, log_type: str, symbol: Optional[str], message: str, details: Dict = None):
        """Add a log entry."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": log_type,
            "symbol": symbol,
            "message": message,
            "details": self._sanitize_value(details) if details else {}
        }
        self.analysis_logs.insert(0, log_entry)
        
        # Trim logs
        if len(self.analysis_logs) > self.max_logs:
            self.analysis_logs = self.analysis_logs[:self.max_logs]
    
    def get_status(self) -> dict:
        """Get current bot status."""
        return {
            "is_running": bool(self.is_running),
            "current_action": self.current_action,
            "current_symbol": self.current_symbol,
            "session": {
                "name": self.session_name,
                "is_tradeable": bool(self.is_tradeable)
            },
            "cycle_info": {
                "count": self.cycle_count,
                "last_cycle_time": self.last_cycle_time.isoformat() if self.last_cycle_time else None,
                "symbols_this_cycle": self.symbols_analyzed_this_cycle
            },
            "config": {
                "trading_symbols": settings.trading.symbols,
                "allowed_sessions": settings.trading.allowed_sessions,
                "risk_per_trade": settings.trading.risk_per_trade,
                "max_daily_trades": settings.trading.max_daily_trades
            },
            "last_error": self.last_error
        }


# Global bot state instance
bot_state = BotState()


# Response models
class BotStatusResponse(BaseModel):
    """Bot status response."""
    is_running: bool
    current_action: str
    current_symbol: Optional[str]
    session: dict
    cycle_info: dict
    config: dict
    last_error: Optional[str]


class AnalysisLog(BaseModel):
    """Analysis log entry."""
    timestamp: str
    type: str
    symbol: Optional[str]
    message: str
    details: dict


class AnalysisLogsResponse(BaseModel):
    """Analysis logs response."""
    logs: List[AnalysisLog]
    total: int


# API Endpoints
@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status():
    """
    Get current bot status.
    
    Shows what the bot is currently doing, which symbol it's analyzing,
    and configuration details.
    """
    return bot_state.get_status()


@router.get("/logs", response_model=AnalysisLogsResponse)
async def get_analysis_logs(
    limit: int = 50,
    symbol: Optional[str] = None,
    log_type: Optional[str] = None
):
    """
    Get bot analysis logs.
    
    Shows detailed logs of what the bot has been doing.
    
    Args:
        limit: Number of logs to return
        symbol: Filter by symbol
        log_type: Filter by type (analyzing, fetching, technical, claude, signal, decision, trade, error)
    """
    logs = bot_state.analysis_logs[:limit]
    
    if symbol:
        logs = [l for l in logs if l.get("symbol") == symbol.upper()]
    
    if log_type:
        logs = [l for l in logs if l.get("type") == log_type]
    
    return AnalysisLogsResponse(
        logs=[AnalysisLog(**l) for l in logs],
        total=len(bot_state.analysis_logs)
    )


@router.delete("/logs")
async def clear_logs():
    """Clear analysis logs."""
    bot_state.analysis_logs = []
    return {"message": "Logs cleared"}


@router.get("/symbols/trading")
async def get_trading_symbols():
    """
    Get the symbols the bot is configured to trade.
    
    These are the symbols the bot will analyze each cycle.
    """
    return {
        "trading_symbols": settings.trading.symbols,
        "count": len(settings.trading.symbols)
    }


# Export bot_state for use in main.py
def get_bot_state() -> BotState:
    """Get the global bot state instance."""
    return bot_state


@router.post("/start", dependencies=[Depends(RequireAuth())])
async def start_bot():
    """
    Start the trading bot.
    
    Note: The bot is auto-started when the API server launches.
    Use this endpoint to restart after stopping.
    """
    from ..main import get_bot_instance, start_bot_task
    
    bot = get_bot_instance()
    if bot and bot.running:
        bot_state.is_running = True
        return {"status": "already_running", "message": "Bot is already running"}
    
    # Start bot in background and wait for result
    try:
        success = await start_bot_task()
        
        if success:
            bot_state.is_running = True
            return {"status": "started", "message": "Trading bot started successfully"}
        else:
            return {"status": "failed", "message": "Bot failed to start - check MT5 connection and logs"}
    except Exception as e:
        return {"status": "failed", "message": f"Bot start error: {str(e)}"}


@router.post("/stop", dependencies=[Depends(RequireAuth())])
async def stop_bot():
    """
    Stop the trading bot.
    
    Gracefully stops the trading loop but keeps the API running.
    """
    from ..main import get_bot_instance
    
    bot = get_bot_instance()
    if not bot or not bot.running:
        return {"status": "not_running", "message": "Bot is not running"}
    
    bot.stop()
    bot_state.is_running = False
    bot_state.current_action = "stopped"
    
    return {"status": "stopped", "message": "Trading bot stopped"}


@router.post("/emergency-close", dependencies=[Depends(RequireAuth())])
async def emergency_close_all(reason: str = "Manual emergency close"):
    """
    EMERGENCY: Close all open positions immediately. [REQUIRES AUTH]
    
    Use this during flash crashes or when you need to exit all trades NOW.
    
    Args:
        reason: Reason for emergency close (for logging)
    """
    from ..main import get_bot_instance
    
    bot = get_bot_instance()
    if not bot:
        return {"status": "error", "message": "Bot not available"}
    
    try:
        await bot.emergency_close_all(reason)
        return {
            "status": "success",
            "message": f"Emergency close triggered: {reason}",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Emergency close failed: {e}")
        return {
            "status": "error",
            "message": f"Emergency close failed: {str(e)}"
        }


@router.get("/positions")
async def get_open_positions():
    """
    Get all open positions - uses live MT5 data for P&L and prices,
    enriched with bot-tracked metadata (R-multiple, status, management flags).
    """
    from ..main import get_bot_instance, get_mt5_client
    
    bot = get_bot_instance()
    mt5_client = get_mt5_client()
    positions = []
    
    # Build a lookup of bot-tracked positions for enrichment
    bot_tracked = {}
    if bot and bot.position_manager:
        for pos in bot.position_manager.get_all_positions():
            bot_tracked[pos.ticket] = pos
    
    # Get LIVE MT5 positions — these have real-time P&L and current prices
    mt5_positions = []
    if mt5_client:
        try:
            mt5_positions = await mt5_client.get_positions()
        except Exception as e:
            logger.warning(f"Could not fetch MT5 positions: {e}")
    
    seen_tickets = set()
    
    # Build position list from live MT5 data, enriching with bot metadata
    for mt5_pos in mt5_positions:
        seen_tickets.add(mt5_pos.ticket)
        bot_pos = bot_tracked.get(mt5_pos.ticket)
        
        if bot_pos:
            # Bot-tracked position — use MT5 for P&L/prices, bot for management data
            positions.append({
                "ticket": mt5_pos.ticket,
                "symbol": mt5_pos.symbol,
                "direction": bot_pos.direction,
                "volume": mt5_pos.volume,
                "entry_price": mt5_pos.price_open,
                "current_price": mt5_pos.price_current,
                "stop_loss": mt5_pos.sl,
                "take_profit": mt5_pos.tp,
                "unrealized_pnl": mt5_pos.profit,  # Real dollar P&L from MT5
                "r_multiple": bot_pos.current_r_multiple,
                "status": bot_pos.status.value if hasattr(bot_pos.status, 'value') else str(bot_pos.status),
                "be_triggered": bot_pos.be_triggered,
                "trailing_active": bot_pos.trailing_active,
                "partial_closed": bot_pos.partial_closed,
                "open_time": mt5_pos.time.isoformat() if hasattr(mt5_pos, 'time') and mt5_pos.time else (
                    bot_pos.open_time.isoformat() if bot_pos.open_time else None
                ),
                "initial_sl": bot_pos.initial_sl,
                "tp1": bot_pos.tp1,
                "tp2": bot_pos.tp2,
                "tp3": bot_pos.tp3,
                "tp1_hit": bot_pos.tp1_hit,
                "tp2_hit": bot_pos.tp2_hit,
                "initial_volume": bot_pos.initial_volume,
            })
        else:
            # MT5-only position (not tracked by bot)
            positions.append({
                "ticket": mt5_pos.ticket,
                "symbol": mt5_pos.symbol,
                "direction": "long" if mt5_pos.type == "buy" else "short",
                "volume": mt5_pos.volume,
                "entry_price": mt5_pos.price_open,
                "current_price": mt5_pos.price_current,
                "stop_loss": mt5_pos.sl,
                "take_profit": mt5_pos.tp,
                "unrealized_pnl": mt5_pos.profit,  # Real dollar P&L from MT5
                "r_multiple": 0.0,
                "status": "open",
                "be_triggered": False,
                "trailing_active": False,
                "partial_closed": False,
                "open_time": mt5_pos.time.isoformat() if hasattr(mt5_pos, 'time') and mt5_pos.time else None
            })
    
    # If MT5 is unavailable, fall back to bot-tracked positions
    if not mt5_positions and bot_tracked:
        for pos in bot_tracked.values():
            if pos.ticket not in seen_tickets:
                positions.append({
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "volume": pos.volume,
                    "entry_price": pos.entry_price,
                    "current_price": pos.current_price,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "r_multiple": pos.current_r_multiple,
                    "status": pos.status.value if hasattr(pos.status, 'value') else str(pos.status),
                    "be_triggered": pos.be_triggered,
                    "trailing_active": pos.trailing_active,
                    "partial_closed": pos.partial_closed,
                    "open_time": pos.open_time.isoformat() if pos.open_time else None
                })
    
    return {
        "positions": positions,
        "count": len(positions),
        "total_pnl": sum(p["unrealized_pnl"] for p in positions)
    }


@router.post("/positions/{ticket}/close", dependencies=[Depends(RequireAuth())])
async def close_position(ticket: int, reason: str = "Manual close"):
    """
    Close a specific position by ticket number.
    
    Args:
        ticket: MT5 position ticket number
        reason: Reason for closing (for logging)
    """
    from ..main import get_bot_instance
    
    bot = get_bot_instance()
    if not bot:
        return {"status": "error", "message": "Bot not available"}
    
    try:
        position = bot.position_manager.get_position(ticket)
        if not position:
            return {"status": "error", "message": f"Position {ticket} not found"}
        
        result = await bot.order_manager.close_position(ticket)
        
        if result.success:
            # Set close reason on the position so the callback can use it
            position.close_reason = reason
            
            # Trigger the position close callback BEFORE removing from tracking.
            # This records the trade in the DB with proper exit price, P/L from MT5,
            # and sends Telegram notification — just like a TP/SL hit.
            if bot.position_manager.on_position_close:
                try:
                    await bot.position_manager.on_position_close(position)
                except Exception as cb_err:
                    logger.warning(f"Position close callback error for {ticket}: {cb_err}")
            
            bot.position_manager.remove_position(ticket)
            
            from .activity import add_activity
            add_activity(
                "manual_close",
                f"Manually closed {position.symbol} position: {reason}",
                position.symbol,
                {"ticket": ticket, "reason": reason}
            )
            
            return {
                "status": "success",
                "message": f"Position {ticket} closed",
                "symbol": position.symbol
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to close: {result.message}"
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/positions/{ticket}/modify", dependencies=[Depends(RequireAuth())])
async def modify_position(
    ticket: int,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None
):
    """
    Modify stop loss or take profit for a position.
    
    Args:
        ticket: MT5 position ticket number
        stop_loss: New stop loss price (optional)
        take_profit: New take profit price (optional)
    """
    from ..main import get_bot_instance
    
    bot = get_bot_instance()
    if not bot:
        return {"status": "error", "message": "Bot not available"}
    
    try:
        position = bot.position_manager.get_position(ticket)
        if not position:
            return {"status": "error", "message": f"Position {ticket} not found"}
        
        # Validate SL/TP are positive prices
        if stop_loss is not None and stop_loss <= 0:
            return {"status": "error", "message": f"Invalid stop_loss: {stop_loss} (must be > 0)"}
        if take_profit is not None and take_profit <= 0:
            return {"status": "error", "message": f"Invalid take_profit: {take_profit} (must be > 0)"}
        
        # Validate SL/TP direction relative to entry price
        entry = position.entry_price
        if position.direction == 'long':
            if stop_loss is not None and stop_loss >= entry:
                return {"status": "error", "message": f"Long SL ({stop_loss}) must be below entry ({entry})"}
            if take_profit is not None and take_profit <= entry:
                return {"status": "error", "message": f"Long TP ({take_profit}) must be above entry ({entry})"}
        else:  # short
            if stop_loss is not None and stop_loss <= entry:
                return {"status": "error", "message": f"Short SL ({stop_loss}) must be above entry ({entry})"}
            if take_profit is not None and take_profit >= entry:
                return {"status": "error", "message": f"Short TP ({take_profit}) must be below entry ({entry})"}
        
        result = await bot.order_manager.modify_order(
            ticket=ticket,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        if result.success:
            if stop_loss:
                position.stop_loss = stop_loss
            if take_profit:
                position.take_profit = take_profit
            
            return {
                "status": "success",
                "message": f"Position {ticket} modified",
                "new_sl": stop_loss,
                "new_tp": take_profit
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to modify: {result.message}"
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/weekly-review", dependencies=[Depends(RequireAuth())])
async def generate_weekly_review():
    """
    Generate a Claude-powered weekly performance review.
    
    Analyzes the past week's trades and provides:
    - Performance summary
    - Patterns identified
    - Strengths and weaknesses
    - Recommendations for improvement
    """
    from ..main import get_bot_instance
    
    bot = get_bot_instance()
    if not bot:
        return {"status": "error", "message": "Bot not available"}
    
    if not bot.claude_client or not bot.claude_client.api_key:
        return {"status": "error", "message": "Claude client not configured"}
    
    try:
        # Get recent trades from scaling manager
        trades = []
        if bot.scaling_manager:
            trades = bot.scaling_manager.recent_trades
        
        # Get account info
        account = await bot.mt5_client.get_account_info() if bot.mt5_client else None
        equity_start = bot.goal_tracker.starting_equity if bot.goal_tracker else 1000
        equity_end = account.equity if account else 1000
        
        # Get session stats
        session_stats = None
        if bot.session_analytics:
            session_stats = {
                'best_session': bot.session_analytics.get_best_session().value if bot.session_analytics.get_best_session() else None,
                'worst_session': bot.session_analytics.get_worst_session().value if bot.session_analytics.get_worst_session() else None,
            }
        
        # Generate review with Claude
        review = await bot.claude_client.generate_weekly_review(
            trades=trades,
            equity_start=equity_start,
            equity_end=equity_end,
            session_stats=session_stats
        )
        
        # Send notification if configured
        from ...utils.notifications import notify, NotificationType
        
        summary = review.get('summary', 'No summary generated')
        grade = review.get('performance_grade', 'N/A')
        
        await notify(
            NotificationType.INFO,
            f"📊 <b>Weekly Review</b>\n\n"
            f"<b>Grade:</b> {grade}\n\n"
            f"{summary[:500]}..."
        )
        
        return {
            "status": "success",
            "review": review
        }
        
    except Exception as e:
        logger.error(f"Error generating weekly review: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/performance-summary")
async def get_performance_summary():
    """
    Get comprehensive performance summary including all integrated services.
    """
    from ..main import get_bot_instance
    
    bot = get_bot_instance()
    
    summary = {
        "bot_running": bot is not None and bot.running if bot else False,
        "timestamp": datetime.now().isoformat()
    }
    
    if not bot:
        return summary
    
    try:
        # Account info
        account = await bot.mt5_client.get_account_info() if bot.mt5_client else None
        if account:
            summary["account"] = {
                "equity": account.equity,
                "balance": account.balance,
                "profit": account.profit
            }
        
        # Goal progress
        if bot.goal_tracker and account:
            progress = bot.goal_tracker.calculate_progress(account.equity)
            summary["goal"] = progress
        
        # Scaling status
        if bot.scaling_manager and account:
            summary["scaling"] = bot.scaling_manager.get_status(account.equity)
        
        # Session analytics
        if bot.session_analytics:
            summary["sessions"] = {
                "current": bot.session_analytics.get_current_session().value,
                "best": bot.session_analytics.get_best_session().value if bot.session_analytics.get_best_session() else None,
                "worst": bot.session_analytics.get_worst_session().value if bot.session_analytics.get_worst_session() else None,
                "recommendations": bot.session_analytics.get_recommendations()[:5]
            }
        
        # Position sizer tier
        if bot.position_sizer and account:
            summary["tier"] = bot.position_sizer.get_tier_info(account.equity)
        
        # Streaks
        summary["streaks"] = {
            "win_streak": bot.win_streak,
            "loss_streak": bot.loss_streak
        }
        
    except Exception as e:
        logger.error(f"Error getting performance summary: {e}")
        summary["error"] = str(e)
    
    return summary