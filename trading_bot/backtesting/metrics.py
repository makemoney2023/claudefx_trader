"""
Performance metrics calculation for backtesting.

Calculates:
- Basic statistics (win rate, profit factor, etc.)
- Risk-adjusted returns (Sharpe, Sortino, etc.)
- Drawdown analysis
- Monte Carlo simulation
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

from .simulator import SimulatedPosition
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PerformanceMetrics:
    """Complete performance metrics."""
    # Basic stats
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    
    # Profit/Loss
    total_profit: float
    total_loss: float
    net_profit: float
    profit_factor: float
    
    # R metrics
    total_r: float
    avg_r: float
    
    # Trade stats
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_trade: float
    
    # Consecutive trades
    max_consecutive_wins: int
    max_consecutive_losses: int
    
    # Risk metrics
    max_drawdown: float
    max_drawdown_duration: int  # in bars
    recovery_factor: float
    
    # Risk-adjusted returns
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    
    # Expectancy
    expectancy: float
    expectancy_r: float
    
    # Time-based
    avg_trade_duration: float  # in hours
    trades_per_day: float
    
    # ICT concept performance
    ict_concept_stats: Dict[str, Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "total_profit": self.total_profit,
            "total_loss": self.total_loss,
            "net_profit": self.net_profit,
            "profit_factor": self.profit_factor,
            "total_r": self.total_r,
            "avg_r": self.avg_r,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "avg_trade": self.avg_trade,
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration": self.max_drawdown_duration,
            "recovery_factor": self.recovery_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "expectancy": self.expectancy,
            "expectancy_r": self.expectancy_r,
            "avg_trade_duration": self.avg_trade_duration,
            "trades_per_day": self.trades_per_day,
            "ict_concept_stats": self.ict_concept_stats,
        }


def calculate_metrics(
    positions: List[SimulatedPosition],
    equity_curve: List[float],
    initial_balance: float = 10000.0,
    risk_free_rate: float = 0.02
) -> PerformanceMetrics:
    """
    Calculate comprehensive performance metrics.
    
    Args:
        positions: List of closed positions
        equity_curve: List of equity values over time
        initial_balance: Starting balance
        risk_free_rate: Annual risk-free rate for Sharpe calculation
        
    Returns:
        PerformanceMetrics object
    """
    if not positions:
        return _empty_metrics()
    
    # Basic calculations
    wins = [p for p in positions if p.profit_loss > 0]
    losses = [p for p in positions if p.profit_loss <= 0]
    
    total_trades = len(positions)
    num_wins = len(wins)
    num_losses = len(losses)
    win_rate = num_wins / total_trades if total_trades > 0 else 0
    
    total_profit = sum(p.profit_loss for p in wins)
    total_loss = abs(sum(p.profit_loss for p in losses))
    net_profit = total_profit - total_loss
    
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    
    # R metrics
    total_r = sum(p.r_multiple for p in positions)
    avg_r = total_r / total_trades if total_trades > 0 else 0
    
    # Trade stats
    avg_win = total_profit / num_wins if num_wins > 0 else 0
    avg_loss = total_loss / num_losses if num_losses > 0 else 0
    
    pnls = [p.profit_loss for p in positions]
    largest_win = max(pnls) if pnls else 0
    largest_loss = min(pnls) if pnls else 0
    avg_trade = np.mean(pnls) if pnls else 0
    
    # Consecutive trades
    max_consecutive_wins, max_consecutive_losses = _calculate_consecutive(positions)
    
    # Drawdown analysis
    max_drawdown, max_dd_duration = _calculate_drawdown(equity_curve)
    
    # Recovery factor
    recovery_factor = net_profit / max_drawdown if max_drawdown > 0 else float('inf')
    
    # Risk-adjusted returns
    returns = _calculate_returns(equity_curve)
    sharpe_ratio = _calculate_sharpe(returns, risk_free_rate)
    sortino_ratio = _calculate_sortino(returns, risk_free_rate)
    calmar_ratio = _calculate_calmar(returns, max_drawdown, risk_free_rate)
    
    # Expectancy
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    expectancy_r = (win_rate * np.mean([p.r_multiple for p in wins] or [0])) - \
                   ((1 - win_rate) * abs(np.mean([p.r_multiple for p in losses] or [0])))
    
    # Time-based metrics
    avg_trade_duration = _calculate_avg_duration(positions)
    
    # Calculate trades per day
    if positions:
        first_trade = min(p.entry_time for p in positions)
        last_trade = max(p.exit_time for p in positions if p.exit_time)
        if last_trade and first_trade:
            days = (last_trade - first_trade).days or 1
            trades_per_day = total_trades / days
        else:
            trades_per_day = 0
    else:
        trades_per_day = 0
    
    # ICT concept stats
    ict_concept_stats = _calculate_ict_stats(positions)
    
    return PerformanceMetrics(
        total_trades=total_trades,
        wins=num_wins,
        losses=num_losses,
        win_rate=win_rate,
        total_profit=total_profit,
        total_loss=total_loss,
        net_profit=net_profit,
        profit_factor=profit_factor,
        total_r=total_r,
        avg_r=avg_r,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        avg_trade=avg_trade,
        max_consecutive_wins=max_consecutive_wins,
        max_consecutive_losses=max_consecutive_losses,
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_dd_duration,
        recovery_factor=recovery_factor,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        expectancy=expectancy,
        expectancy_r=expectancy_r,
        avg_trade_duration=avg_trade_duration,
        trades_per_day=trades_per_day,
        ict_concept_stats=ict_concept_stats,
    )


def _empty_metrics() -> PerformanceMetrics:
    """Return empty metrics."""
    return PerformanceMetrics(
        total_trades=0, wins=0, losses=0, win_rate=0,
        total_profit=0, total_loss=0, net_profit=0, profit_factor=0,
        total_r=0, avg_r=0, avg_win=0, avg_loss=0,
        largest_win=0, largest_loss=0, avg_trade=0,
        max_consecutive_wins=0, max_consecutive_losses=0,
        max_drawdown=0, max_drawdown_duration=0, recovery_factor=0,
        sharpe_ratio=0, sortino_ratio=0, calmar_ratio=0,
        expectancy=0, expectancy_r=0, avg_trade_duration=0, trades_per_day=0,
        ict_concept_stats={}
    )


def _calculate_consecutive(positions: List[SimulatedPosition]) -> tuple:
    """Calculate max consecutive wins and losses."""
    max_wins = max_losses = 0
    current_wins = current_losses = 0
    
    for p in positions:
        if p.profit_loss > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)
    
    return max_wins, max_losses


def _calculate_drawdown(equity_curve: List[float]) -> tuple:
    """Calculate maximum drawdown and duration."""
    if not equity_curve:
        return 0, 0
    
    equity = np.array(equity_curve)
    peak = np.maximum.accumulate(equity)
    drawdown = (peak - equity) / peak
    
    max_dd = np.max(drawdown) if len(drawdown) > 0 else 0
    
    # Calculate duration
    max_dd_duration = 0
    current_dd_duration = 0
    
    for i in range(len(equity)):
        if drawdown[i] > 0:
            current_dd_duration += 1
            max_dd_duration = max(max_dd_duration, current_dd_duration)
        else:
            current_dd_duration = 0
    
    return max_dd, max_dd_duration


def _calculate_returns(equity_curve: List[float]) -> np.ndarray:
    """Calculate returns from equity curve."""
    if len(equity_curve) < 2:
        return np.array([])
    
    equity = np.array(equity_curve)
    returns = np.diff(equity) / equity[:-1]
    return returns


def _calculate_sharpe(returns: np.ndarray, risk_free_rate: float) -> float:
    """Calculate Sharpe ratio."""
    if len(returns) == 0 or np.std(returns) == 0:
        return 0
    
    excess_returns = returns - (risk_free_rate / 252)  # Daily risk-free rate
    return np.sqrt(252) * np.mean(excess_returns) / np.std(returns)


def _calculate_sortino(returns: np.ndarray, risk_free_rate: float) -> float:
    """Calculate Sortino ratio (uses downside deviation)."""
    if len(returns) == 0:
        return 0
    
    excess_returns = returns - (risk_free_rate / 252)
    downside_returns = returns[returns < 0]
    
    if len(downside_returns) == 0 or np.std(downside_returns) == 0:
        return 0
    
    return np.sqrt(252) * np.mean(excess_returns) / np.std(downside_returns)


def _calculate_calmar(returns: np.ndarray, max_drawdown: float, risk_free_rate: float) -> float:
    """Calculate Calmar ratio."""
    if max_drawdown == 0:
        return 0
    
    annual_return = np.mean(returns) * 252 if len(returns) > 0 else 0
    return annual_return / max_drawdown


def _calculate_avg_duration(positions: List[SimulatedPosition]) -> float:
    """Calculate average trade duration in hours."""
    durations = []
    
    for p in positions:
        if p.entry_time and p.exit_time:
            duration = (p.exit_time - p.entry_time).total_seconds() / 3600
            durations.append(duration)
    
    return np.mean(durations) if durations else 0


def _calculate_ict_stats(positions: List[SimulatedPosition]) -> Dict[str, Dict[str, Any]]:
    """Calculate performance by ICT concept."""
    concept_trades: Dict[str, List[SimulatedPosition]] = {}
    
    for p in positions:
        for concept, used in p.ict_concepts.items():
            if used:
                if concept not in concept_trades:
                    concept_trades[concept] = []
                concept_trades[concept].append(p)
    
    stats = {}
    for concept, trades in concept_trades.items():
        wins = [t for t in trades if t.profit_loss > 0]
        stats[concept] = {
            "trades": len(trades),
            "wins": len(wins),
            "win_rate": len(wins) / len(trades) if trades else 0,
            "avg_r": np.mean([t.r_multiple for t in trades]) if trades else 0,
            "total_pnl": sum(t.profit_loss for t in trades)
        }
    
    return stats


def run_monte_carlo(
    positions: List[SimulatedPosition],
    initial_balance: float = 10000.0,
    num_simulations: int = 1000
) -> Dict[str, Any]:
    """
    Run Monte Carlo simulation on trade results.
    
    Randomly shuffles trade order to estimate result distribution.
    
    Args:
        positions: List of closed positions
        initial_balance: Starting balance
        num_simulations: Number of simulations to run
        
    Returns:
        Dictionary with simulation results
    """
    if not positions:
        return {"error": "No positions to simulate"}
    
    pnls = [p.profit_loss for p in positions]
    final_balances = []
    max_drawdowns = []
    
    for _ in range(num_simulations):
        # Shuffle trades
        shuffled = np.random.permutation(pnls)
        
        # Calculate equity curve
        equity = initial_balance + np.cumsum(shuffled)
        equity = np.insert(equity, 0, initial_balance)
        
        final_balances.append(equity[-1])
        
        # Calculate drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        max_drawdowns.append(np.max(drawdown))
    
    return {
        "final_balance_mean": np.mean(final_balances),
        "final_balance_median": np.median(final_balances),
        "final_balance_std": np.std(final_balances),
        "final_balance_5th_percentile": np.percentile(final_balances, 5),
        "final_balance_95th_percentile": np.percentile(final_balances, 95),
        "max_drawdown_mean": np.mean(max_drawdowns),
        "max_drawdown_95th_percentile": np.percentile(max_drawdowns, 95),
        "probability_of_profit": np.mean(np.array(final_balances) > initial_balance),
        "num_simulations": num_simulations
    }
