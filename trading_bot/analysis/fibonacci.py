"""
Fibonacci and OTE (Optimal Trade Entry) Module.

Implements ICT Fibonacci concepts including:
- Premium/Discount zones (above/below 50% equilibrium)
- Optimal Trade Entry (62-79% retracement)
- Key Fibonacci levels for targets
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


class PriceZone(Enum):
    """Price zone relative to equilibrium."""
    PREMIUM = "premium"        # Above 50% - expensive, look to sell
    DISCOUNT = "discount"      # Below 50% - cheap, look to buy
    EQUILIBRIUM = "equilibrium"  # At or near 50%


@dataclass
class FibonacciLevels:
    """
    Fibonacci retracement levels for a price range.
    
    In ICT context:
    - 0% = Start of move (swing low for bullish, swing high for bearish)
    - 50% = Equilibrium (fair value)
    - 62-79% = OTE zone (optimal entry for retracements)
    - 100% = End of move
    """
    swing_high: float
    swing_low: float
    direction: str  # 'bullish' or 'bearish'
    
    # Standard Fibonacci levels
    level_0: float = 0.0
    level_236: float = 0.0
    level_382: float = 0.0
    level_50: float = 0.0
    level_618: float = 0.0
    level_705: float = 0.0
    level_79: float = 0.0
    level_100: float = 0.0
    
    # OTE zone boundaries
    ote_top: float = 0.0
    ote_bottom: float = 0.0
    
    def __post_init__(self):
        """Calculate all Fibonacci levels."""
        range_size = self.swing_high - self.swing_low
        
        if self.direction == 'bullish':
            # For bullish retracement: measure from low to high
            # Retracement levels go DOWN from the high
            self.level_0 = self.swing_high      # 0% = top
            self.level_236 = self.swing_high - (range_size * 0.236)
            self.level_382 = self.swing_high - (range_size * 0.382)
            self.level_50 = self.swing_high - (range_size * 0.5)
            self.level_618 = self.swing_high - (range_size * 0.618)
            self.level_705 = self.swing_high - (range_size * 0.705)
            self.level_79 = self.swing_high - (range_size * 0.79)
            self.level_100 = self.swing_low     # 100% = bottom
            
            # OTE zone for bullish: 62-79% retracement (discount zone)
            self.ote_top = self.level_618
            self.ote_bottom = self.level_79
        else:
            # For bearish retracement: measure from high to low
            # Retracement levels go UP from the low
            self.level_0 = self.swing_low       # 0% = bottom
            self.level_236 = self.swing_low + (range_size * 0.236)
            self.level_382 = self.swing_low + (range_size * 0.382)
            self.level_50 = self.swing_low + (range_size * 0.5)
            self.level_618 = self.swing_low + (range_size * 0.618)
            self.level_705 = self.swing_low + (range_size * 0.705)
            self.level_79 = self.swing_low + (range_size * 0.79)
            self.level_100 = self.swing_high    # 100% = top
            
            # OTE zone for bearish: 62-79% retracement (premium zone)
            self.ote_bottom = self.level_618
            self.ote_top = self.level_79
    
    @property
    def equilibrium(self) -> float:
        """Get the 50% equilibrium level."""
        return self.level_50
    
    @property
    def range_size(self) -> float:
        """Get the total range size."""
        return self.swing_high - self.swing_low
    
    def get_zone(self, price: float) -> PriceZone:
        """Determine if price is in premium, discount, or equilibrium zone."""
        tolerance = self.range_size * 0.02  # 2% tolerance for equilibrium
        
        if abs(price - self.level_50) <= tolerance:
            return PriceZone.EQUILIBRIUM
        elif price > self.level_50:
            return PriceZone.PREMIUM
        else:
            return PriceZone.DISCOUNT
    
    def is_in_ote(self, price: float) -> bool:
        """Check if price is within the OTE zone."""
        return self.ote_bottom <= price <= self.ote_top
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "swing_high": float(self.swing_high),
            "swing_low": float(self.swing_low),
            "direction": str(self.direction),
            "levels": {
                "0%": float(self.level_0),
                "23.6%": float(self.level_236),
                "38.2%": float(self.level_382),
                "50%": float(self.level_50),
                "61.8%": float(self.level_618),
                "70.5%": float(self.level_705),
                "79%": float(self.level_79),
                "100%": float(self.level_100)
            },
            "ote_zone": {
                "top": float(self.ote_top),
                "bottom": float(self.ote_bottom)
            },
            "equilibrium": float(self.equilibrium)
        }


@dataclass
class OTEAnalysis:
    """Analysis result for Optimal Trade Entry."""
    fib_levels: FibonacciLevels
    current_price: float
    price_zone: PriceZone
    in_ote: bool
    distance_to_ote: float  # Negative if above, positive if below
    optimal_entry: Optional[float]
    
    def to_dict(self) -> dict:
        return {
            "fib_levels": self.fib_levels.to_dict(),
            "current_price": float(self.current_price),
            "price_zone": self.price_zone.value,
            "in_ote": bool(self.in_ote),
            "distance_to_ote": float(self.distance_to_ote),
            "optimal_entry": float(self.optimal_entry) if self.optimal_entry else None
        }


class FibonacciAnalyzer:
    """
    Analyzes price using Fibonacci retracement and OTE concepts.
    
    ICT Fibonacci Rules:
    - Premium zone (above 50%): Look to sell/short
    - Discount zone (below 50%): Look to buy/long
    - OTE zone (62-79%): Optimal entry for retracements
    - 70.5% = "Sweet spot" within OTE
    """
    
    def __init__(self):
        logger.info("Fibonacci analyzer initialized")
    
    def calculate_levels(
        self,
        swing_high: float,
        swing_low: float,
        direction: str
    ) -> FibonacciLevels:
        """
        Calculate Fibonacci levels for a swing range.
        
        Args:
            swing_high: Recent swing high price
            swing_low: Recent swing low price
            direction: 'bullish' (buying pullback) or 'bearish' (selling rally)
            
        Returns:
            FibonacciLevels with all calculated levels
        """
        return FibonacciLevels(
            swing_high=swing_high,
            swing_low=swing_low,
            direction=direction
        )
    
    def analyze_ote(
        self,
        df: pd.DataFrame,
        direction: str,
        lookback: int = 50
    ) -> Optional[OTEAnalysis]:
        """
        Analyze price for OTE entry opportunities.
        
        Args:
            df: DataFrame with OHLCV data
            direction: Expected trade direction ('bullish' or 'bearish')
            lookback: Number of candles to look back for swing points
            
        Returns:
            OTEAnalysis with entry recommendations
        """
        if len(df) < lookback:
            lookback = len(df)
        
        recent_df = df.tail(lookback)
        current_price = df.iloc[-1]['close']
        
        # Find recent swing high and low
        swing_high = recent_df['high'].max()
        swing_low = recent_df['low'].min()
        
        # Calculate Fibonacci levels
        fib = self.calculate_levels(swing_high, swing_low, direction)
        
        # Determine current zone
        price_zone = fib.get_zone(current_price)
        in_ote = fib.is_in_ote(current_price)
        
        # Calculate distance to OTE
        if direction == 'bullish':
            # For bullish, we want price to retrace DOWN into OTE
            if current_price > fib.ote_top:
                distance = fib.ote_top - current_price  # Negative
            elif current_price < fib.ote_bottom:
                distance = fib.ote_bottom - current_price  # Positive
            else:
                distance = 0
            optimal_entry = fib.level_705  # 70.5% is the sweet spot
        else:
            # For bearish, we want price to retrace UP into OTE
            if current_price < fib.ote_bottom:
                distance = current_price - fib.ote_bottom  # Negative
            elif current_price > fib.ote_top:
                distance = current_price - fib.ote_top  # Positive
            else:
                distance = 0
            optimal_entry = fib.level_705
        
        return OTEAnalysis(
            fib_levels=fib,
            current_price=current_price,
            price_zone=price_zone,
            in_ote=in_ote,
            distance_to_ote=distance,
            optimal_entry=optimal_entry if in_ote else None
        )
    
    def get_expansion_targets(
        self,
        swing_high: float,
        swing_low: float,
        direction: str
    ) -> dict:
        """
        Calculate Fibonacci expansion targets for take profit.
        
        Args:
            swing_high: Swing high price
            swing_low: Swing low price
            direction: Trade direction
            
        Returns:
            Dictionary of expansion targets
        """
        range_size = swing_high - swing_low
        
        if direction == 'bullish':
            # Expansion targets above swing high
            return {
                "-27%": swing_high + (range_size * 0.27),
                "-62%": swing_high + (range_size * 0.62),
                "-100%": swing_high + (range_size * 1.0),
                "-162%": swing_high + (range_size * 1.62)
            }
        else:
            # Expansion targets below swing low
            return {
                "-27%": swing_low - (range_size * 0.27),
                "-62%": swing_low - (range_size * 0.62),
                "-100%": swing_low - (range_size * 1.0),
                "-162%": swing_low - (range_size * 1.62)
            }
    
    def is_at_premium(self, price: float, swing_high: float, swing_low: float) -> bool:
        """Check if price is in premium zone (above 50%)."""
        equilibrium = (swing_high + swing_low) / 2
        return price > equilibrium
    
    def is_at_discount(self, price: float, swing_high: float, swing_low: float) -> bool:
        """Check if price is in discount zone (below 50%)."""
        equilibrium = (swing_high + swing_low) / 2
        return price < equilibrium
