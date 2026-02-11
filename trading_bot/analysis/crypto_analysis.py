"""
Crypto Analysis Module.

Specialized analysis for cryptocurrency trading:
- XRP (Ripple)
- ADA (Cardano)

Key differences from forex/metals:
- 24/7 trading (no session-based restrictions)
- Higher volatility
- News/sentiment driven
- Regulatory sensitivity
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CryptoKeyLevels:
    """Key price levels for a cryptocurrency."""
    support_1: float
    support_2: float
    resistance_1: float
    resistance_2: float
    all_time_high: float
    recent_low: float
    recent_high: float


# XRP Key Levels (as of January 2026)
XRP_LEVELS = CryptoKeyLevels(
    support_1=2.00,
    support_2=1.50,
    resistance_1=3.00,
    resistance_2=3.50,
    all_time_high=3.84,  # Historical ATH
    recent_low=0.50,
    recent_high=2.90
)

# ADA Key Levels (as of January 2026)
ADA_LEVELS = CryptoKeyLevels(
    support_1=0.80,
    support_2=0.60,
    resistance_1=1.20,
    resistance_2=1.50,
    all_time_high=3.10,  # Historical ATH
    recent_low=0.25,
    recent_high=1.15
)


class CryptoAnalyzer:
    """
    Analyzer for cryptocurrency trading.
    
    Features:
    - Support/resistance identification
    - Volume analysis
    - RSI-based signals
    - News/regulatory impact assessment
    - 24/7 market considerations
    """
    
    # Symbol configurations
    CRYPTO_CONFIG = {
        'XRPUSD': {
            'name': 'Ripple',
            'symbol': 'XRP',
            'levels': XRP_LEVELS,
            'volatility_multiplier': 1.5,  # Higher volatility than forex
            'regulatory_sensitive': True,  # SEC lawsuit history
            'use_case': 'Cross-border payments',
        },
        'ADAUSD': {
            'name': 'Cardano',
            'symbol': 'ADA',
            'levels': ADA_LEVELS,
            'volatility_multiplier': 1.8,  # Higher volatility
            'regulatory_sensitive': False,
            'use_case': 'Smart contracts, DeFi',
        }
    }
    
    def __init__(self):
        """Initialize crypto analyzer."""
        self.rsi_overbought = 70
        self.rsi_oversold = 30
        self.volatility_threshold = 5.0  # 5% daily move = high volatility
        
        logger.info("Crypto analyzer initialized for XRP and ADA")
    
    def get_config(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a crypto symbol."""
        # Normalize symbol
        normalized = symbol.upper().replace('/', '')
        if not normalized.endswith('USD'):
            normalized += 'USD'
        
        return self.CRYPTO_CONFIG.get(normalized)
    
    def get_key_levels(self, symbol: str) -> Optional[CryptoKeyLevels]:
        """Get key levels for a cryptocurrency."""
        config = self.get_config(symbol)
        if config:
            return config['levels']
        return None
    
    def is_near_support(self, symbol: str, price: float, tolerance: float = 0.03) -> Tuple[bool, str]:
        """
        Check if price is near a support level.
        
        Args:
            symbol: Crypto symbol
            price: Current price
            tolerance: Percentage tolerance (default 3%)
            
        Returns:
            Tuple of (is_near_support, support_level_name)
        """
        levels = self.get_key_levels(symbol)
        if not levels:
            return False, ""
        
        # Check each support level
        for level_name, level_price in [
            ('support_1', levels.support_1),
            ('support_2', levels.support_2),
            ('recent_low', levels.recent_low)
        ]:
            if abs(price - level_price) / level_price <= tolerance:
                return True, level_name
        
        return False, ""
    
    def is_near_resistance(self, symbol: str, price: float, tolerance: float = 0.03) -> Tuple[bool, str]:
        """
        Check if price is near a resistance level.
        
        Args:
            symbol: Crypto symbol
            price: Current price
            tolerance: Percentage tolerance (default 3%)
        """
        levels = self.get_key_levels(symbol)
        if not levels:
            return False, ""
        
        for level_name, level_price in [
            ('resistance_1', levels.resistance_1),
            ('resistance_2', levels.resistance_2),
            ('recent_high', levels.recent_high),
            ('all_time_high', levels.all_time_high)
        ]:
            if abs(price - level_price) / level_price <= tolerance:
                return True, level_name
        
        return False, ""
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI for given prices."""
        if len(prices) < period + 1:
            return 50.0
        
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
    
    def calculate_volatility(self, prices: List[float]) -> float:
        """
        Calculate daily volatility as percentage.
        
        Returns:
            Average daily percentage move
        """
        if len(prices) < 2:
            return 0.0
        
        prices_arr = np.array(prices)
        returns = np.diff(prices_arr) / prices_arr[:-1]
        
        # Average absolute daily move
        avg_move = np.mean(np.abs(returns)) * 100
        
        return float(avg_move)
    
    def is_high_volatility(self, prices: List[float]) -> bool:
        """Check if current volatility is high."""
        vol = self.calculate_volatility(prices)
        return vol > self.volatility_threshold
    
    def get_position_size_adjustment(self, symbol: str, base_size: float) -> float:
        """
        Adjust position size based on crypto volatility.
        
        Crypto is more volatile, so we reduce position sizes.
        """
        config = self.get_config(symbol)
        if not config:
            return base_size
        
        multiplier = config.get('volatility_multiplier', 1.0)
        
        # Reduce size inversely to volatility
        adjusted = base_size / multiplier
        
        return adjusted
    
    def check_regulatory_risk(self, symbol: str) -> Dict[str, Any]:
        """
        Check regulatory risk for a cryptocurrency.
        
        XRP has SEC lawsuit history, ADA is generally safer.
        """
        config = self.get_config(symbol)
        if not config:
            return {'risk': 'unknown', 'details': 'Symbol not configured'}
        
        is_sensitive = config.get('regulatory_sensitive', False)
        
        if is_sensitive:
            return {
                'risk': 'elevated',
                'symbol': config['symbol'],
                'details': 'Has regulatory sensitivity (e.g., SEC considerations)',
                'recommendation': 'Monitor news closely, consider smaller position sizes'
            }
        else:
            return {
                'risk': 'normal',
                'symbol': config['symbol'],
                'details': 'No major regulatory concerns',
                'recommendation': 'Standard position sizing'
            }
    
    def analyze(self, symbol: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate comprehensive crypto analysis.
        
        Args:
            symbol: Crypto symbol (XRP, ADA, etc.)
            market_data: Dict with current_price, prices, volume
            
        Returns:
            Complete analysis
        """
        config = self.get_config(symbol)
        if not config:
            return {'error': f'Symbol {symbol} not configured'}
        
        levels = config['levels']
        current_price = market_data.get('current_price', 0)
        prices = market_data.get('prices', [current_price])
        
        # Technical analysis
        rsi = self.calculate_rsi(prices) if len(prices) >= 15 else 50
        volatility = self.calculate_volatility(prices)
        is_volatile = volatility > self.volatility_threshold
        
        # Level analysis
        near_support, support_level = self.is_near_support(symbol, current_price)
        near_resistance, resistance_level = self.is_near_resistance(symbol, current_price)
        
        # Distance to ATH
        distance_to_ath = ((levels.all_time_high - current_price) / current_price) * 100
        
        # Generate recommendation
        if near_support and rsi < 40:
            recommendation = 'BUY'
            reasoning = f"Near {support_level} with oversold RSI ({rsi:.1f})"
        elif near_resistance and rsi > 65:
            recommendation = 'SELL' 
            reasoning = f"Near {resistance_level} with high RSI ({rsi:.1f})"
        elif rsi < self.rsi_oversold:
            recommendation = 'BUY'
            reasoning = f"Oversold RSI ({rsi:.1f})"
        elif rsi > self.rsi_overbought:
            recommendation = 'CAUTION'
            reasoning = f"Overbought RSI ({rsi:.1f})"
        else:
            recommendation = 'HOLD'
            reasoning = "No clear signal"
        
        # Risk assessment
        risk_factors = []
        if is_volatile:
            risk_factors.append(f"High volatility ({volatility:.1f}%)")
        if config.get('regulatory_sensitive'):
            risk_factors.append("Regulatory sensitivity")
        if distance_to_ath < 20:
            risk_factors.append("Near all-time high")
        
        return {
            'symbol': symbol,
            'name': config['name'],
            'current_price': current_price,
            'recommendation': recommendation,
            'reasoning': reasoning,
            'technical': {
                'rsi': rsi,
                'volatility': volatility,
                'is_high_volatility': is_volatile
            },
            'levels': {
                'support_1': levels.support_1,
                'support_2': levels.support_2,
                'resistance_1': levels.resistance_1,
                'resistance_2': levels.resistance_2,
                'all_time_high': levels.all_time_high,
                'distance_to_ath_percent': distance_to_ath
            },
            'position_near': {
                'support': near_support,
                'support_level': support_level,
                'resistance': near_resistance,
                'resistance_level': resistance_level
            },
            'risk': {
                'factors': risk_factors,
                'level': 'high' if len(risk_factors) >= 2 else 'medium' if risk_factors else 'low',
                'regulatory': self.check_regulatory_risk(symbol)
            },
            'position_size_adjustment': self.get_position_size_adjustment(symbol, 1.0),
            'use_case': config.get('use_case', ''),
            'is_24_7': True  # Crypto trades 24/7
        }
    
    def get_crypto_summary(self) -> Dict[str, Any]:
        """Get summary of all configured cryptos."""
        summaries = {}
        
        for symbol, config in self.CRYPTO_CONFIG.items():
            summaries[symbol] = {
                'name': config['name'],
                'symbol': config['symbol'],
                'levels': {
                    'support': config['levels'].support_1,
                    'resistance': config['levels'].resistance_1,
                    'ath': config['levels'].all_time_high
                },
                'regulatory_sensitive': config.get('regulatory_sensitive', False),
                'use_case': config.get('use_case', '')
            }
        
        return {
            'cryptos': summaries,
            'trading_hours': '24/7',
            'note': 'Crypto is more volatile - position sizes are automatically reduced'
        }
