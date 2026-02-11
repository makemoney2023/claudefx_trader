"""
Liquidity Analysis Module.

Implements ICT liquidity concepts:
- Buy-side liquidity (BSL) - stops above swing highs
- Sell-side liquidity (SSL) - stops below swing lows
- Equal highs/lows (EQH/EQL) identification
- Liquidity sweep/grab detection
- Inducement recognition
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np

from ..utils.candle_utils import find_swing_highs, find_swing_lows, find_equal_highs, find_equal_lows
from ..utils.logging import get_logger

logger = get_logger(__name__)


class LiquidityType(Enum):
    """Types of liquidity pools."""
    BSL = "buy_side_liquidity"      # Above swing highs
    SSL = "sell_side_liquidity"     # Below swing lows
    EQH = "equal_highs"             # Cluster of equal highs
    EQL = "equal_lows"              # Cluster of equal lows


class LiquidityStatus(Enum):
    """Status of a liquidity pool."""
    UNTAPPED = "untapped"           # Liquidity hasn't been taken
    SWEPT = "swept"                 # Liquidity has been swept/grabbed
    PARTIAL = "partial"             # Partially swept


@dataclass
class LiquidityPool:
    """
    Represents a liquidity pool in the market.
    
    Liquidity pools are areas where stop-loss orders cluster,
    typically above swing highs (BSL) or below swing lows (SSL).
    """
    type: LiquidityType
    price: float                    # Price level of the liquidity
    index: int                      # Index where liquidity formed
    status: LiquidityStatus = LiquidityStatus.UNTAPPED
    timestamp: Optional[pd.Timestamp] = None
    
    # For equal highs/lows
    touch_count: int = 1            # Number of times level was touched
    
    # Sweep information
    sweep_index: Optional[int] = None
    sweep_price: Optional[float] = None
    
    @property
    def is_valid(self) -> bool:
        """Check if liquidity is still available."""
        return self.status == LiquidityStatus.UNTAPPED
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "price": float(self.price),
            "index": int(self.index),
            "status": self.status.value,
            "touch_count": int(self.touch_count),
            "swept": bool(self.status == LiquidityStatus.SWEPT)
        }


@dataclass
class LiquiditySweep:
    """Represents a liquidity sweep event."""
    liquidity_pool: LiquidityPool
    sweep_index: int
    sweep_price: float
    reversal_detected: bool = False
    timestamp: Optional[pd.Timestamp] = None
    volume_spike: bool = False  # True when sweep candle volume > 2x 20-bar average
    
    def to_dict(self) -> dict:
        return {
            "type": self.liquidity_pool.type.value,
            "pool_price": float(self.liquidity_pool.price),
            "sweep_price": float(self.sweep_price),
            "sweep_index": int(self.sweep_index),
            "reversal_detected": bool(self.reversal_detected),
            "volume_spike": bool(self.volume_spike)
        }


@dataclass
class LiquidityAnalysis:
    """Complete liquidity analysis result."""
    bsl_pools: List[LiquidityPool] = field(default_factory=list)  # Buy-side liquidity
    ssl_pools: List[LiquidityPool] = field(default_factory=list)  # Sell-side liquidity
    equal_highs: List[LiquidityPool] = field(default_factory=list)
    equal_lows: List[LiquidityPool] = field(default_factory=list)
    recent_sweeps: List[LiquiditySweep] = field(default_factory=list)
    active_pools: List[LiquidityPool] = field(default_factory=list)
    
    # Key levels
    nearest_bsl: Optional[float] = None
    nearest_ssl: Optional[float] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "bsl_pools": [p.to_dict() for p in self.bsl_pools],
            "ssl_pools": [p.to_dict() for p in self.ssl_pools],
            "equal_highs": [p.to_dict() for p in self.equal_highs],
            "equal_lows": [p.to_dict() for p in self.equal_lows],
            "recent_sweeps": [s.to_dict() for s in self.recent_sweeps],
            "active_pools": [p.to_dict() for p in self.active_pools],
            "nearest_bsl": float(self.nearest_bsl) if self.nearest_bsl else None,
            "nearest_ssl": float(self.nearest_ssl) if self.nearest_ssl else None
        }


class LiquidityMapper:
    """
    Maps liquidity pools in the market.
    
    Identifies where stop-loss orders likely cluster:
    - Above swing highs (buy stops = BSL)
    - Below swing lows (sell stops = SSL)
    - At equal highs/lows (double/triple tops/bottoms)
    
    Also detects when these pools are swept (liquidity grabs).
    """
    
    def __init__(
        self,
        swing_lookback: int = 5,
        equal_tolerance_pips: float = 5.0,
        pip_value: float = 0.0001,
        sweep_threshold_pips: float = 2.0
    ):
        """
        Initialize the liquidity mapper.
        
        Args:
            swing_lookback: Bars to look back/forward for swing detection
            equal_tolerance_pips: Pip tolerance for equal highs/lows
            pip_value: Value of one pip
            sweep_threshold_pips: Pips beyond level to confirm sweep
        """
        self.swing_lookback = swing_lookback
        self.equal_tolerance_pips = equal_tolerance_pips
        self.pip_value = pip_value
        self.sweep_threshold_pips = sweep_threshold_pips
    
    def analyze(self, df: pd.DataFrame, current_price: Optional[float] = None) -> LiquidityAnalysis:
        """
        Perform complete liquidity analysis.
        
        Args:
            df: DataFrame with OHLCV data
            current_price: Optional current price for nearest calculations
            
        Returns:
            LiquidityAnalysis object with all findings
        """
        # Exclude the current still-forming candle from analysis
        from . import exclude_forming_candle
        df = exclude_forming_candle(df)
        
        logger.debug(f"Analyzing liquidity in {len(df)} candles")
        
        # Find swing points
        swing_highs = find_swing_highs(df, self.swing_lookback, self.swing_lookback)
        swing_lows = find_swing_lows(df, self.swing_lookback, self.swing_lookback)
        
        # Create liquidity pools from swing points
        bsl_pools = self._create_bsl_pools(swing_highs, df)
        ssl_pools = self._create_ssl_pools(swing_lows, df)
        
        # Find equal highs/lows
        equal_highs = self._find_equal_highs(swing_highs, df)
        equal_lows = self._find_equal_lows(swing_lows, df)
        
        # Check for sweeps
        all_pools = bsl_pools + ssl_pools + equal_highs + equal_lows
        all_pools, recent_sweeps = self._check_sweeps(all_pools, df)
        
        # Separate updated pools
        bsl_pools = [p for p in all_pools if p.type == LiquidityType.BSL]
        ssl_pools = [p for p in all_pools if p.type == LiquidityType.SSL]
        equal_highs = [p for p in all_pools if p.type == LiquidityType.EQH]
        equal_lows = [p for p in all_pools if p.type == LiquidityType.EQL]
        
        # Get active pools
        active_pools = [p for p in all_pools if p.status == LiquidityStatus.UNTAPPED]
        
        # Calculate nearest levels
        if current_price is None:
            current_price = df.iloc[-1]['close']
        
        nearest_bsl = self._find_nearest_above(bsl_pools + equal_highs, current_price)
        nearest_ssl = self._find_nearest_below(ssl_pools + equal_lows, current_price)
        
        logger.info(f"Liquidity analysis complete: {len(bsl_pools)} BSL, {len(ssl_pools)} SSL, {len(recent_sweeps)} sweeps")
        
        return LiquidityAnalysis(
            bsl_pools=bsl_pools,
            ssl_pools=ssl_pools,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
            recent_sweeps=recent_sweeps,
            active_pools=active_pools,
            nearest_bsl=nearest_bsl,
            nearest_ssl=nearest_ssl
        )
    
    def _create_bsl_pools(
        self,
        swing_highs: List[Tuple[int, float]],
        df: pd.DataFrame
    ) -> List[LiquidityPool]:
        """Create buy-side liquidity pools from swing highs."""
        pools = []
        for idx, price in swing_highs:
            timestamp = df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None
            pools.append(LiquidityPool(
                type=LiquidityType.BSL,
                price=price,
                index=idx,
                timestamp=timestamp
            ))
        return pools
    
    def _create_ssl_pools(
        self,
        swing_lows: List[Tuple[int, float]],
        df: pd.DataFrame
    ) -> List[LiquidityPool]:
        """Create sell-side liquidity pools from swing lows."""
        pools = []
        for idx, price in swing_lows:
            timestamp = df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None
            pools.append(LiquidityPool(
                type=LiquidityType.SSL,
                price=price,
                index=idx,
                timestamp=timestamp
            ))
        return pools
    
    def _find_equal_highs(
        self,
        swing_highs: List[Tuple[int, float]],
        df: pd.DataFrame
    ) -> List[LiquidityPool]:
        """Find equal highs (clusters of similar swing high prices)."""
        equal_clusters = find_equal_highs(
            swing_highs,
            self.equal_tolerance_pips,
            self.pip_value
        )
        
        pools = []
        for cluster in equal_clusters:
            avg_price = np.mean([price for _, price in cluster])
            first_idx = min(idx for idx, _ in cluster)
            timestamp = df.index[first_idx] if isinstance(df.index, pd.DatetimeIndex) else None
            
            pools.append(LiquidityPool(
                type=LiquidityType.EQH,
                price=avg_price,
                index=first_idx,
                timestamp=timestamp,
                touch_count=len(cluster)
            ))
        
        return pools
    
    def _find_equal_lows(
        self,
        swing_lows: List[Tuple[int, float]],
        df: pd.DataFrame
    ) -> List[LiquidityPool]:
        """Find equal lows (clusters of similar swing low prices)."""
        equal_clusters = find_equal_lows(
            swing_lows,
            self.equal_tolerance_pips,
            self.pip_value
        )
        
        pools = []
        for cluster in equal_clusters:
            avg_price = np.mean([price for _, price in cluster])
            first_idx = min(idx for idx, _ in cluster)
            timestamp = df.index[first_idx] if isinstance(df.index, pd.DatetimeIndex) else None
            
            pools.append(LiquidityPool(
                type=LiquidityType.EQL,
                price=avg_price,
                index=first_idx,
                timestamp=timestamp,
                touch_count=len(cluster)
            ))
        
        return pools
    
    def _check_sweeps(
        self,
        pools: List[LiquidityPool],
        df: pd.DataFrame
    ) -> Tuple[List[LiquidityPool], List[LiquiditySweep]]:
        """
        Check if liquidity pools have been swept.
        
        A sweep occurs when price briefly breaks through a liquidity level
        before reversing.
        """
        sweeps = []
        sweep_threshold = self.sweep_threshold_pips * self.pip_value
        
        for pool in pools:
            if pool.index >= len(df) - 1:
                continue
            
            subsequent_df = df.iloc[pool.index + 1:]
            
            for i, (idx, candle) in enumerate(subsequent_df.iterrows()):
                actual_idx = pool.index + 1 + i
                
                if pool.type in [LiquidityType.BSL, LiquidityType.EQH]:
                    # Check if price swept above
                    if candle['high'] > pool.price + sweep_threshold:
                        pool.status = LiquidityStatus.SWEPT
                        pool.sweep_index = actual_idx
                        pool.sweep_price = candle['high']
                        
                        # Check for reversal (price closed below the pool)
                        reversal = candle['close'] < pool.price
                        
                        # Check for volume spike on sweep candle
                        vol_spike = self._check_volume_spike(df, actual_idx)
                        
                        timestamp = idx if isinstance(idx, pd.Timestamp) else None
                        sweeps.append(LiquiditySweep(
                            liquidity_pool=pool,
                            sweep_index=actual_idx,
                            sweep_price=candle['high'],
                            reversal_detected=reversal,
                            timestamp=timestamp,
                            volume_spike=vol_spike
                        ))
                        break
                
                elif pool.type in [LiquidityType.SSL, LiquidityType.EQL]:
                    # Check if price swept below
                    if candle['low'] < pool.price - sweep_threshold:
                        pool.status = LiquidityStatus.SWEPT
                        pool.sweep_index = actual_idx
                        pool.sweep_price = candle['low']
                        
                        # Check for reversal (price closed above the pool)
                        reversal = candle['close'] > pool.price
                        
                        # Check for volume spike on sweep candle
                        vol_spike = self._check_volume_spike(df, actual_idx)
                        
                        timestamp = idx if isinstance(idx, pd.Timestamp) else None
                        sweeps.append(LiquiditySweep(
                            liquidity_pool=pool,
                            sweep_index=actual_idx,
                            sweep_price=candle['low'],
                            reversal_detected=reversal,
                            timestamp=timestamp,
                            volume_spike=vol_spike
                        ))
                        break
        
        return pools, sweeps
    
    def _check_volume_spike(self, df: pd.DataFrame, candle_idx: int) -> bool:
        """
        Check if the candle at *candle_idx* has a volume spike (> 2x 20-bar avg).
        
        Returns False if volume data is unavailable.
        """
        if 'volume' not in df.columns or candle_idx < 0 or candle_idx >= len(df):
            return False
        try:
            candle_vol = float(df['volume'].iloc[candle_idx])
            start = max(0, candle_idx - 20)
            if start >= candle_idx:
                return False
            avg_vol = float(df['volume'].iloc[start:candle_idx].mean())
            return avg_vol > 0 and candle_vol > avg_vol * 2.0
        except Exception:
            return False
    
    def _find_nearest_above(
        self,
        pools: List[LiquidityPool],
        current_price: float
    ) -> Optional[float]:
        """Find the nearest untapped liquidity above current price."""
        valid_pools = [p for p in pools if p.status == LiquidityStatus.UNTAPPED and p.price > current_price]
        if not valid_pools:
            return None
        return min(valid_pools, key=lambda p: p.price).price
    
    def _find_nearest_below(
        self,
        pools: List[LiquidityPool],
        current_price: float
    ) -> Optional[float]:
        """Find the nearest untapped liquidity below current price."""
        valid_pools = [p for p in pools if p.status == LiquidityStatus.UNTAPPED and p.price < current_price]
        if not valid_pools:
            return None
        return max(valid_pools, key=lambda p: p.price).price
    
    def identify_inducement(
        self,
        pools: List[LiquidityPool],
        df: pd.DataFrame,
        lookback: int = 20
    ) -> List[LiquidityPool]:
        """
        Identify potential inducement levels.
        
        Inducement is liquidity that appears to be "easy" to take,
        often created to lure traders before the real move.
        """
        inducements = []
        
        recent_pools = [p for p in pools if len(df) - p.index <= lookback]
        
        for pool in recent_pools:
            if pool.touch_count >= 2:  # Multiple touches = more obvious
                inducements.append(pool)
        
        return inducements
