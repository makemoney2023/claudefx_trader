"""
Premium/Discount Zone Analysis.

ICT concept: Price should be bought in discount (below equilibrium)
and sold in premium (above equilibrium).

This module:
- Calculates equilibrium (50% level between swing high/low)
- Identifies premium zone (above 50%)
- Identifies discount zone (below 50%)
- Validates entries are in optimal zones
- Integrates with OTE (Optimal Trade Entry) at 62-79% retracement
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List
from enum import Enum
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


class PriceZone(Enum):
    """Price zone classification."""
    EXTREME_PREMIUM = "extreme_premium"    # Above 88.6%
    PREMIUM = "premium"                     # 61.8% - 88.6%
    EQUILIBRIUM = "equilibrium"            # 38.2% - 61.8%
    DISCOUNT = "discount"                   # 11.4% - 38.2%
    EXTREME_DISCOUNT = "extreme_discount"  # Below 11.4%


@dataclass
class PremiumDiscountAnalysis:
    """Premium/Discount zone analysis result."""
    swing_high: float
    swing_low: float
    equilibrium: float  # 50% level
    current_price: float
    current_zone: PriceZone
    retracement_percent: float
    
    # OTE zone (Optimal Trade Entry)
    ote_high: float  # 62% retracement
    ote_low: float   # 79% retracement
    in_ote: bool
    
    # Fibonacci levels
    fib_levels: Dict[str, float]
    
    # Trade validation
    long_valid: bool   # True if in discount
    short_valid: bool  # True if in premium
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "swing_high": self.swing_high,
            "swing_low": self.swing_low,
            "equilibrium": self.equilibrium,
            "current_price": self.current_price,
            "current_zone": self.current_zone.value,
            "retracement_percent": self.retracement_percent,
            "ote": {
                "high": self.ote_high,
                "low": self.ote_low,
                "in_zone": self.in_ote
            },
            "fib_levels": self.fib_levels,
            "validation": {
                "long_valid": self.long_valid,
                "short_valid": self.short_valid
            }
        }


class PremiumDiscountAnalyzer:
    """
    Analyzes price relative to premium and discount zones.
    
    Core ICT Principle:
    - Buy in DISCOUNT (price below equilibrium)
    - Sell in PREMIUM (price above equilibrium)
    
    Violating this principle significantly reduces win rate.
    """
    
    # Fibonacci levels for zone boundaries
    FIB_LEVELS = {
        0.0: "swing_low",
        0.236: "fib_23.6",
        0.382: "fib_38.2",
        0.5: "equilibrium",
        0.618: "fib_61.8",
        0.705: "ote_mid",
        0.786: "fib_78.6",
        1.0: "swing_high"
    }
    
    def __init__(self, swing_lookback: int = 20):
        """
        Initialize analyzer.
        
        Args:
            swing_lookback: Candles to look back for swing detection
        """
        self.swing_lookback = swing_lookback
        logger.info("Premium/Discount analyzer initialized")
    
    def analyze(
        self,
        df: pd.DataFrame,
        current_price: Optional[float] = None,
        manual_swing_high: Optional[float] = None,
        manual_swing_low: Optional[float] = None
    ) -> PremiumDiscountAnalysis:
        """
        Analyze current price position relative to premium/discount zones.
        
        Args:
            df: DataFrame with OHLCV data
            current_price: Current price (uses last close if not provided)
            manual_swing_high: Override swing high detection
            manual_swing_low: Override swing low detection
            
        Returns:
            PremiumDiscountAnalysis
        """
        if current_price is None:
            current_price = df.iloc[-1]['close']
        
        # Get swing points
        if manual_swing_high is not None and manual_swing_low is not None:
            swing_high = manual_swing_high
            swing_low = manual_swing_low
        else:
            swing_high, swing_low = self._find_swing_points(df)
        
        if swing_high == swing_low:
            # Can't analyze without a range
            return self._empty_analysis(current_price)
        
        # Calculate key levels
        range_size = swing_high - swing_low
        equilibrium = swing_low + (range_size * 0.5)
        
        # OTE zone (62-79% retracement from low)
        ote_high = swing_low + (range_size * 0.618)
        ote_low = swing_low + (range_size * 0.786)
        
        # Calculate Fibonacci levels
        fib_levels = {}
        for level, name in self.FIB_LEVELS.items():
            fib_levels[name] = swing_low + (range_size * level)
        
        # Determine current position as percentage
        retracement_percent = (current_price - swing_low) / range_size
        
        # Determine zone
        current_zone = self._get_zone(retracement_percent)
        
        # Check if in OTE
        in_ote = ote_high <= current_price <= ote_low or ote_low <= current_price <= ote_high
        
        # Validate trade directions
        long_valid = retracement_percent <= 0.5  # In discount
        short_valid = retracement_percent >= 0.5  # In premium
        
        return PremiumDiscountAnalysis(
            swing_high=swing_high,
            swing_low=swing_low,
            equilibrium=equilibrium,
            current_price=current_price,
            current_zone=current_zone,
            retracement_percent=retracement_percent,
            ote_high=ote_high,
            ote_low=ote_low,
            in_ote=in_ote,
            fib_levels=fib_levels,
            long_valid=long_valid,
            short_valid=short_valid
        )
    
    def _find_swing_points(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Find recent swing high and low."""
        recent = df.tail(self.swing_lookback)
        
        swing_high = recent['high'].max()
        swing_low = recent['low'].min()
        
        return swing_high, swing_low
    
    def _get_zone(self, retracement_percent: float) -> PriceZone:
        """Determine price zone from retracement percentage."""
        if retracement_percent >= 0.886:
            return PriceZone.EXTREME_PREMIUM
        elif retracement_percent >= 0.618:
            return PriceZone.PREMIUM
        elif retracement_percent >= 0.382:
            return PriceZone.EQUILIBRIUM
        elif retracement_percent >= 0.114:
            return PriceZone.DISCOUNT
        else:
            return PriceZone.EXTREME_DISCOUNT
    
    def _empty_analysis(self, current_price: float) -> PremiumDiscountAnalysis:
        """Return empty analysis when swing range is invalid."""
        return PremiumDiscountAnalysis(
            swing_high=current_price,
            swing_low=current_price,
            equilibrium=current_price,
            current_price=current_price,
            current_zone=PriceZone.EQUILIBRIUM,
            retracement_percent=0.5,
            ote_high=current_price,
            ote_low=current_price,
            in_ote=False,
            fib_levels={},
            long_valid=True,
            short_valid=True
        )
    
    def validate_entry(
        self,
        direction: str,
        current_price: float,
        df: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Validate if an entry is in the correct zone.
        
        Args:
            direction: 'long' or 'short'
            current_price: Proposed entry price
            df: Price data
            
        Returns:
            Validation result with approval/rejection and reason
        """
        analysis = self.analyze(df, current_price)
        
        result = {
            "valid": False,
            "zone": analysis.current_zone.value,
            "retracement": f"{analysis.retracement_percent:.1%}",
            "reason": "",
            "in_ote": analysis.in_ote,
            "adjustment_needed": None
        }
        
        if direction == 'long':
            if analysis.long_valid:
                result["valid"] = True
                result["reason"] = f"Long entry valid: In {analysis.current_zone.value}"
                
                if analysis.in_ote:
                    result["reason"] += " (OPTIMAL - in OTE zone)"
                elif analysis.current_zone == PriceZone.EXTREME_DISCOUNT:
                    result["reason"] += " (EXCELLENT - extreme discount)"
            else:
                result["reason"] = f"Long entry in PREMIUM zone ({analysis.retracement_percent:.0%})"
                result["adjustment_needed"] = analysis.equilibrium  # Wait for discount
                
        else:  # short
            if analysis.short_valid:
                result["valid"] = True
                result["reason"] = f"Short entry valid: In {analysis.current_zone.value}"
                
                if analysis.in_ote:
                    result["reason"] += " (OPTIMAL - in OTE zone)"
                elif analysis.current_zone == PriceZone.EXTREME_PREMIUM:
                    result["reason"] += " (EXCELLENT - extreme premium)"
            else:
                result["reason"] = f"Short entry in DISCOUNT zone ({analysis.retracement_percent:.0%})"
                result["adjustment_needed"] = analysis.equilibrium  # Wait for premium
        
        return result
    
    def get_optimal_entry_zone(
        self,
        direction: str,
        df: pd.DataFrame
    ) -> Dict[str, float]:
        """
        Get the optimal entry zone for a trade direction.
        
        Args:
            direction: 'long' or 'short'
            df: Price data
            
        Returns:
            Entry zone with ideal entry price and boundaries
        """
        analysis = self.analyze(df)
        
        if direction == 'long':
            # Long entries should be in discount (below 50%)
            # Ideal is 62-79% retracement (OTE zone)
            return {
                "ideal_entry": analysis.fib_levels.get("fib_78.6", analysis.equilibrium),
                "zone_high": analysis.equilibrium,  # 50%
                "zone_low": analysis.swing_low,
                "ote_high": analysis.ote_low,  # 79% (lower price)
                "ote_low": analysis.ote_high,  # 62% (higher price)
                "description": "Enter at or below equilibrium, ideally in OTE (62-79%)"
            }
        else:
            # Short entries should be in premium (above 50%)
            return {
                "ideal_entry": analysis.fib_levels.get("fib_23.6", analysis.equilibrium),
                "zone_high": analysis.swing_high,
                "zone_low": analysis.equilibrium,  # 50%
                "ote_high": analysis.ote_high,  # 62%
                "ote_low": analysis.ote_low,  # 79%
                "description": "Enter at or above equilibrium, ideally in OTE (62-79%)"
            }


def validate_entry_zone(
    direction: str,
    current_price: float,
    df: pd.DataFrame
) -> Dict[str, Any]:
    """
    Convenience function to validate entry is in correct zone.
    
    Args:
        direction: 'long' or 'short'
        current_price: Proposed entry price
        df: Price data
        
    Returns:
        Validation result
    """
    analyzer = PremiumDiscountAnalyzer()
    return analyzer.validate_entry(direction, current_price, df)
