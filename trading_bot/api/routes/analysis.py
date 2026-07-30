"""
Analysis routes for the API.

Provides endpoints for ICT analysis data:
- Market structure
- Fair Value Gaps
- Order Blocks
- Liquidity levels
- Kill zone status
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import numpy as np

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...config import settings

# Lazy imports to avoid circular dependency
def get_bot_instance():
    from ..main import get_bot_instance as _get_bot_instance
    return _get_bot_instance()

def get_mt5_client():
    from ..main import get_mt5_client as _get_mt5_client
    return _get_mt5_client()
from ...analysis import (
    MarketStructureAnalyzer,
    FVGDetector,
    OrderBlockDetector,
    LiquidityMapper,
    KillZoneChecker,
    FibonacciAnalyzer,
    PowerOfThreeAnalyzer
)
from ...utils.logging import get_logger
from ..database import async_session_maker, AnalysisLogModel, AnalysisLogRepository

logger = get_logger(__name__)
router = APIRouter()

# In-memory signal storage (for quick access without DB)
_recent_signals: List[Dict[str, Any]] = []
MAX_SIGNALS = 50


def convert_numpy_types(obj: Any) -> Any:
    """
    Recursively convert numpy types to native Python types for JSON serialization.
    
    This fixes the PydanticSerializationError for numpy.bool_ and other numpy types.
    """
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj


def add_signal(signal: Dict[str, Any]):
    """Add a signal to the in-memory store and persist to database."""
    global _recent_signals
    # Convert numpy types to native Python types before storing
    signal = convert_numpy_types(signal)
    signal['id'] = str(len(_recent_signals) + 1)
    signal['timestamp'] = datetime.now().isoformat()
    _recent_signals.insert(0, signal)
    if len(_recent_signals) > MAX_SIGNALS:
        _recent_signals = _recent_signals[:MAX_SIGNALS]
    
    # Also persist to database asynchronously
    import asyncio
    try:
        asyncio.create_task(_persist_signal_to_db(signal))
    except RuntimeError:
        # No event loop running (sync context) - skip async persistence
        pass


async def _persist_signal_to_db(signal: Dict[str, Any]) -> Optional[int]:
    """Persist signal to database and return the signal ID."""
    try:
        async with async_session_maker() as session:
            repo = AnalysisLogRepository(session)
            
            # Map signal fields to database model
            db_signal = await repo.create({
                'symbol': signal.get('symbol', ''),
                'timeframe': signal.get('timeframe', 'H1'),
                'session': signal.get('session', ''),
                'market_structure': signal.get('market_structure', ''),
                'trend': signal.get('market_structure', ''),
                'signal_direction': signal.get('direction', 'no_trade'),
                'confidence': signal.get('confidence', 0.0),
                'entry_price': signal.get('entry_price'),
                'stop_loss': signal.get('stop_loss'),
                'take_profit': signal.get('take_profit'),
                'reasoning': signal.get('reasoning', ''),
                'analysis_data': signal.get('details'),
            })
            
            logger.debug(f"Persisted signal {db_signal.id} to database")
            return db_signal.id
            
    except Exception as e:
        logger.error(f"Error persisting signal to database: {e}")
        return None


async def save_signal_to_db(signal: Dict[str, Any]) -> Optional[int]:
    """
    Save signal to database and return the signal ID.
    
    Use this when you need the ID for linking to a trade.
    """
    add_signal(signal)  # Also add to in-memory
    return await _persist_signal_to_db(signal)


def get_signals(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent signals from in-memory store."""
    return _recent_signals[:limit]


# Response Models
class MarketStructureResponse(BaseModel):
    """Market structure analysis response."""
    trend: str
    last_structure_break: Optional[str] = None
    swing_highs: List[Dict[str, Any]]
    swing_lows: List[Dict[str, Any]]
    higher_high: Optional[float] = None
    higher_low: Optional[float] = None
    lower_high: Optional[float] = None
    lower_low: Optional[float] = None


class FVGResponse(BaseModel):
    """Fair Value Gap response."""
    type: str
    top: float
    bottom: float
    midpoint: float
    status: str
    index: int


class OrderBlockResponse(BaseModel):
    """Order Block response."""
    type: str
    top: float
    bottom: float
    midpoint: float
    status: str
    strength: float
    index: int


