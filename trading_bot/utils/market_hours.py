"""
Market Hours Utility.

Handles market open/close times, weekends, and holidays.
Session boundaries follow US/Eastern (NYSE) wall-clock times so DST is handled
consistently with kill_zones.py and silver_bullet.py.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional, Tuple
from enum import Enum

import pytz

from .logging import get_logger

logger = get_logger(__name__)

NY_TZ = pytz.timezone("US/Eastern")

# Wall-clock Eastern session boundaries (DST-aware via NY_TZ conversion)
FOREX_OPEN = time(17, 0)   # Sunday 5 PM ET
FOREX_CLOSE = time(17, 0)  # Friday 5 PM ET
DAILY_BREAK_START = time(17, 0)  # Mon-Thu maintenance
DAILY_BREAK_END = time(18, 0)


class MarketType(Enum):
    """Types of markets with different hours."""
    FOREX = "forex"
    CRYPTO = "crypto"
    METALS = "metals"
    INDICES = "indices"
    OIL = "oil"


def _to_eastern(current_time: datetime) -> datetime:
    """Convert any aware/naive UTC time to US/Eastern."""
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return current_time.astimezone(NY_TZ)


def get_market_type(symbol: str) -> MarketType:
    """Determine market type from symbol."""
    symbol = symbol.upper()
    
    # DANGEROUS: BTC/BIT pairs are blocked - treat as FOREX (closed on weekends)
    # These pairs have contract values in BTC, not USD, causing massive position sizing errors
    if symbol.endswith('BTC') or symbol.endswith('BIT'):
        return MarketType.FOREX  # This will block them on weekends
    
    # SAFE Crypto symbols - ONLY USD pairs!
    crypto_symbols = [
        'BTCUSD', 'ETHUSD', 'XRPUSD', 'ADAUSD', 'LTCUSD', 'DOGEUSD',
        'SOLUSD', 'DOTUSD', 'EOSUSD', 'NEOUSD', 'ETCUSD', 'XMRUSD',
        'ZECUSD', 'DASHUSD', 'IOTAUSD', 'BITUSD', 'USDTUSD'
    ]
    # Only USD-quoted crypto is safe for 24/7 trading
    if symbol in crypto_symbols or symbol.endswith('USDT'):
        return MarketType.CRYPTO
    
    # Metals
    if symbol in ['XAUUSD', 'XAGUSD', 'GOLD', 'SILVER']:
        return MarketType.METALS
    
    # Oil / Energy
    oil_symbols = ['USOIL', 'WTIUSD', 'CRUDEOIL', 'BRENT', 'UKOIL', 'XTIUSD', 'XBRUSD']
    if symbol in oil_symbols or 'OIL' in symbol or 'WTI' in symbol or 'BRENT' in symbol:
        return MarketType.OIL
    
    # Indices
    index_symbols = [
        'US30', 'NAS100', 'US500', 'DJ30', 'USTEC', 'SP500',
        'US30CASH', 'NAS100CASH', 'US500CASH',
        'DE30', 'UK100', 'JP225', 'AU200', 'FR40', 'EU50',
    ]
    if symbol in index_symbols or symbol.startswith('US30') or symbol.startswith('NAS') or symbol.startswith('US500') or symbol.startswith('SP500'):
        return MarketType.INDICES
    
    return MarketType.FOREX


def is_market_open(symbol: str, current_time: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    Check if market is open for a symbol.
    
    Args:
        symbol: Trading symbol
        current_time: Time to check (default: now UTC)
        
    Returns:
        Tuple of (is_open, reason)
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    
    market_type = get_market_type(symbol)
    
    # Crypto is always open
    if market_type == MarketType.CRYPTO:
        return True, "Crypto markets are 24/7"
    
    et = _to_eastern(current_time)
    weekday = et.weekday()
    current_t = et.time()
    
    # Check weekend
    if weekday == 5:  # Saturday
        return False, "Market closed - Saturday"
    
    if weekday == 6:  # Sunday
        if current_t < FOREX_OPEN:
            return False, "Market closed - Sunday before open"
    
    if weekday == 4:  # Friday
        if current_t >= FOREX_CLOSE:
            return False, "Market closed - Friday after close"
    
    # Daily break for metals, indices, and oil (Mon-Thu only, NOT on Sunday open)
    if market_type in (MarketType.METALS, MarketType.INDICES, MarketType.OIL):
        # Daily break only applies Mon-Thu (weekday 0-3)
        if weekday <= 3 and DAILY_BREAK_START <= current_t <= DAILY_BREAK_END:
            return False, "Market closed - daily maintenance break"
    
    return True, "Market is open"


def get_next_market_open(symbol: str, current_time: Optional[datetime] = None) -> Optional[datetime]:
    """
    Get the next market open time.
    
    Args:
        symbol: Trading symbol
        current_time: Current time (default: now UTC)
        
    Returns:
        Datetime of next market open (UTC), or None if always open
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    
    market_type = get_market_type(symbol)
    
    if market_type == MarketType.CRYPTO:
        return None  # Always open
    
    is_open, _ = is_market_open(symbol, current_time)
    if is_open:
        return None  # Already open
    
    et = _to_eastern(current_time)
    weekday = et.weekday()
    
    # Calculate days until Sunday 17:00 ET
    if weekday == 5:  # Saturday
        days_ahead = 1  # Sunday
    elif weekday == 6:  # Sunday
        if et.time() < FOREX_OPEN:
            days_ahead = 0  # Later today
        else:
            days_ahead = 7  # Next Sunday
    else:  # Friday after close or other closed state
        days_ahead = (6 - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
    
    next_open_et = et + timedelta(days=days_ahead)
    next_open_et = next_open_et.replace(
        hour=FOREX_OPEN.hour,
        minute=FOREX_OPEN.minute,
        second=0,
        microsecond=0,
    )
    return next_open_et.astimezone(timezone.utc)


def get_time_until_close(symbol: str, current_time: Optional[datetime] = None) -> Optional[timedelta]:
    """
    Get time remaining until market close.
    
    Args:
        symbol: Trading symbol
        current_time: Current time (default: now UTC)
        
    Returns:
        Timedelta until close, or None if always open
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    
    market_type = get_market_type(symbol)
    
    if market_type == MarketType.CRYPTO:
        return None  # Always open
    
    is_open, _ = is_market_open(symbol, current_time)
    if not is_open:
        return timedelta(0)  # Already closed
    
    et = _to_eastern(current_time)
    weekday = et.weekday()
    
    # Days until Friday close at 17:00 ET
    if weekday <= 4:
        days_ahead = 4 - weekday
    else:
        days_ahead = 4 + (7 - weekday)
    
    close_et = et + timedelta(days=days_ahead)
    close_et = close_et.replace(
        hour=FOREX_CLOSE.hour,
        minute=FOREX_CLOSE.minute,
        second=0,
        microsecond=0,
    )
    
    return close_et - et


def should_avoid_new_trades(symbol: str, current_time: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    Check if new trades should be avoided (close to market close).
    
    Args:
        symbol: Trading symbol
        current_time: Current time (default: now UTC)
        
    Returns:
        Tuple of (should_avoid, reason)
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    
    is_open, reason = is_market_open(symbol, current_time)
    if not is_open:
        return True, reason
    
    time_until_close = get_time_until_close(symbol, current_time)
    
    if time_until_close is None:
        return False, "Market always open"
    
    # Avoid new trades within 2 hours of close
    if time_until_close < timedelta(hours=2):
        return True, f"Market closing in {time_until_close}"
    
    # Avoid new trades on Friday afternoon (after 2 PM ET)
    et = _to_eastern(current_time)
    if et.weekday() == 4 and et.hour >= 14:
        return True, "Friday afternoon - avoid new positions"
    
    return False, "OK to trade"
