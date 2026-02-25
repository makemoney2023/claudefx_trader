"""
FastAPI Application for ICT Trading Bot Dashboard.

Main entry point for the API server with CORS, lifespan management,
and route registration.

The trading bot runs as a background task within this process,
sharing the same bot_state for real-time activity updates.
"""

from contextlib import asynccontextmanager
from typing import Optional
import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Rate limiting - optional dependency
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    RATE_LIMITING_AVAILABLE = True
except ImportError:
    RATE_LIMITING_AVAILABLE = False

from ..config import settings
from ..utils.logging import setup_logging, get_logger
from .routes import trades, analysis, config, performance
from .websocket import router as ws_router
from .auth import get_api_key, is_protected_endpoint

logger = get_logger(__name__)

# Rate limiter instance
limiter = None
if RATE_LIMITING_AVAILABLE:
    limiter = Limiter(key_func=get_remote_address, default_limits=["1000/minute"])

# Global references for bot components (set during startup)
_bot_instance = None
_trade_journal = None
_bot_task: Optional[asyncio.Task] = None
_firecrawl_service = None
_intelligence_task: Optional[asyncio.Task] = None
_command_handler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan.
    
    Handles startup and shutdown events for the FastAPI app.
    The trading bot runs as a background task within this process.
    """
    global _bot_instance, _trade_journal, _mt5_client, _bot_task
    
    # Startup
    setup_logging()
    logger.info("Starting ICT Trading Bot API Server")
    
    # Initialize database
    from .database import init_db
    await init_db()
    logger.info("Database initialized")
    
    # Initialize MT5 client
    from ..mt5.client import MT5Client
    _mt5_client = MT5Client()
    
    try:
        connected = await _mt5_client.connect()
        if connected:
            if _mt5_client.is_simulation:
                logger.warning("MT5 running in SIMULATION mode")
            else:
                logger.info("MT5 connected to REAL account")
                account = await _mt5_client.get_account_info()
                if account:
                    logger.info(f"Account: {account.login}, Balance: {account.balance} {account.currency}")
        else:
            logger.error("Failed to connect to MT5")
    except Exception as e:
        logger.error(f"MT5 connection error: {e}")
    
    # Initialize components
    from ..utils.trade_journal import TradeJournal
    _trade_journal = TradeJournal()
    
    # Initialize Firecrawl service independently (before bot starts)
    # This ensures intelligence is available even if bot fails to initialize
    global _firecrawl_service, _intelligence_task
    try:
        from ..services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        firecrawl_api_key = os.getenv("FIRECRAWL_API_KEY") or settings.firecrawl.api_key
        _firecrawl_service = FirecrawlIntelligenceService(
            api_key=firecrawl_api_key,
            enabled=settings.firecrawl.enabled
        )
        
        # Sync to routes immediately
        from .routes.intelligence import set_firecrawl_service
        from .routes.news import set_firecrawl_service as set_news_firecrawl
        set_firecrawl_service(_firecrawl_service)
        set_news_firecrawl(_firecrawl_service)
        
        if _firecrawl_service.is_available:
            logger.info("Firecrawl intelligence service initialized and synced to routes")
            # Start background refresh task
            _intelligence_task = asyncio.create_task(_refresh_intelligence_background())
            logger.info("Intelligence background refresh task started (15 min interval)")
        else:
            logger.warning("Firecrawl service not available - check API key")
    except Exception as e:
        logger.error(f"Failed to initialize Firecrawl service: {e}")
    
    # Force-release any stale instance lock from a previous (killed) server process
    from ..utils.instance_lock import release_instance_lock
    release_instance_lock()
    logger.info("Cleared any stale instance lock from previous run")
    
    # Auto-start the trading bot as a background task
    logger.info("Starting trading bot as background task...")
    _bot_task = asyncio.create_task(_run_bot_background())
    
    # Start Telegram command handler (polling for /commands)
    global _command_handler
    try:
        from ..utils.telegram_commands import TelegramCommandHandler
        _command_handler = TelegramCommandHandler(bot_instance=None)  # bot set after init
        await _command_handler.start_polling()
        logger.info("Telegram command handler started")
    except Exception as e:
        logger.warning(f"Telegram command handler failed to start: {e}")
    
    # Log API key for protected endpoints
    api_key = get_api_key()
    logger.info("=" * 50)
    logger.info(f"🔐 API Key for protected endpoints: {api_key}")
    logger.info("Use header: X-API-Key: <key>")
    logger.info("=" * 50)
    
    logger.info("API Server started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down API Server")
    
    # Stop the bot
    if _bot_instance:
        _bot_instance.stop()
    
    if _bot_task:
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass
    
    # Stop Telegram command handler
    if _command_handler:
        _command_handler.stop()
    
    # Stop intelligence refresh task
    if _intelligence_task:
        _intelligence_task.cancel()
        try:
            await _intelligence_task
        except asyncio.CancelledError:
            pass
    
    if _mt5_client:
        await _mt5_client.disconnect()


async def _refresh_intelligence_background():
    """
    Refresh market intelligence periodically with tiered fetching.
    
    This implements a three-tier intelligence refresh strategy:
    
    Tier 1: Agent (Deep Research) - Every 30 min
        - Geopolitical risk analysis (AI-powered autonomous search)
        - Central bank policy research
        - Intermarket correlations
    
    Tier 2: News Search - Every 5 min
        - Breaking news
        - Quick sentiment refresh
    
    Tier 3: Extract (Structured Data) - Every 15 min
        - Economic calendar from ForexFactory
        - COT positioning data
        - Rate expectations
    
    Plus existing refresh_all for legacy data.
    """
    global _firecrawl_service
    
    # Counters for tiered refresh timing
    tier1_counter = 0  # Agent refresh (every 6 cycles = 30 min)
    tier3_counter = 0  # Extract refresh (every 3 cycles = 15 min)
    TIER1_INTERVAL = 6  # Every 6 cycles (30 min)
    TIER3_INTERVAL = 3  # Every 3 cycles (15 min)
    CYCLE_DURATION = 300  # 5 minutes per cycle (Tier 2)
    
    # Short initial delay (2 seconds) - we want data ASAP for dashboard
    await asyncio.sleep(2)
    
    # Do an immediate comprehensive refresh on startup
    try:
        if _firecrawl_service and _firecrawl_service.is_available:
            logger.info("🚀 Initial Deep Research intelligence fetch starting...")
            
            # Initial Tier 1: Deep Research (critical for first load)
            logger.info("📊 Tier 1: Starting Agent-based deep research...")
            try:
                await asyncio.gather(
                    _firecrawl_service.research_geopolitical_risk(),
                    _firecrawl_service.research_central_bank_policy(),
                    _firecrawl_service.research_intermarket_correlations(),
                    return_exceptions=True
                )
                logger.info("✅ Tier 1: Deep Research complete")
            except Exception as e:
                logger.warning(f"Tier 1 initial fetch error: {e}")
            
            # Initial Tier 3: Structured extraction
            logger.info("📅 Tier 3: Starting structured data extraction...")
            try:
                await asyncio.gather(
                    _firecrawl_service.extract_economic_calendar(),
                    _firecrawl_service.extract_cot_positioning(),
                    _firecrawl_service.extract_rate_expectations(),
                    return_exceptions=True
                )
                logger.info("✅ Tier 3: Structured extraction complete")
            except Exception as e:
                logger.warning(f"Tier 3 initial fetch error: {e}")
            
            # Initial Tier 2: Quick news refresh
            await _firecrawl_service.refresh_quick(['EURUSD', 'XAUUSD', 'GBPUSD'])
            logger.info("✅ Initial intelligence fetch complete (all tiers)")
    except Exception as e:
        logger.warning(f"Initial intelligence fetch failed: {e}")
    
    while True:
        try:
            if _firecrawl_service and _firecrawl_service.is_available:
                # Get symbols from settings or use defaults
                symbols = getattr(settings.trading, 'symbols', ['EURUSD', 'GBPUSD', 'XAUUSD'])
                symbols = symbols[:30]  # Support up to 30 symbols
                
                # === TIER 2: Quick News Refresh (Every 5 min) ===
                logger.info(f"🔄 Tier 2: Quick news refresh...")
                try:
                    await _firecrawl_service.refresh_quick(symbols[:3])
                    logger.debug("Tier 2 complete")
                except Exception as e:
                    logger.warning(f"Tier 2 refresh error: {e}")
                
                # === TIER 3: Structured Extraction (Every 15 min) ===
                tier3_counter += 1
                if tier3_counter >= TIER3_INTERVAL:
                    tier3_counter = 0
                    logger.info(f"📅 Tier 3: Structured data extraction...")
                    try:
                        await asyncio.gather(
                            _firecrawl_service.extract_economic_calendar(),
                            _firecrawl_service.extract_cot_positioning(),
                            _firecrawl_service.extract_rate_expectations(),
                            return_exceptions=True
                        )
                        logger.info("✅ Tier 3 complete")
                    except Exception as e:
                        logger.warning(f"Tier 3 refresh error: {e}")
                    
                    # Also run legacy refresh_all for compatibility
                    logger.info(f"Refreshing legacy intelligence for {len(symbols)} symbols...")
                    await _firecrawl_service.refresh_all(symbols)
                    logger.debug("Legacy refresh complete")
                
                # === TIER 1: Deep Research Agent (Every 30 min) ===
                tier1_counter += 1
                if tier1_counter >= TIER1_INTERVAL:
                    tier1_counter = 0
                    logger.info(f"🔍 Tier 1: Agent-based deep research starting...")
                    try:
                        # Deep research runs with longer timeout as Agent can take 30-60s
                        await asyncio.wait_for(
                            asyncio.gather(
                                _firecrawl_service.research_geopolitical_risk(),
                                _firecrawl_service.research_central_bank_policy(),
                                _firecrawl_service.research_intermarket_correlations(),
                                return_exceptions=True
                            ),
                            timeout=180  # 3 min timeout for all Agent calls
                        )
                        logger.info("✅ Tier 1: Deep Research complete")
                    except asyncio.TimeoutError:
                        logger.warning("Tier 1 deep research timed out (180s)")
                    except Exception as e:
                        logger.warning(f"Tier 1 refresh error: {e}")
            else:
                logger.debug("Firecrawl service not available, skipping refresh")
        except Exception as e:
            logger.warning(f"Intelligence refresh cycle failed: {e}")
        
        # Wait 5 minutes before next cycle (Tier 2 interval)
        await asyncio.sleep(CYCLE_DURATION)


async def _run_bot_background():
    """
    Run the trading bot as a background task.
    
    This allows the bot to share the same process and bot_state
    as the API, enabling real-time activity updates in the dashboard.
    """
    global _bot_instance
    
    try:
        from ..main import TradingBot
        import sys
        
        print("[BOT] Creating TradingBot instance...", flush=True)
        _bot_instance = TradingBot()
        
        # Pass the already-connected MT5 client to avoid a second connection hang
        if _mt5_client and _mt5_client.is_connected:
            print("[BOT] Reusing existing MT5 client connection", flush=True)
            _bot_instance._shared_mt5 = _mt5_client
        
        print("[BOT] Calling initialize()...", flush=True)
        try:
            init_result = await asyncio.wait_for(_bot_instance.initialize(), timeout=60)
        except asyncio.TimeoutError:
            print("[BOT] ERROR: initialize() timed out after 60s!", flush=True)
            init_result = False
        print(f"[BOT] initialize() returned: {init_result}", flush=True)
        
        if not init_result:
            logger.error("Bot initialization failed")
            print("[BOT] ERROR: Bot initialization failed!", file=sys.stderr, flush=True)
            return
        
        # CRITICAL: Share bot's services with API routes
        _sync_bot_services_to_api(_bot_instance)
        
        # Give Telegram command handler access to the bot instance
        if _command_handler:
            _command_handler.set_bot_instance(_bot_instance)
            print("[BOT] Telegram command handler linked to bot instance", flush=True)
        
        _bot_instance.running = True
        
        # Update bot_state so dashboard shows running immediately
        from .routes.bot_status import bot_state
        bot_state.is_running = True
        bot_state.current_action = "initializing"
        
        logger.info("Trading bot started in background")
        logger.info(f"Trading symbols: {settings.trading.symbols}")
        logger.info(f"Allowed sessions: {settings.trading.allowed_sessions}")
        
        # Launch independent position management loop (runs every 10s, decoupled from analysis)
        _bot_instance._position_mgr_task = asyncio.create_task(
            _bot_instance._position_management_loop()
        )
        logger.info("Independent position management loop launched (10s interval)")
        print("[BOT] Independent position management loop launched (10s interval)", flush=True)
        
        while _bot_instance.running:
            await _bot_instance._trading_cycle()
            await asyncio.sleep(15)  # Check every 15 seconds for faster ICT timing
            
    except asyncio.CancelledError:
        print("[BOT] Task cancelled", flush=True)
        logger.info("Bot background task cancelled")
    except Exception as e:
        print(f"[BOT] EXCEPTION: {e}", flush=True)
        logger.error(f"Error in bot background task: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[BOT] Shutting down...", flush=True)
        if _bot_instance:
            # Cancel the position management loop
            if hasattr(_bot_instance, '_position_mgr_task') and _bot_instance._position_mgr_task:
                _bot_instance._position_mgr_task.cancel()
                try:
                    await _bot_instance._position_mgr_task
                except asyncio.CancelledError:
                    pass
                print("[BOT] Position management loop stopped", flush=True)
            await _bot_instance.shutdown()

# Global MT5 client
_mt5_client = None


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Returns:
        Configured FastAPI app instance
    """
    app = FastAPI(
        title="ICT Trading Bot API",
        description="API for monitoring and controlling the ICT Trading Bot",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json"
    )
    
    # Gap 33: Add rate limiting
    if RATE_LIMITING_AVAILABLE and limiter:
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        logger.info("Rate limiting enabled: 1000 requests/minute")
    else:
        logger.warning("Rate limiting not available - slowapi not installed")
    
    # Gap 34: Configure CORS for frontend access
    cors_env = os.environ.get("CORS_ORIGINS", "")
    
    if cors_env.strip():
        # Use custom origins from environment
        allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    else:
        # Default: allow all common development origins
        allowed_origins = [
            "http://localhost:3000",    # Next.js dev server
            "http://localhost:3001",    # Next.js dev server (fallback port)
            "http://localhost:8000",    # Backend
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "http://127.0.0.1:8000",
            "http://0.0.0.0:3000",
            "http://0.0.0.0:8000",
        ]
    
    logger.info(f"CORS allowed origins: {allowed_origins}")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],  # Allow all methods
        allow_headers=["*"],  # Allow all headers
        expose_headers=["*"],
        max_age=600,  # Cache preflight for 10 minutes
    )
    
    # Register routers
    from .routes import activity, backtest, bot_status, news, silver, goal, crypto, scaling, session, precious_metals, learning, orders, intelligence
    app.include_router(trades.router, prefix="/api/trades", tags=["Trades"])
    app.include_router(backtest.router, prefix="/api/backtest", tags=["Backtesting"])
    app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
    app.include_router(config.router, prefix="/api/config", tags=["Configuration"])
    app.include_router(performance.router, prefix="/api/performance", tags=["Performance"])
    app.include_router(activity.router, prefix="/api", tags=["Activity"])
    app.include_router(bot_status.router, prefix="/api/bot", tags=["Bot Status"])
    app.include_router(news.router, prefix="/api/news", tags=["News & Calendar"])
    app.include_router(silver.router, prefix="/api/silver", tags=["Silver Analysis"])
    app.include_router(crypto.router, prefix="/api/crypto", tags=["Crypto Analysis"])
    app.include_router(goal.router, prefix="/api/goal", tags=["Goal Tracker"])
    app.include_router(scaling.router, prefix="/api/scaling", tags=["Scaling & Position Sizing"])
    app.include_router(session.router, prefix="/api/session", tags=["Session Analytics"])
    app.include_router(precious_metals.router, prefix="/api/precious-metals", tags=["Precious Metals"])
    app.include_router(learning.router, tags=["Learning System"])
    app.include_router(orders.router, prefix="/api", tags=["Pending Orders"])
    app.include_router(intelligence.router, prefix="/api", tags=["Market Intelligence"])
    app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])
    
    # Health check endpoint
    @app.get("/api/health", tags=["Health"])
    async def health_check():
        """Check API server health status."""
        mt5_status = "disconnected"
        mt5_mode = "unknown"
        account_info = None
        
        if _mt5_client:
            if _mt5_client.is_connected:
                mt5_status = "connected"
                mt5_mode = "simulation" if _mt5_client.is_simulation else "live"
                try:
                    acc = await _mt5_client.get_account_info()
                    if acc:
                        account_info = {
                            "login": acc.login,
                            "balance": acc.balance,
                            "currency": acc.currency
                        }
                except:
                    pass
        
        return {
            "status": "healthy",
            "version": "1.0.0",
            "mt5_status": mt5_status,
            "mt5_mode": mt5_mode,
            "account": account_info,
            "bot_connected": _bot_instance is not None
        }
    
    # Debug endpoint for MT5 connection
    @app.get("/api/debug/mt5", tags=["Debug"])
    async def debug_mt5():
        """Debug MT5 connection status."""
        from ..config import settings
        
        result = {
            "config": {
                "login": settings.mt5.login,
                "server": settings.mt5.server,
                "password_set": bool(settings.mt5.password),
            },
            "client_exists": _mt5_client is not None,
            "is_connected": False,
            "is_simulation": True,
            "connection_error": None,
            "account_info": None
        }
        
        if _mt5_client:
            result["is_connected"] = _mt5_client.is_connected
            result["is_simulation"] = _mt5_client.is_simulation
            
            try:
                account = await _mt5_client.get_account_info()
                if account:
                    result["account_info"] = {
                        "login": account.login,
                        "balance": account.balance,
                        "equity": account.equity,
                        "currency": account.currency,
                        "leverage": account.leverage
                    }
            except Exception as e:
                result["connection_error"] = str(e)
        
        return result
    
    @app.get("/api", tags=["Root"])
    async def api_root():
        """API root endpoint with available routes."""
        return {
            "message": "ICT Trading Bot API",
            "docs": "/api/docs",
            "endpoints": {
                "trades": "/api/trades",
                "analysis": "/api/analysis/{symbol}",
                "config": "/api/config",
                "performance": "/api/performance",
                "websocket": "/ws/trades"
            }
        }
    
    return app


