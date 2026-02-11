"""
Market Data Fetcher Module.

Retrieves and processes market data from MT5 through MCP,
converting it to pandas DataFrames for analysis.
"""

from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from .client import MT5Client
from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


class DataFetcher:
    """
    Fetches and prepares market data for analysis.
    
    Retrieves OHLCV data from MT5 and converts it to
    pandas DataFrames suitable for technical analysis.
    """
    
    def __init__(self, mt5_client: Optional[MT5Client] = None):
        """
        Initialize the data fetcher.
        
        Args:
            mt5_client: MT5 client instance
        """
        self.mt5_client = mt5_client
        self._cache: Dict[str, pd.DataFrame] = {}
        
        logger.info("Data fetcher initialized")
    
    def set_mt5_client(self, client: MT5Client):
        """Set the MT5 client."""
        self.mt5_client = client
    
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        count: int = 100,
        use_cache: bool = True,
        allow_sample_data: bool = False
    ) -> Optional[pd.DataFrame]:
        """
        Get OHLCV data as a pandas DataFrame.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1)
            count: Number of bars to retrieve
            use_cache: Whether to use cached data
            allow_sample_data: If True, return generated sample data when MT5 fails.
                               For trading, this should be False to prevent trading on fake data.
            
        Returns:
            DataFrame with OHLCV data or None
        """
        cache_key = f"{symbol}_{timeframe}"
        
        # Check cache
        if use_cache and cache_key in self._cache:
            cached_df = self._cache[cache_key]
            # Check if cache is still valid (within last candle period)
            if self._is_cache_valid(cached_df, timeframe):
                logger.debug(f"Using cached data for {cache_key}")
                return cached_df
        
        if not self.mt5_client:
            logger.error("MT5 client not connected")
            return None
        
        try:
            # Fetch data from MT5
            raw_data = await self.mt5_client.get_ohlcv_data(
                symbol=symbol,
                timeframe=timeframe,
                count=count
            )
            
            if not raw_data:
                logger.warning(f"No data returned for {symbol} {timeframe}")
                if allow_sample_data:
                    logger.warning("Returning sample data (NOT for live trading)")
                    return self._generate_sample_data(count)
                return None  # Don't return fake data for trading
            
            # Convert to DataFrame
            df = self._to_dataframe(raw_data)
            
            # Cache the data
            self._cache[cache_key] = df
            
            logger.info(f"Fetched {len(df)} bars for {symbol} {timeframe}")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV data: {e}")
            return None
    
    async def get_multi_timeframe_data(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        count: int = 100
    ) -> Dict[str, pd.DataFrame]:
        """
        Get OHLCV data for multiple timeframes.
        
        Args:
            symbol: Trading symbol
            timeframes: List of timeframes (defaults to config)
            count: Number of bars per timeframe
            
        Returns:
            Dict of timeframe -> DataFrame
        """
        if timeframes is None:
            timeframes = [settings.timeframes.higher_tf, settings.timeframes.execution_tf]
        
        data = {}
        
        for tf in timeframes:
            df = await self.get_ohlcv(symbol, tf, count)
            if df is not None:
                data[tf] = df
        
        return data
    
    async def get_current_price(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Get current bid/ask/spread for a symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with bid, ask, spread
        """
        if not self.mt5_client:
            return None
        
        return await self.mt5_client.get_current_price(symbol)
    
    async def get_session_range(
        self,
        symbol: str,
        session: str = "asian"
    ) -> Optional[Dict[str, float]]:
        """
        Get the high/low range for a trading session.
        
        Args:
            symbol: Trading symbol
            session: Session name (asian, london, new_york)
            
        Returns:
            Dict with session high, low, range
        """
        try:
            # Get M15 data for the session period
            df = await self.get_ohlcv(symbol, "M15", count=50)
            
            if df is None or df.empty:
                return None
            
            # Calculate session range based on time
            # This is a simplified version - production would use actual session times
            
            if session == "asian":
                # Asian session: roughly 19:00 - 00:00 EST
                session_bars = 20  # Approximate
            elif session == "london":
                # London session: roughly 02:00 - 05:00 EST
                session_bars = 12
            else:
                session_bars = 20
            
            session_data = df.tail(session_bars)
            
            return {
                "high": session_data['high'].max(),
                "low": session_data['low'].min(),
                "range": session_data['high'].max() - session_data['low'].min()
            }
            
        except Exception as e:
            logger.error(f"Error getting session range: {e}")
            return None
    
    async def get_daily_range(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Get today's high/low range.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with daily high, low, range
        """
        try:
            # Get H1 data for today
            df = await self.get_ohlcv(symbol, "H1", count=24)
            
            if df is None or df.empty:
                return None
            
            return {
                "high": df['high'].max(),
                "low": df['low'].min(),
                "open": df.iloc[0]['open'],
                "range": df['high'].max() - df['low'].min()
            }
            
        except Exception as e:
            logger.error(f"Error getting daily range: {e}")
            return None
    
    def _to_dataframe(self, data: List[Dict[str, Any]]) -> pd.DataFrame:
        """Convert raw data to pandas DataFrame."""
        df = pd.DataFrame(data)
        
        # Ensure standard column names
        column_mapping = {
            'time': 'time',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'tick_volume': 'volume',
            'real_volume': 'volume',
            'spread': 'spread'
        }
        
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
        
        # Convert time to datetime and set as index
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
        
        # Ensure numeric columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        return df
    
    def _is_cache_valid(self, df: pd.DataFrame, timeframe: str) -> bool:
        """Check if cached data is still valid."""
        if df.empty:
            return False
        
        # Get timeframe in minutes
        tf_minutes = {
            'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30,
            'H1': 60, 'H4': 240, 'D1': 1440
        }
        
        minutes = tf_minutes.get(timeframe.upper(), 60)
        
        # Check if last bar is within current candle period
        last_bar_time = df.index[-1]
        if isinstance(last_bar_time, pd.Timestamp):
            age = datetime.now() - last_bar_time.to_pydatetime()
            return age < timedelta(minutes=minutes)
        
        return False
    
    def _generate_sample_data(self, count: int = 100) -> pd.DataFrame:
        """
        Generate sample OHLCV data for testing.
        
        This is used when MT5 connection is not available.
        """
        logger.warning("Generating sample data - no MT5 connection")
        
        # Generate realistic-looking price data
        np.random.seed(42)
        
        base_price = 1.0850
        dates = pd.date_range(end=datetime.now(), periods=count, freq='1h')
        
        # Random walk for close prices
        returns = np.random.randn(count) * 0.001
        close = base_price * np.exp(np.cumsum(returns))
        
        # Generate OHLC from close
        high = close * (1 + np.abs(np.random.randn(count) * 0.0005))
        low = close * (1 - np.abs(np.random.randn(count) * 0.0005))
        open_prices = close * (1 + np.random.randn(count) * 0.0003)
        
        # Ensure high/low contain open/close
        high = np.maximum.reduce([high, open_prices, close])
        low = np.minimum.reduce([low, open_prices, close])
        
        volume = np.random.randint(100, 1000, count)
        
        df = pd.DataFrame({
            'open': open_prices,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        }, index=dates)
        
        return df
    
    def clear_cache(self):
        """Clear the data cache."""
        self._cache.clear()
        logger.info("Data cache cleared")
