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
from contextlib import asynccontextmanager
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
from .analysis.kill_zones import KillZoneChecker, claude_analysis_allowed
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
from .execution.scaling_position_sizer import (
    ScalingPositionSizer,
    SetupGrade,
    enforce_final_risk_cap,
)
from .services.news_service import NewsService
from .services.correlation_service import CorrelationService
from .services.trade_judge import JudgeOutcome, run_trade_judge
from .services.live_trade_gates import effective_max_daily_trades
from .services.entry_gates import (
    ZoneGateSettings,
    should_use_zone_gate,
)
from .utils.win_optimization import apply_demote_policy, apply_friday_session_gates
from .services.goal_tracker import GoalTracker
from .services.scaling_manager import ScalingManager, TradingMode
from .services.session_analytics import SessionAnalytics
from .services.trade_learning_service import TradeLearningService
from .services.gate_funnel import get_gate_funnel
from .services.claude_trade_manager import ClaudeTradeManager
from .services.pending_order_manager import PendingOrderManager
from .services.trade_reservations import TradeReservationLedger, ReservationState
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
            try:
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
                    judge_verdict=judge_verdict,
                    judge_reason=judge_reason[:1000] if judge_reason else None,
                    judge_risk_flags=judge_risk_flags,
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
                await session.rollback()
                logger.error(f"Failed to save trade to database: {e}")
    except Exception as e:
        logger.error(f"Failed to open database session for trade save: {e}")


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
            try:
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
                await session.rollback()
                logger.warning(f"Failed to save signal to analysis_logs: {e}")
    except Exception as e:
        logger.warning(f"Failed to open database session for signal save: {e}")


# =============================================================================
# STATIC RE-EVALUATION RULES
# -----------------------------------------------------------------------------
# These never change between calls, so they go in prompt-cached system blocks
# instead of being rebuilt into every re-eval user prompt (the re-eval loops run
# every ~60-120s on Opus 5, so caching pays for itself within one cycle).
# =============================================================================

POSITION_REEVAL_RULES = """You re-evaluate an open trading position described in the user message.

## SCOPE
Deliver only a HOLD / CLOSE / TIGHTEN decision for this open position. Do not widen
into market re-analysis, strategy advice, or unsolicited checklist items.

## Context -- BE PATIENT
Good entries need time to develop. Closing too early is worse than holding through
normal consolidation. Only recommend CLOSE if the original trade thesis is CLEARLY
invalidated (structure break against the position, key level lost, or R-multiple
below -0.5R). A trade that is flat or slightly positive is NOT a reason to close --
it means the market hasn't moved yet, not that the thesis is wrong. Let the trade breathe.

## Swing Exhaustion Check
Consider the 4-6 swing rule when evaluating the position:
- If the trade entered after 4+ swings into the POI with a sweep, the thesis is strong -- lean HOLD.
- If price is now making new swings AGAINST the position (4+ against), the thesis may be invalidating -- lean CLOSE.
- If price is consolidating/rounding near the entry, momentum may be shifting -- lean TIGHTEN.
- Use the 21 EMA as a trailing reference: if price has closed beyond the 21 EMA against the trade direction, consider TIGHTEN or CLOSE.

## Decision
Based on current market conditions, choose one:
1. HOLD - Keep position. This is the DEFAULT choice. Flat, slightly positive, or
   consolidating trades that haven't invalidated their thesis should be HELD.
   Let the trade develop.
2. CLOSE - ONLY if the trade thesis is CLEARLY invalidated: structure break against
   the position, key level lost, or R-multiple below -0.5R. A flat or barely
   profitable trade is NOT a reason to close. Stagnation is NOT invalidation.
3. TIGHTEN - Move stop loss closer to lock profits. Use when the trade is in
   profit and you want to protect gains while giving it room to run.

Default to HOLD unless there is strong evidence the thesis is broken.

## OUTPUT CONTRACT (strict)
The FIRST WORD of your reply MUST be exactly one of: HOLD, CLOSE, or TIGHTEN
(uppercase, nothing before it — no preamble, no markdown, no "My recommendation is").
After that first word, add a brief 1-2 sentence reasoning on the same or next line.
No filler and no restating these rules.
Example: "HOLD — thesis intact, price consolidating above the OB, still +0.4R."

<tone_preference>
Keep outputs reasonably concise. 1-2 sentences of reasoning max.
</tone_preference>
"""

PENDING_REEVAL_RULES = """You re-evaluate a pending (unfilled) order described in the user message.

## SCOPE
Deliver only a KEEP / CANCEL decision for this pending order. Do not widen into
unsolicited market commentary or strategy advice.

## Decision
The order has been waiting without filling. Choose one:
- KEEP: The setup is still valid, price may still reach the entry level.
- CANCEL: Market has moved away, structure has changed, or the opportunity has passed.

Consider: Is price moving TOWARD or AWAY from the entry? Has the entry zone been invalidated?

## OUTPUT CONTRACT (strict)
The FIRST WORD of your reply MUST be exactly KEEP or CANCEL (uppercase, nothing before it —
no preamble, no markdown). After that first word, add a brief 1-2 sentence reasoning.
No filler and no restating these rules.
Example: "KEEP — price still coiling below the FVG, entry zone intact."

<tone_preference>
Keep outputs reasonably concise. 1-2 sentences of reasoning max.
</tone_preference>
"""


