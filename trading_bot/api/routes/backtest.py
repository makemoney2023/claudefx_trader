"""
API routes for backtesting: ICT strategy, Claude Replay, Walk-Forward Optimizer.

Provides endpoints to run backtests, estimate costs, list past runs, and cancel running jobs.
Long-running runs are executed as background tasks with progress persisted to BacktestRunModel.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import RequireAuth
from ..database import async_session_maker, BacktestRunModel
from ...utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

_backtest_tasks: Dict[int, asyncio.Task] = {}
MAX_CONCURRENT_BACKTESTS = 2


def _validate_dates(start_str: str, end_str: str) -> tuple:
    """Parse and validate date strings. Raises HTTPException on invalid input."""
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid start_date format: '{start_str}'. Use YYYY-MM-DD.")
    try:
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid end_date format: '{end_str}'. Use YYYY-MM-DD.")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")
    if (end_dt - start_dt).days > 365:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 1 year")
    return start_dt, end_dt


def _check_concurrent_limit():
    """Raise 429 if too many backtests are already running."""
    active = sum(1 for t in _backtest_tasks.values() if not t.done())
    if active >= MAX_CONCURRENT_BACKTESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Max {MAX_CONCURRENT_BACKTESTS} concurrent backtests allowed. Wait for a run to finish or cancel one.",
        )


async def cleanup_orphaned_runs():
    """Mark any 'running' backtest rows as 'failed' on startup."""
    try:
        async with async_session_maker() as session:
            from sqlalchemy import select
            q = select(BacktestRunModel).where(BacktestRunModel.status == "running")
            result = await session.execute(q)
            rows = result.scalars().all()
            for r in rows:
                r.status = "failed"
                r.error_message = "Server restarted while backtest was running"
                r.completed_at = datetime.utcnow()
            if rows:
                await session.commit()
                logger.info(f"[BACKTEST] Cleaned up {len(rows)} orphaned 'running' backtest rows")
    except Exception as e:
        logger.warning(f"[BACKTEST] Orphan cleanup failed: {e}")


def _get_mt5_client():
    from ..main import get_mt5_client as _get_mt5_client
    return _get_mt5_client()


def _get_bot_instance():
    from ..main import get_bot_instance as _get_bot_instance
    return _get_bot_instance()


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class IctBacktestRequest(BaseModel):
    symbol: str
    timeframe: str = "H1"
    start_date: str  # YYYY-MM-DD
    end_date: str
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.01
    min_risk_reward: float = 2.0


class ReplayEstimateRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    interval_hours: float = 4.0


class ReplayBacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    interval_hours: float = 4.0
    max_signals: int = 100


class OptimizerRequest(BaseModel):
    lookback_days: int = 180
    n_folds: int = 3
    train_ratio: float = 0.7
    param_space: Optional[Dict[str, List[Any]]] = None


class BacktestRunResponse(BaseModel):
    id: int
    run_type: str
    status: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    start_date: str
    end_date: str
    progress_pct: int = 0
    current_step: str = ""
    total_trades: Optional[int] = None
    win_rate: Optional[float] = None
    net_profit: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    max_drawdown: Optional[float] = None
    estimated_cost: Optional[float] = None
    actual_cost: Optional[float] = None
    error_message: Optional[str] = None
    config_json: Optional[Dict[str, Any]] = None
    result_json: Optional[Dict[str, Any]] = None
    created_at: str
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helpers: persist replay result and feed learnings
# ---------------------------------------------------------------------------

def _replay_trade_to_dict(trade: Any) -> Dict[str, Any]:
    """Serialize a ReplayTrade for JSON storage."""
    sig = trade.signal
    return {
        "timestamp": sig.timestamp.isoformat() if hasattr(sig.timestamp, "isoformat") else str(sig.timestamp),
        "symbol": sig.symbol,
        "direction": sig.direction,
        "confidence": sig.confidence,
        "entry_price": sig.entry_price,
        "stop_loss": sig.stop_loss,
        "take_profit": sig.take_profit,
        "reasoning": getattr(sig, "reasoning", "") or "",
        "outcome": trade.outcome,
        "exit_price": trade.exit_price,
        "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
        "pnl_pips": trade.pnl_pips,
        "r_multiple": trade.r_multiple,
        "mfe_pips": getattr(trade, "mfe_pips", 0),
        "mae_pips": getattr(trade, "mae_pips", 0),
        "bars_held": getattr(trade, "bars_held", 0),
    }


async def _feed_replay_learnings(run_id: int, result: Any, learning_service=None) -> int:
    """
    Store qualifying replay trades (losses and wins > 2R) as learnings.
    Returns count of learnings stored.
    """
    try:
        if learning_service is None:
            from ...services.trade_learning_service import TradeLearningService
            learning_service = TradeLearningService()
        count = 0
        for i, t in enumerate(result.trades):
            if t.outcome == "loss" or (t.outcome == "win" and t.r_multiple >= 2.0):
                trade_id = f"bt-replay-{run_id}-{result.symbol}-{i:03d}"

                ts = t.signal.timestamp
                h = ts.hour if hasattr(ts, "hour") else 0
                if 0 <= h < 7:
                    session_name = "asian"
                elif 7 <= h < 13:
                    session_name = "london"
                elif 13 <= h < 17:
                    session_name = "new_york"
                else:
                    session_name = "new_york_pm"

                review = {
                    "grade": "B" if t.outcome == "win" else "C",
                    "outcome": t.outcome,
                    "analysis": (t.signal.reasoning or "")[:5000],
                    "what_went_right": [f"R-multiple: {t.r_multiple:.2f}"] if t.outcome == "win" else [],
                    "what_went_wrong": [f"SL hit, R: {t.r_multiple:.2f}"] if t.outcome == "loss" else [],
                    "learnings": [f"Backtest trade {t.signal.direction} {result.symbol} | bars_held={t.bars_held}"],
                    "source": "backtest",
                }
                await learning_service.store_trade_review(
                    trade_id=trade_id,
                    symbol=result.symbol,
                    direction=t.signal.direction,
                    profit_loss=t.r_multiple,
                    r_multiple=t.r_multiple,
                    review=review,
                    session=session_name,
                    setup_type="ICT",
                    entry_reason=(t.signal.reasoning or "")[:2000],
                    original_confidence=t.signal.confidence,
                    timeframe="M15",
                )
                count += 1
        return count
    except Exception as e:
        logger.warning(f"[BACKTEST] Feed learnings failed: {e}")
        return 0


# ---------------------------------------------------------------------------
# GET /runs — List past runs
# ---------------------------------------------------------------------------

@router.get("/runs", response_model=List[BacktestRunResponse])
async def list_backtest_runs(
    run_type: Optional[str] = Query(None, description="Filter by ict, replay, optimizer"),
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List backtest runs with optional filters. Excludes full result_json in list."""
    async with async_session_maker() as session:
        from sqlalchemy import select, desc
        q = select(BacktestRunModel).order_by(desc(BacktestRunModel.created_at))
        if run_type:
            q = q.where(BacktestRunModel.run_type == run_type)
        if status:
            q = q.where(BacktestRunModel.status == status)
        q = q.offset(offset).limit(limit)
        result = await session.execute(q)
        rows = result.scalars().all()
        out = []
        for r in rows:
            out.append(BacktestRunResponse(
                id=r.id,
                run_type=r.run_type,
                status=r.status,
                symbol=r.symbol,
                timeframe=r.timeframe,
                start_date=r.start_date.isoformat() if r.start_date else "",
                end_date=r.end_date.isoformat() if r.end_date else "",
                progress_pct=r.progress_pct or 0,
                current_step=r.current_step or "",
                total_trades=r.total_trades,
                win_rate=r.win_rate,
                net_profit=r.net_profit,
                sharpe_ratio=r.sharpe_ratio,
                profit_factor=r.profit_factor,
                max_drawdown=r.max_drawdown,
                estimated_cost=r.estimated_cost,
                actual_cost=r.actual_cost,
                error_message=r.error_message,
                config_json=r.config_json,
                result_json=None,
                created_at=r.created_at.isoformat() if r.created_at else "",
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
            ))
        return out


