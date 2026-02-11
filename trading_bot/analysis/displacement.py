"""
Displacement Candle Detection.

Identifies strong impulsive candles that indicate institutional commitment
and confirm the start of distribution phase moves.

A displacement candle is characterized by:
- Large body (significantly larger than average)
- Minimal wicks (closes near extreme)
- Creates FVG (fair value gap)
- Shows clear directional intent
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DisplacementCandle:
    """Represents a displacement candle."""
    index: int
    timestamp: Optional[datetime]
    direction: str  # 'bullish' or 'bearish'
    open_price: float
    high: float
    low: float
    close: float
    body_size: float
    body_percent: float  # Body as % of total range
    atr_multiple: float  # How many ATRs the candle is
    creates_fvg: bool
    fvg_size: Optional[float] = None
    strength: float = 0.0  # 0-1 score
    volume_confirmed: bool = False  # True when volume > 1.5x 20-bar average
    
    @property
    def is_strong(self) -> bool:
        """Check if this is a strong displacement."""
        return (
            self.atr_multiple >= 1.5 and
            self.body_percent >= 0.7 and
            self.creates_fvg
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "direction": self.direction,
            "open": self.open_price,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "body_size": self.body_size,
            "body_percent": self.body_percent,
            "atr_multiple": self.atr_multiple,
            "creates_fvg": self.creates_fvg,
            "fvg_size": self.fvg_size,
            "strength": self.strength,
            "is_strong": self.is_strong,
            "volume_confirmed": self.volume_confirmed
        }


@dataclass
class DisplacementAnalysis:
    """Complete displacement analysis result."""
    recent_displacements: List[DisplacementCandle]
    last_bullish: Optional[DisplacementCandle]
    last_bearish: Optional[DisplacementCandle]
    distribution_confirmed: bool
    distribution_direction: Optional[str]
    avg_atr: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "recent_displacements": [d.to_dict() for d in self.recent_displacements],
            "last_bullish": self.last_bullish.to_dict() if self.last_bullish else None,
            "last_bearish": self.last_bearish.to_dict() if self.last_bearish else None,
            "distribution_confirmed": self.distribution_confirmed,
            "distribution_direction": self.distribution_direction,
            "avg_atr": self.avg_atr
        }


class DisplacementDetector:
    """
    Detects displacement candles that indicate institutional commitment.
    
    Displacement is the KEY confirmation for entering distribution phase.
    Without displacement, the "move" hasn't started yet.
    
    Characteristics of displacement:
    1. Large body (1.5x+ ATR)
    2. Body is 70%+ of total candle range (minimal wicks)
    3. Creates a Fair Value Gap
    4. Occurs after manipulation (Judas swing)
    """
    
    def __init__(
        self,
        atr_period: int = 14,
        min_atr_multiple: float = 1.5,
        min_body_percent: float = 0.7,
        pip_value: float = 0.0001,
        lookback: int = 20
    ):
        """
        Initialize displacement detector.
        
        Args:
            atr_period: Period for ATR calculation
            min_atr_multiple: Minimum ATR multiple for displacement
            min_body_percent: Minimum body/range ratio
            pip_value: Pip value for the instrument
            lookback: Candles to look back for recent displacements
        """
        self.atr_period = atr_period
        self.min_atr_multiple = min_atr_multiple
        self.min_body_percent = min_body_percent
        self.pip_value = pip_value
        self.lookback = lookback
        
        logger.info("Displacement detector initialized")
    
    def detect(
        self,
        df: pd.DataFrame,
        expected_direction: Optional[str] = None
    ) -> DisplacementAnalysis:
        """
        Detect displacement candles in price data.
        
        Args:
            df: DataFrame with OHLCV data
            expected_direction: Expected move direction from AMD analysis
            
        Returns:
            DisplacementAnalysis with findings
        """
        # Exclude the current still-forming candle from analysis
        from . import exclude_forming_candle
        df = exclude_forming_candle(df)
        
        if len(df) < self.atr_period + 5:
            return DisplacementAnalysis(
                recent_displacements=[],
                last_bullish=None,
                last_bearish=None,
                distribution_confirmed=False,
                distribution_direction=None,
                avg_atr=0.0
            )
        
        # Calculate ATR
        atr = self._calculate_atr(df)
        avg_atr = atr.iloc[-self.lookback:].mean() if len(atr) > 0 else 0
        
        # Find displacement candles
        displacements = []
        recent_df = df.tail(self.lookback)
        
        for i, (idx, row) in enumerate(recent_df.iterrows()):
            if i < 2:  # Need prior candles for FVG check
                continue
            
            displacement = self._check_for_displacement(
                df, idx, row, atr, avg_atr
            )
            
            if displacement:
                displacements.append(displacement)
        
        # Sort by recency (most recent last)
        displacements.sort(key=lambda d: d.index)
        
        # Find last bullish/bearish
        last_bullish = None
        last_bearish = None
        
        for d in reversed(displacements):
            if d.direction == 'bullish' and not last_bullish:
                last_bullish = d
            elif d.direction == 'bearish' and not last_bearish:
                last_bearish = d
            if last_bullish and last_bearish:
                break
        
        # Check if distribution is confirmed
        distribution_confirmed = False
        distribution_direction = None
        
        if expected_direction:
            # Look for displacement in expected direction
            recent_strong = [d for d in displacements[-5:] if d.is_strong]
            for d in recent_strong:
                if expected_direction == 'bullish' and d.direction == 'bullish':
                    distribution_confirmed = True
                    distribution_direction = 'bullish'
                    break
                elif expected_direction == 'bearish' and d.direction == 'bearish':
                    distribution_confirmed = True
                    distribution_direction = 'bearish'
                    break
        elif displacements:
            # Use most recent strong displacement
            recent_strong = [d for d in displacements[-3:] if d.is_strong]
            if recent_strong:
                distribution_confirmed = True
                distribution_direction = recent_strong[-1].direction
        
        return DisplacementAnalysis(
            recent_displacements=displacements,
            last_bullish=last_bullish,
            last_bearish=last_bearish,
            distribution_confirmed=distribution_confirmed,
            distribution_direction=distribution_direction,
            avg_atr=avg_atr
        )
    
    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Calculate Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=self.atr_period).mean()
        
        return atr
    
    def _check_for_displacement(
        self,
        df: pd.DataFrame,
        idx: int,
        row: pd.Series,
        atr: pd.Series,
        avg_atr: float
    ) -> Optional[DisplacementCandle]:
        """Check if a candle is a displacement candle."""
        if avg_atr == 0:
            return None
        
        # Calculate body size
        body_size = abs(row['close'] - row['open'])
        range_size = row['high'] - row['low']
        
        if range_size == 0:
            return None
        
        # Body percentage
        body_percent = body_size / range_size
        
        # ATR multiple
        current_atr = atr.loc[idx] if idx in atr.index else avg_atr
        atr_multiple = body_size / current_atr if current_atr > 0 else 0
        
        # Direction
        direction = 'bullish' if row['close'] > row['open'] else 'bearish'
        
        # Check FVG creation
        creates_fvg, fvg_size = self._check_fvg_creation(df, idx, direction)
        
        # Must meet minimum criteria
        if atr_multiple < self.min_atr_multiple * 0.8:  # Allow some flexibility
            return None
        
        if body_percent < self.min_body_percent * 0.8:
            return None
        
        # Check volume confirmation
        volume_confirmed = False
        if 'volume' in df.columns:
            try:
                pos = idx if isinstance(idx, int) else df.index.get_loc(idx)
                candle_vol = float(df['volume'].iloc[pos])
                # 20-bar rolling average volume up to (but not including) this candle
                start = max(0, pos - 20)
                avg_vol = float(df['volume'].iloc[start:pos].mean()) if pos > 0 else 0.0
                if avg_vol > 0 and candle_vol > avg_vol * 1.5:
                    volume_confirmed = True
            except Exception:
                pass
        
        # Calculate strength score
        strength = self._calculate_strength(
            body_percent, atr_multiple, creates_fvg, fvg_size, volume_confirmed
        )
        
        timestamp = None
        if 'time' in df.columns:
            timestamp = df.loc[idx, 'time']
        
        return DisplacementCandle(
            index=idx if isinstance(idx, int) else df.index.get_loc(idx),
            timestamp=timestamp,
            direction=direction,
            open_price=row['open'],
            high=row['high'],
            low=row['low'],
            close=row['close'],
            body_size=body_size,
            body_percent=body_percent,
            atr_multiple=atr_multiple,
            creates_fvg=creates_fvg,
            fvg_size=fvg_size,
            strength=strength,
            volume_confirmed=volume_confirmed
        )
    
    def _check_fvg_creation(
        self,
        df: pd.DataFrame,
        idx: int,
        direction: str
    ) -> Tuple[bool, Optional[float]]:
        """Check if the displacement creates a Fair Value Gap."""
        try:
            # Get position in dataframe
            if isinstance(idx, int):
                pos = idx
            else:
                pos = df.index.get_loc(idx)
            
            if pos < 1 or pos >= len(df) - 1:
                return False, None
            
            prev_candle = df.iloc[pos - 1]
            curr_candle = df.iloc[pos]
            next_candle = df.iloc[pos + 1] if pos + 1 < len(df) else None
            
            if next_candle is None:
                # Can't confirm FVG without next candle
                # Use current range vs prior as proxy
                if direction == 'bullish':
                    gap = curr_candle['low'] - prev_candle['high']
                else:
                    gap = prev_candle['low'] - curr_candle['high']
                
                if gap > 0:
                    return True, gap / self.pip_value
                return False, None
            
            # Check for proper FVG
            if direction == 'bullish':
                # Bullish FVG: prior high < next low
                gap = next_candle['low'] - prev_candle['high']
            else:
                # Bearish FVG: prior low > next high
                gap = prev_candle['low'] - next_candle['high']
            
            if gap > 0:
                return True, gap / self.pip_value
            
            return False, None
            
        except Exception as e:
            logger.debug(f"FVG check error: {e}")
            return False, None
    
    def _calculate_strength(
        self,
        body_percent: float,
        atr_multiple: float,
        creates_fvg: bool,
        fvg_size: Optional[float],
        volume_confirmed: bool = False
    ) -> float:
        """Calculate overall displacement strength (0-1)."""
        strength = 0.0
        
        # Body percentage contribution (max 0.3)
        strength += min(0.3, (body_percent - 0.5) * 0.6)
        
        # ATR multiple contribution (max 0.4)
        strength += min(0.4, (atr_multiple - 1.0) * 0.2)
        
        # FVG contribution (0.3)
        if creates_fvg:
            strength += 0.2
            if fvg_size and fvg_size > 10:  # 10+ pip FVG
                strength += min(0.1, fvg_size / 100)
        
        # Volume confirmation contribution (up to +0.15)
        if volume_confirmed:
            strength += 0.15
        
        return min(1.0, max(0.0, strength))
    
    def is_distribution_starting(
        self,
        df: pd.DataFrame,
        amd_phase: str,
        expected_direction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check if distribution phase is starting with displacement.
        
        This is THE key function for timing entries.
        
        Args:
            df: Price data
            amd_phase: Current AMD phase
            expected_direction: Expected direction from Judas swing
            
        Returns:
            Dictionary with confirmation status and details
        """
        analysis = self.detect(df, expected_direction)
        
        result = {
            "confirmed": False,
            "direction": None,
            "displacement": None,
            "reason": "No displacement detected",
            "ready_to_trade": False
        }
        
        # Need to be in manipulation or early distribution phase
        if amd_phase not in ['manipulation', 'distribution']:
            result["reason"] = f"Not in tradeable phase: {amd_phase}"
            return result
        
        # Check for recent strong displacement
        if not analysis.distribution_confirmed:
            result["reason"] = "Waiting for displacement candle"
            return result
        
        # Get the confirming displacement
        if expected_direction == 'bullish' and analysis.last_bullish:
            disp = analysis.last_bullish
        elif expected_direction == 'bearish' and analysis.last_bearish:
            disp = analysis.last_bearish
        elif analysis.recent_displacements:
            disp = analysis.recent_displacements[-1]
        else:
            result["reason"] = "No recent displacement"
            return result
        
        # Must be strong
        if not disp.is_strong:
            result["reason"] = f"Displacement not strong enough: ATR={disp.atr_multiple:.1f}x, Body={disp.body_percent:.0%}"
            return result
        
        # Must create FVG
        if not disp.creates_fvg:
            result["reason"] = "Displacement didn't create FVG"
            return result
        
        # All checks passed!
        result["confirmed"] = True
        result["direction"] = disp.direction
        result["displacement"] = disp.to_dict()
        result["reason"] = f"Strong {disp.direction} displacement with FVG"
        result["ready_to_trade"] = True
        
        return result


def detect_displacement(
    df: pd.DataFrame,
    expected_direction: Optional[str] = None,
    pip_value: float = 0.0001
) -> Dict[str, Any]:
    """
    Convenience function to detect displacement.
    
    Args:
        df: DataFrame with OHLCV data
        expected_direction: Expected direction from AMD/Judas analysis
        pip_value: Pip value for the instrument
        
    Returns:
        Displacement analysis results
    """
    detector = DisplacementDetector(pip_value=pip_value)
    analysis = detector.detect(df, expected_direction)
    return analysis.to_dict()
