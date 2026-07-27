"""
ICT Trading Bot

An AI-powered forex trading bot combining ICT (Inner Circle Trading),
Market Maker, and Fair Value Gap strategies with MetaTrader 5 via MCP,
using Claude Opus 5 for intelligent chart analysis and trade execution.
"""

__version__ = "0.1.0"
__author__ = "Trading Bot Team"

from .config import settings

__all__ = ["settings", "__version__"]
