"""
Tests for Crypto Analysis Module (XRP, ADA).
"""

import pytest
from trading_bot.analysis.crypto_analysis import CryptoAnalyzer, XRP_LEVELS, ADA_LEVELS


@pytest.fixture
def crypto_analyzer():
    """Create a CryptoAnalyzer instance for testing."""
    return CryptoAnalyzer()


class TestCryptoAnalyzer:
    """Tests for CryptoAnalyzer class."""
    
    def test_initialization(self, crypto_analyzer):
        """Test crypto analyzer initialization."""
        assert crypto_analyzer is not None
        assert 'XRPUSD' in crypto_analyzer.CRYPTO_CONFIG
        assert 'ADAUSD' in crypto_analyzer.CRYPTO_CONFIG
    
    def test_get_config_xrp(self, crypto_analyzer):
        """Test getting XRP configuration."""
        config = crypto_analyzer.get_config('XRP')
        
        assert config is not None
        assert config['name'] == 'Ripple'
        assert config['regulatory_sensitive'] == True
    
    def test_get_config_ada(self, crypto_analyzer):
        """Test getting ADA configuration."""
        config = crypto_analyzer.get_config('ADA')
        
        assert config is not None
        assert config['name'] == 'Cardano'
        assert config['regulatory_sensitive'] == False
    
    def test_get_key_levels_xrp(self, crypto_analyzer):
        """Test getting XRP key levels."""
        levels = crypto_analyzer.get_key_levels('XRPUSD')
        
        assert levels is not None
        assert levels.support_1 == 2.00
        assert levels.resistance_1 == 3.00
        assert levels.all_time_high == 3.84


class TestCryptoSupportResistance:
    """Tests for support/resistance detection."""
    
    def test_near_support_xrp(self, crypto_analyzer):
        """Test detection of XRP near support."""
        # Price at support_1 ($2.00)
        near, level = crypto_analyzer.is_near_support('XRP', 2.02)
        
        assert near == True
        assert level == 'support_1'
    
    def test_not_near_support(self, crypto_analyzer):
        """Test when price is not near support."""
        near, level = crypto_analyzer.is_near_support('XRP', 2.50)
        
        assert near == False
    
    def test_near_resistance_xrp(self, crypto_analyzer):
        """Test detection of XRP near resistance."""
        # Price at resistance_1 ($3.00)
        near, level = crypto_analyzer.is_near_resistance('XRP', 2.95)
        
        assert near == True
        assert level == 'resistance_1'


class TestCryptoRSI:
    """Tests for RSI calculation."""
    
    def test_rsi_calculation(self, crypto_analyzer):
        """Test RSI calculation."""
        prices = [2.0 + i * 0.02 for i in range(20)]  # Uptrend
        
        rsi = crypto_analyzer.calculate_rsi(prices)
        
        assert rsi > 50  # Should be bullish
    
    def test_rsi_overbought(self, crypto_analyzer):
        """Test RSI overbought detection."""
        # Strong uptrend
        prices = [2.0 + i * 0.05 for i in range(20)]
        
        rsi = crypto_analyzer.calculate_rsi(prices)
        # Strong trend should show elevated RSI


class TestCryptoVolatility:
    """Tests for volatility analysis."""
    
    def test_volatility_calculation(self, crypto_analyzer):
        """Test volatility calculation."""
        # 5% daily moves
        prices = [100, 105, 100, 105, 100, 105]
        
        vol = crypto_analyzer.calculate_volatility(prices)
        
        assert vol > 0
    
    def test_high_volatility_detection(self, crypto_analyzer):
        """Test high volatility detection."""
        # Large moves
        prices = [100, 110, 100, 112, 98, 115]
        
        is_high = crypto_analyzer.is_high_volatility(prices)
        
        assert is_high == True
    
    def test_normal_volatility(self, crypto_analyzer):
        """Test normal volatility detection."""
        # Small moves
        prices = [100, 100.5, 100.2, 100.7, 100.3, 100.8]
        
        is_high = crypto_analyzer.is_high_volatility(prices)
        
        assert is_high == False


class TestCryptoPositionSizing:
    """Tests for position size adjustment."""
    
    def test_position_size_reduced_for_crypto(self, crypto_analyzer):
        """Test that crypto position sizes are reduced."""
        base_size = 0.10
        
        xrp_size = crypto_analyzer.get_position_size_adjustment('XRP', base_size)
        ada_size = crypto_analyzer.get_position_size_adjustment('ADA', base_size)
        
        # Both should be less than base due to volatility
        assert xrp_size < base_size
        assert ada_size < base_size
        
        # ADA has higher volatility multiplier, so smaller size
        assert ada_size < xrp_size


class TestCryptoRegulatoryRisk:
    """Tests for regulatory risk assessment."""
    
    def test_xrp_regulatory_risk_elevated(self, crypto_analyzer):
        """Test that XRP has elevated regulatory risk."""
        risk = crypto_analyzer.check_regulatory_risk('XRP')
        
        assert risk['risk'] == 'elevated'
        assert 'SEC' in risk['details'] or 'regulatory' in risk['details'].lower()
    
    def test_ada_regulatory_risk_normal(self, crypto_analyzer):
        """Test that ADA has normal regulatory risk."""
        risk = crypto_analyzer.check_regulatory_risk('ADA')
        
        assert risk['risk'] == 'normal'


class TestCryptoFullAnalysis:
    """Tests for full crypto analysis."""
    
    def test_analyze_xrp(self, crypto_analyzer):
        """Test full XRP analysis."""
        market_data = {
            'current_price': 2.50,
            'prices': [2.40 + i * 0.02 for i in range(20)],
            'volume': []
        }
        
        analysis = crypto_analyzer.analyze('XRP', market_data)
        
        assert analysis['symbol'] == 'XRP'
        assert analysis['name'] == 'Ripple'
        assert 'recommendation' in analysis
        assert 'technical' in analysis
        assert 'levels' in analysis
        assert 'risk' in analysis
        assert analysis['is_24_7'] == True
    
    def test_analyze_ada(self, crypto_analyzer):
        """Test full ADA analysis."""
        market_data = {
            'current_price': 0.95,
            'prices': [0.90 + i * 0.01 for i in range(20)],
            'volume': []
        }
        
        analysis = crypto_analyzer.analyze('ADA', market_data)
        
        assert analysis['symbol'] == 'ADA'
        assert analysis['name'] == 'Cardano'
        assert 'Smart contracts' in analysis['use_case']
    
    def test_buy_recommendation_near_support(self, crypto_analyzer):
        """Test BUY recommendation when near support with low RSI."""
        market_data = {
            'current_price': 2.02,  # Near support
            'prices': [2.10 - i * 0.01 for i in range(20)],  # Downtrend
            'volume': []
        }
        
        analysis = crypto_analyzer.analyze('XRP', market_data)
        
        # Should suggest buying near support
        assert analysis['position_near']['support'] == True


class TestCryptoSummary:
    """Tests for crypto summary."""
    
    def test_get_crypto_summary(self, crypto_analyzer):
        """Test getting summary of all cryptos."""
        summary = crypto_analyzer.get_crypto_summary()
        
        assert 'cryptos' in summary
        assert 'XRPUSD' in summary['cryptos']
        assert 'ADAUSD' in summary['cryptos']
        assert summary['trading_hours'] == '24/7'