def get_trade_journal():
    """Get the global trade journal instance."""
    global _trade_journal
    if _trade_journal is None:
        from ..utils.trade_journal import TradeJournal
        _trade_journal = TradeJournal()
    return _trade_journal


def set_bot_instance(bot):
    """Set the global bot instance for API access."""
    global _bot_instance
    _bot_instance = bot


def get_bot_instance():
    """Get the global bot instance."""
    return _bot_instance


async def start_bot_task():
    """Start the bot as a background task (if not already running)."""
    global _bot_task
    
    # Check if bot instance is already running
    if _bot_instance and _bot_instance.running:
        return True  # Already running is success
    
    # Check if a background task is already in progress (even if not yet "running")
    if _bot_task and not _bot_task.done():
        logger.info("Bot task already in progress, waiting for it...")
        await asyncio.sleep(5)
        return _bot_instance is not None and _bot_instance.running
    
    # Release stale lock before starting fresh
    from ..utils.instance_lock import release_instance_lock
    release_instance_lock()
    
    _bot_task = asyncio.create_task(_run_bot_background())
    
    # Wait for initialization
    await asyncio.sleep(5)
    
    return _bot_instance is not None and _bot_instance.running


def get_mt5_client():
    """Get the global MT5 client instance."""
    global _mt5_client
    return _mt5_client


