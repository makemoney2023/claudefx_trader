"""
Performance routes for the API.

Provides endpoints for:
- Trading statistics
- Performance metrics
- Historical analysis
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...utils.logging import get_logger

# Lazy imports to avoid circular dependency
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
            # Build query for REAL EXECUTED trades only.
            # Exclude: cancelled pending orders (P/L=0), commission-only artifacts (P/L=-0.06),
            # and expired orders. Only count trades with meaningful P/L (>= $0.10).
            from sqlalchemy import and_, or_, func as sa_func
            
            query = select(TradeModel).where(
                and_(
                    TradeModel.profit_loss.isnot(None),
                    # abs(profit_loss) >= 0.10 — excludes cancelled ($0) and commission artifacts
                    or_(
                        TradeModel.profit_loss >= 0.10,
                        TradeModel.profit_loss <= -0.10,
                    )
                )
            )
            
            if period_days:
                cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
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
        return PerformanceStats(
            total_trades=0, wins=0, losses=0, win_rate=0.0,
            total_profit=0.0, total_r=0.0, avg_r=0.0,
            avg_win=0.0, avg_loss=0.0, profit_factor=0.0,
            largest_win=0.0, largest_loss=0.0
        )


@router.get("/daily", response_model=List[DailySummary])
async def get_daily_summaries(
    days: int = Query(30, ge=1, le=365, description="Number of days to include")
):
    """
    Get daily trading summaries from database.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select, func, cast, Date

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        summaries = []

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeModel).where(TradeModel.timestamp >= cutoff)
            )
            trades = result.scalars().all()

        daily: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            ts = t.exit_time or t.timestamp
            if ts is None:
                continue
            day_key = ts.strftime("%Y-%m-%d")
            if day_key not in daily:
                daily[day_key] = {"opened": 0, "closed": 0, "pnl": 0.0, "r": 0.0}

            if t.exit_time:
                daily[day_key]["closed"] += 1
                daily[day_key]["pnl"] += t.profit_loss or 0
                daily[day_key]["r"] += t.r_multiple or 0
            else:
                daily[day_key]["opened"] += 1

        for day_key in sorted(daily.keys(), reverse=True):
            d = daily[day_key]
            if d["opened"] > 0 or d["closed"] > 0:
                summaries.append(DailySummary(
                    date=day_key,
                    trades_opened=d["opened"],
                    trades_closed=d["closed"],
                    daily_pnl=d["pnl"],
                    daily_r=d["r"]
                ))

        return summaries
    except Exception as e:
        logger.error(f"Error getting daily summaries: {e}")
        return []


