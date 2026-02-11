"""
Backtesting module for ICT Trading Bot.

Provides functionality for:
- Historical data loading
- Strategy backtesting
- Performance metrics calculation
- Report generation
"""

from .engine import Backtester, BacktestResult, BacktestConfig
from .data_loader import DataLoader
from .simulator import OrderSimulator, SimulatedPosition
from .metrics import PerformanceMetrics, calculate_metrics
from .report import BacktestReport, generate_html_report

__all__ = [
    "Backtester",
    "BacktestResult",
    "BacktestConfig",
    "DataLoader",
    "OrderSimulator",
    "SimulatedPosition",
    "PerformanceMetrics",
    "calculate_metrics",
    "BacktestReport",
    "generate_html_report",
]