# ---------------------------------------------------------------------------
# GET /runs/{run_id} — Full detail
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}", response_model=BacktestRunResponse)
async def get_backtest_run(run_id: int):
    """Get full backtest run including result_json."""
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))
        r = result.scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="Backtest run not found")
        return BacktestRunResponse(
            id=r.id,
            run_type=r.run_type,
            status=r.status,
            symbol=r.symbol,
            timeframe=r.timeframe,
            start_date=r.start_date.isoformat() if r.start_date else "",
            end_date=r.end_date.isoformat() if r.end_date else "",
            progress_pct=r.progress_pct or 0,
            current_step=r.current_step or "",
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            net_profit=r.net_profit,
            sharpe_ratio=r.sharpe_ratio,
            profit_factor=r.profit_factor,
            max_drawdown=r.max_drawdown,
            estimated_cost=r.estimated_cost,
            actual_cost=r.actual_cost,
            error_message=r.error_message,
            config_json=r.config_json,
            result_json=r.result_json,
            created_at=r.created_at.isoformat() if r.created_at else "",
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
        )


# ---------------------------------------------------------------------------
# DELETE /runs/{run_id}
# ---------------------------------------------------------------------------

@router.delete("/runs/{run_id}", dependencies=[Depends(RequireAuth())])
async def delete_backtest_run(run_id: int):
    """Delete a backtest run."""
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))
        r = result.scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=404, detail="Backtest run not found")
        await session.delete(r)
        await session.commit()
    if run_id in _backtest_tasks:
        _backtest_tasks[run_id].cancel()
        del _backtest_tasks[run_id]
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/cancel
# ---------------------------------------------------------------------------

