"""
Scaling Manager for automated risk adjustment.

Manages trading mode (aggressive/normal/conservative/defensive)
based on performance, drawdown, and goal progress.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from enum import Enum

from ..utils.logging import get_logger
from ..api.routes.activity import add_activity

logger = get_logger(__name__)


class TradingMode(Enum):
    """Trading modes with different risk profiles."""
    AGGRESSIVE = "aggressive"    # Increased sizes, more setups
    NORMAL = "normal"            # Standard operation
    CONSERVATIVE = "conservative"  # Reduced sizes, selective
    DEFENSIVE = "defensive"      # Minimum sizes, A+ only


@dataclass
class ModeConfig:
    """Configuration for each trading mode."""
    risk_multiplier: float       # Multiply base risk by this
    setup_filter: str            # 'all', 'A_and_B', 'A_only'
    confidence_threshold: float  # Minimum confidence
    max_daily_trades: int        # Max trades per day
    description: str


MODE_CONFIGS = {
    TradingMode.AGGRESSIVE: ModeConfig(
        risk_multiplier=1.15,
        setup_filter='all',
        confidence_threshold=0.60,
        max_daily_trades=30,
        description="Data collection mode: lower bars to gather trade outcomes for learning"
    ),
    TradingMode.NORMAL: ModeConfig(
        risk_multiplier=1.0,
        setup_filter='A_and_B',
        confidence_threshold=0.60,
        max_daily_trades=25,
        description="Standard risk, accepts B+ setups at 60%+ confidence"
    ),
    TradingMode.CONSERVATIVE: ModeConfig(
        risk_multiplier=0.5,
        setup_filter='A_and_B',
        confidence_threshold=0.60,
        max_daily_trades=15,
        description="Half risk, A+/A/B setups at 60%+ confidence (Mon/Fri caution)"
    ),
    TradingMode.DEFENSIVE: ModeConfig(
        risk_multiplier=0.25,
        setup_filter='A_only',
        confidence_threshold=0.90,
        max_daily_trades=8,
        description="Quarter risk, only A+ setups, 3:1+ R:R required (severe drawdown)"
    )
}


class ScalingManager:
    """
    Manages trading mode and risk adjustments.
    
    Features:
    - Automatic mode selection based on performance
    - Drawdown-based risk reduction
    - Goal progress tracking
    - Claude integration for decisions
    """
    
    def __init__(
        self,
        starting_equity: float = 1000,
        target_equity: float = 10000,
        max_daily_drawdown: float = 0.03,
        max_weekly_drawdown: float = 0.06
    ):
        """
        Initialize scaling manager.
        
        Args:
            starting_equity: Starting account equity
            target_equity: Target equity goal
            max_daily_drawdown: Max daily drawdown before defensive mode
            max_weekly_drawdown: Max weekly drawdown before trading pause
        """
        self.starting_equity = starting_equity
        self.target_equity = target_equity
        self.max_daily_drawdown = max_daily_drawdown
        self.max_weekly_drawdown = max_weekly_drawdown
        
        # Current state
        self.current_mode = TradingMode.NORMAL
        self.daily_high_equity = starting_equity
        self.weekly_high_equity = starting_equity
        self.last_mode_change = datetime.now()
        
        # Performance tracking
        self.recent_trades: List[Dict[str, Any]] = []
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        
        # Per-symbol/session performance tracking (T2-4)
        self.symbol_session_stats: Dict[str, Dict[str, Any]] = {}
        # Format: {"EURUSD_london": {"wins": 5, "losses": 2, "total_r": 8.5, "trades": 7}}
        
        # Equity curve tracking for max drawdown (T3-4)
        self._equity_snapshots: List[Dict[str, Any]] = []
        self._peak_equity: float = starting_equity
        
        # Edge health integration (updated externally via set_edge_health)
        self._edge_health_score: float = 100.0
        self._blocked_symbols: set = set()
        
        logger.info(f"Scaling manager initialized: ${starting_equity:,.0f} -> ${target_equity:,.0f}")
    
    def update_equity(self, current_equity: float):
        """Update equity tracking for drawdown calculations."""
        # Update daily high
        if current_equity > self.daily_high_equity:
            self.daily_high_equity = current_equity
        
        # Update weekly high
        if current_equity > self.weekly_high_equity:
            self.weekly_high_equity = current_equity
        
        # Record equity snapshot for max drawdown tracking (T3-4)
        self.record_equity_snapshot(current_equity)
    
    def record_equity_snapshot(self, equity: float, timestamp: Optional[datetime] = None):
        """
        Record an equity snapshot for max drawdown calculation.
        Called periodically (e.g., each trading cycle).
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        self._equity_snapshots.append({
            'equity': equity,
            'timestamp': timestamp
        })
        
        # Track peak for drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity
        
        # Keep last 1000 snapshots (approx 1 week at 15-second intervals during market hours)
        if len(self._equity_snapshots) > 1000:
            self._equity_snapshots = self._equity_snapshots[-1000:]
    
    def calculate_max_drawdown(self) -> float:
        """
        Calculate maximum drawdown from equity snapshots.
        
        Max drawdown is the largest peak-to-trough decline in equity,
        expressed as a decimal fraction (e.g., 0.05 = 5%).
        """
        if len(self._equity_snapshots) < 2:
            return 0.0
        
        peak = self._equity_snapshots[0]['equity']
        max_dd = 0.0
        
        for snapshot in self._equity_snapshots:
            equity = snapshot['equity']
            
            # Update peak
            if equity > peak:
                peak = equity
            
            # Calculate current drawdown from peak
            if peak > 0:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
        
        return max_dd
    
    def record_trade(self, trade_result: Dict[str, Any]):
        """
        Record a completed trade for performance tracking.
        
        Args:
            trade_result: Dict with profit_loss, r_multiple, symbol, direction
        """
        trade_result['timestamp'] = datetime.now()
        self.recent_trades.append(trade_result)
        
        # Keep only last 50 trades
        if len(self.recent_trades) > 50:
            self.recent_trades = self.recent_trades[-50:]
        
        # Update daily/weekly P&L
        pnl = trade_result.get('profit_loss', 0)
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
    
    def get_recent_performance(self, num_trades: int = 20) -> Dict[str, Any]:
        """Get recent performance statistics."""
        recent = self.recent_trades[-num_trades:] if self.recent_trades else []
        
        if not recent:
            return {
                'win_rate': 50.0,
                'avg_r': 0.0,
                'max_drawdown': 0.0,
                'current_streak': 'None',
                'trades_count': 0
            }
        
        wins = len([t for t in recent if t.get('profit_loss', 0) > 0])
        total_r = sum(t.get('r_multiple', 0) for t in recent)
        
        # Calculate streak
        streak = 0
        streak_type = None
        for trade in reversed(recent):
            pnl = trade.get('profit_loss', 0)
            if streak_type is None:
                streak_type = 'win' if pnl > 0 else 'loss'
            
            if (pnl > 0 and streak_type == 'win') or (pnl < 0 and streak_type == 'loss'):
                streak += 1
            else:
                break
        
        return {
            'win_rate': (wins / len(recent)) * 100,
            'avg_r': total_r / len(recent),
            'max_drawdown': self.calculate_max_drawdown(),
            'current_streak': f"{streak} {streak_type}s" if streak > 0 else "None",
            'trades_count': len(recent)
        }
    
    def calculate_daily_drawdown(self, current_equity: float) -> float:
        """Calculate current daily drawdown."""
        if self.daily_high_equity <= 0:
            return 0.0
        return (self.daily_high_equity - current_equity) / self.daily_high_equity
    
    def calculate_weekly_drawdown(self, current_equity: float) -> float:
        """Calculate current weekly drawdown."""
        if self.weekly_high_equity <= 0:
            return 0.0
        return (self.weekly_high_equity - current_equity) / self.weekly_high_equity
    
    def calculate_goal_progress(self, current_equity: float) -> float:
        """Calculate progress toward goal (0-100)."""
        import math
        
        if current_equity >= self.target_equity:
            return 100.0
        if current_equity <= self.starting_equity:
            return 0.0
        
        # Logarithmic progress
        log_start = math.log(self.starting_equity)
        log_target = math.log(self.target_equity)
        log_current = math.log(current_equity)
        
        return ((log_current - log_start) / (log_target - log_start)) * 100
    
    def determine_mode(
        self,
        current_equity: float,
        claude_recommendation: Optional[str] = None
    ) -> TradingMode:
        """
        Determine the appropriate trading mode.
        
        Args:
            current_equity: Current account equity
            claude_recommendation: Optional mode from Claude
            
        Returns:
            Appropriate TradingMode
        """
        # Update equity tracking
        self.update_equity(current_equity)
        
        # Calculate metrics
        daily_dd = self.calculate_daily_drawdown(current_equity)
        weekly_dd = self.calculate_weekly_drawdown(current_equity)
        goal_progress = self.calculate_goal_progress(current_equity)
        performance = self.get_recent_performance()
        
        # AGGRESSIVE LOCK: If manually set to AGGRESSIVE (data collection mode),
        # only allow drawdown rules to override — NOT performance/streak rules
        aggressive_locked = (self.current_mode == TradingMode.AGGRESSIVE)
        
        # Rule 1: Weekly drawdown - pause trading (always respected, even in aggressive lock)
        if weekly_dd >= self.max_weekly_drawdown:
            logger.warning(f"Weekly drawdown {weekly_dd:.1%} exceeds limit - DEFENSIVE mode")
            return TradingMode.DEFENSIVE
        
        # Rule 2: Daily drawdown - go defensive (always respected)
        if daily_dd >= self.max_daily_drawdown:
            logger.warning(f"Daily drawdown {daily_dd:.1%} exceeds limit - DEFENSIVE mode")
            return TradingMode.DEFENSIVE
        
        # If aggressive locked, skip all other mode-changing rules
        if aggressive_locked:
            return TradingMode.AGGRESSIVE
        
        # Rule 2b: Edge health auto-protection
        if self._edge_health_score < 30:
            logger.warning(f"[EDGE] Score {self._edge_health_score:.0f} < 30 — DEFENSIVE")
            return TradingMode.DEFENSIVE
        if self._edge_health_score < 40:
            logger.warning(f"[EDGE] Score {self._edge_health_score:.0f} < 40 — CONSERVATIVE")
            return TradingMode.CONSERVATIVE
        
        # Rule 3: Use Claude's recommendation if available
        if claude_recommendation:
            mode_map = {
                'aggressive': TradingMode.AGGRESSIVE,
                'normal': TradingMode.NORMAL,
                'conservative': TradingMode.CONSERVATIVE,
                'defensive': TradingMode.DEFENSIVE
            }
            if claude_recommendation.lower() in mode_map:
                return mode_map[claude_recommendation.lower()]
        
        # Rule 4: Loss streak - go conservative or defensive (check higher streak first!)
        # Only activate if we have meaningful trade history (>= 3 trades)
        trades_count = performance.get('trades_count', 0)
        if trades_count >= 3 and 'loss' in performance.get('current_streak', '').lower():
            streak_count = int(performance['current_streak'].split()[0])
            if streak_count >= 5:
                logger.info(f"Loss streak ({streak_count}) - DEFENSIVE mode")
                return TradingMode.DEFENSIVE
            if streak_count >= 3:
                logger.info(f"Loss streak ({streak_count}) - CONSERVATIVE mode")
                return TradingMode.CONSERVATIVE
        
        # Rule 5: Strong performance - can go aggressive (only if no drawdown concerns)
        win_rate = performance.get('win_rate', 50)
        avg_r = performance.get('avg_r', 0)
        
        if win_rate >= 60 and avg_r >= 1.5 and daily_dd < 0.01 and weekly_dd < 0.02:
            logger.info(f"Strong performance (WR: {win_rate:.0f}%, R: {avg_r:.1f}) - AGGRESSIVE mode")
            return TradingMode.AGGRESSIVE
        
        # Rule 6: Weak performance - go conservative (only if we have enough data)
        trades_count = performance.get('trades_count', 0)
        if trades_count >= 5 and (win_rate < 45 or avg_r < 0.5):
            logger.info(f"Weak performance (WR: {win_rate:.0f}%, R: {avg_r:.1f}, {trades_count} trades) - CONSERVATIVE mode")
            return TradingMode.CONSERVATIVE
        
        # Default: Normal mode
        return TradingMode.NORMAL
    
    def get_mode_config(self, mode: Optional[TradingMode] = None) -> ModeConfig:
        """Get configuration for a mode."""
        mode = mode or self.current_mode
        return MODE_CONFIGS[mode]
    
    def should_take_trade(
        self,
        setup_grade: str,
        confidence: float,
        daily_trades: int
    ) -> tuple[bool, str]:
        """
        Check if a trade should be taken based on current mode.
        
        Args:
            setup_grade: Setup quality (A+, A, B, C)
            confidence: Trade confidence (0-1)
            daily_trades: Number of trades taken today
            
        Returns:
            Tuple of (should_trade, reason)
        """
        config = self.get_mode_config()
        
        # Check daily trade limit
        if daily_trades >= config.max_daily_trades:
            return False, f"Daily limit reached ({config.max_daily_trades} trades)"
        
        # Check confidence threshold
        if confidence < config.confidence_threshold:
            return False, f"Confidence {confidence:.0%} below threshold {config.confidence_threshold:.0%}"
        
        # Check setup grade filter
        grade_passes = False
        if config.setup_filter == 'all':
            grade_passes = True
        elif config.setup_filter == 'A_and_B':
            grade_passes = setup_grade in ['A+', 'A', 'B']
        elif config.setup_filter == 'A_only':
            grade_passes = setup_grade in ['A+', 'A']
        
        if not grade_passes:
            return False, f"Setup grade {setup_grade} filtered out ({config.setup_filter})"
        
        return True, "Trade approved"
    
    def get_status(self, current_equity: float) -> Dict[str, Any]:
        """Get comprehensive scaling status."""
        config = self.get_mode_config()
        performance = self.get_recent_performance()
        
        return {
            'current_mode': self.current_mode.value,
            'mode_description': config.description,
            'risk_multiplier': config.risk_multiplier,
            'confidence_threshold': config.confidence_threshold,
            'setup_filter': config.setup_filter,
            'max_daily_trades': config.max_daily_trades,
            'daily_drawdown': self.calculate_daily_drawdown(current_equity) * 100,
            'weekly_drawdown': self.calculate_weekly_drawdown(current_equity) * 100,
            'goal_progress': self.calculate_goal_progress(current_equity),
            'recent_performance': performance,
            'daily_pnl': self.daily_pnl,
            'weekly_pnl': self.weekly_pnl
        }
    
    def reset_daily(self, current_equity: float):
        """Reset daily tracking (call at start of new day)."""
        self.daily_high_equity = current_equity
        self.daily_pnl = 0.0
        logger.info("Daily tracking reset")
    
    def reset_weekly(self, current_equity: float):
        """Reset weekly tracking (call at start of new week)."""
        self.weekly_high_equity = current_equity
        self.weekly_pnl = 0.0
        logger.info("Weekly tracking reset")
    
    def record_symbol_trade(self, symbol: str, session: str, profit_loss: float, r_multiple: float):
        """
        Record a trade result for per-symbol/session tracking.
        
        Args:
            symbol: Trading symbol (e.g., 'EURUSD')
            session: Trading session (e.g., 'london', 'new_york')
            profit_loss: Dollar P&L
            r_multiple: R-multiple result
        """
        key = f"{symbol}_{session}"
        
        if key not in self.symbol_session_stats:
            self.symbol_session_stats[key] = {
                'wins': 0, 'losses': 0, 'total_r': 0.0, 'trades': 0,
                'symbol': symbol, 'session': session
            }
        
        stats = self.symbol_session_stats[key]
        stats['trades'] += 1
        stats['total_r'] += r_multiple
        
        if profit_loss > 0:
            stats['wins'] += 1
        else:
            stats['losses'] += 1
        
        logger.info(
            f"Symbol stats updated: {key} -> "
            f"{stats['wins']}W/{stats['losses']}L, "
            f"WR: {self.get_symbol_win_rate(symbol, session):.0f}%, "
            f"Avg R: {self.get_symbol_avg_r(symbol, session):.2f}"
        )
    
    def get_symbol_win_rate(self, symbol: str, session: str) -> float:
        """Get win rate for a symbol/session combo."""
        key = f"{symbol}_{session}"
        stats = self.symbol_session_stats.get(key)
        
        if not stats or stats['trades'] == 0:
            return 50.0  # Default 50% for unknown
        
        return (stats['wins'] / stats['trades']) * 100
    
    def get_symbol_avg_r(self, symbol: str, session: str) -> float:
        """Get average R-multiple for a symbol/session combo."""
        key = f"{symbol}_{session}"
        stats = self.symbol_session_stats.get(key)
        
        if not stats or stats['trades'] == 0:
            return 0.0
        
        return stats['total_r'] / stats['trades']
    
    def get_symbol_size_multiplier(self, symbol: str, session: str) -> float:
        """
        Get position size multiplier based on symbol/session performance.
        
        After 10+ trades on a combo:
        - Win rate below 40%: BLOCK (return 0.0)
        - Win rate 40-50%: Reduce size by 50% (return 0.5)
        - Win rate 50-60%: Normal (return 1.0)
        - Win rate above 60%: Allow boost (return 1.2)
        
        Returns:
            Multiplier for position size (0.0 = blocked)
        """
        key = f"{symbol}_{session}"
        stats = self.symbol_session_stats.get(key)
        
        if not stats or stats['trades'] < 10:
            return 1.0  # Not enough data, allow normal size
        
        win_rate = (stats['wins'] / stats['trades']) * 100
        
        if win_rate < 40:
            logger.warning(f"BLOCKING {symbol}/{session}: Win rate {win_rate:.0f}% (< 40%) over {stats['trades']} trades")
            add_activity("win_rate_blocked", f"{symbol}/{session} blocked: win rate {win_rate:.0f}% below 40%", symbol=symbol, details={"win_rate": win_rate, "trades": stats['trades'], "session": session})
            return 0.0
        elif win_rate < 45:
            logger.info(f"REDUCING {symbol}/{session}: Win rate {win_rate:.0f}% (< 45%) -> 40% size")
            return 0.4
        elif win_rate < 50:
            logger.info(f"REDUCING {symbol}/{session}: Win rate {win_rate:.0f}% (< 50%) -> 60% size")
            return 0.6
        elif win_rate >= 60 and stats['trades'] >= 15:
            logger.info(f"BOOSTING {symbol}/{session}: Win rate {win_rate:.0f}% (>= 60%) -> 130% size")
            return 1.3
        elif win_rate >= 55:
            logger.info(f"BOOSTING {symbol}/{session}: Win rate {win_rate:.0f}% (>= 55%) -> 115% size")
            return 1.15
        else:
            return 1.0
    
    def should_trade_symbol(self, symbol: str, session: str) -> tuple:
        """
        Check if a symbol/session combo should be traded.
        
        Returns:
            Tuple of (should_trade, reason, size_multiplier)
        """
        multiplier = self.get_symbol_size_multiplier(symbol, session)
        
        if multiplier == 0.0:
            key = f"{symbol}_{session}"
            stats = self.symbol_session_stats.get(key, {})
            win_rate = (stats.get('wins', 0) / max(stats.get('trades', 1), 1)) * 100
            return False, f"Blocked: {win_rate:.0f}% win rate over {stats.get('trades', 0)} trades", 0.0
        
        return True, "Approved", multiplier
    
    def set_edge_health(self, overall_score: float, symbol_scores: Dict[str, float]):
        """
        Update edge health from the edge tracker API data.
        Called periodically by the main trading loop.
        
        Auto-protection rules:
        - overall < 40 -> force CONSERVATIVE
        - overall < 30 -> force DEFENSIVE
        - per-symbol < 30 -> block that symbol
        - per-symbol < 50 -> reduce risk on that symbol
        """
        prev = self._edge_health_score
        self._edge_health_score = overall_score
        prev_blocked = set(self._blocked_symbols)
        self._blocked_symbols = set()
        
        for sym, score in symbol_scores.items():
            if score < 30:
                self._blocked_symbols.add(sym)
                logger.warning(f"[EDGE] {sym} auto-blocked (score {score:.0f} < 30)")
                if sym not in prev_blocked:
                    add_activity("edge_blocked", f"{sym} auto-blocked: edge score {score:.0f} collapsed below 30", symbol=sym, details={"score": score, "threshold": 30})
        
        if abs(overall_score - prev) > 10:
            logger.info(f"[EDGE] Health updated: {prev:.0f} -> {overall_score:.0f}")
            add_activity("edge_health", f"Edge health score changed: {prev:.0f} → {overall_score:.0f}", details={"previous_score": round(prev, 1), "new_score": round(overall_score, 1), "change": round(overall_score - prev, 1)})
        elif abs(overall_score - prev) > 5:
            logger.info(f"[EDGE] Health updated: {prev:.0f} -> {overall_score:.0f}")
    
    def is_symbol_edge_blocked(self, symbol: str) -> bool:
        """Check if a symbol is blocked due to collapsed edge."""
        return symbol in self._blocked_symbols
    
    def get_edge_risk_multiplier(self) -> float:
        """
        Get risk multiplier based on edge health.
        Score >= 60: 1.0 (full risk)
        Score 40-60: 0.75 (reduced)
        Score < 40: 0.5 (half risk, mode override handles the rest)
        """
        if self._edge_health_score >= 60:
            return 1.0
        elif self._edge_health_score >= 40:
            return 0.75
        return 0.5

    def get_all_symbol_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get all symbol/session performance stats."""
        result = {}
        for key, stats in self.symbol_session_stats.items():
            trades = stats['trades']
            if trades > 0:
                result[key] = {
                    **stats,
                    'win_rate': (stats['wins'] / trades) * 100,
                    'avg_r': stats['total_r'] / trades,
                    'size_multiplier': self.get_symbol_size_multiplier(
                        stats['symbol'], stats['session']
                    )
                }
        return result
