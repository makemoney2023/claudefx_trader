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
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import numpy as np

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
from .api.websocket import broadcast_trade_update, broadcast_analysis_update

# Import bot state for activity tracking
try:
    from .api.routes.bot_status import get_bot_state
    bot_state = get_bot_state()
except ImportError:
    bot_state = None

# Import database for trade persistence
try:
    from .api.database import async_session, TradeModel, AnalysisLogModel
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
    reasoning: str = "",
    # Judge analysis
    judge_verdict: str = None,
    judge_reason: str = None,
    judge_risk_flags: list = None,
    # Trade classification & context
    trade_type: str = None,
    order_type: str = None,
    amd_phase: str = None,
    market_structure: str = None,
    confluence_factors: list = None,
    confluence_count: int = None,
    ict_concepts: dict = None,
    timeframe: str = "M15",
    session_name: str = "",
    risk_percent: float = None,
):
    """Save an executed trade to the database with full analysis context."""
    if not DB_AVAILABLE:
        logger.warning("Database not available - trade not persisted")
        return
    
    try:
        async with async_session() as session:
            trade = TradeModel(
                trade_id=str(ticket),
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                direction=direction,
                timeframe=timeframe,
                session=session_name,
                entry_price=entry_price,
                entry_time=datetime.now(timezone.utc),
                entry_reason=reasoning[:1000] if reasoning else f"ICT Signal - Confidence: {confidence:.0%}",
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size=position_size,
                risk_amount=0.0,
                claude_confidence=confidence,
                claude_reasoning=reasoning[:5000] if reasoning else "",
                # Judge analysis
                judge_verdict=judge_verdict,
                judge_reason=judge_reason[:1000] if judge_reason else None,
                judge_risk_flags=judge_risk_flags,
                # Trade classification
                trade_type=trade_type,
                order_type=order_type,
                amd_phase=amd_phase,
                market_structure=market_structure or "",
                ict_concepts=ict_concepts,
                confluence_factors=confluence_factors,
                confluence_count=confluence_count,
                risk_percent=risk_percent,
            )
            session.add(trade)
            await session.commit()
            logger.info(f"Trade {ticket} saved to database (judge={judge_verdict}, type={trade_type}, confluence={confluence_count})")
    except Exception as e:
        try:
            await session.rollback()
        except Exception:
            pass
        logger.error(f"Failed to save trade to database: {e}")


