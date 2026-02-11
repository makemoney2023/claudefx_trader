"""
AMD Cycle (Accumulation, Manipulation, Distribution) Detector.

Identifies the Power of 3 market phases and Judas swing patterns.
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, time, timedelta
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


class AMDPhase(Enum):
    """AMD cycle phases."""
    ACCUMULATION = "accumulation"
    MANIPULATION = "manipulation"
    DISTRIBUTION = "distribution"
    UNKNOWN = "unknown"


@dataclass
class AMDCycleState:
    """Current state of the AMD cycle."""
    phase: AMDPhase
    accumulation_high: Optional[float]
    accumulation_low: Optional[float]
    manipulation_extreme: Optional[float]
    manipulation_direction: Optional[str]  # 'up' or 'down' (opposite of true direction)
    expected_direction: Optional[str]  # 'bullish' or 'bearish'
    phase_start_time: Optional[datetime]
    confidence: float


@dataclass 
class AMDJudasSwing:
    """Represents a Judas swing in the AMD manipulation phase.
    
    Note: Renamed from JudasSwing to avoid conflict with the
    JudasSwing class in power_of_three.py which is the canonical export.
    """
    direction: str  # 'up' or 'down'
    extreme_price: float
    extreme_time: datetime
    range_broken: str  # 'high' or 'low'
    reversal_confirmed: bool
    pips_beyond_range: float


class AMDCycleAnalyzer:
    """
    Analyzes market phases according to ICT AMD cycle.
    
    Phases:
    - Accumulation: Asian session consolidation (7 PM - 2 AM EST)
    - Manipulation: False breakout / Judas swing (2 AM - 4 AM EST)
    - Distribution: True directional move (4 AM - 12 PM EST)
    """
    
    # Time boundaries (EST)
    ACCUMULATION_START = time(19, 0)  # 7 PM
    ACCUMULATION_END = time(2, 0)     # 2 AM
    MANIPULATION_END = time(4, 0)     # 4 AM (approx)
    DISTRIBUTION_END = time(12, 0)    # 12 PM
    
    def __init__(
        self,
        range_threshold_pips: float = 30.0,
        manipulation_threshold_pips: float = 10.0,
        pip_value: float = 0.0001
    ):
        """
        Initialize AMD analyzer.
        
        Args:
            range_threshold_pips: Max accumulation range size
            manipulation_threshold_pips: Min pips beyond range for manipulation
            pip_value: Pip value for the instrument
        """
        self.range_threshold_pips = range_threshold_pips
        self.manipulation_threshold_pips = manipulation_threshold_pips
        self.pip_value = pip_value
        
        self._current_state = AMDCycleState(
            phase=AMDPhase.UNKNOWN,
            accumulation_high=None,
            accumulation_low=None,
            manipulation_extreme=None,
            manipulation_direction=None,
            expected_direction=None,
            phase_start_time=None,
            confidence=0.0
        )
    
    def analyze(
        self,
        df: pd.DataFrame,
        current_time: Optional[datetime] = None
    ) -> AMDCycleState:
        """
        Analyze current AMD cycle state.
        
        Args:
            df: DataFrame with OHLCV data including time
            current_time: Current time for analysis
            
        Returns:
            Current AMDCycleState
        """
        if len(df) < 10:
            return self._current_state
        
        if current_time is None:
            current_time = datetime.now()
        
        # Determine current phase based on time
        current_phase = self._get_time_phase(current_time)
        
        # Identify accumulation range (Asian session)
        acc_high, acc_low = self._identify_accumulation_range(df, current_time)
        
        if acc_high is None or acc_low is None:
            return self._current_state
        
        # Check for manipulation (Judas swing)
        judas = self._detect_judas_swing(df, acc_high, acc_low, current_time)
        
        # Determine expected direction
        expected_dir = self._determine_expected_direction(judas, df)
        
        # Calculate confidence
        confidence = self._calculate_confidence(
            current_phase, acc_high, acc_low, judas, df
        )
        
        self._current_state = AMDCycleState(
            phase=current_phase,
            accumulation_high=acc_high,
            accumulation_low=acc_low,
            manipulation_extreme=judas.extreme_price if judas else None,
            manipulation_direction=judas.direction if judas else None,
            expected_direction=expected_dir,
            phase_start_time=self._get_phase_start(current_phase, current_time),
            confidence=confidence
        )
        
        return self._current_state
    
    def _get_time_phase(self, current_time: datetime) -> AMDPhase:
        """Determine AMD phase based on time."""
        t = current_time.time()
        
        # Handle day boundary
        if t >= self.ACCUMULATION_START or t < self.ACCUMULATION_END:
            return AMDPhase.ACCUMULATION
        elif t < self.MANIPULATION_END:
            return AMDPhase.MANIPULATION
        elif t < self.DISTRIBUTION_END:
            return AMDPhase.DISTRIBUTION
        else:
            return AMDPhase.UNKNOWN
    
    def _identify_accumulation_range(
        self,
        df: pd.DataFrame,
        current_time: datetime
    ) -> Tuple[Optional[float], Optional[float]]:
        """Identify the accumulation (Asian session) range."""
        if 'time' not in df.columns:
            # Fall back to last N candles
            recent = df.tail(20)
            return recent['high'].max(), recent['low'].min()
        
        # Get today's date
        today = current_time.date()
        yesterday = today - timedelta(days=1)
        
        # Asian session is from 7 PM yesterday to 2 AM today
        asian_start = datetime.combine(yesterday, self.ACCUMULATION_START)
        asian_end = datetime.combine(today, self.ACCUMULATION_END)
        
        # Filter for Asian session
        df['datetime'] = pd.to_datetime(df['time'])
        asian_data = df[
            (df['datetime'] >= asian_start) & 
            (df['datetime'] <= asian_end)
        ]
        
        if len(asian_data) < 3:
            return None, None
        
        acc_high = asian_data['high'].max()
        acc_low = asian_data['low'].min()
        
        # Validate range size
        range_pips = (acc_high - acc_low) / self.pip_value
        if range_pips > self.range_threshold_pips * 2:
            logger.warning(f"Accumulation range too large: {range_pips} pips")
        
        return acc_high, acc_low
    
    def _detect_judas_swing(
        self,
        df: pd.DataFrame,
        acc_high: float,
        acc_low: float,
        current_time: datetime
    ) -> Optional[AMDJudasSwing]:
        """Detect Judas swing (manipulation phase)."""
        if 'time' not in df.columns:
            return None
        
        today = current_time.date()
        manipulation_start = datetime.combine(today, self.ACCUMULATION_END)
        manipulation_end = datetime.combine(today, self.MANIPULATION_END)
        
        df['datetime'] = pd.to_datetime(df['time'])
        manip_data = df[
            (df['datetime'] >= manipulation_start) & 
            (df['datetime'] <= manipulation_end)
        ]
        
        if len(manip_data) < 2:
            return None
        
        # Check for break above accumulation high (bearish Judas)
        high_break = manip_data['high'].max()
        if high_break > acc_high:
            pips_beyond = (high_break - acc_high) / self.pip_value
            
            if pips_beyond >= self.manipulation_threshold_pips:
                # Check if price reversed back
                last_close = manip_data['close'].iloc[-1]
                reversal_confirmed = last_close < acc_high
                
                extreme_idx = manip_data['high'].idxmax()
                extreme_time = manip_data.loc[extreme_idx, 'datetime']
                
                return AMDJudasSwing(
                    direction='up',
                    extreme_price=high_break,
                    extreme_time=extreme_time,
                    range_broken='high',
                    reversal_confirmed=reversal_confirmed,
                    pips_beyond_range=pips_beyond
                )
        
        # Check for break below accumulation low (bullish Judas)
        low_break = manip_data['low'].min()
        if low_break < acc_low:
            pips_beyond = (acc_low - low_break) / self.pip_value
            
            if pips_beyond >= self.manipulation_threshold_pips:
                last_close = manip_data['close'].iloc[-1]
                reversal_confirmed = last_close > acc_low
                
                extreme_idx = manip_data['low'].idxmin()
                extreme_time = manip_data.loc[extreme_idx, 'datetime']
                
                return AMDJudasSwing(
                    direction='down',
                    extreme_price=low_break,
                    extreme_time=extreme_time,
                    range_broken='low',
                    reversal_confirmed=reversal_confirmed,
                    pips_beyond_range=pips_beyond
                )
        
        return None
    
    def _determine_expected_direction(
        self,
        judas: Optional[AMDJudasSwing],
        df: pd.DataFrame
    ) -> Optional[str]:
        """Determine expected move direction after manipulation."""
        if judas is None:
            return None
        
        # Judas swing is OPPOSITE to true direction
        if judas.direction == 'up':
            # Bearish Judas = Bullish expected move
            return 'bearish'
        else:
            # Bullish Judas = Bearish expected move
            return 'bullish'
    
    def _calculate_confidence(
        self,
        phase: AMDPhase,
        acc_high: float,
        acc_low: float,
        judas: Optional[AMDJudasSwing],
        df: pd.DataFrame
    ) -> float:
        """Calculate confidence in current AMD analysis."""
        confidence = 0.3  # Base confidence
        
        # Valid accumulation range
        if acc_high and acc_low:
            range_pips = (acc_high - acc_low) / self.pip_value
            if 10 <= range_pips <= self.range_threshold_pips:
                confidence += 0.2
        
        # Judas swing detected
        if judas:
            confidence += 0.2
            
            # Judas confirmed with reversal
            if judas.reversal_confirmed:
                confidence += 0.2
            
            # Strong manipulation (more pips beyond range)
            if judas.pips_beyond_range >= self.manipulation_threshold_pips * 1.5:
                confidence += 0.1
        
        return min(1.0, confidence)
    
    def _get_phase_start(
        self,
        phase: AMDPhase,
        current_time: datetime
    ) -> Optional[datetime]:
        """Get the start time of current phase."""
        today = current_time.date()
        yesterday = today - timedelta(days=1)
        
        if phase == AMDPhase.ACCUMULATION:
            return datetime.combine(yesterday, self.ACCUMULATION_START)
        elif phase == AMDPhase.MANIPULATION:
            return datetime.combine(today, self.ACCUMULATION_END)
        elif phase == AMDPhase.DISTRIBUTION:
            return datetime.combine(today, self.MANIPULATION_END)
        
        return None
    
    def get_trading_recommendation(self) -> Dict[str, Any]:
        """Get trading recommendation based on current AMD state."""
        state = self._current_state
        
        if state.phase == AMDPhase.ACCUMULATION:
            return {
                'action': 'wait',
                'reason': 'In accumulation phase - mark range and wait for manipulation',
                'range_high': state.accumulation_high,
                'range_low': state.accumulation_low
            }
        
        elif state.phase == AMDPhase.MANIPULATION:
            if state.manipulation_extreme and not state.expected_direction:
                return {
                    'action': 'wait',
                    'reason': 'Manipulation in progress - wait for reversal confirmation',
                    'manipulation_extreme': state.manipulation_extreme
                }
            elif state.expected_direction:
                return {
                    'action': 'prepare',
                    'reason': f'Judas swing detected - prepare for {state.expected_direction} entry',
                    'expected_direction': state.expected_direction,
                    'entry_zone': state.accumulation_low if state.expected_direction == 'bullish' else state.accumulation_high
                }
        
        elif state.phase == AMDPhase.DISTRIBUTION:
            if state.expected_direction:
                return {
                    'action': 'trade',
                    'reason': f'Distribution phase - look for {state.expected_direction} entries',
                    'direction': state.expected_direction,
                    'confidence': state.confidence
                }
        
        return {
            'action': 'wait',
            'reason': 'Insufficient data for AMD analysis'
        }


def analyze_amd_cycle(
    df: pd.DataFrame,
    current_time: Optional[datetime] = None,
    pip_value: float = 0.0001
) -> Dict[str, Any]:
    """
    Convenience function to analyze AMD cycle.
    
    Args:
        df: DataFrame with OHLCV data
        current_time: Current time
        pip_value: Pip value for instrument
        
    Returns:
        Dictionary with AMD analysis
    """
    analyzer = AMDCycleAnalyzer(pip_value=pip_value)
    state = analyzer.analyze(df, current_time)
    recommendation = analyzer.get_trading_recommendation()
    
    return {
        'phase': state.phase.value,
        'accumulation_range': {
            'high': state.accumulation_high,
            'low': state.accumulation_low
        },
        'manipulation': {
            'extreme': state.manipulation_extreme,
            'direction': state.manipulation_direction
        },
        'expected_direction': state.expected_direction,
        'confidence': state.confidence,
        'recommendation': recommendation
    }
