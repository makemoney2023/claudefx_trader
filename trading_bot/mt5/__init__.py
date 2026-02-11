"""
MetaTrader 5 integration modules.

Provides MT5 MCP client wrapper for:
- Account information
- Market data retrieval
- Order execution
- Position management
"""

from .client import MT5Client
from .data_fetcher import DataFetcher

__all__ = [
    "MT5Client",
    "DataFetcher",
]