async def save_signal_to_db(
    symbol: str,
    direction: str,
    confidence: float,
    entry_price: float = None,
    stop_loss: float = None,
    take_profit: float = None,
    reasoning: str = "",
    # Judge analysis
    judge_verdict: str = None,
    judge_reason: str = None,
    judge_risk_flags: list = None,
    # Context
    trade_type: str = None,
    market_structure: str = None,
    confluence_factors: list = None,
    confluence_count: int = None,
    trade_id: int = None,
):
    """Save every signal (approved, demoted, rejected) to analysis_logs for correlation analysis."""
    if not DB_AVAILABLE:
        return
    
    try:
        async with async_session() as session:
            log = AnalysisLogModel(
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                timeframe="M15",
                session="",
                market_structure=market_structure or "",
                trend="",
                signal_direction=direction,
                confidence=confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                analysis_data=None,
                reasoning=reasoning[:2000] if reasoning else "",
                warnings=None,
                judge_verdict=judge_verdict,
                judge_reason=judge_reason[:1000] if judge_reason else None,
                judge_risk_flags=judge_risk_flags,
                confluence_factors=confluence_factors,
                confluence_count=confluence_count,
                trade_type=trade_type,
                trade_id=trade_id,
            )
            session.add(log)
            await session.commit()
            logger.debug(f"Signal logged to DB: {symbol} {direction} — judge={judge_verdict}")
    except Exception as e:
        try:
            await session.rollback()
        except Exception:
            pass
        logger.warning(f"Failed to save signal to analysis_logs: {e}")


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
        self.regime_classifier = None
        
        # Playbook cache (rebuilt daily)
        self._playbook_cache = None
        self._playbook_cache_time = None
        
        # Trading state
        self.win_streak = 0
        self.loss_streak = 0
        
        # Track daily statistics
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now(timezone.utc).date()
        
        # Gap 20: Thread-safe trade execution
        self._trade_lock = asyncio.Lock()
        
        # Gap 21: Duplicate signal prevention
        self._recent_signal_hashes: set = set()
        self._signal_hash_expiry: Dict[str, datetime] = {}
        
        # Position re-eval throttle: {ticket: {"decision": str, "time": datetime}}
        # HOLD decisions trigger a cooldown before next re-eval (saves API calls, reduces log spam)
        self._position_reeval_state: Dict[int, Dict] = {}
        
        # Cache MTF results per symbol for position re-evaluation context
        self._last_mtf_results: Dict[str, Dict] = {}
        
        # Cycle-to-cycle signal memory (per symbol) for reactive context
        self._last_signal_per_symbol: Dict[str, Dict[str, Any]] = {}
        
        # Direction-flip cooldown tracking
        self._last_signal_direction: Dict[str, tuple] = {}  # symbol -> (direction, datetime)
        
        # Post-loss cooldown: prevent revenge trading by blocking re-entry
        # for 30 minutes after a stop-loss exit on the same symbol
        self._symbol_loss_cooldowns: Dict[str, datetime] = {}  # symbol -> cooldown_expires_at
        self._off_hours_mode: bool = False  # True when outside kill zones (soft-block)
        
        # Analysis cooldown: only call Claude once per 5 minutes per symbol
        # Saves API costs by skipping redundant analyses on unchanged chart data
        self._last_analysis_time: Dict[str, datetime] = {}  # symbol -> last analysis datetime
        self._analysis_cooldown_seconds: int = 300  # 5 minutes
        
        # Dynamic learnings: throttle doc updates to at most once per hour
        self._last_learnings_update: Optional[datetime] = None
        
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
        
        # Index symbols
        self.INDEX_SYMBOLS = [
            'US30', 'NAS100', 'US500', 'DJ30', 'USTEC', 'SP500',
            'US30CASH', 'NAS100CASH', 'US500CASH',
        ]
        
        # Oil / Energy symbols
        self.OIL_SYMBOLS = [
            'USOIL', 'WTIUSD', 'XTIUSD', 'BRENT', 'UKOIL', 'XBRUSD',
        ]
        
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
                target_equity=10000.0
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
                target_equity=10000.0,
                max_daily_drawdown=settings.trading.max_daily_drawdown,  # 3% from config
                max_weekly_drawdown=settings.trading.max_weekly_drawdown,  # 6% from config
            )
            # Demo account: AGGRESSIVE mode for maximum data collection
            # AGGRESSIVE LOCK prevents performance rules from downgrading;
            # only drawdown protections can override
            from .services.scaling_manager import TradingMode
            self.scaling_manager.current_mode = TradingMode.AGGRESSIVE
            logger.info("Scaling mode set to AGGRESSIVE (demo data collection)")
            from .api.routes.activity import add_activity
            add_activity("mode_change", "Trading mode set to AGGRESSIVE (init)", details={"mode": "AGGRESSIVE", "reason": "demo data collection"})
            
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
                max_concurrent_positions=3,  # Reduced for capital preservation on small accounts
                max_exposure_percent=0.30  # 30% of equity as max margin exposure
            )
            
            # Pending Order Manager - track and manage pending orders
            logger.info("Initializing pending order manager...")
            self.pending_order_manager = PendingOrderManager(
                mt5_client=self.mt5_client,
                order_manager=self.order_manager,
                kill_zone_checker=self.kill_zone_checker
            )
            
            # Import any existing MT5 pending orders into the tracker
            # so they survive restarts and can be re-evaluated by Claude
            try:
                import_result = await self.pending_order_manager.import_from_mt5()
                _imported = import_result.get('imported', 0)
                if _imported > 0:
                    print(f"[INIT] Imported {_imported} pending order(s) from MT5 into tracker", flush=True)
                    # Restore original expiration times from persisted state
                    try:
                        persistence = get_persistence()
                        po_meta = persistence.load_pending_order_metadata()
                        if po_meta:
                            _restored_exp = 0
                            for ticket, order in self.pending_order_manager.pending_orders.items():
                                meta = po_meta.get(str(ticket))
                                if meta and meta.get('expiration'):
                                    try:
                                        original_exp = datetime.fromisoformat(meta['expiration'])
                                        if original_exp > datetime.now():
                                            order.expiration = original_exp
                                            _restored_exp += 1
                                    except (ValueError, TypeError):
                                        pass
                            if _restored_exp > 0:
                                print(f"[INIT] Restored original expiration for {_restored_exp} pending order(s)", flush=True)
                    except Exception as e:
                        logger.warning(f"Could not restore pending order metadata: {e}")
                else:
                    print(f"[INIT] No pending orders to import from MT5", flush=True)
            except Exception as e:
                logger.warning(f"Error importing pending orders from MT5: {e}")
            
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
            
            # Regime Classifier - Market regime detection for strategy adaptation
            logger.info("Initializing regime classifier...")
            from .analysis.regime_classifier import RegimeClassifier
            self.regime_classifier = RegimeClassifier()
            
            # MTF Analyzer - Higher timeframe bias confirmation
            logger.info("Initializing MTF analyzer...")
            self.mtf_analyzer = MTFAnalyzer(mt5_client=self.mt5_client)
            
            # Fibonacci Analyzer - OTE zone identification
            logger.info("Initializing Fibonacci analyzer...")
            self.fibonacci_analyzer = FibonacciAnalyzer()
            
            # Firecrawl Intelligence Service (optional — disabled if no credits)
            print("[INIT] Firecrawl intelligence service...", flush=True)
            self.firecrawl_service = None
            self._firecrawl_consecutive_failures = 0
            firecrawl_key = getattr(getattr(settings, 'firecrawl', None), 'api_key', None) or \
                           getattr(settings, 'firecrawl_api_key', None)
            firecrawl_enabled = getattr(getattr(settings, 'firecrawl', None), 'enabled', False)
            if firecrawl_key and firecrawl_enabled:
                refresh_min = getattr(getattr(settings, 'firecrawl', None), 'refresh_minutes', 15)
                self.firecrawl_service = FirecrawlIntelligenceService(
                    api_key=firecrawl_key,
                    refresh_minutes=refresh_min,
                    enabled=True
                )
                logger.info("Firecrawl intelligence service initialized")
                print("[INIT] Firecrawl enabled (will auto-disable after 3 consecutive failures)", flush=True)
            else:
                logger.info("Firecrawl intelligence disabled (no API key or disabled in config)")
                print("[INIT] Firecrawl disabled", flush=True)
            
            # Initialize Telegram notifier
            print("[INIT] Telegram notifier...", flush=True)
            logger.info("Initializing Telegram notifications...")
            notifier = get_notifier()
            print(f"[INIT] Telegram enabled={notifier.enabled}, token={'SET' if notifier.bot_token else 'MISSING'}, chat_id={'SET' if notifier.chat_id else 'MISSING'}", flush=True)
            if notifier.enabled:
                logger.info("✅ Telegram notifications enabled")
                try:
                    symbols_str = ", ".join(settings.trading.symbols) if settings.trading.symbols else "None"
                    sent = await notify(
                        NotificationType.INFO,
                        f"🤖 ICT Trading Bot started!\n\n"
                        f"💰 Equity: ${starting_equity:,.2f}\n"
                        f"🎯 Goal: $10,000\n"
                        f"📊 Symbols: {symbols_str}\n"
                        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    print(f"[INIT] Telegram startup notification sent: {sent}", flush=True)
                except Exception as e:
                    print(f"[INIT] Telegram startup notification FAILED: {e}", flush=True)
            else:
                print("[INIT] Telegram notifications DISABLED (missing credentials)", flush=True)
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
            logger.info(f"Goal: ${starting_equity:.2f} -> $10,000.00")
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
        pending_reeval_cycle = 0
        
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
                            closed_list = sync_result['closed']
                            for cp in closed_list:
                                _sym = getattr(cp, 'symbol', '?')
                                _tkt = getattr(cp, 'ticket', '?')
                                _dir = getattr(cp, 'direction', '?')
                                print(f"[POS-MGR] Position CLOSED externally: #{_tkt} {_sym} {_dir}", flush=True)
                            logger.info(f"POS-MGR: {len(closed_list)} position(s) closed externally")
                            
                            # Trigger immediate trade history sync to update DB
                            try:
                                await self._sync_trade_history(days_back=1)
                                print(f"[POS-MGR] Trade history synced after close detection", flush=True)
                            except Exception as e:
                                logger.warning(f"POS-MGR: Post-close history sync error: {e}")
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
                    if actions:
                        asyncio.create_task(broadcast_trade_update({"event": "position_actions", "actions": actions}))
                    
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
                    
                
                # Pending order re-evaluation every 12 cycles (~2 minutes)
                # Runs regardless of whether there are open positions
                pending_reeval_cycle += 1
                if pending_reeval_cycle >= 12:
                    pending_reeval_cycle = 0
                    if not getattr(self, '_pending_reeval_running', False):
                        self._pending_reeval_running = True
                        async def _run_pending_reeval():
                            try:
                                await self._claude_reevaluate_pending_orders()
                            except Exception as e:
                                logger.warning(f"POS-MGR Pending order re-eval error: {e}")
                            finally:
                                self._pending_reeval_running = False
                        asyncio.create_task(_run_pending_reeval())
                    else:
                        logger.debug("POS-MGR: Skipping pending re-eval (previous still running)")
                
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
                if not hasattr(self, '_last_firecrawl_refresh'):
                    self._last_firecrawl_refresh = datetime.min
                
                time_since_refresh = (datetime.now() - self._last_firecrawl_refresh).total_seconds() / 60
                refresh_min = getattr(getattr(settings, 'firecrawl', None), 'refresh_minutes', 15)
                
                if time_since_refresh >= refresh_min:
                    try:
                        await self.firecrawl_service.refresh_all(cycle_symbols)
                        self._last_firecrawl_refresh = datetime.now()
                        self._firecrawl_consecutive_failures = 0
                        logger.info("Firecrawl intelligence refreshed")
                    except Exception as e:
                        self._firecrawl_consecutive_failures += 1
                        if self._firecrawl_consecutive_failures >= 3:
                            print(
                                f"[FIRECRAWL] Auto-disabled after {self._firecrawl_consecutive_failures} "
                                f"consecutive failures (likely credits exhausted)",
                                flush=True
                            )
                            logger.warning(f"Firecrawl auto-disabled after {self._firecrawl_consecutive_failures} failures: {e}")
                            self.firecrawl_service = None
                        else:
                            logger.warning(f"Firecrawl refresh failed ({self._firecrawl_consecutive_failures}/3): {e}")
            
            # ============================================
            # STEP 0E: UPDATE DYNAMIC CORRELATIONS
            # ============================================
            if self.correlation_service:
                try:
                    _corr_data = {}
                    for _corr_sym in cycle_symbols:
                        _corr_df = await self.data_fetcher.get_ohlcv(
                            symbol=_corr_sym, timeframe='D1', count=25
                        )
                        if _corr_df is not None and not _corr_df.empty:
                            _corr_data[_corr_sym] = _corr_df
                    if len(_corr_data) >= 2:
                        self.correlation_service.update_dynamic_correlations(_corr_data)
                        _port_risk = self.correlation_service.get_portfolio_risk_score()
                        if _port_risk > 0.5:
                            logger.warning(f"[CORR] Portfolio risk score: {_port_risk:.2f} (elevated)")
                except Exception as _corr_err:
                    logger.debug(f"[CORR] Dynamic correlation update error: {_corr_err}")
            
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
            
            self._day_of_week_mode_locked = False  # Reset each cycle
            if _weekday == 0:  # Monday - manipulation day
                if self.scaling_manager:
                    _prev_mode = self.scaling_manager.current_mode
                    self.scaling_manager.current_mode = TradingMode.CONSERVATIVE
                    self._day_of_week_mode_locked = True
                    logger.info("Monday: CONSERVATIVE mode (manipulation day, A+ setups only)")
                    if self.scaling_manager.current_mode != _prev_mode:
                        from .api.routes.activity import add_activity
                        add_activity("mode_change", f"Trading mode changed to CONSERVATIVE (Monday manipulation day)", details={"mode": "CONSERVATIVE", "previous": _prev_mode.value, "reason": "monday"})
            elif _weekday == 4:  # Friday - profit-taking day
                if self.scaling_manager:
                    _prev_mode = self.scaling_manager.current_mode
                    self.scaling_manager.current_mode = TradingMode.CONSERVATIVE
                    self._day_of_week_mode_locked = True
                    logger.info("Friday: CONSERVATIVE mode (profit-taking day)")
                    if self.scaling_manager.current_mode != _prev_mode:
                        from .api.routes.activity import add_activity
                        add_activity("mode_change", f"Trading mode changed to CONSERVATIVE (Friday profit-taking day)", details={"mode": "CONSERVATIVE", "previous": _prev_mode.value, "reason": "friday"})
            else:
                # Tuesday-Thursday and weekends (crypto trading) - use normal mode determination
                if self.scaling_manager:
                    try:
                        account = await self.mt5_client.get_account_info()
                        if account:
                            _prev_mode = self.scaling_manager.current_mode
                            mode = self.scaling_manager.determine_mode(account.balance)
                            self.scaling_manager.current_mode = mode
                            if self.scaling_manager.current_mode != _prev_mode:
                                from .api.routes.activity import add_activity
                                add_activity("mode_change", f"Trading mode changed to {mode.value}", details={"mode": mode.value, "previous": _prev_mode.value, "reason": "performance_based"})
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
                    from .api.routes.activity import add_activity
                    add_activity("news_blackout", f"News blackout active: {reason}", details={"reason": reason})
                    # During news blackout, only allow crypto (unaffected by forex news)
                    cycle_symbols = [s for s in cycle_symbols if s in self.CRYPTO_SYMBOLS]
                    if not cycle_symbols:
                        print("[CYCLE] BLOCKED: News blackout and no crypto symbols", flush=True)
                        return
            
            # ============================================
            # STEP 2b: FRIDAY PRE-CLOSE (Weekend Gap Protection)
            # ============================================
            import pytz
            est_tz = pytz.timezone('US/Eastern')
            now_est = datetime.now(est_tz)
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
                                    pos.close_reason = 'weekend_protection'
                                    await self._handle_position_close(pos)
                                    self.position_manager.remove_position(ticket)
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
                if settings.trading.crypto_kill_zone_only:
                    print(f"[CYCLE] OFF-HOURS ({session.session_name}): soft-block active, analysis continues with caps", flush=True)
                    logger.info(f"Outside kill zone ({session.session_name}) — soft-block: confidence capped, R:R raised")
                    self._off_hours_mode = True
                else:
                    crypto_in_cycle = [s for s in cycle_symbols if s in self.CRYPTO_SYMBOLS]
                    if crypto_in_cycle:
                        logger.info(
                            f"Outside kill zone ({session.session_name}) - "
                            f"forex blocked, {len(crypto_in_cycle)} crypto symbols still active"
                        )
                        cycle_symbols = crypto_in_cycle
                        self._off_hours_mode = True
                    else:
                        print(f"[CYCLE] BLOCKED: Outside kill zone ({session.session_name}), no crypto to trade", flush=True)
                        logger.debug(f"Outside valid trading session ({session.session_name}), skipping cycle")
                        return
            else:
                self._off_hours_mode = False
            
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
                        
                        # ANALYSIS COOLDOWN: Only call Claude once per 5 minutes per symbol
                        last_run = self._last_analysis_time.get(sym)
                        now = datetime.now()
                        if last_run is not None:
                            elapsed = (now - last_run).total_seconds()
                            if elapsed < self._analysis_cooldown_seconds:
                                return
                        self._last_analysis_time[sym] = now
                        
                        print(f"[CYCLE] Analyzing {sym} (crypto={is_crypto})...", flush=True)
                        # Per-symbol timeout: 120s max per analysis to prevent one slow symbol
                        # from blocking the entire batch
                        await asyncio.wait_for(
                            self._analyze_and_trade(sym, is_crypto=is_crypto),
                            timeout=120.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"Analysis of {sym} TIMED OUT after 120s - skipping")
                    except Exception as e:
                        logger.error(f"Error analyzing {sym}: {e}")
                
                await asyncio.gather(*[_process_symbol(sym) for sym in batch])
                # Yield control to event loop between batches so API can serve requests
                await asyncio.sleep(0.1)
            
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
            
            # POST-LOSS COOLDOWN: Prevent revenge trading
            cooldown_expiry = self._symbol_loss_cooldowns.get(symbol)
            if cooldown_expiry:
                if datetime.now() < cooldown_expiry:
                    remaining = (cooldown_expiry - datetime.now()).total_seconds() / 60
                    logger.info(f"[LOSS-COOLDOWN] {symbol}: Skipping — {remaining:.0f}min cooldown remaining")
                    if bot_state:
                        bot_state.symbol_complete(symbol, "loss_cooldown")
                    from .api.routes.activity import add_activity
                    add_activity("symbol_cooldown_skip", f"{symbol}: Skipped — {remaining:.0f}min loss cooldown remaining", symbol=symbol, details={"remaining_minutes": round(remaining)})
                    return
                else:
                    # Cooldown expired — flag this symbol for higher confidence bar
                    del self._symbol_loss_cooldowns[symbol]
                    if not hasattr(self, '_post_cooldown_symbols'):
                        self._post_cooldown_symbols = set()
                    self._post_cooldown_symbols.add(symbol)
            
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
            
            # Generate initial chart (will be regenerated with overlays after analysis)
            chart_base64 = await self._generate_chart_image(df, symbol)
            if not chart_base64:
                logger.warning(f"Failed to generate chart for {symbol}")
                return
            
            # Fetch multi-timeframe data for composite chart and LTF analysis
            _mtf_dfs = {}  # {timeframe: DataFrame}
            additional_charts = []
            try:
                for _ctf, _ctf_candles in [('D1', 60), ('H1', 100), ('M5', 100), ('M1', 100)]:
                    _ctf_df = await self.data_fetcher.get_ohlcv(
                        symbol=symbol, timeframe=_ctf, count=_ctf_candles
                    )
                    if _ctf_df is not None and not _ctf_df.empty:
                        _mtf_dfs[_ctf] = _ctf_df
                
                # Fetch last 5 trades for trade markers on M15 chart
                _trade_markers = []
                try:
                    if DB_AVAILABLE:
                        from .api.database import async_session_maker, TradeModel as _TM
                        async with async_session_maker() as _tm_sess:
                            from sqlalchemy import select, desc
                            _tm_q = select(_TM).where(
                                _TM.symbol == symbol
                            ).order_by(desc(_TM.timestamp)).limit(5)
                            _tm_rows = (await _tm_sess.execute(_tm_q)).scalars().all()
                            for _t in _tm_rows:
                                _trade_markers.append({
                                    'time': _t.entry_time or _t.timestamp,
                                    'price': _t.entry_price,
                                    'direction': _t.direction,
                                    'outcome': 'win' if (_t.profit_loss or 0) > 0 else 'loss',
                                    'label': f"{'+' if (_t.r_multiple or 0) >= 0 else ''}{(_t.r_multiple or 0):.1f}R"
                                })
                        if _trade_markers:
                            logger.info(f"[MARKERS] {symbol}: {len(_trade_markers)} trade markers for chart")
                except Exception as _tm_err:
                    logger.debug(f"[MARKERS] Could not fetch trade markers for {symbol}: {_tm_err}")
                
                # Fetch reactive levels for M15 chart heatmap
                _reactive_levels = []
                try:
                    if self.learning_service:
                        _reactive_levels = await self.learning_service.get_reactive_levels(symbol, lookback_days=90)
                        if _reactive_levels:
                            logger.info(f"[REACTIVE] {symbol}: {len(_reactive_levels)} reactive levels found")
                except Exception as _rl_err:
                    logger.debug(f"[REACTIVE] Error fetching reactive levels for {symbol}: {_rl_err}")
                
                # Compute volume profile for M15 chart
                _vp_data = None
                try:
                    from .analysis.volume_profile import compute_volume_profile
                    _vp_data = compute_volume_profile(df, num_bins=50)
                    if _vp_data:
                        market_data["volume_profile_levels"] = {
                            'poc': _vp_data['poc'],
                            'vah': _vp_data['vah'],
                            'val': _vp_data['val'],
                        }
                        logger.info(
                            f"[VP] {symbol}: POC={_vp_data['poc']:.5f}, "
                            f"VAH={_vp_data['vah']:.5f}, VAL={_vp_data['val']:.5f}"
                        )
                except Exception as _vp_err:
                    logger.debug(f"[VP] Volume profile error for {symbol}: {_vp_err}")

                # Detect bar extreme supply/demand zones on available timeframes
                _bar_extreme_zones = []
                try:
                    from .analysis.bar_extreme_zones import BarExtremeZoneDetector
                    _be_detector = BarExtremeZoneDetector()
                    _current_price = float(df['close'].iloc[-1])
                    for _be_tf, _be_df in [('D1', _mtf_dfs.get('D1')), ('H1', _mtf_dfs.get('H1')), ('M15', df), ('M5', _mtf_dfs.get('M5'))]:
                        if _be_df is not None and len(_be_df) > 20:
                            _be_result = _be_detector.detect(_be_df, _current_price, _be_tf)
                            market_data[f"bar_extreme_{_be_tf.lower()}"] = _be_result.to_dict()
                            if _be_result.supply_zone:
                                _bar_extreme_zones.append({"top": _be_result.supply_zone.top, "bottom": _be_result.supply_zone.bottom, "type": "supply", "tf": _be_tf})
                            if _be_result.demand_zone:
                                _bar_extreme_zones.append({"top": _be_result.demand_zone.top, "bottom": _be_result.demand_zone.bottom, "type": "demand", "tf": _be_tf})
                    if _bar_extreme_zones:
                        logger.info(f"[BAR_EXTREME] {symbol}: {len(_bar_extreme_zones)} zones across {len(set(z['tf'] for z in _bar_extreme_zones))} timeframes")
                except Exception as _be_err:
                    logger.debug(f"[BAR_EXTREME] Error for {symbol}: {_be_err}")

                # Generate composite chart (D1, H1, M15, M5) as primary image
                _composite_base64 = None
                try:
                    from .utils.chart_screenshot import create_composite_chart
                    _composite_panels = []
                    for _panel_tf, _panel_df in [
                        ('D1', _mtf_dfs.get('D1')),
                        ('H1', _mtf_dfs.get('H1')),
                        ('M15', df),
                        ('M5', _mtf_dfs.get('M5'))
                    ]:
                        if _panel_df is not None and not _panel_df.empty:
                            _composite_panels.append({
                                'timeframe': _panel_tf,
                                'df': _panel_df,
                                'overlays': {}
                            })
                    if len(_composite_panels) >= 2:
                        _comp_kwargs = {}
                        if _trade_markers:
                            _comp_kwargs['trade_markers'] = _trade_markers
                        if _vp_data:
                            _comp_kwargs['volume_profile'] = _vp_data
                        if _reactive_levels:
                            _comp_kwargs['reactive_levels'] = _reactive_levels
                        if _bar_extreme_zones:
                            _comp_kwargs['bar_extreme_zones'] = _bar_extreme_zones
                        _composite_base64 = await asyncio.to_thread(
                            create_composite_chart, _composite_panels, symbol,
                            **_comp_kwargs
                        )
                        if _composite_base64:
                            logger.info(f"[COMPOSITE] {symbol}: Generated {len(_composite_panels)}-panel composite chart")
                except Exception as _comp_err:
                    logger.warning(f"[COMPOSITE] {symbol}: Failed to generate composite: {_comp_err}")
                
                # Generate individual M5/M1 charts for precision entry analysis
                for ltf in ['M5', 'M1']:
                    ltf_df = _mtf_dfs.get(ltf)
                    if ltf_df is not None and not ltf_df.empty:
                        ltf_chart = await self._generate_chart_image(ltf_df, symbol, timeframe=ltf)
                        if ltf_chart:
                            additional_charts.append({'base64': ltf_chart, 'timeframe': ltf})
                
                # Prepend composite chart as the first additional chart if available
                if _composite_base64:
                    additional_charts.insert(0, {
                        'base64': _composite_base64,
                        'timeframe': 'COMPOSITE (D1/H1/M15/M5)'
                    })
                
                if additional_charts:
                    logger.info(f"Sending {len(additional_charts)} charts for {symbol} (composite + LTF)")
            except Exception as e:
                logger.warning(f"Failed to generate multi-TF charts for {symbol}: {e}")
            
            # Build strategy context
            strategy_context = self.context_builder.get_ict_context()
            
            # Get account info for enhanced context
            account_info = await self.mt5_client.get_account_info()
            current_equity = account_info.equity if account_info else 1000.0
            
            # Prepare market data for Claude (ENHANCED with all integrated services)
            from .config import get_symbol_spec as _gss
            _sym_spec = _gss(symbol)
            # Fetch real bid/ask/spread from MT5
            _real_bid = current_price
            _real_ask = current_price
            _real_spread = 0.0
            _spread_pct = 0.0
            try:
                _sym_info = await self.mt5_client.get_symbol_info(symbol)
                if _sym_info and getattr(_sym_info, 'ask', 0) > 0:
                    _real_bid = _sym_info.bid
                    _real_ask = _sym_info.ask
                    _real_spread = _real_ask - _real_bid
                    _spread_pct = _real_spread / ((_real_ask + _real_bid) / 2) if (_real_ask + _real_bid) > 0 else 0
            except Exception:
                pass
            
            market_data = {
                "current_price": current_price,
                "bid": _real_bid,
                "ask": _real_ask,
                "spread": round(_real_spread, 6),
                "spread_pct": f"{_spread_pct:.4%}",
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
            
            # Add ATR context for Claude's SL placement awareness
            try:
                from .utils.candle_utils import calculate_atr as _calc_atr_ctx
                _atr_ctx = _calc_atr_ctx(df, period=14)
                _atr_current = float(_atr_ctx.iloc[-1]) if not _atr_ctx.empty and not np.isnan(_atr_ctx.iloc[-1]) else None
                if _atr_current:
                    market_data["atr_14"] = round(_atr_current, 6)
                    market_data["atr_min_sl"] = round(_atr_current * 1.5, 6)
            except Exception:
                pass
            
            # Add market regime classification
            if self.regime_classifier:
                try:
                    _amd_phase = 'unknown'
                    if 'amd_cycle' in analysis_results:
                        _amd_phase = analysis_results['amd_cycle'].get('phase', 'unknown')
                    _regime_result = self.regime_classifier.classify(df, market_phase=_amd_phase)
                    if _regime_result:
                        market_data["regime"] = _regime_result.to_dict()
                        logger.info(
                            f"[REGIME] {symbol}: {_regime_result.regime.value} "
                            f"(ADX={_regime_result.adx:.1f}, vol={_regime_result.volatility_ratio:.2f}x)"
                        )
                except Exception as _reg_err:
                    logger.debug(f"[REGIME] Classification error for {symbol}: {_reg_err}")
            
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
            
            # Add MFE/MAE excursion data for SL/TP validation
            try:
                from .analysis.excursion_analysis import ExcursionAnalyzer
                _excursion = ExcursionAnalyzer()
                _exc_result = await _excursion.compute(symbol, direction='all', lookback_days=90)
                if _exc_result and _exc_result.sample_size >= 5:
                    market_data["excursion_data"] = _exc_result.to_dict()
                    logger.debug(f"Added MFE/MAE data for {symbol}: opt_SL={_exc_result.optimal_sl:.5f}, opt_TP={_exc_result.optimal_tp:.5f}")
            except Exception as _exc_err:
                logger.debug(f"Could not compute excursion data for {symbol}: {_exc_err}")
            
            # Add setup playbook from historical trade data
            if self.learning_service:
                try:
                    if not hasattr(self, '_playbook_cache') or not self._playbook_cache:
                        self._playbook_cache = await self.learning_service.build_setup_playbook()
                        self._playbook_cache_time = datetime.now()
                    elif hasattr(self, '_playbook_cache_time') and (datetime.now() - self._playbook_cache_time).total_seconds() > 86400:
                        self._playbook_cache = await self.learning_service.build_setup_playbook()
                        self._playbook_cache_time = datetime.now()
                    if self._playbook_cache:
                        market_data["setup_playbook"] = self._playbook_cache
                except Exception as e:
                    logger.debug(f"Could not build setup playbook: {e}")
            
            # Add precious metals context for gold/silver
            if symbol in self.PRECIOUS_METALS and self.precious_metals_analyzer:
                try:
                    # Get prices for both metals
                    gold_price = market_data.get('current_price', 0) if symbol == 'XAUUSD' else 0
                    silver_price = market_data.get('current_price', 0) if symbol == 'XAGUSD' else 0
                    
                    # Try to get the other metal's price
                    other_symbol = 'XAGUSD' if symbol == 'XAUUSD' else 'XAUUSD'
                    other_data = await self.data_fetcher.get_ohlcv(other_symbol, settings.timeframes.execution_tf)
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
            
            # Add MTF context to market_data for Claude (and cache for position re-eval)
            if mtf_result:
                market_data["htf_bias"] = mtf_result.overall_bias.value
                market_data["htf_alignment"] = mtf_result.alignment
                market_data["htf_can_trade_long"] = mtf_result.can_trade_long
                market_data["htf_can_trade_short"] = mtf_result.can_trade_short
                self._last_mtf_results[symbol] = {
                    "d1_bias": mtf_result.daily_analysis.bias.value if mtf_result.daily_analysis else "unknown",
                    "h4_bias": mtf_result.h4_analysis.bias.value if mtf_result.h4_analysis else "unknown",
                    "alignment": mtf_result.alignment,
                }
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
            
            # Regenerate M15 chart WITH ICT overlays now that analysis is complete
            print(f"[CHART-DEBUG] {symbol}: ob_obj={type(ob_obj).__name__ if ob_obj else None}, fvg_obj={type(fvg_obj).__name__ if fvg_obj else None}, liq_obj={type(liq_obj).__name__ if liq_obj else None}, ms_obj={type(ms_obj).__name__ if ms_obj else None}", flush=True)
            try:
                _chart_obs = []
                _chart_fvgs = []
                _chart_liq = []
                _chart_swings = []
                if ob_obj:
                    for ob in (getattr(ob_obj, 'bullish_obs', []) or [])[-5:]:
                        _chart_obs.append({"top": float(ob.high), "bottom": float(ob.low), "type": "bullish"})
                    for ob in (getattr(ob_obj, 'bearish_obs', []) or [])[-5:]:
                        _chart_obs.append({"top": float(ob.high), "bottom": float(ob.low), "type": "bearish"})
                if fvg_obj:
                    for f in (getattr(fvg_obj, 'bullish_fvgs', []) or [])[-5:]:
                        _chart_fvgs.append({"top": float(f.top), "bottom": float(f.bottom), "type": "bullish"})
                    for f in (getattr(fvg_obj, 'bearish_fvgs', []) or [])[-5:]:
                        _chart_fvgs.append({"top": float(f.top), "bottom": float(f.bottom), "type": "bearish"})
                if liq_obj:
                    for p in (getattr(liq_obj, 'bsl_pools', []) or [])[-5:]:
                        _price = float(p.price) if hasattr(p, 'price') else float(p)
                        _chart_liq.append({"price": _price, "label": "BSL", "color": "purple"})
                    for p in (getattr(liq_obj, 'ssl_pools', []) or [])[-5:]:
                        _price = float(p.price) if hasattr(p, 'price') else float(p)
                        _chart_liq.append({"price": _price, "label": "SSL", "color": "purple"})
                if ms_obj:
                    for sh in (getattr(ms_obj, 'swing_highs', []) or [])[-8:]:
                        _p = float(sh.price) if hasattr(sh, 'price') else float(sh)
                        _idx = getattr(sh, 'index', None) or getattr(sh, 'bar_index', None)
                        _chart_swings.append({"price": _p, "type": "high", "index": _idx})
                    for sl_pt in (getattr(ms_obj, 'swing_lows', []) or [])[-8:]:
                        _p = float(sl_pt.price) if hasattr(sl_pt, 'price') else float(sl_pt)
                        _idx = getattr(sl_pt, 'index', None) or getattr(sl_pt, 'bar_index', None)
                        _chart_swings.append({"price": _p, "type": "low", "index": _idx})
                if _chart_obs or _chart_fvgs or _chart_liq or _chart_swings:
                    _enhanced_chart = await self._generate_chart_image(
                        df, symbol,
                        order_blocks=_chart_obs if _chart_obs else None,
                        fvg_zones=_chart_fvgs if _chart_fvgs else None,
                        liquidity_levels=_chart_liq if _chart_liq else None,
                        swing_points=_chart_swings if _chart_swings else None,
                    )
                    if _enhanced_chart:
                        chart_base64 = _enhanced_chart
                        print(f"[CHART] {symbol}: Enhanced M15 chart with {len(_chart_obs)} OBs, {len(_chart_fvgs)} FVGs, {len(_chart_liq)} liq levels, {len(_chart_swings)} swings", flush=True)
                else:
                    print(f"[CHART] {symbol}: No ICT overlays found (OBs={len(_chart_obs)}, FVGs={len(_chart_fvgs)}, liq={len(_chart_liq)}, swings={len(_chart_swings)})", flush=True)
            except Exception as overlay_err:
                print(f"[CHART] {symbol}: Overlay error: {overlay_err}", flush=True)
            
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
                "reasoning": trade_signal.reasoning or "",
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
            asyncio.create_task(broadcast_analysis_update(symbol, {
                "direction": trade_signal.direction,
                "confidence": trade_signal.confidence,
                "rr_ratio": trade_signal.risk_reward,
                "entry_price": trade_signal.entry_price,
                "stop_loss": trade_signal.stop_loss,
                "take_profit": trade_signal.take_profit,
                "market_structure": trade_signal.market_structure
            }))
            
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
            
            # Step 0: Handle SL == Entry (zero risk distance).
            # Claude sometimes outputs SL at the same price as entry.
            # Derive a sensible SL using the key support/resistance level or a default offset.
            if _sl and _entry and abs(_sl - _entry) < _entry * 0.0001:  # Within 0.01%
                _key_levels = getattr(claude_result, 'key_levels', {}) or {}
                _key_s1 = _key_levels.get('support_1')
                _key_r1 = _key_levels.get('resistance_1')
                
                if _dir == 'long':
                    # Use S1 if available and below entry, otherwise use 1% below entry
                    if _key_s1 and _key_s1 < _entry:
                        _sl = _key_s1
                    else:
                        _sl = _entry * 0.99  # 1% below
                elif _dir == 'short':
                    if _key_r1 and _key_r1 > _entry:
                        _sl = _key_r1
                    else:
                        _sl = _entry * 1.01  # 1% above
                
                trade_signal.stop_loss = _sl
                logger.warning(
                    f"SL=ENTRY AUTO-FIX for {symbol} ({_dir}): "
                    f"SL was at entry {_entry}, corrected to {_sl}"
                )
                print(f"[A5-FIX] {symbol}: SL was at entry, corrected to {_sl}", flush=True)
            
            # ── DIRECTION COHERENCE CHECK (A5 safety net) ──────────
            # If SL/TP orientation clearly indicates the opposite direction,
            # flip the direction label instead of swapping levels.
            _direction_flipped = False
            if _sl and _tp and _entry:
                _levels_say_long = (_sl < _entry and _tp > _entry)
                _levels_say_short = (_sl > _entry and _tp < _entry)
                
                if _dir == 'short' and _levels_say_long:
                    logger.warning(
                        f"[A5-FLIP] {symbol}: Levels say LONG (SL={_sl} < Entry={_entry} < TP={_tp}) "
                        f"but direction was SHORT. Flipping to LONG."
                    )
                    print(f"[A5-FLIP] {symbol}: Direction flipped SHORT→LONG (levels indicate LONG)", flush=True)
                    _dir = 'long'
                    trade_signal.direction = 'long'
                    _direction_flipped = True
                elif _dir == 'long' and _levels_say_short:
                    logger.warning(
                        f"[A5-FLIP] {symbol}: Levels say SHORT (TP={_tp} < Entry={_entry} < SL={_sl}) "
                        f"but direction was LONG. Flipping to SHORT."
                    )
                    print(f"[A5-FLIP] {symbol}: Direction flipped LONG→SHORT (levels indicate SHORT)", flush=True)
                    _dir = 'short'
                    trade_signal.direction = 'short'
                    _direction_flipped = True
            
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
                # Re-check after swap (post-swap validation)
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
            
            # After swap, SL may still equal entry — apply the SL=entry fix again
            if _sl and _entry and abs(_sl - _entry) < _entry * 0.0001:
                _key_levels2 = getattr(claude_result, 'key_levels', {}) or {}
                _key_s1_2 = _key_levels2.get('support_1')
                _key_r1_2 = _key_levels2.get('resistance_1')
                
                if _dir == 'long':
                    if _key_s1_2 and _key_s1_2 < _entry:
                        _sl = _key_s1_2
                    else:
                        _sl = _entry * 0.99
                elif _dir == 'short':
                    if _key_r1_2 and _key_r1_2 > _entry:
                        _sl = _key_r1_2
                    else:
                        _sl = _entry * 1.01
                trade_signal.stop_loss = _sl
                sl_wrong = False
                logger.warning(f"SL=ENTRY POST-SWAP FIX for {symbol}: corrected SL to {_sl}")
                print(f"[A5-FIX] {symbol}: Post-swap SL=entry fix, SL now {_sl}", flush=True)
            
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
            # ATR-BASED MINIMUM SL DISTANCE (v3)
            # Ensure SL is at least 1.5x ATR(14) from entry
            # to avoid stop-outs from normal price noise.
            # ============================================
            try:
                from .utils.candle_utils import calculate_atr as _calc_atr
                _atr_series = _calc_atr(df, period=14)
                _atr_val = float(_atr_series.iloc[-1]) if not _atr_series.empty and not np.isnan(_atr_series.iloc[-1]) else None
                if _atr_val and _atr_val > 0 and _sl and _entry:
                    _min_sl_dist = _atr_val * 1.5
                    _current_sl_dist = abs(_entry - _sl)
                    if _current_sl_dist < _min_sl_dist:
                        _old_sl = _sl
                        if _dir == 'long':
                            _sl = _entry - _min_sl_dist
                        else:
                            _sl = _entry + _min_sl_dist
                        trade_signal.stop_loss = _sl
                        _new_tp_dist = abs(_tp - _entry) if _tp else 0
                        _new_rr = _new_tp_dist / _min_sl_dist if _min_sl_dist > 0 else 0
                        logger.info(
                            f"[ATR-SL-ADJUST] {symbol}: SL widened from {_old_sl:.5f} to {_sl:.5f} "
                            f"(ATR={_atr_val:.5f}, min_dist={_min_sl_dist:.5f}, new R:R={_new_rr:.2f})"
                        )
                        print(
                            f"[ATR-SL-ADJUST] {symbol}: SL {_old_sl:.5f} -> {_sl:.5f} "
                            f"(1.5x ATR={_min_sl_dist:.5f}), R:R now {_new_rr:.2f}:1",
                            flush=True
                        )
                        if _new_rr < 1.5:
                            logger.warning(
                                f"[ATR-SL-BLOCK] {symbol}: After ATR SL widen, R:R={_new_rr:.2f} < 1.5. "
                                f"SL too wide for TP target. Blocking trade."
                            )
                            print(
                                f"[ATR-SL-BLOCK] {symbol}: R:R {_new_rr:.2f}:1 after ATR widen — trade blocked.",
                                flush=True
                            )
                            return
            except Exception as _atr_err:
                logger.debug(f"[ATR-SL] Could not apply ATR SL check for {symbol}: {_atr_err}")
            
            # ============================================
            # R:R ENFORCEMENT (A6)
            # Ensure TP distance >= min_rr * SL distance
            # If Claude gives bad R:R, auto-correct the TP
            # ============================================
            # Adjust min R:R based on trade type AND asset category
            # Crypto assets need higher R:R because even minimum lot sizes carry
            # significant dollar risk — a 1.5:1 R:R on ETH/BTC is not worth it.
            _trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
            from .config import get_symbol_spec as _get_spec_rr
            _spec_rr = _get_spec_rr(symbol)
            
            if _spec_rr.category == 'crypto':
                _rr_by_type = {'scalp': 2.0, 'intraday': 2.5, 'swing': 3.5}
            else:
                _rr_by_type = {'scalp': 1.5, 'intraday': 2.0, 'swing': 3.0}
            
            min_rr = _rr_by_type.get(_trade_type, settings.trading.min_risk_reward)
            logger.info(f"R:R threshold for {symbol} ({_trade_type}, {_spec_rr.category}): {min_rr:.1f}:1")
            sl_distance = abs(_entry - _sl)
            tp_distance = abs(_tp - _entry)
            
            if sl_distance > 0:
                actual_rr = tp_distance / sl_distance
            else:
                actual_rr = 0.0
            
            if actual_rr < min_rr and sl_distance > 0:
                # R:R is below minimum. Two tiers:
                # 1) If R:R >= hard floor, let Trade Judge decide.
                # 2) If R:R < hard floor, hard-reject.
                # In AGGRESSIVE mode (demo data collection), lower floor to 1.0
                # to collect more trade outcomes for learning.
                _is_aggressive = (self.scaling_manager and 
                                  self.scaling_manager.current_mode.value == 'aggressive')
                _hard_floor_rr = 1.0 if _is_aggressive else 1.5
                
                if actual_rr < _hard_floor_rr:
                    # Reward < risk — hard reject regardless of setup quality
                    logger.warning(
                        f"[BLOCKED] {symbol}: R:R {actual_rr:.2f}:1 below hard floor {_hard_floor_rr:.1f}:1 "
                        f"(risk ${sl_distance:.2f} > reward ${tp_distance:.2f}). Rejected."
                    )
                    print(
                        f"[BLOCKED] {symbol}: R:R {actual_rr:.2f}:1 — risking ${sl_distance:.2f} "
                        f"for only ${tp_distance:.2f} reward. Not worth it.",
                        flush=True
                    )
                    return
                else:
                    # R:R is between 1.0 and min_rr — borderline.
                    # Let the Trade Judge decide with full context.
                    logger.info(
                        f"[R:R WARNING] {symbol}: R:R {actual_rr:.2f}:1 below target {min_rr:.1f}:1 "
                        f"but above 1.0 floor. Passing to Trade Judge for evaluation."
                    )
                    print(
                        f"[R:R WARNING] {symbol}: R:R {actual_rr:.2f}:1 (target {min_rr:.1f}:1) — "
                        f"borderline, letting Trade Judge decide.",
                        flush=True
                    )
            else:
                logger.info(f"R:R OK for {symbol}: {actual_rr:.2f} (min {min_rr:.1f})")
            
            # Claude's TP is trusted — based on structure, liquidity, IPDA levels.
            # No hardcoded TP floors or ceilings. R:R enforcement above handles rejection.
            
            # ============================================
            # COUNTER-TREND SCALP CAP
            # If scalp direction opposes the D1 bias, enforce stricter limits
            # ============================================
            _trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
            _d1_bias = market_data.get('d1_bias', '').lower() if market_data else ''
            _is_counter_trend_scalp = (
                _trade_type == 'scalp'
                and _d1_bias in ('bullish', 'bearish')
                and (
                    (_d1_bias == 'bullish' and _dir == 'short')
                    or (_d1_bias == 'bearish' and _dir == 'long')
                )
            )
            if _is_counter_trend_scalp:
                # Cap confidence at 70%
                if trade_signal.confidence > 0.70:
                    logger.info(
                        f"[COUNTER-SCALP] {symbol}: Counter-D1-trend scalp confidence "
                        f"{trade_signal.confidence:.0%} -> capped at 70%"
                    )
                    trade_signal.confidence = 0.70
                # Enforce 2.0:1 R:R minimum
                if actual_rr < 2.0:
                    logger.warning(
                        f"[BLOCKED] {symbol}: Counter-D1-trend scalp R:R {actual_rr:.2f}:1 "
                        f"below 2.0:1 minimum. D1={_d1_bias}, dir={_dir}. Rejected."
                    )
                    print(
                        f"[BLOCKED] {symbol}: Counter-trend scalp needs 2.0:1 R:R, "
                        f"got {actual_rr:.2f}:1. Skipping.",
                        flush=True
                    )
                    return
            
            # ============================================
            # ZONE-AWARE DIRECTION GATE (ALL TRADE TYPES)
            # ICT principle: sell from premium, buy from discount.
            # Uses PremiumDiscountAnalyzer when available,
            # falls back to legacy D1 bias gate otherwise.
            # ============================================
            _regime_data = market_data.get('regime', {}) if market_data else {}
            _regime_type = _regime_data.get('regime', '').lower() if isinstance(_regime_data, dict) else ''

            _zone_gate_mode = settings.trading.zone_gate_mode
            _use_zone_gate_live = (
                pd_analysis is not None
                and _zone_gate_mode in ('active', 'shadow')
                and symbol not in settings.trading.zone_gate_disabled_symbols
                and not _is_counter_trend_scalp
            )

            if _use_zone_gate_live:
                _zone_str = pd_analysis.current_zone.value
                _retrace = pd_analysis.retracement_percent
                _zone_aligned = (
                    (_dir == 'short' and _retrace >= 0.5)
                    or (_dir == 'long' and _retrace <= 0.5)
                )
                _zone_misaligned = (
                    (_dir == 'long' and _retrace >= 0.618)
                    or (_dir == 'short' and _retrace <= 0.382)
                )

                _zone_blocked = False
                if _zone_misaligned:
                    _zm_conf = settings.trading.zone_misaligned_min_confidence
                    _zm_rr = settings.trading.zone_misaligned_min_rr
                    if trade_signal.confidence < _zm_conf or actual_rr < _zm_rr:
                        _zone_blocked = True
                        _block_reason = (
                            f"[ZONE-GATE] {symbol}: {_dir.upper()} from {_zone_str} "
                            f"(retrace={_retrace:.0%}). conf={trade_signal.confidence:.0%} "
                            f"(need {_zm_conf:.0%}), RR={actual_rr:.1f} (need {_zm_rr:.0f}:1). Rejected."
                        )
                elif not _zone_aligned:
                    _eq_conf = settings.trading.zone_equilibrium_min_confidence
                    if trade_signal.confidence < _eq_conf:
                        _zone_blocked = True
                        _block_reason = (
                            f"[ZONE-GATE] {symbol}: {_dir.upper()} from {_zone_str} "
                            f"(equilibrium). conf={trade_signal.confidence:.0%} "
                            f"(need {_eq_conf:.0%}). Rejected."
                        )

                if _zone_blocked and _zone_gate_mode == 'shadow':
                    logger.info(f"[ZONE-GATE-SHADOW] Would block: {_block_reason}")
                    _zone_blocked = False

                if _zone_blocked:
                    logger.warning(_block_reason)
                    print(_block_reason, flush=True)
                    return
                elif _zone_aligned:
                    logger.info(
                        f"[ZONE-GATE] {symbol}: {_dir.upper()} zone-aligned in {_zone_str} "
                        f"(retrace={_retrace:.0%}) — allowed"
                    )

            elif not _is_counter_trend_scalp:
                # Legacy D1 direction gate fallback
                _is_counter_d1 = (
                    _d1_bias in ('bullish', 'bearish')
                    and (
                        (_d1_bias == 'bullish' and _dir == 'short')
                        or (_d1_bias == 'bearish' and _dir == 'long')
                    )
                )
                if _is_counter_d1:
                    _ct_min_conf = 0.70
                    _ct_min_rr = 3.0
                    if trade_signal.confidence < _ct_min_conf or actual_rr < _ct_min_rr:
                        logger.warning(
                            f"[DIRECTION-GATE] {symbol}: {_dir.upper()} opposes D1 bias "
                            f"({_d1_bias}). Confidence {trade_signal.confidence:.0%} "
                            f"(need {_ct_min_conf:.0%}), R:R {actual_rr:.2f} "
                            f"(need {_ct_min_rr:.1f}). Rejected."
                        )
                        print(
                            f"[DIRECTION-GATE] {symbol}: {_dir.upper()} vs D1 {_d1_bias}. "
                            f"Need {_ct_min_conf:.0%} conf + {_ct_min_rr:.0f}:1 RR for counter-trend. "
                            f"Got {trade_signal.confidence:.0%} / {actual_rr:.1f}:1. Skipping.",
                            flush=True
                        )
                        return
                    else:
                        logger.info(
                            f"[DIRECTION-GATE] {symbol}: Counter-D1 {_dir.upper()} ALLOWED "
                            f"— high conf ({trade_signal.confidence:.0%}) + strong R:R ({actual_rr:.1f}:1)"
                        )

            if _regime_type == 'volatile_ranging':
                _vr_min_conf = 0.70
                if trade_signal.confidence < _vr_min_conf:
                    logger.warning(
                        f"[REGIME-GATE] {symbol}: Volatile ranging regime — "
                        f"confidence {trade_signal.confidence:.0%} below {_vr_min_conf:.0%} minimum. Rejected."
                    )
                    print(
                        f"[REGIME-GATE] {symbol}: VOLATILE RANGING detected. "
                        f"Need {_vr_min_conf:.0%} confidence, got {trade_signal.confidence:.0%}. Skipping.",
                        flush=True
                    )
                    return

            # ============================================
            # TIME-OF-DAY PERFORMANCE GATE
            # Skip historically weak hours unless confidence
            # is elevated. Based on backtest analysis.
            # ============================================
            _current_utc_hour = datetime.now(timezone.utc).hour
            _weak_hours = settings.trading.weak_hours_by_symbol.get(symbol, [])
            if _current_utc_hour in _weak_hours:
                _tod_min_conf = 0.68
                if trade_signal.confidence < _tod_min_conf:
                    logger.warning(
                        f"[TOD-GATE] {symbol}: Hour {_current_utc_hour:02d}:00 UTC is a weak hour. "
                        f"Confidence {trade_signal.confidence:.0%} below {_tod_min_conf:.0%}. Rejected."
                    )
                    print(
                        f"[TOD-GATE] {symbol}: Weak hour ({_current_utc_hour:02d}:00 UTC). "
                        f"Need {_tod_min_conf:.0%} confidence, got {trade_signal.confidence:.0%}. Skipping.",
                        flush=True
                    )
                    return

            # ============================================
            # M15 EXECUTION TIMEFRAME STRUCTURE GATE
            # M15 is the execution TF — trading against it means
            # fighting the current price action.
            # ============================================
            _m15_bias = (market_data.get('m15_bias') or '').lower() if market_data else ''
            _amd_phase_raw = getattr(trade_signal, 'amd_phase', '') or ''
            _amd_phase_lc = _amd_phase_raw.lower()
            _m15_opposes = (
                (_m15_bias == 'bearish' and _dir == 'long')
                or (_m15_bias == 'bullish' and _dir == 'short')
            )
            if _m15_opposes and _amd_phase_lc != 'manipulation':
                _d1_supports = (
                    (_d1_bias == 'bullish' and _dir == 'long')
                    or (_d1_bias == 'bearish' and _dir == 'short')
                )
                _h4_supports = (
                    (_h4_bias == 'bullish' and _dir == 'long')
                    or (_h4_bias == 'bearish' and _dir == 'short')
                )
                _is_pending_limit = trade_signal.order_type in ('buy_limit', 'sell_limit')
                _is_pullback = _d1_supports and _h4_supports and _is_pending_limit

                if _is_pullback:
                    trade_signal.confidence = min(trade_signal.confidence, 0.55)
                    logger.info(
                        f"[ANTICIPATORY] {symbol}: M15 opposes {_dir.upper()} but D1+H4 "
                        f"support — allowing pending limit (pullback entry). "
                        f"Confidence capped at {trade_signal.confidence:.0%}"
                    )
                    print(
                        f"[ANTICIPATORY] {symbol}: Pullback detected, pending {trade_signal.order_type} "
                        f"allowed at key level. Confidence {trade_signal.confidence:.0%}",
                        flush=True
                    )
                else:
                    logger.warning(
                        f"[BLOCKED] {symbol}: {_dir.upper()} contradicts M15 bias "
                        f"({_m15_bias}). Execution TF must confirm direction. Rejected."
                    )
                    print(
                        f"[BLOCKED] {symbol}: {_dir.upper()} vs M15 {_m15_bias} structure. "
                        f"Execution timeframe opposes trade. Skipping.",
                        flush=True
                    )
                    return

            # ============================================
            # HTF (D1 + H4) ALIGNMENT GATE
            # Both D1 and H4 opposing = hard block.
            # One opposing = confidence cap.
            # ============================================
            _h4_bias = (market_data.get('h4_bias') or '').lower() if market_data else ''
            _d1_opposes = (
                (_d1_bias == 'bearish' and _dir == 'long')
                or (_d1_bias == 'bullish' and _dir == 'short')
            )
            _h4_opposes = (
                (_h4_bias == 'bearish' and _dir == 'long')
                or (_h4_bias == 'bullish' and _dir == 'short')
            )
            _trade_type_lc = (trade_signal.trade_type or '').lower()
            _is_scalp = 'scalp' in _trade_type_lc
            if _d1_opposes and _h4_opposes:
                if _is_scalp and not _m15_opposes and actual_rr >= 2.0 and trade_signal.confidence >= 0.70:
                    trade_signal.confidence = min(trade_signal.confidence, 0.55)
                    logger.info(
                        f"[COUNTER-SCALP] {symbol}: HTFs oppose but M15 confirms {_dir.upper()} — "
                        f"allowing scalp with capped confidence {trade_signal.confidence:.0%}"
                    )
                else:
                    logger.warning(
                        f"[BLOCKED] {symbol}: {_dir.upper()} opposes BOTH D1 ({_d1_bias}) "
                        f"and H4 ({_h4_bias}). HTF alignment required. Rejected."
                    )
                    print(
                        f"[BLOCKED] {symbol}: {_dir.upper()} vs D1={_d1_bias} & H4={_h4_bias}. "
                        f"Both HTFs oppose — skipping.",
                        flush=True
                    )
                    return
            elif _d1_opposes or _h4_opposes:
                _opposing_tf = 'D1' if _d1_opposes else 'H4'
                if trade_signal.confidence > 0.60:
                    logger.info(
                        f"[HTF-CAP] {symbol}: {_opposing_tf} opposes {_dir.upper()} "
                        f"({_d1_bias}/{_h4_bias}). Confidence {trade_signal.confidence:.0%} -> 60%"
                    )
                    trade_signal.confidence = 0.60

            # ============================================
            # AMD DISTRIBUTION PHASE GATE
            # Distribution = move is done. Require strong R:R
            # and cap confidence for marginal entries.
            # ============================================
            _bot_amd_phase = ''
            if analysis_results.get("amd_cycle"):
                _bot_amd_phase = (analysis_results["amd_cycle"].get("phase") or '').lower()
            _effective_amd = _bot_amd_phase or _amd_phase_lc
            if _effective_amd == 'distribution':
                if trade_signal.confidence > 0.55:
                    logger.info(
                        f"[DISTRIB-CAP] {symbol}: Distribution phase — confidence "
                        f"{trade_signal.confidence:.0%} -> capped at 55%"
                    )
                    trade_signal.confidence = 0.55
                if actual_rr < 2.5:
                    logger.warning(
                        f"[BLOCKED] {symbol}: Distribution phase + R:R {actual_rr:.2f}:1 "
                        f"below 2.5:1 minimum. Move is done. Rejected."
                    )
                    print(
                        f"[BLOCKED] {symbol}: Distribution phase, R:R only "
                        f"{actual_rr:.2f}:1 (need 2.5). Skipping.",
                        flush=True
                    )
                    return

            # ============================================
            # OFF-HOURS SOFT BLOCK
            # Outside kill zones: cap confidence, raise R:R bar
            # ============================================
            if self._off_hours_mode:
                if trade_signal.confidence > 0.50:
                    logger.info(
                        f"[OFF-HOURS] {symbol}: Confidence {trade_signal.confidence:.0%} -> 50% (outside kill zone)"
                    )
                    trade_signal.confidence = 0.50
                if actual_rr < 3.0:
                    logger.warning(
                        f"[BLOCKED] {symbol}: Off-hours R:R {actual_rr:.2f}:1 < 3.0 minimum. Skipping."
                    )
                    print(
                        f"[BLOCKED] {symbol}: Off-hours, need 3.0 R:R, got {actual_rr:.2f}. Skipping.",
                        flush=True
                    )
                    return

            # ============================================
            # POST-COOLDOWN CONFIDENCE GATE
            # First trade after a loss cooldown needs 75%+
            # ============================================
            if hasattr(self, '_post_cooldown_symbols') and symbol in self._post_cooldown_symbols:
                if trade_signal.confidence < 0.75:
                    logger.warning(
                        f"[POST-COOLDOWN] {symbol}: First signal after loss cooldown "
                        f"needs 75%+ confidence, got {trade_signal.confidence:.0%}. Rejected."
                    )
                    print(
                        f"[POST-COOLDOWN] {symbol}: Need 75% confidence after loss, "
                        f"got {trade_signal.confidence:.0%}. Skipping.",
                        flush=True
                    )
                    return
                self._post_cooldown_symbols.discard(symbol)

            # ============================================
            # SESSION-AWARE CONFIDENCE ADJUSTMENT
            # Kill zones = baseline, off-session = penalty
            # ============================================
            _session = self.kill_zone_checker.get_current_session() if self.kill_zone_checker else None
            _session_name = (_session.session_name if _session else '').lower()
            _is_kill = _session.is_kill_zone if _session else False
            _session_penalty = 0.0
            if self._off_hours_mode:
                pass  # Off-hours block already capped confidence — don't double-penalize
            elif not _is_kill:
                if 'asian' in _session_name:
                    _session_penalty = 0.10
                else:
                    _session_penalty = 0.15
            elif 'london close' in _session_name or 'london_close' in _session_name:
                _session_penalty = 0.05
            if _session_penalty > 0 and trade_signal.confidence > 0:
                _old_conf = trade_signal.confidence
                trade_signal.confidence = max(0.40, trade_signal.confidence - _session_penalty)
                if trade_signal.confidence != _old_conf:
                    logger.info(
                        f"[SESSION-CONF] {symbol}: {_session_name} penalty -{_session_penalty:.0%}: "
                        f"confidence {_old_conf:.0%} -> {trade_signal.confidence:.0%}"
                    )

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
            
            # Volume enforcement -- block dead-market entries
            _rel_vol = 1.0
            try:
                _vol_data = analysis_results.get("volume", {})
                if isinstance(_vol_data, dict):
                    _rel_vol = _vol_data.get("relative_volume", 1.0) or 1.0
                elif hasattr(_vol_data, 'relative_volume'):
                    _rel_vol = getattr(_vol_data, 'relative_volume', 1.0) or 1.0
            except Exception:
                pass
            
            if _rel_vol < 0.3:
                print(f"[VOLUME-BLOCK] {symbol}: Relative volume {_rel_vol:.2f}x < 0.3 — dead market, skipping", flush=True)
                logger.warning(f"Trade blocked for {symbol}: relative volume {_rel_vol:.2f}x (dead market)")
                return
            elif _rel_vol < 0.5:
                _old_conf = trade_signal.confidence
                trade_signal.confidence = min(trade_signal.confidence, 0.70)
                if _old_conf != trade_signal.confidence:
                    print(f"[VOLUME-CAP] {symbol}: Relative volume {_rel_vol:.2f}x — confidence capped {_old_conf:.0%} -> {trade_signal.confidence:.0%}", flush=True)
            
            # Require minimum confluence factors for trades
            # In AGGRESSIVE mode (data collection), lower the bar to 1 factor at 65%+ confidence
            min_confluence = 1 if (self.scaling_manager and self.scaling_manager.current_mode.value == 'aggressive') else 2
            confidence_override = 0.65 if (self.scaling_manager and self.scaling_manager.current_mode.value == 'aggressive') else 0.75
            
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
                # Use EQUITY (includes unrealized P/L) for drawdown-related mode decisions
                # Using balance alone ignores floating profits from open positions,
                # causing false DEFENSIVE downgrades when open trades dip temporarily.
                account_info = await self.mt5_client.get_account_info()
                current_equity = account_info.equity if account_info else 1000.0
                
                # Skip per-symbol mode recalculation if day-of-week override is active
                # (Monday=CONSERVATIVE, Friday=CONSERVATIVE) — only drawdown can override
                if getattr(self, '_day_of_week_mode_locked', False):
                    # Still check drawdown even on locked days
                    daily_dd = self.scaling_manager.calculate_daily_drawdown(current_equity)
                    weekly_dd = self.scaling_manager.calculate_weekly_drawdown(current_equity)
                    if weekly_dd >= self.scaling_manager.max_weekly_drawdown or daily_dd >= self.scaling_manager.max_daily_drawdown:
                        _prev_mode = self.scaling_manager.current_mode
                        self.scaling_manager.current_mode = TradingMode.DEFENSIVE
                        print(f"[SCALING] {symbol}: DEFENSIVE (drawdown override on locked day), equity={current_equity}", flush=True)
                        if TradingMode.DEFENSIVE != _prev_mode:
                            from .api.routes.activity import add_activity
                            add_activity("mode_change", f"Trading mode changed to DEFENSIVE (drawdown override)", details={"mode": "DEFENSIVE", "previous": _prev_mode.value, "reason": "drawdown_override", "daily_dd": f"{daily_dd:.2%}", "weekly_dd": f"{weekly_dd:.2%}"})
                    else:
                        print(f"[SCALING] {symbol}: Mode={self.scaling_manager.current_mode.value} (day-of-week locked), equity={current_equity}", flush=True)
                else:
                    # Let Claude help with mode decision if available
                    # OVERRIDE: In AGGRESSIVE mode (data collection), ignore Claude's mode recommendation
                    # to prevent it from downgrading to defensive/conservative during demo testing
                    claude_mode = None
                    if self.scaling_manager.current_mode != TradingMode.AGGRESSIVE:
                        if self.claude_client and self.claude_client.api_key:
                            try:
                                scaling_decision = await self.claude_client.assess_scaling_decision(
                                    current_equity=current_equity,
                                    current_tier=self.position_sizer.get_tier_name(current_equity) if self.position_sizer else "Unknown",
                                    recent_performance=self.scaling_manager.get_recent_performance(),
                                    goal_progress=self.scaling_manager.calculate_goal_progress(current_equity)
                                )
                                claude_mode = scaling_decision.get('recommended_mode')
                                print(f"[SCALING] {symbol}: Claude recommended mode: {claude_mode}", flush=True)
                            except Exception as e:
                                logger.debug(f"Could not get Claude scaling decision: {e}")
                    else:
                        print(f"[SCALING] {symbol}: AGGRESSIVE mode locked (data collection) — skipping Claude mode assessment", flush=True)
                    
                    # Determine current trading mode (uses equity for drawdown watermarks)
                    _prev_mode = self.scaling_manager.current_mode
                    mode = self.scaling_manager.determine_mode(current_equity, claude_mode)
                    print(f"[SCALING] {symbol}: Mode={mode.value}, equity={current_equity}", flush=True)
                    if mode != self.scaling_manager.current_mode:
                        self.scaling_manager.current_mode = mode
                        logger.info(f"Trading mode changed to: {mode.value}")
                    if self.scaling_manager.current_mode != _prev_mode:
                        from .api.routes.activity import add_activity
                        add_activity("mode_change", f"Trading mode changed to {self.scaling_manager.current_mode.value}", details={"mode": self.scaling_manager.current_mode.value, "previous": _prev_mode.value, "symbol": symbol, "equity": current_equity})
                
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
            
            # R:R was already validated in the A6 block above (rejected if below 1.0,
            # borderline passed to Trade Judge). Claude's TP/SL are never modified.
            # We do NOT reject based on Claude's self-reported risk_reward field here.
            # The final validate_trade() call will do the definitive R:R check.
            
            # =============================================
            # DIRECTION-FLIP COOLDOWN
            # =============================================
            # If Claude just flipped direction on the same symbol within 15 minutes,
            # require higher confidence (80%) to proceed. This prevents the
            # prediction-driven flip-flopping pattern (e.g., LONG 75% -> SHORT 75%).
            # EXCEPTIONS: Reversal re-entries and direction coherence flips bypass this.
            flip_cooldown_minutes = 15
            flip_min_confidence = 0.80
            
            if _direction_flipped:
                logger.info(
                    f"[FLIP-GUARD] {symbol}: Bypassing cooldown — direction coherence "
                    f"check already flipped to {trade_signal.direction.upper()}"
                )
            elif getattr(trade_signal, 'reversal_reentry', False):
                logger.info(
                    f"[FLIP-GUARD] {symbol}: Bypassing cooldown for reversal re-entry "
                    f"({trade_signal.direction.upper()})"
                )
            elif symbol in self._last_signal_direction:
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
            
            # Gap 21: Track signal hashes for dedup, but DON'T hard-block.
            # Multiple trades per symbol are allowed if the analysis supports it.
            # The pending order replacement logic downstream already handles
            # cancelling old orders and placing new ones for the same symbol+direction.
            signal_hash = self._get_signal_hash(symbol, trade_signal.direction, trade_signal.entry_price or current_price)
            if signal_hash in self._recent_signal_hashes:
                # Same exact entry price + direction was placed recently.
                # Allow it to proceed — the downstream logic will either:
                # (a) replace the pending order with updated TP/SL, or
                # (b) open a second position if the first already filled.
                logger.info(f"[DEDUP] {symbol}: Repeat signal (same entry), allowing through for re-evaluation")
                print(f"[DEDUP] {symbol}: Repeat {trade_signal.direction} signal @ {trade_signal.entry_price or current_price:.2f} — allowing (may update pending order)", flush=True)
            
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
                    claude_recommendation=claude_size_rec,
                    confluence_count=confluence_count or 0,
                )
                
                # Apply crypto volatility adjustment if applicable
                final_lots = size_result.lots
                if is_crypto and self.crypto_analyzer:
                    crypto_adj = self.crypto_analyzer.get_position_size_adjustment(symbol, final_lots)
                    from .config import normalize_lots
                    final_lots = normalize_lots(symbol, crypto_adj)
                    logger.info(f"🪙 Crypto volatility adjustment: {size_result.lots} -> {final_lots} lots")
                
                # Apply scaling manager risk multiplier (reduces lots during drawdowns)
                if self.scaling_manager:
                    mode_config = self.scaling_manager.get_mode_config()
                    risk_mult = getattr(mode_config, 'risk_multiplier', 1.0)
                    if risk_mult != 1.0:
                        pre_scale_lots = final_lots
                        from .config import normalize_lots as _norm_lots
                        final_lots = _norm_lots(symbol, final_lots * risk_mult)
                        logger.info(
                            f"[SCALING] {symbol}: Lots {pre_scale_lots} x {risk_mult:.2f} "
                            f"({self.scaling_manager.current_mode.value}) = {final_lots}"
                        )
                        print(
                            f"[SCALING] {symbol}: Position size adjusted "
                            f"{pre_scale_lots} -> {final_lots} lots "
                            f"(mode={self.scaling_manager.current_mode.value}, mult={risk_mult:.2f})",
                            flush=True
                        )
                
                # Apply news impact position size reduction
                if self.news_service:
                    try:
                        _news_mult, _news_reason = self.news_service.should_reduce_size(symbol)
                        if _news_mult < 1.0:
                            pre_news_lots = final_lots
                            from .config import normalize_lots as _norm_news
                            final_lots = _norm_news(symbol, final_lots * _news_mult)
                            logger.info(
                                f"[NEWS-IMPACT] {symbol}: Lots {pre_news_lots} x {_news_mult:.2f} "
                                f"= {final_lots} ({_news_reason})"
                            )
                            print(
                                f"[NEWS-IMPACT] {symbol}: Size reduced {pre_news_lots} -> "
                                f"{final_lots} lots ({_news_reason})",
                                flush=True
                            )
                    except Exception as _news_err:
                        logger.debug(f"[NEWS-IMPACT] Error checking news impact for {symbol}: {_news_err}")
                
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
                _val_entry = trade_signal.entry_price or current_price
                _val_sl = trade_signal.stop_loss
                _val_tp = trade_signal.take_profit
                _val_dir = trade_signal.direction
                print(f"[VALIDATE] {symbol}: Running risk validation (entry={_val_entry}, SL={_val_sl}, TP={_val_tp}, dir={_val_dir})...", flush=True)
                validation = self.risk_manager.validate_trade(
                    entry_price=_val_entry,
                    stop_loss=_val_sl,
                    take_profit=_val_tp,
                    direction=_val_dir,
                    symbol=symbol,
                    account_balance=account_info.balance,
                    actual_risk_pct=size_result.risk_percent,  # Use actual scaled risk, not default
                    trade_type=getattr(trade_signal, 'trade_type', 'intraday')
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
                
                # Record original confidence before secondary modifiers
                _conf_before_modifiers = trade_signal.confidence
                
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
                
                # Cap total positive confidence boost from secondary modifiers at +10%
                _conf_boost = trade_signal.confidence - _conf_before_modifiers
                if _conf_boost > 0.10:
                    trade_signal.confidence = _conf_before_modifiers + 0.10
                    logger.info(
                        f"[CONF-CAP] {symbol}: Secondary modifiers boosted +{_conf_boost*100:.0f}%, "
                        f"capped to +10% (final: {trade_signal.confidence:.0%})"
                    )
                
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
                        _max_tp_pct = 0.20 if symbol in self.CRYPTO_SYMBOLS or symbol in self.PRECIOUS_METALS or symbol in self.INDEX_SYMBOLS or symbol in self.OIL_SYMBOLS else 0.10
                        
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
                        
                        # IPDA levels are used for multi-TP management (partial closes),
                        # but NEVER override Claude's primary TP. Claude's TP is based on
                        # actual structure/liquidity analysis. IPDA snap was corrupting TP
                        # (e.g. XAUUSD R:R going from 2.37:1 to 0.02:1).
                        # Keep Claude's TP as the primary target.
                        logger.info(
                            f"IPDA TP levels calculated for {symbol} (multi-TP only, "
                            f"Claude's TP {trade_signal.take_profit:.5f} preserved as primary)"
                        )
                        
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
                    
                    if _final_rr < 1.0 and _final_sl_dist > 0:
                        # R:R below 1.0 after adjustments — hard reject
                        logger.warning(
                            f"[BLOCKED] FINAL R:R CHECK {symbol}: {_final_rr:.2f}:1 < 1.0:1 "
                            f"after adjustments. Reward < risk. Trade rejected. "
                            f"(entry={_final_entry}, SL={_final_sl}, TP={_final_tp})"
                        )
                        print(
                            f"[BLOCKED] {symbol}: Final R:R {_final_rr:.2f}:1 < 1.0 "
                            f"after price adjustments. Trade rejected. "
                            f"(entry={_final_entry}, SL={_final_sl}, TP={_final_tp})",
                            flush=True
                        )
                        return
                    elif _final_rr < min_rr and _final_sl_dist > 0:
                        # Borderline — log warning, Trade Judge will evaluate
                        logger.info(
                            f"[R:R WARNING] FINAL CHECK {symbol}: R:R {_final_rr:.2f}:1 "
                            f"below target {min_rr:.1f}:1 — Trade Judge will decide."
                        )
                
                # No hardcoded TP floors or sanity gates — Claude's TP is final.
                # The FINAL R:R SAFETY NET above handles rejection of bad R:R.
                # No price fabrication. Accept or reject only.
                
                # =============================================
                # STALE ENTRY FIX: Rebase SL/TP for market orders
                # When Claude proposed entry differs from current price,
                # preserve the SL/TP distances so they stay valid
                # =============================================
                if (trade_signal.order_type in ('market', None) and 
                    trade_signal.entry_price and trade_signal.stop_loss and trade_signal.take_profit and
                    current_price > 0):
                    proposed_entry = trade_signal.entry_price
                    drift = abs(current_price - proposed_entry)
                    drift_pct = drift / proposed_entry if proposed_entry > 0 else 0
                    if drift_pct > 0.002:  # >0.2% drift triggers rebase
                        sl_offset = trade_signal.stop_loss - proposed_entry
                        tp_offset = trade_signal.take_profit - proposed_entry
                        new_sl = round(current_price + sl_offset, 5)
                        new_tp = round(current_price + tp_offset, 5)
                        print(
                            f"[REBASE] {symbol}: Price drifted {drift_pct:.2%} "
                            f"(proposed {proposed_entry:.5f} -> market {current_price:.5f}). "
                            f"SL {trade_signal.stop_loss:.5f}->{new_sl:.5f}, "
                            f"TP {trade_signal.take_profit:.5f}->{new_tp:.5f}",
                            flush=True
                        )
                        trade_signal.entry_price = current_price
                        trade_signal.stop_loss = new_sl
                        trade_signal.take_profit = new_tp
                
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
                    
                    # Save rejected signal to DB for correlation (did we miss a winner?)
                    await save_signal_to_db(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        confidence=trade_signal.confidence,
                        entry_price=trade_signal.entry_price or current_price,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                        judge_verdict="REJECT",
                        judge_reason=reason,
                        judge_risk_flags=flags,
                        trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                        market_structure=getattr(trade_signal, 'market_structure', ''),
                        confluence_factors=confluence_factors if confluence_factors else None,
                        confluence_count=confluence_count if confluence_count else None,
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
                        # Default: 0.1% improvement from current price (tight enough to fill)
                        if trade_signal.direction == 'long':
                            demoted_entry = round(current_price * 0.999, 5)  # Buy slightly cheaper
                        else:
                            demoted_entry = round(current_price * 1.001, 5)  # Sell slightly higher
                    
                    # ---- Guard: R:R must still be >= 1.0 after demotion ----
                    # No hardcoded TP floors. Just check Claude's TP vs demoted entry.
                    _sl_for_check = trade_signal.stop_loss or 0
                    _demote_sl_dist = abs(demoted_entry - _sl_for_check) if _sl_for_check else 0
                    _demote_tp_dist = abs((trade_signal.take_profit or 0) - demoted_entry)
                    _demote_rr = _demote_tp_dist / _demote_sl_dist if _demote_sl_dist > 0 else 0
                    
                    if _demote_rr < 1.0 and _demote_sl_dist > 0:
                        logger.warning(
                            f"[JUDGE] DEMOTE REJECTED {symbol}: R:R {_demote_rr:.2f}:1 < 1.0 "
                            f"after demotion to {demoted_entry:.5f}. "
                            f"SL dist=${_demote_sl_dist:.2f}, TP dist=${_demote_tp_dist:.2f}."
                        )
                        print(
                            f"[JUDGE] DEMOTE REJECTED {symbol}: R:R {_demote_rr:.2f}:1 < 1.0 "
                            f"after demotion (entry={demoted_entry:.5f}, SL={_sl_for_check:.5f}, "
                            f"TP={trade_signal.take_profit:.5f})",
                            flush=True
                        )
                        self.daily_trades = max(0, self.daily_trades - 1)
                        logger.info(f"Trade slot released after demote R:R rejection ({self.daily_trades}/{settings.trading.max_daily_trades})")
                        return
                    
                    # Override to pending limit order
                    if trade_signal.direction == 'long':
                        trade_signal.order_type = 'buy_limit'
                    else:
                        trade_signal.order_type = 'sell_limit'
                    # Rebase SL/TP as offsets from the demoted entry
                    _orig_entry = trade_signal.entry_price or current_price
                    if trade_signal.stop_loss and _orig_entry and _orig_entry > 0:
                        _sl_offset = trade_signal.stop_loss - _orig_entry
                        trade_signal.stop_loss = demoted_entry + _sl_offset
                    if trade_signal.take_profit and _orig_entry and _orig_entry > 0:
                        _tp_offset = trade_signal.take_profit - _orig_entry
                        trade_signal.take_profit = demoted_entry + _tp_offset
                    
                    trade_signal.entry_price = demoted_entry
                    
                    # ---- Guard: SL must remain on the correct side of the demoted entry ----
                    _sl_check = trade_signal.stop_loss or 0
                    if _sl_check > 0:
                        _sl_wrong_side = False
                        if trade_signal.direction == 'long' and _sl_check >= demoted_entry:
                            _sl_wrong_side = True
                        elif trade_signal.direction == 'short' and _sl_check <= demoted_entry:
                            _sl_wrong_side = True
                        
                        if _sl_wrong_side:
                            logger.warning(
                                f"[JUDGE] DEMOTE REJECTED {symbol}: SL ({_sl_check:.5f}) is on wrong side "
                                f"of demoted entry ({demoted_entry:.5f}) for {trade_signal.direction}. "
                                f"Demotion pushed entry past the SL."
                            )
                            print(
                                f"[JUDGE] DEMOTE REJECTED {symbol}: SL {_sl_check:.5f} is on WRONG SIDE "
                                f"of entry {demoted_entry:.5f} ({trade_signal.direction}). Skipping.",
                                flush=True
                            )
                            self.daily_trades = max(0, self.daily_trades - 1)
                            logger.info(f"Trade slot released after SL-side rejection ({self.daily_trades}/{settings.trading.max_daily_trades})")
                            return
                    
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
                    
                    # Save demoted signal to DB for correlation
                    await save_signal_to_db(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        confidence=trade_signal.confidence,
                        entry_price=demoted_entry,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                        judge_verdict="DEMOTE",
                        judge_reason=reason,
                        judge_risk_flags=flags,
                        trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                        market_structure=getattr(trade_signal, 'market_structure', ''),
                        confluence_factors=confluence_factors if confluence_factors else None,
                        confluence_count=confluence_count if confluence_count else None,
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
                    
                    # Save approved signal to DB for correlation
                    await save_signal_to_db(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        confidence=trade_signal.confidence,
                        entry_price=trade_signal.entry_price or current_price,
                        stop_loss=trade_signal.stop_loss,
                        take_profit=trade_signal.take_profit,
                        reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                        judge_verdict="APPROVE",
                        judge_reason=reason,
                        judge_risk_flags=flags,
                        trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                        market_structure=getattr(trade_signal, 'market_structure', ''),
                        confluence_factors=confluence_factors if confluence_factors else None,
                        confluence_count=confluence_count if confluence_count else None,
                    )
                
                # =============================================
                # POSITION CONFLICT GUARD: Block duplicate or
                # conflicting positions on the same symbol.
                # =============================================
                if self.position_manager:
                    existing_positions = self.position_manager.get_positions_by_symbol(symbol)
                    if existing_positions:
                        # Block opposite-direction conflict
                        opposite_dir = 'short' if trade_signal.direction == 'long' else 'long'
                        conflicting = [
                            p for p in existing_positions
                            if p.direction == opposite_dir
                        ]
                        if conflicting:
                            print(
                                f"[BLOCKED] {symbol}: Cannot place {trade_signal.direction.upper()} order — "
                                f"already have {opposite_dir.upper()} position open "
                                f"(ticket={conflicting[0].ticket}). "
                                f"Close existing position first or wait for reversal re-entry logic.",
                                flush=True
                            )
                            logger.warning(
                                f"Blocked {trade_signal.direction} {symbol}: opposite-direction "
                                f"position exists (ticket={conflicting[0].ticket})"
                            )
                            self.daily_trades = max(0, self.daily_trades - 1)
                            return
                        
                        # Block same-direction stacking
                        same_dir = [
                            p for p in existing_positions
                            if p.direction == trade_signal.direction
                        ]
                        if same_dir:
                            print(
                                f"[BLOCKED] {symbol}: Already have {trade_signal.direction.upper()} "
                                f"position open (ticket={same_dir[0].ticket}). "
                                f"No same-direction stacking allowed.",
                                flush=True
                            )
                            logger.warning(
                                f"Blocked {trade_signal.direction} {symbol}: same-direction "
                                f"position already open (ticket={same_dir[0].ticket})"
                            )
                            self.daily_trades = max(0, self.daily_trades - 1)
                            return
                
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
                
                # Fix mislabeled limit/stop orders: buy_limit must be BELOW price,
                # sell_limit must be ABOVE price. If inverted, swap to stop.
                if order_type in ('buy_limit', 'sell_limit', 'buy_stop', 'sell_stop') and entry_price and current_price > 0:
                    if order_type == 'buy_limit' and entry_price > current_price * 1.001:
                        order_type = 'buy_stop'
                        trade_signal.order_type = order_type
                        print(f"[ORDER-FIX] {symbol}: buy_limit above market -> buy_stop (entry={entry_price:.5f} > market={current_price:.5f})", flush=True)
                    elif order_type == 'sell_limit' and entry_price < current_price * 0.999:
                        order_type = 'sell_stop'
                        trade_signal.order_type = order_type
                        print(f"[ORDER-FIX] {symbol}: sell_limit below market -> sell_stop (entry={entry_price:.5f} < market={current_price:.5f})", flush=True)
                    elif order_type == 'buy_stop' and entry_price < current_price * 0.999:
                        order_type = 'buy_limit'
                        trade_signal.order_type = order_type
                        print(f"[ORDER-FIX] {symbol}: buy_stop below market -> buy_limit (entry={entry_price:.5f} < market={current_price:.5f})", flush=True)
                    elif order_type == 'sell_stop' and entry_price > current_price * 1.001:
                        order_type = 'sell_limit'
                        trade_signal.order_type = order_type
                        print(f"[ORDER-FIX] {symbol}: sell_stop above market -> sell_limit (entry={entry_price:.5f} > market={current_price:.5f})", flush=True)
                
                # ICT Zone Validation: block limit orders that contradict the price zone
                # Premium = sell only, Discount = buy only (stop orders exempt - breakouts)
                if order_type in ('buy_limit', 'sell_limit'):
                    _zone_str = None
                    _retrace_pct = None
                    try:
                        _pd_data = analysis_results.get("premium_discount", {})
                        if isinstance(_pd_data, dict):
                            _zone_str = _pd_data.get("current_zone")
                            _retrace_pct = _pd_data.get("retracement_percent")
                        elif hasattr(_pd_data, 'current_zone'):
                            _zone_str = _pd_data.current_zone.value if hasattr(_pd_data.current_zone, 'value') else str(_pd_data.current_zone)
                            _retrace_pct = getattr(_pd_data, 'retracement_percent', None)
                    except Exception:
                        pass

                    if _zone_str and _retrace_pct is not None:
                        if order_type == 'buy_limit' and _retrace_pct > 0.70:
                            print(
                                f"[ZONE-BLOCK] {symbol}: buy_limit in PREMIUM zone ({_retrace_pct:.0%}) — "
                                f"ICT rule: do NOT buy in premium. Blocking trade.",
                                flush=True
                            )
                            logger.warning(f"Zone block: buy_limit in premium ({_retrace_pct:.0%}) for {symbol}")
                            self.daily_trades = max(0, self.daily_trades - 1)
                            return
                        elif order_type == 'sell_limit' and _retrace_pct < 0.30:
                            print(
                                f"[ZONE-BLOCK] {symbol}: sell_limit in DISCOUNT zone ({_retrace_pct:.0%}) — "
                                f"ICT rule: do NOT sell in discount. Blocking trade.",
                                flush=True
                            )
                            logger.warning(f"Zone block: sell_limit in discount ({_retrace_pct:.0%}) for {symbol}")
                            self.daily_trades = max(0, self.daily_trades - 1)
                            return
                        elif order_type == 'buy_limit' and _retrace_pct > 0.55:
                            _old_conf = trade_signal.confidence
                            trade_signal.confidence = min(trade_signal.confidence, 0.60)
                            print(
                                f"[ZONE-WARN] {symbol}: buy_limit in upper zone ({_retrace_pct:.0%}) — "
                                f"confidence capped {_old_conf:.0%} -> {trade_signal.confidence:.0%}",
                                flush=True
                            )
                        elif order_type == 'sell_limit' and _retrace_pct < 0.45:
                            _old_conf = trade_signal.confidence
                            trade_signal.confidence = min(trade_signal.confidence, 0.60)
                            print(
                                f"[ZONE-WARN] {symbol}: sell_limit in lower zone ({_retrace_pct:.0%}) — "
                                f"confidence capped {_old_conf:.0%} -> {trade_signal.confidence:.0%}",
                                flush=True
                            )
                
                # Respect Claude's explicit pending order choice — do NOT override to market
                # even during distribution phase. Claude knows the entry model.
                
                # Add spread buffer to SL to prevent premature stop-outs from spread widening
                _final_sl = trade_signal.stop_loss
                _final_tp = trade_signal.take_profit
                try:
                    import MetaTrader5 as mt5
                    _tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
                    if _tick and _tick.ask > 0 and _tick.bid > 0:
                        _spread = _tick.ask - _tick.bid
                        if _spread > 0 and _final_sl:
                            if trade_signal.direction == 'long':
                                # Long SL is below entry, push it down by half a spread
                                _final_sl = _final_sl - (_spread * 0.5)
                            else:
                                # Short SL is above entry, push it up by half a spread
                                _final_sl = _final_sl + (_spread * 0.5)
                            logger.info(
                                f"[SPREAD-BUF] {symbol}: SL adjusted by 0.5x spread ({_spread:.5f}): "
                                f"{trade_signal.stop_loss:.5f} -> {_final_sl:.5f}"
                            )
                except Exception as e:
                    logger.debug(f"[SPREAD-BUF] Could not adjust SL for spread: {e}")
                
                # DRY-RUN MODE: Log the trade but skip actual execution
                if settings.trading.dry_run:
                    print(
                        f"[DRY-RUN] Would place {order_type} {trade_signal.direction.upper()} "
                        f"{symbol} @ {trade_signal.entry_price:.5f} "
                        f"(SL: {_final_sl:.5f}, TP: {_final_tp:.5f}, "
                        f"Lots: {position_size.lots}, Conf: {trade_signal.confidence:.0%})",
                        flush=True
                    )
                    logger.info(
                        f"[DRY-RUN] {symbol}: {order_type} {trade_signal.direction} "
                        f"@ {trade_signal.entry_price}, SL={_final_sl}, TP={_final_tp}, "
                        f"lots={position_size.lots}, confidence={trade_signal.confidence:.0%}"
                    )
                    return None
                
                if order_type == 'market':
                    # Tick-level micro-confirmation before market execution
                    _tick_ok = True
                    try:
                        _tick_info = await self.mt5_client.get_symbol_info(symbol)
                        if _tick_info and getattr(_tick_info, 'ask', 0) > 0:
                            _tick_bid = _tick_info.bid
                            _tick_ask = _tick_info.ask
                            _tick_price = _tick_ask if trade_signal.direction == 'long' else _tick_bid
                            _tick_dev = abs(_tick_price - (trade_signal.entry_price or current_price))
                            
                            # Check if price has moved too far from Claude's entry (>0.5x ATR)
                            _tick_atr = market_data.get('atr_14', 0)
                            _tick_max_dev = _tick_atr * 0.5 if _tick_atr > 0 else (trade_signal.entry_price or current_price) * 0.003
                            
                            if _tick_dev > _tick_max_dev and _tick_max_dev > 0:
                                # Price moved significantly — recalculate R:R
                                _new_entry = _tick_price
                                _new_sl_dist = abs(_new_entry - _final_sl)
                                _new_tp_dist = abs(_final_tp - _new_entry)
                                _new_rr = _new_tp_dist / _new_sl_dist if _new_sl_dist > 0 else 0
                                
                                if _new_rr < 1.5:
                                    logger.warning(
                                        f"[TICK-REFINE] {symbol}: Price moved {_tick_dev:.5f} from entry "
                                        f"(>{_tick_max_dev:.5f} limit). New R:R={_new_rr:.2f} < 1.5. Skipping."
                                    )
                                    print(
                                        f"[TICK-REFINE] {symbol}: BLOCKED — price slipped {_tick_dev:.5f}, "
                                        f"R:R dropped to {_new_rr:.2f}:1",
                                        flush=True
                                    )
                                    _tick_ok = False
                                else:
                                    trade_signal.entry_price = _new_entry
                                    logger.info(
                                        f"[TICK-REFINE] {symbol}: Entry adjusted to live tick "
                                        f"{_new_entry:.5f} (was {current_price:.5f}), R:R={_new_rr:.2f}"
                                    )
                    except Exception as _tick_err:
                        logger.debug(f"[TICK-REFINE] Error for {symbol}: {_tick_err}")
                    
                    if not _tick_ok:
                        return
                    
                    logger.info(f"Executing MARKET order (AMD: {trade_signal.amd_phase})")
                    
                    result = await self.order_manager.place_market_order(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        volume=position_size.lots,
                        stop_loss=_final_sl,
                        take_profit=_final_tp,
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
                    
                    # Cancel existing pending orders for the same symbol+direction to prevent pile-up
                    existing_orders = [
                        o for o in self.pending_order_manager.get_active_orders(symbol=symbol)
                        if o.direction == trade_signal.direction
                    ]
                    for old_order in existing_orders:
                        old_success = await self.pending_order_manager.cancel_order(
                            old_order.ticket, reason="replaced_by_newer"
                        )
                        if old_success:
                            self.daily_trades = max(0, self.daily_trades - 1)
                            # Reclaim risk budget for the old order (use stored risk, not default)
                            if hasattr(self, 'risk_manager') and self.risk_manager:
                                _risk_pct = getattr(old_order, 'risk_percent', None) or self.risk_manager.risk_per_trade
                                self.risk_manager.update_daily_risk(-_risk_pct)
                            # Clear old signal hash
                            old_hash = self._get_signal_hash(symbol, old_order.direction, old_order.price)
                            self._recent_signal_hashes.discard(old_hash)
                            self._signal_hash_expiry.pop(old_hash, None)
                            print(
                                f"[PENDING] Cancelled old #{old_order.ticket} {symbol} {old_order.direction} "
                                f"@ {old_order.price} — replaced by newer signal @ {entry_price}",
                                flush=True
                            )
                    
                    result = await self.order_manager.place_pending_order(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        order_type=order_type,
                        volume=position_size.lots,
                        price=entry_price,
                        stop_loss=_final_sl,
                        take_profit=_final_tp,
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
                            stop_loss=_final_sl,
                            take_profit=_final_tp,
                            expiration_minutes=expiration_minutes,
                            risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else None,
                        )
                else:
                    # Fallback to market order if order type unclear
                    logger.info(f"📈 Executing MARKET order (fallback from {order_type})")
                    
                    result = await self.order_manager.place_market_order(
                        symbol=symbol,
                        direction=trade_signal.direction,
                        volume=position_size.lots,
                        stop_loss=_final_sl,
                        take_profit=_final_tp,
                        comment="ICT_Bot"
                    )
                
                if result.success:
                    # Trade slot was already reserved above (daily_trades incremented)
                    
                    # Update daily risk tracking so the risk limit is enforced
                    # Use the scaling manager's risk_percent (the intended risk per trade),
                    # NOT a manual price-based calculation which can be wildly wrong for crypto/indices.
                    if hasattr(self, 'risk_manager') and self.risk_manager:
                        _risk_pct = size_result.risk_percent if hasattr(size_result, 'risk_percent') else self.risk_manager.risk_per_trade
                        self.risk_manager.update_daily_risk(_risk_pct)
                        print(f"[RISK] {symbol}: Daily risk +{_risk_pct*100:.1f}%, total: {self.risk_manager.daily_risk_used*100:.1f}%/{self.risk_manager.max_daily_risk*100:.0f}%", flush=True)
                    
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
                            asyncio.create_task(broadcast_trade_update({
                                "event": "pending_order_placed",
                                "ticket": result.ticket,
                                "symbol": symbol,
                                "order_type": order_type,
                                "direction": trade_signal.direction,
                                "entry_price": entry_price,
                                "stop_loss": trade_signal.stop_loss,
                                "take_profit": trade_signal.take_profit,
                                "lots": position_size.lots,
                                "confidence": trade_signal.confidence
                            }))
                            
                            # Save to database with full analysis context
                            await save_trade_to_db(
                                ticket=result.ticket,
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=entry_price,
                                stop_loss=trade_signal.stop_loss or 0.0,
                                take_profit=trade_signal.take_profit or 0.0,
                                position_size=position_size.lots,
                                confidence=trade_signal.confidence,
                                reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                                judge_verdict=judge_verdict.get('verdict', 'APPROVE') if judge_verdict else None,
                                judge_reason=judge_verdict.get('reason', '') if judge_verdict else None,
                                judge_risk_flags=judge_verdict.get('risk_flags', []) if judge_verdict else None,
                                trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                                order_type=order_type,
                                amd_phase=getattr(trade_signal, 'amd_phase', 'unknown'),
                                market_structure=getattr(trade_signal, 'market_structure', ''),
                                confluence_factors=confluence_factors if confluence_factors else None,
                                confluence_count=confluence_count if confluence_count else None,
                                ict_concepts={
                                    'order_blocks': getattr(trade_signal, 'order_blocks', []),
                                    'fvg_zones': getattr(trade_signal, 'fvg_zones', []),
                                    'liquidity_targets': getattr(trade_signal, 'liquidity_targets', []),
                                    'manipulation_complete': getattr(trade_signal, 'manipulation_complete', False),
                                },
                                timeframe="M15",
                                session_name=self.kill_zone_checker.get_current_session().session_name if self.kill_zone_checker else "",
                                risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else self.risk_manager.risk_per_trade,
                            )
                            
                            # Pending orders: do NOT send Telegram notification.
                            # These get cancelled/replaced frequently and would spam
                            # the user. Only notify when a trade actually fills.
                            logger.info(f"Pending order placed for {symbol} — Telegram notification deferred until fill")
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
                            asyncio.create_task(broadcast_trade_update({
                                "event": "trade_opened",
                                "ticket": result.ticket,
                                "symbol": symbol,
                                "direction": trade_signal.direction,
                                "entry_price": result.fill_price or current_price,
                                "stop_loss": trade_signal.stop_loss,
                                "take_profit": trade_signal.take_profit,
                                "lots": position_size.lots,
                                "confidence": trade_signal.confidence
                            }))
                            
                            # Save trade to database with full analysis context
                            await save_trade_to_db(
                                ticket=result.ticket,
                                symbol=symbol,
                                direction=trade_signal.direction,
                                entry_price=result.fill_price or current_price,
                                stop_loss=trade_signal.stop_loss or 0.0,
                                take_profit=trade_signal.take_profit or 0.0,
                                position_size=position_size.lots,
                                confidence=trade_signal.confidence,
                                reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                                judge_verdict=judge_verdict.get('verdict', 'APPROVE') if judge_verdict else None,
                                judge_reason=judge_verdict.get('reason', '') if judge_verdict else None,
                                judge_risk_flags=judge_verdict.get('risk_flags', []) if judge_verdict else None,
                                trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                                order_type=order_type,
                                amd_phase=getattr(trade_signal, 'amd_phase', 'unknown'),
                                market_structure=getattr(trade_signal, 'market_structure', ''),
                                confluence_factors=confluence_factors if confluence_factors else None,
                                confluence_count=confluence_count if confluence_count else None,
                                ict_concepts={
                                    'order_blocks': getattr(trade_signal, 'order_blocks', []),
                                    'fvg_zones': getattr(trade_signal, 'fvg_zones', []),
                                    'liquidity_targets': getattr(trade_signal, 'liquidity_targets', []),
                                    'manipulation_complete': getattr(trade_signal, 'manipulation_complete', False),
                                },
                                timeframe="M15",
                                session_name=self.kill_zone_checker.get_current_session().session_name if self.kill_zone_checker else "",
                                risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else self.risk_manager.risk_per_trade,
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
            strategy_context = self.context_builder.get_ict_context()
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
            asyncio.create_task(broadcast_analysis_update(symbol, {
                "direction": trade_signal.direction,
                "confidence": trade_signal.confidence,
                "rr_ratio": trade_signal.risk_reward,
                "entry_price": trade_signal.entry_price,
                "stop_loss": trade_signal.stop_loss,
                "take_profit": trade_signal.take_profit,
                "market_structure": trade_signal.market_structure
            }))
            
            logger.info(f"[SIMULATION] Analysis complete for {symbol}: {trade_signal.direction} ({trade_signal.confidence:.0%})")
            
        except Exception as e:
            logger.error(f"Error in analysis-only mode for {symbol}: {e}")
    
    async def _generate_chart_image(
        self, df, symbol: str, timeframe: Optional[str] = None,
        order_blocks=None, fvg_zones=None, liquidity_levels=None, swing_points=None
    ) -> Optional[str]:
        """Generate a chart image and return as base64.
        
        Runs in a thread pool to avoid blocking the event loop (matplotlib is sync/CPU-bound).
        Accepts optional ICT overlay data to draw on the chart.
        """
        try:
            from .utils.chart_screenshot import create_simple_chart
            if create_simple_chart:
                tf_label = timeframe or settings.timeframes.execution_tf
                kwargs = {}
                if order_blocks:
                    kwargs['order_blocks'] = order_blocks
                if fvg_zones:
                    kwargs['fvg_zones'] = fvg_zones
                if liquidity_levels:
                    kwargs['liquidity_levels'] = liquidity_levels
                if swing_points:
                    kwargs['swing_points'] = swing_points
                chart_base64 = await asyncio.to_thread(
                    create_simple_chart,
                    df, 
                    symbol, 
                    tf_label,
                    **kwargs
                )
                if chart_base64:
                    return chart_base64
        except ImportError:
            logger.warning("Chart screenshot module not available")
        except Exception as e:
            logger.warning(f"Error generating chart: {e}")
        
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
                    NotificationType.TRADE_CLOSED,
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
        Returns APPROVE (proceed as-is), DEMOTE (convert to pending limit),
        or REJECT (skip entirely).
        
        Fails closed: timeout or error returns DEMOTE so marginal trades
        are forced to pending limit orders rather than going straight to market.
        """
        default_demote = {"verdict": "DEMOTE", "reason": "Judge timeout/error — defaulting to limit order", "suggested_entry": None, "risk_flags": ["judge_unavailable"]}
        
        if not self.claude_client or not self.claude_client.api_key:
            return default_demote
        
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
            _lots = getattr(position_size, 'lots', 0.01)
            _at_broker_minimum = False
            if account_balance > 0 and sl_distance > 0:
                from .config import get_symbol_spec
                spec = get_symbol_spec(symbol)
                risk_amount = sl_distance * _lots * spec.contract_size
                position_size_pct = risk_amount / account_balance
                # Check if we're at the broker's minimum lot size
                _at_broker_minimum = (_lots <= spec.volume_min)
            
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
                'at_broker_minimum_lots': _at_broker_minimum,
                'trades_today': self.daily_trades,
                'max_daily_trades': settings.trading.max_daily_trades if hasattr(settings, 'trading') else 5,
                'session': session_name,
                'symbol_category': spec.category if spec else 'unknown',
                'sl_distance': sl_distance,
                'tp_distance': abs((trade_signal.take_profit or 0) - (trade_signal.entry_price or current_price)),
            }
            
            # Get learning context (past mistakes and winning patterns)
            learning_context = ""
            if self.learning_service:
                try:
                    learning_context = await self.learning_service.build_context_for_claude(symbol, session_name)
                except Exception as e:
                    logger.debug(f"[JUDGE] Could not get learning context: {e}")
            
            # Call the judge with a timeout — fail closed (DEMOTE on timeout)
            verdict = await asyncio.wait_for(
                self.claude_client.judge_trade(signal_dict, risk_metrics, learning_context),
                timeout=8.0
            )
            
            logger.info(
                f"[JUDGE] {symbol} {trade_signal.direction}: {verdict.get('verdict', 'APPROVE')} "
                f"— {verdict.get('reason', 'N/A')} | flags: {verdict.get('risk_flags', [])}"
            )
            
            return verdict
            
        except asyncio.TimeoutError:
            logger.warning(f"[JUDGE] Timeout for {symbol} — failing closed (DEMOTE to limit order)")
            return default_demote
        except Exception as e:
            logger.warning(f"[JUDGE] Error for {symbol}: {e} — failing closed (DEMOTE to limit order)")
            return default_demote
    
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
            
            # Track daily start balance for daily drawdown reference point.
            # The actual drawdown comparison below uses equity (includes floating P/L)
            # to prevent false kill-switches when open positions dip temporarily.
            if not hasattr(self, '_daily_start_balance'):
                self._daily_start_balance = account.balance
            
            # Reset daily start balance on new day
            today = datetime.now(timezone.utc).date()
            if hasattr(self, '_drawdown_date') and self._drawdown_date != today:
                self._daily_start_balance = account.balance
                self._drawdown_date = today
            elif not hasattr(self, '_drawdown_date'):
                self._drawdown_date = today
            
            # Use configured drawdown limits
            max_daily_dd = settings.trading.max_daily_drawdown  # Default 3%
            max_weekly_dd = settings.trading.max_weekly_drawdown  # Default 6%
            
            # Use EQUITY (includes floating P/L) not balance for drawdown checks.
            # Balance ignores unrealized profits from open trades, causing false
            # kill-switch triggers when open positions dip temporarily.
            current_value = account.equity
            
            daily_drawdown = 0.0
            if self._daily_start_balance > 0:
                daily_drawdown = (self._daily_start_balance - current_value) / self._daily_start_balance
                daily_drawdown = max(0.0, daily_drawdown)  # Clamp: profit is not drawdown
            
            weekly_drawdown = 0.0
            if self.scaling_manager:
                weekly_drawdown = self.scaling_manager.calculate_weekly_drawdown(current_value)
            
            # Log drawdown values for debugging (only when significant)
            if daily_drawdown > 0.01 or weekly_drawdown > 0.01:
                logger.info(
                    f"Drawdown check (EQUITY): balance=${account.balance:.2f}, equity=${account.equity:.2f}, "
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
                    logger.info("🎉 GOAL REACHED! $10,000 equity achieved!")
                    
                    from .api.routes.activity import add_activity
                    add_activity(
                        "goal_reached",
                        "🎉 $10,000 EQUITY GOAL REACHED!",
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
            
            print(f"[POS-REEVAL] Claude re-evaluating {len(positions)} open position(s)...", flush=True)
            logger.info(f"Claude re-evaluating {len(positions)} open positions...")
            
            # Clean up stale entries for positions that no longer exist
            active_tickets = {p.ticket for p in positions}
            stale = [t for t in self._position_reeval_state if t not in active_tickets]
            for t in stale:
                del self._position_reeval_state[t]
            
            for position in positions:
                try:
                    _pos_age = ""
                    if position.open_time:
                        _pos_age = f"{(datetime.now() - position.open_time).total_seconds() / 60:.0f}min"
                    _pos_pnl = getattr(position, 'unrealized_pnl', None) or 0
                    print(
                        f"[POS-REEVAL] #{position.ticket} {position.symbol} {position.direction} "
                        f"entry={position.entry_price:.5f} SL={position.stop_loss} TP={position.take_profit} "
                        f"P/L=${_pos_pnl:.2f} age={_pos_age}",
                        flush=True
                    )
                    
                    # Throttle: if last decision was HOLD and cooldown hasn't elapsed, skip
                    last_state = self._position_reeval_state.get(position.ticket)
                    if last_state and last_state.get("decision") == "HOLD":
                        since_last = (datetime.now() - last_state["time"]).total_seconds()
                        _hrs = last_state.get("hours_open", 0)
                        if _hrs > 8:
                            cooldown_secs = 900
                        elif _hrs > 2:
                            cooldown_secs = 600
                        else:
                            cooldown_secs = 300
                        
                        if since_last < cooldown_secs:
                            remaining = cooldown_secs - since_last
                            print(f"[POS-REEVAL] #{position.ticket} SKIP — HOLD cooldown ({remaining:.0f}s remaining)", flush=True)
                            continue
                    
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
                    
                    # Gather enriched context for better re-evaluation
                    _reeval_extra = ""
                    try:
                        # HTF bias
                        if hasattr(self, '_last_mtf_results') and position.symbol in self._last_mtf_results:
                            _mtf = self._last_mtf_results[position.symbol]
                            _reeval_extra += f"\n## Higher Timeframe Context\n"
                            _reeval_extra += f"- D1 Bias: {_mtf.get('d1_bias', 'unknown')}\n"
                            _reeval_extra += f"- H4 Bias: {_mtf.get('h4_bias', 'unknown')}\n"
                            _reeval_extra += f"- HTF Alignment: {_mtf.get('alignment', 'unknown')}\n"
                    except Exception:
                        pass
                    try:
                        # Learning context
                        if hasattr(self, 'learning_service') and self.learning_service:
                            _learn_ctx = await self.learning_service.build_context_for_claude(
                                symbol=position.symbol,
                                direction=position.direction,
                                trade_type=getattr(position, 'trade_type', 'intraday'),
                            )
                            if _learn_ctx:
                                _reeval_extra += f"\n## Lessons from Past Trades\n{_learn_ctx[:800]}\n"
                    except Exception:
                        pass
                    try:
                        # Real spread
                        _sym_info = await self.mt5_client.get_symbol_info(position.symbol)
                        if _sym_info and getattr(_sym_info, 'ask', 0) > 0:
                            _spread = _sym_info.ask - _sym_info.bid
                            _spread_pct = _spread / ((_sym_info.ask + _sym_info.bid) / 2) * 100
                            _reeval_extra += f"\n- Current Spread: {_spread:.5f} ({_spread_pct:.3f}%)\n"
                    except Exception:
                        pass
                    
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
{_reeval_extra}

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
                    
                    # Generate chart image for visual context
                    chart_content = []
                    try:
                        chart_b64 = await self._generate_chart_image(
                            df, position.symbol, settings.timeframes.execution_tf
                        )
                        if chart_b64:
                            chart_content.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": chart_b64
                                }
                            })
                    except Exception as chart_err:
                        logger.debug(f"Could not generate chart for reeval: {chart_err}")
                    
                    chart_content.append({
                        "type": "text",
                        "text": position_context
                    })
                    
                    # Get Claude's recommendation (with timeout and validation)
                    try:
                        response = await asyncio.wait_for(
                            self.claude_client.async_client.messages.create(
                                model=self.claude_client.model_light,
                                max_tokens=300,
                                messages=[{
                                    "role": "user",
                                    "content": chart_content
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
                    
                    raw_reeval = response.content[0].text.strip()
                    recommendation = raw_reeval.upper().replace("*", "").replace("#", "").strip()
                    
                    logger.info(f"Claude recommendation for {position.symbol}: {recommendation[:100]}")
                    
                    if recommendation.startswith("CLOSE"):
                        decision = "CLOSE"
                    elif "TIGHTEN" in recommendation:
                        decision = "TIGHTEN"
                    else:
                        decision = "HOLD"
                    
                    print(
                        f"[POS-REEVAL] #{position.ticket} {position.symbol}: {decision} — "
                        f"{raw_reeval[:120]}",
                        flush=True
                    )
                    
                    # Record the re-eval state for throttling (cooldown before next call)
                    self._position_reeval_state[position.ticket] = {
                        "decision": decision,
                        "time": datetime.now(),
                        "hours_open": hours_open,
                    }
                    
                    # Log Claude re-evaluation to bot_state for frontend display
                    # Build a concise summary line (avoids repetitive raw Claude text)
                    direction_arrow = "LONG" if position.direction == "long" else "SHORT"
                    summary = f"{decision} #{position.ticket} {position.symbol} {direction_arrow} | {r_mult:+.1f}R ${pnl:+.2f} | {hours_open:.1f}h"
                    
                    from .api.routes.bot_status import bot_state
                    bot_state._add_log(
                        "claude_reeval",
                        position.symbol,
                        summary,
                        {
                            "decision": decision,
                            "ticket": position.ticket,
                            "symbol": position.symbol,
                            "direction": position.direction,
                            "r_multiple": round(r_mult, 2),
                            "pnl": round(pnl, 2),
                            "hours_open": round(hours_open, 1),
                            "reasoning": recommendation,
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
                        cur_r = position.current_r_multiple
                        if cur_r > 0.5 and position.risk_pips > 0:
                            # Lock actual profit based on current R-multiple
                            if cur_r > 2.0:
                                lock_pct = 0.75  # Lock 75% of profit
                            elif cur_r > 1.5:
                                lock_pct = 0.60  # Lock 60% of profit
                            elif cur_r > 1.0:
                                lock_pct = 0.50  # Lock 50% of profit
                            else:
                                lock_pct = 0.0   # Below 1R, just break-even
                            
                            profit_distance = cur_r * position.risk_pips
                            locked_distance = profit_distance * lock_pct
                            
                            if position.direction == 'long':
                                new_sl = position.entry_price + locked_distance
                                new_sl = max(new_sl, position.stop_loss)  # Only improve SL
                            else:
                                new_sl = position.entry_price - locked_distance
                                new_sl = min(new_sl, position.stop_loss)  # Only improve SL
                            
                            if new_sl != position.stop_loss:
                                result = await self.order_manager.modify_order(
                                    ticket=position.ticket,
                                    stop_loss=new_sl
                                )
                                
                                if result.success:
                                    old_sl = position.stop_loss
                                    position.stop_loss = new_sl
                                    logger.info(
                                        f"[TIGHTEN] {position.ticket} ({position.symbol}): "
                                        f"SL {old_sl:.5f} -> {new_sl:.5f} "
                                        f"(locking {lock_pct:.0%} of {cur_r:.2f}R profit)"
                                    )
                            else:
                                logger.info(
                                    f"[TIGHTEN] {position.ticket}: SL already at or better than "
                                    f"lock level {new_sl:.5f}"
                                )
                    
                except Exception as e:
                    import traceback as _tb
                    logger.error(f"Error re-evaluating position {position.ticket}: {e}\n{_tb.format_exc()}")
                    
        except Exception as e:
            import traceback as _tb
            logger.error(f"Error in Claude re-evaluation: {e}\n{_tb.format_exc()}")
    
    async def _claude_reevaluate_pending_orders(self):
        """
        Re-evaluate pending orders against current market conditions.
        
        Two-tier approach:
        1. FAST CHECK: If the latest signal direction for a symbol has flipped
           vs the pending order direction, cancel immediately (no Claude call).
        2. CLAUDE CHECK: If the order has been sitting for over 1 hour without
           filling and direction still matches, ask Claude whether to KEEP or CANCEL.
        """
        try:
            if not hasattr(self, 'pending_order_manager') or not self.pending_order_manager:
                return
            
            active_orders = self.pending_order_manager.get_active_orders()
            if not active_orders:
                return
            
            logger.info(f"Re-evaluating {len(active_orders)} pending order(s)...")
            
            cancelled_count = 0
            kept_count = 0
            
            print(f"[PENDING-REEVAL] Checking {len(active_orders)} pending order(s)...", flush=True)
            
            for order in active_orders:
                try:
                    symbol = order.symbol
                    order_direction = order.direction  # 'long' or 'short'
                    age_str = f"{(datetime.now() - order.created_at).total_seconds() / 60:.0f}min"
                    print(
                        f"[PENDING-REEVAL] #{order.ticket} {symbol} {order.order_type} "
                        f"@ {order.price:.5f} | dir={order_direction} | age={age_str} "
                        f"| SL={order.stop_loss:.5f} TP={order.take_profit:.5f}",
                        flush=True
                    )
                    
                    # ── TIER 1: Direction flip check (instant, no Claude call) ──
                    latest_signal = self._last_signal_per_symbol.get(symbol)
                    
                    if latest_signal and latest_signal.get('direction') not in (None, 'no_trade'):
                        latest_direction = latest_signal['direction']
                        latest_confidence = latest_signal.get('confidence', 0)
                        
                        if latest_direction != order_direction:
                            # Direction has flipped — cancel immediately
                            print(
                                f"[PENDING-REEVAL] CANCEL #{order.ticket} {symbol} {order.order_type} "
                                f"— direction flipped: order={order_direction.upper()}, "
                                f"latest signal={latest_direction.upper()} ({latest_confidence:.0%})",
                                flush=True
                            )
                            
                            success = await self.pending_order_manager.cancel_order(
                                order.ticket, reason=f"reeval_direction_flip ({order_direction}->{latest_direction})"
                            )
                            
                            if success:
                                cancelled_count += 1
                                
                                # Reclaim daily risk budget (use stored risk, not default)
                                if hasattr(self, 'risk_manager') and self.risk_manager:
                                    _risk_pct = getattr(order, 'risk_percent', None) or self.risk_manager.risk_per_trade
                                    self.risk_manager.update_daily_risk(-_risk_pct)
                                    print(
                                        f"[RISK] {symbol}: Daily risk reclaimed -{_risk_pct*100:.1f}%, "
                                        f"total: {self.risk_manager.daily_risk_used*100:.1f}%/{self.risk_manager.max_daily_risk*100:.0f}%",
                                        flush=True
                                    )
                                
                                # Free the daily trade slot (pending never filled)
                                self.daily_trades = max(0, self.daily_trades - 1)
                                print(
                                    f"[TRADES] {symbol}: Trade slot freed, "
                                    f"daily_trades={self.daily_trades}/{settings.trading.max_daily_trades}",
                                    flush=True
                                )
                                
                                # Clear signal hash so Claude can re-enter this setup
                                old_hash = self._get_signal_hash(symbol, order_direction, order.price)
                                self._recent_signal_hashes.discard(old_hash)
                                self._signal_hash_expiry.pop(old_hash, None)
                                
                                # Log to activity feed
                                from .api.routes.activity import add_activity
                                add_activity(
                                    "pending_order_cancelled",
                                    f"Cancelled {order.order_type} {symbol} #{order.ticket} — direction flipped to {latest_direction.upper()}",
                                    symbol,
                                    {
                                        "ticket": order.ticket,
                                        "reason": "direction_flip",
                                        "order_direction": order_direction,
                                        "latest_direction": latest_direction,
                                    }
                                )
                                
                                # Log to bot_state
                                from .api.routes.bot_status import bot_state
                                bot_state._add_log(
                                    "pending_reeval",
                                    symbol,
                                    f"CANCEL #{order.ticket} {symbol} {order_direction.upper()} — flipped to {latest_direction.upper()}",
                                    {"ticket": order.ticket, "decision": "CANCEL", "reason": "direction_flip"}
                                )
                            else:
                                print(f"[PENDING-REEVAL] FAILED to cancel #{order.ticket} {symbol} via MT5", flush=True)
                            continue
                    
                    # ── TIER 1.5: "Price ran away" — cancel stale limit & enter at market ──
                    # If we placed a buy_limit below market hoping for a dip, but price
                    # ran UP and our thesis was right, the limit will never fill. Detect
                    # this and convert to a market entry while the move is still live.
                    age_minutes = (datetime.now() - order.created_at).total_seconds() / 60
                    
                    # Get current price (needed for both Tier 1.5 and Tier 2)
                    current_price = 0.0
                    try:
                        df = await self.data_fetcher.get_ohlcv(
                            symbol=symbol, timeframe="M5", count=1
                        )
                        if df is not None and not df.empty:
                            current_price = float(df['close'].iloc[-1])
                    except Exception:
                        pass
                    
                    if current_price > 0 and age_minutes >= 10:
                        # Calculate how far price has moved AWAY from our limit
                        # (favorably — in the direction of the trade)
                        if order_direction == 'long' and order.order_type in ('buy_limit',):
                            # Buy limit is below market; price moved UP away from our limit
                            move_pct = (current_price - order.price) / order.price * 100
                        elif order_direction == 'short' and order.order_type in ('sell_limit',):
                            # Sell limit is above market; price moved DOWN away from our limit
                            move_pct = (order.price - current_price) / order.price * 100
                        else:
                            move_pct = 0  # buy_stop/sell_stop — not relevant
                        
                        # If price moved >0.5% favorably past our limit AND the trade
                        # thesis is still valid (TP hasn't been hit, SL hasn't been hit)
                        tp_ok = True
                        sl_ok = True
                        if order.take_profit and order.stop_loss:
                            if order_direction == 'long':
                                tp_ok = current_price < order.take_profit  # haven't reached TP yet
                                sl_ok = current_price > order.stop_loss    # above SL
                            else:
                                tp_ok = current_price > order.take_profit
                                sl_ok = current_price < order.stop_loss
                        
                        if move_pct >= 0.5 and tp_ok and sl_ok:
                            # Check if we already have an open position for this symbol+direction
                            # to avoid stacking duplicate positions from old pending orders
                            existing_positions = [
                                p for p in self.position_manager.get_all_positions()
                                if p.symbol == symbol and p.direction == order_direction
                            ]
                            if existing_positions:
                                # Already have a position — just cancel the stale limit, don't open another
                                cancel_ok = await self.pending_order_manager.cancel_order(
                                    order.ticket, reason=f"duplicate_position (already have {symbol} {order_direction})"
                                )
                                if cancel_ok:
                                    cancelled_count += 1
                                    self.daily_trades = max(0, self.daily_trades - 1)
                                    if hasattr(self, 'risk_manager') and self.risk_manager:
                                        _risk_pct = getattr(order, 'risk_percent', None) or self.risk_manager.risk_per_trade
                                        self.risk_manager.update_daily_risk(-_risk_pct)
                                    old_hash = self._get_signal_hash(symbol, order_direction, order.price)
                                    self._recent_signal_hashes.discard(old_hash)
                                    self._signal_hash_expiry.pop(old_hash, None)
                                    print(
                                        f"[PENDING-REEVAL] CANCEL #{order.ticket} {symbol} {order.order_type} "
                                        f"— already have open {order_direction} position, skipping upgrade",
                                        flush=True
                                    )
                                continue
                            
                            print(
                                f"[PENDING-REEVAL] UPGRADE #{order.ticket} {symbol} {order.order_type} → MARKET "
                                f"| price ran {move_pct:.1f}% favorably (limit={order.price:.5f}, now={current_price:.5f}) "
                                f"| age={age_minutes:.0f}min",
                                flush=True
                            )
                            
                            # Cancel the stale pending order
                            cancel_ok = await self.pending_order_manager.cancel_order(
                                order.ticket, reason=f"upgrade_to_market (price ran {move_pct:.1f}% away)"
                            )
                            
                            if cancel_ok:
                                cancelled_count += 1
                                
                                # Free the trade slot and risk (will be re-used by the market order)
                                self.daily_trades = max(0, self.daily_trades - 1)
                                _risk_pct = 0
                                if hasattr(self, 'risk_manager') and self.risk_manager:
                                    _risk_pct = getattr(order, 'risk_percent', None) or self.risk_manager.risk_per_trade
                                    self.risk_manager.update_daily_risk(-_risk_pct)
                                old_hash = self._get_signal_hash(symbol, order_direction, order.price)
                                self._recent_signal_hashes.discard(old_hash)
                                self._signal_hash_expiry.pop(old_hash, None)
                                
                                # Rebase SL/TP from original limit price to current market price
                                _orig_entry = order.price
                                _new_sl = order.stop_loss
                                _new_tp = order.take_profit
                                if _orig_entry and _orig_entry > 0:
                                    _sl_offset = order.stop_loss - _orig_entry if order.stop_loss else 0
                                    _tp_offset = order.take_profit - _orig_entry if order.take_profit else 0
                                    if _sl_offset != 0:
                                        _new_sl = current_price + _sl_offset
                                    if _tp_offset != 0:
                                        _new_tp = current_price + _tp_offset
                                    print(
                                        f"[PENDING-REEVAL] {symbol}: Rebased SL/TP from limit@{_orig_entry:.5f} "
                                        f"to market@{current_price:.5f} — "
                                        f"SL: {order.stop_loss:.5f}->{_new_sl:.5f}, "
                                        f"TP: {order.take_profit:.5f}->{_new_tp:.5f}",
                                        flush=True
                                    )
                                
                                try:
                                    market_result = await self.order_manager.place_market_order(
                                        symbol=symbol,
                                        direction=order_direction,
                                        volume=order.volume,
                                        stop_loss=_new_sl,
                                        take_profit=_new_tp,
                                        comment="ICT_Bot_Upgrade"
                                    )
                                    
                                    if market_result.success:
                                        # Re-reserve the trade slot and risk
                                        self.daily_trades += 1
                                        if hasattr(self, 'risk_manager') and self.risk_manager and _risk_pct > 0:
                                            self.risk_manager.update_daily_risk(_risk_pct)
                                        
                                        fill_ticket = market_result.ticket or market_result.order_id
                                        fill_price = market_result.fill_price or current_price
                                        print(
                                            f"[PENDING-REEVAL] MARKET FILL #{fill_ticket} {symbol} {order_direction.upper()} "
                                            f"@ {fill_price:.5f} (was limit @ {order.price:.5f})",
                                            flush=True
                                        )
                                        
                                        # Create Position object so position_manager tracks it
                                        try:
                                            upgraded_pos = Position(
                                                ticket=fill_ticket,
                                                symbol=symbol,
                                                direction=order_direction,
                                                volume=order.volume,
                                                entry_price=fill_price,
                                                stop_loss=_new_sl or fill_price,
                                                take_profit=_new_tp or 0,
                                                open_time=datetime.now(),
                                            )
                                            upgraded_pos.trade_type = 'intraday'
                                            upgraded_pos.order_ticket = ticket
                                            self.position_manager.add_position(upgraded_pos)
                                        except Exception as pos_err:
                                            logger.warning(f"[PENDING-REEVAL] Could not create Position for upgrade: {pos_err}")
                                        
                                        # Save to DB
                                        try:
                                            await save_trade_to_db(
                                                ticket=fill_ticket,
                                                symbol=symbol,
                                                direction=order_direction,
                                                entry_price=fill_price,
                                                stop_loss=_new_sl or 0,
                                                take_profit=_new_tp or 0,
                                                position_size=order.volume,
                                                confidence=0.0,
                                                reasoning=f"Upgraded from pending {order.order_type} @ {order.price:.5f}",
                                                order_type="market",
                                                risk_percent=getattr(order, 'risk_percent', None) or (_risk_pct if _risk_pct > 0 else None),
                                            )
                                        except Exception as db_err:
                                            logger.warning(f"[PENDING-REEVAL] DB save for upgrade failed: {db_err}")
                                        
                                        from .api.routes.activity import add_activity
                                        add_activity(
                                            "pending_upgraded_to_market",
                                            f"Upgraded {symbol} {order_direction} to market — price ran {move_pct:.1f}% past limit",
                                            symbol,
                                            {
                                                "old_ticket": order.ticket,
                                                "new_ticket": fill_ticket,
                                                "limit_price": order.price,
                                                "market_price": current_price,
                                                "move_pct": round(move_pct, 2),
                                            }
                                        )
                                    else:
                                        print(
                                            f"[PENDING-REEVAL] MARKET ENTRY FAILED for {symbol}: {getattr(market_result, 'error', 'unknown')}",
                                            flush=True
                                        )
                                except Exception as mkt_err:
                                    print(f"[PENDING-REEVAL] MARKET ENTRY ERROR for {symbol}: {mkt_err}", flush=True)
                            
                            continue  # Done with this order
                    
                    # ── TIER 2: Claude re-eval for aged orders ──
                    if age_minutes < 15:
                        kept_count += 1
                        print(
                            f"[PENDING-REEVAL] KEEP #{order.ticket} {symbol} {order.order_type} "
                            f"@ {order.price:.5f} — fresh ({age_minutes:.0f}min, re-eval at 15min)",
                            flush=True
                        )
                        continue
                    
                    # Order has been sitting for over 1 hour — ask Claude
                    if not self.claude_client or not self.claude_client.api_key:
                        kept_count += 1
                        continue
                    
                    # Distance from current price to order entry
                    if current_price > 0:
                        distance_pct = abs(order.price - current_price) / current_price * 100
                    else:
                        distance_pct = 0
                    
                    prompt = f"""## Pending Order Re-evaluation

- Symbol: {symbol}
- Order Type: {order.order_type} ({order_direction.upper()})
- Entry Price: {order.price}
- Stop Loss: {order.stop_loss}
- Take Profit: {order.take_profit}
- Current Price: {current_price}
- Distance to Entry: {distance_pct:.2f}%
- Age: {age_minutes:.0f} minutes (placed at {order.created_at.strftime('%H:%M')})
- Latest Signal: {latest_signal.get('direction', 'unknown').upper() if latest_signal else 'N/A'} @ {latest_signal.get('confidence', 0):.0%} confidence

## Question
This pending order has been waiting {age_minutes:.0f} minutes without filling.
Should we KEEP it or CANCEL it?

- KEEP: The setup is still valid, price may still reach the entry level.
- CANCEL: Market has moved away, structure has changed, or the opportunity has passed.

Consider: Is price moving TOWARD or AWAY from the entry? Has the entry zone been invalidated?
Respond with KEEP or CANCEL and brief reasoning (1-2 sentences).
"""
                    # Generate chart for visual context
                    pending_chart_content = []
                    try:
                        _pending_df = await self.data_fetcher.get_ohlcv(
                            symbol=symbol, timeframe=settings.timeframes.execution_tf, count=100
                        )
                        if _pending_df is not None and not _pending_df.empty:
                            _pending_chart_b64 = await self._generate_chart_image(
                                _pending_df, symbol, settings.timeframes.execution_tf
                            )
                            if _pending_chart_b64:
                                pending_chart_content.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": _pending_chart_b64
                                    }
                                })
                    except Exception:
                        pass
                    
                    pending_chart_content.append({"type": "text", "text": prompt})
                    
                    try:
                        response = await asyncio.wait_for(
                            self.claude_client.async_client.messages.create(
                                model=self.claude_client.model_light,
                                max_tokens=200,
                                messages=[{"role": "user", "content": pending_chart_content}]
                            ),
                            timeout=20
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Pending order re-eval timed out for {symbol}")
                        kept_count += 1
                        continue
                    except Exception as api_err:
                        logger.warning(f"Pending order re-eval API error for {symbol}: {api_err}")
                        kept_count += 1
                        continue
                    
                    if not response or not hasattr(response, 'content') or not response.content:
                        kept_count += 1
                        continue
                    
                    raw_recommendation = response.content[0].text.strip()
                    # Strip markdown formatting (bold, etc.) before parsing
                    recommendation = raw_recommendation.upper().replace("*", "").replace("#", "").strip()
                    decision = "CANCEL" if "CANCEL" in recommendation else "KEEP"
                    
                    # Log to bot_state
                    from .api.routes.bot_status import bot_state
                    bot_state._add_log(
                        "pending_reeval",
                        symbol,
                        f"{decision} #{order.ticket} {symbol} {order_direction.upper()} | age={age_minutes:.0f}m dist={distance_pct:.1f}%",
                        {"ticket": order.ticket, "decision": decision, "age_minutes": round(age_minutes), "reasoning": recommendation}
                    )
                    
                    if decision == "CANCEL":
                        print(
                            f"[PENDING-REEVAL] CANCEL #{order.ticket} {symbol} {order.order_type} "
                            f"— Claude: {recommendation[:80]}",
                            flush=True
                        )
                        
                        success = await self.pending_order_manager.cancel_order(
                            order.ticket, reason=f"reeval_claude ({recommendation[:60]})"
                        )
                        
                        if success:
                            cancelled_count += 1
                            
                            # Reclaim daily risk budget (use stored risk, not default)
                            if hasattr(self, 'risk_manager') and self.risk_manager:
                                _risk_pct = getattr(order, 'risk_percent', None) or self.risk_manager.risk_per_trade
                                self.risk_manager.update_daily_risk(-_risk_pct)
                                print(
                                    f"[RISK] {symbol}: Daily risk reclaimed -{_risk_pct*100:.1f}%, "
                                    f"total: {self.risk_manager.daily_risk_used*100:.1f}%/{self.risk_manager.max_daily_risk*100:.0f}%",
                                    flush=True
                                )
                            
                            # Free the daily trade slot (pending never filled)
                            self.daily_trades = max(0, self.daily_trades - 1)
                            print(
                                f"[TRADES] {symbol}: Trade slot freed, "
                                f"daily_trades={self.daily_trades}/{settings.trading.max_daily_trades}",
                                flush=True
                            )
                            
                            # Clear signal hash so Claude can re-enter this setup
                            old_hash = self._get_signal_hash(symbol, order_direction, order.price)
                            self._recent_signal_hashes.discard(old_hash)
                            self._signal_hash_expiry.pop(old_hash, None)
                            
                            from .api.routes.activity import add_activity
                            add_activity(
                                "pending_order_cancelled",
                                f"Claude cancelled {order.order_type} {symbol} #{order.ticket} — {recommendation[:80]}",
                                symbol,
                                {"ticket": order.ticket, "reason": "claude_reeval", "recommendation": recommendation[:200]}
                            )
                        else:
                            print(f"[PENDING-REEVAL] FAILED to cancel #{order.ticket} {symbol} via MT5", flush=True)
                    else:
                        kept_count += 1
                        print(
                            f"[PENDING-REEVAL] KEEP #{order.ticket} {symbol} {order.order_type} "
                            f"— Claude: {recommendation[:80]}",
                            flush=True
                        )
                    
                except Exception as e:
                    logger.error(f"Error re-evaluating pending order {order.ticket}: {e}")
                    print(f"[PENDING-REEVAL] ERROR #{order.ticket} {symbol}: {e}", flush=True)
                    kept_count += 1
            
            if cancelled_count > 0 or kept_count > 0:
                print(
                    f"[PENDING-REEVAL] Done: {cancelled_count} cancelled, {kept_count} kept "
                    f"(of {len(active_orders)} active orders)",
                    flush=True
                )
                
        except Exception as e:
            logger.error(f"Error in pending order re-evaluation: {e}")
    
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
            
            # Clean up stale DB records BEFORE main sync to avoid noisy
            # "position closed" logs for positions that are long gone
            stale_removed = await self.position_manager.cleanup_stale_db_records(self.mt5_client)
            if stale_removed > 0:
                # Reload after cleanup so db_positions is fresh
                db_positions = await self.position_manager.load_from_db()
            
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
                    # Only import positions that were placed by this bot
                    # (identified by "ICT_Bot" in the comment field).
                    # This prevents phantom close notifications for manual or
                    # third-party trades that the bot never opened.
                    mt5_comment = getattr(mt5_pos, 'comment', '') or ''
                    if 'ICT_Bot' not in mt5_comment:
                        print(
                            f"[INIT] Skipping MT5 position {ticket} ({mt5_pos.symbol}) — "
                            f"not bot-placed (comment='{mt5_comment}')",
                            flush=True
                        )
                        continue
                    
                    # New bot position not in database - create and track
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
                    if hasattr(self, 'pending_order_manager') and self.pending_order_manager:
                        _orig_order = self.pending_order_manager.filled_order_map.get(ticket)
                        if _orig_order:
                            position.order_ticket = _orig_order
                            logger.info(f"  Linked position {ticket} to original order {_orig_order}")
                    self.position_manager.add_position(position)
                    logger.info(f"Added MT5 bot position {ticket} to tracking (comment='{mt5_comment}')")
            
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
                    today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())
                    today_deals = await self.mt5_client.get_history(today_start, datetime.now())
                    # Count only entry deals (entry=0 is trade-in) placed by THIS bot (magic=12345)
                    entry_deals = [
                        d for d in today_deals
                        if d.get('entry') == 0 and d.get('volume', 0) > 0 and d.get('magic') == 12345
                    ]
                    if entry_deals:
                        self.daily_trades = len(entry_deals)
                        logger.info(f"Initialized daily_trades from MT5 history: {self.daily_trades} bot trades today")
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
            
            # Check if any bot-placed trades in our DB closed on MT5 while we were offline.
            # This ONLY updates existing DB records (matched by ticket) — it does NOT import
            # new trades from MT5 history, which would contaminate the DB with old account trades.
            try:
                await self._sync_trade_history(days_back=7)
                print("[INIT] Trade close sync done (checked open DB trades against MT5 history)", flush=True)
            except Exception as e:
                print(f"[INIT] Trade close sync error (non-fatal): {e}", flush=True)
            
        except Exception as e:
            logger.error(f"Error syncing positions on startup: {e}")
            import traceback
            traceback.print_exc()
    
    async def _sync_trade_history(self, days_back: int = 1):
        """
        Sync closed trade history from MT5 to database.
        
        IMPORTANT: This only updates EXISTING trades in the DB that were opened
        by this bot but closed while the bot was briefly offline (e.g., TP/SL hit).
        It does NOT import new trades from MT5 history — that would contaminate the
        DB with old account trades that all share magic=12345.
        
        The bot records its own trades to the DB when it places them. This sync
        only fills in exit data (exit_price, exit_time, profit_loss) for trades
        that the bot opened but didn't see close.
        
        It also detects pending orders that no longer exist on MT5 (deleted/expired)
        and marks them as cancelled in the DB.
        
        Args:
            days_back: Number of days of history to check (default 1)
        """
        if not self.mt5_client or self.mt5_client.is_simulation:
            logger.debug("Skipping trade history sync - simulation mode or no MT5")
            return
        
        if not DB_AVAILABLE:
            self._last_history_sync = datetime.now()
            return
        
        try:
            # Find trades in our DB that need close data:
            # 1. Truly open trades (no exit_price)
            # 2. Trades wrongly marked as cancelled (exit_price == entry_price, profit_loss == 0)
            #    These happen when manual close removes position before sync detects the close.
            from sqlalchemy import or_, and_
            open_trade_tickets = {}  # trade_id -> TradeModel data
            async with async_session() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(TradeModel).where(
                        or_(
                            TradeModel.exit_price.is_(None),
                            TradeModel.exit_price == 0,
                            # Wrongly cancelled: exit == entry and P/L is zero
                            and_(
                                TradeModel.profit_loss == 0,
                                TradeModel.exit_price == TradeModel.entry_price,
                            )
                        )
                    )
                )
                open_db_trades = result.scalars().all()
                for t in open_db_trades:
                    if t.trade_id:
                        open_trade_tickets[str(t.trade_id)] = {
                            'symbol': t.symbol,
                            'direction': t.direction,
                        }
            
            if not open_trade_tickets:
                logger.debug("No open trades in DB to check for MT5 closes")
                self._last_history_sync = datetime.now()
                return
            
            print(f"[SYNC] Checking {len(open_trade_tickets)} open DB trades against MT5...", flush=True)
            
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days_back)
            
            # Get closed deals from MT5 history
            deals = await self.mt5_client.get_history(start_time, end_time)
            
            updated_count = 0
            matched_tickets = set()
            
            if deals:
                # Build lookup maps: deals indexed by multiple keys for flexible matching
                # MT5 deals have: ticket (deal ID), order (order ticket), position_id (position ticket)
                # Our DB stores the ORDER ticket as trade_id (for both market and pending orders)
                deals_by_position = {}
                deals_by_order = {}
                
                for deal in deals:
                    deal_entry = deal.get('entry', 0)
                    # entry=1 means "out" (closing deal), which is what we want
                    if deal_entry == 1:
                        pos_id = str(deal.get('position_id', 0))
                        order_id = str(deal.get('order', 0))
                        if pos_id != '0':
                            deals_by_position[pos_id] = deal
                        if order_id != '0':
                            deals_by_order[order_id] = deal
                
                # Try to match each open DB trade against MT5 close deals
                for trade_id in list(open_trade_tickets.keys()):
                    # Try matching by: position_id, order ticket, or deal ticket
                    close_deal = (
                        deals_by_position.get(trade_id) or
                        deals_by_order.get(trade_id) or
                        None
                    )
                    
                    # Also try direct deal ticket match (original logic)
                    if not close_deal:
                        for deal in deals:
                            if str(deal.get('ticket', 0)) == trade_id:
                                close_deal = deal
                                break
                    
                    if not close_deal:
                        continue
                    
                    deal_type = close_deal.get('type', '')
                    if deal_type not in ['buy', 'sell', 'DEAL_TYPE_BUY', 'DEAL_TYPE_SELL']:
                        continue
                    
                    profit = float(close_deal.get('profit', 0))
                    commission = float(close_deal.get('commission', 0))
                    swap = float(close_deal.get('swap', 0))
                    total_pnl = profit + commission + swap
                    price = float(close_deal.get('price', 0))
                    close_time = close_deal.get('time', datetime.now())
                    
                    try:
                        async with async_session() as session:
                            result = await session.execute(
                                select(TradeModel).where(TradeModel.trade_id == trade_id)
                            )
                            trade = result.scalar_one_or_none()
                            _needs_update = (
                                trade and (
                                    trade.exit_price is None or 
                                    trade.exit_price == 0 or
                                    (trade.profit_loss == 0 and trade.exit_price == trade.entry_price) or
                                    getattr(trade, 'pnl_source', None) == 'fallback'
                                )
                            )
                            if _needs_update:
                                _exit_dt = close_time if isinstance(close_time, datetime) else datetime.now(timezone.utc)
                                _open_dt = getattr(trade, 'entry_time', None) or getattr(trade, 'timestamp', None)
                                if _open_dt and isinstance(_open_dt, datetime) and _exit_dt < _open_dt:
                                    logger.warning(f"[SYNC] Trade {trade_id}: close_time ({_exit_dt}) < open_time ({_open_dt}), adjusting")
                                    _exit_dt = datetime.now(timezone.utc)

                                trade.exit_price = price
                                trade.exit_time = _exit_dt
                                trade.profit_loss = total_pnl
                                trade.pnl_source = "mt5"
                                trade.exit_reason = "Closed on MT5 (TP/SL/manual)"
                                await session.commit()
                                updated_count += 1
                                matched_tickets.add(trade_id)
                                print(f"[SYNC] Updated trade {trade_id} ({open_trade_tickets[trade_id]['symbol']}): P/L=${total_pnl:.2f}", flush=True)
                                logger.info(f"Updated trade {trade_id} with MT5 close data: P/L=${total_pnl:.2f}")
                    except Exception as e:
                        try:
                            await session.rollback()
                        except Exception:
                            pass
                        logger.warning(f"Could not update trade {trade_id}: {e}")
            
            # ── STEP 2: Detect pending orders that no longer exist on MT5 ──
            # These were deleted/expired externally — mark them as cancelled in the DB
            unmatched_tickets = set(open_trade_tickets.keys()) - matched_tickets
            cancelled_count = 0
            
            if unmatched_tickets:
                # Get current open positions and pending orders from MT5
                current_positions = set()
                current_orders = set()
                
                try:
                    positions = await self.mt5_client.get_positions()
                    if positions:
                        for p in positions:
                            current_positions.add(str(p.ticket))
                except Exception:
                    pass
                
                try:
                    import MetaTrader5 as mt5
                    orders = await asyncio.to_thread(mt5.orders_get)
                    if orders:
                        for o in orders:
                            current_orders.add(str(o.ticket))
                except Exception:
                    pass
                
                cancelled_count = 0
                filled_closed_count = 0
                for trade_id in unmatched_tickets:
                    # If this trade_id is neither an open position nor a pending order on MT5,
                    # check deal history FIRST before assuming it was cancelled
                    if trade_id not in current_positions and trade_id not in current_orders:
                        try:
                            # Check deal history to see if the order actually filled then closed
                            symbol = open_trade_tickets[trade_id]['symbol']
                            deal_check_start = datetime.now() - timedelta(days=days_back)
                            deals_for_symbol = await self.mt5_client.get_history(
                                deal_check_start, datetime.now(), symbol=symbol
                            )
                            
                            # Look for an opening deal (entry=0) with order == trade_id
                            opening_deal = None
                            closing_deal = None
                            ticket_int = int(trade_id) if trade_id.isdigit() else 0
                            
                            if deals_for_symbol and ticket_int:
                                for deal in deals_for_symbol:
                                    if deal.get('entry') == 0 and deal.get('order') == ticket_int:
                                        opening_deal = deal
                                        break
                                
                                if opening_deal:
                                    position_id = opening_deal.get('position_id')
                                    if position_id:
                                        for deal in deals_for_symbol:
                                            if (deal.get('entry') == 1 and 
                                                deal.get('position_id') == position_id):
                                                closing_deal = deal
                                                break
                            
                            async with async_session() as session:
                                result = await session.execute(
                                    select(TradeModel).where(TradeModel.trade_id == trade_id)
                                )
                                trade = result.scalar_one_or_none()
                                _needs_fix = (
                                    trade and (
                                        trade.exit_price is None or
                                        trade.exit_price == 0 or
                                        (trade.profit_loss == 0 and trade.exit_price == trade.entry_price) or
                                        getattr(trade, 'pnl_source', None) in (None, 'fallback')
                                    )
                                )
                                if _needs_fix:
                                    if opening_deal and closing_deal:
                                        # Order was FILLED then CLOSED -- record real P/L
                                        fill_price = opening_deal.get('price', trade.entry_price)
                                        close_price = closing_deal.get('price', 0)
                                        profit = float(closing_deal.get('profit', 0))
                                        commission = float(closing_deal.get('commission', 0))
                                        open_commission = float(opening_deal.get('commission', 0))
                                        swap = float(closing_deal.get('swap', 0))
                                        total_pnl = profit + commission + open_commission + swap
                                        close_time = closing_deal.get('time', datetime.now())
                                        
                                        trade.entry_price = fill_price
                                        trade.exit_price = close_price
                                        trade.profit_loss = total_pnl
                                        trade.pnl_source = "mt5"
                                        trade.exit_time = close_time if isinstance(close_time, datetime) else datetime.now()
                                        trade.exit_reason = "SL/TP hit (filled-then-closed, detected via trade sync)"
                                        await session.commit()
                                        filled_closed_count += 1
                                        print(
                                            f"[SYNC] Trade {trade_id} ({symbol}) was filled then closed, "
                                            f"NOT cancelled — P/L: ${total_pnl:.2f} "
                                            f"(entry={fill_price}, exit={close_price})",
                                            flush=True
                                        )
                                    else:
                                        # Truly cancelled -- no fill deal found
                                        trade.exit_price = trade.entry_price
                                        trade.exit_time = datetime.now(timezone.utc)
                                        trade.profit_loss = 0.0
                                        trade.exit_reason = "Cancelled/deleted (not found on MT5)"
                                        await session.commit()
                                        cancelled_count += 1
                                        print(f"[SYNC] Marked trade {trade_id} ({symbol}) as cancelled (not on MT5)", flush=True)
                        except Exception as e:
                            try:
                                await session.rollback()
                            except Exception:
                                pass
                            logger.warning(f"Could not process trade {trade_id}: {e}")
                
                if cancelled_count > 0:
                    logger.info(f"Marked {cancelled_count} orphaned trades as cancelled")
                if filled_closed_count > 0:
                    logger.info(f"Detected {filled_closed_count} filled-then-closed trades via deal history")
            
            self._last_history_sync = datetime.now()
            
            total_updates = updated_count + cancelled_count + (filled_closed_count if 'filled_closed_count' in dir() else 0)
            if total_updates > 0:
                fc = filled_closed_count if 'filled_closed_count' in dir() else 0
                logger.info(f"Trade sync: {updated_count} closed, {fc} filled-then-closed, {cancelled_count} cancelled")
                print(f"[SYNC] Done: {updated_count} trades updated with close data, {fc} filled-then-closed, {cancelled_count} cancelled/orphaned", flush=True)
                
                from .api.routes.activity import add_activity
                add_activity(
                    "info",
                    f"Trade sync: {updated_count} closed, {fc} filled-then-closed, {cancelled_count} cancelled",
                    None,
                    {"updated_count": updated_count, "filled_closed": fc, "cancelled": cancelled_count, "days_back": days_back}
                )
            else:
                logger.debug("No open bot trades needed MT5 close updates")
            
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
            print(
                f"[CLOSE] Detected close: #{position.ticket} {position.symbol} "
                f"{position.direction} vol={position.volume} entry={position.entry_price}",
                flush=True
            )
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
                    # Wide window (7 days back) so trades that closed while the bot was offline are found
                    history = await self.mt5_client.get_history(
                        close_time - timedelta(days=7), close_time + timedelta(hours=1)
                    )
                    print(f"[CLOSE] {position.symbol}: Searching {len(history)} deals for ticket #{position.ticket}...", flush=True)
                    total_profit = 0.0
                    total_commission = 0.0
                    total_swap = 0.0
                    close_deal_count = 0
                    _last_deal_time = None
                    _order_tkt = getattr(position, 'order_ticket', None)
                    for deal in history:
                        _matches_ticket = (
                            deal.get('position_id') == position.ticket
                            or deal.get('order') == position.ticket
                            or (_order_tkt and deal.get('position_id') == _order_tkt)
                            or (_order_tkt and deal.get('order') == _order_tkt)
                        )
                        if _matches_ticket and deal.get('entry') == 1:
                            actual_close_price = deal.get('price', position.current_price)
                            mt5_profit = deal.get('profit', 0) or 0
                            mt5_commission = deal.get('commission', 0) or 0
                            mt5_swap = deal.get('swap', 0) or 0
                            total_profit += mt5_profit
                            total_commission += mt5_commission
                            total_swap += mt5_swap
                            close_deal_count += 1
                            _deal_ts = deal.get('time', None)
                            if _deal_ts:
                                try:
                                    _last_deal_time = datetime.fromtimestamp(_deal_ts) if isinstance(_deal_ts, (int, float)) else _deal_ts
                                except Exception:
                                    pass
                        elif _matches_ticket and deal.get('entry') == 0:
                            total_commission += deal.get('commission', 0) or 0
                    if close_deal_count > 0:
                        profit_loss = total_profit + total_commission + total_swap
                        mt5_profit_found = True
                        if _last_deal_time:
                            close_time = _last_deal_time
                        print(
                            f"[CLOSE] {position.symbol}: MT5 actual P/L = ${profit_loss:.2f} "
                            f"({close_deal_count} close deal(s), profit={total_profit:.2f}, "
                            f"commission={total_commission:.2f}, swap={total_swap:.2f}, "
                            f"exit={actual_close_price:.5f})",
                            flush=True
                        )
                    if not mt5_profit_found:
                        print(f"[CLOSE] {position.symbol}: WARNING — no close deal found in {len(history)} deals for #{position.ticket}", flush=True)
                except Exception as e:
                    logger.warning(f"Could not fetch close details from MT5 history: {e}")
                    print(f"[CLOSE] {position.symbol}: MT5 history error: {e}", flush=True)
            
            # Fallback: manual calculation if MT5 history unavailable
            # Conservative approach: assume SL was hit unless price clearly past TP.
            # This prevents false WINs when current_price drifts favorably after SL hit.
            if profit_loss is None:
                sl_price = getattr(position, 'stop_loss', None)
                tp_price = getattr(position, 'take_profit', None)
                fallback_exit = actual_close_price  # default: current_price
                fallback_reason = "current_price (no SL/TP)"
                
                # Check TP first — only if price is clearly past TP
                _tp_hit = False
                if tp_price and tp_price > 0:
                    if position.direction == 'long' and actual_close_price >= tp_price:
                        fallback_exit = tp_price
                        fallback_reason = "TP_hit (price past TP)"
                        _tp_hit = True
                    elif position.direction == 'short' and actual_close_price <= tp_price:
                        fallback_exit = tp_price
                        fallback_reason = "TP_hit (price past TP)"
                        _tp_hit = True
                
                # If TP not clearly hit, assume SL (conservative)
                if not _tp_hit and sl_price and sl_price > 0:
                    fallback_exit = sl_price
                    fallback_reason = "SL_assumed (no MT5 history, conservative)"
                
                actual_close_price = fallback_exit
                
                from .config import calculate_pl
                if position.direction == 'long':
                    profit_loss = calculate_pl(position.symbol, actual_close_price - position.entry_price, position.volume)
                else:
                    profit_loss = calculate_pl(position.symbol, position.entry_price - actual_close_price, position.volume)
                print(
                    f"[CLOSE] {position.symbol}: Fallback P/L = ${profit_loss:.2f} "
                    f"(exit={actual_close_price:.5f}, reason={fallback_reason}, "
                    f"SL={sl_price}, TP={tp_price}, market={position.current_price:.5f})",
                    flush=True
                )
            
            # Calculate pips using actual close price and symbol spec
            pip_size = _spec.pip_size
            
            raw_pips = (actual_close_price - position.entry_price) / pip_size
            
            if position.direction == 'short':
                pips = -raw_pips  # Short profits when price drops (negative raw_pips)
            else:
                pips = raw_pips   # Long profits when price rises (positive raw_pips)
            
            # =============================================
            # UPDATE DATABASE RECORD (so trades page shows it)
            # =============================================
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

                        if not trade_record:
                            _fallback_ticket = getattr(position, 'order_ticket', None)
                            if _fallback_ticket:
                                result2 = await db_sess.execute(
                                    select(TradeModel).where(
                                        TradeModel.trade_id == str(_fallback_ticket)
                                    )
                                )
                                trade_record = result2.scalar_one_or_none()
                                if trade_record:
                                    logger.info(
                                        f"[CLOSE] {position.symbol}: Found DB record via "
                                        f"order_ticket={_fallback_ticket} (position ticket={position.ticket})"
                                    )

                        if not trade_record:
                            try:
                                history = await self.mt5_client.get_history(
                                    datetime.now() - timedelta(days=7), datetime.now()
                                )
                                for deal in history:
                                    if deal.get('position_id') == position.ticket and deal.get('entry') == 0:
                                        _orig_order = str(deal.get('order', 0))
                                        if _orig_order != '0' and _orig_order != str(position.ticket):
                                            result3 = await db_sess.execute(
                                                select(TradeModel).where(
                                                    TradeModel.trade_id == _orig_order
                                                )
                                            )
                                            trade_record = result3.scalar_one_or_none()
                                            if trade_record:
                                                logger.info(
                                                    f"[CLOSE] {position.symbol}: Found DB record via "
                                                    f"deal history order={_orig_order} (position ticket={position.ticket})"
                                                )
                                        break
                            except Exception as _hist_err:
                                logger.debug(f"Deal history fallback failed: {_hist_err}")

                        if trade_record:
                            _open_time = getattr(trade_record, 'entry_time', None) or getattr(trade_record, 'timestamp', None)
                            if _open_time and close_time and isinstance(_open_time, datetime) and isinstance(close_time, datetime):
                                if close_time < _open_time:
                                    logger.warning(
                                        f"[CLOSE] {position.symbol}: close_time ({close_time}) < open_time ({_open_time}) — "
                                        f"adjusting close_time to now"
                                    )
                                    close_time = datetime.now(timezone.utc)

                            trade_record.exit_price = actual_close_price
                            trade_record.exit_time = close_time
                            trade_record.exit_reason = getattr(position, 'close_reason', 'position_closed')
                            trade_record.profit_loss = profit_loss
                            trade_record.profit_loss_pips = pips
                            trade_record.pnl_source = "mt5" if mt5_profit_found else "fallback"
                            if position.stop_loss and position.entry_price:
                                risk_pips = abs(position.entry_price - position.stop_loss) / pip_size
                                if risk_pips > 0:
                                    trade_record.r_multiple = pips / risk_pips
                            await db_sess.commit()
                            print(f"[CLOSE] {position.symbol}: DB updated — exit={actual_close_price:.5f}, P/L=${profit_loss:+.2f}", flush=True)
                        else:
                            print(f"[CLOSE] {position.symbol}: No DB record found for ticket {position.ticket}", flush=True)
                except Exception as e:
                    try:
                        await db_sess.rollback()
                    except Exception:
                        pass
                    logger.warning(f"Could not update trade in database: {e}")
            
            # Update daily P&L tracker
            self.daily_pnl += profit_loss
            
            # Update win/loss streak
            if profit_loss > 0:
                self.win_streak += 1
                self.loss_streak = 0
            elif profit_loss < 0:
                self.loss_streak += 1
                self.win_streak = 0
                _is_crypto_sym = any(c in position.symbol.upper() for c in ['BTC', 'ETH', 'XRP', 'SOL', 'ADA', 'DOGE'])
                _cooldown_min = 15 if _is_crypto_sym else 30
                cooldown_until = datetime.now() + timedelta(minutes=_cooldown_min)
                self._symbol_loss_cooldowns[position.symbol] = cooldown_until
                logger.info(
                    f"[LOSS-COOLDOWN] {position.symbol}: {_cooldown_min}-min cooldown set until "
                    f"{cooldown_until.strftime('%H:%M:%S')} (P/L: ${profit_loss:.2f})"
                )
                print(
                    f"[LOSS-COOLDOWN] {position.symbol}: No new entries for {_cooldown_min} minutes "
                    f"(cooldown until {cooldown_until.strftime('%H:%M:%S')})",
                    flush=True
                )
                from .api.routes.activity import add_activity
                add_activity("loss_cooldown_set", f"{position.symbol}: {_cooldown_min}-min cooldown set after loss", symbol=position.symbol, details={"cooldown_minutes": _cooldown_min, "until": cooldown_until.isoformat()})
            
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
            asyncio.create_task(broadcast_trade_update({
                "event": "trade_closed",
                "ticket": position.ticket,
                "symbol": position.symbol,
                "direction": position.direction,
                "entry_price": position.entry_price,
                "exit_price": actual_close_price,
                "profit_loss": profit_loss,
                "pips": pips,
                "r_multiple": position.current_r_multiple
            }))
            
            # Dynamic learning update: refresh trading_learnings.md after each close
            # Throttled to at most once per hour to avoid excessive disk writes
            if self.learning_service:
                try:
                    should_update = (
                        self._last_learnings_update is None
                        or (datetime.now() - self._last_learnings_update).total_seconds() >= 3600
                    )
                    if should_update:
                        await self.learning_service.update_learnings_documentation()
                        self._last_learnings_update = datetime.now()
                        logger.info("[LEARNINGS] Updated trading_learnings.md after trade close")
                except Exception as learn_err:
                    logger.debug(f"Could not update learnings doc: {learn_err}")
            
            # Send Telegram notification
            if mt5_profit_found:
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
            else:
                # No MT5 history confirmation — send UNCONFIRMED notification
                # so the user knows to verify the P/L manually
                print(
                    f"[CLOSE] {position.symbol}: UNCONFIRMED P/L (no MT5 close deal found) — "
                    f"estimated P/L=${profit_loss:+.2f}, verify manually",
                    flush=True
                )
                await notify(
                    NotificationType.TRADE_CLOSED,
                    f"Trade closed (UNCONFIRMED): {position.symbol}",
                    symbol=position.symbol,
                    direction=position.direction,
                    entry_price=position.entry_price,
                    exit_price=actual_close_price,
                    profit_loss=profit_loss,
                    pips=pips,
                    ticket=position.ticket,
                    unconfirmed=True
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
            
            # Reclaim daily risk budget now that the position is closed
            if hasattr(self, 'risk_manager') and self.risk_manager:
                try:
                    _reclaim_pct = self.risk_manager.risk_per_trade  # default
                    if DB_AVAILABLE:
                        try:
                            from sqlalchemy import select
                            async with async_session() as db_sess:
                                _tr = await db_sess.execute(
                                    select(TradeModel).where(TradeModel.trade_id == str(position.ticket))
                                )
                                _rec = _tr.scalar_one_or_none()
                                if _rec and getattr(_rec, 'risk_percent', None):
                                    _reclaim_pct = _rec.risk_percent
                        except Exception:
                            pass
                    self.risk_manager.update_daily_risk(-_reclaim_pct)
                    print(
                        f"[RISK] {position.symbol}: Daily risk reclaimed -{_reclaim_pct*100:.1f}% "
                        f"(position closed), total: {self.risk_manager.daily_risk_used*100:.1f}%/"
                        f"{self.risk_manager.max_daily_risk*100:.0f}%",
                        flush=True
                    )
                except Exception as e:
                    logger.warning(f"Could not reclaim daily risk for {position.symbol}: {e}")
            
            # Have Claude review ALL closed trades for learning
            # (Previously only losses and big wins; now every trade for data collection)
            should_review = True
            
            if should_review and self.claude_client and self.claude_client.api_key:
                try:
                    # Retrieve original trade metadata from DB (including judge/confluence)
                    entry_reason = 'N/A'
                    original_confidence = 0.0
                    trade_timeframe = 'M15'
                    _db_judge_verdict = None
                    _db_judge_reason = None
                    _db_confluence_factors = None
                    _db_confluence_count = None
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
                                    entry_reason = trade_record.claude_reasoning or trade_record.entry_reason or 'N/A'
                                    original_confidence = trade_record.claude_confidence or 0.0
                                    trade_timeframe = trade_record.timeframe or 'M15'
                                    _db_judge_verdict = getattr(trade_record, 'judge_verdict', None)
                                    _db_judge_reason = getattr(trade_record, 'judge_reason', None)
                                    _db_confluence_factors = getattr(trade_record, 'confluence_factors', None)
                                    _db_confluence_count = getattr(trade_record, 'confluence_count', None)
                                    logger.debug(f"Retrieved trade metadata for {position.ticket}: confidence={original_confidence:.0%}, judge={_db_judge_verdict}, confluence={_db_confluence_count}")
                        except Exception as e:
                            logger.warning(f"Could not retrieve trade metadata for {position.ticket}: {e}")
                    
                    trade_data = {
                        'symbol': position.symbol,
                        'direction': position.direction,
                        'entry_price': position.entry_price,
                        'exit_price': actual_close_price,
                        'stop_loss': position.stop_loss,
                        'take_profit': position.take_profit,
                        'profit_loss': profit_loss,
                        'pips': pips,
                        'r_multiple': position.current_r_multiple,
                        'duration': str(close_time - position.open_time) if position.open_time else 'Unknown',
                        'entry_reason': entry_reason,
                        'original_confidence': original_confidence,
                        'timeframe': trade_timeframe,
                        'judge_verdict': _db_judge_verdict,
                        'judge_reason': _db_judge_reason,
                        'confluence_factors': _db_confluence_factors,
                        'confluence_count': _db_confluence_count,
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
                            # Judge & confluence context for learning correlation
                            judge_verdict=_db_judge_verdict,
                            judge_reason=_db_judge_reason,
                            confluence_factors=_db_confluence_factors,
                            confluence_count=_db_confluence_count,
                        )
                        logger.info(f"Trade review stored for learning: {position.ticket} (judge={_db_judge_verdict})")
                        
                except Exception as e:
                    logger.warning(f"Could not get/store Claude trade review: {e}")
            
            # =============================================
            # REVERSAL RE-ENTRY: If closed due to profit protection reversal,
            # analyze for a new trade in the opposite direction
            # =============================================
            reversal_reasons = {"giveback_protection", "near_tp_reversal"}
            close_reason = getattr(position, 'close_reason', '')
            if close_reason in reversal_reasons and profit_loss > 0:
                logger.info(
                    f"[REVERSAL] {position.symbol}: Closed due to {close_reason} "
                    f"(P/L=${profit_loss:+.2f}, peak={position.peak_r_multiple:.2f}R). "
                    f"Spawning reversal analysis..."
                )
                print(
                    f"[REVERSAL] {position.symbol}: Profit protection close ({close_reason}). "
                    f"Analyzing reversal re-entry in opposite direction...",
                    flush=True
                )
                asyncio.create_task(self._analyze_reversal_entry(position))
                    
        except Exception as e:
            logger.error(f"Error handling position close: {e}")
    
    async def _analyze_reversal_entry(self, closed_position):
        """
        Analyze whether a reversal re-entry trade is warranted after profit protection
        closed a position. Calls Claude with reversal context, validates the signal,
        and places the trade through the full pipeline if approved.
        
        Args:
            closed_position: The Position object that was just closed by profit protection
        """
        symbol = closed_position.symbol
        opposite_direction = 'short' if closed_position.direction == 'long' else 'long'
        
        try:
            # ---- Safeguard 1: Per-symbol reversal cooldown (1 hour) ----
            if not hasattr(self, '_reversal_cooldowns'):
                self._reversal_cooldowns = {}
            
            last_reversal = self._reversal_cooldowns.get(symbol)
            if last_reversal:
                minutes_since = (datetime.now() - last_reversal).total_seconds() / 60
                if minutes_since < 60:
                    logger.info(
                        f"[REVERSAL] {symbol}: Skipping — reversal cooldown active "
                        f"({minutes_since:.0f}min < 60min)"
                    )
                    print(
                        f"[REVERSAL] {symbol}: SKIPPED — reversal cooldown "
                        f"({minutes_since:.0f}m since last reversal attempt)",
                        flush=True
                    )
                    from .api.routes.activity import add_activity
                    add_activity("reversal_cooldown_skip", f"{symbol}: Skipped — reversal cooldown ({minutes_since:.0f}m < 60m)", symbol=symbol, details={"minutes_since": round(minutes_since)})
                    return
            
            # ---- Safeguard 2: No existing position for this symbol ----
            if self.position_manager:
                existing = self.position_manager.get_positions_by_symbol(symbol)
                if existing:
                    logger.info(
                        f"[REVERSAL] {symbol}: Skipping — position already exists "
                        f"(ticket {existing[0].ticket})"
                    )
                    return
            
            # ---- Safeguard 3: Daily trade limit ----
            if self.daily_trades >= settings.trading.max_daily_trades:
                logger.info(
                    f"[REVERSAL] {symbol}: Skipping — daily trade limit reached "
                    f"({self.daily_trades}/{settings.trading.max_daily_trades})"
                )
                return
            
            # ---- Safeguard 4: No scalp reversals ----
            if getattr(closed_position, 'trade_type', 'intraday') == 'scalp':
                logger.info(f"[REVERSAL] {symbol}: Skipping — scalp trades don't trigger reversals")
                return
            
            # Record the reversal attempt timestamp
            self._reversal_cooldowns[symbol] = datetime.now()
            
            # ---- Fetch fresh market data ----
            df = await self.data_fetcher.get_ohlcv(
                symbol=symbol,
                timeframe=settings.timeframes.execution_tf,
                count=settings.timeframes.execution_tf_candles
            )
            if df is None or df.empty:
                logger.warning(f"[REVERSAL] {symbol}: No market data available")
                return
            
            current_price = float(df['close'].iloc[-1])
            
            # ---- Generate chart image ----
            chart_base64 = await self._generate_chart_image(df, symbol)
            if not chart_base64:
                logger.warning(f"[REVERSAL] {symbol}: Failed to generate chart")
                return
            
            # ---- Build reversal-specific context ----
            strategy_context = self.context_builder.get_ict_context()
            
            reversal_context = (
                f"\n\n## REVERSAL ANALYSIS CONTEXT\n"
                f"A {closed_position.direction.upper()} trade on {symbol} was just closed "
                f"by profit protection.\n"
                f"- Close reason: {closed_position.close_reason}\n"
                f"- Entry: {closed_position.entry_price:.5f}\n"
                f"- Exit (approx): {closed_position.current_price:.5f}\n"
                f"- Peak R-multiple: {closed_position.peak_r_multiple:.2f}R\n"
                f"- Current R at close: {closed_position.current_r_multiple:.2f}R\n"
                f"- P/L: ${closed_position.unrealized_pnl:+.2f}\n"
                f"- Direction: Was {closed_position.direction.upper()}, "
                f"evaluating {opposite_direction.upper()} reversal\n\n"
                f"**TASK**: Evaluate if this is a STRUCTURAL reversal worth entering "
                f"in the {opposite_direction.upper()} direction. Look for:\n"
                f"- Break of structure (BOS) against the original trade direction\n"
                f"- Displacement candle(s) confirming the reversal\n"
                f"- Order blocks or FVGs on the opposite side for entry\n"
                f"- Liquidity that was swept (the original TP area may have been the target)\n\n"
                f"If the reversal has structural backing, provide a {opposite_direction.upper()} "
                f"signal with entry, SL, and TP based on the new structure. "
                f"If the reversal is NOT structurally backed (just a pullback/retracement), "
                f"return no_trade.\n"
            )
            
            from .config import get_symbol_spec as _gss
            _sym_spec = _gss(symbol)
            
            market_data = {
                "current_price": current_price,
                "bid": current_price - _sym_spec.pip_size,
                "ask": current_price + _sym_spec.pip_size,
                "spread": 1.0,
                "reversal_context": reversal_context,
            }
            
            # Add account info if available
            try:
                account_info = await self.mt5_client.get_account_info()
                if account_info:
                    market_data["account_equity"] = account_info.equity
                    market_data["account_balance"] = account_info.balance
            except Exception:
                pass
            
            # ---- Run ICT analysis on fresh data ----
            from .config import get_symbol_spec
            _core_pip = get_symbol_spec(symbol).pip_size
            market_structure_analyzer = MarketStructureAnalyzer()
            fvg_detector = FVGDetector(pip_value=_core_pip)
            ob_detector = OrderBlockDetector()
            liquidity_mapper = LiquidityMapper(pip_value=_core_pip)
            
            analysis_data = {
                "market_structure": market_structure_analyzer.analyze(df),
                "fvg": fvg_detector.detect(df),
                "order_blocks": ob_detector.detect(df),
                "liquidity": liquidity_mapper.analyze(df),
            }
            
            # Serialize analysis_data for Claude
            serialized_analysis = {}
            for key, val in analysis_data.items():
                try:
                    if hasattr(val, 'to_dict'):
                        serialized_analysis[key] = val.to_dict()
                    elif hasattr(val, '__dict__'):
                        serialized_analysis[key] = str(val)
                    else:
                        serialized_analysis[key] = val
                except Exception:
                    serialized_analysis[key] = str(val)
            
            # ---- Call Claude for reversal analysis ----
            logger.info(f"[REVERSAL] {symbol}: Calling Claude for reversal analysis...")
            
            claude_result = await self.claude_client.analyze_chart_async(
                chart_image_base64=chart_base64,
                symbol=symbol,
                timeframe=settings.timeframes.execution_tf,
                strategy_context=strategy_context + reversal_context,
                market_data=market_data,
                analysis_data=serialized_analysis,
            )
            
            trade_signal = claude_result.signal
            
            # ---- Validate: Must be opposite direction ----
            if trade_signal.direction == 'no_trade':
                logger.info(
                    f"[REVERSAL] {symbol}: Claude says NO TRADE — "
                    f"reversal not structurally backed. Reasoning: "
                    f"{(trade_signal.reasoning or 'N/A')[:100]}"
                )
                print(
                    f"[REVERSAL] {symbol}: NO TRADE — Claude doesn't see structural reversal",
                    flush=True
                )
                return
            
            if trade_signal.direction != opposite_direction:
                logger.info(
                    f"[REVERSAL] {symbol}: Claude signal is {trade_signal.direction.upper()}, "
                    f"not {opposite_direction.upper()} — same direction as closed trade, skipping"
                )
                return
            
            # ---- Tag as reversal re-entry (bypasses flip cooldown) ----
            trade_signal.reversal_reentry = True
            
            logger.info(
                f"[REVERSAL] {symbol}: Claude confirms {opposite_direction.upper()} reversal! "
                f"Confidence: {trade_signal.confidence:.0%}, "
                f"Entry: {trade_signal.entry_price}, SL: {trade_signal.stop_loss}, "
                f"TP: {trade_signal.take_profit}"
            )
            print(
                f"[REVERSAL] {symbol}: Claude confirms {opposite_direction.upper()} reversal "
                f"({trade_signal.confidence:.0%}). Running through full pipeline...",
                flush=True
            )
            
            # ---- R:R Check ----
            if trade_signal.stop_loss and trade_signal.take_profit and trade_signal.entry_price:
                entry = trade_signal.entry_price
                sl_dist = abs(entry - trade_signal.stop_loss)
                tp_dist = abs(trade_signal.take_profit - entry)
                rr = tp_dist / sl_dist if sl_dist > 0 else 0
                
                if rr < 1.0:
                    logger.warning(
                        f"[REVERSAL] {symbol}: REJECTED — R:R {rr:.2f}:1 < 1.0"
                    )
                    print(
                        f"[REVERSAL] {symbol}: REJECTED — R:R {rr:.2f}:1 too low",
                        flush=True
                    )
                    return
            
            # ---- Risk Manager Validation ----
            if self.risk_manager:
                try:
                    _acct = await self.mt5_client.get_account_info() if self.mt5_client else None
                    _balance = _acct.balance if _acct else 10000.0
                except Exception:
                    _balance = 10000.0
                risk_check = self.risk_manager.validate_trade(
                    entry_price=trade_signal.entry_price or current_price,
                    stop_loss=trade_signal.stop_loss or 0,
                    take_profit=trade_signal.take_profit or 0,
                    direction=trade_signal.direction,
                    symbol=symbol,
                    account_balance=_balance,
                    trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                )
                if not risk_check.is_valid:
                    logger.warning(
                        f"[REVERSAL] {symbol}: Risk manager rejected — {risk_check.errors}"
                    )
                    print(
                        f"[REVERSAL] {symbol}: REJECTED by risk manager — {risk_check.errors}",
                        flush=True
                    )
                    return
            
            # ---- Trade Judge ----
            if self.claude_client and self.claude_client.api_key:
                try:
                    judge_signal = {
                        "symbol": symbol,
                        "direction": trade_signal.direction,
                        "confidence": trade_signal.confidence,
                        "entry_price": trade_signal.entry_price or current_price,
                        "stop_loss": trade_signal.stop_loss,
                        "take_profit": trade_signal.take_profit,
                        "reasoning": trade_signal.reasoning or "",
                        "trade_type": getattr(trade_signal, 'trade_type', 'intraday'),
                        "reversal_reentry": True,
                    }
                    
                    risk_metrics = {
                        "risk_reward": rr if 'rr' in dir() else 0,
                        "position_size_pct": 1.0,
                        "daily_trades": self.daily_trades,
                        "daily_pnl": self.daily_pnl,
                    }
                    
                    learning_context = ""
                    if self.learning_service:
                        try:
                            learning_context = self.learning_service.build_context_for_claude(
                                symbol=symbol,
                                direction=trade_signal.direction,
                                trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                            )
                        except Exception:
                            pass
                    
                    judge_result = await self.claude_client.judge_trade(
                        signal=judge_signal,
                        risk_metrics=risk_metrics,
                        learning_context=learning_context,
                    )
                    
                    verdict = judge_result.get('verdict', 'APPROVE')
                    reason = judge_result.get('reason', '')
                    
                    if verdict == 'REJECT':
                        logger.info(
                            f"[REVERSAL] {symbol}: Judge REJECTED — {reason}"
                        )
                        print(
                            f"[REVERSAL] {symbol}: Judge REJECTED reversal — {reason}",
                            flush=True
                        )
                        return
                    
                    logger.info(
                        f"[REVERSAL] {symbol}: Judge verdict: {verdict} — {reason}"
                    )
                    
                except Exception as e:
                    logger.warning(f"[REVERSAL] Judge call failed, proceeding: {e}")
            
            # ---- Position Sizing ----
            entry_price = trade_signal.entry_price or current_price
            stop_loss = trade_signal.stop_loss
            
            if not stop_loss:
                logger.warning(f"[REVERSAL] {symbol}: No stop loss provided, aborting")
                return
            
            position_size = 0.01  # Default minimum
            if self.risk_manager:
                try:
                    account_info = await self.mt5_client.get_account_info()
                    equity = account_info.equity if account_info else 1000.0
                    sl_distance = abs(entry_price - stop_loss)
                    
                    sizing = self.risk_manager.calculate_position_size(
                        symbol=symbol,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        account_equity=equity,
                    )
                    if sizing and sizing.get('position_size', 0) > 0:
                        position_size = sizing['position_size']
                    
                    # Apply scaling manager risk multiplier for reversals too
                    if self.scaling_manager:
                        _mode_cfg = self.scaling_manager.get_mode_config()
                        _rmult = getattr(_mode_cfg, 'risk_multiplier', 1.0)
                        if _rmult != 1.0:
                            from .config import normalize_lots as _nl
                            position_size = _nl(symbol, position_size * _rmult)
                except Exception as e:
                    logger.warning(f"[REVERSAL] Position sizing error: {e}")
            
            # ---- Place the order ----
            self.daily_trades += 1
            _reversal_risk_pct = self.risk_manager.risk_per_trade if self.risk_manager else 0.02
            if hasattr(self, 'risk_manager') and self.risk_manager:
                self.risk_manager.update_daily_risk(_reversal_risk_pct)
                print(
                    f"[RISK] {symbol}: Reversal daily risk +{_reversal_risk_pct*100:.1f}%, "
                    f"total: {self.risk_manager.daily_risk_used*100:.1f}%/"
                    f"{self.risk_manager.max_daily_risk*100:.0f}%",
                    flush=True
                )
            
            order_type = getattr(trade_signal, 'order_type', 'market') or 'market'
            
            if order_type == 'market' or order_type.endswith('_market'):
                result = await self.order_manager.place_market_order(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    volume=position_size,
                    stop_loss=stop_loss,
                    take_profit=trade_signal.take_profit,
                )
                
                if result.success:
                    logger.info(
                        f"[REVERSAL] {symbol}: Market order PLACED — "
                        f"{trade_signal.direction.upper()} {position_size} lots @ {entry_price:.5f}"
                    )
                    print(
                        f"[REVERSAL] {symbol}: REVERSAL TRADE PLACED! "
                        f"{trade_signal.direction.upper()} {position_size} lots "
                        f"(SL={stop_loss:.5f}, TP={trade_signal.take_profit:.5f})",
                        flush=True
                    )
                    
                    # Track the new position
                    if self.position_manager and result.order_id:
                        from .execution.position_manager import Position as Pos
                        new_pos = Pos(
                            ticket=result.order_id,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            volume=position_size,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=trade_signal.take_profit or 0,
                            open_time=datetime.now(),
                            trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                        )
                        # Set up multi-TP levels
                        sl_dist = abs(entry_price - stop_loss)
                        if trade_signal.direction == 'long':
                            new_pos.tp1 = entry_price + sl_dist * 1.0
                            new_pos.tp2 = entry_price + sl_dist * 2.0
                            new_pos.tp3 = entry_price + sl_dist * 3.0
                        else:
                            new_pos.tp1 = entry_price - sl_dist * 1.0
                            new_pos.tp2 = entry_price - sl_dist * 2.0
                            new_pos.tp3 = entry_price - sl_dist * 3.0
                        
                        self.position_manager.add_position(new_pos)
                    
                    # Store in DB
                    if DB_AVAILABLE:
                        try:
                            async with async_session() as db_sess:
                                trade_record = TradeModel(
                                    trade_id=str(result.order_id),
                                    symbol=symbol,
                                    direction=trade_signal.direction,
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=trade_signal.take_profit,
                                    volume=position_size,
                                    entry_time=datetime.now(),
                                    entry_reason=f"Reversal re-entry ({closed_position.close_reason})",
                                    claude_confidence=trade_signal.confidence,
                                    timeframe=settings.timeframes.execution_tf,
                                    trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                                )
                                db_sess.add(trade_record)
                                await db_sess.commit()
                        except Exception as e:
                            try:
                                await db_sess.rollback()
                            except Exception:
                                pass
                            logger.warning(f"[REVERSAL] DB store error: {e}")

                    # Telegram notification
                    try:
                        from .notifications import notify, NotificationType
                        await notify(
                            NotificationType.TRADE_OPENED,
                            f"REVERSAL: {trade_signal.direction.upper()} {symbol}",
                            symbol=symbol,
                            direction=trade_signal.direction,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=trade_signal.take_profit,
                            volume=position_size,
                            confidence=trade_signal.confidence,
                        )
                    except Exception:
                        pass
                else:
                    logger.warning(
                        f"[REVERSAL] {symbol}: Order placement FAILED — "
                        f"{getattr(result, 'message', 'unknown error')}"
                    )
                    self.daily_trades = max(0, self.daily_trades - 1)
            else:
                # Pending order for reversal (limit entry)
                suggested_entry = trade_signal.entry_price or current_price
                result = await self.order_manager.place_pending_order(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    order_type=order_type,
                    price=suggested_entry,
                    volume=position_size,
                    stop_loss=stop_loss,
                    take_profit=trade_signal.take_profit,
                )
                
                if result.success:
                    logger.info(
                        f"[REVERSAL] {symbol}: Pending {order_type} PLACED — "
                        f"{trade_signal.direction.upper()} @ {suggested_entry:.5f}"
                    )
                    print(
                        f"[REVERSAL] {symbol}: REVERSAL PENDING ORDER! "
                        f"{order_type} {trade_signal.direction.upper()} @ {suggested_entry:.5f}",
                        flush=True
                    )
                    
                    # Track pending order
                    if hasattr(self, 'pending_order_manager') and self.pending_order_manager:
                        self.pending_order_manager.track_order(
                            ticket=result.order_id,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            order_type=order_type,
                            price=suggested_entry,
                            stop_loss=stop_loss,
                            take_profit=trade_signal.take_profit,
                            volume=position_size,
                        )
                else:
                    logger.warning(
                        f"[REVERSAL] {symbol}: Pending order FAILED — "
                        f"{getattr(result, 'message', 'unknown error')}"
                    )
                    self.daily_trades = max(0, self.daily_trades - 1)
        
        except Exception as e:
            logger.error(f"[REVERSAL] Error analyzing reversal for {symbol}: {e}")
            import traceback
            traceback.print_exc()
    
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
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_date:
            logger.info("New trading day - resetting daily counters")

            try:
                from trading_bot.api.database import backup_database
                backup_database()
            except Exception as _bk_err:
                logger.warning(f"Daily DB backup failed: {_bk_err}")

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
            
            # Check rejected signal outcomes every day (did we miss winners?)
            if self.learning_service:
                try:
                    updated = await self.learning_service.check_rejected_signal_outcomes(lookback_hours=24)
                    if updated > 0:
                        print(f"[LEARNING] Updated {updated} rejected signal outcomes (checking if judge missed winners)", flush=True)
                except Exception as e:
                    logger.debug(f"Rejected signal outcome check failed: {e}")
            
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
        
        milestones = [250, 500, 750, 1000, 2500, 5000, 10000]
        
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
    
    def get_status_summary(self) -> dict:
        """
        Get a summary dict of bot state for the Telegram command handler.
        
        Returns a dict with key bot attributes for the /status command.
        """
        # Current session
        session = "unknown"
        if self.session_analytics:
            try:
                current = self.session_analytics.get_current_session()
                session = current.value
            except Exception:
                pass
        
        # Open positions count
        open_positions = 0
        if self.position_manager:
            open_positions = len(self.position_manager.positions)
        
        # Pending orders count
        pending_orders = 0
        if self.pending_order_manager:
            pending_orders = len(self.pending_order_manager.get_active_orders())
        
        # Uptime
        uptime = "N/A"
        if self.last_reset_date:
            delta = datetime.now(timezone.utc).date() - self.last_reset_date
            if delta.days > 0:
                uptime = f"{delta.days}d"
            else:
                uptime = "today"
        
        from .config import settings
        
        return {
            'running': self.running,
            'session': session,
            'symbols': settings.trading.symbols,
            'open_positions': open_positions,
            'pending_orders': pending_orders,
            'win_streak': self.win_streak,
            'loss_streak': self.loss_streak,
            'daily_pnl': self.daily_pnl,
            'daily_trades': self.daily_trades,
            'uptime': uptime,
        }
    
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
