"""
Main entry point for the ICT Trading Bot.

This module orchestrates the trading bot's main loop, coordinating
between market data fetching, analysis, Claude-based decision making,
and trade execution via the MT5 client.
"""

import asyncio
import signal
import sys
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict

from .config import settings
from .utils.logging import setup_logging, get_logger
from .mt5.client import MT5Client
from .mt5.data_fetcher import DataFetcher
from .llm.claude_client import ClaudeClient
from .llm.context_builder import ContextBuilder
from .analysis.market_structure import MarketStructureAnalyzer
from .analysis.fair_value_gap import FVGDetector
from .analysis.order_blocks import OrderBlockDetector
from .analysis.liquidity import LiquidityMapper
from .analysis.kill_zones import KillZoneChecker
from .analysis.silver_bullet import SilverBulletDetector
from .analysis.silver_analysis import SilverAnalyzer
from .analysis.crypto_analysis import CryptoAnalyzer
from .analysis.precious_metals_analysis import PreciousMetalsAnalyzer
from .analysis.amd_cycle import AMDCycleAnalyzer
from .analysis.nwog import NWOGTracker
from .analysis.displacement import DisplacementDetector
from .analysis.volume_analysis import VolumeAnalyzer
from .analysis.ipda import IPDATracker
from .analysis.premium_discount import PremiumDiscountAnalyzer
from .analysis.mtf_analyzer import MTFAnalyzer
from .analysis.fibonacci import FibonacciAnalyzer
from .strategy.ict_strategy import ICTStrategy
from .execution.risk_manager import RiskManager
from .execution.order_manager import OrderManager
from .execution.position_manager import PositionManager, Position
from .execution.scaling_position_sizer import ScalingPositionSizer, SetupGrade
from .services.news_service import NewsService
from .services.correlation_service import CorrelationService
from .services.goal_tracker import GoalTracker
from .services.scaling_manager import ScalingManager, TradingMode
from .services.session_analytics import SessionAnalytics
from .services.trade_learning_service import TradeLearningService
from .services.claude_trade_manager import ClaudeTradeManager
from .services.pending_order_manager import PendingOrderManager
from .services.firecrawl_intelligence import FirecrawlIntelligenceService
from .utils.notifications import notify, NotificationType, get_notifier
from .utils.state_persistence import save_full_state, load_full_state, get_persistence
from .utils.market_hours import is_market_open, should_avoid_new_trades, get_next_market_open
from .utils.instance_lock import ensure_single_instance, release_instance_lock

# Import bot state for activity tracking
try:
    from .api.routes.bot_status import get_bot_state
    bot_state = get_bot_state()
except ImportError:
    bot_state = None

# Import database for trade persistence
try:
    from .api.database import async_session, TradeModel
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

logger = get_logger(__name__)


async def save_trade_to_db(
    ticket: int,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    position_size: float,
    confidence: float,
    reasoning: str = ""
):
    """Save an executed trade to the database."""
    if not DB_AVAILABLE:
        logger.warning("Database not available - trade not persisted")
        return
    
    try:
        async with async_session() as session:
            trade = TradeModel(
                trade_id=str(ticket),
                timestamp=datetime.utcnow(),
                symbol=symbol,
                direction=direction,
                timeframe="M15",
                session="",
                entry_price=entry_price,
                entry_time=datetime.utcnow(),
                entry_reason=reasoning[:500] if reasoning else f"ICT Signal - Confidence: {confidence:.0%}",
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size=position_size,
                risk_amount=0.0,
                claude_confidence=confidence,
                claude_reasoning=reasoning[:1000] if reasoning else ""
            )
            session.add(trade)
            await session.commit()
            logger.info(f"Trade {ticket} saved to database")
    except Exception as e:
        logger.error(f"Failed to save trade to database: {e}")


