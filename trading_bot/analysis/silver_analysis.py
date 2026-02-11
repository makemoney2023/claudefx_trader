"""
Silver Analysis Module.

Specialized analysis for silver (XAGUSD) trading based on:
- 1979 historic pattern comparison
- Paper vs physical price disconnect
- RSI-based exit signals
- Volume accumulation/distribution
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SilverKeyLevels:
    """Key price levels for silver trading."""
    recent_low: float = 95.00       # January 2026 crash low
    recent_high: float = 121.00     # Prior peak
    target_1: float = 150.00        # First extension target
    target_2: float = 160.00        # Second extension target
    euphoria: float = 200.00        # Euphoria zone (exit signal)
    invalidation: float = 90.00     # Pattern invalidation level
    entry_zone_low: float = 95.00   # Entry zone start
    entry_zone_high: float = 105.00 # Entry zone end


class SilverAnalyzer:
    """
    Specialized analyzer for silver trading opportunities.
    
    Based on the historic 1979 pattern where silver:
    - Gained 65% in December 1979
    - Gained another 35-40% in January 1980
    - Peaked at $50 then crashed
    
    Current situation (January 2026):
    - 65% gain in January 2026 (only 3rd occurrence in 52 years)
    - Paper price crashed to $95 while physical unavailable
    - Potential for significant continuation if pattern repeats
    """
    
    def __init__(self):
        """Initialize silver analyzer with key levels."""
        self.key_levels = SilverKeyLevels()
        self.rsi_exit_threshold = 85  # Weekly RSI exit signal
        self.rsi_overbought = 70
        self.disconnect_warning_threshold = 15.0  # 15% premium = warning
        
        logger.info("Silver analyzer initialized with 1979 pattern parameters")
    
    def is_in_entry_zone(self, price: float) -> bool:
        """Check if price is in optimal entry zone."""
        return self.key_levels.entry_zone_low <= price <= self.key_levels.entry_zone_high
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """
        Calculate RSI for given price series.
        
        Args:
            prices: List of closing prices (oldest to newest)
            period: RSI period (default 14)
            
        Returns:
            RSI value (0-100)
        """
        if len(prices) < period + 1:
            return 50.0  # Default neutral if not enough data
        
        prices_arr = np.array(prices)
        deltas = np.diff(prices_arr)
        
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    def is_rsi_overbought(self, prices: List[float], period: int = 14) -> bool:
        """Check if RSI indicates overbought conditions."""
        rsi = self.calculate_rsi(prices, period)
        return rsi > self.rsi_overbought
    
    def check_rsi_exit_signal(self, rsi_value: float) -> bool:
        """Check if RSI indicates exit signal (weekly RSI > 85)."""
        return rsi_value >= self.rsi_exit_threshold
    
    def match_1979_pattern(self, current_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare current market to 1979 silver pattern.
        
        Args:
            current_data: Dict with keys:
                - january_2026_gain: Monthly gain (e.g., 0.65 for 65%)
                - previous_month_close: Prior month close price
                - current_price: Current price
                
        Returns:
            Pattern match analysis
        """
        # 1979 pattern characteristics
        pattern_1979 = {
            'initial_surge': 0.65,  # 65% December gain
            'continuation': 0.35,   # 35-40% additional
            'peak_multiple': 1.67,  # Peak was 67% above continuation start
        }
        
        monthly_gain = current_data.get('january_2026_gain', 0)
        
        # Calculate similarity score
        surge_similarity = min(monthly_gain / pattern_1979['initial_surge'], 1.0) * 100
        
        # Adjust for current phase
        similarity_score = surge_similarity
        
        return {
            'similarity_score': similarity_score,
            'pattern_1979': pattern_1979,
            'current_gain': monthly_gain,
            'interpretation': self._interpret_pattern_match(similarity_score)
        }
    
    def _interpret_pattern_match(self, score: float) -> str:
        """Interpret pattern similarity score."""
        if score >= 90:
            return "Strong pattern match - high probability of continuation"
        elif score >= 70:
            return "Good pattern match - moderate probability"
        elif score >= 50:
            return "Partial pattern match - proceed with caution"
        else:
            return "Weak pattern match - current move differs from 1979"
    
    def detect_pattern_phase(
        self,
        monthly_gain: float,
        rsi_weekly: float,
        public_sentiment: str
    ) -> str:
        """
        Detect current phase in the 1979-style pattern.
        
        Phases:
        - accumulation: Pre-surge period
        - surge: Initial 65% move
        - continuation: Post-surge continuation (35-40%)
        - euphoria: Blowoff top (everyone bullish)
        - crash: Post-peak collapse
        """
        if rsi_weekly >= 85 and public_sentiment in ['extremely_bullish', 'euphoric']:
            return 'euphoria'
        elif monthly_gain >= 0.80:
            return 'continuation'
        elif monthly_gain >= 0.50:
            return 'surge'
        elif monthly_gain >= 0.20:
            return 'accumulation'
        else:
            return 'accumulation'
    
    def get_1979_projections(
        self,
        current_price: float,
        pattern_similarity: float
    ) -> Dict[str, float]:
        """
        Get price projections based on 1979 pattern.
        
        Args:
            current_price: Current silver price
            pattern_similarity: How closely current matches 1979 (0-1)
        """
        # 1979: After 65% surge, additional 35-40% before peak
        continuation_factor = 1.35  # 35% additional
        peak_factor = 1.67  # Total peak from continuation start
        
        # Adjust projections by similarity
        adj_factor = 0.5 + (pattern_similarity * 0.5)  # 50-100% of projection
        
        return {
            'target_conservative': current_price * 1.25 * adj_factor,  # 25% up
            'target_aggressive': current_price * continuation_factor * adj_factor,
            'peak_estimate': current_price * peak_factor * adj_factor,
            'pattern_similarity_used': pattern_similarity
        }
    
    def detect_accumulation(self, volume_data: List[Dict[str, Any]]) -> bool:
        """
        Detect accumulation pattern via volume analysis.
        
        Accumulation: High volume on up days, low on down days
        """
        if not volume_data:
            return False
        
        up_volume = sum(d['volume'] for d in volume_data if d.get('direction') == 'up')
        down_volume = sum(d['volume'] for d in volume_data if d.get('direction') == 'down')
        
        # Accumulation if up volume significantly exceeds down volume
        return up_volume > down_volume * 1.5
    
    def detect_distribution(self, volume_data: List[Dict[str, Any]]) -> bool:
        """
        Detect distribution pattern via volume analysis.
        
        Distribution: High volume on down days (warning sign)
        """
        if not volume_data:
            return False
        
        up_volume = sum(d['volume'] for d in volume_data if d.get('direction') == 'up')
        down_volume = sum(d['volume'] for d in volume_data if d.get('direction') == 'down')
        
        # Distribution if down volume exceeds up volume
        return down_volume > up_volume * 1.5
    
    def calculate_disconnect(
        self,
        paper_price: float,
        physical_premium: float
    ) -> Dict[str, Any]:
        """
        Calculate paper vs physical price disconnect.
        
        Args:
            paper_price: COMEX/spot price
            physical_premium: Premium at dealers (in %)
        """
        physical_price = paper_price * (1 + physical_premium / 100)
        
        return {
            'paper_price': paper_price,
            'physical_price': physical_price,
            'premium_percent': physical_premium,
            'disconnect_significant': physical_premium > self.disconnect_warning_threshold
        }
    
    def is_significant_disconnect(self, premium_percent: float) -> bool:
        """Check if paper/physical disconnect is significant."""
        return premium_percent >= self.disconnect_warning_threshold
    
    def validate_silver_entry(
        self,
        signal: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Validate entry signal based on silver-specific rules.
        
        Args:
            signal: Dict with price, direction, rsi, pattern_phase
            
        Returns:
            Tuple of (is_valid, reason)
        """
        price = signal.get('price', 0)
        direction = signal.get('direction', '')
        rsi = signal.get('rsi', 50)
        pattern_phase = signal.get('pattern_phase', '')
        
        # Only long trades for silver opportunity
        if direction != 'long':
            return False, "Silver strategy is long-only based on 1979 pattern"
        
        # Check entry zone
        if not self.is_in_entry_zone(price):
            return False, f"Price {price} outside entry zone ({self.key_levels.entry_zone_low}-{self.key_levels.entry_zone_high})"
        
        # Check RSI not extremely overbought
        if rsi > 80:
            return False, f"RSI {rsi} too high for entry (>80)"
        
        # Check pattern phase
        if pattern_phase in ['euphoria', 'crash']:
            return False, f"Pattern phase '{pattern_phase}' not suitable for entry"
        
        return True, "Entry validated"
    
    def check_exit_conditions(self, market_state: Dict[str, Any]) -> bool:
        """
        Check if exit conditions are met.
        
        Exit signals (from video analysis):
        - RSI > 85 on weekly
        - Extreme bullish sentiment
        - Euphoria phase
        """
        rsi_weekly = market_state.get('rsi_weekly', 50)
        sentiment = market_state.get('public_sentiment', 'neutral')
        phase = market_state.get('pattern_phase', '')
        price = market_state.get('price', 0)
        
        # RSI exit signal
        if self.check_rsi_exit_signal(rsi_weekly):
            return True
        
        # Sentiment exit
        if sentiment in ['extremely_bullish', 'euphoric']:
            return True
        
        # Phase exit
        if phase == 'euphoria':
            return True
        
        # Price at euphoria level
        if price >= self.key_levels.euphoria:
            return True
        
        return False
    
    def calculate_silver_stop_loss(self, entry_price: float) -> float:
        """
        Calculate stop loss for silver trade.
        
        Stop below $90 (pattern invalidation level)
        """
        # Stop at invalidation or 8% below entry, whichever is lower
        invalidation_stop = self.key_levels.invalidation - 1.0
        percentage_stop = entry_price * 0.92
        
        return min(invalidation_stop, percentage_stop)
    
    def calculate_silver_targets(self, entry_price: float) -> Dict[str, float]:
        """
        Calculate take profit targets for silver trade.
        
        Based on 1979 pattern and key levels.
        """
        return {
            'tp1': self.key_levels.recent_high,  # First target: recent high
            'tp2': self.key_levels.target_1,     # Second target: 150
            'tp3': self.key_levels.target_2,     # Third target: 160
            'final': self.key_levels.euphoria    # Final target: 200
        }
    
    def analyze(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate comprehensive silver analysis.
        
        Args:
            market_data: Dict with current_price, prices, volume, physical_premium
            
        Returns:
            Complete analysis with recommendation
        """
        current_price = market_data.get('current_price', 0)
        prices = market_data.get('prices', [])
        volume = market_data.get('volume', [])
        physical_premium = market_data.get('physical_premium', 0)
        
        # RSI calculation
        rsi = self.calculate_rsi(prices) if len(prices) >= 15 else 50
        
        # Entry zone check
        in_entry_zone = self.is_in_entry_zone(current_price)
        
        # Volume analysis
        volume_data = []
        if len(prices) >= 2 and len(volume) >= 2:
            for i in range(1, min(len(prices), len(volume))):
                direction = 'up' if prices[i] > prices[i-1] else 'down'
                volume_data.append({
                    'close': prices[i],
                    'volume': volume[i],
                    'direction': direction
                })
        
        is_accumulation = self.detect_accumulation(volume_data)
        is_distribution = self.detect_distribution(volume_data)
        
        # Disconnect analysis
        disconnect = self.calculate_disconnect(current_price, physical_premium)
        
        # Pattern match
        pattern_match = self.match_1979_pattern({
            'january_2026_gain': 0.65,  # Assuming based on video
            'current_price': current_price
        })
        
        # Targets
        targets = self.calculate_silver_targets(current_price)
        
        # Risk assessment
        risk = self.assess_risk({
            'price': current_price,
            'rsi': rsi,
            'pattern_phase': 'surge',
            'geopolitical_risk': 'medium'
        })
        
        # Generate recommendation
        if in_entry_zone and rsi < 70 and is_accumulation:
            recommendation = 'STRONG_BUY'
        elif in_entry_zone and rsi < 75:
            recommendation = 'BUY'
        elif rsi > 85:
            recommendation = 'SELL'
        elif is_distribution:
            recommendation = 'CAUTION'
        else:
            recommendation = 'HOLD'
        
        return {
            'recommendation': recommendation,
            'current_price': current_price,
            'entry_zone_status': 'IN_ZONE' if in_entry_zone else 'OUTSIDE_ZONE',
            'rsi': rsi,
            'pattern_match': pattern_match,
            'volume_analysis': {
                'accumulation': is_accumulation,
                'distribution': is_distribution
            },
            'disconnect': disconnect,
            'targets': targets,
            'stop_loss': self.calculate_silver_stop_loss(current_price),
            'risk_assessment': risk
        }
    
    def assess_risk(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess risk level for silver trade.
        
        Args:
            market_state: Current market conditions
            
        Returns:
            Risk assessment with level and factors
        """
        price = market_state.get('price', 0)
        rsi = market_state.get('rsi', 50)
        pattern_phase = market_state.get('pattern_phase', 'unknown')
        geo_risk = market_state.get('geopolitical_risk', 'low')
        
        risk_factors = []
        risk_score = 0
        
        # Price risk
        if price > self.key_levels.target_1:
            risk_factors.append("Price above first target - reduced upside")
            risk_score += 2
        
        if price > self.key_levels.target_2:
            risk_factors.append("Price in euphoria zone - high crash risk")
            risk_score += 3
        
        # RSI risk
        if rsi > 70:
            risk_factors.append(f"RSI overbought ({rsi})")
            risk_score += 2
        
        if rsi > 85:
            risk_factors.append(f"RSI extreme ({rsi}) - exit signal")
            risk_score += 3
        
        # Pattern phase risk
        if pattern_phase == 'euphoria':
            risk_factors.append("In euphoria phase - crash imminent")
            risk_score += 4
        
        # Geopolitical risk
        if geo_risk in ['high', 'extreme']:
            risk_factors.append(f"Elevated geopolitical risk ({geo_risk})")
            risk_score += 2
        
        # Determine level
        if risk_score >= 8:
            level = 'extreme'
        elif risk_score >= 5:
            level = 'high'
        elif risk_score >= 2:
            level = 'medium'
        else:
            level = 'low'
        
        return {
            'level': level,
            'score': risk_score,
            'factors': risk_factors
        }
