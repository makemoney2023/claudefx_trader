"""
Order Block Detection Module.

Implements ICT Order Block identification:
- Bullish Order Blocks (last down-candle before impulse up)
- Bearish Order Blocks (last up-candle before impulse down)
- Breaker Blocks (invalidated order blocks that become opposing zones)
- Mitigation Blocks
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

from ..utils.candle_utils import (
    is_bullish_candle,
    is_bearish_candle,
    calculate_body_percentage,
    is_impulsive_move
)
from ..utils.logging import get_logger

logger = get_logger(__name__)


class OrderBlockType(Enum):
    """Types of Order Blocks."""
    BULLISH = "bullish"      # Demand zone - last down-candle before up move
    BEARISH = "bearish"      # Supply zone - last up-candle before down move
    BREAKER_BULLISH = "breaker_bullish"  # Failed bearish OB becomes bullish
    BREAKER_BEARISH = "breaker_bearish"  # Failed bullish OB becomes bearish


class OrderBlockStatus(Enum):
    """Status of an Order Block."""
    VALID = "valid"          # OB hasn't been tested
    TESTED = "tested"        # Price has returned to OB but held
    MITIGATED = "mitigated"  # OB has been filled/invalidated
    BREAKER = "breaker"      # OB has become a breaker block


@dataclass
class OrderBlock:
    """
    Represents an Order Block.
    
    Order Blocks are price zones where institutional orders were
    placed, visible as the last opposing candle before an impulse move.
    """
    type: OrderBlockType
    index: int                    # Index of the order block candle
    top: float                    # Upper boundary (candle high or body high)
    bottom: float                 # Lower boundary (candle low or body low)
    status: OrderBlockStatus = OrderBlockStatus.VALID
    timestamp: Optional[pd.Timestamp] = None
    
    # Candle information
    open_price: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    
    # Impulse move information
    impulse_start_idx: int = 0
    impulse_end_idx: int = 0
    impulse_size: float = 0.0
    
    # Quality metrics
    body_percentage: float = 0.0
    strength: float = 0.0         # 0-1 based on impulse strength
    volume_score: float = 0.0     # 0-1 based on impulse volume vs prior average
    
    @property
    def midpoint(self) -> float:
        """Get the midpoint of the order block zone."""
        return (self.top + self.bottom) / 2
    
    @property
    def zone_size(self) -> float:
        """Get the size of the order block zone."""
        return self.top - self.bottom
    
    @property
    def is_valid(self) -> bool:
        """Check if order block is still valid."""
        return self.status in [OrderBlockStatus.VALID, OrderBlockStatus.TESTED]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "index": int(self.index),
            "top": float(self.top),
            "bottom": float(self.bottom),
            "midpoint": float(self.midpoint),
            "zone_size": float(self.zone_size),
            "status": self.status.value,
            "strength": float(self.strength),
            "impulse_size": float(self.impulse_size) if self.impulse_size else None,
            "volume_score": float(self.volume_score)
        }


@dataclass
class OrderBlockAnalysis:
    """Complete Order Block analysis result."""
    bullish_obs: List[OrderBlock] = field(default_factory=list)
    bearish_obs: List[OrderBlock] = field(default_factory=list)
    breaker_blocks: List[OrderBlock] = field(default_factory=list)
    active_obs: List[OrderBlock] = field(default_factory=list)
    
    @property
    def total_obs(self) -> int:
        return len(self.bullish_obs) + len(self.bearish_obs)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "bullish_obs": [ob.to_dict() for ob in self.bullish_obs],
            "bearish_obs": [ob.to_dict() for ob in self.bearish_obs],
            "breaker_blocks": [ob.to_dict() for ob in self.breaker_blocks],
            "active_obs": [ob.to_dict() for ob in self.active_obs],
            "total_obs": self.total_obs
        }


class OrderBlockDetector:
    """
    Detects Order Blocks in price data.
    
    ICT Order Block Definition:
    - Bullish OB: The last bearish (down) candle before a significant bullish move
    - Bearish OB: The last bullish (up) candle before a significant bearish move
    
    The impulse move must be strong enough to indicate institutional involvement.
    """
    
    def __init__(
        self,
        min_impulse_candles: int = 2,
        min_body_percentage: float = 0.5,
        use_body_zone: bool = True,
        check_mitigation: bool = True
    ):
        """
        Initialize the Order Block detector.
        
        Args:
            min_impulse_candles: Minimum candles in impulse move
            min_body_percentage: Minimum body % for OB candle
            use_body_zone: Use candle body (True) or full range (False) for zone
            check_mitigation: Check if OBs have been mitigated
        """
        self.min_impulse_candles = min_impulse_candles
        self.min_body_percentage = min_body_percentage
        self.use_body_zone = use_body_zone
        self.check_mitigation = check_mitigation
    
    def detect(self, df: pd.DataFrame) -> OrderBlockAnalysis:
        """
        Detect all Order Blocks in the price data.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            OrderBlockAnalysis object with all detected OBs
        """
        # Exclude the current still-forming candle from analysis
        from . import exclude_forming_candle
        df = exclude_forming_candle(df)
        
        logger.debug(f"Detecting Order Blocks in {len(df)} candles")
        
        bullish_obs = []
        bearish_obs = []
        
        # Find impulse moves first
        impulse_moves = self._find_impulse_moves(df)
        
        for impulse in impulse_moves:
            ob = self._create_order_block(df, impulse)
            if ob:
                if ob.type == OrderBlockType.BULLISH:
                    bullish_obs.append(ob)
                else:
                    bearish_obs.append(ob)
        
        # Check mitigation status
        if self.check_mitigation:
            bullish_obs = self._update_mitigation_status(bullish_obs, df)
            bearish_obs = self._update_mitigation_status(bearish_obs, df)
        
        # Identify breaker blocks
        breaker_blocks = self._identify_breaker_blocks(bullish_obs + bearish_obs, df)
        
        # Collect active OBs
        active_obs = [ob for ob in bullish_obs + bearish_obs 
                      if ob.status in [OrderBlockStatus.VALID, OrderBlockStatus.TESTED]]
        
        logger.info(f"OB detection complete: {len(bullish_obs)} bullish, {len(bearish_obs)} bearish, {len(breaker_blocks)} breakers")
        
        return OrderBlockAnalysis(
            bullish_obs=bullish_obs,
            bearish_obs=bearish_obs,
            breaker_blocks=breaker_blocks,
            active_obs=active_obs
        )
    
    def _find_impulse_moves(self, df: pd.DataFrame) -> List[dict]:
        """
        Find significant impulse moves in price data.
        
        Returns list of dicts with impulse information:
        - direction: 'bullish' or 'bearish'
        - start_idx: Index where impulse starts
        - end_idx: Index where impulse ends
        - size: Price movement size
        """
        impulses = []
        
        # Calculate ATR for significance threshold
        atr = self._calculate_atr(df)
        
        i = 0
        while i < len(df) - self.min_impulse_candles:
            # Look for consecutive directional candles
            bullish_streak = 0
            bearish_streak = 0
            streak_start = i
            
            # Count bullish streak
            j = i
            while j < len(df):
                row = df.iloc[j]
                if is_bullish_candle(row['open'], row['close']):
                    body_pct = calculate_body_percentage(row['open'], row['high'], row['low'], row['close'])
                    if body_pct >= self.min_body_percentage:
                        bullish_streak += 1
                        j += 1
                    else:
                        break
                else:
                    break
            
            if bullish_streak >= self.min_impulse_candles:
                impulse_size = df.iloc[j-1]['high'] - df.iloc[streak_start]['low']
                avg_atr = atr.iloc[streak_start:j].mean() if not atr.iloc[streak_start:j].isna().all() else 0
                
                if impulse_size > avg_atr * 1.5 or avg_atr == 0:
                    # Compute impulse volume ratio
                    impulse_volume_ratio = 0.0
                    if 'volume' in df.columns:
                        impulse_vol = df['volume'].iloc[streak_start:j].mean()
                        prior_start = max(0, streak_start - 20)
                        prior_vol = df['volume'].iloc[prior_start:streak_start].mean() if streak_start > 0 else 0
                        if prior_vol > 0:
                            impulse_volume_ratio = float(impulse_vol / prior_vol)
                    
                    impulses.append({
                        'direction': 'bullish',
                        'start_idx': streak_start,
                        'end_idx': j - 1,
                        'size': impulse_size,
                        'impulse_volume_ratio': impulse_volume_ratio
                    })
                i = j
                continue
            
            # Count bearish streak
            j = i
            while j < len(df):
                row = df.iloc[j]
                if is_bearish_candle(row['open'], row['close']):
                    body_pct = calculate_body_percentage(row['open'], row['high'], row['low'], row['close'])
                    if body_pct >= self.min_body_percentage:
                        bearish_streak += 1
                        j += 1
                    else:
                        break
                else:
                    break
            
            if bearish_streak >= self.min_impulse_candles:
                impulse_size = df.iloc[streak_start]['high'] - df.iloc[j-1]['low']
                avg_atr = atr.iloc[streak_start:j].mean() if not atr.iloc[streak_start:j].isna().all() else 0
                
                if impulse_size > avg_atr * 1.5 or avg_atr == 0:
                    # Compute impulse volume ratio
                    impulse_volume_ratio = 0.0
                    if 'volume' in df.columns:
                        impulse_vol = df['volume'].iloc[streak_start:j].mean()
                        prior_start = max(0, streak_start - 20)
                        prior_vol = df['volume'].iloc[prior_start:streak_start].mean() if streak_start > 0 else 0
                        if prior_vol > 0:
                            impulse_volume_ratio = float(impulse_vol / prior_vol)
                    
                    impulses.append({
                        'direction': 'bearish',
                        'start_idx': streak_start,
                        'end_idx': j - 1,
                        'size': impulse_size,
                        'impulse_volume_ratio': impulse_volume_ratio
                    })
                i = j
                continue
            
            i += 1
        
        return impulses
    
    def _create_order_block(self, df: pd.DataFrame, impulse: dict) -> Optional[OrderBlock]:
        """Create an Order Block from an impulse move."""
        start_idx = impulse['start_idx']
        direction = impulse['direction']
        
        # Find the last opposing candle before the impulse
        ob_idx = None
        for i in range(start_idx - 1, max(start_idx - 10, -1), -1):
            if i < 0:
                break
            row = df.iloc[i]
            
            if direction == 'bullish' and is_bearish_candle(row['open'], row['close']):
                ob_idx = i
                break
            elif direction == 'bearish' and is_bullish_candle(row['open'], row['close']):
                ob_idx = i
                break
        
        if ob_idx is None:
            return None
        
        ob_candle = df.iloc[ob_idx]
        timestamp = df.index[ob_idx] if isinstance(df.index, pd.DatetimeIndex) else None
        
        # Determine zone boundaries
        if self.use_body_zone:
            top = max(ob_candle['open'], ob_candle['close'])
            bottom = min(ob_candle['open'], ob_candle['close'])
        else:
            top = ob_candle['high']
            bottom = ob_candle['low']
        
        body_pct = calculate_body_percentage(
            ob_candle['open'], ob_candle['high'], 
            ob_candle['low'], ob_candle['close']
        )
        
        # Calculate strength based on impulse size
        strength = min(impulse['size'] / (top - bottom + 0.0001), 5) / 5  # Normalize to 0-1
        
        # Volume score: normalise impulse_volume_ratio to 0-1 range
        # ratio of 1.0 = same as prior -> 0.0 score
        # ratio of 2.0+ -> 1.0 score
        raw_vol_ratio = impulse.get('impulse_volume_ratio', 0.0)
        volume_score = min(1.0, max(0.0, (raw_vol_ratio - 1.0))) if raw_vol_ratio > 0 else 0.0
        
        return OrderBlock(
            type=OrderBlockType.BULLISH if direction == 'bullish' else OrderBlockType.BEARISH,
            index=ob_idx,
            top=top,
            bottom=bottom,
            timestamp=timestamp,
            open_price=ob_candle['open'],
            high=ob_candle['high'],
            low=ob_candle['low'],
            close=ob_candle['close'],
            impulse_start_idx=impulse['start_idx'],
            impulse_end_idx=impulse['end_idx'],
            impulse_size=impulse['size'],
            body_percentage=body_pct,
            strength=strength,
            volume_score=volume_score
        )
    
    def _update_mitigation_status(
        self,
        order_blocks: List[OrderBlock],
        df: pd.DataFrame
    ) -> List[OrderBlock]:
        """Update the mitigation status of Order Blocks."""
        for ob in order_blocks:
            # Look at candles after the impulse move
            if ob.impulse_end_idx >= len(df) - 1:
                continue
                
            subsequent_df = df.iloc[ob.impulse_end_idx + 1:]
            
            for _, candle in subsequent_df.iterrows():
                if ob.type == OrderBlockType.BULLISH:
                    # Bullish OB is mitigated if price closes below the bottom
                    if candle['close'] < ob.bottom:
                        ob.status = OrderBlockStatus.MITIGATED
                        break
                    elif candle['low'] <= ob.top:
                        ob.status = OrderBlockStatus.TESTED
                else:
                    # Bearish OB is mitigated if price closes above the top
                    if candle['close'] > ob.top:
                        ob.status = OrderBlockStatus.MITIGATED
                        break
                    elif candle['high'] >= ob.bottom:
                        ob.status = OrderBlockStatus.TESTED
        
        return order_blocks
    
    def _identify_breaker_blocks(
        self,
        order_blocks: List[OrderBlock],
        df: pd.DataFrame
    ) -> List[OrderBlock]:
        """
        Identify Order Blocks that have become Breaker Blocks.
        
        A Breaker Block forms when an OB is mitigated and then
        price returns to it from the opposite direction.
        """
        breaker_blocks = []
        
        for ob in order_blocks:
            if ob.status == OrderBlockStatus.MITIGATED:
                # Check if price has returned to the zone from the opposite side
                subsequent_idx = self._find_subsequent_return(ob, df)
                if subsequent_idx:
                    breaker = OrderBlock(
                        type=OrderBlockType.BREAKER_BEARISH if ob.type == OrderBlockType.BULLISH else OrderBlockType.BREAKER_BULLISH,
                        index=ob.index,
                        top=ob.top,
                        bottom=ob.bottom,
                        status=OrderBlockStatus.BREAKER,
                        timestamp=ob.timestamp,
                        open_price=ob.open_price,
                        high=ob.high,
                        low=ob.low,
                        close=ob.close,
                        strength=ob.strength * 0.7  # Breakers are slightly weaker
                    )
                    breaker_blocks.append(breaker)
        
        return breaker_blocks
    
    def _find_subsequent_return(self, ob: OrderBlock, df: pd.DataFrame) -> Optional[int]:
        """Find if price returned to OB zone after mitigation."""
        if ob.impulse_end_idx >= len(df) - 5:
            return None
        
        subsequent_df = df.iloc[ob.impulse_end_idx + 1:]
        mitigation_found = False
        
        for i, (idx, candle) in enumerate(subsequent_df.iterrows()):
            if not mitigation_found:
                if ob.type == OrderBlockType.BULLISH and candle['close'] < ob.bottom:
                    mitigation_found = True
                elif ob.type == OrderBlockType.BEARISH and candle['close'] > ob.top:
                    mitigation_found = True
            else:
                # Look for return to zone
                if candle['low'] <= ob.top and candle['high'] >= ob.bottom:
                    return ob.impulse_end_idx + 1 + i
        
        return None
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR for impulse significance."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    def find_nearest_ob(
        self,
        order_blocks: List[OrderBlock],
        current_price: float,
        ob_type: Optional[OrderBlockType] = None
    ) -> Optional[OrderBlock]:
        """Find the nearest valid Order Block to current price."""
        valid_obs = [ob for ob in order_blocks if ob.is_valid]
        
        if ob_type:
            valid_obs = [ob for ob in valid_obs if ob.type == ob_type]
        
        if not valid_obs:
            return None
        
        def distance(ob):
            return abs(current_price - ob.midpoint)
        
        return min(valid_obs, key=distance)
