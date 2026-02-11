"""
Precious Metals Analysis Module.

Unified analysis for Gold (XAUUSD) and Silver (XAGUSD) trading based on:
- Gold/Silver ratio analysis (historically 60-80:1)
- Cross-metal correlation signals
- Safe-haven demand indicators
- USD inverse correlation
- Geopolitical risk premium
- Central bank activity
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from ..utils.logging import get_logger
from .silver_analysis import SilverAnalyzer, SilverKeyLevels

logger = get_logger(__name__)


@dataclass
class GoldKeyLevels:
    """Key price levels for gold trading (XAUUSD)."""
    # 2026 price range based on current market
    recent_low: float = 2850.00       # Recent support
    recent_high: float = 3150.00      # Recent resistance / ATH zone
    all_time_high: float = 3100.00    # 2026 ATH
    support_1: float = 2900.00        # First support
    support_2: float = 2800.00        # Second support
    resistance_1: float = 3000.00     # First resistance
    resistance_2: float = 3100.00     # Second resistance
    invalidation: float = 2750.00     # Pattern invalidation level
    entry_zone_low: float = 2850.00   # Entry zone start
    entry_zone_high: float = 2950.00  # Entry zone end


@dataclass  
class GoldSilverRatio:
    """Gold to Silver price ratio analysis."""
    current_ratio: float              # Current gold/silver ratio
    historical_avg: float = 70.0      # Long-term average
    normal_low: float = 60.0          # Normal range low
    normal_high: float = 80.0         # Normal range high
    extreme_low: float = 40.0         # Historically rare - silver overvalued
    extreme_high: float = 100.0       # Historically rare - silver undervalued
    
    @property
    def interpretation(self) -> str:
        """Interpret the current ratio."""
        if self.current_ratio >= self.extreme_high:
            return "Extreme - Silver severely undervalued vs Gold"
        elif self.current_ratio >= self.normal_high:
            return "High - Silver undervalued, may outperform"
        elif self.current_ratio <= self.extreme_low:
            return "Extreme - Silver overvalued vs Gold"
        elif self.current_ratio <= self.normal_low:
            return "Low - Silver may be overextended"
        else:
            return "Normal range - balanced relationship"
    
    @property
    def trade_bias(self) -> str:
        """Get trading bias based on ratio."""
        if self.current_ratio >= self.normal_high:
            return "Favor Silver longs over Gold"
        elif self.current_ratio <= self.normal_low:
            return "Favor Gold longs over Silver"
        else:
            return "No strong bias - trade both equally"


class PreciousMetalsAnalyzer:
    """
    Unified analyzer for precious metals (Gold & Silver).
    
    Combines individual metal analysis with cross-metal signals
    for more informed trading decisions.
    """
    
    def __init__(self):
        """Initialize precious metals analyzer."""
        # Individual analyzers
        self.silver_analyzer = SilverAnalyzer()
        
        # Gold key levels
        self.gold_levels = GoldKeyLevels()
        
        # RSI settings
        self.rsi_overbought = 70
        self.rsi_oversold = 30
        self.rsi_exit_threshold = 85
        
        # Safe haven indicators
        self.safe_haven_signals = []
        
        logger.info("Precious metals analyzer initialized")
    
    def calculate_gold_silver_ratio(
        self,
        gold_price: float,
        silver_price: float
    ) -> GoldSilverRatio:
        """
        Calculate and analyze the gold/silver ratio.
        
        This ratio is a key indicator for relative value between the metals.
        """
        if silver_price <= 0:
            ratio = 0.0
        else:
            ratio = gold_price / silver_price
        
        return GoldSilverRatio(current_ratio=ratio)
    
    def is_gold_in_entry_zone(self, price: float) -> bool:
        """Check if gold price is in optimal entry zone."""
        return self.gold_levels.entry_zone_low <= price <= self.gold_levels.entry_zone_high
    
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
    
    def analyze_gold(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate comprehensive gold analysis.
        
        Args:
            market_data: Dict with current_price, prices (list), volume (list)
            
        Returns:
            Complete analysis with recommendation
        """
        current_price = market_data.get('current_price', 0)
        prices = market_data.get('prices', [])
        
        # RSI calculation
        rsi = self.calculate_rsi(prices) if len(prices) >= 15 else 50
        
        # Entry zone check
        in_entry_zone = self.is_gold_in_entry_zone(current_price)
        
        # Calculate distance to key levels
        distance_to_support = current_price - self.gold_levels.support_1
        distance_to_resistance = self.gold_levels.resistance_1 - current_price
        distance_to_ath_percent = ((self.gold_levels.all_time_high - current_price) / self.gold_levels.all_time_high) * 100
        
        # Determine recommendation
        if in_entry_zone and rsi < 65 and distance_to_support > 50:
            recommendation = 'BUY'
            reasoning = f"Price in entry zone, RSI neutral ({rsi:.1f}), good support buffer"
        elif rsi > 80 and current_price > self.gold_levels.resistance_1:
            recommendation = 'CAUTION'
            reasoning = f"RSI overbought ({rsi:.1f}), extended above resistance"
        elif rsi < 35 and current_price < self.gold_levels.support_1:
            recommendation = 'STRONG_BUY'
            reasoning = f"RSI oversold ({rsi:.1f}), price at support - potential bounce"
        elif current_price > self.gold_levels.all_time_high * 0.98:
            recommendation = 'HOLD'
            reasoning = "Near all-time highs - reduced new entries, manage existing"
        else:
            recommendation = 'HOLD'
            reasoning = "No clear setup - wait for better entry"
        
        # Calculate targets
        targets = self._calculate_gold_targets(current_price)
        stop_loss = self._calculate_gold_stop_loss(current_price)
        
        return {
            'symbol': 'XAUUSD',
            'name': 'Gold',
            'current_price': current_price,
            'recommendation': recommendation,
            'reasoning': reasoning,
            'entry_zone_status': 'IN_ZONE' if in_entry_zone else 'OUTSIDE_ZONE',
            'rsi': rsi,
            'key_levels': {
                'support_1': self.gold_levels.support_1,
                'support_2': self.gold_levels.support_2,
                'resistance_1': self.gold_levels.resistance_1,
                'resistance_2': self.gold_levels.resistance_2,
                'all_time_high': self.gold_levels.all_time_high,
                'entry_zone_low': self.gold_levels.entry_zone_low,
                'entry_zone_high': self.gold_levels.entry_zone_high,
                'invalidation': self.gold_levels.invalidation,
                'distance_to_ath_percent': distance_to_ath_percent
            },
            'targets': targets,
            'stop_loss': stop_loss,
            'risk_assessment': self._assess_gold_risk({
                'price': current_price,
                'rsi': rsi,
                'near_ath': current_price > self.gold_levels.all_time_high * 0.95
            })
        }
    
    def _calculate_gold_targets(self, entry_price: float) -> Dict[str, float]:
        """Calculate take profit targets for gold trade."""
        return {
            'tp1': self.gold_levels.resistance_1,
            'tp2': self.gold_levels.resistance_2,
            'tp3': self.gold_levels.all_time_high * 1.05,  # 5% above ATH
            'final': entry_price * 1.10  # 10% gain target
        }
    
    def _calculate_gold_stop_loss(self, entry_price: float) -> float:
        """Calculate stop loss for gold trade."""
        # Stop at invalidation or 3% below entry, whichever is lower
        invalidation_stop = self.gold_levels.invalidation
        percentage_stop = entry_price * 0.97
        return min(invalidation_stop, percentage_stop)
    
    def _assess_gold_risk(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk level for gold trade."""
        price = market_state.get('price', 0)
        rsi = market_state.get('rsi', 50)
        near_ath = market_state.get('near_ath', False)
        
        risk_factors = []
        risk_score = 0
        
        # Price risk
        if price > self.gold_levels.resistance_2:
            risk_factors.append("Price extended beyond resistance")
            risk_score += 2
        
        if near_ath:
            risk_factors.append("Near all-time high - limited upside")
            risk_score += 2
        
        # RSI risk
        if rsi > 70:
            risk_factors.append(f"RSI overbought ({rsi:.0f})")
            risk_score += 2
        
        if rsi > 85:
            risk_factors.append(f"RSI extreme ({rsi:.0f}) - exit signal")
            risk_score += 3
        
        # Determine level
        if risk_score >= 6:
            level = 'high'
        elif risk_score >= 3:
            level = 'medium'
        else:
            level = 'low'
        
        return {
            'level': level,
            'score': risk_score,
            'factors': risk_factors
        }
    
    def analyze_combined(
        self,
        gold_price: float,
        silver_price: float,
        gold_prices: List[float] = None,
        silver_prices: List[float] = None,
        geopolitical_risk: str = 'normal'
    ) -> Dict[str, Any]:
        """
        Generate combined precious metals analysis.
        
        This provides cross-metal insights for better trading decisions.
        """
        # Calculate ratio
        ratio = self.calculate_gold_silver_ratio(gold_price, silver_price)
        
        # Analyze each metal
        gold_analysis = self.analyze_gold({
            'current_price': gold_price,
            'prices': gold_prices or []
        })
        
        silver_analysis = self.silver_analyzer.analyze({
            'current_price': silver_price,
            'prices': silver_prices or [],
            'volume': [],
            'physical_premium': 0
        })
        
        # Cross-metal signals
        cross_signals = self._generate_cross_signals(
            gold_analysis, silver_analysis, ratio, geopolitical_risk
        )
        
        # Safe haven assessment
        safe_haven = self._assess_safe_haven_demand(geopolitical_risk, ratio)
        
        # Primary recommendation
        primary_metal, primary_reasoning = self._get_primary_recommendation(
            gold_analysis, silver_analysis, ratio
        )
        
        return {
            'timestamp': datetime.now().isoformat(),
            'gold': gold_analysis,
            'silver': silver_analysis,
            'ratio': {
                'current': ratio.current_ratio,
                'historical_avg': ratio.historical_avg,
                'interpretation': ratio.interpretation,
                'trade_bias': ratio.trade_bias,
                'normal_range': f"{ratio.normal_low}-{ratio.normal_high}"
            },
            'cross_signals': cross_signals,
            'safe_haven': safe_haven,
            'primary_recommendation': {
                'metal': primary_metal,
                'reasoning': primary_reasoning
            },
            'correlation': {
                'gold_silver': 0.90,  # Known high correlation
                'note': 'Gold and silver typically move together with silver more volatile'
            }
        }
    
    def _generate_cross_signals(
        self,
        gold_analysis: Dict[str, Any],
        silver_analysis: Dict[str, Any],
        ratio: GoldSilverRatio,
        geopolitical_risk: str
    ) -> List[Dict[str, str]]:
        """Generate trading signals based on cross-metal analysis."""
        signals = []
        
        # Ratio-based signals
        if ratio.current_ratio >= ratio.normal_high:
            signals.append({
                'type': 'ratio',
                'signal': 'Silver undervalued',
                'action': 'Consider silver over gold for new positions',
                'strength': 'strong' if ratio.current_ratio >= ratio.extreme_high else 'moderate'
            })
        elif ratio.current_ratio <= ratio.normal_low:
            signals.append({
                'type': 'ratio',
                'signal': 'Silver potentially overvalued',
                'action': 'Consider gold over silver for new positions',
                'strength': 'strong' if ratio.current_ratio <= ratio.extreme_low else 'moderate'
            })
        
        # Divergence signals
        gold_rec = gold_analysis.get('recommendation', 'HOLD')
        silver_rec = silver_analysis.get('recommendation', 'HOLD')
        
        if gold_rec in ['BUY', 'STRONG_BUY'] and silver_rec in ['BUY', 'STRONG_BUY']:
            signals.append({
                'type': 'confirmation',
                'signal': 'Both metals bullish',
                'action': 'High conviction long precious metals',
                'strength': 'strong'
            })
        elif gold_rec in ['SELL', 'CAUTION'] and silver_rec in ['SELL', 'CAUTION']:
            signals.append({
                'type': 'confirmation',
                'signal': 'Both metals cautious',
                'action': 'Reduce precious metals exposure',
                'strength': 'strong'
            })
        elif gold_rec != silver_rec:
            signals.append({
                'type': 'divergence',
                'signal': f'Divergence: Gold={gold_rec}, Silver={silver_rec}',
                'action': 'Trade the stronger setup, use caution',
                'strength': 'moderate'
            })
        
        # Geopolitical signal
        if geopolitical_risk in ['high', 'extreme']:
            signals.append({
                'type': 'geopolitical',
                'signal': f'Elevated geopolitical risk ({geopolitical_risk})',
                'action': 'Favor gold as primary safe haven',
                'strength': 'strong'
            })
        
        return signals
    
    def _assess_safe_haven_demand(
        self,
        geopolitical_risk: str,
        ratio: GoldSilverRatio
    ) -> Dict[str, Any]:
        """Assess safe haven demand for precious metals."""
        demand_score = 0
        factors = []
        
        # Geopolitical risk
        if geopolitical_risk == 'extreme':
            demand_score += 4
            factors.append("Extreme geopolitical tensions")
        elif geopolitical_risk == 'high':
            demand_score += 3
            factors.append("Elevated geopolitical risk")
        elif geopolitical_risk == 'medium':
            demand_score += 1
            factors.append("Moderate geopolitical concerns")
        
        # Ratio indicates potential rotation
        if ratio.current_ratio >= ratio.normal_high:
            demand_score += 1
            factors.append("High ratio suggests silver may catch up")
        
        # Determine level
        if demand_score >= 4:
            level = 'very_high'
            recommendation = "Strong safe haven bid expected - favor gold"
        elif demand_score >= 2:
            level = 'elevated'
            recommendation = "Increased safe haven interest - bullish precious metals"
        else:
            level = 'normal'
            recommendation = "Normal market conditions - trade technicals"
        
        return {
            'level': level,
            'score': demand_score,
            'factors': factors,
            'recommendation': recommendation
        }
    
    def _get_primary_recommendation(
        self,
        gold_analysis: Dict[str, Any],
        silver_analysis: Dict[str, Any],
        ratio: GoldSilverRatio
    ) -> Tuple[str, str]:
        """Determine which metal to prioritize and why."""
        gold_rec = gold_analysis.get('recommendation', 'HOLD')
        silver_rec = silver_analysis.get('recommendation', 'HOLD')
        gold_rsi = gold_analysis.get('rsi', 50)
        silver_rsi = silver_analysis.get('rsi', 50)
        
        # Score each metal
        gold_score = 0
        silver_score = 0
        
        # Recommendation score
        rec_scores = {'STRONG_BUY': 3, 'BUY': 2, 'HOLD': 0, 'CAUTION': -1, 'SELL': -2}
        gold_score += rec_scores.get(gold_rec, 0)
        silver_score += rec_scores.get(silver_rec, 0)
        
        # Ratio bias
        if ratio.current_ratio >= ratio.normal_high:
            silver_score += 2  # Silver undervalued
        elif ratio.current_ratio <= ratio.normal_low:
            gold_score += 2  # Gold preferred
        
        # RSI score (prefer not overbought)
        if gold_rsi < 65:
            gold_score += 1
        if silver_rsi < 65:
            silver_score += 1
        
        # Determine winner
        if silver_score > gold_score:
            return 'SILVER', f"Silver preferred (score {silver_score} vs gold {gold_score}): {silver_rec}, undervalued by ratio"
        elif gold_score > silver_score:
            return 'GOLD', f"Gold preferred (score {gold_score} vs silver {silver_score}): {gold_rec}, stronger setup"
        else:
            return 'BOTH', f"Both equally attractive (score {gold_score}): Trade either based on individual setups"
    
    def get_gold_levels(self) -> Dict[str, float]:
        """Return gold key levels as dictionary."""
        return {
            'recent_low': self.gold_levels.recent_low,
            'recent_high': self.gold_levels.recent_high,
            'all_time_high': self.gold_levels.all_time_high,
            'support_1': self.gold_levels.support_1,
            'support_2': self.gold_levels.support_2,
            'resistance_1': self.gold_levels.resistance_1,
            'resistance_2': self.gold_levels.resistance_2,
            'invalidation': self.gold_levels.invalidation,
            'entry_zone_low': self.gold_levels.entry_zone_low,
            'entry_zone_high': self.gold_levels.entry_zone_high
        }
    
    def get_silver_levels(self) -> Dict[str, float]:
        """Return silver key levels as dictionary."""
        sl = self.silver_analyzer.key_levels
        return {
            'recent_low': sl.recent_low,
            'recent_high': sl.recent_high,
            'target_1': sl.target_1,
            'target_2': sl.target_2,
            'euphoria': sl.euphoria,
            'invalidation': sl.invalidation,
            'entry_zone_low': sl.entry_zone_low,
            'entry_zone_high': sl.entry_zone_high
        }
    
    def get_context_for_claude(
        self,
        gold_price: float,
        silver_price: float,
        geopolitical_risk: str = 'normal'
    ) -> str:
        """
        Generate precious metals context string for Claude prompts.
        
        This provides Claude with key precious metals information
        when analyzing gold or silver trades.
        """
        ratio = self.calculate_gold_silver_ratio(gold_price, silver_price)
        safe_haven = self._assess_safe_haven_demand(geopolitical_risk, ratio)
        
        context = f"""
## Precious Metals Context

### Gold/Silver Ratio
- Current Ratio: {ratio.current_ratio:.1f}
- Historical Average: {ratio.historical_avg}
- Normal Range: {ratio.normal_low}-{ratio.normal_high}
- Interpretation: {ratio.interpretation}
- Trade Bias: {ratio.trade_bias}

### Current Prices
- Gold (XAUUSD): ${gold_price:,.2f}
- Silver (XAGUSD): ${silver_price:.2f}

### Gold Key Levels
- Support: ${self.gold_levels.support_1:,.0f} / ${self.gold_levels.support_2:,.0f}
- Resistance: ${self.gold_levels.resistance_1:,.0f} / ${self.gold_levels.resistance_2:,.0f}
- All-Time High: ${self.gold_levels.all_time_high:,.0f}
- Entry Zone: ${self.gold_levels.entry_zone_low:,.0f} - ${self.gold_levels.entry_zone_high:,.0f}

### Silver Key Levels (1979 Pattern)
- Entry Zone: ${self.silver_analyzer.key_levels.entry_zone_low:.0f} - ${self.silver_analyzer.key_levels.entry_zone_high:.0f}
- Target 1: ${self.silver_analyzer.key_levels.target_1:.0f}
- Target 2: ${self.silver_analyzer.key_levels.target_2:.0f}
- Euphoria Exit: ${self.silver_analyzer.key_levels.euphoria:.0f}

### Safe Haven Demand
- Level: {safe_haven['level']}
- Recommendation: {safe_haven['recommendation']}

### Trading Notes
- Gold and Silver correlation: ~0.90 (highly correlated)
- Silver is more volatile (typically 2x gold moves)
- In risk-off environments, gold usually leads
- {"Silver may outperform (ratio high)" if ratio.current_ratio >= ratio.normal_high else "Gold may outperform (ratio low)" if ratio.current_ratio <= ratio.normal_low else "Ratio neutral - trade individual setups"}
"""
        return context