class LiquidityResponse(BaseModel):
    """Liquidity pool response."""
    type: str
    price: float
    status: str
    touch_count: int


class SessionResponse(BaseModel):
    """Trading session response."""
    current_session: str
    session_name: str
    is_kill_zone: bool
    is_tradeable: bool
    minutes_remaining: int
    next_kill_zone: Optional[str] = None


class OTEResponse(BaseModel):
    """Optimal Trade Entry response."""
    swing_high: float
    swing_low: float
    equilibrium: float
    ote_top: float
    ote_bottom: float
    current_price: float
    price_zone: str
    in_ote: bool


class AMDResponse(BaseModel):
    """AMD/Power of 3 response."""
    current_phase: str
    judas_swing_detected: bool
    judas_direction: Optional[str] = None
    expected_direction: Optional[str] = None


class FullAnalysisResponse(BaseModel):
    """Complete ICT analysis response."""
    symbol: str
    timeframe: str
    timestamp: datetime
    session: SessionResponse
    market_structure: MarketStructureResponse
    fvg_zones: List[FVGResponse]
    order_blocks: List[OrderBlockResponse]
    liquidity: Dict[str, Any]
    ote: Optional[OTEResponse] = None
    amd: Optional[AMDResponse] = None


class SignalResponse(BaseModel):
    """Trading signal response."""
    id: str
    timestamp: str
    symbol: str
    direction: str  # 'long', 'short', or 'no_trade'
    confidence: float
    reasoning: str
    market_structure: str
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None


@router.get("/signals", response_model=List[SignalResponse])
async def get_recent_signals(
    limit: int = Query(10, ge=1, le=50, description="Number of signals to return")
):
    """
    Get recent trading signals from Claude AI analysis.
    
    These are the signals generated when the bot analyzes charts.
    """
    signals = get_signals(limit)
    
    if not signals:
        # Return empty list if no signals yet
        return []
    
    # Convert numpy types before creating response models
    cleaned_signals = [convert_numpy_types(s) for s in signals]
    
    return [
        SignalResponse(
            id=s.get('id', ''),
            timestamp=s.get('timestamp', ''),
            symbol=s.get('symbol', ''),
            direction=s.get('direction', 'no_trade'),
            confidence=float(s.get('confidence', 0.0)) if s.get('confidence') is not None else 0.0,
            reasoning=s.get('reasoning', ''),
            market_structure=s.get('market_structure', 'unknown'),
            entry_price=float(s.get('entry_price')) if s.get('entry_price') is not None else None,
            stop_loss=float(s.get('stop_loss')) if s.get('stop_loss') is not None else None,
            take_profit=float(s.get('take_profit')) if s.get('take_profit') is not None else None,
            risk_reward=float(s.get('risk_reward')) if s.get('risk_reward') is not None else None
        )
        for s in cleaned_signals
    ]


@router.post("/signals")
async def create_signal(
    symbol: str,
    direction: str,
    confidence: float,
    reasoning: str = "",
    market_structure: str = "unknown",
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    risk_reward: Optional[float] = None
):
    """
    Add a new trading signal (called internally when Claude analyzes a chart).
    """
    signal = {
        'symbol': symbol.upper(),
        'direction': direction,
        'confidence': confidence,
        'reasoning': reasoning,
        'market_structure': market_structure,
        'entry_price': entry_price,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'risk_reward': risk_reward
    }
    add_signal(signal)
    return {"message": "Signal added", "signal": signal}


@router.get("/session", response_model=SessionResponse)
async def get_current_session():
    """
    Get current trading session information.
    """
    checker = KillZoneChecker(allowed_sessions=settings.trading.allowed_sessions)
    session_info = checker.get_current_session()
    
    return SessionResponse(
        current_session=session_info.current_session.value,
        session_name=str(session_info.session_name),
        is_kill_zone=bool(session_info.is_kill_zone),
        is_tradeable=bool(session_info.is_tradeable),
        minutes_remaining=int(session_info.minutes_remaining),
        next_kill_zone=str(session_info.next_kill_zone) if session_info.next_kill_zone else None
    )


@router.get("/session/schedule")
async def get_session_schedule():
    """
    Get the daily kill zone schedule.
    """
    checker = KillZoneChecker()
    return checker.get_daily_schedule()