@router.get("/ict-concepts", response_model=List[ICTConceptStats])
async def get_ict_concept_stats():
    """
    Get performance breakdown by ICT concept from database.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeModel).where(TradeModel.profit_loss.isnot(None))
            )
            trades = result.scalars().all()

        concept_stats: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            concepts = t.ict_concepts if t.ict_concepts else ["unknown"]
            if isinstance(concepts, str):
                concepts = [concepts]
            for concept in concepts:
                if concept not in concept_stats:
                    concept_stats[concept] = {"trades": 0, "wins": 0, "total_r": 0.0}
                concept_stats[concept]["trades"] += 1
                if t.profit_loss and t.profit_loss > 0:
                    concept_stats[concept]["wins"] += 1
                concept_stats[concept]["total_r"] += t.r_multiple or 0

        return [
            ICTConceptStats(
                concept=concept,
                trades=data["trades"],
                wins=data["wins"],
                win_rate=data["wins"] / data["trades"] if data["trades"] > 0 else 0,
                avg_r=data["total_r"] / data["trades"] if data["trades"] > 0 else 0
            )
            for concept, data in concept_stats.items()
        ]
    except Exception as e:
        logger.error(f"Error getting ICT concept stats: {e}")
        return []


@router.get("/equity-curve", response_model=List[EquityPoint])
async def get_equity_curve(
    days: int = Query(90, ge=1, le=365, description="Number of days")
):
    """
    Get equity curve data for charting from database.

    Shows real-time account equity from MT5, plus historical equity
    based on closed trades from the DB.
    """
    mt5_client = get_mt5_client()
    current_equity = 10000.0
    current_balance = 10000.0

    if mt5_client and mt5_client.is_connected:
        try:
            account = await mt5_client.get_account_info()
            if account:
                current_equity = float(account.equity)
                current_balance = float(account.balance)
        except Exception as e:
            logger.warning(f"Could not get account info: {e}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select, and_

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeModel).where(
                    and_(
                        TradeModel.exit_time.isnot(None),
                        TradeModel.profit_loss.isnot(None),
                        TradeModel.exit_time >= cutoff,
                    )
                ).order_by(TradeModel.exit_time)
            )
            closed_trades = result.scalars().all()
    except Exception as e:
        logger.error(f"Error querying trades for equity curve: {e}")
        closed_trades = []

    if not closed_trades:
        return [
            EquityPoint(timestamp=cutoff, equity=current_balance, drawdown=0.0),
            EquityPoint(
                timestamp=datetime.now(timezone.utc),
                equity=current_equity,
                drawdown=0.0 if current_equity >= current_balance else (current_balance - current_equity) / current_balance
            )
        ]

    total_pnl = sum(t.profit_loss or 0 for t in closed_trades)
    starting_equity = current_balance - total_pnl

    equity = starting_equity
    peak_equity = equity

    equity_points = [EquityPoint(timestamp=cutoff, equity=starting_equity, drawdown=0.0)]

    for trade in closed_trades:
        equity += trade.profit_loss or 0
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        equity_points.append(EquityPoint(timestamp=trade.exit_time, equity=equity, drawdown=drawdown))

    peak_equity = max(peak_equity, current_equity)
    current_drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0
    equity_points.append(EquityPoint(timestamp=datetime.now(timezone.utc), equity=current_equity, drawdown=current_drawdown))

    return equity_points


@router.get("/report")
async def get_performance_report():
    """
    Get a text-based performance report from database.
    """
    try:
        stats = await get_performance_stats()
        lines = [
            "=== Trading Performance Report ===",
            f"Total Trades: {stats.total_trades}",
            f"Win Rate: {stats.win_rate:.1%}  ({stats.wins}W / {stats.losses}L)",
            f"Total Profit: ${stats.total_profit:+.2f}",
            f"Total R: {stats.total_r:+.1f}R  (Avg: {stats.avg_r:+.2f}R)",
            f"Profit Factor: {stats.profit_factor:.2f}",
            f"Avg Win: ${stats.avg_win:+.2f}  |  Avg Loss: ${stats.avg_loss:+.2f}",
            f"Largest Win: ${stats.largest_win:+.2f}  |  Largest Loss: ${stats.largest_loss:+.2f}",
        ]
        report = "\n".join(lines)
    except Exception as e:
        logger.error(f"Error generating performance report: {e}")
        report = "Report generation failed."

    return {
        "report": report,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/by-symbol")
async def get_performance_by_symbol():
    """
    Get performance breakdown by trading symbol from database.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeModel).where(TradeModel.profit_loss.isnot(None))
            )
            trades = result.scalars().all()

        symbol_stats: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            symbol = t.symbol
            if symbol not in symbol_stats:
                symbol_stats[symbol] = {"trades": 0, "wins": 0, "total_pnl": 0.0, "total_r": 0.0}
            symbol_stats[symbol]["trades"] += 1
            if t.profit_loss and t.profit_loss > 0:
                symbol_stats[symbol]["wins"] += 1
            symbol_stats[symbol]["total_pnl"] += t.profit_loss or 0
            symbol_stats[symbol]["total_r"] += t.r_multiple or 0

        items = []
        for symbol, data in symbol_stats.items():
            items.append({
                "symbol": symbol,
                "trades": data["trades"],
                "wins": data["wins"],
                "win_rate": data["wins"] / data["trades"] if data["trades"] > 0 else 0,
                "total_pnl": data["total_pnl"],
                "avg_r": data["total_r"] / data["trades"] if data["trades"] > 0 else 0,
            })
        return sorted(items, key=lambda x: x["total_pnl"], reverse=True)
    except Exception as e:
        logger.error(f"Error getting performance by symbol: {e}")
        return []


