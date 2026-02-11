"""Utility modules for the trading bot."""

from .logging import setup_logging, get_logger
from .candle_utils import (
    calculate_body_percentage,
    is_bullish_candle,
    is_bearish_candle,
    get_candle_range,
    find_swing_highs,
    find_swing_lows,
)
from .trade_journal import TradeJournal, TradeRecord

# Chart screenshot is optional - requires matplotlib/mplfinance
try:
    from .chart_screenshot import ChartScreenshot, create_simple_chart
except ImportError as e:
    import warnings
    warnings.warn(f"Chart screenshot module not available: {e}. Install matplotlib and mplfinance.")
    ChartScreenshot = None
    create_simple_chart = None

__all__ = [
    "setup_logging",
    "get_logger",
    "calculate_body_percentage",
    "is_bullish_candle",
    "is_bearish_candle",
    "get_candle_range",
    "find_swing_highs",
    "find_swing_lows",
    "ChartScreenshot",
    "create_simple_chart",
    "TradeJournal",
    "TradeRecord",
]