# NOTE: must be registered before the catch-all "/{symbol}" route below.
@router.get("/counterfactuals")
async def get_counterfactuals(
    limit: int = Query(50, ge=1, le=500, description="Recent records to include")
):
    """
    Per-gate counterfactual tally: what each decision gate saved vs cost.

    Records come from blocked/rejected trades scored against realized
    price action (tp_first = the gate cost R, sl_first = the gate saved R).
    """
    from ...services.counterfactual_journal import get_counterfactual_journal

    journal = get_counterfactual_journal()
    summary = journal.summary()
    records = journal.load_records()
    return {
        "summary": summary,
        "recent": records[-limit:][::-1],
    }


@router.get("/{symbol}", response_model=FullAnalysisResponse)
async def get_symbol_analysis(
    symbol: str,
    timeframe: str = Query("H1", description="Timeframe for analysis")
):
    """
    Get complete ICT analysis for a symbol.
    """
    import pandas as pd
    
    symbol = symbol.upper()
    df = None
    
    # Try to get data from MT5 client
    mt5_client = get_mt5_client()
    if mt5_client and mt5_client.is_connected and not mt5_client.is_simulation:
        try:
            ohlcv_data = await mt5_client.get_ohlcv_data(symbol, timeframe, count=200)
            if ohlcv_data:
                df = pd.DataFrame(ohlcv_data)
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
                logger.info(f"Fetched {len(df)} bars for {symbol} from MT5")
        except Exception as e:
            logger.error(f"Error fetching data from MT5: {e}")
            df = None
    
    # If no data, generate sample for demo
    if df is None or (hasattr(df, 'empty') and df.empty):
        from ...mt5.data_fetcher import DataFetcher
        fetcher = DataFetcher()
        df = fetcher._generate_sample_data(200)
        logger.info(f"Using sample data for {symbol} (MT5 not connected or in simulation)")
    
    # Run analyzers (symbol-specific pip_value)
    from ...config import get_symbol_spec
    _api_pip = get_symbol_spec(symbol).pip_size
    structure_analyzer = MarketStructureAnalyzer()
    fvg_detector = FVGDetector(pip_value=_api_pip)
    ob_detector = OrderBlockDetector()
    liquidity_mapper = LiquidityMapper(pip_value=_api_pip)
    fib_analyzer = FibonacciAnalyzer()
    amd_analyzer = PowerOfThreeAnalyzer()
    session_checker = KillZoneChecker(allowed_sessions=settings.trading.allowed_sessions)
    
    # Perform analysis
    structure = structure_analyzer.analyze(df)
    fvg = fvg_detector.detect(df)
    ob = ob_detector.detect(df)
    liquidity = liquidity_mapper.analyze(df)
    session = session_checker.get_current_session()
    
    # OTE analysis
    direction = "bullish" if structure.trend.value == "bullish" else "bearish"
    ote = fib_analyzer.analyze_ote(df, direction)
    
    # AMD analysis
    amd = amd_analyzer.analyze(df)
    
    # Build response
    return FullAnalysisResponse(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.now(),
        session=SessionResponse(
            current_session=session.current_session.value,
            session_name=str(session.session_name),
            is_kill_zone=bool(session.is_kill_zone),
            is_tradeable=bool(session.is_tradeable),
            minutes_remaining=int(session.minutes_remaining),
            next_kill_zone=str(session.next_kill_zone) if session.next_kill_zone else None
        ),
        market_structure=MarketStructureResponse(
            trend=structure.trend.value,
            last_structure_break=structure.last_structure.type.value if structure.last_structure else None,
            swing_highs=[{"index": int(s.index), "price": float(s.price)} for s in structure.swing_highs[-10:]],
            swing_lows=[{"index": int(s.index), "price": float(s.price)} for s in structure.swing_lows[-10:]],
            higher_high=float(structure.higher_high) if structure.higher_high else None,
            higher_low=float(structure.higher_low) if structure.higher_low else None,
            lower_high=float(structure.lower_high) if structure.lower_high else None,
            lower_low=float(structure.lower_low) if structure.lower_low else None
        ),
        fvg_zones=[
            FVGResponse(
                type=f.type.value,
                top=float(f.top),
                bottom=float(f.bottom),
                midpoint=float(f.midpoint),
                status=f.status.value,
                index=int(f.index)
            )
            for f in fvg.active_fvgs[-10:]
        ],
        order_blocks=[
            OrderBlockResponse(
                type=o.type.value,
                top=float(o.top),
                bottom=float(o.bottom),
                midpoint=float(o.midpoint),
                status=o.status.value,
                strength=float(o.strength),
                index=int(o.index)
            )
            for o in ob.active_obs[-10:]
        ],
        liquidity=convert_numpy_types({
            "nearest_bsl": float(liquidity.nearest_bsl) if liquidity.nearest_bsl else None,
            "nearest_ssl": float(liquidity.nearest_ssl) if liquidity.nearest_ssl else None,
            "bsl_pools": [p.to_dict() for p in liquidity.bsl_pools[-5:]],
            "ssl_pools": [p.to_dict() for p in liquidity.ssl_pools[-5:]],
            "recent_sweeps": [s.to_dict() for s in liquidity.recent_sweeps[-3:]]
        }),
        ote=OTEResponse(
            swing_high=float(ote.fib_levels.swing_high),
            swing_low=float(ote.fib_levels.swing_low),
            equilibrium=float(ote.fib_levels.equilibrium),
            ote_top=float(ote.fib_levels.ote_top),
            ote_bottom=float(ote.fib_levels.ote_bottom),
            current_price=float(ote.current_price),
            price_zone=ote.price_zone.value,
            in_ote=bool(ote.in_ote)
        ) if ote else None,
        amd=AMDResponse(
            current_phase=amd.current_phase.value,
            judas_swing_detected=bool(amd.judas_swing is not None),
            judas_direction=str(amd.judas_swing.direction) if amd.judas_swing else None,
            expected_direction=str(amd.expected_direction) if amd.expected_direction else None
        ) if amd else None
    )


