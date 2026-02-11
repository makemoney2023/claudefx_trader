"""
Market Structure Analysis Module.

Implements ICT market structure concepts including:
- Break of Structure (BOS)
- Change of Character (CHoCH)
- Market Structure Shift (MSS)
- Swing high/low identification
- Trend direction analysis
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

from ..utils.candle_utils import find_swing_highs, find_swing_lows
from ..utils.logging import get_logger

logger = get_logger(__name__)


class StructureType(Enum):
    """Types of market structure events."""
    BOS_BULLISH = "bos_bullish"      # Break of Structure (bullish continuation)
    BOS_BEARISH = "bos_bearish"      # Break of Structure (bearish continuation)
    CHOCH_BULLISH = "choch_bullish"  # Change of Character (bearish to bullish)
    CHOCH_BEARISH = "choch_bearish"  # Change of Character (bullish to bearish)
    MSS_BULLISH = "mss_bullish"      # Market Structure Shift (strong bullish)
    MSS_BEARISH = "mss_bearish"      # Market Structure Shift (strong bearish)


class TrendDirection(Enum):
    """Market trend direction."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


@dataclass
class SwingPoint:
    """Represents a swing high or low point."""
    index: int
    price: float
    is_high: bool
    timestamp: Optional[pd.Timestamp] = None
    
    @property
    def type_str(self) -> str:
        return "high" if self.is_high else "low"


@dataclass
class MarketStructure:
    """Represents a market structure event."""
    type: StructureType
    index: int
    price: float
    broken_level: float
    timestamp: Optional[pd.Timestamp] = None
    significance: float = 1.0  # 0-1 scale of how significant this break is
    
    @property
    def is_bullish(self) -> bool:
        return self.type in [
            StructureType.BOS_BULLISH,
            StructureType.CHOCH_BULLISH,
            StructureType.MSS_BULLISH
        ]
    
    @property
    def is_bearish(self) -> bool:
        return self.type in [
            StructureType.BOS_BEARISH,
            StructureType.CHOCH_BEARISH,
            StructureType.MSS_BEARISH
        ]


@dataclass
class StructureAnalysis:
    """Complete market structure analysis result."""
    trend: TrendDirection
    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows: List[SwingPoint] = field(default_factory=list)
    structure_breaks: List[MarketStructure] = field(default_factory=list)
    last_structure: Optional[MarketStructure] = None
    higher_high: Optional[float] = None
    higher_low: Optional[float] = None
    lower_high: Optional[float] = None
    lower_low: Optional[float] = None
    
    def to_dict(self) -> dict:
        """Convert analysis to dictionary for JSON serialization."""
        return {
            "trend": self.trend.value,
            "swing_highs": [{"index": int(s.index), "price": float(s.price)} for s in self.swing_highs],
            "swing_lows": [{"index": int(s.index), "price": float(s.price)} for s in self.swing_lows],
            "structure_breaks": [
                {
                    "type": s.type.value,
                    "index": int(s.index),
                    "price": float(s.price),
                    "broken_level": float(s.broken_level),
                    "significance": float(s.significance)
                }
                for s in self.structure_breaks
            ],
            "last_structure": {
                "type": self.last_structure.type.value,
                "price": float(self.last_structure.price)
            } if self.last_structure else None
        }


