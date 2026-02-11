"""
Fair Value Gap (FVG) Detection Module.

Implements FVG identification according to ICT methodology:
- Three-candle pattern detection
- Bullish and bearish FVG classification
- Mitigation tracking
- FVG validation and filtering
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


class FVGType(Enum):
    """Types of Fair Value Gaps."""
    BULLISH = "bullish"    # Gap created during bullish move (potential support)
    BEARISH = "bearish"    # Gap created during bearish move (potential resistance)


class FVGStatus(Enum):
    """Status of an FVG."""
    UNFILLED = "unfilled"      # FVG has not been tested
    PARTIALLY_FILLED = "partially_filled"  # Price has entered but not fully filled
    FILLED = "filled"          # FVG has been completely filled (mitigated)


@dataclass
class FairValueGap:
    """
    Represents a Fair Value Gap.
    
    An FVG is an imbalance zone where price moved so aggressively
    that a gap was left in the price action. The market often
    returns to these zones to "fill" the gap.
    """
    type: FVGType
    index: int                    # Index of the middle candle (impulse candle)
    top: float                    # Upper boundary of the gap
    bottom: float                 # Lower boundary of the gap
    status: FVGStatus = FVGStatus.UNFILLED
    timestamp: Optional[pd.Timestamp] = None
    
    # Candle information
    candle1_high: float = 0.0     # First candle high
    candle1_low: float = 0.0      # First candle low
    candle3_high: float = 0.0     # Third candle high
    candle3_low: float = 0.0      # Third candle low
    
    # Quality metrics
    gap_size: float = 0.0         # Size of the gap in price
    body_percentage: float = 0.0  # Body % of the impulse candle
    
    @property
    def midpoint(self) -> float:
        """Get the midpoint of the FVG zone."""
        return (self.top + self.bottom) / 2
    
    @property
    def is_valid(self) -> bool:
        """Check if FVG is still valid (unfilled)."""
        return self.status == FVGStatus.UNFILLED
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "index": int(self.index),
            "top": float(self.top),
            "bottom": float(self.bottom),
            "midpoint": float(self.midpoint),
            "gap_size": float(self.gap_size),
            "status": self.status.value,
            "body_percentage": float(self.body_percentage)
        }


@dataclass
class FVGAnalysis:
    """Complete FVG analysis result."""
    bullish_fvgs: List[FairValueGap] = field(default_factory=list)
    bearish_fvgs: List[FairValueGap] = field(default_factory=list)
    active_fvgs: List[FairValueGap] = field(default_factory=list)  # Unfilled FVGs
    recent_fvg: Optional[FairValueGap] = None
    
    @property
    def total_fvgs(self) -> int:
        return len(self.bullish_fvgs) + len(self.bearish_fvgs)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "bullish_fvgs": [fvg.to_dict() for fvg in self.bullish_fvgs],
            "bearish_fvgs": [fvg.to_dict() for fvg in self.bearish_fvgs],
            "active_fvgs": [fvg.to_dict() for fvg in self.active_fvgs],
            "total_fvgs": self.total_fvgs,
            "recent_fvg": self.recent_fvg.to_dict() if self.recent_fvg else None
        }


class FVGDetector:
    """
    Detects Fair Value Gaps in price data.
    
    An FVG forms when the wicks of candles 1 and 3 don't overlap,
    creating a "gap" in the price action around the impulse candle (candle 2).
    
    Bullish FVG: Candle 1's high < Candle 3's low (gap below impulse)
    Bearish FVG: Candle 1's low > Candle 3's high (gap above impulse)
    """
    
    def __init__(
        self,
        min_gap_pips: float = 3.0,
        min_body_percentage: float = 0.5,
        pip_value: float = 0.0001,
        check_mitigation: bool = True
    ):
        """
        Initialize the FVG detector.
        
        Args:
            min_gap_pips: Minimum gap size in pips to be considered valid
            min_body_percentage: Minimum body % of impulse candle (0-1)
            pip_value: Value of one pip for the trading symbol
            check_mitigation: Whether to check if FVGs have been filled
        """
        self.min_gap_pips = min_gap_pips
        self.min_body_percentage = min_body_percentage
        self.pip_value = pip_value
        self.check_mitigation = check_mitigation
    
    def detect(self, df: pd.DataFrame) -> FVGAnalysis:
        """
        Detect all FVGs in the price data.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            FVGAnalysis object with all detected FVGs
        """
        logger.debug(f"Detecting FVGs in {len(df)} candles")
        
        bullish_fvgs = []
        bearish_fvgs = []
        
        min_gap_size = self.min_gap_pips * self.pip_value
        
        # Need at least 3 candles
        if len(df) < 3:
            return FVGAnalysis()
        
        # Iterate through candles looking for FVG patterns
        for i in range(1, len(df) - 1):
            candle1 = df.iloc[i - 1]  # First candle
            candle2 = df.iloc[i]      # Middle (impulse) candle
            candle3 = df.iloc[i + 1]  # Third candle
            
            # Check for bullish FVG: candle1 high < candle3 low
            if candle1['high'] < candle3['low']:
                gap_bottom = candle1['high']
                gap_top = candle3['low']
                gap_size = gap_top - gap_bottom
                
                if gap_size >= min_gap_size:
                    body_pct = self._calculate_body_percentage(candle2)
                    
                    if body_pct >= self.min_body_percentage:
                        timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                        
                        fvg = FairValueGap(
                            type=FVGType.BULLISH,
                            index=i,
                            top=gap_top,
                            bottom=gap_bottom,
                            timestamp=timestamp,
                            candle1_high=candle1['high'],
                            candle1_low=candle1['low'],
                            candle3_high=candle3['high'],
                            candle3_low=candle3['low'],
                            gap_size=gap_size,
                            body_percentage=body_pct
                        )
                        bullish_fvgs.append(fvg)
            
            # Check for bearish FVG: candle1 low > candle3 high
            if candle1['low'] > candle3['high']:
                gap_top = candle1['low']
                gap_bottom = candle3['high']
                gap_size = gap_top - gap_bottom
                
                if gap_size >= min_gap_size:
                    body_pct = self._calculate_body_percentage(candle2)
                    
                    if body_pct >= self.min_body_percentage:
                        timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                        
                        fvg = FairValueGap(
                            type=FVGType.BEARISH,
                            index=i,
                            top=gap_top,
                            bottom=gap_bottom,
                            timestamp=timestamp,
                            candle1_high=candle1['high'],
                            candle1_low=candle1['low'],
                            candle3_high=candle3['high'],
                            candle3_low=candle3['low'],
                            gap_size=gap_size,
                            body_percentage=body_pct
                        )
                        bearish_fvgs.append(fvg)
        
        # Check mitigation status if enabled
        if self.check_mitigation:
            bullish_fvgs = self._update_mitigation_status(bullish_fvgs, df)
            bearish_fvgs = self._update_mitigation_status(bearish_fvgs, df)
        
        # Collect active (unfilled) FVGs
        active_fvgs = [fvg for fvg in bullish_fvgs + bearish_fvgs if fvg.status == FVGStatus.UNFILLED]
        
        # Get most recent FVG
        all_fvgs = sorted(bullish_fvgs + bearish_fvgs, key=lambda x: x.index)
        recent_fvg = all_fvgs[-1] if all_fvgs else None
        
        logger.info(f"FVG detection complete: {len(bullish_fvgs)} bullish, {len(bearish_fvgs)} bearish, {len(active_fvgs)} active")
        
        return FVGAnalysis(
            bullish_fvgs=bullish_fvgs,
            bearish_fvgs=bearish_fvgs,
            active_fvgs=active_fvgs,
            recent_fvg=recent_fvg
        )
    
    def _calculate_body_percentage(self, candle: pd.Series) -> float:
        """Calculate the body size as percentage of candle range."""
        candle_range = candle['high'] - candle['low']
        if candle_range == 0:
            return 0.0
        
        body_size = abs(candle['close'] - candle['open'])
        return body_size / candle_range
    
    def _update_mitigation_status(
        self,
        fvgs: List[FairValueGap],
        df: pd.DataFrame
    ) -> List[FairValueGap]:
        """
        Update the mitigation status of FVGs based on subsequent price action.
        
        Mitigation methods:
        - Close method: Price closes within/beyond the FVG
        - Wick method: Price wicks into the FVG
        - 50% method: Price reaches the midpoint of the FVG
        """
        for fvg in fvgs:
            # Look at candles after the FVG formed
            subsequent_df = df.iloc[fvg.index + 2:]  # Start after candle 3
            
            for _, candle in subsequent_df.iterrows():
                if fvg.type == FVGType.BULLISH:
                    # Bullish FVG is filled when price drops into the zone
                    if candle['low'] <= fvg.top:
                        if candle['low'] <= fvg.bottom:
                            fvg.status = FVGStatus.FILLED
                        else:
                            if fvg.status == FVGStatus.UNFILLED:
                                fvg.status = FVGStatus.PARTIALLY_FILLED
                else:
                    # Bearish FVG is filled when price rises into the zone
                    if candle['high'] >= fvg.bottom:
                        if candle['high'] >= fvg.top:
                            fvg.status = FVGStatus.FILLED
                        else:
                            if fvg.status == FVGStatus.UNFILLED:
                                fvg.status = FVGStatus.PARTIALLY_FILLED
                
                # If filled, no need to check further
                if fvg.status == FVGStatus.FILLED:
                    break
        
        return fvgs
    
    def find_nearest_fvg(
        self,
        fvgs: List[FairValueGap],
        current_price: float,
        fvg_type: Optional[FVGType] = None
    ) -> Optional[FairValueGap]:
        """
        Find the nearest unfilled FVG to the current price.
        
        Args:
            fvgs: List of FVGs to search
            current_price: Current market price
            fvg_type: Optional filter for bullish/bearish
            
        Returns:
            Nearest unfilled FVG or None
        """
        valid_fvgs = [fvg for fvg in fvgs if fvg.status == FVGStatus.UNFILLED]
        
        if fvg_type:
            valid_fvgs = [fvg for fvg in valid_fvgs if fvg.type == fvg_type]
        
        if not valid_fvgs:
            return None
        
        # Calculate distance to midpoint
        def distance(fvg):
            return abs(current_price - fvg.midpoint)
        
        return min(valid_fvgs, key=distance)
    
    def get_fvgs_in_range(
        self,
        fvgs: List[FairValueGap],
        price_low: float,
        price_high: float
    ) -> List[FairValueGap]:
        """
        Get all FVGs within a price range.
        
        Args:
            fvgs: List of FVGs to search
            price_low: Lower bound of price range
            price_high: Upper bound of price range
            
        Returns:
            List of FVGs within the range
        """
        result = []
        for fvg in fvgs:
            # FVG overlaps with range if not completely above or below
            if not (fvg.bottom > price_high or fvg.top < price_low):
                result.append(fvg)
        return result
