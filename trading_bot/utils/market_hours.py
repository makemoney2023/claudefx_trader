"""
Market Hours Utility.

Handles market open/close times, weekends, and holidays.
"""

from datetime import datetime, time, timedelta
from typing import Optional, Tuple
from enum import Enum

from .logging import get_logger

logger = get_logger(__name__)


class MarketType(Enum):
    """Types of markets with different hours."""
    FOREX = "forex"
    CRYPTO = "crypto"
    METALS = "metals"


# Market hours in UTC
MARKET_HOURS = {
    MarketType.FOREX: {
        # Forex: Sunday 22:00 UTC to Friday 22:00 UTC
        "open_day": 6,  # Sunday
        "open_time": time(22, 0),
        "close_day": 4,  # Friday
        "close_time": time(22, 0),
    },
    MarketType.CRYPTO: {
        # Crypto: 24/7
        "always_open": True
    },
    MarketType.METALS: {
        # Similar to forex but with daily breaks
        "open_day": 6,  # Sunday
        "open_time": time(22, 0),
        "close_day": 4,  # Friday
        "close_time": time(22, 0),
        "daily_break_start": time(22, 0),
        "daily_break_end": time(23, 0),
    }
}


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
        current_time = datetime.utcnow()
    
    market_type = get_market_type(symbol)
    hours = MARKET_HOURS[market_type]
    
    # Crypto is always open
    if hours.get("always_open"):
        return True, "Crypto markets are 24/7"
    
    weekday = current_time.weekday()
    current_t = current_time.time()
    
    # Check weekend
    if weekday == 5:  # Saturday
        return False, "Market closed - Saturday"
    
    if weekday == 6:  # Sunday
        if current_t < hours["open_time"]:
            return False, "Market closed - Sunday before open"
    
    if weekday == 4:  # Friday
        if current_t >= hours["close_time"]:
            return False, "Market closed - Friday after close"
    
    # Check daily break for metals (Mon-Thu only, NOT on Sunday open)
    if market_type == MarketType.METALS:
        break_start = hours.get("daily_break_start")
        break_end = hours.get("daily_break_end")
        if break_start and break_end:
            # Daily break only applies Mon-Thu (weekday 0-3)
            # Sunday (6) is market open, not a break
            # Friday (4) is market close, handled above
            if weekday <= 3 and break_start <= current_t <= break_end:
                return False, "Market closed - daily maintenance break"
    
    return True, "Market is open"


def get_next_market_open(symbol: str, current_time: Optional[datetime] = None) -> Optional[datetime]:
    """
    Get the next market open time.
    
    Args:
        symbol: Trading symbol
        current_time: Current time (default: now UTC)
        
    Returns:
        Datetime of next market open, or None if always open
    """
    if current_time is None:
        current_time = datetime.utcnow()
    
    market_type = get_market_type(symbol)
    
    if market_type == MarketType.CRYPTO:
        return None  # Always open
    
    hours = MARKET_HOURS[market_type]
    
    is_open, _ = is_market_open(symbol, current_time)
    if is_open:
        return None  # Already open
    
    weekday = current_time.weekday()
    
    # Calculate days until Sunday 22:00 UTC
    if weekday == 5:  # Saturday
        days_ahead = 1  # Sunday
    elif weekday == 6:  # Sunday
        if current_time.time() < hours["open_time"]:
            days_ahead = 0  # Later today
        else:
            days_ahead = 7  # Next Sunday
    else:  # Friday after close
        days_ahead = 6 - weekday + 1  # Days to Sunday
    
    next_open = current_time + timedelta(days=days_ahead)
    next_open = next_open.replace(
        hour=hours["open_time"].hour,
        minute=hours["open_time"].minute,
        second=0,
        microsecond=0
    )
    
    return next_open


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
        current_time = datetime.utcnow()
    
    market_type = get_market_type(symbol)
    
    if market_type == MarketType.CRYPTO:
        return None  # Always open
    
    hours = MARKET_HOURS[market_type]
    
    is_open, _ = is_market_open(symbol, current_time)
    if not is_open:
        return timedelta(0)  # Already closed
    
    weekday = current_time.weekday()
    
    # Days until Friday
    if weekday <= 4:
        days_ahead = 4 - weekday  # Days to Friday
    else:
        days_ahead = 4 + (7 - weekday)  # Days to next Friday
    
    close_time = current_time + timedelta(days=days_ahead)
    close_time = close_time.replace(
        hour=hours["close_time"].hour,
        minute=hours["close_time"].minute,
        second=0,
        microsecond=0
    )
    
    return close_time - current_time


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
        current_time = datetime.utcnow()
    
    is_open, reason = is_market_open(symbol, current_time)
    if not is_open:
        return True, reason
    
    time_until_close = get_time_until_close(symbol, current_time)
    
    if time_until_close is None:
        return False, "Market always open"
    
    # Avoid new trades within 2 hours of close
    if time_until_close < timedelta(hours=2):
        return True, f"Market closing in {time_until_close}"
    
    # Avoid new trades on Friday afternoon
    weekday = current_time.weekday()
    if weekday == 4 and current_time.hour >= 18:  # Friday 6pm UTC
        return True, "Friday evening - avoid new positions"
    
    return False, "OK to trade"