class MarketStructureAnalyzer:
    """
    Analyzes market structure according to ICT methodology.
    
    Identifies swing points, trend direction, and structure breaks
    (BOS, CHoCH, MSS) to determine market bias.
    """
    
    def __init__(
        self,
        swing_lookback: int = 5,
        min_swing_bars: int = 3,
        mss_displacement_factor: float = 1.5
    ):
        """
        Initialize the market structure analyzer.
        
        Args:
            swing_lookback: Number of bars to look back/forward for swing detection
            min_swing_bars: Minimum bars between swing points
            mss_displacement_factor: ATR multiplier to qualify as MSS (strong break)
        """
        self.swing_lookback = swing_lookback
        self.min_swing_bars = min_swing_bars
        self.mss_displacement_factor = mss_displacement_factor
    
    def analyze(self, df: pd.DataFrame) -> StructureAnalysis:
        """
        Perform complete market structure analysis.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            StructureAnalysis object with all findings
        """
        logger.debug(f"Analyzing market structure for {len(df)} candles")
        
        # Find swing points
        swing_highs = self._find_swing_highs(df)
        swing_lows = self._find_swing_lows(df)
        
        # Identify structure breaks
        structure_breaks = self._identify_structure_breaks(df, swing_highs, swing_lows)
        
        # Determine trend
        trend = self._determine_trend(swing_highs, swing_lows, structure_breaks)
        
        # Get key levels
        hh, hl, lh, ll = self._get_key_levels(swing_highs, swing_lows)
        
        analysis = StructureAnalysis(
            trend=trend,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            structure_breaks=structure_breaks,
            last_structure=structure_breaks[-1] if structure_breaks else None,
            higher_high=hh,
            higher_low=hl,
            lower_high=lh,
            lower_low=ll
        )
        
        logger.info(f"Market structure analysis complete: Trend={trend.value}, Breaks={len(structure_breaks)}")
        return analysis
    
    def _find_swing_highs(self, df: pd.DataFrame) -> List[SwingPoint]:
        """Find all swing high points."""
        raw_swings = find_swing_highs(df, self.swing_lookback, self.swing_lookback)
        
        swing_points = []
        for idx, price in raw_swings:
            timestamp = df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None
            swing_points.append(SwingPoint(
                index=idx,
                price=price,
                is_high=True,
                timestamp=timestamp
            ))
        
        return swing_points
    
    def _find_swing_lows(self, df: pd.DataFrame) -> List[SwingPoint]:
        """Find all swing low points."""
        raw_swings = find_swing_lows(df, self.swing_lookback, self.swing_lookback)
        
        swing_points = []
        for idx, price in raw_swings:
            timestamp = df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None
            swing_points.append(SwingPoint(
                index=idx,
                price=price,
                is_high=False,
                timestamp=timestamp
            ))
        
        return swing_points
    
    def _identify_structure_breaks(
        self,
        df: pd.DataFrame,
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint]
    ) -> List[MarketStructure]:
        """
        Identify all structure breaks (BOS, CHoCH, MSS).
        
        Logic:
        - BOS: Price breaks a swing point in the direction of the trend
        - CHoCH: Price breaks a swing point against the prior trend (first sign of reversal)
        - MSS: Strong displacement through a structure level (often with FVG)
        """
        structure_breaks = []
        
        # Combine and sort swing points by index
        all_swings = sorted(swing_highs + swing_lows, key=lambda x: x.index)
        
        if len(all_swings) < 3:
            return structure_breaks
        
        # Calculate ATR for MSS detection
        atr = self._calculate_simple_atr(df)
        
        # Track the current implied trend
        current_trend = None
        last_broken_high = None
        last_broken_low = None
        
        for i, swing in enumerate(all_swings[:-1]):
            # Look for price action after this swing that breaks it
            break_info = self._check_for_break(df, swing, all_swings, i, atr)
            
            if break_info:
                break_idx, break_price, is_strong = break_info
                
                # Determine the type of break
                if swing.is_high:
                    # Breaking above a swing high
                    if current_trend == TrendDirection.BULLISH:
                        struct_type = StructureType.MSS_BULLISH if is_strong else StructureType.BOS_BULLISH
                    else:
                        struct_type = StructureType.MSS_BULLISH if is_strong else StructureType.CHOCH_BULLISH
                        current_trend = TrendDirection.BULLISH
                    last_broken_high = swing.price
                else:
                    # Breaking below a swing low
                    if current_trend == TrendDirection.BEARISH:
                        struct_type = StructureType.MSS_BEARISH if is_strong else StructureType.BOS_BEARISH
                    else:
                        struct_type = StructureType.MSS_BEARISH if is_strong else StructureType.CHOCH_BEARISH
                        current_trend = TrendDirection.BEARISH
                    last_broken_low = swing.price
                
                timestamp = df.index[break_idx] if isinstance(df.index, pd.DatetimeIndex) else None
                
                structure_breaks.append(MarketStructure(
                    type=struct_type,
                    index=break_idx,
                    price=break_price,
                    broken_level=swing.price,
                    timestamp=timestamp,
                    significance=1.0 if is_strong else 0.7
                ))
        
        return structure_breaks
    
    def _check_for_break(
        self,
        df: pd.DataFrame,
        swing: SwingPoint,
        all_swings: List[SwingPoint],
        swing_index: int,
        atr: pd.Series
    ) -> Optional[Tuple[int, float, bool]]:
        """
        Check if a swing point gets broken by subsequent price action.
        
        Returns:
            Tuple of (break_index, break_price, is_strong_break) or None
        """
        # Find the next swing of the same type
        next_same_type_idx = None
        for j in range(swing_index + 1, len(all_swings)):
            if all_swings[j].is_high == swing.is_high:
                next_same_type_idx = all_swings[j].index
                break
        
        # Define the search range
        start_idx = swing.index + 1
        end_idx = next_same_type_idx if next_same_type_idx else len(df)
        
        if start_idx >= end_idx or start_idx >= len(df):
            return None
        
        # Look for the break
        for i in range(start_idx, min(end_idx, len(df))):
            row = df.iloc[i]
            avg_atr = atr.iloc[i] if i < len(atr) and not pd.isna(atr.iloc[i]) else 0
            
            if swing.is_high:
                # Check if price closed above the swing high
                if row['close'] > swing.price:
                    displacement = row['close'] - swing.price
                    is_strong = displacement > (avg_atr * self.mss_displacement_factor) if avg_atr > 0 else False
                    return (i, row['close'], is_strong)
            else:
                # Check if price closed below the swing low
                if row['close'] < swing.price:
                    displacement = swing.price - row['close']
                    is_strong = displacement > (avg_atr * self.mss_displacement_factor) if avg_atr > 0 else False
                    return (i, row['close'], is_strong)
        
        return None
    
    def _determine_trend(
        self,
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint],
        structure_breaks: List[MarketStructure]
    ) -> TrendDirection:
        """
        Determine the overall trend direction.
        
        Uses the most recent structure breaks and swing point sequence.
        """
        # If we have recent structure breaks, use the most recent one
        if structure_breaks:
            last_break = structure_breaks[-1]
            if last_break.is_bullish:
                return TrendDirection.BULLISH
            elif last_break.is_bearish:
                return TrendDirection.BEARISH
        
        # Fall back to swing point analysis
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            recent_highs = sorted(swing_highs, key=lambda x: x.index)[-2:]
            recent_lows = sorted(swing_lows, key=lambda x: x.index)[-2:]
            
            higher_highs = recent_highs[1].price > recent_highs[0].price
            higher_lows = recent_lows[1].price > recent_lows[0].price
            lower_highs = recent_highs[1].price < recent_highs[0].price
            lower_lows = recent_lows[1].price < recent_lows[0].price
            
            if higher_highs and higher_lows:
                return TrendDirection.BULLISH
            elif lower_highs and lower_lows:
                return TrendDirection.BEARISH
        
        return TrendDirection.RANGING
    
    def _get_key_levels(
        self,
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint]
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Get the most recent higher high, higher low, lower high, lower low."""
        hh = hl = lh = ll = None
        
        if len(swing_highs) >= 2:
            sorted_highs = sorted(swing_highs, key=lambda x: x.index)
            if sorted_highs[-1].price > sorted_highs[-2].price:
                hh = sorted_highs[-1].price
            else:
                lh = sorted_highs[-1].price
        
        if len(swing_lows) >= 2:
            sorted_lows = sorted(swing_lows, key=lambda x: x.index)
            if sorted_lows[-1].price > sorted_lows[-2].price:
                hl = sorted_lows[-1].price
            else:
                ll = sorted_lows[-1].price
        
        return hh, hl, lh, ll
    
    def _calculate_simple_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate simple ATR for displacement detection."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