@router.post("/runs/{run_id}/cancel", dependencies=[Depends(RequireAuth())])
async def cancel_backtest_run(run_id: int):
    """Cancel a running backtest."""
    if run_id in _backtest_tasks:
        _backtest_tasks[run_id].cancel()
        del _backtest_tasks[run_id]
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))
        r = result.scalar_one_or_none()
        if r and r.status == "running":
            r.status = "cancelled"
            r.current_step = "Cancelled by user"
            await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /replay/estimate — Cost estimate (no auth)
# ---------------------------------------------------------------------------

@router.post("/replay/estimate")
async def replay_estimate(body: ReplayEstimateRequest):
    """Estimate API cost for a replay backtest without running it."""
    from ...backtesting.replay import ClaudeReplayBacktester
    start, end = _validate_dates(body.start_date, body.end_date)
    bt = ClaudeReplayBacktester(claude_client=None, mt5_client=None)
    estimate = await bt.estimate_cost(body.symbol, start, end, body.interval_hours)
    return estimate


# ---------------------------------------------------------------------------
# POST /ict — Run ICT backtest (background task)
# ---------------------------------------------------------------------------

async def _run_ict_task(run_id: int):
    """Background task: run ICT backtest and persist results."""
    from ...backtesting.engine import Backtester, BacktestConfig

    async with async_session_maker() as session:
        from sqlalchemy import select
        r = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if not r or r.status != "running":
            return
        cfg = r.config_json or {}

    try:
        config = BacktestConfig(
            symbol=cfg.get("symbol", ""),
            timeframe=cfg.get("timeframe", "H1"),
            start_date=datetime.strptime(cfg["start_date"], "%Y-%m-%d"),
            end_date=datetime.strptime(cfg["end_date"], "%Y-%m-%d"),
            initial_balance=float(cfg.get("initial_balance", 10000)),
            risk_per_trade=float(cfg.get("risk_per_trade", 0.01)),
            min_risk_reward=float(cfg.get("min_risk_reward", 2.0)),
            data_source="mt5",
        )
        backtester = Backtester(config)
        result = await asyncio.to_thread(backtester.run, None, True)
        res_dict = result.to_dict()
        metrics = res_dict.get("metrics", {})
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "completed"
                row.progress_pct = 100
                row.current_step = "Completed"
                row.result_json = res_dict
                row.total_trades = metrics.get("total_trades") or 0
                row.win_rate = metrics.get("win_rate")
                row.net_profit = metrics.get("net_profit")
                row.sharpe_ratio = metrics.get("sharpe_ratio")
                row.profit_factor = metrics.get("profit_factor")
                row.max_drawdown = metrics.get("max_drawdown")
                row.completed_at = datetime.utcnow()
                await session.commit()
    except asyncio.CancelledError:
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "cancelled"
                row.current_step = "Cancelled"
                row.completed_at = datetime.utcnow()
                await session.commit()
        return
    except Exception as e:
        logger.exception(f"[BACKTEST] ICT run {run_id} failed: {e}")
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = str(e)[:2000]
                row.completed_at = datetime.utcnow()
                await session.commit()
    finally:
        _backtest_tasks.pop(run_id, None)