def _sync_bot_services_to_api(bot):
    """
    Sync the bot's service instances to the API routes.
    
    This ensures API routes use the same instances as the trading bot,
    rather than creating their own.
    """
    try:
        # Sync scaling services
        if bot.position_sizer:
            from .routes.scaling import set_position_sizer
            set_position_sizer(bot.position_sizer)
            logger.info("Synced position_sizer to API")
        
        if bot.scaling_manager:
            from .routes.scaling import set_scaling_manager
            set_scaling_manager(bot.scaling_manager)
            logger.info("Synced scaling_manager to API")
        
        # Sync session analytics
        if bot.session_analytics:
            from .routes.session import set_session_analytics
            set_session_analytics(bot.session_analytics)
            logger.info("Synced session_analytics to API")
        
        # Sync goal tracker
        if bot.goal_tracker:
            from .routes.goal import set_goal_tracker
            set_goal_tracker(bot.goal_tracker)
            logger.info("Synced goal_tracker to API")
        
        # Sync precious metals analyzer
        if bot.precious_metals_analyzer:
            from .routes.precious_metals import set_precious_metals_analyzer
            set_precious_metals_analyzer(bot.precious_metals_analyzer)
            logger.info("Synced precious_metals_analyzer to API")
        
        # Sync learning service
        if bot.learning_service:
            from .routes.learning import set_learning_service
            set_learning_service(bot.learning_service)
            logger.info("Synced learning_service to API")
        
        # Sync pending order manager
        if hasattr(bot, 'pending_order_manager') and bot.pending_order_manager:
            from .routes.orders import set_pending_order_manager, set_order_manager
            set_pending_order_manager(bot.pending_order_manager)
            set_order_manager(bot.order_manager)
            logger.info("Synced pending_order_manager to API")
        
        # Sync firecrawl intelligence service
        if hasattr(bot, 'firecrawl_service') and bot.firecrawl_service:
            from .routes.intelligence import set_firecrawl_service
            set_firecrawl_service(bot.firecrawl_service)
            # Also sync to news routes for geopolitical data
            from .routes.news import set_firecrawl_service as set_news_firecrawl
            set_news_firecrawl(bot.firecrawl_service)
            logger.info("Synced firecrawl_service to API and news routes")
        
        logger.info("Bot services synced to API routes")
        
    except Exception as e:
        logger.error(f"Error syncing bot services to API: {e}")


# Create the app instance
app = create_app()


@app.post("/api/admin/reset-daily-risk")
async def reset_daily_risk():
    """Reset the daily risk counter so new trades can be placed."""
    if not _bot_instance or not hasattr(_bot_instance, 'risk_manager'):
        return JSONResponse(status_code=503, content={"error": "Bot not running"})
    rm = _bot_instance.risk_manager
    old = rm.daily_risk_used
    rm.reset_daily_risk()
    _bot_instance.daily_trades = 0
    return {"status": "ok", "old_daily_risk": f"{old*100:.1f}%", "new_daily_risk": "0.0%"}


if __name__ == "__main__":
    import sys
    import uvicorn
    
    # Only enable reload if explicitly requested via --reload flag
    # Auto-reload kills in-flight Claude analysis calls, breaking live trading
    use_reload = "--reload" in sys.argv
    
    uvicorn.run(
        "trading_bot.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=use_reload,
        reload_excludes=["*.log", "*.tmp", "*.json"] if use_reload else None
    )