class TradingBot:
    """
    Main trading bot class that orchestrates all components.
    
    Implements the ICT/Market Maker/FVG trading strategy using:
    - MT5 Client for market data and trade execution
    - Claude Opus 5 for intelligent chart analysis
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
        self.gate_funnel = None
        
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

        # Same-direction circuit breaker: after N consecutive same-direction
        # losses on a symbol in one UTC day, block that direction until tomorrow
        from .services.direction_circuit_breaker import DirectionLossTracker
        self._direction_loss_tracker = DirectionLossTracker()
        self._off_hours_mode: bool = False  # True when outside kill zones (soft-block)

        # Counterfactual journal: records every blocked/rejected trade and
        # later scores whether the block saved or cost R
        from .services.counterfactual_journal import get_counterfactual_journal
        self.counterfactual_journal = get_counterfactual_journal()
        self._last_counterfactual_score: Optional[datetime] = None
        
        # Analysis cooldown: throttle Claude per symbol. 270s (not 300) so
        # consecutive analyses land inside Anthropic's 5-minute prompt-cache
        # TTL — the cached system prompt stays warm instead of expiring
        # right before the next call.
        self._last_analysis_time: Dict[str, datetime] = {}  # symbol -> last analysis datetime
        self._analysis_cooldown_seconds: int = 270
        
        # Dynamic learnings: throttle doc updates to at most once per hour
        self._last_learnings_update: Optional[datetime] = None
        
        # Volatility spike response state
        self._volatility_pause_until: Optional[datetime] = None
        self._volatility_spike_expiry: Dict[str, datetime] = {}
        
        # Edge policy state: regime tags for fill telemetry, playbook cache
        self._last_regime_by_symbol: Dict[str, Optional[str]] = {}
        
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
        self._max_synced_deal_ids = 10000
    
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

            from .config import format_startup_config_banner
            _config_banner = format_startup_config_banner()
            if _config_banner:
                logger.warning(_config_banner)
            
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
            
            # Initialize strategy (advisory baseline — see _mechanical_setup_advisory).
            # Pass a session-configured checker so its session gate matches the bot's.
            self.strategy = ICTStrategy(
                structure_analyzer=market_structure,
                fvg_detector=fvg_detector,
                ob_detector=ob_detector,
                liquidity_mapper=liquidity_mapper,
                kill_zone_checker=KillZoneChecker(
                    allowed_sessions=settings.trading.allowed_sessions
                )
            )
            
            # Initialize execution components
            self.risk_manager = RiskManager(
                risk_per_trade=settings.trading.risk_per_trade,
                min_risk_reward=settings.trading.min_risk_reward
            )
            self.reservation_ledger = TradeReservationLedger(
                risk_manager=self.risk_manager,
                get_daily_trades=lambda: self.daily_trades,
                set_daily_trades=lambda v: setattr(self, 'daily_trades', v),
            )
            self._processed_pending_close_deals: set = set()
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
            
            # MFE-tuned per-symbol exit triggers (fail-open without data)
            try:
                await asyncio.wait_for(self._refresh_exit_overrides(), timeout=15)
            except Exception as e:
                logger.debug(f"Exit override refresh skipped: {e}")
            
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
            # Demo/simulation or explicit demo-data flag: AGGRESSIVE mode for data collection.
            # Live production accounts stay at NORMAL unless TRADING_DEMO_DATA_COLLECTION=true.
            from .services.scaling_manager import TradingMode
            if self._should_use_aggressive_data_collection():
                self.scaling_manager.current_mode = TradingMode.AGGRESSIVE
                logger.info("Scaling mode set to AGGRESSIVE (demo data collection)")
                from .api.routes.activity import add_activity
                add_activity("mode_change", "Trading mode set to AGGRESSIVE (init)", details={"mode": "AGGRESSIVE", "reason": "demo data collection"})
            else:
                logger.info("Scaling mode left at NORMAL (live/production account)")
            
            # Session Analytics - track performance by session
            logger.info("Initializing session analytics...")
            self.session_analytics = SessionAnalytics()
            
            # Trade Learning Service - Claude's learning system
            logger.info("Initializing trade learning service...")
            self.learning_service = TradeLearningService()
            self.gate_funnel = get_gate_funnel()
            
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
            self._wire_pending_reservation_accounting()
            
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
                                        if original_exp > datetime.now(timezone.utc):
                                            order.expiration = original_exp
                                            _restored_exp += 1
                                    except (ValueError, TypeError):
                                        pass
                                if meta:
                                    order.risk_percent = meta.get("risk_percent")
                                    order.reservation_id = meta.get("reservation_id")
                                    if order.reservation_id:
                                        self.reservation_ledger.restore_pending(
                                            reservation_id=order.reservation_id,
                                            symbol=order.symbol,
                                            ticket=order.ticket,
                                            risk_percent=order.risk_percent or 0.0,
                                        )
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
                        f"⏰ Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
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
        
        self._last_state_save = datetime.now(timezone.utc)

        try:
            while self.running:
                await self._trading_cycle()

                # Periodic state save every 60 seconds
                if (datetime.now(timezone.utc) - self._last_state_save).total_seconds() >= 60:
                    try:
                        save_full_state(self)
                        self._last_state_save = datetime.now(timezone.utc)
                    except Exception as e:
                        logger.debug(f"Periodic state save failed: {e}")

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
        vol_check_cycle = 0
        
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
                    
                
                # Volatility spike check every 3 cycles (~30 seconds).
                # Runs whenever positions OR pending orders are exposed —
                # covers pending-cancel and entry-pause even with no fills yet.
                vol_check_cycle += 1
                if vol_check_cycle >= 3:
                    vol_check_cycle = 0
                    _has_exposure = bool(
                        (self.position_manager and self.position_manager.positions)
                        or (
                            hasattr(self, 'pending_order_manager')
                            and self.pending_order_manager
                            and self.pending_order_manager.get_active_orders()
                        )
                    )
                    if _has_exposure:
                        try:
                            volatility_alert = await self._check_volatility()
                            if volatility_alert:
                                logger.warning(f"POS-MGR: HIGH VOLATILITY: {volatility_alert['message']}")
                                await self._handle_high_volatility(volatility_alert)
                        except Exception as e:
                            logger.warning(f"POS-MGR volatility check error: {e}")
                
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
                            await self._apply_pending_fill_transfers(
                                sync_result.get('filled_position_events', [])
                            )
                        if sync_result.get('filled_closed', 0) > 0:
                            logger.info(
                                f"POS-MGR: {sync_result['filled_closed']} pending order(s) filled then closed"
                            )
                            await self._process_pending_closed_trade_events(
                                sync_result.get('closed_trade_events', [])
                            )
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
            # Cleanup expired signal hashes every cycle (they expire hourly)
            self._cleanup_expired_signal_hashes()
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
                    # Must be UTC-aware — naive datetime.min crashes the cycle:
                    # "can't subtract offset-naive and offset-aware datetimes"
                    self._last_firecrawl_refresh = datetime.min.replace(
                        tzinfo=timezone.utc
                    )

                from .utils.datetime_utils import as_utc
                time_since_refresh = (
                    datetime.now(timezone.utc)
                    - as_utc(self._last_firecrawl_refresh)
                ).total_seconds() / 60
                refresh_min = getattr(getattr(settings, 'firecrawl', None), 'refresh_minutes', 15)
                
                if time_since_refresh >= refresh_min:
                    try:
                        await self.firecrawl_service.refresh_all(cycle_symbols)
                        self._last_firecrawl_refresh = datetime.now(timezone.utc)
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

            # Score counterfactuals at most once per hour (background task —
            # OHLCV fetches must not delay the trading cycle)
            _cf_now = datetime.now(timezone.utc)
            if (
                self._last_counterfactual_score is None
                or (_cf_now - self._last_counterfactual_score).total_seconds() >= 3600
            ):
                self._last_counterfactual_score = _cf_now
                try:
                    asyncio.create_task(
                        self.counterfactual_journal.score_pending(
                            self.data_fetcher.get_ohlcv
                        )
                    )
                except Exception as _cf_err:
                    logger.debug(f"[COUNTERFACTUAL] score task failed to start: {_cf_err}")
            
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
            
            # Check if we've hit daily trade limit (tier + mode caps, not config default alone)
            _max_daily = settings.trading.max_daily_trades
            try:
                _limit_account = await self.mt5_client.get_account_info()
                if _limit_account:
                    _max_daily = self._effective_max_daily_trades(_limit_account.balance)
            except Exception:
                pass
            if not _blocked and self.daily_trades >= _max_daily:
                _blocked = True
                _block_reason = f"Daily trade limit reached ({self.daily_trades}/{_max_daily})"
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
            # DEFENSIVE MODE = EXPLICIT HALT
            # Severe drawdown means stop entering, not "reject everything at
            # a 0.90 bar no signal meets". Position management already ran.
            # ============================================
            if self.scaling_manager and self.scaling_manager.current_mode == TradingMode.DEFENSIVE:
                if not getattr(self, "_defensive_halt_logged", False):
                    self._defensive_halt_logged = True
                    logger.warning(
                        "TRADING HALTED: DEFENSIVE mode (severe drawdown) — "
                        "no new entries until performance recovers"
                    )
                    from .api.routes.activity import add_activity
                    add_activity(
                        "trading_halted",
                        "Trading halted — DEFENSIVE mode (severe drawdown). "
                        "Position management continues.",
                        details={"mode": "defensive"},
                    )
                    if bot_state:
                        bot_state.error(None, "Trading halted — DEFENSIVE mode")
                print("[CYCLE] BLOCKED for new trades: DEFENSIVE mode halt (positions still being managed)", flush=True)
                return
            else:
                self._defensive_halt_logged = False
            
            # ============================================
            # STEP 2: CHECK NEWS BLACKOUT
            # ============================================
            if settings.trading.news_gates_enabled and self.news_service:
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

                # Fail-closed when economic calendar feed is stale/unreliable
                if self.news_service.is_calendar_unreliable():
                    logger.warning(
                        "News calendar UNKNOWN/stale — blocking all new trades until refreshed (fail-closed)"
                    )
                    from .api.routes.activity import add_activity
                    add_activity(
                        "news_calendar_stale",
                        "News calendar stale — fail-closed, no new trades",
                        details={"fail_closed": True},
                    )
                    print("[CYCLE] BLOCKED: News calendar stale (fail-closed)", flush=True)
                    return
            elif not settings.trading.news_gates_enabled:
                logger.info(
                    "News gates disabled (TRADING_NEWS_GATES_ENABLED=false) — "
                    "skipping blackout/stale-calendar blocks"
                )
            
            # ============================================
            # STEP 2b: FRIDAY PRE-CLOSE (Weekend Gap Protection)
            # Close gate (16:30+) and entry block (noon+) are independent.
            # ============================================
            import pytz
            est_tz = pytz.timezone('US/Eastern')
            now_est = datetime.now(est_tz)
            friday = apply_friday_session_gates(
                now_est,
                symbols=list(cycle_symbols),
                crypto_symbols=set(self.CRYPTO_SYMBOLS),
            )
            if friday.close_forex:
                logger.warning("FRIDAY PRE-CLOSE: Closing forex positions before weekend")
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
            if friday.entry_symbols != list(cycle_symbols):
                logger.info("Friday afternoon - no new forex entries")
                cycle_symbols = friday.entry_symbols
                if not cycle_symbols:
                    return
            
            print(f"[CYCLE] Passed drawdown/profit/limit checks, checking kill zone...", flush=True)
            
            # Check if we're in a valid kill zone
            session = self.kill_zone_checker.get_current_session()
            print(f"[CYCLE] Session: {session.session_name}, is_tradeable={session.is_tradeable}, is_kill_zone={session.is_kill_zone}", flush=True)
            if not claude_analysis_allowed(
                session.is_tradeable,
                claude_kill_zone_only=settings.trading.claude_kill_zone_only,
            ):
                next_kz = session.next_kill_zone or "next kill zone"
                mins = session.next_kill_zone_in_minutes
                eta = f" in {mins}min" if mins is not None else ""
                print(
                    f"[CYCLE] Outside KZ ({session.session_name}) — Claude skipped until {next_kz}{eta}",
                    flush=True,
                )
                logger.info(
                    f"Outside kill zone ({session.session_name}) — "
                    f"Claude hard-skipped until {next_kz}{eta}"
                )
                self._off_hours_mode = True
                if bot_state:
                    bot_state.start_cycle(session.session_name, session.is_tradeable)
                    bot_state.cycle_complete()
                return
            if not session.is_tradeable:
                # Outside kill zones only crypto may continue (off-hours mode
                # hard-rejects entries via the off_hours_cap gate anyway).
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
                        now = datetime.now(timezone.utc)
                        if last_run is not None:
                            elapsed = (now - last_run).total_seconds()
                            if elapsed < self._analysis_cooldown_seconds:
                                return
                        self._last_analysis_time[sym] = now
                        
                        print(f"[CYCLE] Analyzing {sym} (crypto={is_crypto})...", flush=True)
                        # Per-symbol timeout to prevent one slow symbol from blocking the
                        # batch. Covers Opus 5 low-effort streamed analysis (16k budget,
                        # thinking + images can still take minutes) + judge + execution.
                        await asyncio.wait_for(
                            self._analyze_and_trade(sym, is_crypto=is_crypto),
                            timeout=420.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"Analysis of {sym} TIMED OUT after 420s - skipping")
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
    
    def _wire_pending_reservation_accounting(self) -> None:
        """Give pending lifecycle operations the authoritative reservation ledger."""
        self.pending_order_manager.set_budget_reclaim(
            risk_manager=self.risk_manager,
            reservation_ledger=self.reservation_ledger,
            get_daily_trades=lambda: self.daily_trades,
            set_daily_trades=lambda value: setattr(self, "daily_trades", value),
        )
        self.pending_order_manager.set_decision_recorder(
            self._record_pending_lifecycle_decision
        )

    async def _record_pending_lifecycle_decision(
        self, outcome_type: str, order, reason: str
    ) -> Optional[str]:
        return await self._record_terminal_decision(
            outcome_type,
            order.symbol,
            direction=order.direction,
            entry=order.price,
            sl=order.stop_loss or 0.0,
            tp=order.take_profit or 0.0,
            reason=reason,
        )

    @asynccontextmanager
    async def _trade_reservation_scope(
        self,
        symbol: str,
        signal_id: Optional[str] = None,
        risk_percent: float = 0.0,
    ):
        """Release an attempt unless ownership transfers to pending/position."""
        reservation = self.reservation_ledger.reserve(
            symbol=symbol,
            signal_id=signal_id,
            risk_percent=risk_percent,
        )
        try:
            yield reservation
        finally:
            if reservation.state == ReservationState.RESERVED:
                self.reservation_ledger.release(reservation)

    async def _cancel_pending_for_replacement(self, old_order) -> bool:
        """Cancel the old pending owner without touching the incoming attempt."""
        return await self.pending_order_manager.cancel_order(
            old_order.ticket,
            reason="replaced_by_newer",
        )

    def _reject_reservation_attempt(self, reservation) -> bool:
        """Release reservation on post-reservation rejection."""
        if hasattr(self, "reservation_ledger") and self.reservation_ledger:
            self.reservation_ledger.release(reservation)
        return False

    async def _record_terminal_decision(
        self,
        outcome_type: str,
        symbol: str,
        *,
        gate_id: Optional[str] = None,
        direction: str = "",
        entry: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        confidence: float = 0.0,
        session: str = "",
        mode: str = "",
        reason: str = "",
        details: Optional[dict] = None,
        judge_verdict: Optional[str] = None,
        market_snapshot_ref: Optional[str] = None,
        confidence_components: Optional[dict] = None,
    ) -> Optional[str]:
        if confidence_components is None:
            confidence_components = getattr(self, "_last_confidence_components", None)

        # Counterfactual journal: every terminal non-trade decision is
        # recorded so blocked trades can be scored against realized price.
        _cf_journal = getattr(self, "counterfactual_journal", None)
        if _cf_journal is not None:
            try:
                _cf_journal.record(
                    symbol=symbol,
                    gate_id=gate_id or outcome_type,
                    outcome_type=outcome_type,
                    direction=direction,
                    confidence=confidence,
                    entry=entry or None,
                    sl=sl or None,
                    tp=tp or None,
                    reason=reason,
                )
            except Exception as _cf_err:
                logger.debug(f"[COUNTERFACTUAL] record failed: {_cf_err}")

        funnel = getattr(self, "gate_funnel", None) or get_gate_funnel()
        return await funnel.record_decision(
            outcome_type,
            symbol,
            gate_id=gate_id,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            confidence=confidence,
            session=session,
            mode=mode or getattr(self, "_trading_mode", "normal"),
            reason=reason,
            details=details,
            judge_verdict=judge_verdict,
            market_snapshot_ref=market_snapshot_ref,
            confidence_components=confidence_components,
        )

    async def _reject_and_record(
        self,
        reservation,
        outcome_type: str,
        symbol: str,
        **fields,
    ) -> bool:
        await self._record_terminal_decision(outcome_type, symbol, **fields)
        return self._reject_reservation_attempt(reservation)

    def _accept_nonzero_lots(self, reservation, lots) -> bool:
        if lots <= 0:
            return self._reject_reservation_attempt(reservation)
        return True

    def _accept_precheck(self, reservation, precheck) -> bool:
        if not precheck.can_execute:
            return self._reject_reservation_attempt(reservation)
        return True

    def _accept_final_rr(self, reservation, entry, sl, tp) -> bool:
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        final_rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if final_rr < 1.0 and sl_dist > 0:
            return self._reject_reservation_attempt(reservation)
        return True

    def _accept_execution_mode(self, reservation, is_blocked) -> bool:
        if is_blocked:
            return self._reject_reservation_attempt(reservation)
        return True

    def _accept_tick_refine(self, reservation, tick_ok) -> bool:
        if not tick_ok:
            return self._reject_reservation_attempt(reservation)
        return True

    def _accept_verified_position(self, reservation, ticket, positions) -> bool:
        if not positions:
            return self._reject_reservation_attempt(reservation)
        return True

    async def _apply_pending_fill_transfers(self, events) -> None:
        """Transfer reservation ownership from pending ticket to position ticket."""
        from .services.pending_order_manager import FilledPositionEvent

        if not events:
            return

        for event in events:
            if not isinstance(event, FilledPositionEvent):
                continue

            reservation = None
            if hasattr(self, "reservation_ledger") and self.reservation_ledger:
                reservation = self.reservation_ledger.get_for_ticket(event.order_ticket)
                if not reservation and event.reservation_id:
                    reservation = self.reservation_ledger.get_by_id(event.reservation_id)
                if reservation:
                    self.reservation_ledger.transfer_to_position(
                        reservation,
                        event.position_ticket,
                    )

            if not self.position_manager:
                continue

            position = self.position_manager.get_position(event.position_ticket)
            if not position:
                continue

            position.order_ticket = event.order_ticket
            position.reservation_id = (
                event.reservation_id
                or (reservation.reservation_id if reservation else None)
            )
            await self.position_manager._persist_and_wait(position)

            order_snapshot = None
            if hasattr(self, "pending_order_manager") and self.pending_order_manager:
                for hist_order in reversed(self.pending_order_manager.order_history):
                    if hist_order.ticket == event.order_ticket:
                        order_snapshot = hist_order
                        break
            if order_snapshot:
                await self._record_terminal_decision(
                    "pending_filled",
                    order_snapshot.symbol,
                    direction=order_snapshot.direction,
                    entry=order_snapshot.fill_price or order_snapshot.price,
                    sl=order_snapshot.stop_loss or 0.0,
                    tp=order_snapshot.take_profit or 0.0,
                    reason="Pending order filled",
                    details={
                        "order_ticket": event.order_ticket,
                        "position_ticket": event.position_ticket,
                    },
                )

    def _mechanical_setup_advisory(self, symbol: str, htf_df, ltf_df):
        """
        Run the rule-based ICTStrategy as an advisory cross-check.

        This NEVER drives execution. The setup (if any) is added to Claude's
        context as a mechanical baseline, and mechanical-vs-Claude agreement
        is logged so the LLM's value-add over pure rules can be measured.
        """
        if not self.strategy or htf_df is None or ltf_df is None:
            return None
        try:
            # ICTStrategy's SL buffer logic reads df.attrs['symbol']
            ltf_df.attrs['symbol'] = symbol
            setup = self.strategy.analyze(
                htf_data=htf_df,
                ltf_data=ltf_df,
                symbol=symbol,
                htf_name='H4',
                ltf_name=settings.timeframes.execution_tf,
            )
            return setup.to_dict() if setup else None
        except Exception as e:
            logger.debug(f"[MECH] ICTStrategy advisory failed for {symbol}: {e}")
            return None

    async def _analyze_and_trade(self, symbol: str, is_crypto: bool = False):
        """
        Analyze a symbol and execute trade if valid setup found.

        Args:
            symbol: Trading symbol to analyze
            is_crypto: Whether this is a crypto symbol (24/7 trading)
        """
        from .services.trade_pipeline import TradePipeline

        if not hasattr(self, "_trade_pipeline"):
            self._trade_pipeline = TradePipeline(self)
        await self._trade_pipeline.run(symbol, is_crypto=is_crypto)

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
            
            if not hasattr(self, "_analysis_orchestrator"):
                from .services.analysis_orchestrator import AnalysisOrchestrator
                self._analysis_orchestrator = AnalysisOrchestrator()
            analysis_results = self._analysis_orchestrator.run_core_analysis(symbol, df)
            
            # Volume telemetry (simulation mode)
            try:
                volume_analysis = analysis_results.get("volume", {})
                if bot_state and isinstance(volume_analysis, dict):
                    bot_state.volume_analysis_complete(
                        symbol,
                        volume_analysis.get("relative_volume", 1.0),
                        volume_analysis.get("volume_trend", ""),
                        len(volume_analysis.get("spike_bars", [])),
                        volume_analysis.get("relative_volume", 1.0) < 0.5,
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
                largest_loser.close_reason = "margin_emergency"
                try:
                    await self._handle_position_close(largest_loser)
                except Exception as cb_err:
                    logger.error(f"Close lifecycle error for {largest_loser.ticket}: {cb_err}")
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
                            delta = datetime.now(timezone.utc) - open_dt.replace(tzinfo=timezone.utc)
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
                pos.closed_profit_loss = weakest["pnl"]
                pos.close_reason = "position_replaced"
                await self._handle_position_close(pos)
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
    
    VOLATILITY_PAUSE_MINUTES = 15

    async def _check_volatility(self) -> Optional[dict]:
        """
        Check for abnormal volatility on all configured trading symbols
        plus any symbols with open positions.

        Returns {"message": str, "symbols": [spiking symbols]} or None.
        """
        try:
            import pandas as pd

            volatility_alerts = []
            spiking_symbols = []

            check_symbols = list(settings.trading.symbols)
            if self.position_manager:
                for pos in self.position_manager.get_all_positions():
                    if pos.symbol not in check_symbols:
                        check_symbols.append(pos.symbol)

            for symbol in check_symbols:
                try:
                    df = await self.data_fetcher.get_ohlcv(
                        symbol=symbol,
                        timeframe="M5",
                        count=50
                    )
                    
                    if df is None or df.empty or len(df) < 15:
                        continue
                    
                    # Calculate ATR
                    high_low = df['high'] - df['low']
                    atr = high_low.rolling(14).mean().iloc[-1]
                    current_range = float(df['high'].iloc[-1] - df['low'].iloc[-1])
                    
                    # Alert if current candle range is 3x normal ATR
                    if pd.notna(atr) and atr > 0 and current_range > atr * 3:
                        volatility_alerts.append(
                            f"{symbol}: Range {current_range:.5f} is {current_range/atr:.1f}x ATR"
                        )
                        spiking_symbols.append(symbol)
                        
                except Exception as e:
                    logger.debug(f"Error checking volatility for {symbol}: {e}")
            
            if volatility_alerts:
                return {
                    "message": "; ".join(volatility_alerts),
                    "symbols": spiking_symbols,
                }
            return None
            
        except Exception as e:
            logger.error(f"Error in volatility check: {e}")
            return None
    
    async def _handle_high_volatility(self, alert):
        """
        Defensive response to a volatility spike (never closes positions):

        1. Pause new entries for VOLATILITY_PAUSE_MINUTES.
        2. Cancel unfilled pending orders on spiking symbols.
        3. Tighten giveback protection for in-profit positions via the
           position manager (existing machinery, no broker SL changes).
        """
        try:
            if isinstance(alert, dict):
                message = alert.get("message", "")
                symbols = list(alert.get("symbols", []))
            else:
                message = str(alert)
                symbols = []

            now = datetime.now(timezone.utc)
            pause_until = now + timedelta(minutes=self.VOLATILITY_PAUSE_MINUTES)

            # Dedupe notifications: only announce symbols not already spiking
            expiry_map = getattr(self, "_volatility_spike_expiry", None)
            if expiry_map is None or not isinstance(expiry_map, dict):
                expiry_map = {}
            newly_spiking = [
                s for s in symbols
                if s not in expiry_map or expiry_map[s] < now
            ]
            for s in symbols:
                expiry_map[s] = pause_until
            self._volatility_spike_expiry = expiry_map

            # 1. Pause new entries (checked by analyze_and_trade_runner)
            self._volatility_pause_until = pause_until

            # 3. Tighten giveback protection while the spike window is active
            if self.position_manager:
                self.position_manager.volatility_tighten_until = pause_until

            # 2. Cancel unfilled pending orders on spiking symbols
            cancelled = []
            if self.pending_order_manager:
                for sym in symbols:
                    try:
                        for order in self.pending_order_manager.get_active_orders(symbol=sym):
                            ok = await self.pending_order_manager.cancel_order(
                                order.ticket, reason="volatility_spike"
                            )
                            if ok:
                                cancelled.append(order.ticket)
                                logger.warning(
                                    f"[VOLATILITY] Cancelled pending #{order.ticket} "
                                    f"{sym} — spike in progress"
                                )
                    except Exception as cancel_err:
                        logger.warning(f"[VOLATILITY] Pending cancel error for {sym}: {cancel_err}")

            from .api.routes.activity import add_activity
            add_activity(
                "volatility_alert",
                f"High volatility: {message}",
                None,
                {
                    "alert": message,
                    "symbols": symbols,
                    "entries_paused_until": pause_until.isoformat(),
                    "pending_cancelled": cancelled,
                    "giveback_tightened": True,
                }
            )

            if newly_spiking:
                logger.warning(
                    f"[VOLATILITY] Spike on {', '.join(newly_spiking)} — new entries "
                    f"paused {self.VOLATILITY_PAUSE_MINUTES}min, "
                    f"{len(cancelled)} pending(s) cancelled, giveback tightened"
                )
                await notify(
                    NotificationType.ALERT,
                    f"⚡ VOLATILITY SPIKE\n\n"
                    f"{message}\n\n"
                    f"Entries paused {self.VOLATILITY_PAUSE_MINUTES}min | "
                    f"{len(cancelled)} pending cancelled | protection tightened"
                )
            
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
    ) -> JudgeOutcome:
        """Shared fail-closed judge adapter for regular entries."""
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

        entry = signal_dict['entry_price']
        sl = signal_dict['stop_loss'] or 0
        tp = signal_dict['take_profit'] or 0
        sl_distance = abs(entry - sl) if sl else 0
        tp_distance = abs(tp - entry) if tp else 0
        risk_reward = tp_distance / sl_distance if sl_distance > 0 else 0

        account_balance = 0.0
        spec = None
        if hasattr(self, 'mt5_client') and self.mt5_client:
            try:
                acct = await self.mt5_client.get_account_info()
                if acct:
                    account_balance = acct.balance
            except Exception as e:
                logger.debug(f"Could not get account balance: {e}")

        position_size_pct = 0.0
        _lots = getattr(position_size, 'lots', 0.01)
        _at_broker_minimum = False
        if account_balance > 0 and sl_distance > 0:
            from .config import get_symbol_spec
            spec = get_symbol_spec(symbol)
            risk_amount = sl_distance * _lots * spec.contract_size
            position_size_pct = risk_amount / account_balance
            _at_broker_minimum = (_lots <= spec.volume_min)

        session_name = ""
        if self.session_analytics:
            current_session = self.session_analytics.get_current_session()
            session_name = current_session.value if current_session else ""

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

        learning_context = ""
        if self.learning_service:
            try:
                learning_context = await self.learning_service.build_context_for_claude(symbol, session_name)
            except Exception as e:
                logger.debug(f"[JUDGE] Could not get learning context: {e}")

        outcome = await run_trade_judge(
            self.claude_client,
            signal_dict,
            risk_metrics,
            learning_context,
            timeout=45.0,  # Opus 5 + adaptive thinking; 8s would fail-close every trade
        )
        logger.info(
            f"[JUDGE] {symbol} {trade_signal.direction}: {outcome.verdict.value} "
            f"— {outcome.reason} | flags: {outcome.risk_flags}"
        )
        return outcome

    async def _run_reversal_trade_judge(
        self,
        symbol: str,
        trade_signal,
        current_price: float,
        risk_metrics: dict,
        learning_context: str = "",
    ) -> JudgeOutcome:
        """Shared fail-closed judge adapter for reversal entries."""
        signal_dict = {
            "symbol": symbol,
            "direction": trade_signal.direction,
            "confidence": trade_signal.confidence,
            "entry_price": trade_signal.entry_price or current_price,
            "stop_loss": trade_signal.stop_loss,
            "take_profit": trade_signal.take_profit,
            "reasoning": getattr(trade_signal, "reasoning", ""),
            "trade_type": getattr(trade_signal, "trade_type", "intraday"),
            "reversal_reentry": True,
        }
        return await run_trade_judge(
            self.claude_client,
            signal_dict,
            risk_metrics,
            learning_context,
            timeout=45.0,  # Opus 5 + adaptive thinking; 8s would fail-close every trade
        )

    def _build_pipeline_context(
        self,
        *,
        symbol: str,
        trade_signal,
        market_data: dict,
        analysis_results: dict,
        pd_analysis,
        current_price: float,
        actual_rr: float,
        is_crypto: bool,
        is_counter_trend_scalp: bool,
    ):
        from datetime import datetime, timezone
        from .config import get_symbol_spec
        from .services.trade_context import TradeContext

        _sym_spec = get_symbol_spec(symbol)
        _is_index = _sym_spec.category == "index" if _sym_spec else False
        _weak_hours = tuple(settings.trading.weak_hours_by_symbol.get(symbol, []))
        ctx = TradeContext.from_signal(
            symbol=symbol,
            trade_signal=trade_signal,
            market_data=market_data,
            analysis_results=analysis_results,
            current_price=current_price,
            is_crypto=is_crypto,
            pd_analysis=pd_analysis,
            off_hours_mode=bool(getattr(self, "_off_hours_mode", False)),
            post_cooldown=(
                hasattr(self, "_post_cooldown_symbols")
                and symbol in self._post_cooldown_symbols
            ),
            utc_hour=datetime.now(timezone.utc).hour,
            weak_hours=_weak_hours,
            is_index=_is_index,
            is_counter_trend_scalp=is_counter_trend_scalp,
            actual_rr=actual_rr,
        )
        ctx.scaling_aggressive = (
            self.scaling_manager is not None
            and self.scaling_manager.current_mode.value == "aggressive"
        )
        _zone_settings = ZoneGateSettings(
            gate_mode=settings.trading.zone_gate_mode,
            misaligned_min_confidence=settings.trading.zone_misaligned_min_confidence,
            misaligned_min_rr=settings.trading.zone_misaligned_min_rr,
            equilibrium_min_confidence=settings.trading.zone_equilibrium_min_confidence,
            disabled_symbols=tuple(settings.trading.zone_gate_disabled_symbols),
        )
        _use_zone = should_use_zone_gate(
            pd_analysis is not None,
            _zone_settings.gate_mode,
            symbol,
            _zone_settings.disabled_symbols,
            is_counter_trend_scalp=is_counter_trend_scalp,
        )
        return ctx, _zone_settings, _use_zone

    def _session_for_gates(self):
        _session = (
            self.kill_zone_checker.get_current_session()
            if self.kill_zone_checker
            else None
        )
        _name = (_session.session_name if _session else "").lower()
        _is_kill = _session.is_kill_zone if _session else False
        return _name, _is_kill

    async def _handle_pipeline_gate_block(
        self, symbol: str, outcome, *, ctx=None
    ) -> None:
        from .services.post_claude_gates import build_reject_details

        logger.warning(f"[GATE] {symbol}: {outcome.reason}")
        print(f"[BLOCKED] {symbol}: {outcome.reason}", flush=True)
        gate_path = list(getattr(outcome, "gate_path", None) or [])
        if ctx is not None and getattr(ctx, "gate_path", None):
            gate_path = list(ctx.gate_path)
        direction = getattr(ctx, "direction", "") if ctx else ""
        entry = 0.0
        sl = 0.0
        tp = 0.0
        confidence = getattr(ctx, "confidence", 0.0) if ctx else 0.0
        if ctx is not None and getattr(ctx, "trade_signal", None):
            sig = ctx.trade_signal
            entry = getattr(sig, "entry_price", None) or getattr(ctx, "current_price", 0.0) or 0.0
            sl = getattr(sig, "stop_loss", None) or 0.0
            tp = getattr(sig, "take_profit", None) or 0.0
            confidence = getattr(sig, "confidence", confidence) or confidence
        details = build_reject_details(
            gate_path=gate_path,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            confidence=confidence,
        )
        await self._record_terminal_decision(
            "mechanical_reject",
            symbol,
            gate_id=getattr(outcome, "gate_id", None) or "",
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            confidence=confidence,
            reason=outcome.reason,
            details=details,
        )
        if bot_state:
            bot_state.trade_decision(symbol, "blocked", outcome.reason)
            if outcome.gate_id in ("low_confluence", "min_confidence", "scaling_manager"):
                bot_state.symbol_complete(symbol, outcome.gate_id)

    def _effective_max_daily_trades(self, equity: float) -> int:
        """Min of tier, scaling-mode, config, and optional optimizer gate override."""
        gate_override = getattr(settings.trading, "gate_max_daily_trades", None)
        return effective_max_daily_trades(
            equity,
            self.position_sizer,
            self.scaling_manager,
            settings.trading.max_daily_trades,
            gate_override=gate_override,
        )

    @staticmethod
    def _session_key_for_edge(session_name: str) -> str:
        """Map kill-zone session label to scaling_manager session key."""
        name = (session_name or "").lower()
        if "london close" in name:
            return "london_close"
        if "london" in name:
            return "london"
        if "new york" in name or name.startswith("ny"):
            return "new_york"
        if "asian" in name:
            return "asian"
        return "unknown"

    def _apply_judge_outcome(self, outcome: JudgeOutcome, reservation=None) -> bool:
        """Release reservation and block when judge is unavailable/reject."""
        if outcome.blocks_execution():
            self._release_trade_reservation(reservation)
            return True
        return False

    def _enforce_final_risk_before_order(
        self,
        *,
        symbol: str,
        entry: float,
        stop_loss: float,
        lots: float,
        account_equity: float,
        symbol_spec,
        risk_fraction: Optional[float] = None,
    ):
        risk_fraction = risk_fraction or (
            self.risk_manager.risk_per_trade if self.risk_manager else 0.02
        )
        return enforce_final_risk_cap(
            account_equity,
            risk_fraction,
            entry,
            stop_loss,
            lots,
            symbol_spec,
            symbol=symbol,
        )

    async def _place_market_with_final_risk(
        self,
        *,
        symbol: str,
        direction: str,
        lots: float,
        stop_loss: float,
        take_profit: float,
        account_equity: float,
        symbol_spec,
        comment: str = "ICT_Bot",
        risk_fraction: Optional[float] = None,
    ):
        allowed, _, reason = self._enforce_final_risk_before_order(
            symbol=symbol,
            entry=await self._current_market_entry(symbol, direction),
            stop_loss=stop_loss,
            lots=lots,
            account_equity=account_equity,
            symbol_spec=symbol_spec,
            risk_fraction=risk_fraction,
        )
        if reason:
            logger.warning(f"[FINAL-RISK] {symbol}: blocked market order — {reason}")
            return None
        result = await self.order_manager.place_market_order(
            symbol=symbol,
            direction=direction,
            volume=allowed,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=comment,
        )
        # Return failed OrderResult so callers can reconcile / log broker errors.
        # Only risk-cap blocks return None (see above).
        if result is None:
            from .execution.order_manager import OrderResult, OrderStatus
            return OrderResult(
                success=False,
                order_id=None,
                ticket=None,
                status=OrderStatus.REJECTED,
                message="No result from broker place_market_order",
            )
        return result

    async def _place_pending_with_final_risk(
        self,
        *,
        symbol: str,
        direction: str,
        order_type: str,
        price: float,
        lots: float,
        stop_loss: float,
        take_profit: float,
        account_equity: float,
        symbol_spec,
        expiration_minutes: Optional[int] = None,
        comment: str = "ICT_Bot",
        risk_fraction: Optional[float] = None,
    ):
        allowed, _, reason = self._enforce_final_risk_before_order(
            symbol=symbol,
            entry=price,
            stop_loss=stop_loss,
            lots=lots,
            account_equity=account_equity,
            symbol_spec=symbol_spec,
            risk_fraction=risk_fraction,
        )
        if reason:
            logger.warning(f"[FINAL-RISK] {symbol}: blocked pending order — {reason}")
            return None
        kwargs = {
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "volume": allowed,
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "comment": comment,
        }
        if expiration_minutes is not None:
            kwargs["expiration_minutes"] = expiration_minutes
        result = await self.order_manager.place_pending_order(**kwargs)
        # Return failed OrderResult so callers can reconcile / log broker errors.
        # Only risk-cap blocks return None (see above).
        if result is None:
            from .execution.order_manager import OrderResult, OrderStatus
            return OrderResult(
                success=False,
                order_id=None,
                ticket=None,
                status=OrderStatus.REJECTED,
                message="No result from broker place_pending_order",
            )
        return result

    async def _current_market_entry(self, symbol: str, direction: str) -> float:
        try:
            tick = await self.mt5_client.get_tick(symbol)
            if tick:
                return tick.ask if direction == "long" else tick.bid
        except Exception:
            pass
        return 0.0
    
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
            if self.scaling_manager:
                self.scaling_manager.update_equity(current_value)
                daily_drawdown = self.scaling_manager.calculate_daily_drawdown(current_value)
                daily_drawdown = max(0.0, daily_drawdown)
            
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
                
                progress = self.goal_tracker.calculate_progress(account.equity)
                
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
                        from .utils.datetime_utils import as_utc
                        _pos_age = (
                            f"{(datetime.now(timezone.utc) - as_utc(position.open_time)).total_seconds() / 60:.0f}min"
                        )
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
                        since_last = (datetime.now(timezone.utc) - last_state["time"]).total_seconds()
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
                                delta = datetime.now(timezone.utc) - open_dt.replace(tzinfo=timezone.utc)
                            hours_open = delta.total_seconds() / 3600
                    except Exception as e:
                        logger.debug(f"Could not compute hours_open for re-eval: {e}")
                    
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
                    except Exception as e:
                        logger.debug(f"Could not get MTF bias for re-eval: {e}")
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
                    except Exception as e:
                        logger.debug(f"Could not get learning context: {e}")
                        pass
                    try:
                        # Real spread
                        _sym_info = await self.mt5_client.get_symbol_info(position.symbol)
                        if _sym_info and getattr(_sym_info, 'ask', 0) > 0:
                            _spread = _sym_info.ask - _sym_info.bid
                            _spread_pct = _spread / ((_sym_info.ask + _sym_info.bid) / 2) * 100
                            _reeval_extra += f"\n- Current Spread: {_spread:.5f} ({_spread_pct:.3f}%)\n"
                    except Exception as e:
                        logger.debug(f"Could not get spread for re-eval: {e}")
                        pass
                    
                    # Build context for Claude. The evaluation rules (BE PATIENT, swing
                    # exhaustion check, decision options, OUTPUT CONTRACT) live in the
                    # prompt-cached POSITION_REEVAL_RULES system block.
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

Apply the evaluation rules from the system message and reply per the OUTPUT CONTRACT
(first word exactly HOLD, CLOSE, or TIGHTEN).
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
                    
                    # Get Claude's recommendation (with timeout and validation).
                    # Opus 5: no temperature; adaptive thinking + light effort. Budget
                    # covers thinking + reply; timeout raised for the bigger model.
                    try:
                        response = await asyncio.wait_for(
                            self.claude_client.async_client.messages.create(
                                model=self.claude_client.model_light,
                                max_tokens=3000,  # thinking + short HOLD/CLOSE/TIGHTEN reply
                                thinking={"type": "adaptive"},
                                output_config={"effort": self.claude_client.effort_light},
                                system=[{
                                    "type": "text",
                                    "text": POSITION_REEVAL_RULES,
                                    "cache_control": {"type": "ephemeral"},
                                }],
                                messages=[{
                                    "role": "user",
                                    "content": chart_content
                                }]
                            ),
                            timeout=60  # timeout per position (Opus 5 + thinking)
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
                    
                    self.claude_client._record_usage("position_reeval", response)
                    
                    # Skip any leading thinking block; take the text content.
                    raw_reeval = self.claude_client._extract_text(response).strip()
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
                        "time": datetime.now(timezone.utc),
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
                            # Run the unified close lifecycle (P/L from MT5,
                            # streaks, scaling, risk release, learning) BEFORE
                            # removing from tracking
                            position.close_reason = "claude_close"
                            try:
                                await self._handle_position_close(position)
                            except Exception as cb_err:
                                logger.error(f"Close lifecycle error for {position.ticket}: {cb_err}")
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
                                    self.position_manager._schedule_persist(position)
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
                    age_str = f"{(datetime.now(timezone.utc) - order.created_at).total_seconds() / 60:.0f}min"
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
                                print(
                                    f"[TRADES] {symbol}: Reservation released if owned, "
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
                    age_minutes = (datetime.now(timezone.utc) - order.created_at).total_seconds() / 60
                    
                    # Get current price (needed for both Tier 1.5 and Tier 2)
                    current_price = 0.0
                    try:
                        df = await self.data_fetcher.get_ohlcv(
                            symbol=symbol, timeframe="M5", count=1
                        )
                        if df is not None and not df.empty:
                            current_price = float(df['close'].iloc[-1])
                    except Exception as e:
                        logger.debug(f"Could not fetch current price: {e}")
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
                            old_reservation = self.reservation_ledger.get_by_id(
                                getattr(order, "reservation_id", None)
                            )
                            cancel_ok = await self.pending_order_manager.cancel_order(
                                order.ticket,
                                reason=f"upgrade_to_market (price ran {move_pct:.1f}% away)",
                                reclaim_budget=False,
                            )
                            
                            if cancel_ok:
                                cancelled_count += 1
                                
                                _risk_pct = (
                                    getattr(order, 'risk_percent', None)
                                    or self.risk_manager.risk_per_trade
                                )
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
                                    from .config import get_symbol_spec as _gss_upgrade
                                    _upgrade_spec = _gss_upgrade(symbol)
                                    _upgrade_acct = await self.mt5_client.get_account_info()
                                    _upgrade_equity = (
                                        _upgrade_acct.equity if _upgrade_acct else 0.0
                                    )
                                    market_result = await self._place_market_with_final_risk(
                                        symbol=symbol,
                                        direction=order_direction,
                                        lots=order.volume,
                                        stop_loss=_new_sl,
                                        take_profit=_new_tp,
                                        account_equity=_upgrade_equity,
                                        symbol_spec=_upgrade_spec,
                                        risk_fraction=_risk_pct,
                                        comment="ICT_Bot_Upgrade",
                                    )
                                    
                                    if market_result and market_result.success:
                                        fill_ticket = market_result.ticket or market_result.order_id
                                        if old_reservation:
                                            self.reservation_ledger.transfer_to_position(
                                                old_reservation,
                                                fill_ticket,
                                            )
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
                                                open_time=datetime.now(timezone.utc),
                                                reservation_id=(
                                                    old_reservation.reservation_id
                                                    if old_reservation
                                                    else None
                                                ),
                                            )
                                            upgraded_pos.trade_type = 'intraday'
                                            upgraded_pos.order_ticket = order.ticket
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
                                        if old_reservation:
                                            self.reservation_ledger.release(old_reservation)
                                        print(
                                            f"[PENDING-REEVAL] MARKET ENTRY FAILED for {symbol}: {getattr(market_result, 'error', 'unknown')}",
                                            flush=True
                                        )
                                except Exception as mkt_err:
                                    if old_reservation:
                                        self.reservation_ledger.release(old_reservation)
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
                    
                    # Decision rules + OUTPUT CONTRACT live in the prompt-cached
                    # PENDING_REEVAL_RULES system block; only order facts go here.
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

Apply the evaluation rules from the system message and reply per the OUTPUT CONTRACT
(first word exactly KEEP or CANCEL).
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
                    except Exception as e:
                        logger.debug(f"Could not generate pending order chart: {e}")
                        pass
                    
                    pending_chart_content.append({"type": "text", "text": prompt})
                    
                    try:
                        # Opus 5: no temperature; adaptive thinking + light effort.
                        response = await asyncio.wait_for(
                            self.claude_client.async_client.messages.create(
                                model=self.claude_client.model_light,
                                max_tokens=2500,  # thinking + short KEEP/CANCEL reply
                                thinking={"type": "adaptive"},
                                output_config={"effort": self.claude_client.effort_light},
                                system=[{
                                    "type": "text",
                                    "text": PENDING_REEVAL_RULES,
                                    "cache_control": {"type": "ephemeral"},
                                }],
                                messages=[{"role": "user", "content": pending_chart_content}]
                            ),
                            timeout=45  # Opus 5 + thinking needs more headroom than Sonnet
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
                    
                    self.claude_client._record_usage("pending_reeval", response)
                    
                    # Skip any leading thinking block; take the text content.
                    raw_recommendation = self.claude_client._extract_text(response).strip()
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
                            print(
                                f"[TRADES] {symbol}: Reservation released if owned, "
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
                        position.close_reason = "emergency_close"
                        try:
                            await self._handle_position_close(position)
                        except Exception as cb_err:
                            logger.error(f"Close lifecycle error for {position.ticket}: {cb_err}")
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
                    if db_match.reservation_id and hasattr(self, "reservation_ledger"):
                        _risk_pct = getattr(db_match, "risk_percent", None)
                        if _risk_pct is None and self.risk_manager:
                            _risk_pct = self.risk_manager.risk_per_trade
                        self.reservation_ledger.restore_position(
                            reservation_id=db_match.reservation_id,
                            symbol=db_match.symbol,
                            ticket=db_match.ticket,
                            risk_percent=_risk_pct or 0.0,
                            order_ticket=getattr(db_match, "order_ticket", None),
                        )
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
                        open_time=datetime.now(timezone.utc)
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
                    today_deals = await self.mt5_client.get_history(today_start, datetime.now(timezone.utc))
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
            
            # Recompute daily_pnl from today's closed trades in DB
            try:
                if DB_AVAILABLE:
                    from sqlalchemy import select, func
                    async with async_session() as sess:
                        today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time())
                        result = await sess.execute(
                            select(func.coalesce(func.sum(TradeModel.profit_loss), 0.0))
                            .where(TradeModel.exit_time >= today_start)
                            .where(TradeModel.exit_price.isnot(None))
                            .where(TradeModel.exit_price != 0)
                        )
                        db_daily_pnl = float(result.scalar() or 0.0)
                        if db_daily_pnl != 0:
                            self.daily_pnl = db_daily_pnl
                            logger.info(f"Recomputed daily_pnl from DB: ${db_daily_pnl:.2f}")
            except Exception as e:
                logger.debug(f"Could not recompute daily_pnl from DB: {e}")
            
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
            self._last_history_sync = datetime.now(timezone.utc)
            return
        
        try:
            # Find trades in our DB that need close data:
            # 1. Truly open trades (no exit_price)
            # 2. Trades wrongly marked as cancelled (exit_price == entry_price, profit_loss == 0)
            #    These happen when manual close removes position before sync detects the close.
            from sqlalchemy import or_, and_, func
            open_trade_tickets = {}  # trade_id -> TradeModel data
            async with async_session() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(TradeModel).where(
                        or_(
                            TradeModel.exit_price.is_(None),
                            TradeModel.exit_price == 0,
                            and_(
                                func.abs(TradeModel.profit_loss) < 0.00001,
                                func.abs(TradeModel.exit_price - TradeModel.entry_price) < 0.00001,
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
                self._last_history_sync = datetime.now(timezone.utc)
                return
            
            print(f"[SYNC] Checking {len(open_trade_tickets)} open DB trades against MT5...", flush=True)
            
            end_time = datetime.now(timezone.utc)
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
                    close_time = close_deal.get('time', datetime.now(timezone.utc))
                    
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
                                    (abs(trade.profit_loss or 0) < 1e-5 and
                                     abs((trade.exit_price or 0) - (trade.entry_price or 0)) < 1e-5) or
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
            filled_closed_count = 0

            if unmatched_tickets:
                # Get current open positions and pending orders from MT5
                current_positions = set()
                current_orders = set()
                
                try:
                    positions = await self.mt5_client.get_positions()
                    if positions:
                        for p in positions:
                            current_positions.add(str(p.ticket))
                except Exception as e:
                    logger.debug(f"Failed to fetch MT5 positions during sync: {e}")
                
                try:
                    import MetaTrader5 as mt5
                    orders = await asyncio.to_thread(mt5.orders_get)
                    if orders:
                        for o in orders:
                            current_orders.add(str(o.ticket))
                except Exception as e:
                    logger.debug(f"Failed to fetch MT5 orders during sync: {e}")
                
                cancelled_count = 0
                filled_closed_count = 0
                for trade_id in unmatched_tickets:
                    # If this trade_id is neither an open position nor a pending order on MT5,
                    # check deal history FIRST before assuming it was cancelled
                    if trade_id not in current_positions and trade_id not in current_orders:
                        try:
                            # Check deal history to see if the order actually filled then closed
                            symbol = open_trade_tickets[trade_id]['symbol']
                            deal_check_start = datetime.now(timezone.utc) - timedelta(days=days_back)
                            deals_for_symbol = await self.mt5_client.get_history(
                                deal_check_start, datetime.now(timezone.utc), symbol=symbol
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
                                        (abs(trade.profit_loss or 0) < 1e-5 and
                                         abs((trade.exit_price or 0) - (trade.entry_price or 0)) < 1e-5) or
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
                                        close_time = closing_deal.get('time', datetime.now(timezone.utc))
                                        
                                        trade.entry_price = fill_price
                                        trade.exit_price = close_price
                                        trade.profit_loss = total_pnl
                                        trade.pnl_source = "mt5"
                                        trade.exit_time = close_time if isinstance(close_time, datetime) else datetime.now(timezone.utc)
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
            
            self._last_history_sync = datetime.now(timezone.utc)
            
            total_updates = updated_count + cancelled_count + filled_closed_count
            if total_updates > 0:
                logger.info(f"Trade sync: {updated_count} closed, {filled_closed_count} filled-then-closed, {cancelled_count} cancelled")
                print(f"[SYNC] Done: {updated_count} trades updated with close data, {filled_closed_count} filled-then-closed, {cancelled_count} cancelled/orphaned", flush=True)
                
                from .api.routes.activity import add_activity
                add_activity(
                    "info",
                    f"Trade sync: {updated_count} closed, {filled_closed_count} filled-then-closed, {cancelled_count} cancelled",
                    None,
                    {"updated_count": updated_count, "filled_closed": filled_closed_count, "cancelled": cancelled_count, "days_back": days_back}
                )
            else:
                logger.debug("No open bot trades needed MT5 close updates")
            
        except Exception as e:
            logger.error(f"Error syncing trade history: {e}")
            import traceback
            traceback.print_exc()
    
    @staticmethod
    def _safe_duration(close_time, open_time) -> str:
        """Safely compute duration string between two datetimes (handles naive/aware mismatch)."""
        if not open_time:
            return 'Unknown'
        try:
            return str(close_time - open_time)
        except TypeError:
            ct = close_time.replace(tzinfo=None) if close_time.tzinfo else close_time
            ot = open_time.replace(tzinfo=None) if open_time.tzinfo else open_time
            return str(ct - ot)

    def _should_sync_history(self) -> bool:
        """Check if it's time to sync trade history."""
        if self._last_history_sync is None:
            return True
        return datetime.now(timezone.utc) - self._last_history_sync >= self._history_sync_interval


    def _should_use_aggressive_data_collection(self) -> bool:
        """AGGRESSIVE scaling only for simulation or explicit demo-data flag."""
        if not self.mt5_client:
            return False
        return bool(
            self.mt5_client.is_simulation
            or settings.trading.demo_data_collection_mode
        )

    async def _reconcile_fill_after_ambiguous_order(
        self,
        *,
        symbol: str,
        direction: str,
        lots: float,
        reservation=None,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> Optional[int]:
        """
        After a timed-out or ambiguous broker response, check MT5 for a filled position.
        If found, track it and transfer the reservation instead of releasing the slot.
        """
        if not self.mt5_client or not self.position_manager:
            return None

        try:
            mt5_positions = await self.mt5_client.get_positions(symbol=symbol)
        except Exception as exc:
            logger.warning(f"[RECONCILE] {symbol}: could not query MT5 positions — {exc}")
            return None

        expected_type = "buy" if direction == "long" else "sell"
        volume_tolerance = max(0.001, lots * 0.05)

        for mt5_pos in mt5_positions or []:
            comment = getattr(mt5_pos, "comment", "") or ""
            if "ICT_Bot" not in comment:
                continue
            if mt5_pos.type != expected_type:
                continue
            if abs(mt5_pos.volume - lots) > volume_tolerance:
                continue
            if mt5_pos.ticket in self.position_manager.positions:
                continue

            position = Position(
                ticket=mt5_pos.ticket,
                symbol=mt5_pos.symbol,
                direction=direction,
                volume=mt5_pos.volume,
                entry_price=mt5_pos.price_open,
                stop_loss=mt5_pos.sl or stop_loss or mt5_pos.price_open,
                take_profit=mt5_pos.tp or take_profit or 0.0,
                open_time=getattr(mt5_pos, "time", None) or datetime.now(timezone.utc),
                reservation_id=getattr(reservation, "reservation_id", None),
            )
            self.position_manager.add_position(position)
            if reservation and hasattr(self, "reservation_ledger") and self.reservation_ledger:
                self.reservation_ledger.transfer_to_position(reservation, mt5_pos.ticket)
            logger.warning(
                f"[RECONCILE] {symbol}: recovered ambiguous fill as position {mt5_pos.ticket} "
                f"({direction} {mt5_pos.volume} lots)"
            )
            return mt5_pos.ticket

        return None

    def _release_trade_reservation(self, reservation) -> None:
        """Release only while still RESERVED — never undo a transferred fill/pending."""
        if not reservation or not hasattr(self, 'reservation_ledger') or not self.reservation_ledger:
            return
        from .services.trade_reservations import ReservationState
        if getattr(reservation, "state", None) != ReservationState.RESERVED:
            return
        if self.reservation_ledger.release(reservation):
            logger.info(
                f"Trade slot released ({self.daily_trades}/"
                f"{settings.trading.max_daily_trades})"
            )

    async def _process_pending_closed_trade_events(self, events):
        """Route fast pending fill→close events through the unified close lifecycle."""
        from .services.pending_order_manager import ClosedTradeEvent
        from .execution.position_manager import Position

        for event in events:
            if not isinstance(event, ClosedTradeEvent):
                continue

            closing_deal = event.closing_deal_ticket
            if closing_deal and closing_deal in self._processed_pending_close_deals:
                logger.info(
                    f"Skipping duplicate pending close event for deal {closing_deal}"
                )
                continue
            if closing_deal:
                self._processed_pending_close_deals.add(closing_deal)

            position = Position(
                ticket=event.position_ticket or event.order_ticket,
                symbol=event.symbol,
                direction=event.direction,
                volume=event.volume,
                entry_price=event.entry_price,
                stop_loss=0.0,
                take_profit=0.0,
                open_time=event.close_time,
                current_price=event.exit_price,
                order_ticket=event.order_ticket,
                reservation_id=event.reservation_id,
                close_reason=event.close_reason,
                closed_profit_loss=event.profit_loss,
                closed_exit_price=event.exit_price,
            )

            await self._handle_position_close(position)

    async def _refresh_exit_overrides(self):
        """
        Tune per-symbol TP1/TP2 exit triggers from measured winner MFE.

        Fail-open: symbols without >= 10 winners keep the default ladder.
        """
        if not self.position_manager:
            return
        from .analysis.excursion_analysis import ExcursionAnalyzer
        from .services.edge_policies import exit_trigger_overrides_from_excursion

        analyzer = ExcursionAnalyzer()
        tuned = 0
        for symbol in settings.trading.symbols:
            try:
                result = await analyzer.compute(symbol, direction="all", lookback_days=90)
                if not result:
                    continue
                overrides = exit_trigger_overrides_from_excursion(
                    result.median_winner_mfe_r, result.winner_sample
                )
                if overrides:
                    self.position_manager.set_exit_overrides(
                        symbol, overrides["tp1_r"], overrides["tp2_r"]
                    )
                    tuned += 1
            except Exception as exc:
                logger.debug(f"Exit tuning skipped for {symbol}: {exc}")
        if tuned:
            logger.info(f"[EXIT-TUNE] Applied MFE-tuned exit triggers for {tuned} symbol(s)")

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
            close_time = datetime.now(timezone.utc)
            
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

            if getattr(position, 'closed_profit_loss', None) is not None:
                profit_loss = position.closed_profit_loss
                actual_close_price = (
                    getattr(position, 'closed_exit_price', None) or position.current_price
                )
                mt5_profit_found = True
                print(
                    f"[CLOSE] {position.symbol}: Fast pending close P/L = ${profit_loss:.2f} "
                    f"(exit={actual_close_price:.5f})",
                    flush=True,
                )
            elif hasattr(self, 'mt5_client') and self.mt5_client and not self.mt5_client.is_simulation:
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
                                except Exception as e:
                                    logger.debug(f"Could not parse deal timestamp: {e}")
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
            
            pip_size = _spec.pip_size
            
            if pip_size <= 0:
                raw_pips = 0.0
            else:
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
                                    datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
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
                            # Persist measured MFE/MAE for expectancy analytics
                            peak_r = float(getattr(position, "peak_r_multiple", 0.0) or 0.0)
                            trough_r = float(getattr(position, "trough_r_multiple", 0.0) or 0.0)
                            trade_record.peak_r_multiple = peak_r
                            trade_record.trough_r_multiple = trough_r
                            trade_record.mfe_r = peak_r
                            trade_record.mae_r = abs(min(0.0, trough_r))
                            _regime = getattr(self, "_last_regime_by_symbol", {}).get(
                                position.symbol
                            )
                            if _regime:
                                trade_record.regime = str(_regime)
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
                self._direction_loss_tracker.record(
                    position.symbol, position.direction, "win",
                    datetime.now(timezone.utc),
                )
            elif profit_loss < 0:
                self.loss_streak += 1
                self.win_streak = 0
                self._direction_loss_tracker.record(
                    position.symbol, position.direction, "loss",
                    datetime.now(timezone.utc),
                )
                _cb_streak = self._direction_loss_tracker.consecutive_losses(
                    position.symbol, position.direction, datetime.now(timezone.utc)
                )
                from .services.direction_circuit_breaker import (
                    DirectionCircuitBreakerSettings as _CBSettings,
                )
                _cb_max = _CBSettings().max_consecutive_losses
                if _cb_max > 0 and _cb_streak >= _cb_max:
                    logger.warning(
                        f"[CIRCUIT-BREAKER] {position.symbol}: {_cb_streak} consecutive "
                        f"{position.direction.upper()} losses today — direction blocked until next UTC day"
                    )
                    print(
                        f"[CIRCUIT-BREAKER] {position.symbol}: {position.direction.upper()} "
                        f"blocked for the rest of the day ({_cb_streak} consecutive losses)",
                        flush=True,
                    )
                _is_crypto_sym = any(c in position.symbol.upper() for c in ['BTC', 'ETH', 'XRP', 'SOL', 'ADA', 'DOGE'])
                _cooldown_min = 15 if _is_crypto_sym else 30
                cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=_cooldown_min)
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
                        or (datetime.now(timezone.utc) - self._last_learnings_update).total_seconds() >= 3600
                    )
                    if should_update:
                        await self.learning_service.update_learnings_documentation()
                        self._last_learnings_update = datetime.now(timezone.utc)
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
            reservation = None
            if getattr(position, 'reservation_id', None):
                reservation = self.reservation_ledger.get_by_id(position.reservation_id)
            if not reservation:
                reservation = self.reservation_ledger.get_for_ticket(position.ticket)
            if not reservation and getattr(position, 'order_ticket', None):
                reservation = self.reservation_ledger.get_for_ticket(position.order_ticket)

            if reservation:
                self.reservation_ledger.mark_closed(reservation)
                print(
                    f"[RISK] {position.symbol}: Daily risk reclaimed via reservation "
                    f"(position closed), total: {self.risk_manager.daily_risk_used*100:.1f}%/"
                    f"{self.risk_manager.max_daily_risk*100:.0f}%",
                    flush=True,
                )
            else:
                logger.info(
                    f"[RISK] {position.symbol}: No reservation ownership; "
                    "daily risk unchanged"
                )
            
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
                        'duration': self._safe_duration(close_time, position.open_time),
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
    
    async def _reversal_position_size(
        self, symbol: str, entry_price: float, stop_loss: float
    ) -> float:
        """
        Risk-based position sizing for reversal re-entries.

        Uses RiskManager fixed-percentage sizing plus the scaling manager's
        mode risk multiplier. Falls back to the minimum lot on any error.
        """
        position_size = 0.01  # Default minimum
        if not self.risk_manager:
            return position_size
        try:
            account_info = await self.mt5_client.get_account_info()
            equity = account_info.equity if account_info else 1000.0

            sizing = self.risk_manager.calculate_position_size(
                account_balance=equity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                symbol=symbol,
            )
            if sizing and sizing.lots > 0:
                position_size = sizing.lots

            # Apply scaling manager risk multiplier for reversals too
            if self.scaling_manager:
                _mode_cfg = self.scaling_manager.get_mode_config()
                _rmult = getattr(_mode_cfg, 'risk_multiplier', 1.0)
                if _rmult != 1.0:
                    from .config import normalize_lots as _nl
                    position_size = _nl(symbol, position_size * _rmult)
        except Exception as e:
            logger.warning(f"[REVERSAL] Position sizing error: {e}")
            position_size = 0.01
        return position_size

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
        _reversal_reservation = None
        
        try:
            if await self._check_drawdown_circuit_breaker():
                logger.info(
                    f"[REVERSAL] {symbol}: Skipping — drawdown kill-switch active"
                )
                return

            # ---- Safeguard 1: Per-symbol reversal cooldown (1 hour) ----
            if not hasattr(self, '_reversal_cooldowns'):
                self._reversal_cooldowns = {}
            
            last_reversal = self._reversal_cooldowns.get(symbol)
            if last_reversal:
                from .utils.datetime_utils import as_utc
                minutes_since = (
                    datetime.now(timezone.utc) - as_utc(last_reversal)
                ).total_seconds() / 60
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
            self._reversal_cooldowns[symbol] = datetime.now(timezone.utc)
            
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
            except Exception as e:
                logger.debug(f"Could not get account equity: {e}")
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
                except Exception as e:
                    logger.debug(f"Could not get learning context for reversal judge: {e}")

            judge_outcome = await self._run_reversal_trade_judge(
                symbol=symbol,
                trade_signal=trade_signal,
                current_price=current_price,
                risk_metrics=risk_metrics,
                learning_context=learning_context,
            )

            if judge_outcome.blocks_execution():
                verdict_label = judge_outcome.verdict.value
                reason = judge_outcome.reason or (
                    'Judge rejected' if verdict_label == 'REJECT' else 'Judge unavailable'
                )
                outcome_type = (
                    "judge_reject" if verdict_label == "REJECT" else "judge_failure"
                )
                await self._record_terminal_decision(
                    outcome_type,
                    symbol,
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=reason,
                    judge_verdict=verdict_label,
                    details={"risk_flags": judge_outcome.risk_flags},
                )
                logger.info(
                    f"[REVERSAL] {symbol}: Judge {verdict_label} — {reason}"
                )
                print(
                    f"[REVERSAL] {symbol}: Judge {verdict_label} reversal — {reason}",
                    flush=True
                )
                return

            _reversal_size_multiplier = 1.0
            if judge_outcome.allows_demote_execution():
                from .config import normalize_lots as _nl_demote

                demote = apply_demote_policy(
                    trade_signal.direction,
                    current_price,
                    trade_signal.entry_price or current_price,
                    trade_signal.stop_loss or 0.0,
                    trade_signal.take_profit or 0.0,
                    getattr(trade_signal, 'order_type', 'market') or 'market',
                    judge_outcome.suggested_entry,
                )
                demoted_entry = demote["demoted_entry"]
                _demote_sl = demote["stop_loss"]
                _demote_tp = demote["take_profit"]
                _demote_sl_dist = abs(demoted_entry - _demote_sl) if _demote_sl else 0
                _demote_tp_dist = abs(_demote_tp - demoted_entry) if _demote_tp else 0
                _demote_rr = _demote_tp_dist / _demote_sl_dist if _demote_sl_dist > 0 else 0

                if _demote_rr < 1.0 and _demote_sl_dist > 0:
                    await self._record_terminal_decision(
                        "mechanical_reject",
                        symbol,
                        gate_id="demote_rr_below_1",
                        direction=trade_signal.direction,
                        entry=demoted_entry,
                        sl=_demote_sl,
                        tp=_demote_tp,
                        confidence=trade_signal.confidence,
                        reason=f"Reversal DEMOTE R:R {_demote_rr:.2f}:1 below 1.0",
                        judge_verdict="DEMOTE",
                    )
                    return

                trade_signal.order_type = demote["order_type"]
                trade_signal.entry_price = demoted_entry
                trade_signal.stop_loss = _demote_sl
                trade_signal.take_profit = _demote_tp
                _reversal_size_multiplier = demote.get("size_multiplier", 0.75)

                demote_reason = judge_outcome.reason or 'Judge demoted reversal'
                await self._record_terminal_decision(
                    "judge_demote",
                    symbol,
                    direction=trade_signal.direction,
                    entry=demoted_entry,
                    sl=_demote_sl,
                    tp=_demote_tp,
                    confidence=trade_signal.confidence,
                    reason=demote_reason,
                    judge_verdict="DEMOTE",
                    details={
                        "risk_flags": judge_outcome.risk_flags,
                        "order_type": demote["order_type"],
                        "reversal": True,
                    },
                )

            logger.info(
                f"[REVERSAL] {symbol}: Judge verdict: {judge_outcome.verdict.value} — {judge_outcome.reason}"
            )
            
            # ---- Position Sizing ----
            entry_price = trade_signal.entry_price or current_price
            stop_loss = trade_signal.stop_loss
            
            if not stop_loss:
                logger.warning(f"[REVERSAL] {symbol}: No stop loss provided, aborting")
                return
            
            position_size = await self._reversal_position_size(
                symbol, entry_price, stop_loss
            )

            if _reversal_size_multiplier != 1.0:
                from .config import normalize_lots as _nl_rev_demote
                position_size = _nl_rev_demote(
                    symbol, position_size * _reversal_size_multiplier
                )
            
            # ---- Place the order ----
            _reversal_risk_pct = self.risk_manager.risk_per_trade if self.risk_manager else 0.02
            _reversal_reservation = self.reservation_ledger.reserve(
                symbol=symbol,
                signal_id=f"reversal:{closed_position.ticket}",
                risk_percent=_reversal_risk_pct,
            )
            self.reservation_ledger.commit_risk(_reversal_reservation)
            if hasattr(self, 'risk_manager') and self.risk_manager:
                print(
                    f"[RISK] {symbol}: Reversal daily risk +{_reversal_risk_pct*100:.1f}%, "
                    f"total: {self.risk_manager.daily_risk_used*100:.1f}%/"
                    f"{self.risk_manager.max_daily_risk*100:.0f}%",
                    flush=True
                )
            
            order_type = getattr(trade_signal, 'order_type', 'market') or 'market'
            from .config import get_symbol_spec as _gss_reversal
            _reversal_spec = _gss_reversal(symbol)
            try:
                _rev_acct = await self.mt5_client.get_account_info()
                _reversal_equity = _rev_acct.equity if _rev_acct else 1000.0
            except Exception:
                _reversal_equity = 1000.0
            
            if order_type == 'market' or order_type.endswith('_market'):
                result = await self._place_market_with_final_risk(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    lots=position_size,
                    stop_loss=stop_loss,
                    take_profit=trade_signal.take_profit,
                    account_equity=_reversal_equity,
                    symbol_spec=_reversal_spec,
                    risk_fraction=_reversal_risk_pct,
                    comment="ICT_Bot_Reversal",
                )
                
                if result and result.success:
                    fill_ticket = result.ticket or result.order_id
                    self.reservation_ledger.transfer_to_position(
                        _reversal_reservation,
                        fill_ticket,
                    )
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
                    if self.position_manager and fill_ticket:
                        from .execution.position_manager import Position as Pos
                        new_pos = Pos(
                            ticket=fill_ticket,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            volume=position_size,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=trade_signal.take_profit or 0,
                            open_time=datetime.now(timezone.utc),
                            trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                            reservation_id=_reversal_reservation.reservation_id,
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
                                    entry_time=datetime.now(timezone.utc),
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
                    except Exception as e:
                        logger.debug(f"Could not send reversal trade notification: {e}")
                        pass
                else:
                    logger.warning(
                        f"[REVERSAL] {symbol}: Order placement FAILED — "
                        f"{getattr(result, 'message', 'unknown error') if result else 'final risk blocked'}"
                    )
                    self.reservation_ledger.release(_reversal_reservation)
            else:
                # Pending order for reversal (limit entry)
                suggested_entry = trade_signal.entry_price or current_price
                result = await self._place_pending_with_final_risk(
                    symbol=symbol,
                    direction=trade_signal.direction,
                    order_type=order_type,
                    price=suggested_entry,
                    lots=position_size,
                    stop_loss=stop_loss,
                    take_profit=trade_signal.take_profit,
                    account_equity=_reversal_equity,
                    symbol_spec=_reversal_spec,
                    risk_fraction=_reversal_risk_pct,
                    comment="ICT_Bot_Reversal",
                )
                
                if result and result.success:
                    pending_ticket = result.ticket or result.order_id
                    self.reservation_ledger.transfer_to_pending(
                        _reversal_reservation,
                        pending_ticket,
                    )
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
                        await self.pending_order_manager.add_order(
                            ticket=pending_ticket,
                            symbol=symbol,
                            direction=trade_signal.direction,
                            order_type=order_type,
                            volume=position_size,
                            price=suggested_entry,
                            stop_loss=stop_loss,
                            take_profit=trade_signal.take_profit,
                            risk_percent=_reversal_risk_pct,
                            reservation_id=_reversal_reservation.reservation_id,
                        )
                else:
                    logger.warning(
                        f"[REVERSAL] {symbol}: Pending order FAILED — "
                        f"{getattr(result, 'message', 'unknown error')}"
                    )
                    self.reservation_ledger.release(_reversal_reservation)
        
        except Exception as e:
            logger.error(f"[REVERSAL] Error analyzing reversal for {symbol}: {e}")
            import traceback
            traceback.print_exc()
            if (
                _reversal_reservation
                and _reversal_reservation.state == ReservationState.RESERVED
            ):
                self.reservation_ledger.release(_reversal_reservation)
    
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
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
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
            
            # Refresh MFE-tuned exit triggers with latest trade data
            try:
                await self._refresh_exit_overrides()
            except Exception as e:
                logger.debug(f"Exit override refresh skipped: {e}")
            
            # Check for weekly reset (Monday) - weekly consolidation happens on Sunday
            if today.weekday() == 0:  # Monday
                logger.info("New trading week - resetting weekly counters")
                if self.scaling_manager:
                    self.scaling_manager.reset_weekly(current_equity)
            
            # Check rejected signal outcomes every day (did we miss winners?)
            if self.learning_service:
                try:
                    updated = await self.learning_service.check_rejected_signal_outcomes(lookback_hours=24)
                    if updated:
                        logger.info(f"Updated {updated} rejected signal outcomes")
                    decision_updates = await self.learning_service.process_decision_outcomes(
                        mt5_client=self.mt5_client,
                        lookback_hours=48,
                        horizon_hours=8,
                    )
                    if decision_updates:
                        logger.info(f"Decision outcome worker updated {decision_updates} rows")
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
            
            # Prune _synced_deal_ids to prevent unbounded growth
            if len(self._synced_deal_ids) > self._max_synced_deal_ids:
                excess = len(self._synced_deal_ids) - self._max_synced_deal_ids
                to_discard = sorted(self._synced_deal_ids)[:excess]
                self._synced_deal_ids -= set(to_discard)
                logger.info(f"Pruned {excess} old entries from _synced_deal_ids")
    
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
            except Exception as e:
                logger.debug(f"Could not get current session: {e}")
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
        
        if hasattr(self, 'position_manager') and self.position_manager:
            try:
                await self.position_manager.flush_persistence()
                logger.info("Position persistence queue drained")
            except Exception as e:
                logger.warning(f"Could not flush position persistence: {e}")

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
