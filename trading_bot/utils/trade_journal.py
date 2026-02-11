"""
Trade Journal Module.

Logs and tracks all trades for performance analysis and review.
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import pandas as pd

from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class TradeRecord:
    """Record of a single trade."""
    trade_id: str
    timestamp: datetime
    symbol: str
    direction: str  # 'long' or 'short'
    
    # Entry details
    entry_price: float
    entry_time: datetime
    entry_reason: str
    
    # Exit details
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    
    # Risk management
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size: float = 0.0
    risk_amount: float = 0.0
    
    # Results
    profit_loss: Optional[float] = None
    profit_loss_pips: Optional[float] = None
    r_multiple: Optional[float] = None
    
    # Context
    timeframe: str = ""
    session: str = ""
    market_structure: str = ""
    
    # ICT concepts used
    ict_concepts: List[str] = None
    
    # Claude analysis
    claude_confidence: float = 0.0
    claude_reasoning: str = ""
    
    # Screenshots
    entry_chart_path: Optional[str] = None
    exit_chart_path: Optional[str] = None
    
    # Notes
    notes: str = ""
    
    def __post_init__(self):
        if self.ict_concepts is None:
            self.ict_concepts = []
    
    def calculate_results(self, pip_value: float = 0.0001):
        """Calculate profit/loss after trade closes."""
        if self.exit_price is None:
            return
        
        if self.direction == 'long':
            self.profit_loss_pips = (self.exit_price - self.entry_price) / pip_value
        else:
            self.profit_loss_pips = (self.entry_price - self.exit_price) / pip_value
        
        self.profit_loss = self.profit_loss_pips * self.position_size * pip_value * 100000
        
        # Calculate R multiple
        risk_pips = abs(self.entry_price - self.stop_loss) / pip_value
        if risk_pips > 0:
            self.r_multiple = self.profit_loss_pips / risk_pips
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['entry_time'] = self.entry_time.isoformat()
        if self.exit_time:
            data['exit_time'] = self.exit_time.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TradeRecord':
        """Create from dictionary."""
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        data['entry_time'] = datetime.fromisoformat(data['entry_time'])
        if data.get('exit_time'):
            data['exit_time'] = datetime.fromisoformat(data['exit_time'])
        return cls(**data)


class TradeJournal:
    """
    Manages trade history and performance analytics.
    
    Features:
    - Trade logging
    - Performance statistics
    - Export to CSV/JSON
    - ICT concept analysis
    """
    
    def __init__(self, journal_path: str = "trades/journal.json"):
        """
        Initialize the trade journal.
        
        Args:
            journal_path: Path to journal file
        """
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.trades: List[TradeRecord] = []
        self._load_journal()
        
        logger.info(f"Trade journal initialized: {self.journal_path}")
    
    def _load_journal(self):
        """Load existing journal from file."""
        if self.journal_path.exists():
            try:
                with open(self.journal_path, 'r') as f:
                    data = json.load(f)
                    self.trades = [TradeRecord.from_dict(t) for t in data]
                logger.info(f"Loaded {len(self.trades)} trades from journal")
            except Exception as e:
                logger.error(f"Error loading journal: {e}")
                self.trades = []
    
    def _save_journal(self):
        """Save journal to file."""
        try:
            with open(self.journal_path, 'w') as f:
                json.dump([t.to_dict() for t in self.trades], f, indent=2)
        except Exception as e:
            logger.error(f"Error saving journal: {e}")
    
    def log_trade(self, trade: TradeRecord):
        """Log a new trade."""
        self.trades.append(trade)
        self._save_journal()
        logger.info(f"Logged trade {trade.trade_id}: {trade.symbol} {trade.direction}")
    
    def update_trade(self, trade_id: str, **kwargs):
        """Update an existing trade."""
        for trade in self.trades:
            if trade.trade_id == trade_id:
                for key, value in kwargs.items():
                    if hasattr(trade, key):
                        setattr(trade, key, value)
                self._save_journal()
                logger.info(f"Updated trade {trade_id}")
                return True
        return False
    
    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        exit_reason: str
    ):
        """Close a trade and calculate results."""
        for trade in self.trades:
            if trade.trade_id == trade_id:
                trade.exit_price = exit_price
                trade.exit_time = exit_time
                trade.exit_reason = exit_reason
                trade.calculate_results()
                self._save_journal()
                logger.info(f"Closed trade {trade_id}: P/L = {trade.profit_loss:.2f}")
                return trade
        return None
    
    def get_trade(self, trade_id: str) -> Optional[TradeRecord]:
        """Get a specific trade by ID."""
        for trade in self.trades:
            if trade.trade_id == trade_id:
                return trade
        return None
    
    def get_open_trades(self) -> List[TradeRecord]:
        """Get all open trades."""
        return [t for t in self.trades if t.exit_price is None]
    
    def get_closed_trades(self) -> List[TradeRecord]:
        """Get all closed trades."""
        return [t for t in self.trades if t.exit_price is not None]
    
    def get_statistics(self, period_days: Optional[int] = None) -> Dict[str, Any]:
        """
        Calculate trading statistics.
        
        Args:
            period_days: Limit to last N days (None for all time)
            
        Returns:
            Dictionary of statistics
        """
        closed_trades = self.get_closed_trades()
        
        if period_days:
            cutoff = datetime.now() - pd.Timedelta(days=period_days)
            closed_trades = [t for t in closed_trades if t.exit_time and t.exit_time >= cutoff]
        
        if not closed_trades:
            return {"total_trades": 0, "message": "No closed trades"}
        
        wins = [t for t in closed_trades if t.profit_loss and t.profit_loss > 0]
        losses = [t for t in closed_trades if t.profit_loss and t.profit_loss <= 0]
        
        total_profit = sum(t.profit_loss or 0 for t in closed_trades)
        total_r = sum(t.r_multiple or 0 for t in closed_trades)
        
        win_rate = len(wins) / len(closed_trades) if closed_trades else 0
        
        avg_win = sum(t.profit_loss or 0 for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t.profit_loss or 0 for t in losses) / len(losses)) if losses else 0
        
        profit_factor = avg_win * len(wins) / (avg_loss * len(losses)) if losses and avg_loss > 0 else float('inf')
        
        # ICT concept effectiveness
        ict_stats = self._analyze_ict_concepts(closed_trades)
        
        return {
            "total_trades": len(closed_trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_profit": total_profit,
            "total_r": total_r,
            "avg_r": total_r / len(closed_trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "largest_win": max((t.profit_loss or 0 for t in closed_trades), default=0),
            "largest_loss": min((t.profit_loss or 0 for t in closed_trades), default=0),
            "ict_concept_stats": ict_stats
        }
    
    def _analyze_ict_concepts(self, trades: List[TradeRecord]) -> Dict[str, Dict]:
        """Analyze which ICT concepts are most effective."""
        concept_stats = {}
        
        for trade in trades:
            for concept in trade.ict_concepts:
                if concept not in concept_stats:
                    concept_stats[concept] = {
                        "trades": 0,
                        "wins": 0,
                        "total_r": 0
                    }
                
                concept_stats[concept]["trades"] += 1
                if trade.profit_loss and trade.profit_loss > 0:
                    concept_stats[concept]["wins"] += 1
                concept_stats[concept]["total_r"] += trade.r_multiple or 0
        
        # Calculate win rates
        for concept, stats in concept_stats.items():
            stats["win_rate"] = stats["wins"] / stats["trades"] if stats["trades"] > 0 else 0
            stats["avg_r"] = stats["total_r"] / stats["trades"] if stats["trades"] > 0 else 0
        
        return concept_stats
    
    def get_daily_summary(self, date: Optional[datetime] = None) -> Dict[str, Any]:
        """Get summary for a specific day."""
        if date is None:
            date = datetime.now()
        
        day_start = datetime(date.year, date.month, date.day)
        day_end = day_start + pd.Timedelta(days=1)
        
        day_trades = [
            t for t in self.trades
            if t.entry_time >= day_start and t.entry_time < day_end
        ]
        
        closed_today = [t for t in day_trades if t.exit_time and t.exit_time < day_end]
        
        return {
            "date": date.strftime("%Y-%m-%d"),
            "trades_opened": len(day_trades),
            "trades_closed": len(closed_today),
            "daily_pnl": sum(t.profit_loss or 0 for t in closed_today),
            "daily_r": sum(t.r_multiple or 0 for t in closed_today)
        }
    
    def export_to_csv(self, filepath: str):
        """Export trades to CSV file."""
        if not self.trades:
            return
        
        df = pd.DataFrame([t.to_dict() for t in self.trades])
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(self.trades)} trades to {filepath}")
    
    def generate_report(self) -> str:
        """Generate a text report of trading performance."""
        stats = self.get_statistics()
        
        report = f"""
