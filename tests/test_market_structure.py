"""
Tests for Market Structure Analyzer.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from trading_bot.analysis.market_structure import (
    MarketStructureAnalyzer,
    StructureAnalysis,
    StructureType,
    TrendDirection,
    SwingPoint,
    MarketStructure
)


@pytest.fixture
def sample_bullish_data():
    """Create sample data with bullish structure (HH + HL)."""
    dates = pd.date_range(start='2024-01-01', periods=100, freq='1h')
    
    # Create trending up data with clear swings
    np.random.seed(42)
    base = 1.0800
    trend = np.linspace(0, 0.0100, 100)  # Upward trend
    noise = np.random.randn(100) * 0.0010
    
    close = base + trend + noise
    high = close + np.abs(np.random.randn(100) * 0.0005)
    low = close - np.abs(np.random.randn(100) * 0.0005)
    open_prices = close + np.random.randn(100) * 0.0003
    
    # Ensure high/low contain open/close
    high = np.maximum.reduce([high, open_prices, close])
    low = np.minimum.reduce([low, open_prices, close])
    
    df = pd.DataFrame({
        'open': open_prices,
        'high': high,
        'low': low,
        'close': close,
        'volume': np.random.randint(100, 1000, 100)
    }, index=dates)
    
    return df


@pytest.fixture
def sample_bearish_data():
    """Create sample data with bearish structure (LH + LL)."""
    dates = pd.date_range(start='2024-01-01', periods=100, freq='1h')
    
    np.random.seed(42)
    base = 1.0900
    trend = np.linspace(0, -0.0100, 100)  # Downward trend
    noise = np.random.randn(100) * 0.0010
    
    close = base + trend + noise
    high = close + np.abs(np.random.randn(100) * 0.0005)
    low = close - np.abs(np.random.randn(100) * 0.0005)
    open_prices = close + np.random.randn(100) * 0.0003
    
    high = np.maximum.reduce([high, open_prices, close])
    low = np.minimum.reduce([low, open_prices, close])
    
    df = pd.DataFrame({
        'open': open_prices,
        'high': high,
        'low': low,
        'close': close,
        'volume': np.random.randint(100, 1000, 100)
    }, index=dates)
    
    return df


@pytest.fixture
def analyzer():
    """Create a MarketStructureAnalyzer instance."""
    return MarketStructureAnalyzer(swing_lookback=5, min_swing_bars=3)


class TestMarketStructureAnalyzer:
    """Tests for MarketStructureAnalyzer class."""
    
    def test_initialization(self, analyzer):
        """Test analyzer initialization."""
        assert analyzer.swing_lookback == 5
        assert analyzer.min_swing_bars == 3
    
    def test_analyze_returns_structure_analysis(self, analyzer, sample_bullish_data):
        """Test that analyze returns StructureAnalysis object."""
        result = analyzer.analyze(sample_bullish_data)
        
        assert isinstance(result, StructureAnalysis)
        assert hasattr(result, 'trend')
        assert hasattr(result, 'swing_highs')
        assert hasattr(result, 'swing_lows')
        assert hasattr(result, 'structure_breaks')
    
    def test_bullish_trend_detected(self, analyzer, sample_bullish_data):
        """Test that bullish trend is correctly identified."""
        result = analyzer.analyze(sample_bullish_data)
        
        # Should detect swing points in the uptrend data
        assert len(result.swing_highs) > 0
        assert len(result.swing_lows) > 0
        # Should identify the trend as bullish
        assert result.trend == TrendDirection.BULLISH
    
    def test_bearish_trend_detected(self, analyzer, sample_bearish_data):
        """Test that bearish trend is correctly identified."""
        result = analyzer.analyze(sample_bearish_data)
        
        # Should detect swing points in the downtrend data
        assert len(result.swing_highs) > 0
        assert len(result.swing_lows) > 0
        # Should identify the trend as bearish
        assert result.trend == TrendDirection.BEARISH
    
    def test_swing_points_have_correct_attributes(self, analyzer, sample_bullish_data):
        """Test that swing points have required attributes."""
        result = analyzer.analyze(sample_bullish_data)
        
        if result.swing_highs:
            swing_high = result.swing_highs[0]
            assert isinstance(swing_high, SwingPoint)
            assert hasattr(swing_high, 'index')
            assert hasattr(swing_high, 'price')
            assert hasattr(swing_high, 'is_high')
            assert swing_high.is_high == True
        
        if result.swing_lows:
            swing_low = result.swing_lows[0]
            assert swing_low.is_high == False
    
    def test_to_dict_method(self, analyzer, sample_bullish_data):
        """Test that to_dict returns proper dictionary."""
        result = analyzer.analyze(sample_bullish_data)
        result_dict = result.to_dict()
        
        assert isinstance(result_dict, dict)
        assert 'trend' in result_dict
        assert 'swing_highs' in result_dict
        assert 'swing_lows' in result_dict
        assert 'structure_breaks' in result_dict
    
    def test_empty_dataframe_handling(self, analyzer):
        """Test handling of empty DataFrame."""
        empty_df = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        
        # Should handle gracefully
        result = analyzer.analyze(empty_df)
        assert isinstance(result, StructureAnalysis)
        assert result.trend == TrendDirection.RANGING
    
    def test_small_dataframe_handling(self, analyzer):
        """Test handling of DataFrame with few rows."""
        dates = pd.date_range(start='2024-01-01', periods=3, freq='1h')
        small_df = pd.DataFrame({
            'open': [1.0800, 1.0810, 1.0805],
            'high': [1.0815, 1.0820, 1.0815],
            'low': [1.0795, 1.0805, 1.0800],
            'close': [1.0810, 1.0815, 1.0810],
            'volume': [100, 150, 120]
        }, index=dates)
        
        result = analyzer.analyze(small_df)
        assert isinstance(result, StructureAnalysis)


class TestStructureTypes:
    """Tests for structure type identification."""
    
    def test_bos_bullish(self):
        """Test BOS bullish structure type."""
        bos = StructureType.BOS_BULLISH
        assert bos.value == "bos_bullish"
    
    def test_choch_bearish(self):
        """Test CHoCH bearish structure type."""
        choch = StructureType.CHOCH_BEARISH
        assert choch.value == "choch_bearish"
    
    def test_market_structure_is_bullish(self):
        """Test MarketStructure.is_bullish property."""
        ms = MarketStructure(
            type=StructureType.BOS_BULLISH,
            index=10,
            price=1.0850,
            broken_level=1.0800
        )
        assert ms.is_bullish == True
        assert ms.is_bearish == False
    
    def test_market_structure_is_bearish(self):
        """Test MarketStructure.is_bearish property."""
        ms = MarketStructure(
            type=StructureType.CHOCH_BEARISH,
            index=10,
            price=1.0750,
            broken_level=1.0800
        )
        assert ms.is_bullish == False
        assert ms.is_bearish == True


class TestTrendDirection:
    """Tests for TrendDirection enum."""
    
    def test_trend_values(self):
        """Test trend direction values."""
        assert TrendDirection.BULLISH.value == "bullish"
        assert TrendDirection.BEARISH.value == "bearish"
        assert TrendDirection.RANGING.value == "ranging"