@router.post("/ict", response_model=BacktestRunResponse, dependencies=[Depends(RequireAuth())])
async def start_ict_backtest(body: IctBacktestRequest):
    """Start ICT strategy backtest as a background task."""
    _check_concurrent_limit()
    start_dt, end_dt = _validate_dates(body.start_date, body.end_date)

    config_json = {
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "initial_balance": body.initial_balance,
        "risk_per_trade": body.risk_per_trade,
        "min_risk_reward": body.min_risk_reward,
    }

    async with async_session_maker() as session:
        row = BacktestRunModel(
            run_type="ict",
            status="running",
            symbol=body.symbol,
            timeframe=body.timeframe,
            start_date=start_dt,
            end_date=end_dt,
            config_json=config_json,
            progress_pct=0,
            current_step="Starting ICT backtest...",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        run_id = row.id

    task = asyncio.create_task(_run_ict_task(run_id))
    _backtest_tasks[run_id] = task

    async with async_session_maker() as session:
        from sqlalchemy import select
        r = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=500, detail="Run record lost")
        return BacktestRunResponse(
            id=r.id,
            run_type=r.run_type,
            status=r.status,
            symbol=r.symbol,
            timeframe=r.timeframe,
            start_date=r.start_date.isoformat() if r.start_date else "",
            end_date=r.end_date.isoformat() if r.end_date else "",
            progress_pct=r.progress_pct or 0,
            current_step=r.current_step or "",
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            net_profit=r.net_profit,
            sharpe_ratio=r.sharpe_ratio,
            profit_factor=r.profit_factor,
            max_drawdown=r.max_drawdown,
            estimated_cost=r.estimated_cost,
            actual_cost=r.actual_cost,
            error_message=r.error_message,
            config_json=r.config_json,
            result_json=r.result_json,
            created_at=r.created_at.isoformat() if r.created_at else "",
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
        )


# ---------------------------------------------------------------------------
# POST /replay — Start replay backtest (background)
# ---------------------------------------------------------------------------

