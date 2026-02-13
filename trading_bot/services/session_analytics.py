"""
Session Analytics Service.

Tracks trading performance by session (Asian, London, New York)
to identify optimal trading times.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, Any, List, Optional
from enum import Enum

from ..utils.logging import get_logger

logger = get_logger(__name__)


class TradingSession(Enum):
    """Trading sessions."""
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "new_york"
    LONDON_NY_OVERLAP = "london_ny_overlap"
    OFF_HOURS = "off_hours"


@dataclass
class SessionStats:
    """Statistics for a trading session."""
    session: TradingSession
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_r: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0
    symbols_traded: Dict[str, int] = field(default_factory=dict)
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate (excluding breakeven/scratch trades)."""
        decided = self.wins + self.losses
        if decided == 0:
            return 0.0
        return (self.wins / decided) * 100
    
    @property
    def avg_r(self) -> float:
        """Calculate average R."""
        if self.total_trades == 0:
            return 0.0
        return self.total_r / self.total_trades
    
    @property
    def expectancy(self) -> float:
        """Calculate expectancy (average R per trade)."""
        return self.avg_r
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'session': self.session.value,
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': self.win_rate,
            'total_pnl': self.total_pnl,
            'total_r': self.total_r,
            'avg_r': self.avg_r,
            'best_trade_r': self.best_trade_r,
            'worst_trade_r': self.worst_trade_r,
            'expectancy': self.expectancy,
            'top_symbols': dict(sorted(
                self.symbols_traded.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5])
        }


# Session time definitions (UTC)
SESSION_TIMES = {
    TradingSession.ASIAN: (time(0, 0), time(8, 0)),       # 00:00 - 08:00 UTC
    TradingSession.LONDON: (time(7, 0), time(16, 0)),     # 07:00 - 16:00 UTC
    TradingSession.NEW_YORK: (time(12, 0), time(21, 0)),  # 12:00 - 21:00 UTC
    TradingSession.LONDON_NY_OVERLAP: (time(12, 0), time(16, 0)),  # 12:00 - 16:00 UTC
}