@router.get("/by-session")
async def get_performance_by_session():
    """
    Get performance breakdown by trading session from database.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TradeModel).where(TradeModel.profit_loss.isnot(None))
            )
            trades = result.scalars().all()

        session_stats: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            sess_name = getattr(t, 'session', None) or "unknown"
            if sess_name not in session_stats:
                session_stats[sess_name] = {"trades": 0, "wins": 0, "total_pnl": 0.0, "total_r": 0.0}
            session_stats[sess_name]["trades"] += 1
            if t.profit_loss and t.profit_loss > 0:
                session_stats[sess_name]["wins"] += 1
            session_stats[sess_name]["total_pnl"] += t.profit_loss or 0
            session_stats[sess_name]["total_r"] += t.r_multiple or 0

        items = []
        for sess_name, data in session_stats.items():
            items.append({
                "session": sess_name,
                "trades": data["trades"],
                "wins": data["wins"],
                "win_rate": data["wins"] / data["trades"] if data["trades"] > 0 else 0,
                "total_pnl": data["total_pnl"],
                "avg_r": data["total_r"] / data["trades"] if data["trades"] > 0 else 0,
            })
        return sorted(items, key=lambda x: x["total_pnl"], reverse=True)
    except Exception as e:
        logger.error(f"Error getting performance by session: {e}")
        return []


# ============================================
# EDGE TRACKER
# ============================================

class SymbolEdge(BaseModel):
    symbol: str
    trades: int
    win_rate: float
    avg_r: float
    total_r: float
    score: float
    status: str

class EdgeAlert(BaseModel):
    level: str
    message: str
    symbol: Optional[str] = None

class EdgeTrackerResponse(BaseModel):
    overall_score: float
    overall_status: str
    rolling_win_rate: float
    rolling_avg_r: float
    rolling_total_r: float
    rolling_trades: int
    window_label: str
    symbols: List[SymbolEdge]
    alerts: List[EdgeAlert]
    recent_wr_trend: List[float]


# Fail-open until enough closed trades exist. Matches session WR blocking
# (10+ trades) and playbook gates — one stop-out must not "collapse" the edge.
EDGE_HEALTH_MIN_SAMPLE = 10


def _compute_edge_score(win_rate: float, avg_r: float, n_trades: int) -> float:
    """
    Composite edge health score 0-100.
    WR contributes 50%, avg_r contributes 35%, sample size 15%.
    """
    wr_score = min(win_rate / 0.60, 1.0) * 50
    r_score = min(avg_r / 1.0, 1.0) * 35 if avg_r > 0 else 0
    n_score = min(n_trades / 30, 1.0) * 15
    return round(wr_score + r_score + n_score, 1)


def _status_from_score(score: float, n_trades: int = 999) -> str:
    """Map score to status. Thin samples never go critical/blocked (fail-open)."""
    if n_trades < EDGE_HEALTH_MIN_SAMPLE:
        if score >= 60:
            return "healthy"
        return "warning"
    if score >= 60:
        return "healthy"
    elif score >= 40:
        return "warning"
    elif score >= 25:
        return "critical"
    return "blocked"


def _edge_symbol_alert(
    symbol: str,
    score: float,
    status: str,
    trades: int,
) -> Optional[EdgeAlert]:
    """Build per-symbol edge alert; never claim auto-block on thin samples."""
    if trades < EDGE_HEALTH_MIN_SAMPLE:
        return EdgeAlert(
            level="info",
            message=(
                f"{symbol}: building sample ({trades}/{EDGE_HEALTH_MIN_SAMPLE}). "
                f"Edge protection inactive (score {score:.0f})."
            ),
            symbol=symbol,
        )
    if status == "blocked":
        return EdgeAlert(
            level="critical",
            message=f"{symbol} edge collapsed (score {score:.0f}). Auto-blocked.",
            symbol=symbol,
        )
    if status == "critical":
        return EdgeAlert(
            level="warning",
            message=f"{symbol} edge degrading (score {score:.0f}). Watch closely.",
            symbol=symbol,
        )
    return None


@router.get("/edge-tracker", response_model=EdgeTrackerResponse)
async def get_edge_tracker(
    window: int = Query(50, ge=10, le=500, description="Rolling trade window size"),
):
    """
    Rolling edge health tracker with per-symbol breakdown and alerts.
    Uses the most recent N closed trades.
    """
    try:
        from ..database import AsyncSessionLocal, TradeModel
        from sqlalchemy import select, and_, or_, desc as sa_desc

        async with AsyncSessionLocal() as session:
            query = (
                select(TradeModel)
                .where(
                    and_(
                        TradeModel.profit_loss.isnot(None),
                        or_(
                            TradeModel.profit_loss >= 0.10,
                            TradeModel.profit_loss <= -0.10,
                        ),
                    )
                )
                .order_by(sa_desc(TradeModel.timestamp))
                .limit(window)
            )
            result = await session.execute(query)
            trades = list(result.scalars().all())

        if not trades:
            return EdgeTrackerResponse(
                overall_score=100,
                overall_status="healthy",
                rolling_win_rate=0,
                rolling_avg_r=0,
                rolling_total_r=0,
                rolling_trades=0,
                window_label=f"Last {window} trades (0 available)",
                symbols=[],
                alerts=[EdgeAlert(
                    level="info",
                    message=(
                        f"No closed trades yet. Edge protection inactive "
                        f"until {EDGE_HEALTH_MIN_SAMPLE} trades."
                    ),
                )],
                recent_wr_trend=[],
            )

        def _sanitize_r(r) -> float:
            val = r or 0.0
            return val if abs(val) <= 10 else 0.0

        wins = [t for t in trades if t.profit_loss and t.profit_loss > 0]
        losses = [t for t in trades if t.profit_loss and t.profit_loss < 0]
        n = len(wins) + len(losses)
        wr = len(wins) / n if n > 0 else 0.0
        r_vals = [_sanitize_r(t.r_multiple) for t in trades]
        avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0
        total_r = sum(r_vals)

        overall_score = _compute_edge_score(wr, avg_r, n)
        overall_status = _status_from_score(overall_score, n_trades=n)

        sym_data: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            s = t.symbol
            if s not in sym_data:
                sym_data[s] = {"wins": 0, "losses": 0, "r_vals": []}
            if t.profit_loss and t.profit_loss > 0:
                sym_data[s]["wins"] += 1
            elif t.profit_loss and t.profit_loss < 0:
                sym_data[s]["losses"] += 1
            sym_data[s]["r_vals"].append(_sanitize_r(t.r_multiple))

        symbol_edges = []
        for s, d in sym_data.items():
            sn = d["wins"] + d["losses"]
            swr = d["wins"] / sn if sn > 0 else 0.0
            sar = sum(d["r_vals"]) / len(d["r_vals"]) if d["r_vals"] else 0.0
            str_r = sum(d["r_vals"])
            ss = _compute_edge_score(swr, sar, sn)
            symbol_edges.append(SymbolEdge(
                symbol=s, trades=sn, win_rate=round(swr, 4),
                avg_r=round(sar, 3), total_r=round(str_r, 2),
                score=ss, status=_status_from_score(ss, n_trades=sn),
            ))
        symbol_edges.sort(key=lambda x: x.score, reverse=True)

        recent_wr_trend = []
        chunk = 10
        for i in range(0, min(len(trades), 50), chunk):
            batch = trades[i:i + chunk]
            bw = sum(1 for t in batch if t.profit_loss and t.profit_loss > 0)
            bl = sum(1 for t in batch if t.profit_loss and t.profit_loss < 0)
            bn = bw + bl
            recent_wr_trend.append(round(bw / bn, 4) if bn > 0 else 0.0)

        alerts: List[EdgeAlert] = []
        if n < EDGE_HEALTH_MIN_SAMPLE:
            alerts.append(EdgeAlert(
                level="info",
                message=(
                    f"Building edge sample ({n}/{EDGE_HEALTH_MIN_SAMPLE}). "
                    f"Score {overall_score:.0f}/100 is informational only — not blocking."
                ),
            ))
        elif overall_score < 30:
            alerts.append(EdgeAlert(
                level="critical",
                message=f"Edge health critically low ({overall_score:.0f}/100). Consider pausing trading."
            ))
        elif overall_score < 50:
            alerts.append(EdgeAlert(
                level="warning",
                message=f"Edge health declining ({overall_score:.0f}/100). Reduced risk recommended."
            ))

        for se in symbol_edges:
            alert = _edge_symbol_alert(se.symbol, se.score, se.status, se.trades)
            if alert:
                alerts.append(alert)

        if len(recent_wr_trend) >= 3 and all(
            recent_wr_trend[i] < recent_wr_trend[i + 1]
            for i in range(min(2, len(recent_wr_trend) - 1))
        ):
            alerts.append(EdgeAlert(
                level="warning",
                message="Win rate declining across recent trade batches."
            ))

        return EdgeTrackerResponse(
            overall_score=overall_score,
            overall_status=overall_status,
            rolling_win_rate=round(wr, 4),
            rolling_avg_r=round(avg_r, 3),
            rolling_total_r=round(total_r, 2),
            rolling_trades=n,
            window_label=f"Last {window} trades ({n} closed)",
            symbols=symbol_edges,
            alerts=alerts,
            recent_wr_trend=recent_wr_trend,
        )

    except Exception as e:
        logger.error(f"Edge tracker error: {e}")
        return EdgeTrackerResponse(
            overall_score=100,
            overall_status="warning",
            rolling_win_rate=0,
            rolling_avg_r=0,
            rolling_total_r=0,
            rolling_trades=0,
            window_label=f"Error: {str(e)[:50]}",
            symbols=[],
            alerts=[EdgeAlert(level="warning", message=f"Error computing edge: {str(e)[:100]}")],
            recent_wr_trend=[],
        )