@router.get("/{symbol}/structure", response_model=MarketStructureResponse)
async def get_market_structure(
    symbol: str,
    timeframe: str = Query("H1", description="Timeframe")
):
    """
    Get market structure analysis only.
    """
    full_analysis = await get_symbol_analysis(symbol, timeframe)
    return full_analysis.market_structure


@router.get("/{symbol}/fvg", response_model=List[FVGResponse])
async def get_fvg_zones(
    symbol: str,
    timeframe: str = Query("H1", description="Timeframe")
):
    """
    Get Fair Value Gaps only.
    """
    full_analysis = await get_symbol_analysis(symbol, timeframe)
    return full_analysis.fvg_zones


@router.get("/{symbol}/orderblocks", response_model=List[OrderBlockResponse])
async def get_order_blocks(
    symbol: str,
    timeframe: str = Query("H1", description="Timeframe")
):
    """
    Get Order Blocks only.
    """
    full_analysis = await get_symbol_analysis(symbol, timeframe)
    return full_analysis.order_blocks


class ClaudeAnalysisResponse(BaseModel):
    """Claude AI analysis response."""
    success: bool
    direction: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    market_structure: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    order_blocks: List[str] = []
    fvg_zones: List[str] = []
    liquidity_targets: List[str] = []
    analysis_time: Optional[float] = None
    error: Optional[str] = None
    using_mock_data: bool = False