========================================
        TRADING PERFORMANCE REPORT
========================================

Period: All Time
Total Trades: {stats.get('total_trades', 0)}
Win Rate: {stats.get('win_rate', 0):.1%}

PROFIT/LOSS
-----------
Total P/L: ${stats.get('total_profit', 0):,.2f}
Total R: {stats.get('total_r', 0):.2f}R
Average R: {stats.get('avg_r', 0):.2f}R

WINS/LOSSES
-----------
Wins: {stats.get('wins', 0)}
Losses: {stats.get('losses', 0)}
Average Win: ${stats.get('avg_win', 0):,.2f}
Average Loss: ${stats.get('avg_loss', 0):,.2f}
Profit Factor: {stats.get('profit_factor', 0):.2f}

BEST/WORST
----------
Largest Win: ${stats.get('largest_win', 0):,.2f}
Largest Loss: ${stats.get('largest_loss', 0):,.2f}

ICT CONCEPT EFFECTIVENESS
-------------------------
"""
        
        ict_stats = stats.get('ict_concept_stats', {})
        for concept, data in sorted(ict_stats.items(), key=lambda x: x[1].get('win_rate', 0), reverse=True):
            report += f"{concept}: {data['trades']} trades, {data['win_rate']:.1%} win rate, {data['avg_r']:.2f}R avg\n"
        
        report += "\n========================================"
        
        return report
