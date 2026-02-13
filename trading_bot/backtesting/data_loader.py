"""
Historical data loader for backtesting.

Supports loading data from:
- CSV files
- MT5 history (when connected)
- Sample data generation
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

from ..config import get_symbol_spec
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DataConfig:
    """Configuration for data loading."""
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    source: str = "csv"  # csv, mt5, sample


class DataLoader:
    """
    Load historical OHLCV data for backtesting.
    
    Supports multiple data sources and handles data preprocessing.
    """
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache: Dict[str, pd.DataFrame] = {}
        logger.info(f"DataLoader initialized with data_dir: {data_dir}")
    
    def load(self, config: DataConfig) -> pd.DataFrame:
        """
        Load historical data based on configuration.
        
        Args:
            config: Data loading configuration
            
        Returns:
            DataFrame with OHLCV data
        """
        cache_key = f"{config.symbol}_{config.timeframe}_{config.start_date}_{config.end_date}"
        
        if cache_key in self.cache:
            logger.debug(f"Returning cached data for {cache_key}")
            return self.cache[cache_key].copy()
        
        if config.source == "csv":
            df = self._load_from_csv(config)
        elif config.source == "mt5":
            df = self._load_from_mt5(config)
        else:
            df = self._generate_sample_data(config)
        
        # Preprocess data
        df = self._preprocess(df, config)
        
        # Cache the result
        self.cache[cache_key] = df
        
        logger.info(f"Loaded {len(df)} bars for {config.symbol} {config.timeframe}")
        return df
    
    def _load_from_csv(self, config: DataConfig) -> pd.DataFrame:
        """Load data from CSV file."""
        filename = f"{config.symbol}_{config.timeframe}.csv"
        filepath = self.data_dir / filename
        
        if not filepath.exists():
            logger.warning(f"CSV file not found: {filepath}, generating sample data")
            return self._generate_sample_data(config)
        
        df = pd.read_csv(filepath, parse_dates=['time'])
        df.set_index('time', inplace=True)
        
        return df
    
    def _load_from_mt5(self, config: DataConfig) -> pd.DataFrame:
        """
        Load historical data from MT5.
        
        Uses the MT5 API to fetch real historical OHLCV data.
        """
        try:
            import MetaTrader5 as mt5
            
            # Initialize MT5 if not already
            if not mt5.initialize():
                logger.warning("MT5 initialization failed, using sample data")
                return self._generate_sample_data(config)
            
            # Map timeframe string to MT5 constant
            tf_map = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1,
                "H4": mt5.TIMEFRAME_H4,
                "D1": mt5.TIMEFRAME_D1,
                "W1": mt5.TIMEFRAME_W1,
            }
            
            mt5_timeframe = tf_map.get(config.timeframe.upper())
            if mt5_timeframe is None:
                logger.warning(f"Unknown timeframe {config.timeframe}, using H1")
                mt5_timeframe = mt5.TIMEFRAME_H1
            
            # Fetch rates
            rates = mt5.copy_rates_range(
                config.symbol,
                mt5_timeframe,
                config.start_date,
                config.end_date
            )
            
            if rates is None or len(rates) == 0:
                logger.warning(f"No MT5 data for {config.symbol}, using sample data")
                return self._generate_sample_data(config)
            
            # Convert to DataFrame
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            
            # Rename columns to standard format
            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'tick_volume': 'volume'
            })
            
            # Keep only OHLCV columns
            df = df[['open', 'high', 'low', 'close', 'volume']]
            
            logger.info(f"Loaded {len(df)} bars from MT5 for {config.symbol} {config.timeframe}")
            return df
            
        except ImportError:
            logger.warning("MetaTrader5 package not installed, using sample data")
            return self._generate_sample_data(config)
        except Exception as e:
            logger.error(f"Error loading MT5 data: {e}, using sample data")
            return self._generate_sample_data(config)
    
    async def load_from_mt5_async(self, config: DataConfig) -> pd.DataFrame:
        """
        Async wrapper for MT5 data loading.
        
        Runs the synchronous MT5 call in a thread pool.
        """
        import asyncio
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._load_from_mt5, config)
    
    def download_and_save_mt5_data(
        self,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime
    ) -> bool:
        """
        Download data from MT5 and save to CSV for future use.
        
        Returns True if successful.
        """
        config = DataConfig(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            source="mt5"
        )
        
        df = self._load_from_mt5(config)
        
        if df is not None and len(df) > 0:
            self.save_to_csv(df, symbol, timeframe)
            return True
        
        return False
    
    def _generate_sample_data(self, config: DataConfig) -> pd.DataFrame:
        """
        Generate realistic sample OHLCV data for backtesting.
        
        Creates data with:
        - Realistic price movements
        - Trend and mean-reversion characteristics
        - Variable volatility
        """
        # Calculate number of bars needed
        tf_minutes = self._timeframe_to_minutes(config.timeframe)
        total_minutes = (config.end_date - config.start_date).total_seconds() / 60
        num_bars = int(total_minutes / tf_minutes)
        
        # Limit to reasonable size
        num_bars = min(num_bars, 50000)
        
        # Generate timestamps
        timestamps = pd.date_range(
            start=config.start_date,
            periods=num_bars,
            freq=f"{tf_minutes}min"
        )
        
        # Base price for different symbols
        base_prices = {
            "EURUSD": 1.0850,
            "GBPUSD": 1.2650,
            "USDJPY": 148.50,
            "USDCHF": 0.8850,
            "AUDUSD": 0.6550,
            "NZDUSD": 0.6050,
            "USDCAD": 1.3650,
            "XAUUSD": 2050.00,
        }
        
        base_price = base_prices.get(config.symbol.upper(), 1.0)
        pip_value = get_symbol_spec(config.symbol).pip_size
        
        # Generate price data with realistic characteristics
        np.random.seed(42)  # For reproducibility
        
        # Trend component (slow-moving)
        trend = np.cumsum(np.random.randn(num_bars) * 0.0001)
        
        # Mean-reverting component
        mean_revert = np.zeros(num_bars)
        for i in range(1, num_bars):
            mean_revert[i] = 0.95 * mean_revert[i-1] + np.random.randn() * 0.0005
        
        # Volatility clustering
        volatility = np.ones(num_bars)
        for i in range(1, num_bars):
            volatility[i] = 0.9 * volatility[i-1] + 0.1 * abs(np.random.randn())
        
        # Combine components
        returns = (trend + mean_revert) * volatility * pip_value * 10
        
        # Generate close prices
        close = base_price * (1 + np.cumsum(returns))
        
        # Generate OHLC from close
        data = []
        for i in range(num_bars):
            daily_vol = volatility[i] * pip_value * 50  # Typical daily range
            
            # Open is previous close (with small gap sometimes)
            open_price = close[i-1] if i > 0 else close[i]
            
            # High and Low
            high = close[i] + abs(np.random.randn()) * daily_vol
            low = close[i] - abs(np.random.randn()) * daily_vol
            
            # Ensure OHLC consistency
            high = max(high, open_price, close[i])
            low = min(low, open_price, close[i])
            
            # Volume
            volume = int(np.random.exponential(1000) * volatility[i])
            
            data.append({
                'open': open_price,
                'high': high,
                'low': low,
                'close': close[i],
                'volume': volume
            })
        
        df = pd.DataFrame(data, index=timestamps)
        df.index.name = 'time'
        
        logger.info(f"Generated {len(df)} bars of sample data for {config.symbol}")
        return df
    
    def _preprocess(self, df: pd.DataFrame, config: DataConfig) -> pd.DataFrame:
        """Preprocess data for backtesting."""
        # Ensure required columns exist
        required_cols = ['open', 'high', 'low', 'close']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")
        
        # Add volume if missing
        if 'volume' not in df.columns:
            df['volume'] = 0
        
        # Filter by date range
        if config.start_date:
            df = df[df.index >= config.start_date]
        if config.end_date:
            df = df[df.index <= config.end_date]
        
        # Sort by time
        df = df.sort_index()
        
        # Remove duplicates
        df = df[~df.index.duplicated(keep='first')]
        
        # Forward fill any missing values
        df = df.ffill()
        
        return df
    
    def _timeframe_to_minutes(self, timeframe: str) -> int:
        """Convert timeframe string to minutes."""
        tf_map = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440, "W1": 10080
        }
        return tf_map.get(timeframe.upper(), 60)
    
    def save_to_csv(self, df: pd.DataFrame, symbol: str, timeframe: str):
        """Save data to CSV file."""
        filename = f"{symbol}_{timeframe}.csv"
        filepath = self.data_dir / filename
        
        df.to_csv(filepath)
        logger.info(f"Saved data to {filepath}")
    
    def list_available_data(self) -> List[Dict[str, Any]]:
        """List available data files."""
        files = []
        for f in self.data_dir.glob("*.csv"):
            parts = f.stem.split("_")
            if len(parts) >= 2:
                files.append({
                    "symbol": parts[0],
                    "timeframe": parts[1],
                    "path": str(f),
                    "size": f.stat().st_size
                })
        return files
