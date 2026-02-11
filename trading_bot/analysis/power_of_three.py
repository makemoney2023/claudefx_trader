"""
Power of 3 / AMD (Accumulation, Manipulation, Distribution) Module.

Implements ICT concepts for market cycle detection:
- Accumulation: Smart money building positions
- Manipulation: Fake move to trap retail (Judas swing)
- Distribution: Real move/trend continuation
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple
from enum import Enum
import pandas as pd
import numpy as np

from ..utils.candle_utils import (
    is_bullish_candle,
    is_bearish_candle,
    calculate_body_percentage
)
from ..utils.logging import get_logger

logger = get_logger(__name__)


class MarketPhase(Enum):
    """Market cycle phases according to ICT Power of 3."""
    ACCUMULATION = "accumulation"    # Range/consolidation, smart money building
    MANIPULATION = "manipulation"    # Fake move, stop hunt, Judas swing
    DISTRIBUTION = "distribution"    # Real move, trend continuation
    UNKNOWN = "unknown"


@dataclass
class PhaseAnalysis:
    """Analysis of current market phase."""
    phase: MarketPhase
    confidence: float
    start_index: int
    end_index: Optional[int]
    price_range: Tuple[float, float]
    description: str
    
    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "confidence": float(self.confidence),
            "start_index": int(self.start_index) if self.start_index is not None else None,
            "end_index": int(self.end_index) if self.end_index is not None else None,
            "price_range": (float(self.price_range[0]), float(self.price_range[1])) if self.price_range else None,
            "description": str(self.description)
        }


@dataclass
class JudasSwing:
    """
    Represents a Judas Swing (manipulation move).
    
    A Judas swing is an initial fake move designed to:
    - Trap early traders
    - Sweep liquidity
    - Set up the real move in opposite direction
    """
    direction: str  # 'up' or 'down' - the fake direction
    start_index: int
    end_index: int
    start_price: float
    extreme_price: float  # Highest/lowest point of the fake move
    reversal_confirmed: bool
    liquidity_swept: bool
    
    def to_dict(self) -> dict:
        return {
            "direction": str(self.direction),
            "start_index": int(self.start_index),
            "end_index": int(self.end_index),
            "start_price": float(self.start_price),
            "extreme_price": float(self.extreme_price),
            "reversal_confirmed": bool(self.reversal_confirmed),
            "liquidity_swept": bool(self.liquidity_swept)
        }


@dataclass
class AMDAnalysis:
    """Complete AMD/Power of 3 analysis."""
    current_phase: MarketPhase
    phases: List[PhaseAnalysis]
    judas_swing: Optional[JudasSwing]
    expected_direction: Optional[str]  # Expected direction after manipulation
    
    def to_dict(self) -> dict:
        return {
            "current_phase": self.current_phase.value,
            "phases": [p.to_dict() for p in self.phases],
            "judas_swing": self.judas_swing.to_dict() if self.judas_swing else None,
            "expected_direction": self.expected_direction
        }


class PowerOfThreeAnalyzer:
    """
    Analyzes market phases using ICT Power of 3 concepts.
    
    The Power of 3 describes how smart money operates:
    1. ACCUMULATION: Build positions during consolidation
    2. MANIPULATION: Create false move to trap retail, sweep liquidity
    3. DISTRIBUTION: Real move in intended direction
    
    This cycle repeats on all timeframes.
    """
    
    def __init__(
        self,
        consolidation_bars: int = 10,
        manipulation_threshold: float = 0.5,
        distribution_threshold: float = 1.0
    ):
        """
        Initialize the Power of 3 analyzer.
        
        Args:
            consolidation_bars: Minimum bars for accumulation phase
            manipulation_threshold: ATR multiplier for manipulation move
            distribution_threshold: ATR multiplier for distribution move
        """
        self.consolidation_bars = consolidation_bars
        self.manipulation_threshold = manipulation_threshold
        self.distribution_threshold = distribution_threshold
    
    def analyze(self, df: pd.DataFrame) -> AMDAnalysis:
        """
        Analyze the market for AMD/Power of 3 phases.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            AMDAnalysis with phase identification
        """
        logger.debug(f"Analyzing Power of 3 for {len(df)} candles")
        
        phases = []
        current_phase = MarketPhase.UNKNOWN
        judas_swing = None
        expected_direction = None
        
        # Calculate ATR for move significance
        atr = self._calculate_atr(df)
        
        # Identify consolidation (accumulation) zones
        consolidations = self._find_consolidation_zones(df, atr)
        
        for consol in consolidations:
            phases.append(PhaseAnalysis(
                phase=MarketPhase.ACCUMULATION,
                confidence=consol['confidence'],
                start_index=consol['start'],
                end_index=consol['end'],
                price_range=(consol['low'], consol['high']),
                description="Consolidation/accumulation zone"
            ))
        
        # Look for manipulation (fake breakout / Judas swing)
        judas = self._find_judas_swing(df, consolidations, atr)
        if judas:
            judas_swing = judas
            phases.append(PhaseAnalysis(
                phase=MarketPhase.MANIPULATION,
                confidence=0.7 if judas.reversal_confirmed else 0.5,
                start_index=judas.start_index,
                end_index=judas.end_index,
                price_range=(judas.start_price, judas.extreme_price),
                description=f"Judas swing {judas.direction}"
            ))
            
            # Expected direction is opposite of Judas
            expected_direction = 'bullish' if judas.direction == 'down' else 'bearish'
        
        # Look for distribution (real move)
        distribution = self._find_distribution(df, consolidations, judas_swing, atr)
        if distribution:
            phases.append(PhaseAnalysis(
                phase=MarketPhase.DISTRIBUTION,
                confidence=distribution['confidence'],
                start_index=distribution['start'],
                end_index=distribution.get('end'),
                price_range=(distribution['low'], distribution['high']),
                description=f"Distribution move {distribution['direction']}"
            ))
        
        # Determine current phase
        current_phase = self._determine_current_phase(df, phases, atr)
        
        logger.info(f"AMD analysis complete: Current phase = {current_phase.value}")
        
        return AMDAnalysis(
            current_phase=current_phase,
            phases=phases,
            judas_swing=judas_swing,
            expected_direction=expected_direction
        )
    
    def _find_consolidation_zones(
        self,
        df: pd.DataFrame,
        atr: pd.Series
    ) -> List[dict]:
        """Find consolidation/accumulation zones."""
        consolidations = []
        
        if len(df) < self.consolidation_bars:
            return consolidations
        
        # Rolling range calculation
        for i in range(self.consolidation_bars, len(df)):
            window = df.iloc[i - self.consolidation_bars:i]
            range_high = window['high'].max()
            range_low = window['low'].min()
            range_size = range_high - range_low
            
            avg_atr = atr.iloc[i - self.consolidation_bars:i].mean()
            
            # Consolidation: range is small relative to ATR
            if avg_atr > 0 and range_size < avg_atr * 2:
                consolidations.append({
                    'start': i - self.consolidation_bars,
                    'end': i,
                    'high': range_high,
                    'low': range_low,
                    'confidence': min(1.0, (avg_atr * 2 - range_size) / avg_atr)
                })
        
        # Merge overlapping consolidations
        return self._merge_consolidations(consolidations)
    
    def _merge_consolidations(self, consolidations: List[dict]) -> List[dict]:
        """Merge overlapping consolidation zones."""
        if not consolidations:
            return []
        
        merged = [consolidations[0]]
        
        for current in consolidations[1:]:
            last = merged[-1]
            
            # If overlapping, extend the last one
            if current['start'] <= last['end']:
                last['end'] = max(last['end'], current['end'])
                last['high'] = max(last['high'], current['high'])
                last['low'] = min(last['low'], current['low'])
                last['confidence'] = (last['confidence'] + current['confidence']) / 2
            else:
                merged.append(current)
        
        return merged
    
    def _find_judas_swing(
        self,
        df: pd.DataFrame,
        consolidations: List[dict],
        atr: pd.Series
    ) -> Optional[JudasSwing]:
        """
        Find Judas swing (manipulation move).
        
        A Judas swing is a fake breakout from consolidation
        that quickly reverses.
        """
        if not consolidations:
            return None
        
        # Look at the most recent consolidation
        last_consol = consolidations[-1]
        
        if last_consol['end'] >= len(df) - 3:
            return None  # Not enough data after consolidation
        
        post_consol = df.iloc[last_consol['end']:]
        
        if len(post_consol) < 3:
            return None
        
        consol_high = last_consol['high']
        consol_low = last_consol['low']
        avg_atr = atr.iloc[last_consol['end']:].mean() if len(atr) > last_consol['end'] else 0
        
        # Check for upward fake (Judas up, then reversal down)
        if post_consol['high'].max() > consol_high:
            extreme_idx = post_consol['high'].idxmax()
            if isinstance(extreme_idx, int):
                extreme_pos = extreme_idx
            else:
                extreme_pos = df.index.get_loc(extreme_idx)
            
            # Check for reversal after
            post_extreme = df.iloc[extreme_pos:]
            if len(post_extreme) >= 2:
                reversal = post_extreme['close'].iloc[-1] < consol_high
                
                return JudasSwing(
                    direction='up',
                    start_index=last_consol['end'],
                    end_index=extreme_pos,
                    start_price=df.iloc[last_consol['end']]['close'],
                    extreme_price=post_consol['high'].max(),
                    reversal_confirmed=reversal,
                    liquidity_swept=post_consol['high'].max() > consol_high
                )
        
        # Check for downward fake (Judas down, then reversal up)
        if post_consol['low'].min() < consol_low:
            extreme_idx = post_consol['low'].idxmin()
            if isinstance(extreme_idx, int):
                extreme_pos = extreme_idx
            else:
                extreme_pos = df.index.get_loc(extreme_idx)
            
            post_extreme = df.iloc[extreme_pos:]
            if len(post_extreme) >= 2:
                reversal = post_extreme['close'].iloc[-1] > consol_low
                
                return JudasSwing(
                    direction='down',
                    start_index=last_consol['end'],
                    end_index=extreme_pos,
                    start_price=df.iloc[last_consol['end']]['close'],
                    extreme_price=post_consol['low'].min(),
                    reversal_confirmed=reversal,
                    liquidity_swept=post_consol['low'].min() < consol_low
                )
        
        return None
    
    def _find_distribution(
        self,
        df: pd.DataFrame,
        consolidations: List[dict],
        judas: Optional[JudasSwing],
        atr: pd.Series
    ) -> Optional[dict]:
        """Find distribution (real move) phase."""
        if not judas or not judas.reversal_confirmed:
            return None
        
        # Distribution starts after Judas reversal
        start_idx = judas.end_index
        if start_idx >= len(df) - 1:
            return None
        
        post_judas = df.iloc[start_idx:]
        avg_atr = atr.iloc[start_idx:].mean() if len(atr) > start_idx else 0
        
        # Determine direction (opposite of Judas)
        if judas.direction == 'up':
            # Expect bearish distribution
            move_size = judas.extreme_price - post_judas['low'].min()
            if move_size > avg_atr * self.distribution_threshold:
                return {
                    'start': start_idx,
                    'end': len(df) - 1,
                    'high': judas.extreme_price,
                    'low': post_judas['low'].min(),
                    'direction': 'bearish',
                    'confidence': min(1.0, move_size / (avg_atr * 2))
                }
        else:
            # Expect bullish distribution
            move_size = post_judas['high'].max() - judas.extreme_price
            if move_size > avg_atr * self.distribution_threshold:
                return {
                    'start': start_idx,
                    'end': len(df) - 1,
                    'high': post_judas['high'].max(),
                    'low': judas.extreme_price,
                    'direction': 'bullish',
                    'confidence': min(1.0, move_size / (avg_atr * 2))
                }
        
        return None
    
    def _determine_current_phase(
        self,
        df: pd.DataFrame,
        phases: List[PhaseAnalysis],
        atr: pd.Series
    ) -> MarketPhase:
        """Determine the current market phase."""
        if not phases:
            return MarketPhase.UNKNOWN
        
        # Get the most recent phase
        recent_phases = sorted(phases, key=lambda x: x.end_index or len(df), reverse=True)
        
        if recent_phases:
            last_phase = recent_phases[0]
            
            # If the last phase is ongoing (no end or end at last candle)
            if last_phase.end_index is None or last_phase.end_index >= len(df) - 2:
                return last_phase.phase
        
        # Default based on recent price action
        recent_df = df.tail(10)
        range_size = recent_df['high'].max() - recent_df['low'].min()
        avg_atr = atr.tail(10).mean()
        
        if avg_atr > 0 and range_size < avg_atr * 1.5:
            return MarketPhase.ACCUMULATION
        elif range_size > avg_atr * 2.5:
            return MarketPhase.DISTRIBUTION
        else:
            return MarketPhase.MANIPULATION
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    def detect_institutional_candle(
        self,
        df: pd.DataFrame,
        lookback: int = 1
    ) -> Optional[dict]:
        """
        Detect institutional candles (large displacement candles).
        
        Institutional candles have:
        - Large body (>70% of range)
        - Strong momentum
        - Often occur during kill zones
        """
        if len(df) < lookback:
            return None
        
        atr = self._calculate_atr(df)
        
        for i in range(-lookback, 0):
            candle = df.iloc[i]
            candle_atr = atr.iloc[i] if i < len(atr) and not pd.isna(atr.iloc[i]) else 0
            
            range_size = candle['high'] - candle['low']
            body_pct = calculate_body_percentage(
                candle['open'], candle['high'], candle['low'], candle['close']
            )
            
            # Institutional candle: large body, significant range
            if body_pct >= 0.7 and (candle_atr == 0 or range_size > candle_atr * 1.5):
                direction = 'bullish' if is_bullish_candle(candle['open'], candle['close']) else 'bearish'
                
                return {
                    'index': len(df) + i,
                    'direction': direction,
                    'body_percentage': body_pct,
                    'range_vs_atr': range_size / candle_atr if candle_atr > 0 else 0,
                    'open': candle['open'],
                    'high': candle['high'],
                    'low': candle['low'],
                    'close': candle['close']
                }
        
        return None
