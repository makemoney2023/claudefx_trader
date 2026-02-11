"""
IPDA (Interbank Price Delivery Algorithm) Levels Tracker.

Tracks key draw on liquidity levels:
- Previous Day High/Low (PDH/PDL)
- Previous Week High/Low (PWH/PWL)
- Previous Month High/Low (PMH/PML)

These are the PRIMARY targets for 100-pip expansion moves.
Price is algorithmically delivered to these levels.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class IPDALevel:
    """Represents an IPDA level (draw on liquidity target)."""
    level_type: str  # 'PDH', 'PDL', 'PWH', 'PWL', 'PMH', 'PML'
    price: float
    period_start: datetime
    period_end: datetime
    swept: bool = False
    sweep_time: Optional[datetime] = None
    distance_pips: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.level_type,
            "price": self.price,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "swept": self.swept,
            "sweep_time": self.sweep_time.isoformat() if self.sweep_time else None,
            "distance_pips": self.distance_pips
        }


@dataclass
class IPDAAnalysis:
    """Complete IPDA analysis result."""
    # Previous Day
    pdh: Optional[IPDALevel] = None  # Previous Day High
    pdl: Optional[IPDALevel] = None  # Previous Day Low
    
    # Previous Week
    pwh: Optional[IPDALevel] = None  # Previous Week High
    pwl: Optional[IPDALevel] = None  # Previous Week Low
    
    # Previous Month
    pmh: Optional[IPDALevel] = None  # Previous Month High
    pml: Optional[IPDALevel] = None  # Previous Month Low
    
    # Current ranges
    current_day_high: float = 0.0
    current_day_low: float = 0.0
    current_week_high: float = 0.0
    current_week_low: float = 0.0
    
    # Targets
    nearest_target_long: Optional[IPDALevel] = None
    nearest_target_short: Optional[IPDALevel] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pdh": self.pdh.to_dict() if self.pdh else None,
            "pdl": self.pdl.to_dict() if self.pdl else None,
            "pwh": self.pwh.to_dict() if self.pwh else None,
            "pwl": self.pwl.to_dict() if self.pwl else None,
            "pmh": self.pmh.to_dict() if self.pmh else None,
            "pml": self.pml.to_dict() if self.pml else None,
            "current_day_range": {
                "high": self.current_day_high,
                "low": self.current_day_low
            },
            "current_week_range": {
                "high": self.current_week_high,
                "low": self.current_week_low
            },
            "nearest_target_long": self.nearest_target_long.to_dict() if self.nearest_target_long else None,
            "nearest_target_short": self.nearest_target_short.to_dict() if self.nearest_target_short else None
        }
    
    def get_targets_for_direction(self, direction: str, current_price: float) -> List[IPDALevel]:
        """Get IPDA targets for a given trade direction, sorted by distance."""
        targets = []
        
        if direction == 'long':
            # Long targets are above current price
            for level in [self.pdh, self.pwh, self.pmh]:
                if level and level.price > current_price and not level.swept:
                    targets.append(level)
        else:
            # Short targets are below current price
            for level in [self.pdl, self.pwl, self.pml]:
                if level and level.price < current_price and not level.swept:
                    targets.append(level)
        
        # Sort by distance (nearest first)
        targets.sort(key=lambda x: abs(x.price - current_price))
        return targets
    
    def get_100_pip_target(
        self,
        direction: str,
        current_price: float,
        pip_value: float = 0.0001
    ) -> Optional[float]:
        """
        Get the best target for a 100-pip move.
        
        Prioritizes:
        1. Nearest unswepted IPDA level that's 80-120 pips away
        2. Any IPDA level that's 50+ pips away
        3. Projected 100-pip level if no IPDA available
        """
        targets = self.get_targets_for_direction(direction, current_price)
        
        # Look for ideal 100-pip targets
        for level in targets:
            distance = abs(level.price - current_price) / pip_value
            if 80 <= distance <= 150:  # Sweet spot for 100-pip target
                return level.price
        
        # Settle for any target 50+ pips away
        for level in targets:
            distance = abs(level.price - current_price) / pip_value
            if distance >= 50:
                return level.price
        
        # No IPDA targets - return projected 100 pips
        if direction == 'long':
            return current_price + (100 * pip_value)
        else:
            return current_price - (100 * pip_value)


class IPDATracker:
    """
    Tracks IPDA (draw on liquidity) levels.
    
    ICT teaches that price is delivered algorithmically to these
    key levels. These are the PRIMARY targets for expansion moves.
    
    Usage:
    1. Update daily with new price data
    2. Query for targets based on trade direction
    3. Use as take profit levels
    """
    
    def __init__(self, pip_value: float = 0.0001):
        """
        Initialize IPDA tracker.
        
        Args:
            pip_value: Pip value for the instrument
        """
        self.pip_value = pip_value
        self._analysis = IPDAAnalysis()
        self._last_update = None
        
        logger.info("IPDA tracker initialized")
    
    def update(
        self,
        df: pd.DataFrame,
        current_time: Optional[datetime] = None
    ) -> IPDAAnalysis:
        """
        Update IPDA levels from price data.
        
        Args:
            df: DataFrame with OHLCV data including 'time' column
            current_time: Current timestamp
            
        Returns:
            Updated IPDAAnalysis
        """
        if len(df) < 2:
            return self._analysis
        
        if current_time is None:
            current_time = datetime.now()
        
        current_price = df.iloc[-1]['close']
        
        # Ensure time column is datetime
        df = df.copy()
        if 'time' in df.columns:
            df['datetime'] = pd.to_datetime(df['time'])
        elif isinstance(df.index, pd.DatetimeIndex):
            # Time is the index (set by DataFetcher._to_dataframe)
            df['datetime'] = df.index
        elif df.index.name == 'time':
            df['datetime'] = pd.to_datetime(df.index)
        else:
            # Can't calculate IPDA without timestamps
            logger.warning("IPDA update requires 'time' column")
            return self._analysis
        
        # Calculate Previous Day High/Low
        pdh, pdl = self._calculate_previous_day(df, current_time)
        
        # Calculate Previous Week High/Low
        pwh, pwl = self._calculate_previous_week(df, current_time)
        
        # Calculate Previous Month High/Low
        pmh, pml = self._calculate_previous_month(df, current_time)
        
        # Calculate current ranges
        cdh, cdl = self._calculate_current_day(df, current_time)
        cwh, cwl = self._calculate_current_week(df, current_time)
        
        # Build analysis
        self._analysis = IPDAAnalysis(
            pdh=pdh,
            pdl=pdl,
            pwh=pwh,
            pwl=pwl,
            pmh=pmh,
            pml=pml,
            current_day_high=cdh,
            current_day_low=cdl,
            current_week_high=cwh,
            current_week_low=cwl
        )
        
        # Calculate distances
        self._update_distances(current_price)
        
        # Find nearest targets
        self._analysis.nearest_target_long = self._find_nearest_target(
            current_price, 'long'
        )
        self._analysis.nearest_target_short = self._find_nearest_target(
            current_price, 'short'
        )
        
        self._last_update = current_time
        
        logger.debug(
            f"IPDA updated: PDH={pdh.price if pdh else None}, "
            f"PDL={pdl.price if pdl else None}"
        )
        
        return self._analysis
    
    def _calculate_previous_day(
        self,
        df: pd.DataFrame,
        current_time: datetime
    ) -> Tuple[Optional[IPDALevel], Optional[IPDALevel]]:
        """Calculate previous day high and low."""
        yesterday = (current_time - timedelta(days=1)).date()
        
        # Filter for yesterday's data
        day_data = df[df['datetime'].dt.date == yesterday]
        
        if len(day_data) < 1:
            # Try 2 days ago (in case of weekend/holiday)
            yesterday = (current_time - timedelta(days=2)).date()
            day_data = df[df['datetime'].dt.date == yesterday]
        
        if len(day_data) < 1:
            return None, None
        
        period_start = datetime.combine(yesterday, datetime.min.time())
        period_end = datetime.combine(yesterday, datetime.max.time())
        
        pdh = IPDALevel(
            level_type='PDH',
            price=day_data['high'].max(),
            period_start=period_start,
            period_end=period_end
        )
        
        pdl = IPDALevel(
            level_type='PDL',
            price=day_data['low'].min(),
            period_start=period_start,
            period_end=period_end
        )
        
        return pdh, pdl
    
    def _calculate_previous_week(
        self,
        df: pd.DataFrame,
        current_time: datetime
    ) -> Tuple[Optional[IPDALevel], Optional[IPDALevel]]:
        """Calculate previous week high and low."""
        # Get start of current week (Monday)
        current_week_start = current_time - timedelta(days=current_time.weekday())
        
        # Previous week
        prev_week_end = current_week_start - timedelta(days=1)
        prev_week_start = prev_week_end - timedelta(days=6)
        
        # Filter for previous week
        week_data = df[
            (df['datetime'].dt.date >= prev_week_start.date()) &
            (df['datetime'].dt.date <= prev_week_end.date())
        ]
        
        if len(week_data) < 1:
            return None, None
        
        pwh = IPDALevel(
            level_type='PWH',
            price=week_data['high'].max(),
            period_start=prev_week_start,
            period_end=prev_week_end
        )
        
        pwl = IPDALevel(
            level_type='PWL',
            price=week_data['low'].min(),
            period_start=prev_week_start,
            period_end=prev_week_end
        )
        
        return pwh, pwl
    
    def _calculate_previous_month(
        self,
        df: pd.DataFrame,
        current_time: datetime
    ) -> Tuple[Optional[IPDALevel], Optional[IPDALevel]]:
        """Calculate previous month high and low."""
        # First day of current month
        first_of_month = current_time.replace(day=1)
        
        # Last day of previous month
        prev_month_end = first_of_month - timedelta(days=1)
        prev_month_start = prev_month_end.replace(day=1)
        
        # Filter for previous month
        month_data = df[
            (df['datetime'].dt.date >= prev_month_start.date()) &
            (df['datetime'].dt.date <= prev_month_end.date())
        ]
        
        if len(month_data) < 1:
            return None, None
        
        pmh = IPDALevel(
            level_type='PMH',
            price=month_data['high'].max(),
            period_start=prev_month_start,
            period_end=prev_month_end
        )
        
        pml = IPDALevel(
            level_type='PML',
            price=month_data['low'].min(),
            period_start=prev_month_start,
            period_end=prev_month_end
        )
        
        return pmh, pml
    
    def _calculate_current_day(
        self,
        df: pd.DataFrame,
        current_time: datetime
    ) -> Tuple[float, float]:
        """Calculate current day high and low."""
        today = current_time.date()
        day_data = df[df['datetime'].dt.date == today]
        
        if len(day_data) < 1:
            return 0.0, 0.0
        
        return day_data['high'].max(), day_data['low'].min()
    
    def _calculate_current_week(
        self,
        df: pd.DataFrame,
        current_time: datetime
    ) -> Tuple[float, float]:
        """Calculate current week high and low."""
        week_start = current_time - timedelta(days=current_time.weekday())
        week_data = df[df['datetime'].dt.date >= week_start.date()]
        
        if len(week_data) < 1:
            return 0.0, 0.0
        
        return week_data['high'].max(), week_data['low'].min()
    
    def _update_distances(self, current_price: float):
        """Update distance in pips for all levels."""
        for level in [
            self._analysis.pdh, self._analysis.pdl,
            self._analysis.pwh, self._analysis.pwl,
            self._analysis.pmh, self._analysis.pml
        ]:
            if level:
                level.distance_pips = abs(level.price - current_price) / self.pip_value
    
    def _find_nearest_target(
        self,
        current_price: float,
        direction: str
    ) -> Optional[IPDALevel]:
        """Find nearest unswepted IPDA target."""
        targets = []
        
        if direction == 'long':
            # Targets above
            for level in [self._analysis.pdh, self._analysis.pwh, self._analysis.pmh]:
                if level and level.price > current_price and not level.swept:
                    targets.append(level)
        else:
            # Targets below
            for level in [self._analysis.pdl, self._analysis.pwl, self._analysis.pml]:
                if level and level.price < current_price and not level.swept:
                    targets.append(level)
        
        if not targets:
            return None
        
        # Return nearest
        return min(targets, key=lambda x: abs(x.price - current_price))
    
    def get_take_profit_levels(
        self,
        direction: str,
        current_price: float,
        stop_loss: float
    ) -> Dict[str, Optional[float]]:
        """
        Get recommended take profit levels for scaling out.
        
        Returns multi-target TPs:
        - TP1: ~1:2 R:R (close 30%)
        - TP2: Nearest IPDA level (close 30%)
        - TP3: Second IPDA level / 100-pip target (runner 40%)
        
        Args:
            direction: 'long' or 'short'
            current_price: Entry price
            stop_loss: Stop loss price
            
        Returns:
            Dict with tp1, tp2, tp3 levels
        """
        risk = abs(current_price - stop_loss)
        targets = self._analysis.get_targets_for_direction(direction, current_price)
        
        # TP1: 2R
        if direction == 'long':
            tp1 = current_price + (risk * 2)
        else:
            tp1 = current_price - (risk * 2)
        
        # TP2: First IPDA target (if exists and reasonable)
        tp2 = None
        if targets:
            first_target = targets[0]
            # Must be at least 2R away to be worth using
            target_r = abs(first_target.price - current_price) / risk
            if target_r >= 2:
                tp2 = first_target.price
        
        # TP3: 100-pip target or second IPDA
        tp3 = self._analysis.get_100_pip_target(direction, current_price, self.pip_value)
        
        # If TP2 wasn't set, use 100-pip for TP2
        if tp2 is None:
            tp2 = tp3
            tp3 = None
        
        # If we have second IPDA target, use that for TP3
        if len(targets) > 1 and tp3 is None:
            tp3 = targets[1].price
        
        return {
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk_pips": risk / self.pip_value,
            "tp1_r": 2.0,
            "tp2_r": abs(tp2 - current_price) / risk if tp2 else None,
            "tp3_r": abs(tp3 - current_price) / risk if tp3 else None
        }
    
    def check_sweep(
        self,
        current_price: float,
        current_time: datetime
    ):
        """
        Check if any IPDA levels have been swept.
        
        A level is swept when price trades through it.
        """
        for level in [
            self._analysis.pdh, self._analysis.pdl,
            self._analysis.pwh, self._analysis.pwl,
            self._analysis.pmh, self._analysis.pml
        ]:
            if level and not level.swept:
                if level.level_type.endswith('H'):  # High level
                    if current_price > level.price:
                        level.swept = True
                        level.sweep_time = current_time
                        logger.info(f"IPDA {level.level_type} swept at {current_price}")
                else:  # Low level
                    if current_price < level.price:
                        level.swept = True
                        level.sweep_time = current_time
                        logger.info(f"IPDA {level.level_type} swept at {current_price}")
    
    @property
    def analysis(self) -> IPDAAnalysis:
        """Get current IPDA analysis."""
        return self._analysis


def get_ipda_targets(
    df: pd.DataFrame,
    direction: str,
    current_price: float,
    stop_loss: float,
    pip_value: float = 0.0001
) -> Dict[str, Any]:
    """
    Convenience function to get IPDA-based take profit targets.
    
    Args:
        df: DataFrame with OHLCV data including 'time' column
        direction: 'long' or 'short'
        current_price: Entry price
        stop_loss: Stop loss price
        pip_value: Pip value for instrument
        
    Returns:
        Dictionary with take profit levels
    """
    tracker = IPDATracker(pip_value=pip_value)
    tracker.update(df)
    return tracker.get_take_profit_levels(direction, current_price, stop_loss)