async def _run_replay_task(run_id: int):
    """Background task: run replay backtester and persist result + learnings."""
    logger.info(f"[REPLAY-TASK] Starting background replay task for run_id={run_id}")
    from ...backtesting.replay import ClaudeReplayBacktester, ReplayResult
    from ...llm.claude_client import ClaudeClient
    from ...services.trade_learning_service import TradeLearningService

    async with async_session_maker() as session:
        from sqlalchemy import select
        r = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if not r or r.status != "running":
            return
        cfg = r.config_json or {}
        symbol = cfg.get("symbol", "")
        start_date = datetime.strptime(cfg.get("start_date", ""), "%Y-%m-%d")
        end_date = datetime.strptime(cfg.get("end_date", ""), "%Y-%m-%d")
        interval_hours = float(cfg.get("interval_hours", 4.0))
        max_signals = int(cfg.get("max_signals", 100))

    mt5 = _get_mt5_client()
    if mt5 and not getattr(mt5, "is_connected", False):
        try:
            await mt5.ensure_connected()
        except Exception as e:
            logger.warning(f"[REPLAY-TASK] ensure_connected failed: {e}")
    if not mt5 or not getattr(mt5, "is_connected", False):
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = "MT5 not connected"
                row.completed_at = datetime.utcnow()
                await session.commit()
        return

    bot = _get_bot_instance()
    claude = getattr(bot, "claude_client", None) if bot else None
    if not claude or not getattr(claude, "api_key", None):
        claude = ClaudeClient()
    if not claude or not getattr(claude, "api_key", None):
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = "Claude API key not configured"
                row.completed_at = datetime.utcnow()
                await session.commit()
        return

    learning_service = TradeLearningService()
    bt = ClaudeReplayBacktester(claude_client=claude, mt5_client=mt5, trade_learning_service=learning_service)

    _live_log: list = []

    async def _update_progress(pct: int, step: str, log_entry=None):
        try:
            if log_entry:
                _live_log.append(log_entry)
                if len(_live_log) > 50:
                    _live_log.pop(0)
            async with async_session_maker() as sess:
                from sqlalchemy import select
                row = (await sess.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
                if row:
                    row.progress_pct = pct
                    row.current_step = step
                    cfg = row.config_json or {}
                    cfg["live_log"] = list(_live_log)
                    row.config_json = cfg
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(row, "config_json")
                    await sess.commit()
        except Exception:
            pass

    logger.info(f"[REPLAY-TASK] Run {run_id}: launching bt.run({symbol}, {start_date}, {end_date}, interval={interval_hours}h)")
    try:
        result = await bt.run(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            interval_hours=interval_hours,
            max_signals=max_signals,
            dry_run=False,
            progress_callback=_update_progress,
        )
    except asyncio.CancelledError:
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "cancelled"
                row.current_step = "Cancelled"
                row.completed_at = datetime.utcnow()
                await session.commit()
        return
    except Exception as e:
        logger.exception(f"[BACKTEST] Replay run {run_id} failed: {e}")
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = str(e)[:2000]
                row.completed_at = datetime.utcnow()
                await session.commit()
        return

    result_dict = result.to_dict()
    result_dict["trades"] = [_replay_trade_to_dict(t) for t in result.trades]
    learnings_stored = await _feed_replay_learnings(run_id, result, learning_service=learning_service)
    result_dict["learnings_stored"] = learnings_stored

    async with async_session_maker() as session:
        from sqlalchemy import select
        row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if row:
            row.status = "completed"
            row.progress_pct = 100
            row.current_step = "Completed"
            row.result_json = result_dict
            row.total_trades = result.total_trades
            row.win_rate = result.win_rate
            row.net_profit = None
            row.sharpe_ratio = result.sharpe_ratio
            row.profit_factor = result.profit_factor
            row.max_drawdown = getattr(result, "max_drawdown_r", None)
            row.estimated_cost = result.estimated_cost
            row.actual_cost = result.estimated_cost
            row.completed_at = datetime.utcnow()
            await session.commit()

    _backtest_tasks.pop(run_id, None)


@router.post("/replay", response_model=BacktestRunResponse, dependencies=[Depends(RequireAuth())])
async def start_replay_backtest(body: ReplayBacktestRequest):
    """Start Claude replay backtest as a background task. Returns run id and status immediately."""
    _check_concurrent_limit()
    start_dt, end_dt = _validate_dates(body.start_date, body.end_date)
    config_json = {
        "symbol": body.symbol,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "interval_hours": body.interval_hours,
        "max_signals": body.max_signals,
    }
    async with async_session_maker() as session:
        row = BacktestRunModel(
            run_type="replay",
            status="running",
            symbol=body.symbol,
            timeframe="M15",
            start_date=start_dt,
            end_date=end_dt,
            config_json=config_json,
            progress_pct=0,
            current_step="Starting replay...",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        run_id = row.id

    task = asyncio.create_task(_run_replay_task(run_id))
    _backtest_tasks[run_id] = task

    async with async_session_maker() as session:
        from sqlalchemy import select
        r = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=500, detail="Run record lost")
        return BacktestRunResponse(
            id=r.id,
            run_type=r.run_type,
            status=r.status,
            symbol=r.symbol,
            timeframe=r.timeframe,
            start_date=r.start_date.isoformat() if r.start_date else "",
            end_date=r.end_date.isoformat() if r.end_date else "",
            progress_pct=r.progress_pct or 0,
            current_step=r.current_step or "",
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            net_profit=r.net_profit,
            sharpe_ratio=r.sharpe_ratio,
            profit_factor=r.profit_factor,
            max_drawdown=r.max_drawdown,
            estimated_cost=r.estimated_cost,
            actual_cost=r.actual_cost,
            error_message=r.error_message,
            config_json=r.config_json,
            result_json=r.result_json,
            created_at=r.created_at.isoformat() if r.created_at else "",
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
        )


# ---------------------------------------------------------------------------
# POST /optimizer — Start walk-forward optimizer (background)
# ---------------------------------------------------------------------------

async def _run_optimizer_task(run_id: int):
    """Background task: run walk-forward optimizer and persist result."""
    from ...backtesting.optimizer import WalkForwardOptimizer

    async with async_session_maker() as session:
        from sqlalchemy import select
        r = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if not r or r.status != "running":
            return
        cfg = r.config_json or {}
        lookback_days = int(cfg.get("lookback_days", 180))
        n_folds = int(cfg.get("n_folds", 3))
        train_ratio = float(cfg.get("train_ratio", 0.7))
        param_space = cfg.get("param_space")

    async def _opt_progress(pct: int, step: str):
        try:
            async with async_session_maker() as sess:
                from sqlalchemy import select
                row = (await sess.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
                if row:
                    row.progress_pct = pct
                    row.current_step = step
                    await sess.commit()
        except Exception:
            pass

    try:
        opt = WalkForwardOptimizer(param_space=param_space)
        result = await opt.optimize(
            lookback_days=lookback_days,
            n_folds=n_folds,
            train_ratio=train_ratio,
            progress_callback=_opt_progress,
        )
    except asyncio.CancelledError:
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "cancelled"
                row.current_step = "Cancelled"
                row.completed_at = datetime.utcnow()
                await session.commit()
        return
    except Exception as e:
        logger.exception(f"[BACKTEST] Optimizer run {run_id} failed: {e}")
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = str(e)[:2000]
                row.completed_at = datetime.utcnow()
                await session.commit()
        return

    if result is None:
        async with async_session_maker() as session:
            from sqlalchemy import select
            row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
            if row:
                row.status = "failed"
                row.error_message = "Insufficient trade data for optimization"
                row.completed_at = datetime.utcnow()
                await session.commit()
        _backtest_tasks.pop(run_id, None)
        return

    result_dict = result.to_dict()
    async with async_session_maker() as session:
        from sqlalchemy import select
        row = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if row:
            row.status = "completed"
            row.progress_pct = 100
            row.current_step = "Completed"
            row.result_json = result_dict
            row.total_trades = result_dict.get("out_of_sample_trades") or result_dict.get("in_sample_trades")
            row.win_rate = result_dict.get("out_of_sample_win_rate") or result_dict.get("in_sample_win_rate")
            row.sharpe_ratio = result_dict.get("out_of_sample_sharpe") or result_dict.get("in_sample_sharpe")
            row.completed_at = datetime.utcnow()
            await session.commit()

    _backtest_tasks.pop(run_id, None)


@router.post("/optimizer", response_model=BacktestRunResponse, dependencies=[Depends(RequireAuth())])
async def start_optimizer(body: OptimizerRequest):
    """Start walk-forward parameter optimization as a background task."""
    _check_concurrent_limit()
    config_json = {
        "lookback_days": body.lookback_days,
        "n_folds": body.n_folds,
        "train_ratio": body.train_ratio,
        "param_space": body.param_space,
    }
    async with async_session_maker() as session:
        row = BacktestRunModel(
            run_type="optimizer",
            status="running",
            symbol=None,
            timeframe=None,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow(),
            config_json=config_json,
            progress_pct=0,
            current_step="Running walk-forward optimization...",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        run_id = row.id

    task = asyncio.create_task(_run_optimizer_task(run_id))
    _backtest_tasks[run_id] = task

    async with async_session_maker() as session:
        from sqlalchemy import select
        r = (await session.execute(select(BacktestRunModel).where(BacktestRunModel.id == run_id))).scalar_one_or_none()
        if not r:
            raise HTTPException(status_code=500, detail="Run record lost")
        return BacktestRunResponse(
            id=r.id,
            run_type=r.run_type,
            status=r.status,
            symbol=r.symbol,
            timeframe=r.timeframe,
            start_date=r.start_date.isoformat() if r.start_date else "",
            end_date=r.end_date.isoformat() if r.end_date else "",
            progress_pct=r.progress_pct or 0,
            current_step=r.current_step or "",
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            net_profit=r.net_profit,
            sharpe_ratio=r.sharpe_ratio,
            profit_factor=r.profit_factor,
            max_drawdown=r.max_drawdown,
            estimated_cost=r.estimated_cost,
            actual_cost=r.actual_cost,
            error_message=r.error_message,
            config_json=r.config_json,
            result_json=r.result_json,
            created_at=r.created_at.isoformat() if r.created_at else "",
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
        )