class TradingBot:
    """
    Main trading bot class that orchestrates all components.
    
    Implements the ICT/Market Maker/FVG trading strategy using:
    - MT5 Client for market data and trade execution
    - Claude Opus 4.5 for intelligent chart analysis
    - Comprehensive strategy documentation for LLM context
    """
    
    def __init__(self):
        """Initialize the trading bot with all components."""
        self.running = False
        self.mt5_client: Optional[MT5Client] = None
        self.data_fetcher: Optional[DataFetcher] = None
        self.claude_client: Optional[ClaudeClient] = None
        self.context_builder: Optional[ContextBuilder] = None
        self.strategy: Optional[ICTStrategy] = None
        self.risk_manager: Optional[RiskManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_manager: Optional[PositionManager] = None
        self.kill_zone_checker: Optional[KillZoneChecker] = None
        
        # NEW: Integrated services
        self.news_service: Optional[NewsService] = None
        self.correlation_service: Optional[CorrelationService] = None
        self.goal_tracker: Optional[GoalTracker] = None
        self.silver_analyzer: Optional[SilverAnalyzer] = None
        self.crypto_analyzer: Optional[CryptoAnalyzer] = None
        self.precious_metals_analyzer: Optional[PreciousMetalsAnalyzer] = None
        self.position_sizer: Optional[ScalingPositionSizer] = None
        self.scaling_manager: Optional[ScalingManager] = None
        self.session_analytics: Optional[SessionAnalytics] = None
        self.learning_service: Optional[TradeLearningService] = None
        
        # NEW: 100-pip expansion analysis components
        self.amd_analyzer: Optional[AMDCycleAnalyzer] = None
        self.nwog_tracker: Optional[NWOGTracker] = None
        self.displacement_detector: Optional[DisplacementDetector] = None
        self.ipda_tracker: Optional[IPDATracker] = None
        self.premium_discount_analyzer: Optional[PremiumDiscountAnalyzer] = None
        self.firecrawl_service: Optional[FirecrawlIntelligenceService] = None
        
        # Additional analysis components
        self.mtf_analyzer: Optional[MTFAnalyzer] = None
        self.fibonacci_analyzer: Optional[FibonacciAnalyzer] = None
        
        # Trading state
        self.win_streak = 0
        self.loss_streak = 0
        
        # Track daily statistics
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()
        
        # Gap 20: Thread-safe trade execution
        self._trade_lock = asyncio.Lock()
        
        # Gap 21: Duplicate signal prevention
        self._recent_signal_hashes: set = set()
        self._signal_hash_expiry: Dict[str, datetime] = {}
        
        # Cycle-to-cycle signal memory (per symbol) for reactive context
        self._last_signal_per_symbol: Dict[str, Dict[str, Any]] = {}
        
        # Direction-flip cooldown tracking
        self._last_signal_direction: Dict[str, tuple] = {}  # symbol -> (direction, datetime)
        
        # SAFE Crypto symbols (24/7 trading) - ONLY USD pairs!
        # WARNING: BTC pairs (ETHBTC, DASHBTC, etc.) are EXCLUDED because 
        # their contract value is in BTC, not USD, causing incorrect position sizing
        self.CRYPTO_SYMBOLS = [
            'BTCUSD', 'ETHUSD', 'XRPUSD', 'ADAUSD', 'LTCUSD', 'DOGEUSD',
            'SOLUSD', 'DOTUSD', 'EOSUSD', 'NEOUSD', 'ETCUSD', 'XMRUSD',
            'ZECUSD', 'DASHUSD', 'IOTAUSD', 'BITUSD', 'USDTUSD'
        ]
        
        # DANGEROUS pairs - DO NOT TRADE (wrong contract value, causes massive losses)
        self.BLOCKED_PAIRS = [
            'ETHBTC', 'XRPBIT', 'LTCBTC', 'XMRBTC', 'ZECBTC', 'DASHBTC', 
            'EOSBIT', 'IOTABIT', 'ETHBIT', 'ADABTC', 'SOLBTC', 'DOTBTC'
        ]
        
        # Priority symbols (Precious metals & crypto opportunity)
        self.PRIORITY_SYMBOLS = ['XAGUSD', 'XAUUSD', 'XRPUSD', 'ADAUSD']
        
        # Precious metals symbols
        self.PRECIOUS_METALS = ['XAUUSD', 'XAGUSD']
        
        # Trade history sync tracking
        self._last_history_sync: Optional[datetime] = None
        self._history_sync_interval = timedelta(minutes=5)
        self._synced_deal_ids: set = set()  # Track already synced deals
    
    async def initialize(self) -> bool:
        """
        Initialize all bot components.
        
        Returns:
            bool: True if initialization successful, False otherwise.
        """
        try:
            import sys as _sys
            print("[INIT] Starting initialization...", flush=True)
            logger.info("Initializing ICT Trading Bot...")
            
            # Ensure single instance
            print("[INIT] Acquiring instance lock...", flush=True)
            if not ensure_single_instance():
                logger.error("❌ Another bot instance is already running!")
                logger.error("Stop the other instance first, or remove data/bot.lock if stale")
                print("[INIT] FAILED: Instance lock not acquired!", flush=True)
                return False
            print("[INIT] Instance lock acquired", flush=True)
            logger.info("✅ Instance lock acquired")
            
            # Initialize MT5 Client — reuse shared connection if available
            if hasattr(self, '_shared_mt5') and self._shared_mt5 and self._shared_mt5.is_connected:
                logger.info("Reusing shared MT5 client connection")
                self.mt5_client = self._shared_mt5
            else:
                logger.info("Connecting to MT5...")
                self.mt5_client = MT5Client()
                if not await self.mt5_client.connect():
                    logger.error("Failed to connect to MT5")
                    return False
            
            # Log connection status
            if self.mt5_client.is_simulation:
                logger.warning("MT5 running in SIMULATION mode - no real trades will execute")
            else:
                account = await self.mt5_client.get_account_info()
                if account:
                    logger.info(f"MT5 connected: Account {account.login}, Balance: {account.balance} {account.currency}")
            
            # =============================================
            # SYNC SYMBOL SPECS FROM MT5 (actual broker values)
            # This MUST happen before any position sizing / P/L calculations
            # =============================================
            print("[INIT] MT5 done, syncing symbol specs from broker...", flush=True)
            from .config import update_symbol_spec_from_mt5
            for _sym in settings.trading.symbols:
                try:
                    _sym_info = await self.mt5_client.get_symbol_info(_sym)
                    if _sym_info:
                        update_symbol_spec_from_mt5(
                            symbol=_sym,
                            trade_contract_size=_sym_info.trade_contract_size,
                            point=_sym_info.point,
                            digits=_sym_info.digits,
                            tick_value=_sym_info.trade_tick_value,
                            volume_min=_sym_info.volume_min,
                            volume_max=_sym_info.volume_max,
                            volume_step=_sym_info.volume_step,
                            swap_long=_sym_info.swap_long,
                            swap_short=_sym_info.swap_short,
                        )
                        print(f"[INIT] {_sym}: contract={_sym_info.trade_contract_size}, tick_val={_sym_info.trade_tick_value}, vol={_sym_info.volume_min}/{_sym_info.volume_max}/{_sym_info.volume_step}, swap={_sym_info.swap_long}/{_sym_info.swap_short}", flush=True)
                    else:
                        print(f"[INIT] {_sym}: Could not get MT5 info, using defaults", flush=True)
                except Exception as e:
                    logger.warning(f"Could not sync spec for {_sym}: {e}")
            
            # Initialize data fetcher with MT5 client
            print("[INIT] Creating DataFetcher...", flush=True)
            self.data_fetcher = DataFetcher(self.mt5_client)
            
            # Initialize Claude client
            print("[INIT] Creating Claude client...", flush=True)
            logger.info("Initializing Claude client...")
            self.claude_client = ClaudeClient()
            if not self.claude_client.api_key:
                logger.warning("Claude API key not configured - AI analysis disabled")
            
            # Initialize context builder with strategy docs
            logger.info("Loading strategy documentation...")
            self.context_builder = ContextBuilder()
            
            # Initialize analyzers
            market_structure = MarketStructureAnalyzer()
            fvg_detector = FVGDetector()
            ob_detector = OrderBlockDetector()
            liquidity_mapper = LiquidityMapper()
            
            # Initialize strategy
            self.strategy = ICTStrategy(
                structure_analyzer=market_structure,
                fvg_detector=fvg_detector,
                ob_detector=ob_detector,
                liquidity_mapper=liquidity_mapper
            )
            
            # Initialize execution components
            self.risk_manager = RiskManager(
                risk_per_trade=settings.trading.risk_per_trade,
                min_risk_reward=settings.trading.min_risk_reward
            )
            self.order_manager = OrderManager(self.mt5_client)
            self.position_manager = PositionManager(order_manager=self.order_manager)
            
            # Set up position close callback for auto-logging
            self.position_manager.set_on_position_close(self._handle_position_close)
            
            # Gap 47: Sync positions from database and MT5 on startup
            print("[INIT] Syncing positions from MT5...", flush=True)
            try:
                await asyncio.wait_for(self._sync_positions_on_startup(), timeout=15)
            except asyncio.TimeoutError:
                print("[INIT] Position sync timed out (15s), continuing...", flush=True)
            except Exception as e:
                print(f"[INIT] Position sync failed: {e}, continuing...", flush=True)
            
            # Initialize kill zone checker with allowed sessions
            self.kill_zone_checker = KillZoneChecker(
                allowed_sessions=settings.trading.allowed_sessions
            )
            
            # =============================================
            # NEW: Initialize integrated services
            # =============================================
            
            # News Service - for blackout period detection
            print("[INIT] News service...", flush=True)
            logger.info("Initializing news service...")
            self.news_service = NewsService(
                blackout_minutes_before=120,
                fomc_blackout_minutes_before=180
            )
            try:
                await asyncio.wait_for(self.news_service.fetch_economic_calendar(), timeout=10)
            except asyncio.TimeoutError:
                print("[INIT] News calendar fetch timed out (10s), continuing...", flush=True)
            except Exception as e:
                print(f"[INIT] News calendar fetch failed: {e}, continuing...", flush=True)
            
            # Correlation Service - prevent correlated losses
            logger.info("Initializing correlation service...")
            self.correlation_service = CorrelationService(
                high_threshold=0.8,
                medium_threshold=0.6
            )
            
            # Goal Tracker - track progress to $100K
            logger.info("Initializing goal tracker...")
            account = await self.mt5_client.get_account_info()
            # Use equity (not balance) so drawdown calculations are consistent
            # Balance excludes unrealized P&L from open positions, causing
            # the scaling_manager to think we've drawn down when we haven't
            starting_equity = account.equity if account else (account.balance if account else 1000.0)
            self.goal_tracker = GoalTracker(
                starting_equity=starting_equity,
                target_equity=100000.0
            )
            
            # Silver Analyzer - special handling for XAGUSD
            logger.info("Initializing silver analyzer...")
            self.silver_analyzer = SilverAnalyzer()
            
            # Silver Bullet Detector - ICT time-based entry strategy
            logger.info("Initializing Silver Bullet detector...")
            self.silver_bullet_detector = SilverBulletDetector()
            
            # Precious Metals Analyzer - combined gold/silver analysis
            logger.info("Initializing precious metals analyzer...")
            self.precious_metals_analyzer = PreciousMetalsAnalyzer()
            
            # Crypto Analyzer - special handling for XRP/ADA
            logger.info("Initializing crypto analyzer...")
            self.crypto_analyzer = CryptoAnalyzer()
            
            # Scaling Position Sizer - dynamic sizing for $1K -> $100K
            logger.info("Initializing scaling position sizer...")
            self.position_sizer = ScalingPositionSizer()
            tier_info = self.position_sizer.get_tier_info(starting_equity)
            logger.info(f"Current scaling tier: {tier_info['current_tier']}")
            logger.info(f"Base lots: {tier_info['base_lots']}, Max lots: {tier_info['max_lots']}")
            
            # Scaling Manager - automated risk adjustment
            logger.info("Initializing scaling manager...")
            self.scaling_manager = ScalingManager(
                starting_equity=starting_equity,
                target_equity=100000.0,
                max_daily_drawdown=settings.trading.max_daily_drawdown,  # 3% from config
                max_weekly_drawdown=settings.trading.max_weekly_drawdown,  # 6% from config
            )
            
            # Session Analytics - track performance by session
            logger.info("Initializing session analytics...")
            self.session_analytics = SessionAnalytics()
            
            # Trade Learning Service - Claude's learning system
            logger.info("Initializing trade learning service...")
            self.learning_service = TradeLearningService()
            
            # Claude Trade Manager - centralized AI trade management with margin validation
            print("[INIT] Claude trade manager...", flush=True)
            logger.info("Initializing Claude trade manager...")
            self.claude_trade_manager = ClaudeTradeManager(
                mt5_client=self.mt5_client,
                risk_manager=self.risk_manager,
                position_manager=self.position_manager,
                claude_client=self.claude_client,
                max_concurrent_positions=8,  # Raised from 5 to allow more crypto positions
                max_exposure_percent=0.40  # 40% of equity as max margin exposure (crypto-friendly)
            )
            
            # Pending Order Manager - track and manage pending orders
            logger.info("Initializing pending order manager...")
            self.pending_order_manager = PendingOrderManager(
                mt5_client=self.mt5_client,
                order_manager=self.order_manager,
                kill_zone_checker=self.kill_zone_checker
            )
            
            # =============================================
            # NEW: 100-PIP EXPANSION ANALYSIS COMPONENTS
            # =============================================
            
            # AMD Cycle Analyzer - Power of Three detection
            logger.info("Initializing AMD cycle analyzer...")
            self.amd_analyzer = AMDCycleAnalyzer()
            
            # NWOG Tracker - Weekend gap targets
            logger.info("Initializing NWOG tracker...")
            self.nwog_tracker = NWOGTracker()
            
            # Displacement Detector - Distribution phase confirmation
            logger.info("Initializing displacement detector...")
            self.displacement_detector = DisplacementDetector()
            
            # IPDA Tracker - PDH/PDL/PWH/PWL targets for 100-pip moves
            logger.info("Initializing IPDA tracker...")
            self.ipda_tracker = IPDATracker()
            
            # Premium/Discount Analyzer - Entry zone validation
            logger.info("Initializing premium/discount analyzer...")
            self.premium_discount_analyzer = PremiumDiscountAnalyzer()
            
            # MTF Analyzer - Higher timeframe bias confirmation
            logger.info("Initializing MTF analyzer...")
            self.mtf_analyzer = MTFAnalyzer(mt5_client=self.mt5_client)
            
            # Fibonacci Analyzer - OTE zone identification
            logger.info("Initializing Fibonacci analyzer...")
            self.fibonacci_analyzer = FibonacciAnalyzer()
            
            # Firecrawl Intelligence Service (optional)
            firecrawl_key = getattr(settings, 'firecrawl_api_key', None) or \
                           getattr(getattr(settings, 'firecrawl', None), 'api_key', None)
            if firecrawl_key:
                logger.info("Initializing Firecrawl intelligence service...")
                self.firecrawl_service = FirecrawlIntelligenceService(
                    api_key=firecrawl_key,
                    refresh_minutes=15,
                    enabled=True
                )
            else:
                self.firecrawl_service = None
                logger.info("Firecrawl service not configured (no API key)")
            
            # Firecrawl Intelligence Service - real-time market data
            print("[INIT] Firecrawl intelligence service...", flush=True)
            if hasattr(settings, 'firecrawl') and getattr(settings.firecrawl, 'api_key', None) and settings.firecrawl.enabled:
                logger.info("Initializing Firecrawl intelligence service...")
                self.firecrawl_service = FirecrawlIntelligenceService(
                    api_key=settings.firecrawl.api_key,
                    refresh_minutes=settings.firecrawl.refresh_minutes,
                    enabled=settings.firecrawl.enabled
                )
                # Initial refresh (non-blocking — will be refreshed in background)
                print("[INIT] Firecrawl initial refresh (skipping — handled by background task)...", flush=True)
            else:
                self.firecrawl_service = None
                logger.info("Firecrawl intelligence disabled (no API key)")
            
            # Initialize Telegram notifier
            print("[INIT] Telegram notifier...", flush=True)
            logger.info("Initializing Telegram notifications...")
            notifier = get_notifier()
            if notifier.enabled:
                logger.info("✅ Telegram notifications enabled")
                await notify(
                    NotificationType.INFO,
                    f"🤖 ICT Trading Bot started!\nEquity: ${starting_equity:,.2f}\nGoal: $100,000"
                )
            else:
                logger.warning("⚠️ Telegram notifications disabled (missing credentials)")
            
            # Load persisted state from previous session
            logger.info("Loading persisted state...")
            if load_full_state(self):
                logger.info(f"✅ Restored state: Win streak={self.win_streak}, Loss streak={self.loss_streak}")
            else:
                logger.info("No previous state to restore")
            
            # Safety: ensure weekly/daily high watermarks aren't stuck above current equity
            # This prevents permanent drawdown kill-switches from stale saved peaks
            if self.scaling_manager and starting_equity > 0:
                if self.scaling_manager.weekly_high_equity > starting_equity * 1.05:
                    logger.warning(
                        f"⚠️ Weekly high equity ({self.scaling_manager.weekly_high_equity:.2f}) "
                        f"is >15% above current equity ({starting_equity:.2f}) - resetting to current"
                    )
                    self.scaling_manager.weekly_high_equity = starting_equity
                if self.scaling_manager.daily_high_equity > starting_equity * 1.05:
                    logger.warning(
                        f"⚠️ Daily high equity ({self.scaling_manager.daily_high_equity:.2f}) "
                        f"is >15% above current equity ({starting_equity:.2f}) - resetting to current"
                    )
                    self.scaling_manager.daily_high_equity = starting_equity
                logger.info(
                    f"Drawdown watermarks: daily_high=${self.scaling_manager.daily_high_equity:.2f}, "
                    f"weekly_high=${self.scaling_manager.weekly_high_equity:.2f}, "
                    f"current_equity=${starting_equity:.2f}"
                )
            
            print("[INIT] ALL DONE - initialization successful!", flush=True)
            logger.info("Trading bot initialized successfully!")
            logger.info(f"Goal: ${starting_equity:.2f} -> $100,000.00")
            return True
            
        except Exception as e:
            print(f"[INIT] EXCEPTION during initialization: {e}", flush=True)
            logger.error(f"Failed to initialize trading bot: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def run(self):
        """Main trading loop."""
        if not await self.initialize():
            logger.error("Bot initialization failed. Exiting.")
            return
        
        self.running = True
        logger.info("Starting main trading loop...")
        logger.info(f"Trading symbols: {settings.trading.symbols}")
        logger.info(f"Allowed sessions: {settings.trading.allowed_sessions}")
        logger.info(f"Max daily trades: {settings.trading.max_daily_trades}")
        
        # Launch independent position management loop
        self._position_mgr_task = asyncio.create_task(self._position_management_loop())
        logger.info("Independent position management loop launched (10s interval)")
        
        try:
            while self.running:
                await self._trading_cycle()
                
                # Wait before next cycle (configurable interval)
                await asyncio.sleep(15)  # Check every 15 seconds for faster ICT timing
                
        except asyncio.CancelledError:
            logger.info("Trading loop cancelled")
        except Exception as e:
            logger.error(f"Error in trading loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Cancel the position management loop
            if hasattr(self, '_position_mgr_task') and self._position_mgr_task:
                self._position_mgr_task.cancel()
                try:
                    await self._position_mgr_task
                except asyncio.CancelledError:
                    pass
                logger.info("Position management loop stopped")
            await self.shutdown()
    
    async def _position_management_loop(self):
        """
        Independent position management loop.
        Runs every 10 seconds regardless of whether the analysis loop is busy.
        
        This ensures open positions are always actively managed:
        - Trailing stops moved
        - Break-even adjustments
        - Claude re-evaluation of open trades
        - Pending order sync with MT5
        
        Even if Claude analysis takes 5-12 minutes for all symbols,
        positions are checked and managed every 10 seconds.
        """
        print("[POS-MGR] Independent position management loop started (10s interval)", flush=True)
        logger.info("Position management loop started (independent, 10s interval)")
        
        # Track cycles for Claude re-evaluation frequency
        pos_mgr_cycle = 0
        
        while self.running:
            try:
                # ALWAYS sync with MT5 to detect new positions (e.g. from filled pending orders)
                # This must run even when position_manager.positions is empty,
                # otherwise positions from filled pending orders are never picked up.
                if self.position_manager and not self.mt5_client.is_simulation:
                    try:
                        sync_result = await self.position_manager.sync_with_mt5(self.mt5_client)
                        if sync_result.get('new_positions'):
                            print(f"[POS-MGR] Detected {len(sync_result['new_positions'])} new position(s) from MT5 (e.g. filled pending orders)", flush=True)
                            logger.info(f"POS-MGR: New positions detected: {sync_result['new_positions']}")
                        if sync_result.get('closed'):
                            logger.info(f"POS-MGR: {len(sync_result['closed'])} position(s) closed externally")
                        logger.debug(f"POS-MGR MT5 sync: {sync_result}")
                    except Exception as e:
                        logger.warning(f"POS-MGR MT5 sync error: {e}")
                
                # Manage open positions (trailing stops, break-even, etc.)
                if self.position_manager and self.position_manager.positions:
                    print(f"[POS-MGR] Managing {len(self.position_manager.positions)} open position(s)...", flush=True)
                    
                    # Get current prices for all symbols with positions
                    price_data = {}
                    for position in self.position_manager.positions.values():
                        try:
                            df = await self.data_fetcher.get_ohlcv(
                                symbol=position.symbol,
                                timeframe="M1",
                                count=1
                            )
                            if df is not None and not df.empty:
                                price_data[position.symbol] = float(df['close'].iloc[-1])
                        except Exception as e:
                            logger.warning(f"POS-MGR: Failed to get price for {position.symbol}: {e}")
                    
                    # Run position management (break-even, trailing stops, partial closes)
                    actions = await self.position_manager.manage_positions(price_data)
                    
                    for action in actions:
                        logger.info(f"POS-MGR action: {action}")
                        from .api.routes.activity import add_activity
                        add_activity(
                            "position_managed",
                            f"{action.get('action', 'update').replace('_', ' ').title()} on position {action.get('ticket')}",
                            None,
                            action
                        )
                    
                    # Check for high volatility
                    try:
                        volatility_alert = await self._check_volatility()
                        if volatility_alert:
                            logger.warning(f"POS-MGR: HIGH VOLATILITY: {volatility_alert}")
                            await self._handle_high_volatility(volatility_alert)
                    except Exception as e:
                        logger.warning(f"POS-MGR volatility check error: {e}")
                    
                    # Claude re-evaluation every 6 cycles (every ~60 seconds)
                    # Fire-and-forget: runs in background so it doesn't block the
                    # 10-second position management cycle (Claude calls can take 30-90s)
                    pos_mgr_cycle += 1
                    if pos_mgr_cycle >= 6:
                        pos_mgr_cycle = 0
                        if not getattr(self, '_claude_reeval_running', False):
                            self._claude_reeval_running = True
                            async def _run_claude_reeval():
                                try:
                                    await self._claude_reevaluate_positions()
                                except Exception as e:
                                    logger.warning(f"POS-MGR Claude re-eval error: {e}")
                                finally:
                                    self._claude_reeval_running = False
                            asyncio.create_task(_run_claude_reeval())
                        else:
                            logger.debug("POS-MGR: Skipping Claude re-eval (previous still running)")
                
                # Sync pending orders with MT5
                if hasattr(self, 'pending_order_manager') and self.pending_order_manager:
                    try:
                        sync_result = await self.pending_order_manager.sync_with_mt5()
                        if sync_result.get('filled', 0) > 0:
                            logger.info(f"POS-MGR: {sync_result['filled']} pending order(s) filled")
                        if sync_result.get('cancelled', 0) > 0:
                            logger.info(f"POS-MGR: {sync_result['cancelled']} pending order(s) cancelled externally")
                        
                        expiration_result = await self.pending_order_manager.cancel_expired_orders()
                        if expiration_result.get('cancelled', 0) > 0:
                            logger.info(f"POS-MGR: {expiration_result['cancelled']} expired order(s) cancelled")
                    except Exception as e:
                        logger.warning(f"POS-MGR pending order sync error: {e}")
                
                # Periodic trade history sync
                try:
                    if self._should_sync_history():
                        await self._sync_trade_history(days_back=1)
                except Exception as e:
                    logger.warning(f"POS-MGR history sync error: {e}")
                
            except asyncio.CancelledError:
                logger.info("Position management loop cancelled")
                break
            except Exception as e:
                print(f"[POS-MGR] Error: {e}", flush=True)
                logger.error(f"Position management loop error: {e}")
                import traceback
                traceback.print_exc()
            
            await asyncio.sleep(10)
    
    async def _trading_cycle(self):
        """
        Execute one trading cycle — focuses on NEW trade analysis only.
        Position management runs independently in _position_management_loop().
        """
        try:
            print(f"[CYCLE] Starting trading cycle...", flush=True)
            # Reset daily counters if new day
            await self._check_daily_reset()
            
            # ============================================
            # STEP 0A: CHECK MT5 CONNECTION
            # ============================================
            if self.mt5_client and not self.mt5_client.is_simulation:
                if not await self.mt5_client.ensure_connected():
                    logger.error("MT5 connection lost and reconnection failed")
                    await self._notify_error("MT5 disconnected", "Reconnection failed")
                    return
            
            # ============================================
            # STEP 0B: CHECK MARKET HOURS (WEEKEND)
            # ============================================
            # Filter symbols based on market hours - crypto is 24/7!
            from .utils.market_hours import get_market_type, MarketType
            
            tradeable_symbols = []
            configured_symbols = settings.trading.symbols
            for symbol in configured_symbols:
                market_type = get_market_type(symbol)
                is_open, reason = is_market_open(symbol)
                if is_open:
                    tradeable_symbols.append(symbol)
                    # Log crypto being added on weekends
                    if market_type == MarketType.CRYPTO:
                        logger.debug(f"✓ {symbol} (crypto) is tradeable: {reason}")
                else:
                    logger.debug(f"✗ {symbol} ({market_type.value}) market closed: {reason}")
            
            print(f"[CYCLE] Tradeable symbols: {tradeable_symbols} (out of {configured_symbols})", flush=True)
            
            if not tradeable_symbols:
                # No symbols available to trade - check why
                # Check if it's just forex closed (weekends) or all markets
                crypto_symbols = [s for s in configured_symbols if get_market_type(s) == MarketType.CRYPTO]
                if crypto_symbols:
                    # We have crypto symbols but they're not in tradeable - this shouldn't happen
                    logger.warning(f"Crypto symbols available but not tradeable: {crypto_symbols}")
                
                is_open, reason = is_market_open("EURUSD")
                next_open = get_next_market_open("EURUSD")
                logger.info(f"Forex/metals markets closed: {reason}. Forex opens: {next_open}")
                
                # Only set error if we truly have no crypto to trade
                if not crypto_symbols:
                    if bot_state:
                        bot_state.error(None, f"Market closed: {reason}")
                else:
                    # Log that we should have crypto
                    logger.info(f"Crypto should be tradeable 24/7, checking {len(crypto_symbols)} crypto symbols...")
                    if bot_state:
                        bot_state.error(None, f"Weekend: Forex closed, checking crypto...")
                
                # Positions already managed in Step 0
                return
            
            # Log what's tradeable
            if len(tradeable_symbols) < len(configured_symbols):
                logger.info(f"Trading {len(tradeable_symbols)}/{len(configured_symbols)} symbols (some markets closed)")
            
            # Use only tradeable symbols for this cycle
            cycle_symbols = tradeable_symbols
            
            # ============================================
            # STEP 0C: MARGIN HEALTH CHECK (P0 CRITICAL)
            # ============================================
            margin_health = await self.claude_trade_manager.monitor_margin_health()
            
            if margin_health["status"] == "emergency":
                print(f"[CYCLE] BLOCKED: EMERGENCY MARGIN - {margin_health['margin_level']:.0f}%", flush=True)
                logger.critical(
                    f"🚨 EMERGENCY MARGIN: {margin_health['margin_level']:.0f}% - "
                    f"Action: {margin_health['action']}"
                )
                # In emergency, close largest losing position
                if margin_health["action"] == "close_largest_loser":
                    await self._close_largest_loser()
                return
            elif margin_health["status"] == "warning":
                logger.warning(
                    f"⚠️ LOW MARGIN: {margin_health['margin_level']:.0f}% - "
                    f"No new trades allowed"
                )
                # Positions already managed in Step 0, skip new trades
                return
            
            # ============================================
            # STEP 0D: PERIODIC FIRECRAWL INTELLIGENCE REFRESH
            # ============================================
            if hasattr(self, 'firecrawl_service') and self.firecrawl_service:
                # Check if it's time to refresh (every 15 minutes)
                if not hasattr(self, '_last_firecrawl_refresh'):
                    self._last_firecrawl_refresh = datetime.min
                
                time_since_refresh = (datetime.now() - self._last_firecrawl_refresh).total_seconds() / 60
                
                if time_since_refresh >= settings.firecrawl.refresh_minutes:
                    logger.info(f"🔄 Refreshing Firecrawl intelligence ({time_since_refresh:.0f}min since last refresh)...")
                    try:
                        await self.firecrawl_service.refresh_all(cycle_symbols)
                        self._last_firecrawl_refresh = datetime.now()
                        logger.info("✅ Firecrawl intelligence refreshed for all symbols")
                    except Exception as e:
                        logger.error(f"Firecrawl refresh error: {e}")
            
            # ============================================
            # STEP 1: TRACK EQUITY FOR GOAL
            # ============================================
            await self._update_goal_tracker()
            
            # ============================================
            # STEP 1: CHECK BLOCKERS (only blocks NEW trades, never position management)
            # Position management, pending order sync, and history sync
            # already ran unconditionally in Step 0 above.
            # Position management (Step 1A above) always runs regardless of these checks.
            # ============================================
            _blocked = False
            _block_reason = None
            
            # Check drawdown circuit breaker
            if await self._check_drawdown_circuit_breaker():
                _blocked = True
                _block_reason = "Drawdown circuit breaker triggered"
                print("[CYCLE] BLOCKED for new trades: Drawdown circuit breaker (positions still being managed)", flush=True)
            
            # Check daily profit target - lock in gains
            if not _blocked and await self._check_daily_profit_target():
                _blocked = True
                _block_reason = "Daily profit target reached"
                print("[CYCLE] BLOCKED for new trades: Daily profit target reached (positions still being managed)", flush=True)
            
            # Check if we've hit daily trade limit
            if not _blocked and self.daily_trades >= settings.trading.max_daily_trades:
                _blocked = True
                _block_reason = f"Daily trade limit reached ({self.daily_trades}/{settings.trading.max_daily_trades})"
                print(f"[CYCLE] BLOCKED for new trades: {_block_reason} (positions still being managed)", flush=True)
            
            if _blocked:
                logger.info(f"New trades blocked: {_block_reason} — but position management continues")
                return
            
            # ============================================
            # DAY-OF-WEEK RISK ADJUSTMENT (T2-3)
            # ============================================
            import pytz as _pytz
            _est = _pytz.timezone('US/Eastern')
            _now_est = datetime.now(_est)
            _weekday = _now_est.weekday()  # 0=Monday, 4=Friday
            
            if _weekday == 0:  # Monday - manipulation day
                if self.scaling_manager:
                    self.scaling_manager.current_mode = TradingMode.CONSERVATIVE
                    logger.info("Monday: CONSERVATIVE mode (manipulation day, A+ setups only)")
            elif _weekday == 4:  # Friday - profit-taking day
                if self.scaling_manager:
                    self.scaling_manager.current_mode = TradingMode.CONSERVATIVE
                    logger.info("Friday: CONSERVATIVE mode (profit-taking day)")
            else:
                # Tuesday-Thursday and weekends (crypto trading) - use normal mode determination
                if self.scaling_manager:
                    try:
                        account = await self.mt5_client.get_account_info()
                        if account:
                            # Use balance (realized P/L) not equity for mode determination
                            mode = self.scaling_manager.determine_mode(account.balance)
                            self.scaling_manager.current_mode = mode
                    except Exception as e:
                        logger.warning(f"Scaling manager mode determination failed: {e}")
            
            # ============================================
            # STEP 2: CHECK NEWS BLACKOUT
            # ============================================
            if self.news_service:
                is_blackout, reason = self.news_service.is_blackout_period()
                if is_blackout:
                    logger.warning(f"📰 NEWS BLACKOUT: {reason} - skipping new forex trades")
                    if bot_state:
                        bot_state.error(None, f"News blackout: {reason}")
                    # During news blackout, only allow crypto (unaffected by forex news)
                    cycle_symbols = [s for s in cycle_symbols if s in self.CRYPTO_SYMBOLS]
                    if not cycle_symbols:
                        print("[CYCLE] BLOCKED: News blackout and no crypto symbols", flush=True)
                        return
            
            # ============================================
            # STEP 2b: FRIDAY PRE-CLOSE (Weekend Gap Protection)
            # ============================================
            import pytz
            from datetime import datetime as dt_module
            est_tz = pytz.timezone('US/Eastern')
            now_est = dt_module.now(est_tz)
            if now_est.weekday() == 4:  # Friday
                if now_est.hour >= 16 and now_est.minute >= 30:
                    logger.warning("FRIDAY PRE-CLOSE: Closing forex positions before weekend")
                    # Close all non-crypto positions
                    for ticket, pos in list(self.position_manager.positions.items()):
                        if pos.symbol not in self.CRYPTO_SYMBOLS:
                            try:
                                result = await self.order_manager.close_position(ticket=ticket)
                                if result.success:
                                    logger.info(f"  Closed {pos.symbol} position {ticket} for weekend protection")
                                    if self.on_position_close:
                                        await self.on_position_close(pos)
                            except Exception as e:
                                logger.error(f"  Failed to close {pos.symbol} position {ticket}: {e}")
                    
                    # Also block new entries on Fridays after 12 PM EST
                    if now_est.hour >= 12:
                        logger.info("Friday afternoon - no new forex entries")
                        # Still allow crypto
                        cycle_symbols = [s for s in cycle_symbols if s in self.CRYPTO_SYMBOLS]
                        if not cycle_symbols:
                            return
            
            print(f"[CYCLE] Passed drawdown/profit/limit checks, checking kill zone...", flush=True)
            
            # Check if we're in a valid kill zone
            session = self.kill_zone_checker.get_current_session()
            print(f"[CYCLE] Session: {session.session_name}, is_tradeable={session.is_tradeable}, is_kill_zone={session.is_kill_zone}", flush=True)
            if not session.is_tradeable:
                # Outside kill zone — but crypto trades 24/7, so only filter out forex
                crypto_in_cycle = [s for s in cycle_symbols if s in self.CRYPTO_SYMBOLS]
                if crypto_in_cycle:
                    logger.info(
                        f"Outside kill zone ({session.session_name}) - "
                        f"forex blocked, {len(crypto_in_cycle)} crypto symbols still active"
                    )
                    cycle_symbols = crypto_in_cycle
                else:
                    print(f"[CYCLE] BLOCKED: Outside kill zone ({session.session_name}), no crypto to trade", flush=True)
                    logger.debug(f"Outside valid trading session ({session.session_name}), skipping cycle")
                    return
            
            # Check for Silver Bullet window
            silver_bullet_status = self.silver_bullet_detector.is_in_silver_bullet_window()
            if silver_bullet_status.get('active'):
                logger.info(
                    f"🔫 SILVER BULLET WINDOW ACTIVE: {silver_bullet_status['window']} "
                    f"({silver_bullet_status['time_remaining_minutes']:.0f}min remaining)"
                )
            
            logger.info(
                f"Trading cycle - Session: {session.session_name}, "
                f"Kill Zone: {session.is_kill_zone}, "
                f"Silver Bullet: {silver_bullet_status.get('active', False)}"
            )
            
            # Update bot state
            if bot_state:
                bot_state.start_cycle(session.session_name, session.is_tradeable)
            
            # Process each symbol - prioritize silver and crypto
            # Use cycle_symbols (filtered by market hours) instead of all symbols
            symbols = cycle_symbols.copy()
            
            # Sort symbols to prioritize XAGUSD, XRPUSD, ADAUSD
            priority_order = []
            regular_order = []
            for sym in symbols:
                if sym in self.PRIORITY_SYMBOLS:
                    priority_order.append(sym)
                else:
                    regular_order.append(sym)
            
            ordered_symbols = priority_order + regular_order
            logger.info(f"Processing {len(ordered_symbols)} tradeable symbols (priority first): {', '.join(ordered_symbols)}")
            
            # Process symbols in parallel batches for faster execution
            batch_size = 5
            for i in range(0, len(ordered_symbols), batch_size):
                batch = ordered_symbols[i:i + batch_size]
                
                async def _process_symbol(sym):
                    try:
                        is_crypto = sym in self.CRYPTO_SYMBOLS
                        if is_crypto:
                            logger.info(f"🪙 {sym} is crypto - 24/7 trading enabled")
                        print(f"[CYCLE] Analyzing {sym} (crypto={is_crypto})...", flush=True)
                        # Per-symbol timeout: 120s max per analysis to prevent one slow symbol
                        # from blocking the entire batch
                        await asyncio.wait_for(
                            self._analyze_and_trade(sym, is_crypto=is_crypto),
                            timeout=120.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"⏰ Analysis of {sym} TIMED OUT after 120s — skipping")
                    except Exception as e:
                        logger.error(f"Error analyzing {sym}: {e}")
                
                await asyncio.gather(*[_process_symbol(sym) for sym in batch])
            
            # Mark cycle complete
            if bot_state:
                bot_state.cycle_complete()
                
        except Exception as e:
            print(f"[ERROR] Trading cycle CRASHED: {e}", flush=True)
            logger.error(f"Error in trading cycle: {e}")
            import traceback
            traceback.print_exc()
            if bot_state:
                bot_state.error(None, str(e))
            import traceback
            traceback.print_exc()
    
    async def _analyze_and_trade(self, symbol: str, is_crypto: bool = False):
        """
        Analyze a symbol and execute trade if valid setup found.
        
        Args:
            symbol: Trading symbol to analyze
            is_crypto: Whether this is a crypto symbol (24/7 trading)
        """
        try:
            logger.info(f"Analyzing {symbol}...")
            
            # Update bot state
            if bot_state:
                bot_state.analyzing_symbol(symbol)
            
            # CRITICAL: Block dangerous pairs (BTC-quoted pairs have wrong contract values)
            if symbol.upper() in self.BLOCKED_PAIRS or symbol.upper().endswith('BTC') or symbol.upper().endswith('BIT'):
                logger.error(f"🚫 BLOCKED: {symbol} is a BTC/BIT pair - contract value issues cause massive losses!")
                if bot_state:
                    bot_state.error(symbol, f"BLOCKED: {symbol} is a dangerous BTC pair")
                    bot_state.symbol_complete(symbol, "blocked_btc_pair")
                return
            
            # Gap 55: Block trading in simulation mode unless explicitly allowed
            if self.mt5_client.is_simulation:
                if not settings.trading.allow_simulation_trades:
                    logger.warning(
                        f"Skipping trade execution for {symbol} - MT5 in simulation mode. "
                        f"Set TRADING_ALLOW_SIMULATION_TRADES=true to enable simulation trading."
                    )
                    # Still run analysis for dashboard display, but don't execute trades
                    await self._run_analysis_only(symbol)
                    return
                else:
                    logger.warning(f"SIMULATION MODE: Trade for {symbol} will be simulated (not real)")
            
            # Fetch OHLCV data (using config candle counts)
            if bot_state:
                bot_state.fetching_data(symbol)
            
            df = await self.data_fetcher.get_ohlcv(
                symbol=symbol,
                timeframe=settings.timeframes.execution_tf,
                count=settings.timeframes.execution_tf_candles
            )
            
            # Gap 56: Verify data is real before trading
            if df is None or df.empty:
                logger.error(f"No real market data for {symbol} - cannot trade without data")
                if bot_state:
                    bot_state.symbol_complete(symbol, "no_data")
                return
            
            # Run technical analysis
            if bot_state:
                bot_state.running_technical_analysis(symbol)
            
            # Core ICT analysis (use symbol-specific pip_value)
            from .config import get_symbol_spec as _get_spec
            _core_pip = _get_spec(symbol).pip_size
            market_structure_analyzer = MarketStructureAnalyzer()
            fvg_detector = FVGDetector(pip_value=_core_pip)
            ob_detector = OrderBlockDetector()
            liquidity_mapper = LiquidityMapper(pip_value=_core_pip)
            volume_analyzer = VolumeAnalyzer()
            
            analysis_results = {
                "market_structure": market_structure_analyzer.analyze(df),
                "fvg": fvg_detector.detect(df),
                "order_blocks": ob_detector.detect(df),
                "liquidity": liquidity_mapper.analyze(df)
            }
            
            # Volume analysis
            try:
                volume_analysis = volume_analyzer.analyze(df)
                analysis_results["volume"] = volume_analysis.to_dict()
                logger.info(
                    f"Volume: {volume_analysis.relative_volume:.1f}x avg, "
                    f"Trend: {volume_analysis.volume_trend}, "
                    f"Spikes: {len(volume_analysis.spike_bars)}, "
                    f"Low: {'YES' if volume_analysis.relative_volume < 0.5 else 'NO'}"
                )
                if bot_state:
                    bot_state.volume_analysis_complete(
                        symbol,
                        volume_analysis.relative_volume,
                        volume_analysis.volume_trend,
                        len(volume_analysis.spike_bars),
                        volume_analysis.relative_volume < 0.5
                    )
            except Exception as e:
                logger.warning(f"Volume analysis error: {e}")
                volume_analysis = None
                analysis_results["volume"] = {}
            
            # =============================================
            # NEW: 100-PIP EXPANSION ANALYSIS
            # =============================================
            
            print(f"[ANALYSIS] Core ICT done for {symbol}. Running expanded analysis...", flush=True)
            
            # Update pip_value on all analyzers for this symbol
            from .config import get_symbol_spec
            _sym_spec = get_symbol_spec(symbol)
            _sym_pip = _sym_spec.pip_size
            if self.amd_analyzer:
                self.amd_analyzer.pip_value = _sym_pip
            if self.displacement_detector:
                self.displacement_detector.pip_value = _sym_pip
            if self.ipda_tracker:
                self.ipda_tracker.pip_value = _sym_pip
            if self.nwog_tracker:
                self.nwog_tracker.pip_value = _sym_pip
            
            # 1. AMD Cycle Analysis - Power of Three
            amd_state = None
            if self.amd_analyzer:
                try:
                    amd_state = self.amd_analyzer.analyze(df)
                    logger.info(
                        f"AMD Phase: {amd_state.phase.value}, "
                        f"Expected Direction: {amd_state.expected_direction or 'Unknown'}, "
                        f"Confidence: {amd_state.confidence:.0%}"
                    )
                    analysis_results["amd_cycle"] = {
                        "phase": amd_state.phase.value,
                        "expected_direction": amd_state.expected_direction,
                        "manipulation_extreme": amd_state.manipulation_extreme,
                        "confidence": amd_state.confidence
                    }
                except Exception as e:
                    logger.warning(f"AMD analysis error: {e}")
            
            # 2. Displacement Detection - Distribution confirmation
            displacement_analysis = None
            if self.displacement_detector:
                try:
                    expected_dir = amd_state.expected_direction if amd_state else None
                    displacement_analysis = self.displacement_detector.detect(df, expected_dir)
                    
                    if displacement_analysis.distribution_confirmed:
                        logger.info(
                            f"🚀 DISPLACEMENT CONFIRMED: {displacement_analysis.distribution_direction} "
                            f"(Strong candle + FVG)"
                        )
                    
                    analysis_results["displacement"] = displacement_analysis.to_dict()
                except Exception as e:
                    logger.warning(f"Displacement analysis error: {e}")
            
            # 3. Breaker Block Prioritization - Check for A+ setups
            breaker_blocks = analysis_results["order_blocks"].breaker_blocks if analysis_results["order_blocks"].breaker_blocks else []
            if breaker_blocks:
                logger.info(f"🔄 Found {len(breaker_blocks)} Breaker Blocks - HIGH PRIORITY ENTRY ZONES")
                analysis_results["breaker_blocks"] = {
                    "count": len(breaker_blocks),
                    "bullish": [bb.to_dict() for bb in breaker_blocks if bb.type.value == "breaker_bullish"],
                    "bearish": [bb.to_dict() for bb in breaker_blocks if bb.type.value == "breaker_bearish"]
                }
            
            # 4. Premium/Discount Zone Analysis
            pd_analysis = None
            if self.premium_discount_analyzer:
                try:
                    current_price_pd = float(df['close'].iloc[-1])
                    pd_analysis = self.premium_discount_analyzer.analyze(df, current_price_pd)
                    
                    logger.info(
                        f"📊 Price Zone: {pd_analysis.current_zone.value} "
                        f"({pd_analysis.retracement_percent:.0%}), "
                        f"OTE: {'YES' if pd_analysis.in_ote else 'NO'}"
                    )
                    
                    analysis_results["premium_discount"] = pd_analysis.to_dict()
                except Exception as e:
                    logger.warning(f"Premium/Discount analysis error: {e}")
            
            # 5. IPDA Levels - Draw on Liquidity targets for 100-pip moves
            ipda_analysis = None
            if self.ipda_tracker:
                try:
                    ipda_analysis = self.ipda_tracker.update(df)
                    if ipda_analysis.pdh or ipda_analysis.pdl:
                        pdh_str = f"{ipda_analysis.pdh.price:.5f}" if ipda_analysis.pdh else "N/A"
                        pdl_str = f"{ipda_analysis.pdl.price:.5f}" if ipda_analysis.pdl else "N/A"
                        logger.info(f"📍 IPDA Levels: PDH={pdh_str}, PDL={pdl_str}")
                    analysis_results["ipda_levels"] = ipda_analysis.to_dict()
                except Exception as e:
                    logger.warning(f"IPDA analysis error: {e}")
            
            # 6. NWOG Check - Weekend gaps as targets
            nwog_target = None
            if self.nwog_tracker and hasattr(self.nwog_tracker, 'gaps') and self.nwog_tracker.gaps:
                try:
                    nearest_nwog = self.nwog_tracker.get_nearest_nwog(float(df['close'].iloc[-1]))
                    if nearest_nwog:
                        nwog_target = nearest_nwog.ce_level
                        logger.info(f"🎯 NWOG Target: {nwog_target:.5f} ({nearest_nwog.gap_size_pips:.0f} pip gap)")
                        analysis_results["nwog_target"] = {
                            "ce_level": nwog_target,
                            "gap_size_pips": nearest_nwog.gap_size_pips,
                            "filled": nearest_nwog.filled
                        }
                except Exception as e:
                    logger.warning(f"NWOG analysis error: {e}")
            
            # 7. Silver Bullet + Displacement Check
            silver_bullet_ready = False
            if hasattr(self, 'silver_bullet_detector') and self.silver_bullet_detector:
                sb_status = self.silver_bullet_detector.is_in_silver_bullet_window()
                if sb_status.get('active', False):
                    # Silver Bullet requires displacement in the window
                    if displacement_analysis and displacement_analysis.distribution_confirmed:
                        silver_bullet_ready = True
                        logger.info(f"🔫⚡ SILVER BULLET READY: Displacement confirmed in {sb_status['window']} window!")
                    else:
                        logger.info(f"🔫 Silver Bullet window active but waiting for displacement...")
                    analysis_results["silver_bullet_status"] = {
                        "window_active": True,
                        "window": sb_status.get('window'),
                        "displacement_confirmed": silver_bullet_ready,
                        "time_remaining": sb_status.get('time_remaining_minutes', 0)
                    }
            
            print(f"[ANALYSIS] Running MTF analysis for {symbol} (D1->H4->H1->M15->M5->M1)...", flush=True)
            # 8. MULTI-TIMEFRAME ANALYSIS - HTF bias confirmation
            mtf_result = None
            if self.mtf_analyzer:
                try:
                    mtf_result = await self.mtf_analyzer.analyze(symbol)
                    if mtf_result:
                        logger.info(
                            f"📊 MTF Bias: {mtf_result.overall_bias.value}, "
                            f"Alignment: {mtf_result.alignment}, "
                            f"Can Long: {mtf_result.can_trade_long}, "
                            f"Can Short: {mtf_result.can_trade_short}"
                        )
                        analysis_results["mtf_analysis"] = mtf_result.to_dict()
                        
                        # Log MTF results to bot activity dashboard
                        if bot_state:
                            mtf_details = {
                                "d1_bias": mtf_result.daily_analysis.bias.value if mtf_result.daily_analysis else "N/A",
                                "h4_bias": mtf_result.h4_analysis.bias.value if mtf_result.h4_analysis else "N/A",
                                "h4_structure": mtf_result.h4_analysis.structure if mtf_result.h4_analysis else "N/A",
                                "h1_bias": mtf_result.h1_analysis.bias.value if mtf_result.h1_analysis else "N/A",
                                "h1_structure": mtf_result.h1_analysis.structure if mtf_result.h1_analysis else "N/A",
                                "m15_bias": mtf_result.m15_analysis.bias.value if mtf_result.m15_analysis else "N/A",
                                "m5_bias": mtf_result.m5_analysis.bias.value if mtf_result.m5_analysis else "N/A",
                                "m5_structure": mtf_result.m5_analysis.structure if mtf_result.m5_analysis else "N/A",
                                "m1_bias": mtf_result.m1_analysis.bias.value if mtf_result.m1_analysis else "N/A",
                                "m1_structure": mtf_result.m1_analysis.structure if mtf_result.m1_analysis else "N/A",
                                "alignment": mtf_result.alignment,
                                "key_levels": mtf_result.htf_key_levels[:5] if mtf_result.htf_key_levels else [],
                            }
                            bot_state.mtf_analysis_complete(
                                symbol,
                                bias=mtf_result.overall_bias.value,
                                alignment=mtf_result.alignment,
                                can_long=mtf_result.can_trade_long,
                                can_short=mtf_result.can_trade_short,
                                details=mtf_details
                            )
                    else:
                        logger.warning(f"MTF analysis returned no result for {symbol}")
                        if bot_state:
                            bot_state.error(symbol, f"MTF analysis: no D1/H4/H1 data available")
                except Exception as e:
                    logger.warning(f"MTF analysis error: {e}")
                    if bot_state:
                        bot_state.error(symbol, f"MTF analysis error: {e}")
            
            print(f"[ANALYSIS] Running Fibonacci/OTE analysis for {symbol}...", flush=True)
            # 9. FIBONACCI / OTE ANALYSIS
            fib_analysis = None
            if self.fibonacci_analyzer:
                try:
                    # Determine direction from market structure
                    ms_trend = analysis_results["market_structure"].trend.value if analysis_results.get("market_structure") else 'bullish'
                    fib_direction = 'bullish' if ms_trend == 'bullish' else 'bearish'
                    fib_analysis = self.fibonacci_analyzer.analyze_ote(df, fib_direction, lookback=50)
                    if fib_analysis:
                        logger.info(
                            f"📐 Fibonacci: Zone={fib_analysis.price_zone.value}, "
                            f"In OTE: {fib_analysis.in_ote}, "
                            f"Optimal Entry: {fib_analysis.optimal_entry}"
                        )
                        analysis_results["fibonacci"] = fib_analysis.to_dict()
                        
                        # Log Fibonacci results to bot activity dashboard
                        if bot_state:
                            fib_details = {
                                "zone": fib_analysis.price_zone.value,
                                "in_ote": fib_analysis.in_ote,
                                "optimal_entry": fib_analysis.optimal_entry,
                                "direction": fib_direction,
                                "levels": fib_analysis.to_dict().get('levels', {}) if hasattr(fib_analysis, 'to_dict') else {},
                            }
                            bot_state.fibonacci_analysis_complete(
                                symbol,
                                zone=fib_analysis.price_zone.value,
                                in_ote=fib_analysis.in_ote,
                                optimal_entry=fib_analysis.optimal_entry,
                                details=fib_details
                            )
                except Exception as e:
                    logger.warning(f"Fibonacci analysis error: {e}")
            
            # 10. ENHANCED FIRECRAWL INTELLIGENCE
            dxy_confirmation = None
            retail_contrarian = None
            vix_risk_mode = None
            currency_strength_recommendation = None
            
            if self.firecrawl_service:
                try:
                    # 8a. DXY Correlation for FX pairs
                    if symbol in ['EURUSD', 'GBPUSD', 'USDCHF', 'USDJPY', 'AUDUSD', 'NZDUSD']:
                        dxy_data = await self.firecrawl_service.get_dxy_analysis()
                        dxy_trend = dxy_data.get('trend', 'unknown')
                        
                        if symbol in ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD']:
                            if dxy_trend == 'bullish':
                                dxy_confirmation = 'short'
                                logger.info(f"💵 DXY BULLISH: Confirms SHORT bias for {symbol}")
                            elif dxy_trend == 'bearish':
                                dxy_confirmation = 'long'
                                logger.info(f"💵 DXY BEARISH: Confirms LONG bias for {symbol}")
                        elif symbol in ['USDCHF', 'USDJPY']:
                            if dxy_trend == 'bullish':
                                dxy_confirmation = 'long'
                                logger.info(f"💵 DXY BULLISH: Confirms LONG bias for {symbol}")
                            elif dxy_trend == 'bearish':
                                dxy_confirmation = 'short'
                                logger.info(f"💵 DXY BEARISH: Confirms SHORT bias for {symbol}")
                        
                        analysis_results["dxy_correlation"] = {
                            "dxy_trend": dxy_trend,
                            "confirmed_direction": dxy_confirmation
                        }
                    
                    # 8b. RETAIL SENTIMENT (Contrarian Indicator)
                    retail_data = await self.firecrawl_service.get_retail_sentiment(symbol)
                    if retail_data.get('contrarian_signal') != 'unknown':
                        retail_contrarian = retail_data.get('contrarian_signal')
                        logger.info(
                            f"🔄 RETAIL CONTRARIAN: {retail_data.get('bias', 'N/A')} bias, "
                            f"Signal: {retail_contrarian.upper()}"
                        )
                        if retail_data.get('note'):
                            logger.info(f"   {retail_data.get('note')}")
                        analysis_results["retail_sentiment"] = retail_data
                    
                    # 8c. VIX Risk Sentiment
                    vix_data = await self.firecrawl_service.get_vix_sentiment()
                    if vix_data.get('risk_mode'):
                        vix_risk_mode = vix_data.get('risk_mode')
                        logger.info(
                            f"📊 VIX RISK MODE: {vix_risk_mode.upper()} "
                            f"(Level: {vix_data.get('level', 'N/A')})"
                        )
                        analysis_results["vix_sentiment"] = vix_data
                        
                        # Adjust for risk-off (favor JPY, CHF, Gold)
                        if vix_risk_mode == 'risk_off':
                            if symbol in ['USDJPY', 'USDCHF']:
                                logger.info(f"⚠️ RISK-OFF: Consider SHORT {symbol} (safe-haven flows)")
                            elif symbol == 'XAUUSD':
                                logger.info(f"✅ RISK-OFF: Supports LONG GOLD")
                    
                    # 8d. Currency Strength Meter
                    strength_data = await self.firecrawl_service.get_currency_strength()
                    if strength_data.get('strongest') or strength_data.get('weakest'):
                        logger.info(
                            f"💪 CURRENCY STRENGTH: Strongest={strength_data.get('strongest')}, "
                            f"Weakest={strength_data.get('weakest')}"
                        )
                        if strength_data.get('recommendation'):
                            currency_strength_recommendation = strength_data.get('recommendation')
                            logger.info(f"   💡 {currency_strength_recommendation}")
                        analysis_results["currency_strength"] = strength_data
                    
                    # 8e. TradingView Technical Consensus
                    tv_tech = await self.firecrawl_service.get_tradingview_technical(symbol)
                    if tv_tech.get('signal') != 'neutral':
                        logger.info(
                            f"📈 TV TECHNICAL: {tv_tech.get('consensus', 'N/A').upper()} "
                            f"({tv_tech.get('signal', 'neutral').upper()})"
                        )
                        analysis_results["tv_technical"] = tv_tech
                    
                    # 8f. Commodity Correlations (for CAD and AUD)
                    if symbol in ['USDCAD', 'CADJPY']:
                        oil_data = await self.firecrawl_service.get_commodity_correlation("oil")
                        if oil_data.get('trend') != 'unknown':
                            logger.info(f"🛢️ OIL: {oil_data.get('trend').upper()} - {oil_data.get('currency_implication', {}).get('pair_recommendation', '')}")
                            analysis_results["oil_correlation"] = oil_data
                    
                    if symbol in ['AUDUSD', 'AUDJPY', 'XAUUSD']:
                        gold_data = await self.firecrawl_service.get_commodity_correlation("gold")
                        if gold_data.get('trend') != 'unknown':
                            logger.info(f"🥇 GOLD: {gold_data.get('trend').upper()} - {gold_data.get('currency_implication', {}).get('pair_recommendation', '')}")
                            analysis_results["gold_correlation"] = gold_data
                    
                    # 8g. SOCIAL SENTIMENT (Twitter/X - Contrarian)
                    social_data = await self.firecrawl_service.get_twitter_forex_sentiment(symbol)
                    if social_data.get('sentiment') != 'unknown':
                        logger.info(
                            f"🐦 SOCIAL: {social_data.get('sentiment', 'N/A').upper()} "
                            f"(Volume: {social_data.get('volume', 'N/A')})"
                        )
                        analysis_results["social_sentiment"] = social_data
                    
                    # 8h. OPTIONS FLOW (Magnet Levels)
                    options_data = await self.firecrawl_service.get_options_flow(symbol)
                    if options_data.get('flow') != 'neutral':
                        logger.info(
                            f"📊 OPTIONS FLOW: {options_data.get('flow', 'N/A').upper()}"
                        )
                        if options_data.get('magnet_levels'):
                            logger.info(f"   Magnet Levels: {options_data.get('magnet_levels')}")
                        analysis_results["options_flow"] = options_data
                    
                    # 8i. BOND YIELD SPREAD (EUR/USD bias)
                    if symbol in ['EURUSD', 'EURGBP', 'EURJPY']:
                        yield_data = await self.firecrawl_service.get_bond_yield_spread()
                        if yield_data.get('spread') is not None:
                            logger.info(
                                f"📈 YIELD SPREAD: US-DE = {yield_data.get('spread', 0):.2f}% "
                                f"-> EUR/USD bias: {yield_data.get('eurusd_bias', 'neutral').upper()}"
                            )
                            analysis_results["bond_yields"] = yield_data
                    
                    # 8j. INTERMARKET RISK ENVIRONMENT
                    intermarket_data = await self.firecrawl_service.get_intermarket_analysis()
                    if intermarket_data.get('risk_environment') != 'unknown':
                        risk_env = intermarket_data.get('risk_environment', 'unknown')
                        logger.info(
                            f"🌐 INTERMARKET: {risk_env.upper().replace('_', ' ')} "
                            f"(SPX: {intermarket_data.get('spx_trend', 'N/A').upper()})"
                        )
                        analysis_results["intermarket"] = intermarket_data
                    
                    # 8k. SEASONAL PATTERN
                    seasonal_data = await self.firecrawl_service.get_seasonal_pattern(symbol)
                    if seasonal_data.get('current_month_bias') != 'unknown':
                        logger.info(
                            f"📅 SEASONAL: {seasonal_data.get('current_month', 'N/A')} "
                            f"bias = {seasonal_data.get('current_month_bias', 'N/A').upper()} "
                            f"({seasonal_data.get('historical_accuracy', 0)}% accuracy)"
                        )
                        analysis_results["seasonal_pattern"] = seasonal_data
                    
                    # 8l. ECONOMIC SURPRISE INDEX
                    surprise_data = await self.firecrawl_service.get_economic_surprise_index()
                    if surprise_data.get('us') != 'unknown' or surprise_data.get('eu') != 'unknown':
                        logger.info(
                            f"📰 ECONOMIC SURPRISE: US={surprise_data.get('us', 'N/A').upper()}, "
                            f"EU={surprise_data.get('eu', 'N/A').upper()}"
                        )
                        analysis_results["economic_surprise"] = surprise_data
                    
                    # 8l2. RATE EXPECTATIONS (Critical for currency bias)
                    rate_data = await self.firecrawl_service.get_rate_expectations()
                    if rate_data.get('fed', {}).get('next_move') not in ['unknown', None]:
                        logger.info(
                            f"💰 FED RATE: Expected {rate_data['fed'].get('next_move', 'N/A').upper()} "
                            f"-> USD {rate_data['fed'].get('usd_impact', 'N/A').upper()}"
                        )
                        analysis_results["rate_expectations"] = rate_data
                    
                    # 8l3. ECONOMIC CALENDAR TODAY
                    calendar_events = await self.firecrawl_service.get_economic_calendar_today()
                    if calendar_events:
                        high_impact = [e for e in calendar_events if e.get('impact') == 'high']
                        if high_impact:
                            logger.info(f"📅 {len(high_impact)} HIGH IMPACT events today")
                        analysis_results["economic_calendar"] = calendar_events
                    
                    # 8m. BTC DOMINANCE (for crypto pairs)
                    if symbol in ['BTCUSD', 'ETHUSD', 'XRPUSD', 'SOLUSD', 'ADAUSD']:
                        btc_dom = await self.firecrawl_service.get_btc_dominance()
                        if btc_dom.get('dominance') is not None:
                            logger.info(
                                f"₿ BTC DOMINANCE: {btc_dom.get('dominance', 'N/A')}% "
                                f"({btc_dom.get('trend', 'N/A')}) "
                                f"-> Altcoins: {btc_dom.get('altcoin_sentiment', 'N/A').upper()}"
                            )
                            analysis_results["btc_dominance"] = btc_dom
                    
                except Exception as e:
                    logger.debug(f"Firecrawl intelligence error: {e}")
            
            # Get current price
            current_price = float(df['close'].iloc[-1])
            
            # Skip Claude analysis if not configured
            if not self.claude_client or not self.claude_client.api_key:
                logger.debug(f"Claude not configured, using technical analysis only for {symbol}")
                return
            
            # Generate chart image for Claude (primary M15 chart)
            chart_base64 = await self._generate_chart_image(df, symbol)
            if not chart_base64:
                logger.warning(f"Failed to generate chart for {symbol}")
                return
            
            # Generate M5 and M1 charts for swing counting and precision entries
            additional_charts = []
            try:
                for ltf, ltf_candles in [('M5', 100), ('M1', 100)]:
                    ltf_df = await self.data_fetcher.get_ohlcv(
                        symbol=symbol,
                        timeframe=ltf,
                        count=ltf_candles
                    )
                    if ltf_df is not None and not ltf_df.empty:
                        ltf_chart = await self._generate_chart_image(ltf_df, symbol, timeframe=ltf)
                        if ltf_chart:
                            additional_charts.append({
                                'base64': ltf_chart,
                                'timeframe': ltf
                            })
                            logger.debug(f"Generated {ltf} chart for {symbol}")
                if additional_charts:
                    logger.info(f"Sending {len(additional_charts)} additional LTF charts for {symbol} (M5/M1)")
            except Exception as e:
                logger.warning(f"Failed to generate LTF charts for {symbol}: {e}")
                # Continue without LTF charts -- M15 is still available
            
            # Build strategy context
            strategy_context = self.context_builder.get_quick_reference()
            
            # Get account info for enhanced context
            account_info = await self.mt5_client.get_account_info()
            current_equity = account_info.equity if account_info else 1000.0
            
            # Prepare market data for Claude (ENHANCED with all integrated services)
            from .config import get_symbol_spec as _gss
            _sym_spec = _gss(symbol)
            market_data = {
                "current_price": current_price,
                "bid": current_price - 0.00005,
                "ask": current_price + 0.00005,
                "spread": 1.0,
                # Account & Goal Context
                "account_equity": current_equity,
                "scaling_tier": self.position_sizer.get_tier_name(current_equity) if self.position_sizer else "Unknown",
                "trading_mode": self.scaling_manager.current_mode.value if self.scaling_manager else "normal",
                "goal_progress": self.scaling_manager.calculate_goal_progress(current_equity) if self.scaling_manager else 0,
                # Swap/overnight cost awareness
                "swap_long": _sym_spec.swap_long,
                "swap_short": _sym_spec.swap_short,
                "swap_info": (
                    f"Overnight swap costs per lot: Long={_sym_spec.swap_long}, Short={_sym_spec.swap_short}. "
                    f"{'Positive swap favors longs.' if _sym_spec.swap_long > 0 and _sym_spec.swap_short < 0 else ''}"
                    f"{'Positive swap favors shorts.' if _sym_spec.swap_short > 0 and _sym_spec.swap_long < 0 else ''}"
                    f"{'Both directions have negative swap (costly to hold overnight).' if _sym_spec.swap_long < 0 and _sym_spec.swap_short < 0 else ''}"
                ) if (_sym_spec.swap_long != 0 or _sym_spec.swap_short != 0) else "Swap data not available.",
                # Volume constraints for position sizing
                "volume_min": _sym_spec.volume_min,
                "volume_max": _sym_spec.volume_max,
                "volume_step": _sym_spec.volume_step,
            }
            
            # Add recent performance context
            if self.scaling_manager:
                market_data["recent_performance"] = self.scaling_manager.get_recent_performance()
            
            # Add session performance context
            if self.session_analytics:
                current_session = self.session_analytics.get_current_session()
                session_stats = self.session_analytics.get_session_stats(current_session)
                market_data["session"] = current_session.value
                market_data["session_performance"] = {
                    "win_rate": session_stats.win_rate,
                    "avg_r": session_stats.avg_r,
                    "total_trades": session_stats.total_trades
                }
            
            # Add news/blackout context
            if self.news_service:
                is_blackout, blackout_reason = self.news_service.is_blackout_period()
                market_data["news_status"] = {
                    "is_blackout": is_blackout,
                    "reason": blackout_reason
                }
                if not is_blackout:
                    countdown = self.news_service.get_countdown_to_next_event()
                    if countdown and countdown.get('minutes_until', 999) < 60:
                        market_data["news_status"]["next_event"] = {
                            "title": countdown.get('event', {}).get('title', 'Unknown'),
                            "minutes_until": countdown.get('minutes_until', 999),
                            "impact": countdown.get('event', {}).get('impact', 'medium')
                        }
            
            # Add correlation context
            if self.correlation_service:
                should_block, reason = self.correlation_service.should_block_trade(symbol)
                if should_block or self.correlation_service.get_position_size_multiplier(symbol) < 1.0:
                    market_data["correlation_exposure"] = {
                        "warning": reason if should_block else f"Reduced size recommended for {symbol}",
                        "blocked": should_block,
                        "multiplier": self.correlation_service.get_position_size_multiplier(symbol)
                    }
            
            # Add Silver Bullet window context
            if hasattr(self, 'silver_bullet_detector') and self.silver_bullet_detector:
                sb_status = self.silver_bullet_detector.is_in_silver_bullet_window()
                market_data["silver_bullet"] = {
                    "active": sb_status.get('active', False),
                    "window": sb_status.get('window'),
                    "time_remaining_minutes": sb_status.get('time_remaining_minutes', 0),
                    "displacement_confirmed": silver_bullet_ready if 'silver_bullet_ready' in dir() else False
                }
                if sb_status.get('active'):
                    logger.info(f"🔫 Silver Bullet window active for {symbol}: {sb_status['window']}")
            
            # =============================================
            # NEW: ADD 100-PIP EXPANSION CONTEXT FOR CLAUDE
            # =============================================
            
            # AMD Cycle context
            if 'amd_cycle' in analysis_results:
                market_data["amd_cycle"] = analysis_results["amd_cycle"]
            
            # Displacement context
            if 'displacement' in analysis_results:
                market_data["displacement"] = analysis_results["displacement"]
            
            # Breaker blocks for A+ entry prioritization
            if 'breaker_blocks' in analysis_results:
                market_data["breaker_blocks"] = analysis_results["breaker_blocks"]
            
            # Premium/Discount zone
            if 'premium_discount' in analysis_results:
                market_data["premium_discount"] = analysis_results["premium_discount"]
            
            # IPDA levels for 100-pip targets
            if 'ipda_levels' in analysis_results:
                market_data["ipda_levels"] = analysis_results["ipda_levels"]
            
            # NWOG targets
            if 'nwog_target' in analysis_results:
                market_data["nwog_target"] = analysis_results["nwog_target"]
            
            # Volume analysis context
            if 'volume' in analysis_results:
                market_data["volume_profile"] = analysis_results["volume"]
            
            # DXY correlation for FX pairs
            if 'dxy_correlation' in analysis_results:
                market_data["dxy_correlation"] = analysis_results["dxy_correlation"]
            
            # Silver Bullet + displacement status
            if 'silver_bullet_status' in analysis_results:
                market_data["silver_bullet_setup"] = analysis_results["silver_bullet_status"]
            
            # =============================================
            # ADDITIONAL FIRECRAWL INTELLIGENCE FOR CLAUDE
            # =============================================
            
            # Retail sentiment (contrarian indicator)
            if 'retail_sentiment' in analysis_results:
                market_data["retail_sentiment"] = analysis_results["retail_sentiment"]
            
            # VIX risk mode
            if 'vix_sentiment' in analysis_results:
                market_data["vix_sentiment"] = analysis_results["vix_sentiment"]
            
            # Currency strength
            if 'currency_strength' in analysis_results:
                market_data["currency_strength"] = analysis_results["currency_strength"]
            
            # TradingView technical
            if 'tradingview_technical' in analysis_results:
                market_data["tradingview_technical"] = analysis_results["tradingview_technical"]
            
            # Rate expectations
            if 'rate_expectations' in analysis_results:
                market_data["rate_expectations"] = analysis_results["rate_expectations"]
            
            # Economic calendar
            if 'economic_calendar' in analysis_results:
                market_data["economic_calendar"] = analysis_results["economic_calendar"]
            
            # Social sentiment
            if 'social_sentiment' in analysis_results:
                market_data["social_sentiment"] = analysis_results["social_sentiment"]
            
            # Options flow
            if 'options_flow' in analysis_results:
                market_data["options_flow"] = analysis_results["options_flow"]
            
            # Bond yields
            if 'bond_yields' in analysis_results:
                market_data["bond_yields"] = analysis_results["bond_yields"]
            
            # Intermarket analysis
            if 'intermarket' in analysis_results:
                market_data["intermarket"] = analysis_results["intermarket"]
            
            # Seasonal pattern
            if 'seasonal_pattern' in analysis_results:
                market_data["seasonal_pattern"] = analysis_results["seasonal_pattern"]
            
            # Economic surprise
            if 'economic_surprise' in analysis_results:
                market_data["economic_surprise"] = analysis_results["economic_surprise"]
            
            # BTC dominance (crypto)
            if 'btc_dominance' in analysis_results:
                market_data["btc_dominance"] = analysis_results["btc_dominance"]
            
            # Commodity correlations
            if 'oil_correlation' in analysis_results:
                market_data["oil_correlation"] = analysis_results["oil_correlation"]
            if 'gold_correlation' in analysis_results:
                market_data["gold_correlation"] = analysis_results["gold_correlation"]
            
            # Add Firecrawl real-time intelligence context (ENHANCED with Deep Research)
            if hasattr(self, 'firecrawl_service') and self.firecrawl_service:
                # Get comprehensive context including retail sentiment, VIX, etc.
                firecrawl_context = self.firecrawl_service.get_market_context_for_claude(symbol)
                if firecrawl_context:
                    market_data["firecrawl_intelligence"] = firecrawl_context
                
                # === DEEP RESEARCH INTELLIGENCE (AI-POWERED) ===
                # Get structured deep research data from Agent and Extract methods
                comprehensive_intel = self.firecrawl_service.get_comprehensive_intelligence(symbol)
                
                # Add deep research context for Claude
                if comprehensive_intel:
                    # Convert to formatted string for Claude's prompt
                    deep_research_context = comprehensive_intel.to_claude_context()
                    if deep_research_context:
                        market_data["deep_research_intelligence"] = deep_research_context
                    
                    # Add risk warnings and adjustments
                    if comprehensive_intel.warnings:
                        market_data["intelligence_warnings"] = comprehensive_intel.warnings
                    
                    # Specific risk adjustments based on geopolitical analysis
                    if comprehensive_intel.geopolitical:
                        geo = comprehensive_intel.geopolitical
                        market_data["geopolitical_risk_level"] = geo.risk_level
                        if geo.risk_level in ["high", "extreme"]:
                            market_data["risk_warning"] = f"⚠️ HIGH GEOPOLITICAL RISK ({geo.risk_level.upper()}) - REDUCE POSITION SIZES BY 25-50%"
                            market_data["confidence_adjustment"] = -25 if geo.risk_level == "high" else -40
                    
                    # Central bank policy context
                    if comprehensive_intel.central_banks:
                        cb = comprehensive_intel.central_banks
                        if cb.divergence_plays:
                            market_data["cb_divergence_plays"] = cb.divergence_plays
                        market_data["cb_overall_bias"] = cb.overall_bias
                    
                    # Intermarket risk environment
                    if comprehensive_intel.intermarket:
                        im = comprehensive_intel.intermarket
                        market_data["risk_environment"] = im.risk_environment
                        if im.trading_implications:
                            market_data["intermarket_implications"] = im.trading_implications
                        
                        # Risk-off environment adjustments
                        if im.risk_environment in ["strong_risk_off", "risk_off"]:
                            # Favor safe havens
                            if symbol in ["USDJPY", "EURJPY", "GBPJPY"]:
                                market_data["risk_adjustment_note"] = "Risk-off favors JPY strength (bearish bias)"
                            elif symbol in ["USDCHF", "EURCHF"]:
                                market_data["risk_adjustment_note"] = "Risk-off favors CHF strength (bearish bias)"
                            elif symbol == "XAUUSD":
                                market_data["risk_adjustment_note"] = "Risk-off favors Gold (bullish bias)"
                
                # Add symbol-specific fundamentals if available
                symbol_fundamentals = self.firecrawl_service.get_cached_symbol_fundamentals(symbol)
                if symbol_fundamentals:
                    market_data["symbol_fundamentals"] = {
                        "bias": symbol_fundamentals.fundamental_bias,
                        "key_drivers": symbol_fundamentals.key_drivers[:3],
                        "confidence": symbol_fundamentals.confidence,
                        "trade_recommendation": symbol_fundamentals.trade_recommendation
                    }
                
                # Add specific intelligence for trade validation
                if retail_contrarian:
                    market_data["retail_contrarian_signal"] = retail_contrarian
                if vix_risk_mode:
                    market_data["vix_risk_mode"] = vix_risk_mode
                if currency_strength_recommendation:
                    market_data["currency_strength_tip"] = currency_strength_recommendation
            
            # Add learning context from Claude's trade reviews
            if self.learning_service:
                try:
                    session_name = market_data.get("session", "")
                    learning_context = await self.learning_service.build_context_for_claude(
                        symbol=symbol,
                        session=session_name
                    )
                    if learning_context:
                        market_data["learning_context"] = learning_context
                        logger.debug(f"Added learning context for {symbol}")
                except Exception as e:
                    logger.warning(f"Could not add learning context: {e}")
            
            # Add precious metals context for gold/silver
            if symbol in self.PRECIOUS_METALS and self.precious_metals_analyzer:
                try:
                    # Get prices for both metals
                    gold_price = market_data.get('current_price', 0) if symbol == 'XAUUSD' else 0
                    silver_price = market_data.get('current_price', 0) if symbol == 'XAGUSD' else 0
                    
                    # Try to get the other metal's price
                    other_symbol = 'XAGUSD' if symbol == 'XAUUSD' else 'XAUUSD'
                    other_data = await self.data_fetcher.get_market_data(other_symbol, settings.timeframes.execution_tf)
                    if other_data and 'close' in other_data and len(other_data['close']) > 0:
                        if symbol == 'XAUUSD':
                            silver_price = float(other_data['close'].iloc[-1])
                        else:
                            gold_price = float(other_data['close'].iloc[-1])
                    
                    # Generate precious metals context
                    if gold_price > 0 and silver_price > 0:
                        geopolitical = 'normal'
                        if self.news_service:
                            geo_level = self.news_service.geopolitical_risk_level([])
                            geopolitical = geo_level if geo_level else 'normal'
                        
                        market_data["precious_metals_context"] = self.precious_metals_analyzer.get_context_for_claude(
                            gold_price=gold_price,
                            silver_price=silver_price,
                            geopolitical_risk=geopolitical
                        )
                        logger.debug(f"Added precious metals context for {symbol}")
                except Exception as e:
                    logger.warning(f"Could not add precious metals context: {e}")
            
            # Prepare ENRICHED analysis data for Claude (full price levels, not just counts)
            # Use .get() to avoid KeyError if any analyzer failed
            ms_obj = analysis_results.get("market_structure")
            fvg_obj = analysis_results.get("fvg")
            ob_obj = analysis_results.get("order_blocks")
            liq_obj = analysis_results.get("liquidity")
            
            analysis_data = {
                "market_structure": {
                    "trend": ms_obj.trend.value if ms_obj and hasattr(ms_obj, 'trend') else "unknown",
                    "structure_breaks": len(ms_obj.structure_breaks) if ms_obj and hasattr(ms_obj, 'structure_breaks') else 0,
                    "break_details": [
                        {"type": sb.type if hasattr(sb, 'type') else str(sb), 
                         "price": sb.price if hasattr(sb, 'price') else 0}
                        for sb in ms_obj.structure_breaks[-5:]
                    ] if ms_obj and hasattr(ms_obj, 'structure_breaks') and ms_obj.structure_breaks else [],
                    "swing_highs": [float(sh.price) if hasattr(sh, 'price') else float(sh) 
                                    for sh in (ms_obj.swing_highs[-5:] if hasattr(ms_obj, 'swing_highs') and ms_obj.swing_highs else [])] if ms_obj else [],
                    "swing_lows": [float(sl.price) if hasattr(sl, 'price') else float(sl) 
                                   for sl in (ms_obj.swing_lows[-5:] if hasattr(ms_obj, 'swing_lows') and ms_obj.swing_lows else [])] if ms_obj else [],
                },
                "fvg": {
                    "bullish": len(fvg_obj.bullish_fvgs) if fvg_obj and hasattr(fvg_obj, 'bullish_fvgs') else 0,
                    "bearish": len(fvg_obj.bearish_fvgs) if fvg_obj and hasattr(fvg_obj, 'bearish_fvgs') else 0,
                    "active": len(fvg_obj.active_fvgs) if fvg_obj and hasattr(fvg_obj, 'active_fvgs') else 0,
                    "bullish_zones": [
                        {"high": float(f.top), "low": float(f.bottom)} 
                        for f in fvg_obj.bullish_fvgs[-3:]
                    ] if fvg_obj and hasattr(fvg_obj, 'bullish_fvgs') and fvg_obj.bullish_fvgs else [],
                    "bearish_zones": [
                        {"high": float(f.top), "low": float(f.bottom)} 
                        for f in fvg_obj.bearish_fvgs[-3:]
                    ] if fvg_obj and hasattr(fvg_obj, 'bearish_fvgs') and fvg_obj.bearish_fvgs else [],
                },
                "order_blocks": {
                    "bullish": len(ob_obj.bullish_obs) if ob_obj and hasattr(ob_obj, 'bullish_obs') else 0,
                    "bearish": len(ob_obj.bearish_obs) if ob_obj and hasattr(ob_obj, 'bearish_obs') else 0,
                    "bullish_zones": [
                        {"high": float(ob.high), "low": float(ob.low)} 
                        for ob in ob_obj.bullish_obs[-3:]
                    ] if ob_obj and hasattr(ob_obj, 'bullish_obs') and ob_obj.bullish_obs else [],
                    "bearish_zones": [
                        {"high": float(ob.high), "low": float(ob.low)} 
                        for ob in ob_obj.bearish_obs[-3:]
                    ] if ob_obj and hasattr(ob_obj, 'bearish_obs') and ob_obj.bearish_obs else [],
                },
                "liquidity": {
                    "nearest_bsl": float(liq_obj.nearest_bsl) if liq_obj and liq_obj.nearest_bsl else None,
                    "nearest_ssl": float(liq_obj.nearest_ssl) if liq_obj and liq_obj.nearest_ssl else None,
                    "all_bsl": [float(p.price) if hasattr(p, 'price') else float(p) 
                                for p in (liq_obj.bsl_pools[-5:] if hasattr(liq_obj, 'bsl_pools') and liq_obj.bsl_pools else [])] if liq_obj else [],
                    "all_ssl": [float(p.price) if hasattr(p, 'price') else float(p) 
                                for p in (liq_obj.ssl_pools[-5:] if hasattr(liq_obj, 'ssl_pools') and liq_obj.ssl_pools else [])] if liq_obj else [],
                    "equal_highs": [float(eh.price) if hasattr(eh, 'price') else float(eh) 
                                    for eh in (liq_obj.equal_highs[-3:] if hasattr(liq_obj, 'equal_highs') and liq_obj.equal_highs else [])] if liq_obj else [],
                    "equal_lows": [float(el.price) if hasattr(el, 'price') else float(el) 
                                   for el in (liq_obj.equal_lows[-3:] if hasattr(liq_obj, 'equal_lows') and liq_obj.equal_lows else [])] if liq_obj else [],
                },
                "volume": analysis_results.get("volume", {})
            }
            
            # Add MTF context to market_data for Claude
            if mtf_result:
                market_data["htf_bias"] = mtf_result.overall_bias.value
                market_data["htf_alignment"] = mtf_result.alignment
                market_data["htf_can_trade_long"] = mtf_result.can_trade_long
                market_data["htf_can_trade_short"] = mtf_result.can_trade_short
                # D1 context (top-down starting point)
                market_data["d1_bias"] = mtf_result.daily_analysis.bias.value if mtf_result.daily_analysis else None
                market_data["d1_structure"] = mtf_result.daily_analysis.structure if mtf_result.daily_analysis else None
                market_data["d1_trend"] = mtf_result.daily_analysis.trend if mtf_result.daily_analysis else None
                # H4 context
                market_data["h4_bias"] = mtf_result.h4_analysis.bias.value if mtf_result.h4_analysis else None
                market_data["h4_structure"] = mtf_result.h4_analysis.structure if mtf_result.h4_analysis else None
                market_data["h4_trend"] = mtf_result.h4_analysis.trend if mtf_result.h4_analysis else None
                # H1 context
                market_data["h1_bias"] = mtf_result.h1_analysis.bias.value if mtf_result.h1_analysis else None
                market_data["h1_structure"] = mtf_result.h1_analysis.structure if mtf_result.h1_analysis else None
                market_data["h1_trend"] = mtf_result.h1_analysis.trend if mtf_result.h1_analysis else None
                market_data["htf_key_levels"] = mtf_result.htf_key_levels
                # M15 context (execution timeframe)
                market_data["m15_bias"] = mtf_result.m15_analysis.bias.value if mtf_result.m15_analysis else None
                market_data["m15_structure"] = mtf_result.m15_analysis.structure if mtf_result.m15_analysis else None
                market_data["m15_trend"] = mtf_result.m15_analysis.trend if mtf_result.m15_analysis else None
                # M5/M1 context (precision entry)
                market_data["m5_bias"] = mtf_result.m5_analysis.bias.value if mtf_result.m5_analysis else None
                market_data["m5_structure"] = mtf_result.m5_analysis.structure if mtf_result.m5_analysis else None
                market_data["m5_trend"] = mtf_result.m5_analysis.trend if mtf_result.m5_analysis else None
                market_data["m1_bias"] = mtf_result.m1_analysis.bias.value if mtf_result.m1_analysis else None
                market_data["m1_structure"] = mtf_result.m1_analysis.structure if mtf_result.m1_analysis else None
                market_data["m1_trend"] = mtf_result.m1_analysis.trend if mtf_result.m1_analysis else None
            
            # Add Fibonacci/OTE context to market_data for Claude
            if fib_analysis:
                market_data["fibonacci_zone"] = fib_analysis.price_zone.value
                market_data["in_ote"] = fib_analysis.in_ote
                market_data["optimal_entry"] = fib_analysis.optimal_entry
                market_data["fib_levels"] = fib_analysis.fib_levels.to_dict() if fib_analysis.fib_levels else None
            
            # Inject last signal memory so Claude knows what it said last cycle
            if symbol in self._last_signal_per_symbol:
                market_data["last_signal"] = self._last_signal_per_symbol[symbol]
            
            # Get Claude's analysis
            logger.info(f"Requesting Claude analysis for {symbol}...")
            if bot_state:
                bot_state.calling_claude(symbol)
            
            claude_result = await self.claude_client.analyze_chart_async(
                chart_image_base64=chart_base64,
                symbol=symbol,
                timeframe=settings.timeframes.execution_tf,
                strategy_context=strategy_context,
                market_data=market_data,
                analysis_data=analysis_data,
                additional_charts=additional_charts if additional_charts else None
            )
            
            # Extract trade signal from result
            trade_signal = claude_result.signal
            
            # Print detailed analysis block to terminal
            self._print_analysis_summary(symbol, trade_signal, claude_result, market_data)
            
            # Update cycle-to-cycle signal memory
            self._last_signal_per_symbol[symbol] = {
                "direction": trade_signal.direction,
                "confidence": trade_signal.confidence,
                "trade_type": getattr(trade_signal, 'trade_type', 'intraday'),
                "timestamp": datetime.now().isoformat(),
                "reasoning": (trade_signal.reasoning or "")[:200],
            }
            
            # Log Claude's response
            if bot_state:
                bot_state.claude_response(
                    symbol, 
                    trade_signal.direction, 
                    trade_signal.confidence,
                    trade_signal.reasoning or ""
                )
            
            # Save signal to the signals store (for dashboard display)
            self._save_signal(symbol, trade_signal, analysis_results)
            
            # Add signal to activity feed
            from .api.routes.activity import add_activity
            add_activity(
                "signal_generated",
                f"Signal: {trade_signal.direction.upper()} {symbol} ({trade_signal.confidence:.0%} confidence)",
                symbol,
                {
                    "direction": trade_signal.direction,
                    "confidence": trade_signal.confidence,
                    "entry_price": trade_signal.entry_price,
                    "market_structure": trade_signal.market_structure
                }
            )
            
            # Telegram notifications for signals disabled — only notify on executed trades, TP/SL hits
            
            # Check if we have a valid trade signal
            if trade_signal.direction == "no_trade":
                logger.info(f"No trade signal for {symbol}: {trade_signal.reasoning[:100] if trade_signal.reasoning else 'No reason given'}")
                if bot_state:
                    bot_state.trade_decision(symbol, "no_trade", trade_signal.reasoning[:100] if trade_signal.reasoning else "No setup")
                    bot_state.symbol_complete(symbol, "no_trade")
                return
            
            # ============================================
            # SIGNAL PRICE SANITY CHECKS (A5)
            # Validate Claude's prices before any execution
            # ============================================
            _entry = trade_signal.entry_price or current_price
            _sl = trade_signal.stop_loss
            _tp = trade_signal.take_profit
            _dir = trade_signal.direction
            
            # Check 1 & 2: SL/TP must be on the correct side of entry
            # Instead of immediately rejecting, try to auto-correct swapped SL/TP first
            sl_wrong = False
            tp_wrong = False
            
            if _dir == 'long' and _sl and _sl >= _entry:
                sl_wrong = True
            if _dir == 'short' and _sl and _sl <= _entry:
                sl_wrong = True
            if _dir == 'long' and _tp and _tp <= _entry:
                tp_wrong = True
            if _dir == 'short' and _tp and _tp >= _entry:
                tp_wrong = True
            
            # If both are on the wrong side, swap them
            if sl_wrong and tp_wrong and _sl and _tp:
                logger.warning(
                    f"SL/TP SWAP AUTO-FIX for {symbol} ({_dir}): "
                    f"SL={_sl} and TP={_tp} both on wrong side of entry={_entry}. Swapping."
                )
                _sl, _tp = _tp, _sl
                trade_signal.stop_loss = _sl
                trade_signal.take_profit = _tp
                sl_wrong = False
                tp_wrong = False
            elif (sl_wrong or tp_wrong) and _sl and _tp:
                # One is wrong — try swapping
                logger.warning(
                    f"SL/TP SWAP AUTO-FIX for {symbol} ({_dir}): "
                    f"SL={_sl} or TP={_tp} on wrong side of entry={_entry}. Swapping."
                )
                _sl, _tp = _tp, _sl
                trade_signal.stop_loss = _sl
                trade_signal.take_profit = _tp
                # Re-check after swap
                sl_wrong = False
                tp_wrong = False
                if _dir == 'long' and _sl >= _entry:
                    sl_wrong = True
                if _dir == 'short' and _sl <= _entry:
                    sl_wrong = True
                if _dir == 'long' and _tp <= _entry:
                    tp_wrong = True
                if _dir == 'short' and _tp >= _entry:
                    tp_wrong = True
            
            # After auto-fix attempt, reject if still wrong
            if sl_wrong:
                logger.warning(f"SIGNAL REJECTED for {symbol}: SL ({_sl}) on wrong side of entry ({_entry}) for {_dir}")
                if bot_state:
                    bot_state.trade_decision(symbol, "rejected", f"Invalid SL placement for {_dir}")
                    bot_state.symbol_complete(symbol, "invalid_signal")
                return
            if tp_wrong:
                logger.warning(f"SIGNAL REJECTED for {symbol}: TP ({_tp}) on wrong side of entry ({_entry}) for {_dir}")
                if bot_state:
                    bot_state.trade_decision(symbol, "rejected", f"Invalid TP placement for {_dir}")
                    bot_state.symbol_complete(symbol, "invalid_signal")
                return
            
            # Check 3: Entry price must be within 2% of current market price
            if _entry and current_price > 0:
                deviation = abs(_entry - current_price) / current_price
                if deviation > 0.02:
                    logger.warning(
                        f"SIGNAL REJECTED for {symbol}: Entry ({_entry}) deviates {deviation:.1%} "
                        f"from current price ({current_price}) - max 2% allowed"
                    )
                    if bot_state:
                        bot_state.trade_decision(symbol, "rejected", f"Entry price too far from market ({deviation:.1%})")
                        bot_state.symbol_complete(symbol, "invalid_signal")
                    return
            
            # Check 4: SL and TP must exist
            if not _sl or not _tp:
                logger.warning(f"SIGNAL REJECTED for {symbol}: Missing SL ({_sl}) or TP ({_tp})")
                if bot_state:
                    bot_state.trade_decision(symbol, "rejected", "Missing SL or TP")
                    bot_state.symbol_complete(symbol, "invalid_signal")
                return
            
            logger.info(f"Signal price checks passed for {symbol}: Entry={_entry}, SL={_sl}, TP={_tp}")
            
            # ============================================
            # R:R ENFORCEMENT (A6)
            # Ensure TP distance >= min_rr * SL distance
            # If Claude gives bad R:R, auto-correct the TP
            # ============================================
            # Adjust min R:R based on trade type
            _trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
            _rr_by_type = {'scalp': 1.5, 'intraday': 2.0, 'swing': 3.0}
            min_rr = _rr_by_type.get(_trade_type, settings.trading.min_risk_reward)
            logger.info(f"R:R threshold for {symbol} ({_trade_type}): {min_rr:.1f}:1")
            sl_distance = abs(_entry - _sl)
            tp_distance = abs(_tp - _entry)
            
            if sl_distance > 0:
                actual_rr = tp_distance / sl_distance
            else:
                actual_rr = 0.0
            
            if actual_rr < min_rr and sl_distance > 0:
                # TP is too close relative to SL — extend TP to meet minimum R:R
                required_tp_distance = sl_distance * min_rr
                
                if _dir == 'long':
                    new_tp = _entry + required_tp_distance
                else:
                    new_tp = _entry - required_tp_distance
                
                logger.warning(
                    f"R:R FIX for {symbol}: Original R:R was {actual_rr:.2f} (SL dist: {sl_distance:.5f}, "
                    f"TP dist: {tp_distance:.5f}). Extending TP from {_tp:.5f} to {new_tp:.5f} "
                    f"for {min_rr:.1f}:1 R:R"
                )
                trade_signal.take_profit = new_tp
                _tp = new_tp
            elif actual_rr >= min_rr:
                logger.info(f"R:R OK for {symbol}: {actual_rr:.2f} (min {min_rr:.1f})")
            
            # ============================================
            # TRADE QUALITY FILTER (E3)
            # Count ICT confluence factors for quality grading
            # ============================================
            confluence_count = 0
            confluence_factors = []
            
            # Check FVG confluence
            if analysis_results.get("fvg"):
                fvg_data = analysis_results["fvg"]
                if _dir == 'long' and hasattr(fvg_data, 'bullish_fvgs') and fvg_data.bullish_fvgs:
                    confluence_count += 1
                    confluence_factors.append("Bullish FVG")
                elif _dir == 'short' and hasattr(fvg_data, 'bearish_fvgs') and fvg_data.bearish_fvgs:
                    confluence_count += 1
                    confluence_factors.append("Bearish FVG")
            
            # Check Order Block confluence
            if analysis_results.get("order_blocks"):
                ob_data = analysis_results["order_blocks"]
                if _dir == 'long' and hasattr(ob_data, 'bullish_obs') and ob_data.bullish_obs:
                    confluence_count += 1
                    confluence_factors.append("Bullish OB")
                elif _dir == 'short' and hasattr(ob_data, 'bearish_obs') and ob_data.bearish_obs:
                    confluence_count += 1
                    confluence_factors.append("Bearish OB")
            
            # Check liquidity sweep confluence
            if analysis_results.get("liquidity"):
                liq_data = analysis_results["liquidity"]
                if _dir == 'long' and hasattr(liq_data, 'nearest_ssl') and liq_data.nearest_ssl:
                    confluence_count += 1
                    confluence_factors.append("SSL Liquidity")
                elif _dir == 'short' and hasattr(liq_data, 'nearest_bsl') and liq_data.nearest_bsl:
                    confluence_count += 1
                    confluence_factors.append("BSL Liquidity")
            
            # Check AMD cycle confluence
            if analysis_results.get("amd_cycle"):
                amd = analysis_results["amd_cycle"]
                if amd.get("phase") == "distribution" and amd.get("expected_direction") == _dir:
                    confluence_count += 1
                    confluence_factors.append("AMD Distribution")
            
            # Check displacement confluence
            if analysis_results.get("displacement"):
                disp = analysis_results["displacement"]
                if disp.get("distribution_confirmed"):
                    confluence_count += 1
                    confluence_factors.append("Displacement")
            
            # Check premium/discount zone confluence
            if analysis_results.get("premium_discount"):
                pd = analysis_results["premium_discount"]
                if pd.get("in_ote"):
                    confluence_count += 1
                    confluence_factors.append("OTE Zone")
            
            logger.info(f"Confluence factors for {symbol}: {confluence_count} ({', '.join(confluence_factors) if confluence_factors else 'none'})")
            print(f"[CONFLUENCE] {symbol}: {confluence_count} factors ({', '.join(confluence_factors) if confluence_factors else 'none'}), confidence={trade_signal.confidence:.0%}", flush=True)
            
            # Require minimum confluence factors for trades
            # In AGGRESSIVE mode (data collection), lower the bar to 1 factor at 65%+ confidence
            min_confluence = 1 if (self.scaling_manager and self.scaling_manager.current_mode.value == 'aggressive') else 2
            confidence_override = 0.65 if (self.scaling_manager and self.scaling_manager.current_mode.value == 'aggressive') else 0.80
            
            if confluence_count < min_confluence:
                # Allow Claude high-confidence signals through even with low confluence
                if trade_signal.confidence < confidence_override:
                    print(f"[FILTERED] {symbol}: Only {confluence_count} confluence factors (min {min_confluence}), confidence {trade_signal.confidence:.0%} < {confidence_override:.0%}", flush=True)
                    logger.info(
                        f"Trade signal filtered for {symbol}: Only {confluence_count} confluence "
                        f"factors (min {min_confluence} required). Confidence {trade_signal.confidence:.0%} too low to override."
                    )
                    if bot_state:
                        bot_state.trade_decision(symbol, "filtered", f"Low confluence ({confluence_count} factors)")
                        bot_state.symbol_complete(symbol, "low_confluence")
                    return
                else:
                    logger.info(f"Low confluence ({confluence_count}) but high confidence ({trade_signal.confidence:.0%}) - allowing through")
            
            # ============================================
            # SCALING MANAGER: Check if trade is allowed
            # ============================================
            print(f"[SCALING] {symbol}: Checking scaling manager...", flush=True)
            if self.scaling_manager:
                # Determine current mode based on performance
                # Use balance (realized P/L) for drawdown-related mode decisions
                account_info = await self.mt5_client.get_account_info()
                current_balance = account_info.balance if account_info else 1000.0
                
                # Let Claude help with mode decision if available
                # OVERRIDE: In AGGRESSIVE mode (data collection), ignore Claude's mode recommendation
                # to prevent it from downgrading to defensive/conservative during demo testing
                claude_mode = None
                if self.scaling_manager.current_mode != TradingMode.AGGRESSIVE:
                    if self.claude_client and self.claude_client.api_key:
                        try:
                            scaling_decision = await self.claude_client.assess_scaling_decision(
                                current_equity=current_balance,
                                current_tier=self.position_sizer.get_tier_name(current_balance) if self.position_sizer else "Unknown",
                                recent_performance=self.scaling_manager.get_recent_performance(),
                                goal_progress=self.scaling_manager.calculate_goal_progress(current_balance)
                            )
                            claude_mode = scaling_decision.get('recommended_mode')
                            print(f"[SCALING] {symbol}: Claude recommended mode: {claude_mode}", flush=True)
                        except Exception as e:
                            logger.debug(f"Could not get Claude scaling decision: {e}")
                else:
                    print(f"[SCALING] {symbol}: AGGRESSIVE mode locked (data collection) — skipping Claude mode assessment", flush=True)
                
                # Determine current trading mode (uses balance for drawdown watermarks)
                mode = self.scaling_manager.determine_mode(current_balance, claude_mode)
                print(f"[SCALING] {symbol}: Mode={mode.value}, balance={current_balance}", flush=True)
                if mode != self.scaling_manager.current_mode:
                    self.scaling_manager.current_mode = mode
                    logger.info(f"Trading mode changed to: {mode.value}")
                
                # Determine setup grade from confidence
                if trade_signal.confidence >= 0.85:
                    setup_grade_str = "A+"
                elif trade_signal.confidence >= 0.75:
                    setup_grade_str = "A"
                elif trade_signal.confidence >= 0.65:
                    setup_grade_str = "B"
                else:
                    setup_grade_str = "C"
                
                # Check if trade should be taken based on mode
                should_trade, rejection_reason = self.scaling_manager.should_take_trade(
                    setup_grade=setup_grade_str,
                    confidence=trade_signal.confidence,
                    daily_trades=self.daily_trades
                )
                
                print(f"[SCALING] {symbol}: should_trade={should_trade}, grade={setup_grade_str}, reason={rejection_reason}", flush=True)
                
                if not should_trade:
                    print(f"[BLOCKED] {symbol}: Scaling manager rejected - {rejection_reason}", flush=True)
                    logger.info(f"Trade signal rejected by scaling manager for {symbol}: {rejection_reason}")
                    if bot_state:
                        bot_state.trade_decision(symbol, "rejected", rejection_reason)
                    return
            
            # Validate confidence (fallback if no scaling manager)
            min_confidence = 0.60
            if self.scaling_manager:
                min_confidence = self.scaling_manager.get_mode_config().confidence_threshold
                
            if trade_signal.confidence < min_confidence:
                logger.info(f"Trade signal rejected for {symbol}: Low confidence ({trade_signal.confidence:.2f} < {min_confidence})")
                if bot_state:
                    bot_state.trade_decision(symbol, "rejected", f"Low confidence ({trade_signal.confidence:.2f})")
                    bot_state.symbol_complete(symbol, "low_confidence")
                return
            
            # R:R was already auto-corrected in the A6 block above (TP extended to meet min R:R).
            # We do NOT reject based on Claude's self-reported risk_reward field here
            # because the actual price-based R:R has already been fixed.
            # The final validate_trade() call will do the definitive R:R check.
            
            # =============================================
            # DIRECTION-FLIP COOLDOWN
            # =============================================
            # If Claude just flipped direction on the same symbol within 30 minutes,
            # require higher confidence (85%) to proceed. This prevents the
            # prediction-driven flip-flopping pattern (e.g., LONG 75% -> SHORT 75%).
            flip_cooldown_minutes = 30
            flip_min_confidence = 0.85
            
            if symbol in self._last_signal_direction:
                last_dir, last_time = self._last_signal_direction[symbol]
                minutes_since = (datetime.now() - last_time).total_seconds() / 60
                
                if (last_dir != trade_signal.direction and 
                    last_dir != 'no_trade' and 
                    minutes_since < flip_cooldown_minutes):
                    # Direction flip detected within cooldown window
                    if trade_signal.confidence < flip_min_confidence:
                        logger.warning(
                            f"[FLIP-GUARD] {symbol}: Blocked direction flip "
                            f"{last_dir.upper()} -> {trade_signal.direction.upper()} "
                            f"({trade_signal.confidence:.0%} < {flip_min_confidence:.0%} required, "
                            f"{minutes_since:.0f}min since last signal)"
                        )
                        from .api.routes.activity import add_activity
                        add_activity(
                            "direction_flip_blocked",
                            f"Blocked {symbol} flip: {last_dir.upper()} -> {trade_signal.direction.upper()} "
                            f"({trade_signal.confidence:.0%} < {flip_min_confidence:.0%})",
                            symbol,
                            {
                                "previous_direction": last_dir,
                                "new_direction": trade_signal.direction,
                                "confidence": trade_signal.confidence,
                                "required_confidence": flip_min_confidence,
                                "minutes_since_last": round(minutes_since, 1),
                            }
                        )
                        # Print BLOCKED to terminal
                        print(
                            f"[BLOCKED] ║  Direction flip {symbol}: was {last_dir.upper()} {minutes_since:.0f}m ago, "
                            f"need {flip_min_confidence:.0%} conf (got {trade_signal.confidence:.0%})",
                            flush=True
                        )
                        if bot_state:
                            bot_state.trade_decision(symbol, "rejected", 
                                f"Direction flip blocked ({last_dir} -> {trade_signal.direction}, need {flip_min_confidence:.0%})")
                        return
                    else:
                        logger.info(
                            f"[FLIP-GUARD] {symbol}: Allowing high-confidence flip "
                            f"{last_dir.upper()} -> {trade_signal.direction.upper()} "
                            f"({trade_signal.confidence:.0%} >= {flip_min_confidence:.0%})"
                        )
            
            # Track this signal direction for future flip detection
            self._last_signal_direction[symbol] = (trade_signal.direction, datetime.now())
            
            # Gap 21: Check for duplicate signals (prevent same trade within 1 hour)
            signal_hash = self._get_signal_hash(symbol, trade_signal.direction, trade_signal.entry_price or current_price)
            if signal_hash in self._recent_signal_hashes:
                print(f"[BLOCKED] {symbol}: Duplicate signal (same setup recently)", flush=True)
                logger.info(f"Duplicate signal ignored for {symbol} (same setup recently)")
                return
            
            # ============================================
            # CHECK CORRELATION BEFORE TRADING
            # ============================================
            if self.correlation_service:
                should_block, block_reason = self.correlation_service.should_block_trade(
                    symbol, direction=trade_signal.direction
                )
                if should_block:
                    print(f"[BLOCKED] {symbol}: Correlation block - {block_reason}", flush=True)
                    logger.warning(f"⚠️ CORRELATION BLOCK: {symbol} - {block_reason}")
                    if bot_state:
                        bot_state.trade_decision(symbol, "blocked", f"Correlation: {block_reason}")
                    return
                
                # Get position size multiplier
                size_multiplier = self.correlation_service.get_position_size_multiplier(symbol)
                if size_multiplier < 1.0:
                    logger.info(f"📊 Correlation adjustment: {symbol} size reduced to {size_multiplier*100:.0f}%")
            else:
                size_multiplier = 1.0
            
            # ============================================
            # PRE-LOCK: Get Claude's position size recommendation OUTSIDE the trade lock
            # to avoid holding the lock during a slow API call (2-30s)
            # ============================================
            claude_size_rec = None
            
            # Determine setup grade from confidence (needed for Claude call)
            if trade_signal.confidence >= 0.85:
                setup_grade = SetupGrade.A_PLUS
            elif trade_signal.confidence >= 0.75:
                setup_grade = SetupGrade.A
            elif trade_signal.confidence >= 0.65:
                setup_grade = SetupGrade.B
            else:
                setup_grade = SetupGrade.C
            
            if self.claude_client and self.claude_client.api_key:
                try:
                    # Pre-fetch account info for Claude call (non-critical, can be stale)
                    _pre_account = await self.mt5_client.get_account_info()
                    if _pre_account:
                        tier = self.position_sizer.get_tier(_pre_account.balance)
                        rec = await asyncio.wait_for(
                            self.claude_client.recommend_position_size(
                                equity=_pre_account.balance,
                                setup_grade=setup_grade.value,
                                confidence=trade_signal.confidence,
                                symbol=symbol,
                                win_streak=self.win_streak,
                                loss_streak=self.loss_streak,
                                base_lots=tier.base_lots,
                                max_lots=tier.max_lots
                            ),
                            timeout=15.0  # Don't wait more than 15s for size rec
                        )
                        if rec.get('recommended_lots'):
                            claude_size_rec = rec['recommended_lots']
                            logger.info(f"Claude size recommendation: {claude_size_rec} lots ({rec.get('reasoning', '')})")
                except asyncio.TimeoutError:
                    logger.warning("Claude position size recommendation timed out (15s)")
                except Exception as e:
                    logger.warning(f"Could not get Claude size recommendation: {e}")
            
            # Gap 20: Acquire trade lock to prevent race conditions
            async with self._trade_lock:
                # Re-check daily trade limit under lock
                if self.daily_trades >= settings.trading.max_daily_trades:
                    logger.info(f"Daily trade limit reached ({self.daily_trades}/{settings.trading.max_daily_trades})")
                    return
                
                # Reserve this trade slot immediately to prevent over-execution
                self.daily_trades += 1
                logger.info(f"Trade slot reserved ({self.daily_trades}/{settings.trading.max_daily_trades})")
                
                # Get account info for position sizing (fresh, under lock)
                account_info = await self.mt5_client.get_account_info()
                if not account_info:
                    logger.error("Failed to get account info")
                    self.daily_trades = max(0, self.daily_trades - 1)
                    return
                
                # Calculate position size with scaling
                size_result = self.position_sizer.calculate_position_size(
                    equity=account_info.balance,
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss,
                    symbol=symbol,
                    confidence=trade_signal.confidence,
                    setup_grade=setup_grade,
                    win_streak=self.win_streak,
                    loss_streak=self.loss_streak,
                    current_exposure_lots=self._get_current_exposure_lots(),
                    correlation_multiplier=size_multiplier,
                    claude_recommendation=claude_size_rec
                )
                
                # Apply crypto volatility adjustment if applicable
                final_lots = size_result.lots
                if is_crypto and self.crypto_analyzer:
                    crypto_adj = self.crypto_analyzer.get_position_size_adjustment(symbol, final_lots)
                    from .config import normalize_lots
                    final_lots = normalize_lots(symbol, crypto_adj)
                    logger.info(f"🪙 Crypto volatility adjustment: {size_result.lots} -> {final_lots} lots")
                
                # Create position size object
                class SimplePositionSize:
                    def __init__(self, lots):
                        self.lots = lots
                
                position_size = SimplePositionSize(final_lots)
                
                # Log position sizing details
                logger.info(f"📊 Position sizing: {final_lots} lots")
                logger.info(f"   Tier: {size_result.tier_name}, Risk: {size_result.risk_percent*100:.1f}%")
                for adj in size_result.adjustments:
                    logger.info(f"   • {adj}")
                
                # Validate trade with risk manager
                print(f"[VALIDATE] {symbol}: Running risk validation...", flush=True)
                validation = self.risk_manager.validate_trade(
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    direction=trade_signal.direction,
                    symbol=symbol,
                    account_balance=account_info.balance,
                    actual_risk_pct=size_result.risk_percent  # Use actual scaled risk, not default
                )
                
                if not validation.is_valid:
                    print(f"[BLOCKED] {symbol}: Validation failed - {validation.errors}", flush=True)
                    logger.warning(f"Trade validation failed for {symbol}: {validation.errors}")
                    self.daily_trades = max(0, self.daily_trades - 1)
                    logger.info(f"Trade slot released after validation failure ({self.daily_trades}/{settings.trading.max_daily_trades})")
                    return
                
                # =============================================
                # NEW: 100-PIP EXPANSION VALIDATION GATES
                # =============================================
                
                # GATE 1: Premium/Discount Zone Validation
                # Block longs in premium, shorts in discount
                if pd_analysis:
                    zone_validation = self.premium_discount_analyzer.validate_entry(
                        direction=trade_signal.direction,
                        current_price=current_price,
                        df=df
                    )
                    if not zone_validation["valid"]:
                        logger.warning(f"⚠️ ZONE BLOCK: {zone_validation['reason']}")
                        # Don't block entirely - switch to pending order for better entry
                        if trade_signal.order_type == 'market':
                            # Switch to limit order at OTE zone
                            if trade_signal.direction == 'long':
                                trade_signal.order_type = 'buy_limit'
                                trade_signal.entry_price = pd_analysis.ote_low  # 79% retracement
                                logger.info(f"🔄 Converted to BUY LIMIT @ {trade_signal.entry_price:.5f} (OTE zone)")
                            else:
                                trade_signal.order_type = 'sell_limit'
                                trade_signal.entry_price = pd_analysis.ote_high  # 62% retracement
                                logger.info(f"🔄 Converted to SELL LIMIT @ {trade_signal.entry_price:.5f} (OTE zone)")
                    else:
                        logger.info(f"✅ Zone check passed: {zone_validation['reason']}")
                
                # GATE 2: DXY Correlation Check for FX Pairs
                # Block trades that conflict with DXY direction
                if dxy_confirmation and symbol in ['EURUSD', 'GBPUSD', 'USDCHF', 'USDJPY', 'AUDUSD', 'NZDUSD']:
                    if trade_signal.direction != dxy_confirmation:
                        logger.warning(
                            f"⚠️ DXY CONFLICT: {symbol} {trade_signal.direction.upper()} "
                            f"conflicts with DXY confirming {dxy_confirmation.upper()}"
                        )
                        # Reduce position size by 50% instead of blocking
                        original_lots = position_size.lots
                        from .config import normalize_lots as _nl
                        position_size.lots = _nl(symbol, position_size.lots * 0.5)
                        logger.info(f"📉 Reduced size due to DXY conflict: {original_lots} -> {position_size.lots} lots")
                    else:
                        logger.info(f"✅ DXY confirms {trade_signal.direction.upper()} bias for {symbol}")
                
                # GATE 2b: RETAIL CONTRARIAN Check (NEW)
                # If retail is extreme, boost confidence when trading against them
                if retail_contrarian:
                    if trade_signal.direction == retail_contrarian:
                        # Trading WITH contrarian signal (against retail) = BOOST
                        trade_signal.confidence = min(0.95, trade_signal.confidence + 0.05)
                        logger.info(f"🔄✅ RETAIL CONTRARIAN BOOST: Trading against crowd (+5% confidence)")
                    else:
                        # Trading WITH retail = REDUCE confidence
                        trade_signal.confidence = max(0.5, trade_signal.confidence - 0.1)
                        logger.warning(
                            f"🔄⚠️ TRADING WITH RETAIL: {symbol} {trade_signal.direction.upper()} "
                            f"aligns with retail - reduced confidence (-10%)"
                        )
                
                # GATE 2c: VIX Risk Sentiment Check (NEW)
                if vix_risk_mode:
                    if vix_risk_mode == 'risk_off':
                        # Risk-off: Favor JPY, CHF, Gold
                        if symbol in ['USDJPY', 'USDCHF'] and trade_signal.direction == 'long':
                            logger.warning(f"⚠️ RISK-OFF: Long {symbol} less favorable - consider SHORT")
                            trade_signal.confidence = max(0.5, trade_signal.confidence - 0.05)
                        elif symbol == 'XAUUSD' and trade_signal.direction == 'long':
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.05)
                            logger.info(f"✅ RISK-OFF: Long Gold favored (+5% confidence)")
                    elif vix_risk_mode == 'risk_on':
                        # Risk-on: Favor AUD, NZD
                        if symbol in ['AUDUSD', 'NZDUSD'] and trade_signal.direction == 'long':
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.03)
                            logger.info(f"✅ RISK-ON: Long {symbol} favored (+3% confidence)")
                
                # GATE 2d: SOCIAL SENTIMENT Contrarian (NEW)
                social_sentiment = analysis_results.get("social_sentiment", {})
                if social_sentiment.get('contrarian_signal'):
                    social_contrarian = social_sentiment.get('contrarian_signal')
                    if trade_signal.direction == social_contrarian and social_sentiment.get('volume') == 'high':
                        # High volume contrarian = strong signal
                        trade_signal.confidence = min(0.95, trade_signal.confidence + 0.03)
                        logger.info(f"🐦✅ SOCIAL CONTRARIAN: High volume {social_contrarian.upper()} signal (+3%)")
                
                # GATE 2e: OPTIONS FLOW Confluence (NEW)
                options_flow = analysis_results.get("options_flow", {})
                if options_flow.get('flow') != 'neutral':
                    flow_direction = options_flow.get('flow')
                    if (trade_signal.direction == 'long' and flow_direction == 'bullish') or \
                       (trade_signal.direction == 'short' and flow_direction == 'bearish'):
                        trade_signal.confidence = min(0.95, trade_signal.confidence + 0.02)
                        logger.info(f"📊✅ OPTIONS FLOW confirms {trade_signal.direction.upper()} (+2%)")
                    
                    # Check if near magnet level (potential reversal zone)
                    magnet_levels = options_flow.get('magnet_levels', [])
                    if magnet_levels:
                        for magnet in magnet_levels:
                            if abs(current_price - magnet) / current_price < 0.001:  # Within 10 pips
                                logger.warning(f"⚠️ NEAR OPTIONS MAGNET LEVEL: {magnet} - Watch for reversal")
                
                # GATE 2f: INTERMARKET Risk Environment (NEW)
                intermarket = analysis_results.get("intermarket", {})
                if intermarket.get('risk_environment'):
                    risk_env = intermarket.get('risk_environment')
                    
                    # Strong risk-on = boost AUD/NZD longs, Strong risk-off = boost JPY/CHF/Gold longs
                    if 'strong_risk_on' in risk_env:
                        if symbol in ['AUDUSD', 'NZDUSD'] and trade_signal.direction == 'long':
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.05)
                            logger.info(f"🌐✅ STRONG RISK-ON: {symbol} LONG boosted (+5%)")
                        elif symbol in ['USDJPY', 'USDCHF'] and trade_signal.direction == 'short':
                            trade_signal.confidence = max(0.5, trade_signal.confidence - 0.05)
                            logger.warning(f"🌐⚠️ STRONG RISK-ON: {symbol} SHORT penalized (-5%)")
                    elif 'strong_risk_off' in risk_env:
                        if symbol == 'XAUUSD' and trade_signal.direction == 'long':
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.05)
                            logger.info(f"🌐✅ STRONG RISK-OFF: Gold LONG boosted (+5%)")
                        elif symbol in ['USDJPY', 'USDCHF'] and trade_signal.direction == 'short':
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.03)
                            logger.info(f"🌐✅ STRONG RISK-OFF: {symbol} SHORT boosted (safe-haven flows)")
                
                # GATE 2g: SEASONAL Pattern Boost (NEW)
                seasonal = analysis_results.get("seasonal_pattern", {})
                if seasonal.get('current_month_bias') != 'unknown':
                    seasonal_bias = seasonal.get('current_month_bias')
                    accuracy = seasonal.get('historical_accuracy', 0)
                    
                    if accuracy >= 65:  # Only trust high-accuracy patterns
                        if (trade_signal.direction == 'long' and seasonal_bias == 'bullish') or \
                           (trade_signal.direction == 'short' and seasonal_bias == 'bearish'):
                            boost = 0.03 if accuracy >= 75 else 0.02
                            trade_signal.confidence = min(0.95, trade_signal.confidence + boost)
                            logger.info(
                                f"📅✅ SEASONAL: {seasonal.get('current_month')} favors "
                                f"{seasonal_bias.upper()} ({accuracy}% accuracy, +{int(boost*100)}%)"
                            )
                
                # GATE 2h: BOND YIELD Spread for EUR/USD (NEW)
                yields = analysis_results.get("bond_yields", {})
                if yields.get('eurusd_bias') and 'EUR' in symbol:
                    yield_bias = yields.get('eurusd_bias')
                    spread = yields.get('spread', 0)
                    
                    if abs(spread) > 1.5:  # Significant spread
                        if (trade_signal.direction == 'long' and yield_bias == 'bullish') or \
                           (trade_signal.direction == 'short' and yield_bias == 'bearish'):
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.02)
                            logger.info(f"📈✅ YIELD SPREAD ({spread:.2f}%) confirms {trade_signal.direction.upper()} (+2%)")
                        else:
                            trade_signal.confidence = max(0.5, trade_signal.confidence - 0.03)
                            logger.warning(f"📈⚠️ YIELD SPREAD ({spread:.2f}%) conflicts - reduced confidence (-3%)")
                
                # GATE 2i: BTC DOMINANCE for Crypto (NEW)
                btc_dom = analysis_results.get("btc_dominance", {})
                if btc_dom and symbol in ['BTCUSD', 'ETHUSD', 'XRPUSD', 'SOLUSD', 'ADAUSD']:
                    if symbol == 'BTCUSD':
                        # BTC dominance rising = BTC bullish
                        if btc_dom.get('trend') == 'rising' and trade_signal.direction == 'long':
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.03)
                            logger.info(f"₿✅ BTC DOMINANCE rising: BTCUSD LONG boosted (+3%)")
                    else:
                        # Altcoins: opposite relationship
                        alt_sentiment = btc_dom.get('altcoin_sentiment', 'neutral')
                        if (trade_signal.direction == 'long' and alt_sentiment == 'bullish') or \
                           (trade_signal.direction == 'short' and alt_sentiment == 'bearish'):
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.03)
                            logger.info(f"₿✅ ALTCOIN sentiment {alt_sentiment}: {symbol} {trade_signal.direction.upper()} boosted (+3%)")
                
                # GATE 3: Displacement Check for Market Orders
                # Only allow immediate market execution if displacement is confirmed
                if trade_signal.order_type == 'market':
                    if displacement_analysis and not displacement_analysis.distribution_confirmed:
                        # Convert to pending order - wait for displacement
                        if amd_state and amd_state.phase.value in ['manipulation', 'accumulation']:
                            logger.warning(
                                f"⚠️ NO DISPLACEMENT: Converting market order to pending "
                                f"(AMD Phase: {amd_state.phase.value})"
                            )
                            if trade_signal.direction == 'long':
                                trade_signal.order_type = 'buy_limit'
                                # Entry at FVG or OB if available
                                entry_zone = analysis_results.get("fvg", {})
                                if hasattr(entry_zone, 'bullish_fvgs') and entry_zone.bullish_fvgs:
                                    nearest_fvg = min(entry_zone.bullish_fvgs, key=lambda x: abs(x.midpoint - current_price))
                                    trade_signal.entry_price = nearest_fvg.midpoint
                                elif pd_analysis:
                                    trade_signal.entry_price = pd_analysis.ote_low
                                logger.info(f"🔄 Converted to BUY LIMIT @ {trade_signal.entry_price:.5f}")
                            else:
                                trade_signal.order_type = 'sell_limit'
                                entry_zone = analysis_results.get("fvg", {})
                                if hasattr(entry_zone, 'bearish_fvgs') and entry_zone.bearish_fvgs:
                                    nearest_fvg = min(entry_zone.bearish_fvgs, key=lambda x: abs(x.midpoint - current_price))
                                    trade_signal.entry_price = nearest_fvg.midpoint
                                elif pd_analysis:
                                    trade_signal.entry_price = pd_analysis.ote_high
                                logger.info(f"🔄 Converted to SELL LIMIT @ {trade_signal.entry_price:.5f}")
                        else:
                            logger.info("✅ Displacement confirmed - proceeding with market order")
                
                # GATE 4: Silver Bullet Boost
                # If in SB window with displacement, boost confidence
                if silver_bullet_ready:
                    if trade_signal.confidence < 0.9:
                        trade_signal.confidence = min(0.95, trade_signal.confidence + 0.1)
                        logger.info(f"🔫⚡ Silver Bullet confidence boost: {trade_signal.confidence:.0%}")
                
                # GATE 5: Breaker Block A+ Setup
                # If entering at breaker block, boost confidence
                if breaker_blocks:
                    for bb in breaker_blocks:
                        entry_price = trade_signal.entry_price or current_price
                        if bb.bottom <= entry_price <= bb.top:
                            trade_signal.confidence = min(0.95, trade_signal.confidence + 0.1)
                            logger.info(f"🔄 Breaker Block entry - A+ setup! Confidence: {trade_signal.confidence:.0%}")
                            break
                
                # FINAL SAFETY CHECK before execution
                # Block if position size is 0 (blocked pair) or symbol is dangerous
                if position_size.lots <= 0:
                    logger.error(f"🚫 BLOCKED: Position size is 0 for {symbol} - trade not executed")
                    if bot_state:
                        bot_state.error(symbol, "Position size 0 - blocked pair")
                    return
                
                if symbol.upper().endswith('BTC') or symbol.upper().endswith('BIT'):
                    logger.error(f"🚫 FINAL BLOCK: {symbol} is BTC/BIT pair - REFUSING to execute!")
                    if bot_state:
                        bot_state.error(symbol, "BTC/BIT pair blocked at execution")
                    return
                
                # =============================================
                # P0 CRITICAL: MARGIN VALIDATION BEFORE TRADE
                # =============================================
                signal_order_type = getattr(trade_signal, 'order_type', 'market') or 'market'
                signal_trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
                precheck = await self.claude_trade_manager.precheck_trade(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss,
                    take_profit=trade_signal.take_profit,
                    confidence=trade_signal.confidence,
                    order_type=signal_order_type,
                    trade_type=signal_trade_type
                )
                
                if not precheck.can_execute:
                    # =============================================
                    # SMART POSITION REPLACEMENT: If blocked by max
                    # positions and new signal is strong, close the
                    # weakest existing position to make room
                    # =============================================
                    is_position_limit_block = any("Max positions" in b for b in precheck.blockers)
                    
                    if is_position_limit_block and trade_signal.confidence >= 0.70:
                        replaced = await self._try_replace_weakest_position(
                            new_symbol=symbol,
                            new_confidence=trade_signal.confidence,
                            new_direction=trade_signal.direction
                        )
                        if replaced:
                            logger.info(
                                f"♻️ Position replacement: closed weak trade to make room "
                                f"for {symbol} ({trade_signal.confidence:.0%} confidence)"
                            )
                            # Re-run precheck now that a slot is freed
                            precheck = await self.claude_trade_manager.precheck_trade(
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=trade_signal.entry_price or current_price,
                                stop_loss=trade_signal.stop_loss,
                                take_profit=trade_signal.take_profit,
                                confidence=trade_signal.confidence,
                                order_type=signal_order_type,
                                trade_type=signal_trade_type
                            )
                    
                    if not precheck.can_execute:
                        logger.warning(f"🚫 Trade blocked by pre-check: {precheck.blockers}")
                        if bot_state:
                            bot_state.error(symbol, f"Pre-check failed: {'; '.join(precheck.blockers)}")
                        
                        # Blocked trade notifications disabled — only notify on executed trades/TP/SL
                        return
                
                # Log any warnings from precheck
                for warning in precheck.warnings:
                    logger.warning(f"⚠️ Trade warning: {warning}")
                
                # Use pre-checked position size (respects margin/exposure limits)
                if precheck.recommended_lots < position_size.lots:
                    logger.info(
                        f"📉 Position size adjusted by margin check: "
                        f"{position_size.lots} -> {precheck.recommended_lots} lots"
                    )
                    position_size.lots = precheck.recommended_lots
                
                # Log margin status
                logger.info(
                    f"💰 Margin check passed: "
                    f"Free margin: ${precheck.margin_check.free_margin:,.2f}, "
                    f"Level: {precheck.margin_check.margin_level:.0f}%"
                )
                
                # =============================================
                # NEW: MULTI-TIER TAKE PROFIT FOR 100-PIP TARGETS
                # =============================================
                
                # Get enhanced TP levels from IPDA tracker
                take_profit_levels = {}
                original_tp = trade_signal.take_profit
                
                if ipda_analysis and trade_signal.stop_loss:
                    try:
                        tp_levels = self.ipda_tracker.get_take_profit_levels(
                            direction=trade_signal.direction,
                            current_price=trade_signal.entry_price or current_price,
                            stop_loss=trade_signal.stop_loss
                        )
                        
                        # SANITY CHECK: Validate IPDA TP levels are in a reasonable price range
                        # Max TP should be within 10% of current price for forex, 20% for crypto/metals
                        _entry_ref = trade_signal.entry_price or current_price
                        _max_tp_pct = 0.20 if symbol in self.CRYPTO_SYMBOLS or symbol in ('XAUUSD', 'XAGUSD') else 0.10
                        
                        def _is_sane_tp(tp_price):
                            """Check if a TP price is within reasonable range of entry."""
                            if tp_price is None or tp_price <= 0:
                                return False
                            deviation = abs(tp_price - _entry_ref) / _entry_ref
                            return deviation <= _max_tp_pct
                        
                        take_profit_levels = {
                            'tp1': tp_levels.get('tp1') if _is_sane_tp(tp_levels.get('tp1')) else None,
                            'tp2': tp_levels.get('tp2') if _is_sane_tp(tp_levels.get('tp2')) else None,
                            'tp3': tp_levels.get('tp3') if _is_sane_tp(tp_levels.get('tp3')) else None,
                        }
                        
                        # Log if any levels were filtered out as insane
                        for key in ('tp1', 'tp2', 'tp3'):
                            raw = tp_levels.get(key)
                            if raw is not None and take_profit_levels[key] is None:
                                logger.warning(
                                    f"⚠️ IPDA {key} for {symbol} REJECTED: {raw:.5f} is too far "
                                    f"from entry {_entry_ref:.5f} (>{_max_tp_pct:.0%})"
                                )
                        
                        # Add NWOG target if available and closer than IPDA
                        if nwog_target and _is_sane_tp(nwog_target):
                            nwog_distance = abs(nwog_target - _entry_ref)
                            ipda_tp3 = take_profit_levels.get('tp3')
                            if ipda_tp3:
                                ipda_distance = abs(ipda_tp3 - _entry_ref)
                                # Use NWOG if it's between TP2 and TP3
                                if nwog_distance < ipda_distance:
                                    take_profit_levels['nwog_target'] = nwog_target
                                    logger.info(f"🎯 Added NWOG target @ {nwog_target:.5f}")
                        
                        # Use TP2 (IPDA level) as primary TP for better 100-pip chance
                        if take_profit_levels.get('tp2'):
                            # Calculate distance to ensure it's worth it
                            tp2_distance = abs(take_profit_levels['tp2'] - _entry_ref)
                            from .config import get_symbol_spec
                            _tp_spec = get_symbol_spec(symbol)
                            tp2_pips = tp2_distance / _tp_spec.pip_size
                            
                            if tp2_pips >= 50:  # At least 50 pips for extended target
                                trade_signal.take_profit = take_profit_levels['tp2']
                                logger.info(
                                    f"🎯 Extended TP to IPDA level: {trade_signal.take_profit:.5f} "
                                    f"({tp2_pips:.0f} pips)"
                                )
                            else:
                                # Keep original or use TP1 (2R)
                                trade_signal.take_profit = take_profit_levels.get('tp1', original_tp)
                        
                        logger.info(f"📊 TP Levels: TP1={take_profit_levels.get('tp1')}, "
                                   f"TP2={take_profit_levels.get('tp2')}, TP3={take_profit_levels.get('tp3')}")
                        
                    except Exception as e:
                        logger.warning(f"Could not calculate IPDA TP levels: {e}")
                
                # Log trade signal
                logger.info(f"Valid trade signal for {symbol}:")
                logger.info(f"  Direction: {trade_signal.direction}")
                logger.info(f"  Confidence: {trade_signal.confidence:.2f}")
                logger.info(f"  Entry: {trade_signal.entry_price}")
                logger.info(f"  SL: {trade_signal.stop_loss}, TP: {trade_signal.take_profit}")
                if take_profit_levels:
                    logger.info(f"  Multi-TP: {take_profit_levels}")
                logger.info(f"  R:R: {trade_signal.risk_reward}")
                logger.info(f"  Position Size: {position_size.lots} lots")
                logger.info(f"  Order Type: {trade_signal.order_type}")
                logger.info(f"  AMD Phase: {trade_signal.amd_phase}")
                logger.info(f"  Reasoning: {trade_signal.reasoning[:200] if trade_signal.reasoning else 'None'}...")
                
                # =============================================
                # FINAL R:R SAFETY NET (before execution)
                # Catches any R:R degradation from IPDA/OTE/zone adjustments
                # =============================================
                _final_entry = trade_signal.entry_price or current_price
                _final_sl = trade_signal.stop_loss
                _final_tp = trade_signal.take_profit
                _final_dir = trade_signal.direction
                
                if _final_sl and _final_tp:
                    _final_sl_dist = abs(_final_entry - _final_sl)
                    _final_tp_dist = abs(_final_tp - _final_entry)
                    _final_rr = _final_tp_dist / _final_sl_dist if _final_sl_dist > 0 else 0
                    
                    if _final_rr < min_rr and _final_sl_dist > 0:
                        # Auto-correct: extend TP to maintain minimum R:R
                        _required_tp_dist = _final_sl_dist * min_rr
                        if _final_dir == 'long':
                            trade_signal.take_profit = _final_entry + _required_tp_dist
                        else:
                            trade_signal.take_profit = _final_entry - _required_tp_dist
                        logger.warning(
                            f"FINAL R:R FIX for {symbol}: {_final_rr:.2f} -> {min_rr:.1f}. "
                            f"TP adjusted from {_final_tp:.5f} to {trade_signal.take_profit:.5f}"
                        )
                
                # =============================================
                # FINAL PRICE SANITY GATE (absolute protection)
                # Catches cross-symbol contamination from shared IPDA tracker
                # =============================================
                _gate_entry = trade_signal.entry_price or current_price
                _gate_tp = trade_signal.take_profit
                _gate_sl = trade_signal.stop_loss
                
                if _gate_tp and _gate_entry > 0:
                    _tp_deviation = abs(_gate_tp - _gate_entry) / _gate_entry
                    _max_deviation = 0.20 if symbol in self.CRYPTO_SYMBOLS or symbol in ('XAUUSD', 'XAGUSD') else 0.10
                    
                    if _tp_deviation > _max_deviation:
                        # TP is insanely far — fall back to R:R based TP
                        _fallback_sl_dist = abs(_gate_entry - _gate_sl) if _gate_sl else 0
                        if _fallback_sl_dist > 0:
                            if trade_signal.direction == 'long':
                                trade_signal.take_profit = _gate_entry + (_fallback_sl_dist * min_rr)
                            else:
                                trade_signal.take_profit = _gate_entry - (_fallback_sl_dist * min_rr)
                            print(
                                f"[SAFETY] TP REJECTED for {symbol}: {_gate_tp:.5f} was {_tp_deviation:.0%} from "
                                f"entry {_gate_entry:.5f}. Reset to {min_rr:.0f}R: {trade_signal.take_profit:.5f}",
                                flush=True
                            )
                            logger.warning(
                                f"SAFETY GATE: TP {_gate_tp} was {_tp_deviation:.0%} from entry. "
                                f"Reset to {min_rr:.0f}R: {trade_signal.take_profit}"
                            )
                
                # =============================================
                # TRADE JUDGE (pre-execution validation)
                # =============================================
                print(f"[JUDGE] {symbol}: Sending to trade judge (confidence={trade_signal.confidence:.0%}, dir={trade_signal.direction}, lots={position_size.lots})...", flush=True)
                judge_verdict = await self._run_trade_judge(
                    symbol, trade_signal, position_size, current_price
                )
                
                # Handle REJECT verdict — skip trade entirely
                if judge_verdict.get('verdict') == 'REJECT':
                    reason = judge_verdict.get('reason', 'Judge rejected')
                    flags = judge_verdict.get('risk_flags', [])
                    logger.warning(f"[JUDGE] REJECTED {symbol} {trade_signal.direction}: {reason}")
                    
                    from .api.routes.activity import add_activity
                    add_activity(
                        "trade_judge_reject",
                        f"Judge REJECTED {symbol} {trade_signal.direction}: {reason}",
                        symbol,
                        {
                            "verdict": "REJECT",
                            "reason": reason,
                            "risk_flags": flags,
                            "confidence": trade_signal.confidence,
                        }
                    )
                    
                    flags_str = ", ".join(flags) if flags else "none"
                    print(
                        f"[JUDGE] ║  REJECT {symbol} — \"{reason}\"  "
                        f"| flags: [{flags_str}]",
                        flush=True
                    )
                    
                    self.daily_trades = max(0, self.daily_trades - 1)
                    logger.info(f"Trade slot released after judge rejection ({self.daily_trades}/{settings.trading.max_daily_trades})")
                    return
                
                # Handle DEMOTE verdict — convert to pending limit order
                elif judge_verdict.get('verdict') == 'DEMOTE':
                    suggested = judge_verdict.get('suggested_entry')
                    
                    # Calculate demoted entry price
                    if suggested is not None and suggested > 0:
                        demoted_entry = suggested
                    else:
                        # Default: 0.2% improvement from current price
                        if trade_signal.direction == 'long':
                            demoted_entry = round(current_price * 0.998, 5)  # Buy cheaper
                        else:
                            demoted_entry = round(current_price * 1.002, 5)  # Sell higher
                    
                    # Override to pending limit order
                    if trade_signal.direction == 'long':
                        trade_signal.order_type = 'buy_limit'
                    else:
                        trade_signal.order_type = 'sell_limit'
                    trade_signal.entry_price = demoted_entry
                    
                    reason = judge_verdict.get('reason', 'Judge demoted')
                    flags = judge_verdict.get('risk_flags', [])
                    logger.info(
                        f"[JUDGE] Demoted {symbol} {trade_signal.direction} market -> "
                        f"{trade_signal.order_type} @ {demoted_entry:.5f} (reason: {reason})"
                    )
                    
                    # Log to activity feed
                    from .api.routes.activity import add_activity
                    add_activity(
                        "trade_judge_demote",
                        f"Judge demoted {symbol} {trade_signal.direction}: {reason}",
                        symbol,
                        {
                            "verdict": "DEMOTE",
                            "reason": reason,
                            "original_entry": current_price,
                            "demoted_entry": demoted_entry,
                            "risk_flags": flags,
                            "confidence": trade_signal.confidence,
                        }
                    )
                    
                    # Print DEMOTE to terminal
                    flags_str = ", ".join(flags) if flags else "none"
                    print(
                        f"[JUDGE] ║  DEMOTE {symbol} — \"{reason}\" → limit @ {demoted_entry:.5f}  "
                        f"| flags: [{flags_str}]",
                        flush=True
                    )
                else:
                    # APPROVE — log for visibility
                    reason = judge_verdict.get('reason', 'Approved')
                    flags = judge_verdict.get('risk_flags', [])
                    if flags:
                        logger.info(f"[JUDGE] Approved {symbol} with flags: {flags}")
                    
                    from .api.routes.activity import add_activity
                    add_activity(
                        "trade_judge_approve",
                        f"Judge approved {symbol} {trade_signal.direction} ({trade_signal.confidence:.0%})",
                        symbol,
                        {
                            "verdict": "APPROVE",
                            "reason": reason,
                            "risk_flags": flags,
                            "confidence": trade_signal.confidence,
                        }
                    )
                    
                    # Print APPROVE to terminal
                    flags_str = ", ".join(flags) if flags else "none"
                    print(
                        f"[JUDGE] ║  APPROVE {symbol} — \"{reason}\"  "
                        f"| flags: [{flags_str}]",
                        flush=True
                    )
                
                # =============================================
                # PENDING ORDER VS MARKET ORDER DECISION
                # =============================================
                order_type = trade_signal.order_type if hasattr(trade_signal, 'order_type') else 'market'
                entry_price = trade_signal.entry_price or current_price
                
                # Smart conversion: if Claude set a specific entry_price that differs from
                # current_price by more than 0.1%, force a pending order (limit/stop)
                if order_type == 'market' and trade_signal.entry_price and current_price > 0:
                    price_diff_pct = abs(trade_signal.entry_price - current_price) / current_price
                    if price_diff_pct > 0.001:  # >0.1% away from market = use pending
                        if trade_signal.direction == 'long':
                            if trade_signal.entry_price < current_price:
                                order_type = 'buy_limit'
                            else:
                                order_type = 'buy_stop'
                        else:
                            if trade_signal.entry_price > current_price:
                                order_type = 'sell_limit'
                            else:
                                order_type = 'sell_stop'
                        trade_signal.order_type = order_type
                        logger.info(
                            f"🔄 Auto-converted to {order_type}: entry {trade_signal.entry_price:.5f} "
                            f"differs from market {current_price:.5f} by {price_diff_pct:.2%}"
                        )
                
                # Respect Claude's explicit pending order choice — do NOT override to market
                # even during distribution phase. Claude knows the entry model.
                if order_type == 'market':
                    # Use market order - immediate execution
                    logger.info(f"📈 Executing MARKET order (AMD: {trade_signal.amd_phase})")
                    
                    result = await self.order_manager.place_market_order(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        volume=position_size.lots,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        comment="ICT_Bot"
                    )
                elif order_type in ['buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']:
                    # Use pending order at Claude's specified entry price
                    # Crypto trades 24/7, so give longer expiration
                    if is_crypto:
                        expiration_minutes = 480  # 8 hours for crypto
                    else:
                        # For forex, use session remaining but with a minimum of 60 minutes
                        session = self.kill_zone_checker.get_current_session() if self.kill_zone_checker else None
                        session_remaining = int(getattr(session, 'minutes_remaining', 240)) if session else 240
                        expiration_minutes = max(session_remaining, 60)  # At least 1 hour
                        expiration_minutes = min(expiration_minutes, 480)  # Max 8 hours
                    
                    logger.info(
                        f"⏳ Placing PENDING {order_type} order @ {entry_price}, "
                        f"expires in {expiration_minutes}min"
                    )
                    
                    result = await self.order_manager.place_pending_order(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        order_type=order_type,
                        volume=position_size.lots,
                        price=entry_price,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        expiration_minutes=expiration_minutes,
                        comment="ICT_Bot_Pending"
                    )
                    
                    # Track pending order
                    if result.success and (result.ticket or result.order_id):
                        await self.pending_order_manager.add_order(
                            ticket=result.ticket or result.order_id,
                            symbol=symbol,
                            order_type=order_type,
                            direction=trade_signal.direction,
                            volume=position_size.lots,
                            price=entry_price,
                            stop_loss=trade_signal.stop_loss,
                            take_profit=trade_signal.take_profit,
                            expiration_minutes=expiration_minutes
                        )
                else:
                    # Fallback to market order if order type unclear
                    logger.info(f"📈 Executing MARKET order (fallback from {order_type})")
                    
                    result = await self.order_manager.place_market_order(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        volume=position_size.lots,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        comment="ICT_Bot"
                    )
                
                if result.success:
                    # Trade slot was already reserved above (daily_trades incremented)
                    
                    # Update daily risk tracking so the risk limit is enforced
                    if hasattr(self, 'risk_manager') and self.risk_manager:
                        _sl_dist = abs((trade_signal.entry_price or current_price) - trade_signal.stop_loss) if trade_signal.stop_loss else 0
                        from .config import get_symbol_spec
                        _spec = get_symbol_spec(symbol)
                        _risk_pct = (position_size.lots * _sl_dist * _spec.contract_size) / (account_info.balance if account_info.balance > 0 else 1)
                        self.risk_manager.update_daily_risk(_risk_pct)
                        logger.info(f"Daily risk updated: +{_risk_pct*100:.2f}%, total: {self.risk_manager.daily_risk_used*100:.2f}%")
                    
                    # Gap 21: Track signal hash to prevent duplicates
                    self._recent_signal_hashes.add(signal_hash)
                    self._signal_hash_expiry[signal_hash] = datetime.now()
                    
                    logger.info(f"✓ Trade executed: {trade_signal.direction.upper()} {symbol}")
                    logger.info(f"  Ticket: {result.ticket}, Fill Price: {result.fill_price}")
                    
                    # Gap 57: Verify order actually exists in MT5
                    # Only verify for market orders — pending orders won't appear in positions yet
                    is_pending_order = order_type in ['buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']
                    if result.ticket and not self.mt5_client.is_simulation and not is_pending_order:
                        await asyncio.sleep(0.5)  # Brief delay for MT5 to process
                        positions = await self.mt5_client.get_positions(symbol=symbol)
                        
                        # MT5 Position is a dataclass, access attributes directly
                        position_exists = any(
                            p.ticket == result.ticket for p in positions
                        )
                        
                        if not position_exists:
                            logger.error(
                                f"⚠ Order reported success but position {result.ticket} not found in MT5! "
                                f"Manual verification required."
                            )
                            # Don't track the position if it doesn't exist
                            return
                        
                        logger.info(f"  ✓ Position verified in MT5")
                    elif is_pending_order:
                        logger.info(f"  ⏳ Pending order placed — will verify when filled")
                    
                    # Track position — but ONLY for market orders (immediately filled)
                    # Pending orders (buy_limit, sell_limit, etc.) are tracked by pending_order_manager
                    # and will be picked up by sync_with_mt5 when they fill
                    if result.ticket:
                        # Validate SL/TP are real values before tracking
                        tracked_sl = trade_signal.stop_loss if trade_signal.stop_loss and trade_signal.stop_loss > 0 else None
                        tracked_tp = trade_signal.take_profit if trade_signal.take_profit and trade_signal.take_profit > 0 else None
                        if not tracked_sl:
                            logger.error(f"CRITICAL: Position {result.ticket} has no valid SL! trade_signal.stop_loss={trade_signal.stop_loss}")
                        if not tracked_tp:
                            logger.warning(f"Position {result.ticket} has no TP set: trade_signal.take_profit={trade_signal.take_profit}")
                        
                        if is_pending_order:
                            # =============================================
                            # PENDING ORDER: Do NOT add to position_manager!
                            # MT5's get_positions() doesn't return pending orders,
                            # so sync_with_mt5 would falsely detect them as "closed".
                            # They're already tracked by pending_order_manager.
                            # When they fill, sync_with_mt5 will pick them up as new positions.
                            # =============================================
                            print(f"[PENDING] {symbol}: Pending {order_type} placed (ticket={result.ticket}, entry={entry_price:.5f}, SL={trade_signal.stop_loss}, TP={trade_signal.take_profit})", flush=True)
                            logger.info(f"Pending order {result.ticket} tracked by pending_order_manager (NOT position_manager)")
                            
                            # Add to activity feed as pending order (not "trade opened")
                            from .api.routes.activity import add_activity
                            add_activity(
                                "pending_order_placed",
                                f"Pending {order_type.upper()} {trade_signal.direction.upper()} {symbol} @ {entry_price:.5f}",
                                symbol,
                                {
                                    "ticket": result.ticket,
                                    "order_type": order_type,
                                    "direction": trade_signal.direction,
                                    "entry_price": entry_price,
                                    "stop_loss": trade_signal.stop_loss,
                                    "take_profit": trade_signal.take_profit,
                                    "lots": position_size.lots,
                                    "confidence": trade_signal.confidence
                                }
                            )
                            
                            # Save to database
                            await save_trade_to_db(
                                ticket=result.ticket,
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=entry_price,
                                stop_loss=trade_signal.stop_loss or 0.0,
                                take_profit=trade_signal.take_profit or 0.0,
                                position_size=position_size.lots,
                                confidence=trade_signal.confidence,
                                reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else ""
                            )
                            
                            # Send Telegram notification for pending order
                            await notify(
                                NotificationType.TRADE_OPENED,
                                f"Pending order placed: {symbol}",
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=entry_price,
                                stop_loss=trade_signal.stop_loss or 0.0,
                                take_profit=trade_signal.take_profit or 0.0,
                                lots=position_size.lots,
                                confidence=trade_signal.confidence,
                                ticket=result.ticket
                            )
                        else:
                            # =============================================
                            # MARKET ORDER: Immediately filled, add to position_manager
                            # =============================================
                            position = Position(
                                ticket=result.ticket,
                                symbol=symbol,
                                direction=trade_signal.direction,
                                volume=position_size.lots,
                                entry_price=result.fill_price or current_price,
                                stop_loss=tracked_sl or (result.fill_price or current_price),  # Fallback to entry (0 risk) rather than 0.0
                                take_profit=tracked_tp or 0.0,
                                open_time=datetime.now(),
                                trade_type=getattr(trade_signal, 'trade_type', 'intraday') or 'intraday',
                            )
                            
                            # Set multi-TP levels for partial close management
                            # Scalps: single TP — close full position, no partials
                            # Intraday/Swing: multi-TP with partial close management
                            _pos_trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
                            
                            if _pos_trade_type == 'scalp':
                                # Scalps: single TP, full close. No multi-TP complexity.
                                position.tp1 = position.take_profit
                                position.tp2 = 0.0
                                position.tp3 = 0.0
                                logger.info(f"  SCALP: Single TP at {position.tp1:.5f} (full close)")
                            elif take_profit_levels:
                                position.tp1 = take_profit_levels.get('tp1', 0.0) or 0.0
                                position.tp2 = take_profit_levels.get('tp2', 0.0) or 0.0
                                position.tp3 = take_profit_levels.get('tp3', 0.0) or 0.0
                                logger.info(
                                    f"  Multi-TP set: TP1={position.tp1}, TP2={position.tp2}, TP3={position.tp3}"
                                )
                            elif trade_signal.stop_loss and trade_signal.take_profit:
                                # Fallback: auto-calculate TP levels from SL/TP
                                _entry = result.fill_price or current_price
                                _sl_dist = abs(_entry - trade_signal.stop_loss)
                                if trade_signal.direction == 'long':
                                    position.tp1 = _entry + (_sl_dist * 1.0)   # 1R
                                    position.tp2 = _entry + (_sl_dist * 2.0)   # 2R
                                    position.tp3 = _entry + (_sl_dist * 3.0)   # 3R
                                else:
                                    position.tp1 = _entry - (_sl_dist * 1.0)   # 1R
                                    position.tp2 = _entry - (_sl_dist * 2.0)   # 2R
                                    position.tp3 = _entry - (_sl_dist * 3.0)   # 3R
                                logger.info(
                                    f"  Multi-TP (auto): TP1={position.tp1:.5f}, TP2={position.tp2:.5f}, TP3={position.tp3:.5f}"
                                )
                            
                            self.position_manager.add_position(position)
                            print(f"[TRADE] {symbol}: Market order filled (ticket={result.ticket}, fill={result.fill_price})", flush=True)
                            
                            # Track in correlation service
                            if self.correlation_service:
                                self.correlation_service.set_open_position(
                                    symbol, position_size.lots, trade_signal.direction
                                )
                            
                            # Add to activity feed
                            from .api.routes.activity import add_activity
                            add_activity(
                                "trade_opened",
                                f"Opened {trade_signal.direction.upper()} {symbol} @ {result.fill_price or current_price:.5f}",
                                symbol,
                                {
                                    "ticket": result.ticket,
                                    "direction": trade_signal.direction,
                                    "entry_price": result.fill_price or current_price,
                                    "stop_loss": trade_signal.stop_loss,
                                    "take_profit": trade_signal.take_profit,
                                    "lots": position_size.lots,
                                    "confidence": trade_signal.confidence
                                }
                            )
                            
                            # Save trade to database for history tracking
                            await save_trade_to_db(
                                ticket=result.ticket,
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=result.fill_price or current_price,
                                stop_loss=trade_signal.stop_loss or 0.0,
                                take_profit=trade_signal.take_profit or 0.0,
                                position_size=position_size.lots,
                                confidence=trade_signal.confidence,
                                reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else ""
                            )
                            
                            # Send Telegram notification
                            await notify(
                                NotificationType.TRADE_OPENED,
                                f"Trade opened: {symbol}",
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=result.fill_price or current_price,
                                stop_loss=trade_signal.stop_loss or 0.0,
                                take_profit=trade_signal.take_profit or 0.0,
                                lots=position_size.lots,
                                confidence=trade_signal.confidence,
                                ticket=result.ticket
                            )
                else:
                    # Release the reserved trade slot since execution failed
                    self.daily_trades = max(0, self.daily_trades - 1)
                    logger.error(f"✗ Trade execution failed for {symbol}: {result.message}")
                    logger.info(f"Trade slot released ({self.daily_trades}/{settings.trading.max_daily_trades})")
                    
                    # Log error to activity feed
                    from .api.routes.activity import add_activity
                    add_activity(
                        "error",
                        f"Trade execution failed for {symbol}: {result.message}",
                        symbol,
                        {"error": result.message}
                    )
                
        except Exception as e:
            print(f"[ERROR] _analyze_and_trade({symbol}) CRASHED: {e}", flush=True)
            logger.error(f"Error analyzing {symbol}: {e}")
            import traceback
            traceback.print_exc()
            # Release trade slot if it was reserved before the crash
            if hasattr(self, '_trade_lock') and self.daily_trades > 0:
                self.daily_trades = max(0, self.daily_trades - 1)
                logger.info(f"Trade slot released after crash ({self.daily_trades}/{settings.trading.max_daily_trades})")
    
    async def _run_analysis_only(self, symbol: str):
        """
        Run analysis without executing trades (for simulation mode).
        Still saves signals for dashboard display.
        """
        try:
            df = await self.data_fetcher.get_ohlcv(
                symbol=symbol,
                timeframe=settings.timeframes.execution_tf,
                count=200
            )
            
            if df is None or df.empty:
                return
            
            # Run technical analysis (symbol-specific pip_value)
            from .config import get_symbol_spec as _get_spec2
            _ao_pip = _get_spec2(symbol).pip_size
            analysis_results = {
                "market_structure": MarketStructureAnalyzer().analyze(df),
                "fvg": FVGDetector(pip_value=_ao_pip).detect(df),
                "order_blocks": OrderBlockDetector().detect(df),
                "liquidity": LiquidityMapper(pip_value=_ao_pip).analyze(df)
            }
            
            # Volume analysis (simulation mode)
            try:
                volume_analysis = VolumeAnalyzer().analyze(df)
                analysis_results["volume"] = volume_analysis.to_dict()
                if bot_state:
                    bot_state.volume_analysis_complete(
                        symbol,
                        volume_analysis.relative_volume,
                        volume_analysis.volume_trend,
                        len(volume_analysis.spike_bars),
                        volume_analysis.relative_volume < 0.5
                    )
            except Exception as e:
                logger.warning(f"Volume analysis error (simulation): {e}")
                analysis_results["volume"] = {}
            
            current_price = float(df['close'].iloc[-1])
            
            # Skip Claude if not configured
            if not self.claude_client or not self.claude_client.api_key:
                return
            
            # Generate chart image
            chart_base64 = await self._generate_chart_image(df, symbol)
            if not chart_base64:
                return
            
            # Build context and data
            strategy_context = self.context_builder.get_quick_reference()
            market_data = {
                "current_price": current_price,
                "bid": current_price - 0.00005,
                "ask": current_price + 0.00005,
                "spread": 1.0
            }
            
            # Add volume profile to market_data for Claude
            if 'volume' in analysis_results:
                market_data["volume_profile"] = analysis_results["volume"]
            
            analysis_data = {
                "market_structure": {
                    "trend": analysis_results["market_structure"].trend.value,
                    "structure_breaks": len(analysis_results["market_structure"].structure_breaks)
                },
                "fvg": {
                    "bullish": len(analysis_results["fvg"].bullish_fvgs),
                    "bearish": len(analysis_results["fvg"].bearish_fvgs),
                    "active": len(analysis_results["fvg"].active_fvgs)
                },
                "order_blocks": {
                    "bullish": len(analysis_results["order_blocks"].bullish_obs),
                    "bearish": len(analysis_results["order_blocks"].bearish_obs)
                },
                "liquidity": {
                    "nearest_bsl": float(analysis_results["liquidity"].nearest_bsl) if analysis_results["liquidity"].nearest_bsl else None,
                    "nearest_ssl": float(analysis_results["liquidity"].nearest_ssl) if analysis_results["liquidity"].nearest_ssl else None
                },
                "volume": analysis_results.get("volume", {})
            }
            
            # Get Claude's analysis
            claude_result = await self.claude_client.analyze_chart_async(
                chart_image_base64=chart_base64,
                symbol=symbol,
                timeframe=settings.timeframes.execution_tf,
                strategy_context=strategy_context,
                market_data=market_data,
                analysis_data=analysis_data
            )
            
            # Save signal for dashboard (even in simulation mode)
            trade_signal = claude_result.signal
            self._save_signal(symbol, trade_signal, analysis_results)
            
            logger.info(f"[SIMULATION] Analysis complete for {symbol}: {trade_signal.direction} ({trade_signal.confidence:.0%})")
            
        except Exception as e:
            logger.error(f"Error in analysis-only mode for {symbol}: {e}")
    
    async def _generate_chart_image(self, df, symbol: str, timeframe: Optional[str] = None) -> Optional[str]:
        """Generate a chart image and return as base64."""
        try:
            from .utils.chart_screenshot import create_simple_chart
            if create_simple_chart:
                tf_label = timeframe or settings.timeframes.execution_tf
                # create_simple_chart already returns base64 encoded string
                chart_base64 = create_simple_chart(
                    df, 
                    symbol, 
                    tf_label
                )
                if chart_base64:
                    return chart_base64
        except ImportError:
            logger.warning("Chart screenshot module not available")
        except Exception as e:
            logger.warning(f"Error generating chart: {e}")
        
        # Return a minimal placeholder if chart generation fails
        # Claude will rely more on the analysis data
        return self._create_placeholder_image()
    
    def _create_placeholder_image(self) -> str:
        """Create a minimal placeholder PNG image."""
        import struct
        import zlib
        
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
        
        png_data = signature + ihdr + idat + iend
        return base64.b64encode(png_data).decode('utf-8')
    
    def _save_signal(self, symbol: str, trade_signal, analysis_results):
        """Save a signal to the API signals store for dashboard display."""
        try:
            from .api.routes.analysis import add_signal
            
            signal_data = {
                'symbol': symbol,
                'direction': trade_signal.direction,
                'confidence': trade_signal.confidence,
                'reasoning': trade_signal.reasoning or '',
                'market_structure': trade_signal.market_structure or analysis_results["market_structure"].trend.value,
                'entry_price': trade_signal.entry_price,
                'stop_loss': trade_signal.stop_loss,
                'take_profit': trade_signal.take_profit,
                'risk_reward': trade_signal.risk_reward
            }
            add_signal(signal_data)
            logger.debug(f"Saved signal for {symbol}: {trade_signal.direction}")
        except Exception as e:
            logger.warning(f"Could not save signal: {e}")
    
    async def _manage_open_positions(self):
        """
        Legacy method — position management now runs in the independent
        _position_management_loop() task (every 10 seconds).
        
        This method is kept as a no-op for backward compatibility in case
        any other code paths call it.
        """
        pass
    
    async def _close_largest_loser(self):
        """
        Emergency function to close the largest losing position.
        Called when margin level drops below emergency threshold.
        """
        try:
            if not self.position_manager or not self.position_manager.positions:
                logger.warning("No positions to close in emergency")
                return
            
            # Find the position with the largest loss
            largest_loser = None
            largest_loss = 0
            
            for position in self.position_manager.positions.values():
                if position.unrealized_pnl < largest_loss:
                    largest_loss = position.unrealized_pnl
                    largest_loser = position
            
            if not largest_loser:
                logger.info("No losing positions to close")
                return
            
            logger.critical(
                f"🚨 EMERGENCY CLOSE: Closing {largest_loser.symbol} position "
                f"(ticket: {largest_loser.ticket}, loss: ${largest_loss:.2f})"
            )
            
            # Close the position
            result = await self.order_manager.close_position(largest_loser.ticket)
            
            if result.success:
                logger.info(f"✓ Emergency close successful for position {largest_loser.ticket}")
                self.position_manager.remove_position(largest_loser.ticket)
                
                # Notify
                await notify(
                    NotificationType.ALERT,
                    f"🚨 EMERGENCY CLOSE\n\n"
                    f"Symbol: {largest_loser.symbol}\n"
                    f"Reason: Margin emergency\n"
                    f"Loss: ${largest_loss:.2f}"
                )
                
                # Log activity
                from .api.routes.activity import add_activity
                add_activity(
                    "emergency_close",
                    f"Emergency margin close: {largest_loser.symbol}",
                    largest_loser.symbol,
                    {"ticket": largest_loser.ticket, "loss": largest_loss, "reason": "margin_emergency"}
                )
            else:
                logger.error(f"Failed to close position {largest_loser.ticket}: {result.error}")
                
        except Exception as e:
            logger.error(f"Error in emergency close: {e}")
            import traceback
            traceback.print_exc()
    
    async def _try_replace_weakest_position(
        self,
        new_symbol: str,
        new_confidence: float,
        new_direction: str
    ) -> bool:
        """
        Evaluate all open positions and close the weakest one if it makes
        sense to replace it with the new, higher-confidence signal.
        
        A position is considered "weak" / replaceable if:
        - It is stagnant (< 0.3R move after being open for 2+ hours)
        - It is at a small loss or near break-even with low momentum
        - Its confidence was lower than the incoming signal
        
        Returns True if a position was closed to make room.
        """
        try:
            positions = self.position_manager.get_all_positions()
            if not positions:
                return False
            
            # Don't replace with the same symbol we already hold
            positions = [p for p in positions if p.symbol != new_symbol]
            if not positions:
                logger.info(f"♻️ No replaceable positions (already hold {new_symbol})")
                return False
            
            # Score each position — lower score = weaker = better replacement candidate
            import time
            candidates = []
            
            for pos in positions:
                score = 0.0
                hours_open = 0
                
                # Time in trade
                if hasattr(pos, 'open_time') and pos.open_time:
                    try:
                        from datetime import datetime, timezone
                        if isinstance(pos.open_time, str):
                            open_dt = datetime.fromisoformat(pos.open_time.replace('Z', '+00:00'))
                        else:
                            open_dt = pos.open_time
                        
                        # Calculate time delta correctly
                        if open_dt.tzinfo:
                            delta = datetime.now(timezone.utc) - open_dt
                        else:
                            delta = datetime.now() - open_dt
                        hours_open = delta.total_seconds() / 3600
                    except Exception:
                        hours_open = 0
                
                # R-multiple (how much the trade has moved in our favour)
                r_mult = getattr(pos, 'current_r_multiple', 0) or 0
                
                # P&L
                pnl = getattr(pos, 'unrealized_pnl', 0) or 0
                
                # SCORING (higher = stronger position, keep it)
                # R-multiple: strong moves should be kept
                score += r_mult * 30
                
                # P&L direction: profitable positions score higher
                if pnl > 0:
                    score += 15
                elif pnl < 0:
                    score -= 10
                
                # Stagnation penalty: open for hours but barely moved
                if hours_open >= 2 and abs(r_mult) < 0.3:
                    score -= 25  # Very stagnant
                elif hours_open >= 1 and abs(r_mult) < 0.2:
                    score -= 15  # Stagnant
                
                # Freshness bonus: very new trades get benefit of doubt
                if hours_open < 0.5:
                    score += 20  # Don't close trades opened in last 30 min
                
                candidates.append({
                    "position": pos,
                    "score": score,
                    "r_mult": r_mult,
                    "pnl": pnl,
                    "hours_open": hours_open
                })
            
            if not candidates:
                return False
            
            # Sort by score ascending — weakest first
            candidates.sort(key=lambda c: c["score"])
            weakest = candidates[0]
            pos = weakest["position"]
            
            # Only replace if the weakest is genuinely weak
            # Don't close profitable trades running well
            min_replacement_threshold = -5  # Must score below this to be replaceable
            
            if weakest["score"] > min_replacement_threshold:
                logger.info(
                    f"♻️ No weak enough position to replace. "
                    f"Weakest: {pos.symbol} (score={weakest['score']:.1f}, "
                    f"R={weakest['r_mult']:.2f}, P&L=${weakest['pnl']:.2f}, "
                    f"{weakest['hours_open']:.1f}h open)"
                )
                return False
            
            # Close the weakest position
            logger.warning(
                f"♻️ REPLACING {pos.symbol} (score={weakest['score']:.1f}, "
                f"R={weakest['r_mult']:.2f}, P&L=${weakest['pnl']:.2f}, "
                f"{weakest['hours_open']:.1f}h open) "
                f"-> Making room for {new_symbol} ({new_confidence:.0%} confidence)"
            )
            
            result = await self.order_manager.close_position(pos.ticket)
            
            if result.success:
                self.position_manager.remove_position(pos.ticket)
                
                # Notify on Telegram
                await notify(
                    NotificationType.TRADE,
                    f"♻️ POSITION REPLACED\n\n"
                    f"Closed: {pos.symbol} (stagnant, {weakest['r_mult']:.2f}R, "
                    f"P&L ${weakest['pnl']:.2f})\n"
                    f"Reason: Making room for {new_symbol} ({new_confidence:.0%} confidence)"
                )
                
                from .api.routes.activity import add_activity
                add_activity(
                    "position_replaced",
                    f"Replaced {pos.symbol} ({weakest['r_mult']:.2f}R) -> {new_symbol} ({new_confidence:.0%})",
                    pos.symbol,
                    {
                        "closed_ticket": pos.ticket,
                        "closed_symbol": pos.symbol,
                        "closed_pnl": weakest['pnl'],
                        "closed_r_mult": weakest['r_mult'],
                        "new_symbol": new_symbol,
                        "new_confidence": new_confidence
                    }
                )
                
                logger.info(f"✓ Closed {pos.symbol} position {pos.ticket} for replacement")
                return True
            else:
                logger.error(f"Failed to close {pos.symbol} for replacement: {result.error}")
                return False
                
        except Exception as e:
            logger.error(f"Error in position replacement: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _check_volatility(self) -> Optional[str]:
        """
        Check for abnormal volatility across major pairs.
        Returns alert message if volatility spike detected.
        """
        try:
            volatility_alerts = []
            
            # Check ATR spike on major pairs
            check_symbols = ['EURUSD', 'GBPUSD', 'USDJPY']
            
            for symbol in check_symbols:
                if symbol not in settings.trading.symbols:
                    continue
                    
                try:
                    df = await self.data_fetcher.get_ohlcv(
                        symbol=symbol,
                        timeframe="M5",
                        count=50
                    )
                    
                    if df is None or df.empty:
                        continue
                    
                    # Calculate ATR
                    high_low = df['high'] - df['low']
                    atr = high_low.rolling(14).mean().iloc[-1]
                    current_range = float(df['high'].iloc[-1] - df['low'].iloc[-1])
                    
                    # Alert if current candle range is 3x normal ATR
                    if current_range > atr * 3:
                        volatility_alerts.append(
                            f"{symbol}: Range {current_range:.5f} is {current_range/atr:.1f}x ATR"
                        )
                        
                except Exception as e:
                    logger.debug(f"Error checking volatility for {symbol}: {e}")
            
            if volatility_alerts:
                return "; ".join(volatility_alerts)
            return None
            
        except Exception as e:
            logger.error(f"Error in volatility check: {e}")
            return None
    
    async def _handle_high_volatility(self, alert: str):
        """
        Handle high volatility conditions.
        Options: widen stops, close positions, or alert only.
        """
        try:
            # For now, just log and add to activity
            from .api.routes.activity import add_activity
            add_activity(
                "volatility_alert",
                f"High volatility detected: {alert}",
                None,
                {"alert": alert, "action": "monitoring"}
            )
            
            # Future: Could implement emergency close-all
            # await self._emergency_close_all("Volatility spike")
            
        except Exception as e:
            logger.error(f"Error handling high volatility: {e}")
    
    def _print_analysis_summary(
        self,
        symbol: str,
        trade_signal,
        claude_result,
        market_data: dict,
    ):
        """
        Print a structured multi-line analysis block to the terminal
        for real-time visibility into every Claude decision.
        """
        try:
            W = 62  # inner width
            direction = (trade_signal.direction or "no_trade").upper()
            confidence = trade_signal.confidence or 0
            
            # Header
            trade_type = (getattr(trade_signal, 'trade_type', None) or "intraday").upper()
            header = f"  {symbol} — {trade_type} {direction} @ {confidence:.0%} confidence"
            
            # Structure info
            structure = (trade_signal.market_structure or "unknown").capitalize()
            amd = (getattr(trade_signal, 'amd_phase', None) or "unknown").capitalize()
            d1_bias = str(market_data.get('d1_bias', 'N/A')).capitalize()
            h4_bias = str(market_data.get('h4_bias', 'N/A')).capitalize()
            h1_bias = str(market_data.get('h1_bias', 'N/A')).capitalize()
            htf_bias = str(market_data.get('htf_bias', 'N/A')).capitalize()
            raw_alignment = market_data.get('htf_alignment')
            alignment = "Aligned" if raw_alignment else ("Misaligned" if raw_alignment is not None else "N/A")
            m15_bias = str(market_data.get('m15_bias', 'N/A')).capitalize()
            m5_bias = str(market_data.get('m5_bias', 'N/A')).capitalize()
            m1_bias = str(market_data.get('m1_bias', 'N/A')).capitalize()
            fib_zone = str(market_data.get('fibonacci_zone', 'N/A'))
            in_ote = "Yes" if market_data.get('in_ote') else "No"
            
            # Trade levels
            entry = trade_signal.entry_price
            sl = trade_signal.stop_loss
            tp = trade_signal.take_profit
            rr = trade_signal.risk_reward
            order_type = getattr(trade_signal, 'order_type', 'market') or 'market'
            
            # Key levels from claude_result
            key_levels = getattr(claude_result, 'key_levels', {}) or {}
            s1 = key_levels.get('support_1')
            r1 = key_levels.get('resistance_1')
            
            # Reasoning (truncated, cleaned of newlines and markdown)
            raw_reasoning = (trade_signal.reasoning or "No reasoning provided")
            raw_reasoning = raw_reasoning.replace('\n', ' ').replace('\r', ' ').replace('**', '').replace('*', '')
            raw_reasoning = ' '.join(raw_reasoning.split())  # collapse multiple spaces
            reasoning = raw_reasoning[:120]
            if len(raw_reasoning) > 120:
                reasoning += "..."
            
            # Warnings
            warnings_list = getattr(claude_result, 'warnings', []) or []
            warnings_str = ", ".join(warnings_list) if warnings_list else "None"
            
            # Analysis time
            analysis_time = getattr(claude_result, 'analysis_time', 0) or 0
            
            # Build the box
            def pad(text: str) -> str:
                """Pad text to fit inside the box."""
                return text[:W].ljust(W)
            
            lines = []
            lines.append(f"[SIGNAL] ╔{'═' * W}╗")
            lines.append(f"[SIGNAL] ║{pad(header)}║")
            lines.append(f"[SIGNAL] ╠{'═' * W}╣")
            lines.append(f"[SIGNAL] ║{pad(f'  Structure: {structure:<10} | AMD Phase: {amd}')}║")
            lines.append(f"[SIGNAL] ║{pad(f'  D1: {d1_bias:<8} H4: {h4_bias:<8} H1: {h1_bias:<8} [{alignment}]')}║")
            lines.append(f"[SIGNAL] ║{pad(f'  M15: {m15_bias:<7} M5: {m5_bias:<8} M1: {m1_bias}')}║")
            lines.append(f"[SIGNAL] ║{pad(f'  Fib Zone:  {fib_zone:<10} | In OTE:    {in_ote}')}║")
            
            # Only show trade levels if there's an actual signal
            if direction not in ("NO_TRADE",):
                lines.append(f"[SIGNAL] ╠{'─' * W}╣")
                entry_str = f"{entry:.5f}" if entry else "market"
                sl_str = f"{sl:.5f}" if sl else "N/A"
                tp_str = f"{tp:.5f}" if tp else "N/A"
                rr_str = f"{rr:.1f}" if rr else "N/A"
                lines.append(f"[SIGNAL] ║{pad(f'  Entry: {entry_str}  | SL: {sl_str}  | TP: {tp_str}')}║")
                lines.append(f"[SIGNAL] ║{pad(f'  R:R: {rr_str:<12} | Order: {order_type}')}║")
                if s1 or r1:
                    s1_str = f"S1={s1:.5f}" if s1 else "S1=N/A"
                    r1_str = f"R1={r1:.5f}" if r1 else "R1=N/A"
                    lines.append(f"[SIGNAL] ║{pad(f'  Key Levels: {s1_str}  {r1_str}')}║")
            
            lines.append(f"[SIGNAL] ╠{'─' * W}╣")
            # Wrap reasoning to fit box
            reason_line1 = reasoning[:W - 4]
            lines.append(f"[SIGNAL] ║{pad(f'  {reason_line1}')}║")
            if len(reasoning) > W - 4:
                reason_line2 = reasoning[W - 4:]
                lines.append(f"[SIGNAL] ║{pad(f'  {reason_line2}')}║")
            
            lines.append(f"[SIGNAL] ║{pad(f'  Warnings: [{warnings_str[:W - 16]}]')}║")
            lines.append(f"[SIGNAL] ║{pad(f'  Analysis Time: {analysis_time:.1f}s')}║")
            lines.append(f"[SIGNAL] ╚{'═' * W}╝")
            
            print("\n".join(lines), flush=True)
            
        except Exception as e:
            # Never let display code crash the trading loop
            print(f"[SIGNAL] {symbol} — {(trade_signal.direction or 'unknown').upper()} "
                  f"@ {(trade_signal.confidence or 0):.0%} (display error: {e})", flush=True)
    
    async def _run_trade_judge(
        self,
        symbol: str,
        trade_signal,
        position_size,
        current_price: float,
    ) -> dict:
        """
        Run the Trade Judge — a pre-execution validation layer using Claude.
        
        Checks the proposed trade against learned patterns and risk math.
        Returns APPROVE (proceed as-is) or DEMOTE (convert to pending limit).
        
        Fails open: any error or timeout returns APPROVE so we never block
        a validated trade due to infrastructure issues.
        """
        default_approve = {"verdict": "APPROVE", "reason": "Judge skipped", "suggested_entry": None, "risk_flags": []}
        
        if not self.claude_client or not self.claude_client.api_key:
            return default_approve
        
        try:
            # Build signal summary
            signal_dict = {
                'symbol': symbol,
                'direction': trade_signal.direction,
                'confidence': trade_signal.confidence,
                'entry_price': trade_signal.entry_price or current_price,
                'stop_loss': trade_signal.stop_loss,
                'take_profit': trade_signal.take_profit,
                'order_type': getattr(trade_signal, 'order_type', 'market'),
                'trade_type': getattr(trade_signal, 'trade_type', 'intraday'),
                'reasoning': getattr(trade_signal, 'reasoning', ''),
            }
            
            # Build risk metrics
            entry = signal_dict['entry_price']
            sl = signal_dict['stop_loss'] or 0
            tp = signal_dict['take_profit'] or 0
            sl_distance = abs(entry - sl) if sl else 0
            tp_distance = abs(tp - entry) if tp else 0
            risk_reward = tp_distance / sl_distance if sl_distance > 0 else 0
            
            account_balance = 0.0
            if hasattr(self, 'mt5_client') and self.mt5_client:
                try:
                    acct = await self.mt5_client.get_account_info()
                    if acct:
                        account_balance = acct.balance
                except Exception:
                    pass
            
            position_size_pct = 0.0
            if account_balance > 0 and sl_distance > 0:
                from .config import get_symbol_spec
                spec = get_symbol_spec(symbol)
                risk_amount = sl_distance * getattr(position_size, 'lots', 0.01) * spec.contract_size
                position_size_pct = risk_amount / account_balance
            
            # Get current session
            session_name = ""
            if self.session_analytics:
                current_session = self.session_analytics.get_current_session()
                session_name = current_session.value if current_session else ""
            
            # Get drawdown info
            drawdown_pct = 0.0
            if self.scaling_manager:
                weekly_high = getattr(self.scaling_manager, 'weekly_high_equity', account_balance)
                if weekly_high > 0:
                    drawdown_pct = (weekly_high - account_balance) / weekly_high
            
            risk_metrics = {
                'account_balance': account_balance,
                'daily_pnl': self.daily_pnl,
                'drawdown_pct': drawdown_pct,
                'risk_reward': risk_reward,
                'position_size_pct': position_size_pct,
                'trades_today': self.daily_trades,
                'max_daily_trades': settings.trading.max_daily_trades if hasattr(settings, 'trading') else 5,
                'session': session_name,
            }
            
            # Get learning context (past mistakes and winning patterns)
            learning_context = ""
            if self.learning_service:
                try:
                    learning_context = await self.learning_service.build_context_for_claude(symbol, session_name)
                except Exception as e:
                    logger.debug(f"[JUDGE] Could not get learning context: {e}")
            
            # Call the judge with a timeout — fail open
            verdict = await asyncio.wait_for(
                self.claude_client.judge_trade(signal_dict, risk_metrics, learning_context),
                timeout=5.0
            )
            
            logger.info(
                f"[JUDGE] {symbol} {trade_signal.direction}: {verdict.get('verdict', 'APPROVE')} "
                f"— {verdict.get('reason', 'N/A')} | flags: {verdict.get('risk_flags', [])}"
            )
            
            return verdict
            
        except asyncio.TimeoutError:
            logger.warning(f"[JUDGE] Timeout for {symbol} — failing open (APPROVE)")
            return default_approve
        except Exception as e:
            logger.warning(f"[JUDGE] Error for {symbol}: {e} — failing open (APPROVE)")
            return default_approve
    
    async def _check_drawdown_circuit_breaker(self) -> bool:
        """
        Check if daily/weekly drawdown limit has been exceeded.
        Returns True if trading should be stopped.
        
        Kill switch levels (E5):
        - Daily drawdown >= max_daily_drawdown config: STOP trading for the day
        - Weekly drawdown >= max_weekly_drawdown config: STOP trading for the week
        - MT5 connection lost: ALERT and pause
        """
        try:
            # Get account info
            account = await self.mt5_client.get_account_info()
            if not account:
                # MT5 connection lost - alert and pause
                logger.error("KILL SWITCH: Cannot reach MT5 - pausing trading")
                await notify(
                    NotificationType.ERROR,
                    "KILL SWITCH: MT5 connection lost - trading paused",
                )
                from .api.routes.activity import add_activity
                add_activity(
                    "kill_switch",
                    "MT5 connection lost - trading paused",
                    None,
                    {"reason": "mt5_connection_lost"}
                )
                return True
            
            # Calculate daily P&L using BALANCE (realized P/L only)
            # Using equity here would cause unrealized dips on open trades to
            # trigger the kill switch prematurely — we only want to stop when
            # actual closed-trade losses exceed the drawdown limit.
            if not hasattr(self, '_daily_start_balance'):
                self._daily_start_balance = account.balance
            
            # Reset daily start balance on new day
            today = datetime.now().date()
            if hasattr(self, '_drawdown_date') and self._drawdown_date != today:
                self._daily_start_balance = account.balance
                self._drawdown_date = today
            elif not hasattr(self, '_drawdown_date'):
                self._drawdown_date = today
            
            # Use configured drawdown limits
            max_daily_dd = settings.trading.max_daily_drawdown  # Default 3%
            max_weekly_dd = settings.trading.max_weekly_drawdown  # Default 6%
            
            daily_drawdown = 0.0
            if self._daily_start_balance > 0:
                daily_drawdown = (self._daily_start_balance - account.balance) / self._daily_start_balance
            
            weekly_drawdown = 0.0
            if self.scaling_manager:
                # Use BALANCE (realized P/L only) not equity
                weekly_drawdown = self.scaling_manager.calculate_weekly_drawdown(account.balance)
            
            # Log drawdown values for debugging (only when significant)
            if daily_drawdown > 0.01 or weekly_drawdown > 0.01:
                logger.info(
                    f"Drawdown check (REALIZED only): balance=${account.balance:.2f}, equity=${account.equity:.2f}, "
                    f"daily_start=${self._daily_start_balance:.2f}, weekly_high=${self.scaling_manager.weekly_high_equity:.2f}, "
                    f"daily_dd={daily_drawdown:.2%}, weekly_dd={weekly_drawdown:.2%}"
                )
            
            # Weekly circuit breaker (only notify once per trigger)
            if weekly_drawdown >= max_weekly_dd:
                if not getattr(self, '_weekly_kill_switch_active', False):
                    self._weekly_kill_switch_active = True
                    logger.warning(
                        f"KILL SWITCH: Weekly drawdown {weekly_drawdown:.1%} exceeds limit {max_weekly_dd:.1%} - STOPPING"
                    )
                    await notify(
                        NotificationType.ERROR,
                        f"KILL SWITCH: Weekly drawdown {weekly_drawdown:.1%} - all trading halted until next week",
                    )
                    from .api.routes.activity import add_activity
                    add_activity(
                        "kill_switch",
                        f"Weekly drawdown {weekly_drawdown:.1%} exceeds {max_weekly_dd:.1%} limit",
                        None,
                        {"drawdown": weekly_drawdown, "limit": max_weekly_dd, "type": "weekly"}
                    )
                return True
            else:
                # Reset the flag once drawdown recovers below limit
                self._weekly_kill_switch_active = False
            
            # Daily circuit breaker (only notify once per trigger)
            if daily_drawdown >= max_daily_dd:
                if not getattr(self, '_daily_kill_switch_active', False):
                    self._daily_kill_switch_active = True
                    logger.warning(
                        f"KILL SWITCH: Daily drawdown {daily_drawdown:.1%} exceeds limit {max_daily_dd:.1%} - STOPPING"
                    )
                    await notify(
                        NotificationType.ERROR,
                        f"KILL SWITCH: Daily drawdown {daily_drawdown:.1%} - trading paused for today",
                    )
                    from .api.routes.activity import add_activity
                    add_activity(
                        "kill_switch",
                        f"Daily drawdown {daily_drawdown:.1%} exceeds {max_daily_dd:.1%} limit",
                        None,
                        {"drawdown": daily_drawdown, "limit": max_daily_dd, "type": "daily"}
                    )
                return True
            else:
                # Reset the flag once drawdown recovers below limit
                self._daily_kill_switch_active = False
            
            # Warning at 75% of daily limit (throttled)
            if daily_drawdown >= max_daily_dd * 0.75:
                if not getattr(self, '_daily_warning_sent', False):
                    self._daily_warning_sent = True
                    logger.warning(
                        f"DRAWDOWN WARNING: Daily drawdown {daily_drawdown:.1%} approaching limit {max_daily_dd:.1%}"
                    )
                    await notify(
                        NotificationType.ERROR,
                        f"WARNING: Daily drawdown at {daily_drawdown:.1%} (limit: {max_daily_dd:.1%})",
                    )
            else:
                self._daily_warning_sent = False
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking drawdown: {e} — failing SAFE (halt trading)")
            return True  # Fail safe: assume drawdown exceeded when check errors
    
    async def _check_daily_profit_target(self) -> bool:
        """
        Check if daily profit target has been reached.
        Uses REALIZED profit (balance change) not unrealized equity swings,
        so open trades don't prematurely trigger the target.
        Returns True if should stop opening new trades.
        """
        try:
            account = await self.mt5_client.get_account_info()
            if not account:
                return False
            
            if not hasattr(self, '_daily_start_balance'):
                self._daily_start_balance = account.balance
            
            # Use BALANCE (realized P/L only) — not equity
            # This prevents open trades with unrealized profit from blocking new trades
            if self._daily_start_balance > 0:
                daily_profit_pct = (account.balance - self._daily_start_balance) / self._daily_start_balance
            else:
                daily_profit_pct = 0.0
            
            # Daily profit target from config (default 5%)
            max_daily_profit = settings.trading.max_daily_profit_target
            
            if daily_profit_pct >= max_daily_profit:
                if not getattr(self, '_profit_target_notified', False):
                    self._profit_target_notified = True
                    logger.info(
                        f"DAILY PROFIT TARGET: Up {daily_profit_pct:.1%} today "
                        f"(target: {max_daily_profit:.1%}) - locking in gains"
                    )
                    from .api.routes.activity import add_activity
                    add_activity(
                        "profit_target",
                        f"Daily profit target reached: +{daily_profit_pct:.1%} (realized)",
                        None,
                        {"profit_pct": daily_profit_pct, "target": max_daily_profit}
                    )
                return True
            else:
                # Reset notification flag when below target
                self._profit_target_notified = False
            
            return False
        except Exception as e:
            logger.error(f"Error checking daily profit target: {e}")
            return False
    
    async def _update_goal_tracker(self):
        """
        Update goal tracker with current equity.
        Tracks progress towards $100K goal.
        """
        try:
            if not self.goal_tracker:
                return
            
            account = await self.mt5_client.get_account_info()
            if not account:
                return
            
            # Add equity snapshot
            self.goal_tracker.add_equity_snapshot(account.equity)
            
            # Check for milestone notifications
            await self._check_milestone_notification(account.equity)
            
            # Log progress periodically (every 10 cycles)
            if not hasattr(self, '_goal_log_counter'):
                self._goal_log_counter = 0
            
            self._goal_log_counter += 1
            
            if self._goal_log_counter >= 10:
                self._goal_log_counter = 0
                
                progress = self.goal_tracker.calculate_progress()
                
                logger.info(f"📈 GOAL PROGRESS: {progress['progress_percent']:.1f}% complete")
                logger.info(f"   Current: ${progress['current_equity']:.2f} / Target: ${progress['target_equity']:.2f}")
                logger.info(f"   Remaining: ${progress['remaining']:.2f}")
                
                if progress['progress_percent'] >= 100:
                    logger.info("🎉 GOAL REACHED! $100,000 equity achieved!")
                    
                    from .api.routes.activity import add_activity
                    add_activity(
                        "goal_reached",
                        "🎉 $100,000 EQUITY GOAL REACHED!",
                        None,
                        progress
                    )
                    
        except Exception as e:
            logger.error(f"Error updating goal tracker: {e}")
    
    async def _claude_reevaluate_positions(self):
        """
        Have Claude re-evaluate open positions.
        Claude can recommend: HOLD, CLOSE, or TIGHTEN_STOP
        """
        try:
            if not self.claude_client or not self.claude_client.api_key:
                return
            
            positions = self.position_manager.get_all_positions()
            if not positions:
                return
            
            logger.info(f"Claude re-evaluating {len(positions)} open positions...")
            
            for position in positions:
                try:
                    # Get current chart for this position
                    df = await self.data_fetcher.get_ohlcv(
                        symbol=position.symbol,
                        timeframe=settings.timeframes.execution_tf,
                        count=100
                    )
                    
                    if df is None or df.empty:
                        continue
                    
                    current_price = float(df['close'].iloc[-1])
                    
                    # Calculate time in trade and stagnation
                    hours_open = 0
                    try:
                        if hasattr(position, 'open_time') and position.open_time:
                            from datetime import datetime, timezone
                            if isinstance(position.open_time, str):
                                open_dt = datetime.fromisoformat(position.open_time.replace('Z', '+00:00'))
                            else:
                                open_dt = position.open_time
                            if open_dt.tzinfo:
                                delta = datetime.now(timezone.utc) - open_dt
                            else:
                                delta = datetime.now() - open_dt
                            hours_open = delta.total_seconds() / 3600
                    except Exception:
                        pass
                    
                    r_mult = getattr(position, 'current_r_multiple', 0) or 0
                    pnl = getattr(position, 'unrealized_pnl', 0) or 0
                    is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
                    
                    # Build context for Claude
                    position_context = f"""
## Open Position to Evaluate

- Symbol: {position.symbol}
- Direction: {position.direction.upper()}
- Entry Price: {position.entry_price}
- Current Price: {current_price}
- Stop Loss: {position.stop_loss}
- Take Profit: {position.take_profit}
- Current R-Multiple: {r_mult:.2f}R
- Unrealized P&L: ${pnl:.2f}
- Time in Trade: {hours_open:.1f} hours (since {position.open_time})
- Stagnant: {"YES - barely moved" if is_stagnant else "No"}

## Context -- BE PATIENT
Good entries need time to develop. Closing too early is worse than holding through
normal consolidation. Only recommend CLOSE if the original trade thesis is CLEARLY
invalidated (structure break against the position, key level lost, or R-multiple
below -0.5R). A trade that is flat or slightly positive is NOT a reason to close --
it means the market hasn't moved yet, not that the thesis is wrong. Let the trade breathe.

## Swing Exhaustion Check
Consider the 4-6 swing rule when evaluating this position:
- If the trade entered after 4+ swings into the POI with a sweep, the thesis is strong -- lean HOLD.
- If price is now making new swings AGAINST our position (4+ against), the thesis may be invalidating -- lean CLOSE.
- If price is consolidating/rounding near our entry, momentum may be shifting -- lean TIGHTEN.
- Use the 21 EMA as a trailing reference: if price has closed beyond the 21 EMA against our direction, consider TIGHTEN or CLOSE.

## Question
Based on current market conditions, should we:
1. HOLD - Keep position. This is the DEFAULT choice. Flat, slightly positive, or
   consolidating trades that haven't invalidated their thesis should be HELD.
   Let the trade develop.
2. CLOSE - ONLY if the trade thesis is CLEARLY invalidated: structure break against
   the position, key level lost, or R-multiple below -0.5R. A flat or barely
   profitable trade is NOT a reason to close. Stagnation is NOT invalidation.
3. TIGHTEN - Move stop loss closer to lock profits. Use when the trade is in
   profit and you want to protect gains while giving it room to run.

Default to HOLD unless there is strong evidence the thesis is broken.
Respond with one of: HOLD, CLOSE, or TIGHTEN
Include brief reasoning.
"""
                    
                    # Get Claude's recommendation (with timeout and validation)
                    try:
                        response = await asyncio.wait_for(
                            self.claude_client.async_client.messages.create(
                                model=self.claude_client.model,
                                max_tokens=200,
                                messages=[{
                                    "role": "user",
                                    "content": position_context
                                }]
                            ),
                            timeout=30  # 30s timeout per position
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Claude reevaluation timed out for {position.symbol}")
                        continue
                    except Exception as api_err:
                        logger.warning(f"Claude API error during reevaluation of {position.symbol}: {api_err}")
                        continue
                    
                    # Validate response structure
                    if not response or not hasattr(response, 'content') or not response.content:
                        logger.warning(f"Empty Claude response for {position.symbol} reevaluation")
                        continue
                    
                    recommendation = response.content[0].text.strip().upper()
                    
                    logger.info(f"Claude recommendation for {position.symbol}: {recommendation[:100]}")
                    
                    # Determine decision type for logging
                    if recommendation.startswith("CLOSE"):
                        decision = "CLOSE"
                    elif "TIGHTEN" in recommendation:
                        decision = "TIGHTEN"
                    else:
                        decision = "HOLD"
                    
                    # Log Claude re-evaluation to bot_state for frontend display
                    from .api.routes.bot_status import bot_state
                    bot_state._add_log(
                        "claude_reeval",
                        position.symbol,
                        f"Claude re-eval #{position.ticket}: {decision} — {recommendation[:80]}",
                        {
                            "decision": decision,
                            "ticket": position.ticket,
                            "symbol": position.symbol,
                            "direction": position.direction,
                            "r_multiple": round(r_mult, 2),
                            "pnl": round(pnl, 2),
                            "hours_open": round(hours_open, 1),
                            "reasoning": recommendation[:200],
                        }
                    )
                    
                    # Act on recommendation - use startswith to avoid "DO NOT CLOSE" matching
                    if decision == "CLOSE":
                        logger.warning(f"Claude recommends CLOSING {position.symbol} position")
                        # Close the position
                        result = await self.order_manager.close_position(position.ticket)
                        if result.success:
                            logger.info(f"Closed position {position.ticket} per Claude recommendation")
                            self.position_manager.remove_position(position.ticket)
                            
                            from .api.routes.activity import add_activity
                            add_activity(
                                "claude_close",
                                f"Claude closed {position.symbol}: {recommendation[:50]}",
                                position.symbol,
                                {"ticket": position.ticket, "reason": recommendation[:200]}
                            )
                    
                    elif decision == "TIGHTEN":
                        logger.info(f"Claude recommends TIGHTENING stop on {position.symbol}")
                        # Move stop to lock in some profit
                        if position.current_r_multiple > 0.5:
                            new_sl = position.entry_price  # At least break-even
                            if position.direction == 'long':
                                new_sl = max(position.stop_loss, position.entry_price)
                            else:
                                new_sl = min(position.stop_loss, position.entry_price)
                            
                            result = await self.order_manager.modify_order(
                                ticket=position.ticket,
                                stop_loss=new_sl
                            )
                            
                            if result.success:
                                position.stop_loss = new_sl
                                logger.info(f"Tightened stop on {position.ticket} to {new_sl}")
                    
                except Exception as e:
                    logger.error(f"Error re-evaluating position {position.ticket}: {e}")
                    
        except Exception as e:
            logger.error(f"Error in Claude re-evaluation: {e}")
    
    async def emergency_close_all(self, reason: str = "Emergency"):
        """
        Emergency close all open positions.
        Can be called via API endpoint during flash crashes.
        """
        try:
            positions = self.position_manager.get_all_positions()
            logger.warning(f"EMERGENCY CLOSE ALL: {len(positions)} positions - Reason: {reason}")
            
            for position in positions:
                try:
                    result = await self.order_manager.close_position(position.ticket)
                    if result.success:
                        logger.info(f"Emergency closed position {position.ticket}")
                        self.position_manager.remove_position(position.ticket)
                    else:
                        logger.error(f"Failed to close position {position.ticket}: {result.message}")
                except Exception as e:
                    logger.error(f"Error closing position {position.ticket}: {e}")
            
            from .api.routes.activity import add_activity
            add_activity(
                "emergency_close",
                f"Emergency close all: {reason}",
                None,
                {"positions_closed": len(positions), "reason": reason}
            )
            
        except Exception as e:
            logger.error(f"Error in emergency close all: {e}")
    
    async def _sync_positions_on_startup(self):
        """
        Gap 47: Sync positions from database and MT5 on startup.
        
        1. Load persisted positions from database
        2. Get actual positions from MT5
        3. Reconcile - MT5 is source of truth
        """
        logger.info("Syncing positions on startup...")
        
        try:
            # Load positions saved in database
            db_positions = await self.position_manager.load_from_db()
            logger.info(f"Loaded {len(db_positions)} positions from database")
            
            # Get actual positions from MT5
            if self.mt5_client.is_simulation:
                logger.info("Simulation mode - no MT5 positions to sync")
                return
            
            mt5_positions = await self.mt5_client.get_positions()
            # MT5 Position is a dataclass - access attributes directly
            mt5_tickets = {p.ticket for p in mt5_positions}
            
            logger.info(f"Found {len(mt5_positions)} positions in MT5")
            
            # Reconcile: MT5 is source of truth
            for mt5_pos in mt5_positions:
                ticket = mt5_pos.ticket
                
                # Check if we have this in database
                db_match = next((p for p in db_positions if p.ticket == ticket), None)
                
                if db_match:
                    # Restore position with saved management state
                    self.position_manager.positions[ticket] = db_match
                    logger.info(f"Restored position {ticket} from database")
                else:
                    # New position not in database - create and track
                    # MT5 Position has: type='buy'/'sell', price_open, sl, tp
                    # PositionManager Position expects: direction='long'/'short', entry_price, stop_loss, take_profit
                    position = Position(
                        ticket=ticket,
                        symbol=mt5_pos.symbol,
                        direction='long' if mt5_pos.type == 'buy' else 'short',
                        volume=mt5_pos.volume,
                        entry_price=mt5_pos.price_open,
                        stop_loss=mt5_pos.sl,
                        take_profit=mt5_pos.tp,
                        open_time=datetime.now()
                    )
                    self.position_manager.add_position(position)
                    logger.info(f"Added MT5 position {ticket} to tracking")
            
            # Initialize daily_trades from MT5 history to prevent counter drift after restart
            # But skip if a manual reset was requested (skip_mt5_trade_recount flag)
            try:
                from trading_bot.utils.state_persistence import get_persistence
                _persistence = get_persistence()
                skip_recount = _persistence.get('skip_mt5_trade_recount', False)
                
                if skip_recount:
                    logger.info("⚡ skip_mt5_trade_recount flag detected — daily_trades counter reset to 0")
                    self.daily_trades = 0
                    # Clear the flag so future restarts behave normally
                    _persistence.set('skip_mt5_trade_recount', False)
                else:
                    today_start = datetime.combine(datetime.now().date(), datetime.min.time())
                    today_deals = await self.mt5_client.get_history(today_start, datetime.now())
                    # Count only entry deals (entry=0 is trade-in)
                    entry_deals = [d for d in today_deals if d.get('entry') == 0 and d.get('volume', 0) > 0]
                    if entry_deals:
                        self.daily_trades = len(entry_deals)
                        logger.info(f"Initialized daily_trades from MT5 history: {self.daily_trades} trades today")
            except Exception as e:
                logger.warning(f"Could not initialize daily trades from history: {e}")
            
            # Log positions in DB but not in MT5 (closed while bot was down)
            for db_pos in db_positions:
                if db_pos.ticket not in mt5_tickets:
                    logger.warning(
                        f"Position {db_pos.ticket} was in database but not in MT5 - "
                        f"likely closed while bot was offline"
                    )
                    # Remove from database
                    await self.position_manager._delete_position_from_db(db_pos.ticket)
            
            logger.info(f"Position sync complete: tracking {len(self.position_manager.positions)} positions")
            
            # Also sync recent trade history on startup
            await self._sync_trade_history(days_back=30)
            
        except Exception as e:
            logger.error(f"Error syncing positions on startup: {e}")
            import traceback
            traceback.print_exc()
    
    async def _sync_trade_history(self, days_back: int = 1):
        """
        Sync closed trade history from MT5 to database.
        
        This catches trades that were closed while the bot was offline
        and ensures our analytics and goal tracking are accurate.
        
        Args:
            days_back: Number of days of history to sync (default 1, startup uses 30)
        """
        if not self.mt5_client or self.mt5_client.is_simulation:
            logger.debug("Skipping trade history sync - simulation mode or no MT5")
            return
        
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days_back)
            
            logger.info(f"Syncing trade history from {start_time} to {end_time}")
            
            # Get closed deals from MT5
            deals = await self.mt5_client.get_history(start_time, end_time)
            
            if not deals:
                logger.debug("No trade history to sync")
                self._last_history_sync = datetime.now()
                return
            
            synced_count = 0
            
            for deal in deals:
                # Skip if already synced
                deal_id = str(deal.get('ticket', deal.get('deal', 0)))
                if deal_id in self._synced_deal_ids:
                    continue
                
                # Only sync actual trades (not deposits/withdrawals)
                deal_type = deal.get('type', '')
                if deal_type not in ['buy', 'sell', 'DEAL_TYPE_BUY', 'DEAL_TYPE_SELL']:
                    continue
                
                # Extract trade data
                symbol = deal.get('symbol', '')
                if not symbol:
                    continue
                
                direction = 'long' if 'buy' in str(deal_type).lower() else 'short'
                profit = float(deal.get('profit', 0))
                volume = float(deal.get('volume', 0))
                price = float(deal.get('price', 0))
                close_time = deal.get('time', datetime.now())
                
                # Save to database if not exists
                if DB_AVAILABLE:
                    try:
                        async with async_session() as session:
                            # Check if trade already exists
                            from sqlalchemy import select
                            existing = await session.execute(
                                select(TradeModel).where(TradeModel.trade_id == deal_id)
                            )
                            if existing.scalar_one_or_none():
                                self._synced_deal_ids.add(deal_id)
                                continue
                            
                            # Create new trade record
                            trade = TradeModel(
                                trade_id=deal_id,
                                timestamp=close_time if isinstance(close_time, datetime) else datetime.now(),
                                symbol=symbol,
                                direction=direction,
                                timeframe="M15",
                                session="",
                                entry_price=price,
                                entry_time=close_time if isinstance(close_time, datetime) else datetime.now(),
                                exit_price=price,
                                exit_time=close_time if isinstance(close_time, datetime) else datetime.now(),
                                entry_reason="Synced from MT5 history",
                                exit_reason="Synced from MT5 history",
                                stop_loss=0.0,
                                take_profit=0.0,
                                position_size=volume,
                                profit_loss=profit,
                                risk_amount=0.0,
                                r_multiple=0.0,
                                claude_confidence=0.0,
                                claude_reasoning="Historical trade synced from MT5"
                            )
                            session.add(trade)
                            await session.commit()
                            synced_count += 1
                            
                    except Exception as e:
                        logger.warning(f"Could not save deal {deal_id} to database: {e}")
                
                # Mark as synced
                self._synced_deal_ids.add(deal_id)
                
                # Update analytics with synced trade
                if self.session_analytics and profit != 0:
                    try:
                        self.session_analytics.record_trade(
                            symbol=symbol,
                            direction=direction,
                            profit_loss=profit,
                            r_multiple=0.0,
                            entry_time=close_time if isinstance(close_time, datetime) else datetime.now()
                        )
                    except Exception as e:
                        logger.debug(f"Could not update session analytics for synced trade: {e}")
                
                # Update goal tracker
                if self.goal_tracker and profit != 0:
                    try:
                        account = await self.mt5_client.get_account_info()
                        if account:
                            self.goal_tracker.add_snapshot(account.equity)
                    except Exception as e:
                        logger.debug(f"Could not update goal tracker for synced trade: {e}")
            
            self._last_history_sync = datetime.now()
            
            if synced_count > 0:
                logger.info(f"Synced {synced_count} trades from MT5 history")
                
                # Update bot state for dashboard visibility
                if bot_state:
                    bot_state.error(None, f"Synced {synced_count} historical trades from MT5")
                
                # Add to activity feed
                from .api.routes.activity import add_activity
                add_activity(
                    "info",
                    f"Synced {synced_count} historical trades from MT5",
                    None,
                    {"synced_count": synced_count, "days_back": days_back}
                )
            else:
                logger.debug("No new trades to sync from MT5 history")
            
        except Exception as e:
            logger.error(f"Error syncing trade history: {e}")
            import traceback
            traceback.print_exc()
    
    def _should_sync_history(self) -> bool:
        """Check if it's time to sync trade history."""
        if self._last_history_sync is None:
            return True
        return datetime.now() - self._last_history_sync >= self._history_sync_interval

    async def _handle_position_close(self, position):
        """
        Gap 6: Handle position close - auto-log to journal.
        
        Called when a position is detected as closed.
        """
        try:
            logger.info(f"Position {position.ticket} closed, updating journal...")
            
            # Get closing details from MT5 (if available)
            close_time = datetime.now()
            
            # =============================================
            # GET ACTUAL P/L FROM MT5 HISTORY (authoritative)
            # The broker's deal.profit is the real P/L including
            # correct contract sizes, commissions, and swap.
            # Our manual calculation with config.py specs was WRONG
            # for crypto/metals where contract_size varies by broker.
            # =============================================
            from .config import get_symbol_spec
            _spec = get_symbol_spec(position.symbol)
            actual_close_price = position.current_price  # fallback
            profit_loss = None  # Will be set from MT5 if possible
            mt5_profit_found = False
            
            if hasattr(self, 'mt5_client') and self.mt5_client and not self.mt5_client.is_simulation:
                try:
                    from datetime import timedelta
                    history = await self.mt5_client.get_history(
                        close_time - timedelta(minutes=10), close_time + timedelta(minutes=1)
                    )
                    # Find the close deal for this ticket
                    for deal in history:
                        if deal.get('position_id') == position.ticket and deal.get('entry') == 1:
                            actual_close_price = deal.get('price', position.current_price)
                            # Use MT5's authoritative profit (includes commission + swap)
                            mt5_profit = deal.get('profit', None)
                            mt5_commission = deal.get('commission', 0) or 0
                            mt5_swap = deal.get('swap', 0) or 0
                            if mt5_profit is not None:
                                profit_loss = mt5_profit + mt5_commission + mt5_swap
                                mt5_profit_found = True
                                print(f"[CLOSE] {position.symbol}: MT5 actual P/L = ${profit_loss:.2f} (profit={mt5_profit}, commission={mt5_commission}, swap={mt5_swap})", flush=True)
                            logger.info(f"  Actual close price from MT5: {actual_close_price:.5f}, profit: {mt5_profit}")
                            break
                except Exception as e:
                    logger.warning(f"Could not fetch close details from MT5 history: {e}")
            
            # Fallback: manual calculation if MT5 history unavailable
            if profit_loss is None:
                from .config import calculate_pl
                if position.direction == 'long':
                    profit_loss = calculate_pl(position.symbol, actual_close_price - position.entry_price, position.volume)
                else:
                    profit_loss = calculate_pl(position.symbol, position.entry_price - actual_close_price, position.volume)
                print(f"[CLOSE] {position.symbol}: Fallback P/L = ${profit_loss:.2f} (tick_value={_spec.tick_value}, contract_size={_spec.contract_size})", flush=True)
            
            # Calculate pips using actual close price and symbol spec
            pip_size = _spec.pip_size
            
            raw_pips = (actual_close_price - position.entry_price) / pip_size
            
            if position.direction == 'short':
                pips = -raw_pips  # Short profits when price drops (negative raw_pips)
            else:
                pips = raw_pips   # Long profits when price rises (positive raw_pips)
            
            # Update daily P&L tracker
            self.daily_pnl += profit_loss
            
            # Update win/loss streak
            if profit_loss > 0:
                self.win_streak += 1
                self.loss_streak = 0
            elif profit_loss < 0:
                self.loss_streak += 1
                self.win_streak = 0
            
            # Add to activity feed
            from .api.routes.activity import add_activity
            add_activity(
                "trade_closed",
                f"Closed {position.symbol} {position.direction}: ${profit_loss:+.2f} ({pips:+.1f} pips)",
                position.symbol,
                {
                    "ticket": position.ticket,
                    "direction": position.direction,
                    "volume": position.volume,
                    "entry_price": position.entry_price,
                    "exit_price": actual_close_price,
                    "profit_loss": profit_loss,
                    "pips": pips,
                    "r_multiple": position.current_r_multiple
                }
            )
            
            # Send Telegram notification with actual close price and correct P/L
            await notify(
                NotificationType.TRADE_CLOSED,
                f"Trade closed: {position.symbol}",
                symbol=position.symbol,
                direction=position.direction,
                entry_price=position.entry_price,
                exit_price=actual_close_price,
                profit_loss=profit_loss,
                pips=pips,
                ticket=position.ticket
            )
            
            # Remove from correlation tracking
            if self.correlation_service:
                self.correlation_service.remove_position(position.symbol)
            
            # Update Session Analytics - track performance by session
            if self.session_analytics:
                try:
                    self.session_analytics.record_trade(
                        symbol=position.symbol,
                        direction=position.direction,
                        profit_loss=profit_loss,
                        r_multiple=position.current_r_multiple,
                        entry_time=position.open_time
                    )
                    logger.debug(f"Session analytics updated for {position.symbol}")
                except Exception as e:
                    logger.warning(f"Could not update session analytics: {e}")
            
            # Update Scaling Manager - track for mode adjustments
            if self.scaling_manager:
                try:
                    self.scaling_manager.record_trade({
                        'symbol': position.symbol,
                        'direction': position.direction,
                        'profit_loss': profit_loss,
                        'r_multiple': position.current_r_multiple
                    })
                    logger.debug(f"Scaling manager updated for {position.symbol}")
                except Exception as e:
                    logger.warning(f"Could not update scaling manager: {e}")
            
            # Have Claude review the trade for losses AND big wins (>2R)
            # This populates the learning system for future analysis improvement
            should_review = profit_loss < 0 or position.current_r_multiple >= 2.0
            
            if should_review and self.claude_client and self.claude_client.api_key:
                try:
                    # Retrieve original trade metadata from DB
                    entry_reason = 'N/A'
                    original_confidence = 0.0
                    trade_timeframe = 'M15'
                    if DB_AVAILABLE:
                        try:
                            from sqlalchemy import select
                            async with async_session() as db_sess:
                                result = await db_sess.execute(
                                    select(TradeModel).where(
                                        TradeModel.trade_id == str(position.ticket)
                                    )
                                )
                                trade_record = result.scalar_one_or_none()
                                if trade_record:
                                    entry_reason = trade_record.entry_reason or 'N/A'
                                    original_confidence = trade_record.claude_confidence or 0.0
                                    trade_timeframe = trade_record.timeframe or 'M15'
                                    logger.debug(f"Retrieved trade metadata for {position.ticket}: confidence={original_confidence:.0%}, timeframe={trade_timeframe}")
                        except Exception as e:
                            logger.warning(f"Could not retrieve trade metadata for {position.ticket}: {e}")
                    
                    trade_data = {
                        'symbol': position.symbol,
                        'direction': position.direction,
                        'entry_price': position.entry_price,
                        'exit_price': position.current_price,
                        'stop_loss': position.stop_loss,
                        'take_profit': position.take_profit,
                        'profit_loss': profit_loss,
                        'pips': pips,
                        'r_multiple': position.current_r_multiple,
                        'duration': str(close_time - position.open_time) if position.open_time else 'Unknown',
                        'entry_reason': entry_reason,
                        'original_confidence': original_confidence,
                        'timeframe': trade_timeframe,
                    }
                    review = await self.claude_client.review_closed_trade(trade_data)
                    logger.info(f"Claude trade review: Grade {review.get('grade', 'N/A')} - {review.get('analysis', '')[:100]}")
                    
                    # Store the review in the learning system
                    if self.learning_service and review:
                        # Get session from session analytics if available
                        session_name = ""
                        if self.session_analytics:
                            current_session = self.session_analytics.get_current_session()
                            session_name = current_session.value if current_session else ""
                        
                        # Use trade_type for setup classification (scalp/intraday/swing)
                        _setup_type = f"ICT-{getattr(position, 'trade_type', 'intraday').upper()}"
                        await self.learning_service.store_trade_review(
                            trade_id=str(position.ticket),
                            symbol=position.symbol,
                            direction=position.direction,
                            profit_loss=profit_loss,
                            r_multiple=position.current_r_multiple,
                            review=review,
                            session=session_name,
                            setup_type=_setup_type,
                            entry_reason=entry_reason,
                            original_confidence=original_confidence,
                            timeframe=trade_timeframe,
                        )
                        logger.info(f"Trade review stored for learning: {position.ticket}")
                        
                except Exception as e:
                    logger.warning(f"Could not get/store Claude trade review: {e}")
                    
        except Exception as e:
            logger.error(f"Error handling position close: {e}")
    
    def _get_signal_hash(self, symbol: str, direction: str, entry_price: float) -> str:
        """
        Generate a hash for a signal to detect duplicates.
        
        Args:
            symbol: Trading symbol
            direction: Trade direction
            entry_price: Entry price (rounded to prevent false negatives)
            
        Returns:
            Hash string for the signal
        """
        # Round entry price to 4 decimal places for comparison
        rounded_price = round(entry_price, 4)
        return f"{symbol}_{direction}_{rounded_price}"
    
    def _cleanup_expired_signal_hashes(self):
        """Remove signal hashes older than 1 hour."""
        one_hour_ago = datetime.now() - timedelta(hours=1)
        expired = [
            hash_key for hash_key, timestamp in self._signal_hash_expiry.items()
            if timestamp < one_hour_ago
        ]
        for hash_key in expired:
            self._recent_signal_hashes.discard(hash_key)
            del self._signal_hash_expiry[hash_key]
    
    async def _check_daily_reset(self):
        """Check and reset daily counters if new day."""
        today = datetime.now().date()
        if today != self.last_reset_date:
            logger.info("New trading day - resetting daily counters")
            
            # Send daily summary for previous day before reset
            if self.daily_trades > 0 or self.daily_pnl != 0:
                await self._send_daily_summary()
            
            # Get current balance for resets (use balance = realized P/L only,
            # so unrealized equity swings don't inflate the daily/weekly watermarks)
            account = await self.mt5_client.get_account_info() if self.mt5_client else None
            current_equity = account.balance if account else 1000.0
            
            # Reset daily counters
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.last_reset_date = today
            
            # Reset scaling manager daily tracking
            if self.scaling_manager:
                self.scaling_manager.reset_daily(current_equity)
            
            # Reset risk manager daily risk accumulator
            if hasattr(self, 'risk_manager') and self.risk_manager:
                self.risk_manager.reset_daily_risk()
                logger.info("Risk manager daily risk reset")
            
            # Check for weekly reset (Monday) - weekly consolidation happens on Sunday
            if today.weekday() == 0:  # Monday
                logger.info("New trading week - resetting weekly counters")
                if self.scaling_manager:
                    self.scaling_manager.reset_weekly(current_equity)
            
            # Weekly consolidation on Sunday (day before week reset)
            if today.weekday() == 6:  # Sunday
                logger.info("Sunday - running weekly learning consolidation")
                if self.learning_service and self.claude_client and self.claude_client.api_key:
                    try:
                        await self.learning_service.consolidate_weekly(self.claude_client)
                        logger.info("Weekly learning consolidation completed")
                    except Exception as e:
                        logger.error(f"Weekly consolidation failed: {e}")
                        
                # Also prune expired knowledge
                if self.learning_service:
                    try:
                        pruned = await self.learning_service.prune_expired_knowledge()
                        if pruned > 0:
                            logger.info(f"Pruned {pruned} expired knowledge entries")
                    except Exception as e:
                        logger.warning(f"Knowledge pruning failed: {e}")
            
            # Also cleanup old signal hashes
            self._cleanup_expired_signal_hashes()
    
    async def _send_daily_summary(self):
        """Send end-of-day summary notification."""
        try:
            account = await self.mt5_client.get_account_info() if self.mt5_client else None
            
            # Calculate win rate from scaling manager
            win_rate = 0.5
            if self.scaling_manager:
                perf = self.scaling_manager.get_recent_performance()
                win_rate = perf.get('win_rate', 50) / 100
            
            await notify(
                NotificationType.DAILY_SUMMARY,
                "Daily Summary",
                date=self.last_reset_date.strftime('%Y-%m-%d'),
                trades_opened=self.daily_trades,
                trades_closed=self.daily_trades,  # Simplified
                total_pnl=self.daily_pnl,
                win_rate=win_rate,
                balance=account.balance if account else 0,
                equity=account.equity if account else 0
            )
            logger.info("Daily summary sent")
        except Exception as e:
            logger.error(f"Error sending daily summary: {e}")
    
    def _get_current_exposure_lots(self) -> float:
        """Get total lots currently open across all positions."""
        if not self.position_manager:
            return 0.0
        
        total_lots = sum(p.volume for p in self.position_manager.positions.values())
        return total_lots
    
    async def _check_milestone_notification(self, current_equity: float):
        """Check if we hit a new milestone and send notification."""
        if not self.goal_tracker:
            return
        
        milestones = [2500, 5000, 10000, 25000, 50000, 75000, 100000]
        
        for milestone in milestones:
            # Check if we just crossed this milestone
            if current_equity >= milestone:
                # Check if we haven't already notified for this milestone
                milestone_key = f"milestone_{milestone}"
                if not hasattr(self, '_notified_milestones'):
                    self._notified_milestones = set()
                
                if milestone_key not in self._notified_milestones:
                    self._notified_milestones.add(milestone_key)
                    
                    progress = self.goal_tracker.calculate_progress(current_equity)
                    
                    await notify(
                        NotificationType.INFO,
                        f"🎉 <b>MILESTONE REACHED!</b>\n\n"
                        f"💰 Equity: ${current_equity:,.2f}\n"
                        f"🎯 Milestone: ${milestone:,}\n"
                        f"📊 Progress: {progress.get('percent', 0):.1f}% to $100K\n\n"
                        f"Keep going! 🚀"
                    )
                    logger.info(f"🎉 Milestone ${milestone:,} reached!")
                    break  # Only notify for one milestone at a time
    
    async def _notify_error(self, error_message: str, context: str = None):
        """Send error notification via Telegram."""
        try:
            await notify(
                NotificationType.ERROR,
                error_message,
                context=context
            )
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")
    
    async def shutdown(self):
        """Gracefully shutdown the bot."""
        logger.info("Shutting down trading bot...")
        self.running = False
        
        # Cancel position management loop if still running
        if hasattr(self, '_position_mgr_task') and self._position_mgr_task and not self._position_mgr_task.done():
            self._position_mgr_task.cancel()
            try:
                await self._position_mgr_task
            except asyncio.CancelledError:
                pass
            logger.info("Position management loop stopped during shutdown")
        
        # Save state before shutdown
        logger.info("Saving bot state...")
        if save_full_state(self):
            logger.info("✅ Bot state saved successfully")
        else:
            logger.warning("⚠️ Failed to save bot state")
        
        # Send shutdown notification
        try:
            await notify(
                NotificationType.INFO,
                "🛑 ICT Trading Bot shutting down...\nState has been saved."
            )
        except:
            pass
        
        if self.mt5_client:
            await self.mt5_client.disconnect()
        
        logger.info("Trading bot shutdown complete")
    
    def stop(self):
        """Signal the bot to stop."""
        self.running = False
        # Try to save state synchronously
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._async_save_state())
            else:
                save_full_state(self)
        except:
            save_full_state(self)
    
    async def _async_save_state(self):
        """Async helper to save state."""
        save_full_state(self)


def signal_handler(bot: TradingBot):
    """Create a signal handler for graceful shutdown."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating shutdown...")
        bot.stop()
    return handler


async def main():
    """Main entry point."""
    # Setup logging
    setup_logging()
    
    logger.info("=" * 60)
    logger.info("ICT Trading Bot v1.0.0")
    logger.info("=" * 60)
    
    # Create and run bot
    bot = TradingBot()
    
    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler(bot))
    signal.signal(signal.SIGTERM, signal_handler(bot))
    
    # Run the bot
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
