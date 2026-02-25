"""
Configuration routes for the API.

Provides endpoints for:
- Getting current configuration
- Updating trading settings
- Managing symbols
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...config import settings
from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


# Request/Response Models
class TradingConfigResponse(BaseModel):
    """Trading configuration response."""
    symbols: List[str]
    risk_per_trade: float
    max_daily_trades: int
    min_risk_reward: float
    allowed_sessions: List[str]
    max_daily_drawdown: float
    max_weekly_drawdown: float
    max_daily_profit_target: float


class TimeframeConfigResponse(BaseModel):
    """Timeframe configuration response."""
    higher_tf: str
    execution_tf: str
    confirmation_tf: str


class FullConfigResponse(BaseModel):
    """Complete configuration response."""
    trading: TradingConfigResponse
    timeframes: TimeframeConfigResponse
    mt5_connected: bool
    claude_configured: bool


class UpdateTradingConfigRequest(BaseModel):
    """Request to update trading configuration."""
    symbols: Optional[List[str]] = None
    risk_per_trade: Optional[float] = Field(None, ge=0.001, le=0.05)
    max_daily_trades: Optional[int] = Field(None, ge=1, le=50)
    min_risk_reward: Optional[float] = Field(None, ge=1.0, le=10.0)
    allowed_sessions: Optional[List[str]] = None
    max_daily_drawdown: Optional[float] = Field(None, ge=0.01, le=0.20)
    max_weekly_drawdown: Optional[float] = Field(None, ge=0.01, le=0.30)
    max_daily_profit_target: Optional[float] = Field(None, ge=0.01, le=1.0)
    gate_min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    gate_session_penalty_asian: Optional[float] = Field(None, ge=0.0, le=0.5)
    gate_cooldown_minutes: Optional[int] = Field(None, ge=0, le=120)
    gate_counter_trend_rr_floor: Optional[float] = Field(None, ge=1.0, le=10.0)


class UpdateTimeframeConfigRequest(BaseModel):
    """Request to update timeframe configuration."""
    higher_tf: Optional[str] = None
    execution_tf: Optional[str] = None
    confirmation_tf: Optional[str] = None


class MT5SymbolInfo(BaseModel):
    """MT5 symbol information."""
    name: str
    description: str = ""
    path: str = ""
    category: str = ""
    visible: bool = False
    tradeable: bool = True
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread: Optional[int] = None
    digits: Optional[int] = None
    volume_min: Optional[float] = None
    volume_max: Optional[float] = None


class MT5SymbolListResponse(BaseModel):
    """Response containing list of MT5 symbols."""
    symbols: List[MT5SymbolInfo]
    total: int
    source: str  # "mt5" or "simulation"


class SymbolSyncResponse(BaseModel):
    """Response after syncing symbols from Market Watch."""
    synced: List[str]
    added: List[str]
    removed: List[str]
    current_symbols: List[str]


# Lazy import to avoid circular dependency
def get_mt5_client():
    from ..main import get_mt5_client as _get_mt5_client
    return _get_mt5_client()


@router.get("", response_model=FullConfigResponse)
async def get_config():
    """
    Get the current bot configuration.
    """
    return FullConfigResponse(
        trading=TradingConfigResponse(
            symbols=settings.trading.symbols,
            risk_per_trade=settings.trading.risk_per_trade,
            max_daily_trades=settings.trading.max_daily_trades,
            min_risk_reward=settings.trading.min_risk_reward,
            allowed_sessions=settings.trading.allowed_sessions,
            max_daily_drawdown=settings.trading.max_daily_drawdown,
            max_weekly_drawdown=settings.trading.max_weekly_drawdown,
            max_daily_profit_target=settings.trading.max_daily_profit_target
        ),
        timeframes=TimeframeConfigResponse(
            higher_tf=settings.timeframes.higher_tf,
            execution_tf=settings.timeframes.execution_tf,
            confirmation_tf=settings.timeframes.confirmation_tf
        ),
        mt5_connected=bool(settings.mt5.login),
        claude_configured=bool(settings.claude.api_key)
    )


@router.get("/trading", response_model=TradingConfigResponse)
async def get_trading_config():
    """
    Get trading-specific configuration.
    """
    return TradingConfigResponse(
        symbols=settings.trading.symbols,
        risk_per_trade=settings.trading.risk_per_trade,
        max_daily_trades=settings.trading.max_daily_trades,
        min_risk_reward=settings.trading.min_risk_reward,
        allowed_sessions=settings.trading.allowed_sessions,
        max_daily_drawdown=settings.trading.max_daily_drawdown,
        max_weekly_drawdown=settings.trading.max_weekly_drawdown,
        max_daily_profit_target=settings.trading.max_daily_profit_target
    )


@router.put("/trading", response_model=TradingConfigResponse)
async def update_trading_config(request: UpdateTradingConfigRequest, persist: bool = True):
    """
    Update trading configuration.
    
    Args:
        request: Configuration updates
        persist: If True, save changes to .env.local for persistence across restarts
    """
    from ...config import save_config_to_env_local
    
    updates_to_persist = {}
    
    # Apply updates
    if request.symbols is not None:
        settings.trading.symbols = [s.upper() for s in request.symbols]
        updates_to_persist['symbols'] = settings.trading.symbols
    
    if request.risk_per_trade is not None:
        settings.trading.risk_per_trade = request.risk_per_trade
        updates_to_persist['risk_per_trade'] = request.risk_per_trade
    
    if request.max_daily_trades is not None:
        settings.trading.max_daily_trades = request.max_daily_trades
        updates_to_persist['max_daily_trades'] = request.max_daily_trades
    
    if request.min_risk_reward is not None:
        settings.trading.min_risk_reward = request.min_risk_reward
        updates_to_persist['min_risk_reward'] = request.min_risk_reward
    
    if request.allowed_sessions is not None:
        valid_sessions = ["asian", "london", "new_york", "london_close"]
        sessions = [s.lower() for s in request.allowed_sessions if s.lower() in valid_sessions]
        settings.trading.allowed_sessions = sessions
        updates_to_persist['allowed_sessions'] = sessions
    
    if request.max_daily_drawdown is not None:
        settings.trading.max_daily_drawdown = request.max_daily_drawdown
        updates_to_persist['max_daily_drawdown'] = request.max_daily_drawdown
    
    if request.max_weekly_drawdown is not None:
        settings.trading.max_weekly_drawdown = request.max_weekly_drawdown
        updates_to_persist['max_weekly_drawdown'] = request.max_weekly_drawdown
    
    if request.max_daily_profit_target is not None:
        settings.trading.max_daily_profit_target = request.max_daily_profit_target
        updates_to_persist['max_daily_profit_target'] = request.max_daily_profit_target

    if request.gate_min_confidence is not None:
        settings.trading.gate_min_confidence = request.gate_min_confidence
        updates_to_persist['gate_min_confidence'] = request.gate_min_confidence

    if request.gate_session_penalty_asian is not None:
        settings.trading.gate_session_penalty_asian = request.gate_session_penalty_asian
        updates_to_persist['gate_session_penalty_asian'] = request.gate_session_penalty_asian

    if request.gate_cooldown_minutes is not None:
        settings.trading.gate_cooldown_minutes = request.gate_cooldown_minutes
        updates_to_persist['gate_cooldown_minutes'] = request.gate_cooldown_minutes

    if request.gate_counter_trend_rr_floor is not None:
        settings.trading.gate_counter_trend_rr_floor = request.gate_counter_trend_rr_floor
        updates_to_persist['gate_counter_trend_rr_floor'] = request.gate_counter_trend_rr_floor

    # Gap 5: Persist changes to .env.local
    if persist and updates_to_persist:
        try:
            save_config_to_env_local(updates_to_persist, prefix="TRADING_")
            logger.info(f"Configuration persisted to .env.local: {list(updates_to_persist.keys())}")
        except Exception as e:
            logger.error(f"Failed to persist config to .env.local: {e}")
    
    logger.info("Trading configuration updated via API")
    
    return TradingConfigResponse(
        symbols=settings.trading.symbols,
        risk_per_trade=settings.trading.risk_per_trade,
        max_daily_trades=settings.trading.max_daily_trades,
        min_risk_reward=settings.trading.min_risk_reward,
        allowed_sessions=settings.trading.allowed_sessions,
        max_daily_drawdown=settings.trading.max_daily_drawdown,
        max_weekly_drawdown=settings.trading.max_weekly_drawdown,
        max_daily_profit_target=settings.trading.max_daily_profit_target
    )


@router.get("/timeframes", response_model=TimeframeConfigResponse)
async def get_timeframe_config():
    """
    Get timeframe configuration.
    """
    return TimeframeConfigResponse(
        higher_tf=settings.timeframes.higher_tf,
        execution_tf=settings.timeframes.execution_tf,
        confirmation_tf=settings.timeframes.confirmation_tf
    )


@router.put("/timeframes", response_model=TimeframeConfigResponse)
async def update_timeframe_config(request: UpdateTimeframeConfigRequest):
    """
    Update timeframe configuration.
    """
    valid_timeframes = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
    
    if request.higher_tf is not None:
        if request.higher_tf.upper() not in valid_timeframes:
            raise HTTPException(400, f"Invalid timeframe: {request.higher_tf}")
        settings.timeframes.higher_tf = request.higher_tf.upper()
    
    if request.execution_tf is not None:
        if request.execution_tf.upper() not in valid_timeframes:
            raise HTTPException(400, f"Invalid timeframe: {request.execution_tf}")
        settings.timeframes.execution_tf = request.execution_tf.upper()
    
    if request.confirmation_tf is not None:
        if request.confirmation_tf.upper() not in valid_timeframes:
            raise HTTPException(400, f"Invalid timeframe: {request.confirmation_tf}")
        settings.timeframes.confirmation_tf = request.confirmation_tf.upper()
    
    logger.info("Timeframe configuration updated via API")
    
    return TimeframeConfigResponse(
        higher_tf=settings.timeframes.higher_tf,
        execution_tf=settings.timeframes.execution_tf,
        confirmation_tf=settings.timeframes.confirmation_tf
    )


@router.get("/symbols")
async def get_symbols():
    """
    Get the list of configured trading symbols and available symbols from MT5 Market Watch.
    """
    available_symbols = []
    
    # Try to get symbols from MT5 Market Watch
    mt5_client = get_mt5_client()
    if mt5_client:
        try:
            market_watch = await mt5_client.get_market_watch_symbols()
            available_symbols = [s["name"] for s in market_watch]
        except Exception as e:
            logger.warning(f"Could not fetch MT5 symbols: {e}")
    
    # Fallback to default list if MT5 not available
    if not available_symbols:
        available_symbols = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "XAUUSD"]
    
    return {
        "symbols": settings.trading.symbols,
        "available": available_symbols
    }


# NOTE: Specific routes MUST come before parameterized routes like /symbols/{symbol}
@router.get("/symbols/available", response_model=MT5SymbolListResponse)
async def get_available_symbols(
    group: Optional[str] = None,
    include_info: bool = False
):
    """
    Get all symbols available from the MT5 broker.
    
    Args:
        group: Optional filter pattern (e.g., "*USD*", "Forex*")
        include_info: Whether to include detailed price info
    """
    mt5_client = get_mt5_client()
    
    if not mt5_client:
        raise HTTPException(503, "MT5 client not available")
    
    symbols_data = await mt5_client.get_all_symbols(group=group, include_info=include_info)
    
    symbols = [
        MT5SymbolInfo(
            name=s["name"],
            description=s.get("description", ""),
            path=s.get("path", ""),
            category=s.get("category", ""),
            visible=s.get("visible", False),
            tradeable=s.get("tradeable", True),
            bid=s.get("bid"),
            ask=s.get("ask"),
            spread=s.get("spread"),
            digits=s.get("digits"),
            volume_min=s.get("volume_min"),
            volume_max=s.get("volume_max")
        )
        for s in symbols_data
    ]
    
    return MT5SymbolListResponse(
        symbols=symbols,
        total=len(symbols),
        source="simulation" if mt5_client.is_simulation else "mt5"
    )


@router.get("/symbols/market-watch", response_model=MT5SymbolListResponse)
async def get_market_watch_symbols(include_info: bool = False):
    """
    Get symbols currently visible in the MT5 Market Watch.
    
    These are the symbols you have selected in your MT5 terminal.
    """
    mt5_client = get_mt5_client()
    
    if not mt5_client:
        raise HTTPException(503, "MT5 client not available")
    
    symbols_data = await mt5_client.get_market_watch_symbols(include_info=include_info)
    
    symbols = [
        MT5SymbolInfo(
            name=s["name"],
            description=s.get("description", ""),
            path=s.get("path", ""),
            category=s.get("category", ""),
            visible=True,
            tradeable=s.get("tradeable", True),
            bid=s.get("bid"),
            ask=s.get("ask"),
            spread=s.get("spread"),
            digits=s.get("digits"),
            volume_min=s.get("volume_min"),
            volume_max=s.get("volume_max")
        )
        for s in symbols_data
    ]
    
    return MT5SymbolListResponse(
        symbols=symbols,
        total=len(symbols),
        source="simulation" if mt5_client.is_simulation else "mt5"
    )


@router.post("/symbols/sync-market-watch", response_model=SymbolSyncResponse)
async def sync_symbols_from_market_watch():
    """
    Sync symbols from MT5 Market Watch to the bot's trading configuration.
    
    This will update the bot's symbol list to match what's in your MT5 Market Watch.
    Symbols are validated and persisted to .env.local for persistence.
    """
    from ...config import save_config_to_env_local
    
    mt5_client = get_mt5_client()
    
    if not mt5_client:
        raise HTTPException(503, "MT5 client not available")
    
    # Get current Market Watch symbols
    try:
        market_watch_data = await mt5_client.get_market_watch_symbols()
    except Exception as e:
        logger.error(f"Error fetching Market Watch symbols: {e}")
        raise HTTPException(500, f"Failed to fetch Market Watch symbols: {str(e)}")
    
    if not market_watch_data:
        raise HTTPException(
            400, 
            "No symbols found in Market Watch. Please add symbols to your MT5 Market Watch first."
        )
    
    # Extract and validate symbols
    raw_symbols = [s.get("name", "").upper().strip() for s in market_watch_data if s.get("name")]
    
    # Filter out invalid symbols (empty, too short, contains invalid chars)
    # Valid symbols should be 3-12 chars, alphanumeric only (e.g., EURUSD, XAUUSD, BTCUSD)
    valid_symbols = []
    invalid_symbols = []
    
    for sym in raw_symbols:
        if not sym:
            continue
        # Check if symbol looks valid (3-12 chars, alphanumeric, no special chars except common ones)
        if len(sym) < 3 or len(sym) > 12:
            invalid_symbols.append(sym)
            continue
        # Allow alphanumeric and common separators (but we'll normalize)
        if not all(c.isalnum() or c in ['-', '_'] for c in sym):
            invalid_symbols.append(sym)
            continue
        # Filter out obvious non-symbols (like "SYNC-MARKET-WATCH", "MARKET", etc.)
        if sym in ['SYNC-MARKET-WATCH', 'MARKET', 'WATCH', 'SYNC']:
            invalid_symbols.append(sym)
            continue
        valid_symbols.append(sym)
    
    if not valid_symbols:
        raise HTTPException(
            400,
            f"No valid trading symbols found in Market Watch. "
            f"Found {len(raw_symbols)} symbols but all were invalid. "
            f"Invalid symbols: {', '.join(invalid_symbols[:5])}"
        )
    
    # Sort for consistency
    valid_symbols = sorted(set(valid_symbols))
    
    # Get current bot symbols
    current_symbols = set(settings.trading.symbols)
    new_symbols = set(valid_symbols)
    
    # Calculate changes
    added = sorted(list(new_symbols - current_symbols))
    removed = sorted(list(current_symbols - new_symbols))
    
    # Update settings
    settings.trading.symbols = valid_symbols
    
    # Persist to .env.local
    try:
        save_config_to_env_local(
            {"symbols": valid_symbols},
            prefix="TRADING_"
        )
        logger.info(f"Persisted {len(valid_symbols)} symbols to .env.local")
    except Exception as e:
        logger.error(f"Failed to persist symbols to .env.local: {e}")
        # Don't fail the request, but log the error
    
    # Log warnings about invalid symbols
    if invalid_symbols:
        logger.warning(
            f"Filtered out {len(invalid_symbols)} invalid symbols from Market Watch: "
            f"{', '.join(invalid_symbols[:10])}"
        )
    
    logger.info(
        f"Synced symbols from Market Watch: "
        f"{len(valid_symbols)} valid symbols, {len(added)} added, {len(removed)} removed"
    )
    
    return SymbolSyncResponse(
        synced=valid_symbols,
        added=added,
        removed=removed,
        current_symbols=settings.trading.symbols
    )


@router.post("/symbols/add-to-market-watch/{symbol}")
async def add_symbol_to_market_watch(symbol: str):
    """
    Add a symbol to the MT5 Market Watch.
    
    This adds the symbol to your MT5 terminal's Market Watch window.
    """
    mt5_client = get_mt5_client()
    
    if not mt5_client:
        raise HTTPException(503, "MT5 client not available")
    
    symbol = symbol.upper()
    success = await mt5_client.add_symbol_to_market_watch(symbol)
    
    if success:
        return {
            "message": f"Added {symbol} to Market Watch",
            "symbol": symbol,
            "success": True
        }
    else:
        raise HTTPException(400, f"Failed to add {symbol} to Market Watch")


@router.delete("/symbols/remove-from-market-watch/{symbol}")
async def remove_symbol_from_market_watch(symbol: str):
    """
    Remove a symbol from the MT5 Market Watch.
    """
    mt5_client = get_mt5_client()
    
    if not mt5_client:
        raise HTTPException(503, "MT5 client not available")
    
    symbol = symbol.upper()
    success = await mt5_client.remove_symbol_from_market_watch(symbol)
    
    if success:
        return {
            "message": f"Removed {symbol} from Market Watch",
            "symbol": symbol,
            "success": True
        }
    else:
        raise HTTPException(400, f"Failed to remove {symbol} from Market Watch")


# Parameterized routes MUST come LAST to avoid catching specific routes like /symbols/sync-market-watch
@router.post("/symbols/{symbol}")
async def add_symbol(symbol: str):
    """
    Add a symbol to the trading list.
    """
    symbol = symbol.upper()
    
    if symbol in settings.trading.symbols:
        raise HTTPException(400, f"Symbol {symbol} already in list")
    
    settings.trading.symbols.append(symbol)
    logger.info(f"Added symbol {symbol} to trading list")
    
    return {"message": f"Added {symbol}", "symbols": settings.trading.symbols}


@router.delete("/symbols/{symbol}")
async def remove_symbol(symbol: str):
    """
    Remove a symbol from the trading list.
    """
    symbol = symbol.upper()
    
    if symbol not in settings.trading.symbols:
        raise HTTPException(404, f"Symbol {symbol} not in list")
    
    settings.trading.symbols.remove(symbol)
    logger.info(f"Removed symbol {symbol} from trading list")
    
    return {"message": f"Removed {symbol}", "symbols": settings.trading.symbols}


# ============================================
# ALERT CONFIGURATION ENDPOINTS
# ============================================

class AlertThresholdsResponse(BaseModel):
    """Alert thresholds response."""
    profit_alert_usd: float
    loss_alert_usd: float
    daily_profit_alert: float
    daily_loss_alert: float
    position_count_alert: int
    exposure_alert_lots: float
    win_streak_alert: int
    loss_streak_alert: int
    drawdown_warning_pct: float
    drawdown_critical_pct: float
    equity_high_alert: bool
    milestone_alerts: bool
    volatility_alert_atr_multiple: float
    spread_alert_pips: float
    news_blackout_alert: bool
    high_impact_news_alert: bool
    connection_lost_alert: bool
    error_alert: bool
    daily_summary_alert: bool
    weekly_review_alert: bool


class UpdateAlertThresholdsRequest(BaseModel):
    """Request to update alert thresholds."""
    profit_alert_usd: Optional[float] = None
    loss_alert_usd: Optional[float] = None
    daily_profit_alert: Optional[float] = None
    daily_loss_alert: Optional[float] = None
    position_count_alert: Optional[int] = None
    exposure_alert_lots: Optional[float] = None
    win_streak_alert: Optional[int] = None
    loss_streak_alert: Optional[int] = None
    drawdown_warning_pct: Optional[float] = None
    drawdown_critical_pct: Optional[float] = None
    equity_high_alert: Optional[bool] = None
    milestone_alerts: Optional[bool] = None
    volatility_alert_atr_multiple: Optional[float] = None
    spread_alert_pips: Optional[float] = None
    news_blackout_alert: Optional[bool] = None
    high_impact_news_alert: Optional[bool] = None
    connection_lost_alert: Optional[bool] = None
    error_alert: Optional[bool] = None
    daily_summary_alert: Optional[bool] = None
    weekly_review_alert: Optional[bool] = None


@router.get("/alerts")
async def get_alert_config() -> AlertThresholdsResponse:
    """
    Get current alert thresholds configuration.
    """
    from ...utils.alert_config import get_alert_config
    
    config = get_alert_config()
    return AlertThresholdsResponse(**config.thresholds.to_dict())


@router.put("/alerts")
async def update_alert_config(request: UpdateAlertThresholdsRequest):
    """
    Update alert thresholds configuration.
    
    Only provided fields will be updated.
    """
    from ...utils.alert_config import get_alert_config
    
    config = get_alert_config()
    
    # Update only provided fields
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    
    if updates:
        config.update(**updates)
        logger.info(f"Updated alert thresholds: {list(updates.keys())}")
    
    return {
        "message": "Alert thresholds updated",
        "updated_fields": list(updates.keys()),
        "thresholds": config.thresholds.to_dict()
    }


@router.post("/alerts/reset")
async def reset_alert_config():
    """
    Reset alert thresholds to defaults.
    """
    from ...utils.alert_config import AlertThresholds, get_alert_config
    
    config = get_alert_config()
    config.thresholds = AlertThresholds()
    config.save()
    
    logger.info("Alert thresholds reset to defaults")
    
    return {
        "message": "Alert thresholds reset to defaults",
        "thresholds": config.thresholds.to_dict()
    }


# ============================================
# API KEYS CONFIGURATION ENDPOINTS
# ============================================

class APIKeysStatusResponse(BaseModel):
    """API keys status response (masked for security)."""
    anthropic_configured: bool
    firecrawl_configured: bool
    firecrawl_enabled: bool
    anthropic_key_preview: str  # Last 4 chars only
    firecrawl_key_preview: str  # Last 4 chars only


class UpdateAPIKeysRequest(BaseModel):
    """Request to update API keys."""
    anthropic_api_key: Optional[str] = None
    firecrawl_api_key: Optional[str] = None
    firecrawl_enabled: Optional[bool] = None


@router.get("/api-keys", response_model=APIKeysStatusResponse)
async def get_api_keys_status():
    """
    Get API keys configuration status.
    
    Returns masked key previews for security (only shows if configured and last 4 chars).
    """
    anthropic_key = settings.claude.api_key
    firecrawl_key = settings.firecrawl.api_key
    
    return APIKeysStatusResponse(
        anthropic_configured=bool(anthropic_key and len(anthropic_key) > 10),
        firecrawl_configured=bool(firecrawl_key and len(firecrawl_key) > 10),
        firecrawl_enabled=settings.firecrawl.enabled,
        anthropic_key_preview=f"...{anthropic_key[-4:]}" if anthropic_key and len(anthropic_key) > 4 else "",
        firecrawl_key_preview=f"...{firecrawl_key[-4:]}" if firecrawl_key and len(firecrawl_key) > 4 else ""
    )


@router.put("/api-keys")
async def update_api_keys(request: UpdateAPIKeysRequest):
    """
    Update API keys configuration.
    
    Keys are saved to .env.local for persistence across restarts.
    """
    from ...config import save_config_to_env_local, reload_settings
    
    updates_anthropic = {}
    updates_firecrawl = {}
    
    if request.anthropic_api_key is not None:
        # Validate key format (basic check)
        if request.anthropic_api_key and not request.anthropic_api_key.startswith("sk-"):
            raise HTTPException(400, "Invalid Anthropic API key format. Should start with 'sk-'")
        updates_anthropic['api_key'] = request.anthropic_api_key
        settings.claude.api_key = request.anthropic_api_key
    
    if request.firecrawl_api_key is not None:
        updates_firecrawl['api_key'] = request.firecrawl_api_key
        settings.firecrawl.api_key = request.firecrawl_api_key
    
    if request.firecrawl_enabled is not None:
        updates_firecrawl['enabled'] = request.firecrawl_enabled
        settings.firecrawl.enabled = request.firecrawl_enabled
    
    # Persist to .env.local
    try:
        if updates_anthropic:
            save_config_to_env_local(updates_anthropic, prefix="ANTHROPIC_")
            logger.info("Anthropic API key updated and persisted")
        
        if updates_firecrawl:
            save_config_to_env_local(updates_firecrawl, prefix="FIRECRAWL_")
            logger.info("Firecrawl settings updated and persisted")
            
    except Exception as e:
        logger.error(f"Failed to persist API keys to .env.local: {e}")
        raise HTTPException(500, f"Failed to save API keys: {str(e)}")
    
    return {
        "message": "API keys updated successfully",
        "anthropic_configured": bool(settings.claude.api_key),
        "firecrawl_configured": bool(settings.firecrawl.api_key),
        "firecrawl_enabled": settings.firecrawl.enabled
    }
