"""
NWOG (New Week Opening Gap) Tracker.

The New Week Opening Gap is the price gap between Friday close (4:59 PM EST)
and Sunday open (6:00 PM EST). NWOGs act as liquidity magnets and key
support/resistance levels according to ICT methodology.

Key concepts:
- NWOGs draw price as liquidity targets
- Consequent Encroachment (CE) is the 50% level of the gap
- Track last 4 NWOGs as historical S/R levels
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, time as dt_time
from enum import Enum
import pytz

from ..utils.logging import get_logger

logger = get_logger(__name__)


class NWOGType(Enum):
    """Type of NWOG based on direction."""
    BULLISH = "bullish"  # Sunday open > Friday close
    BEARISH = "bearish"  # Sunday open < Friday close
    FLAT = "flat"        # No significant gap


@dataclass
class NWOG:
    """Represents a New Week Opening Gap."""
    friday_close: float
    sunday_open: float
    week_start: datetime  # Sunday date
    gap_size: float = 0.0
    gap_pips: float = 0.0
    ce_level: float = 0.0  # Consequent Encroachment (50%)
    gap_type: NWOGType = NWOGType.FLAT
    filled: bool = False
    ce_tested: bool = False
    
    def __post_init__(self):
        """Calculate derived fields."""
        self.gap_size = abs(self.sunday_open - self.friday_close)
        # CE is the 50% level
        self.ce_level = (self.friday_close + self.sunday_open) / 2
        
        if self.sunday_open > self.friday_close:
            self.gap_type = NWOGType.BULLISH
        elif self.sunday_open < self.friday_close:
            self.gap_type = NWOGType.BEARISH
        else:
            self.gap_type = NWOGType.FLAT
    
    @property
    def high(self) -> float:
        """High of the gap zone."""
        return max(self.friday_close, self.sunday_open)
    
    @property
    def low(self) -> float:
        """Low of the gap zone."""
        return min(self.friday_close, self.sunday_open)
    
    def is_price_in_gap(self, price: float) -> bool:
        """Check if price is within the NWOG zone."""
        return self.low <= price <= self.high
    
    def is_price_at_ce(self, price: float, tolerance_pips: float = 5.0, pip_value: float = 0.0001) -> bool:
        """Check if price is at Consequent Encroachment level."""
        tolerance = tolerance_pips * pip_value
        return abs(price - self.ce_level) <= tolerance
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "friday_close": self.friday_close,
            "sunday_open": self.sunday_open,
            "week_start": self.week_start.isoformat(),
            "gap_size": self.gap_size,
            "gap_pips": self.gap_pips,
            "high": self.high,
            "low": self.low,
            "ce_level": self.ce_level,
            "gap_type": self.gap_type.value,
            "filled": self.filled,
            "ce_tested": self.ce_tested
        }


class NWOGTracker:
    """
    Tracks New Week Opening Gaps.
    
    Features:
    - Calculate weekend gaps (Friday close to Sunday open)
    - Track historical NWOGs as S/R levels
    - Identify when price is drawing toward an NWOG
    - Detect CE level reactions
    """
    
    # EST timezone for market times
    EST = pytz.timezone('US/Eastern')
    
    # Market times (EST)
    FRIDAY_CLOSE = dt_time(16, 59)  # 4:59 PM EST
    SUNDAY_OPEN = dt_time(18, 0)     # 6:00 PM EST
    
    def __init__(
        self,
        max_gaps: int = 4,
        pip_value: float = 0.0001,
        min_gap_pips: float = 5.0
    ):
        """
        Initialize NWOG Tracker.
        
        Args:
            max_gaps: Maximum number of NWOGs to track
            pip_value: Pip value for the instrument
            min_gap_pips: Minimum gap size in pips to track
        """
        self.max_gaps = max_gaps
        self.pip_value = pip_value
        self.min_gap_pips = min_gap_pips
        
        # Historical NWOGs (most recent first)
        self.gaps: List[NWOG] = []
        
        logger.info(f"NWOGTracker initialized (max={max_gaps} gaps)")
    
    def add_weekend_gap(
        self,
        friday_close: float,
        sunday_open: float,
        week_start: Optional[datetime] = None
    ) -> Optional[NWOG]:
        """
        Add a new weekend gap.
        
        Args:
            friday_close: Friday 4:59 PM close price
            sunday_open: Sunday 6:00 PM open price
            week_start: Date of the week start
            
        Returns:
            NWOG if significant gap, None otherwise
        """
        if week_start is None:
            # Find the Sunday of current week
            today = datetime.now(self.EST)
            days_since_sunday = today.weekday() + 1  # Monday=0, Sunday=6
            if days_since_sunday == 7:
                days_since_sunday = 0
            week_start = today - timedelta(days=days_since_sunday)
        
        gap_pips = abs(sunday_open - friday_close) / self.pip_value
        
        if gap_pips < self.min_gap_pips:
            logger.debug(f"Gap too small ({gap_pips:.1f} pips) - not tracking")
            return None
        
        nwog = NWOG(
            friday_close=friday_close,
            sunday_open=sunday_open,
            week_start=week_start,
            gap_pips=gap_pips
        )
        
        # Add to front of list (most recent first)
        self.gaps.insert(0, nwog)
        
        # Keep only max_gaps
        if len(self.gaps) > self.max_gaps:
            self.gaps = self.gaps[:self.max_gaps]
        
        logger.info(
            f"New NWOG tracked: {nwog.gap_type.value}, "
            f"Gap: {gap_pips:.1f} pips, CE: {nwog.ce_level:.5f}"
        )
        
        return nwog
    
    def calculate_from_data(
        self,
        friday_data: Dict[str, Any],
        sunday_data: Dict[str, Any]
    ) -> Optional[NWOG]:
        """
        Calculate NWOG from market data.
        
        Args:
            friday_data: Dict with 'close' price from Friday
            sunday_data: Dict with 'open' price from Sunday
            
        Returns:
            NWOG if significant gap exists
        """
        friday_close = friday_data.get('close')
        sunday_open = sunday_data.get('open')
        
        if friday_close is None or sunday_open is None:
            logger.warning("Missing price data for NWOG calculation")
            return None
        
        return self.add_weekend_gap(friday_close, sunday_open)
    
    def get_current_nwog(self) -> Optional[NWOG]:
        """Get the most recent (current week's) NWOG."""
        return self.gaps[0] if self.gaps else None
    
    def get_nearest_nwog(
        self,
        current_price: float,
        direction: Optional[str] = None
    ) -> Optional[NWOG]:
        """
        Find the nearest NWOG to current price.
        
        Args:
            current_price: Current market price
            direction: Optional 'bullish' or 'bearish' to filter
            
        Returns:
            Nearest unfilled NWOG
        """
        unfilled = [g for g in self.gaps if not g.filled]
        
        if direction:
            if direction == 'bullish':
                # For bullish, look for NWOGs below price
                unfilled = [g for g in unfilled if g.ce_level < current_price]
            else:
                # For bearish, look for NWOGs above price
                unfilled = [g for g in unfilled if g.ce_level > current_price]
        
        if not unfilled:
            return None
        
        # Find nearest by distance
        return min(unfilled, key=lambda g: abs(g.ce_level - current_price))
    
    def get_nwog_target(
        self,
        current_price: float,
        direction: str
    ) -> Optional[float]:
        """
        Get NWOG level as potential target.
        
        Args:
            current_price: Current market price
            direction: 'long' or 'short'
            
        Returns:
            NWOG CE level as target, or None
        """
        if direction == 'long':
            # For long, look for NWOGs above price
            candidates = [g for g in self.gaps if not g.filled and g.ce_level > current_price]
        else:
            # For short, look for NWOGs below price
            candidates = [g for g in self.gaps if not g.filled and g.ce_level < current_price]
        
        if not candidates:
            return None
        
        # Return nearest target
        return min(candidates, key=lambda g: abs(g.ce_level - current_price)).ce_level
    
    def update_fill_status(self, current_price: float):
        """
        Update NWOG fill status based on current price.
        
        Args:
            current_price: Current market price
        """
        for nwog in self.gaps:
            if nwog.filled:
                continue
            
            # Check if gap is filled (price traded through entire gap)
            if nwog.gap_type == NWOGType.BULLISH:
                # Bullish gap filled when price drops to Friday close
                if current_price <= nwog.friday_close:
                    nwog.filled = True
                    logger.info(f"Bullish NWOG filled (week of {nwog.week_start})")
            elif nwog.gap_type == NWOGType.BEARISH:
                # Bearish gap filled when price rises to Friday close
                if current_price >= nwog.friday_close:
                    nwog.filled = True
                    logger.info(f"Bearish NWOG filled (week of {nwog.week_start})")
            
            # Check CE test
            if not nwog.ce_tested and nwog.is_price_at_ce(current_price, pip_value=self.pip_value):
                nwog.ce_tested = True
                logger.info(f"NWOG CE level tested (week of {nwog.week_start})")
    
    def is_price_in_any_nwog(self, current_price: float) -> Optional[NWOG]:
        """Check if price is within any tracked NWOG."""
        for nwog in self.gaps:
            if nwog.is_price_in_gap(current_price):
                return nwog
        return None
    
    def get_all_levels(self) -> Dict[str, List[float]]:
        """
        Get all NWOG levels for charting/analysis.
        
        Returns:
            Dict with 'highs', 'lows', 'ce_levels'
        """
        return {
            "highs": [g.high for g in self.gaps],
            "lows": [g.low for g in self.gaps],
            "ce_levels": [g.ce_level for g in self.gaps],
            "unfilled_ce": [g.ce_level for g in self.gaps if not g.filled]
        }
    
    def get_context_for_claude(self, current_price: float) -> str:
        """
        Build NWOG context string for Claude's analysis.
        
        Args:
            current_price: Current market price
            
        Returns:
            Context string describing NWOGs
        """
        if not self.gaps:
            return ""
        
        lines = ["## NWOG Levels (Liquidity Targets)"]
        
        for i, nwog in enumerate(self.gaps):
            age = "Current week" if i == 0 else f"{i} week(s) ago"
            status = "FILLED" if nwog.filled else "UNFILLED"
            distance = (nwog.ce_level - current_price) / self.pip_value
            
            lines.append(
                f"- {age}: {nwog.gap_type.value.upper()} gap, "
                f"CE: {nwog.ce_level:.5f} ({distance:+.0f} pips), "
                f"Status: {status}"
            )
        
        # Add nearest target
        nearest = self.get_nearest_nwog(current_price)
        if nearest:
            lines.append(f"\nNearest NWOG Target: {nearest.ce_level:.5f}")
        
        return "\n".join(lines)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of tracked NWOGs."""
        return {
            "total_tracked": len(self.gaps),
            "unfilled_count": sum(1 for g in self.gaps if not g.filled),
            "current_week": self.gaps[0].to_dict() if self.gaps else None,
            "all_gaps": [g.to_dict() for g in self.gaps]
        }


def detect_nwog_from_history(
    hourly_data: List[Dict[str, Any]],
    pip_value: float = 0.0001
) -> Optional[NWOG]:
    """
    Detect NWOG from historical hourly data.
    
    Args:
        hourly_data: List of dicts with 'time', 'open', 'close'
        pip_value: Pip value for the instrument
        
    Returns:
        NWOG if found, None otherwise
    """
    tracker = NWOGTracker(pip_value=pip_value)
    
    friday_close = None
    sunday_open = None
    
    for bar in hourly_data:
        bar_time = bar.get('time')
        if isinstance(bar_time, str):
            bar_time = datetime.fromisoformat(bar_time)
        
        if bar_time.weekday() == 4:  # Friday
            if bar_time.hour >= 16:
                friday_close = bar.get('close')
        elif bar_time.weekday() == 6:  # Sunday
            if bar_time.hour >= 18:
                sunday_open = bar.get('open')
                break
    
    if friday_close and sunday_open:
        return tracker.add_weekend_gap(friday_close, sunday_open)
    
    return None