class SessionAnalytics:
    """
    Tracks and analyzes trading performance by session.
    
    Features:
    - Session detection
    - Performance tracking per session
    - Symbol performance per session
    - Best/worst session identification
    """
    
    def __init__(self):
        """Initialize session analytics."""
        self.session_stats: Dict[TradingSession, SessionStats] = {
            session: SessionStats(session=session)
            for session in TradingSession
        }
        
        # Trade history with session info
        self.trade_history: List[Dict[str, Any]] = []
        
        # Load historical data from database
        self._load_from_database()
        
        logger.info("Session analytics initialized")
    
    def _load_from_database(self):
        """Load closed bot trades from database to populate session stats on startup."""
        try:
            import sqlite3
            import os
            
            db_path = os.path.join(os.getcwd(), "trading_bot.db")
            if not os.path.exists(db_path):
                logger.debug("No database found, starting with empty session analytics")
                return
            
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            
            # Only load trades that have exit_price (closed trades) AND were placed by the bot
            # (not synced from MT5 history - those have entry_reason='Synced from MT5 history')
            cur.execute("""
                SELECT symbol, direction, profit_loss, r_multiple, entry_time, stop_loss, entry_price
                FROM trades
                WHERE exit_price IS NOT NULL
                  AND profit_loss IS NOT NULL
                  AND entry_reason != 'Synced from MT5 history'
            """)
            
            rows = cur.fetchall()
            conn.close()
            
            loaded = 0
            for row in rows:
                symbol, direction, profit_loss, r_multiple, entry_time_str, stop_loss, entry_price = row
                
                # Skip trades with zero P/L (not real closes)
                if profit_loss is None or profit_loss == 0:
                    continue
                
                # Parse entry time
                entry_time = None
                if entry_time_str:
                    try:
                        entry_time = datetime.fromisoformat(str(entry_time_str))
                    except (ValueError, TypeError):
                        entry_time = datetime.utcnow()
                else:
                    entry_time = datetime.utcnow()
                
                # Use stored r_multiple, but sanity-check it
                r_mult = float(r_multiple) if r_multiple else 0.0
                if abs(r_mult) > 10:
                    # R-multiple is unreasonable (bad SL data), zero it out
                    r_mult = 0.0
                
                self.record_trade(
                    symbol=symbol or "UNKNOWN",
                    direction=direction or "long",
                    profit_loss=float(profit_loss),
                    r_multiple=r_mult,
                    entry_time=entry_time
                )
                loaded += 1
            
            total_pnl = sum(s.total_pnl for s in self.session_stats.values())
            print(f"[SESSION] Loaded {loaded} bot trades from DB (total P/L: ${total_pnl:.2f})", flush=True)
            logger.info(f"Session analytics loaded {loaded} historical bot trades from database (P/L: ${total_pnl:.2f})")
        except Exception as e:
            print(f"[SESSION] ERROR loading from DB: {e}", flush=True)
            logger.warning(f"Could not load session analytics from database: {e}")
    
    def get_current_session(self, utc_time: Optional[datetime] = None) -> TradingSession:
        """
        Determine the current trading session.
        
        Args:
            utc_time: UTC datetime (uses current time if None)
            
        Returns:
            Current TradingSession
        """
        if utc_time is None:
            utc_time = datetime.utcnow()
        
        current_time = utc_time.time()
        
        # Check for overlap first (most specific)
        overlap_start, overlap_end = SESSION_TIMES[TradingSession.LONDON_NY_OVERLAP]
        if overlap_start <= current_time <= overlap_end:
            return TradingSession.LONDON_NY_OVERLAP
        
        # Check each session
        for session, (start, end) in SESSION_TIMES.items():
            if session == TradingSession.LONDON_NY_OVERLAP:
                continue  # Already checked
            
            if start <= current_time <= end:
                return session
        
        return TradingSession.OFF_HOURS
    
    def record_trade(
        self,
        symbol: str,
        direction: str,
        profit_loss: float,
        r_multiple: float,
        entry_time: Optional[datetime] = None
    ):
        """
        Record a trade with session attribution.
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            profit_loss: Profit/loss amount
            r_multiple: R-multiple of the trade
            entry_time: UTC time of trade entry
        """
        if entry_time is None:
            entry_time = datetime.utcnow()
        
        session = self.get_current_session(entry_time)
        stats = self.session_stats[session]
        
        # Update stats
        stats.total_trades += 1
        stats.total_pnl += profit_loss
        stats.total_r += r_multiple
        
        if profit_loss > 0:
            stats.wins += 1
        elif profit_loss < 0:
            stats.losses += 1
        # Breakeven (profit_loss == 0) counted in total_trades but not as win or loss
        
        # Track best/worst
        if r_multiple > stats.best_trade_r:
            stats.best_trade_r = r_multiple
        if r_multiple < stats.worst_trade_r:
            stats.worst_trade_r = r_multiple
        
        # Track symbols
        if symbol not in stats.symbols_traded:
            stats.symbols_traded[symbol] = 0
        stats.symbols_traded[symbol] += 1
        
        # Save to history
        self.trade_history.append({
            'timestamp': entry_time,
            'session': session.value,
            'symbol': symbol,
            'direction': direction,
            'profit_loss': profit_loss,
            'r_multiple': r_multiple
        })
        
        logger.debug(f"Recorded {session.value} trade: {symbol} {r_multiple:.1f}R")
    
    def get_best_session(self) -> Optional[TradingSession]:
        """Get the best performing session."""
        best_session = None
        best_expectancy = float('-inf')
        
        for session, stats in self.session_stats.items():
            if stats.total_trades >= 5:  # Minimum sample size
                if stats.expectancy > best_expectancy:
                    best_expectancy = stats.expectancy
                    best_session = session
        
        return best_session
    
    def get_worst_session(self) -> Optional[TradingSession]:
        """Get the worst performing session."""
        worst_session = None
        worst_expectancy = float('inf')
        
        for session, stats in self.session_stats.items():
            if stats.total_trades >= 5:  # Minimum sample size
                if stats.expectancy < worst_expectancy:
                    worst_expectancy = stats.expectancy
                    worst_session = session
        
        return worst_session
    
    def get_session_stats(self, session: TradingSession) -> SessionStats:
        """Get stats for a specific session."""
        return self.session_stats[session]
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get all session statistics."""
        return {
            session.value: stats.to_dict()
            for session, stats in self.session_stats.items()
        }
    
    def get_symbol_session_matrix(self) -> Dict[str, Dict[str, Any]]:
        """
        Get performance matrix: symbol x session.
        
        Returns dict like:
        {
            'EURUSD': {
                'asian': {'trades': 5, 'win_rate': 60, 'avg_r': 1.2},
                'london': {'trades': 10, 'win_rate': 70, 'avg_r': 1.8},
                ...
            }
        }
        """
        # Build matrix from trade history
        matrix: Dict[str, Dict[str, Dict[str, Any]]] = {}
        
        for trade in self.trade_history:
            symbol = trade['symbol']
            session = trade['session']
            
            if symbol not in matrix:
                matrix[symbol] = {}
            
            if session not in matrix[symbol]:
                matrix[symbol][session] = {
                    'trades': 0,
                    'wins': 0,
                    'total_r': 0.0
                }
            
            matrix[symbol][session]['trades'] += 1
            matrix[symbol][session]['total_r'] += trade['r_multiple']
            if trade['profit_loss'] > 0:
                matrix[symbol][session]['wins'] += 1
        
        # Calculate derived metrics
        for symbol in matrix:
            for session in matrix[symbol]:
                data = matrix[symbol][session]
                data['win_rate'] = (data['wins'] / data['trades'] * 100) if data['trades'] > 0 else 0
                data['avg_r'] = data['total_r'] / data['trades'] if data['trades'] > 0 else 0
        
        return matrix
    
    def get_recommendations(self) -> List[str]:
        """Generate session-based trading recommendations."""
        recommendations = []
        
        best = self.get_best_session()
        worst = self.get_worst_session()
        
        if best:
            best_stats = self.session_stats[best]
            recommendations.append(
                f"✅ Focus on {best.value} session: {best_stats.win_rate:.0f}% win rate, "
                f"{best_stats.avg_r:.2f}R avg"
            )
        
        if worst and worst != best:
            worst_stats = self.session_stats[worst]
            if worst_stats.avg_r < 0:
                recommendations.append(
                    f"⚠️ Avoid {worst.value} session: {worst_stats.win_rate:.0f}% win rate, "
                    f"{worst_stats.avg_r:.2f}R avg"
                )
            else:
                recommendations.append(
                    f"📊 {worst.value} session underperforming: consider reducing activity"
                )
        
        # Symbol-specific recommendations
        matrix = self.get_symbol_session_matrix()
        for symbol, sessions in matrix.items():
            for session, data in sessions.items():
                if data['trades'] >= 5:
                    if data['avg_r'] > 1.5:
                        recommendations.append(
                            f"🎯 {symbol} performs well in {session}: {data['avg_r']:.1f}R avg"
                        )
                    elif data['avg_r'] < 0:
                        recommendations.append(
                            f"⛔ Avoid {symbol} in {session}: negative expectancy"
                        )
        
        return recommendations[:10]  # Limit to 10 recommendations
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive session analytics summary."""
        total_trades = sum(s.total_trades for s in self.session_stats.values())
        total_pnl = sum(s.total_pnl for s in self.session_stats.values())
        
        return {
            'total_trades': total_trades,
            'total_pnl': total_pnl,
            'sessions': self.get_all_stats(),
            'best_session': self.get_best_session().value if self.get_best_session() else None,
            'worst_session': self.get_worst_session().value if self.get_worst_session() else None,
            'recommendations': self.get_recommendations()
        }