@router.post("/test-claude/{symbol}", response_model=ClaudeAnalysisResponse)
async def test_claude_analysis(
    symbol: str,
    timeframe: str = Query("H1", description="Timeframe for analysis")
):
    """
    Test Claude AI analysis with mock data.
    
    This endpoint:
    1. Generates sample OHLCV data
    2. Creates a chart image
    3. Runs ICT analysis
    4. Sends to Claude for AI analysis
    5. Returns the AI trading signal
    
    Use this to verify your Claude API key is working.
    """
    import pandas as pd
    import base64
    import io
    
    symbol = symbol.upper()
    
    # Check if Claude is configured
    if not settings.claude.api_key:
        return ClaudeAnalysisResponse(
            success=False,
            error="Claude API key not configured. Add CLAUDE_API_KEY to .env.local",
            using_mock_data=True
        )
    
    try:
        # Generate sample data
        from ...mt5.data_fetcher import DataFetcher
        fetcher = DataFetcher()
        df = fetcher._generate_sample_data(200)
        
        # Run ICT analysis on the data
        structure_analyzer = MarketStructureAnalyzer()
        fvg_detector = FVGDetector()
        ob_detector = OrderBlockDetector()
        liquidity_mapper = LiquidityMapper()
        fib_analyzer = FibonacciAnalyzer()
        
        structure = structure_analyzer.analyze(df)
        fvg = fvg_detector.detect(df)
        ob = ob_detector.detect(df)
        liquidity = liquidity_mapper.analyze(df)
        
        # Build analysis context for Claude
        analysis_data = {
            "market_structure": {
                "trend": structure.trend.value,
                "swing_highs": len(structure.swing_highs),
                "swing_lows": len(structure.swing_lows),
                "structure_breaks": len(structure.structure_breaks)
            },
            "fvg": {
                "bullish": len(fvg.bullish_fvgs),
                "bearish": len(fvg.bearish_fvgs),
                "active": len(fvg.active_fvgs)
            },
            "order_blocks": {
                "bullish": len(ob.bullish_obs),
                "bearish": len(ob.bearish_obs),
                "active": len(ob.active_obs)
            },
            "liquidity": {
                "bsl_pools": len(liquidity.bsl_pools),
                "ssl_pools": len(liquidity.ssl_pools),
                "nearest_bsl": float(liquidity.nearest_bsl) if liquidity.nearest_bsl else None,
                "nearest_ssl": float(liquidity.nearest_ssl) if liquidity.nearest_ssl else None
            }
        }
        
        # Try to create chart image
        chart_base64 = None
        try:
            from ...utils.chart_screenshot import create_simple_chart
            if create_simple_chart:
                chart_bytes = create_simple_chart(df, symbol, timeframe)
                if chart_bytes:
                    chart_base64 = base64.b64encode(chart_bytes).decode('utf-8')
        except Exception as chart_error:
            logger.warning(f"Could not create chart image: {chart_error}")
        
        # If no chart, create a simple placeholder
        if not chart_base64:
            # Create a minimal 1x1 pixel PNG as placeholder
            # Claude will rely more on the analysis data
            import struct
            import zlib
            
            def create_minimal_png():
                # 1x1 white pixel PNG
                signature = b'\x89PNG\r\n\x1a\n'
                
                def chunk(chunk_type, data):
                    chunk_len = struct.pack('>I', len(data))
                    chunk_crc = struct.pack('>I', zlib.crc32(chunk_type + data) & 0xffffffff)
                    return chunk_len + chunk_type + data + chunk_crc
                
                ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
                ihdr = chunk(b'IHDR', ihdr_data)
                
                raw_data = b'\x00\xff\xff\xff'
                compressed = zlib.compress(raw_data)
                idat = chunk(b'IDAT', compressed)
                
                iend = chunk(b'IEND', b'')
                
                return signature + ihdr + idat + iend
            
            placeholder_png = create_minimal_png()
            chart_base64 = base64.b64encode(placeholder_png).decode('utf-8')
        
        # Initialize Claude client
        from ...llm.claude_client import ClaudeClient
        from ...llm.context_builder import ContextBuilder
        
        claude = ClaudeClient()
        context_builder = ContextBuilder()
        
        # Get strategy context
        strategy_context = context_builder.get_quick_reference()
        
        # Get current price from sample data
        current_price = float(df['close'].iloc[-1])
        market_data = {
            "current_price": current_price,
            "bid": current_price - 0.00005,
            "ask": current_price + 0.00005,
            "spread": 1.0
        }
        
        # Run Claude analysis
        logger.info(f"Running Claude analysis for {symbol} {timeframe} with mock data")
        result = await claude.analyze_chart_async(
            chart_image_base64=chart_base64,
            symbol=symbol,
            timeframe=timeframe,
            strategy_context=strategy_context,
            market_data=market_data,
            analysis_data=analysis_data,
            use_cache=False  # Don't cache test results
        )
        
        # Extract signal from result
        signal = result.signal
        
        return ClaudeAnalysisResponse(
            success=signal.direction != "no_trade" or signal.confidence > 0,
            direction=signal.direction,
            confidence=signal.confidence,
            reasoning=signal.reasoning,
            market_structure=signal.market_structure,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward=signal.risk_reward,
            order_blocks=signal.order_blocks,
            fvg_zones=signal.fvg_zones,
            liquidity_targets=signal.liquidity_targets,
            analysis_time=result.analysis_time,
            using_mock_data=True
        )
        
    except Exception as e:
        logger.error(f"Error in Claude test analysis: {e}")
        import traceback
        traceback.print_exc()
        return ClaudeAnalysisResponse(
            success=False,
            error=str(e),
            using_mock_data=True
        )
