"""
ICT Silver Bullet Detector.

Identifies Silver Bullet setups during specific time windows
where displacement creates Fair Value Gaps in the direction of bias.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, time
from dataclasses import dataclass
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SilverBulletSetup:
    """Represents a Silver Bullet trading setup."""
    window: str  # 'london', 'ny_am', 'ny_pm'
    direction: str  # 'bullish' or 'bearish'
    displacement_start: int  # Bar index where displacement started
    displacement_end: int  # Bar index where displacement ended
    fvg_high: float
    fvg_low: float
    fvg_50_percent: float
    entry_price: float
    stop_loss: float
    strength: float  # 0-1 confidence score
    timestamp: datetime


class SilverBulletDetector:
    """
    Detects ICT Silver Bullet setups.
    
    Silver Bullet windows (EST):
    - London: 3:00 AM - 4:00 AM
    - NY AM: 10:00 AM - 11:00 AM
    - NY PM: 2:00 PM - 3:00 PM
    """
    
    # Silver Bullet time windows (EST)
    SILVER_BULLET_WINDOWS = {
        'london': (time(3, 0), time(4, 0)),
        'ny_am': (time(10, 0), time(11, 0)),
        'ny_pm': (time(14, 0), time(15, 0))
    }
    
    def __init__(
        self,
        min_displacement_candles: int = 3,
        min_fvg_pips: float = 5.0,
        pip_value: float = 0.0001
    ):
        """
        Initialize Silver Bullet detector.
        
        Args:
            min_displacement_candles: Minimum candles for displacement
            min_fvg_pips: Minimum FVG size in pips
            pip_value: Pip value for the instrument
        """
        self.min_displacement_candles = min_displacement_candles
        self.min_fvg_pips = min_fvg_pips
        self.pip_value = pip_value
    
    def detect(
        self,
        df: pd.DataFrame,
        bias: str = 'neutral',
        current_time: Optional[datetime] = None
    ) -> List[SilverBulletSetup]:
        """
        Detect Silver Bullet setups in the data.
        
        Args:
            df: DataFrame with OHLCV data
            bias: Daily bias ('bullish', 'bearish', 'neutral')
            current_time: Current time for live detection
            
        Returns:
            List of SilverBulletSetup objects
        """
        setups = []
        
        if len(df) < self.min_displacement_candles + 3:
            return setups
        
        # Get the active Silver Bullet window
        active_window = self._get_active_window(df, current_time)
        
        if not active_window:
            return setups
        
        # Find displacements during the window
        displacements = self._find_displacements(df, active_window, bias)
        
        for disp in displacements:
            # Find FVG created by displacement
            fvg = self._find_fvg_from_displacement(df, disp)
            
            if fvg:
                setup = self._create_setup(df, active_window, disp, fvg, bias)
                if setup:
                    setups.append(setup)
        
        return setups
    
    def _get_active_window(
        self,
        df: pd.DataFrame,
        current_time: Optional[datetime]
    ) -> Optional[str]:
        """Determine which Silver Bullet window is active."""
        if current_time is None:
            # Use the last bar's time
            if 'time' in df.columns:
                current_time = pd.to_datetime(df['time'].iloc[-1])
            else:
                current_time = datetime.now()
        
        current_time_only = current_time.time() if isinstance(current_time, datetime) else current_time
        
        for window_name, (start, end) in self.SILVER_BULLET_WINDOWS.items():
            if start <= current_time_only <= end:
                return window_name
        
        return None
    
    def _find_displacements(
        self,
        df: pd.DataFrame,
        window: str,
        bias: str
    ) -> List[Dict[str, Any]]:
        """Find displacement moves during the Silver Bullet window."""
        displacements = []
        
        # Look for consecutive candles in the same direction
        for i in range(self.min_displacement_candles, len(df)):
            start_idx = i - self.min_displacement_candles
            
            # Check for bullish displacement
            if bias in ['bullish', 'neutral']:
                bullish_count = 0
                total_move = 0
                
                for j in range(start_idx, i):
                    if df['close'].iloc[j] > df['open'].iloc[j]:
                        bullish_count += 1
                        total_move += df['close'].iloc[j] - df['open'].iloc[j]
                
                if bullish_count >= self.min_displacement_candles - 1:
                    displacements.append({
                        'direction': 'bullish',
                        'start_idx': start_idx,
                        'end_idx': i - 1,
                        'strength': total_move / self.pip_value
                    })
            
            # Check for bearish displacement
            if bias in ['bearish', 'neutral']:
                bearish_count = 0
                total_move = 0
                
                for j in range(start_idx, i):
                    if df['close'].iloc[j] < df['open'].iloc[j]:
                        bearish_count += 1
                        total_move += df['open'].iloc[j] - df['close'].iloc[j]
                
                if bearish_count >= self.min_displacement_candles - 1:
                    displacements.append({
                        'direction': 'bearish',
                        'start_idx': start_idx,
                        'end_idx': i - 1,
                        'strength': total_move / self.pip_value
                    })
        
        return displacements
    
    def _find_fvg_from_displacement(
        self,
        df: pd.DataFrame,
        displacement: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Find FVG created by the displacement."""
        start_idx = displacement['start_idx']
        end_idx = displacement['end_idx']
        
        # Need at least 3 candles for FVG
        if end_idx - start_idx < 2:
            return None
        
        # Check for FVG in the displacement area
        for i in range(start_idx + 1, end_idx):
            candle_1 = df.iloc[i - 1]
            candle_2 = df.iloc[i]  # Middle candle
            candle_3 = df.iloc[i + 1] if i + 1 < len(df) else None
            
            if candle_3 is None:
                continue
            
            if displacement['direction'] == 'bullish':
                # Bullish FVG: Gap between candle 1 high and candle 3 low
                gap_high = candle_3['low']
                gap_low = candle_1['high']
                
                if gap_high > gap_low:
                    gap_size = (gap_high - gap_low) / self.pip_value
                    
                    if gap_size >= self.min_fvg_pips:
                        return {
                            'high': gap_high,
                            'low': gap_low,
                            'mid': (gap_high + gap_low) / 2,
                            'size_pips': gap_size,
                            'candle_idx': i
                        }
            
            else:  # Bearish
                # Bearish FVG: Gap between candle 1 low and candle 3 high
                gap_high = candle_1['low']
                gap_low = candle_3['high']
                
                if gap_high > gap_low:
                    gap_size = (gap_high - gap_low) / self.pip_value
                    
                    if gap_size >= self.min_fvg_pips:
                        return {
                            'high': gap_high,
                            'low': gap_low,
                            'mid': (gap_high + gap_low) / 2,
                            'size_pips': gap_size,
                            'candle_idx': i
                        }
        
        return None
    
    def _create_setup(
        self,
        df: pd.DataFrame,
        window: str,
        displacement: Dict[str, Any],
        fvg: Dict[str, Any],
        bias: str
    ) -> Optional[SilverBulletSetup]:
        """Create a SilverBulletSetup from components."""
        try:
            direction = displacement['direction']
            
            # Calculate entry and stop
            entry_price = fvg['mid']  # 50% of FVG
            
            if direction == 'bullish':
                stop_loss = fvg['low'] - (5 * self.pip_value)  # 5 pip buffer
            else:
                stop_loss = fvg['high'] + (5 * self.pip_value)
            
            # Calculate strength based on displacement and FVG size
            strength = min(1.0, (displacement['strength'] / 50) * (fvg['size_pips'] / 10))
            
            # Increase strength if bias aligns
            if bias == direction:
                strength = min(1.0, strength * 1.2)
            
            timestamp = datetime.now()
            if 'time' in df.columns:
                timestamp = pd.to_datetime(df['time'].iloc[fvg['candle_idx']])
            
            return SilverBulletSetup(
                window=window,
                direction=direction,
                displacement_start=displacement['start_idx'],
                displacement_end=displacement['end_idx'],
                fvg_high=fvg['high'],
                fvg_low=fvg['low'],
                fvg_50_percent=fvg['mid'],
                entry_price=entry_price,
                stop_loss=stop_loss,
                strength=strength,
                timestamp=timestamp
            )
            
        except Exception as e:
            logger.error(f"Error creating Silver Bullet setup: {e}")
            return None
    
    def is_in_silver_bullet_window(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Check if current time is in a Silver Bullet window.
        
        Returns:
            Dict with 'active', 'window', 'time_remaining'
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_time_only = current_time.time()
        
        for window_name, (start, end) in self.SILVER_BULLET_WINDOWS.items():
            if start <= current_time_only <= end:
                # Calculate time remaining
                end_datetime = datetime.combine(current_time.date(), end)
                remaining = (end_datetime - current_time).total_seconds() / 60
                
                return {
                    'active': True,
                    'window': window_name,
                    'time_remaining_minutes': remaining
                }
        
        # Find next window
        next_window = None
        min_wait = float('inf')
        
        for window_name, (start, end) in self.SILVER_BULLET_WINDOWS.items():
            start_datetime = datetime.combine(current_time.date(), start)
            
            if start_datetime > current_time:
                wait_time = (start_datetime - current_time).total_seconds() / 60
                if wait_time < min_wait:
                    min_wait = wait_time
                    next_window = window_name
        
        return {
            'active': False,
            'next_window': next_window,
            'minutes_until_next': min_wait if min_wait != float('inf') else None
        }


def detect_silver_bullets(
    df: pd.DataFrame,
    bias: str = 'neutral',
    pip_value: float = 0.0001
) -> List[Dict[str, Any]]:
    """
    Convenience function to detect Silver Bullet setups.
    
    Args:
        df: DataFrame with OHLCV data
        bias: Daily bias
        pip_value: Pip value for the instrument
        
    Returns:
        List of setup dictionaries
    """
    detector = SilverBulletDetector(pip_value=pip_value)
    setups = detector.detect(df, bias)
    
    return [
        {
            'window': s.window,
            'direction': s.direction,
            'fvg_high': s.fvg_high,
            'fvg_low': s.fvg_low,
            'entry': s.entry_price,
            'stop': s.stop_loss,
            'strength': s.strength,
            'timestamp': s.timestamp.isoformat()
        }
        for s in setups
    ]
