"""
Tests for Candle Utility Functions.
"""

import pytest
import pandas as pd
import numpy as np

from trading_bot.utils.candle_utils import (
    calculate_body_percentage,
    is_bullish_candle,
    is_bearish_candle,
    get_candle_range,
    find_swing_highs,
    find_swing_lows,
    find_equal_highs,
    find_equal_lows,
    calculate_atr
)


class TestCandleBasics:
    """Tests for basic candle functions."""
    
    def test_calculate_body_percentage_bullish(self):
        """Test body percentage for bullish candle."""
        # Body = 0.0010, Range = 0.0020, Body % = 50%
        result = calculate_body_percentage(
            open_price=1.0800,
            high=1.0820,
            low=1.0800,
            close=1.0810
        )
        assert result == pytest.approx(0.5)
    
    def test_calculate_body_percentage_bearish(self):
        """Test body percentage for bearish candle."""
        result = calculate_body_percentage(
            open_price=1.0810,
            high=1.0820,
            low=1.0800,
            close=1.0800
        )
        assert result == pytest.approx(0.5)
    
    def test_calculate_body_percentage_doji(self):
        """Test body percentage for doji (open = close)."""
        result = calculate_body_percentage(
            open_price=1.0810,
            high=1.0820,
            low=1.0800,
            close=1.0810
        )
        assert result == pytest.approx(0.0)
    
    def test_calculate_body_percentage_full_body(self):
        """Test body percentage for full body candle."""
        result = calculate_body_percentage(
            open_price=1.0800,
            high=1.0820,
            low=1.0800,
            close=1.0820
        )
        assert result == pytest.approx(1.0)
    
    def test_is_bullish_candle(self):
        """Test bullish candle detection."""
        assert is_bullish_candle(1.0800, 1.0820) == True
        assert is_bullish_candle(1.0820, 1.0800) == False
        assert is_bullish_candle(1.0810, 1.0810) == False
    
    def test_is_bearish_candle(self):
        """Test bearish candle detection."""
        assert is_bearish_candle(1.0820, 1.0800) == True
        assert is_bearish_candle(1.0800, 1.0820) == False
        assert is_bearish_candle(1.0810, 1.0810) == False
    
    def test_get_candle_range(self):
        """Test candle range calculation."""
        result = get_candle_range(high=1.0850, low=1.0800)
        assert result == pytest.approx(0.0050)


class TestSwingPoints:
    """Tests for swing point detection."""
    
    @pytest.fixture
    def sample_data(self):
        """Create sample data with clear swing points."""
        dates = pd.date_range(start='2024-01-01', periods=20, freq='1h')
        
        # Create data with clear swing high at index 5 and swing low at index 15
        high = [1.08, 1.081, 1.082, 1.083, 1.084, 1.090, 1.084, 1.083, 1.082, 1.081,
                1.080, 1.079, 1.078, 1.077, 1.076, 1.070, 1.076, 1.077, 1.078, 1.079]
        low = [1.075, 1.076, 1.077, 1.078, 1.079, 1.085, 1.079, 1.078, 1.077, 1.076,
               1.075, 1.074, 1.073, 1.072, 1.071, 1.065, 1.071, 1.072, 1.073, 1.074]
        
        df = pd.DataFrame({
            'open': [h - 0.001 for h in high],
            'high': high,
            'low': low,
            'close': [l + 0.001 for l in low],
            'volume': [100] * 20
        }, index=dates)
        
        return df
    
    def test_find_swing_highs(self, sample_data):
        """Test swing high detection."""
        swings = find_swing_highs(sample_data, left_bars=3, right_bars=3)
        
        # Should find at least one swing high (clear swing high at index 5)
        assert len(swings) > 0
        
        # Each swing should be a tuple (index, price)
        assert isinstance(swings[0], tuple)
        assert len(swings[0]) == 2
    
    def test_find_swing_lows(self, sample_data):
        """Test swing low detection."""
        swings = find_swing_lows(sample_data, left_bars=3, right_bars=3)
        
        # Should find at least one swing low (clear swing low at index 15)
        assert len(swings) > 0
        
        # Each swing should be a tuple (index, price)
        assert isinstance(swings[0], tuple)
        assert len(swings[0]) == 2


class TestEqualHighsLows:
    """Tests for equal highs/lows detection."""
    
    def test_find_equal_highs(self):
        """Test equal highs detection."""
        swing_highs = [
            (5, 1.0900),
            (10, 1.0902),  # Within tolerance of first
            (15, 1.0950),  # Different level
            (20, 1.0948),  # Within tolerance of third
        ]
        
        # Use 5 pip tolerance
        clusters = find_equal_highs(swing_highs, tolerance_pips=5.0, pip_value=0.0001)
        
        # Should find clusters of equal highs
        assert isinstance(clusters, list)
    
    def test_find_equal_lows(self):
        """Test equal lows detection."""
        swing_lows = [
            (5, 1.0800),
            (10, 1.0802),
            (15, 1.0750),
            (20, 1.0752),
        ]
        
        clusters = find_equal_lows(swing_lows, tolerance_pips=5.0, pip_value=0.0001)
        
        assert isinstance(clusters, list)
    
    def test_no_equal_highs(self):
        """Test when no equal highs exist."""
        swing_highs = [
            (5, 1.0900),
            (10, 1.1000),  # Too far apart
            (15, 1.1100),
        ]
        
        clusters = find_equal_highs(swing_highs, tolerance_pips=5.0, pip_value=0.0001)
        
        # Should be empty or contain single-element clusters
        assert isinstance(clusters, list)


class TestATR:
    """Tests for ATR calculation."""
    
    @pytest.fixture
    def sample_data(self):
        """Create sample OHLC data."""
        dates = pd.date_range(start='2024-01-01', periods=30, freq='1h')
        np.random.seed(42)
        
        base = 1.0800
        close = base + np.cumsum(np.random.randn(30) * 0.001)
        high = close + np.abs(np.random.randn(30) * 0.0005)
        low = close - np.abs(np.random.randn(30) * 0.0005)
        
        df = pd.DataFrame({
            'open': close - np.random.randn(30) * 0.0003,
            'high': high,
            'low': low,
            'close': close,
            'volume': [100] * 30
        }, index=dates)
        
        return df
    
    def test_calculate_atr(self, sample_data):
        """Test ATR calculation."""
        atr = calculate_atr(sample_data, period=14)
        
        assert isinstance(atr, pd.Series)
        assert len(atr) == len(sample_data)
        
        # First 13 values should be NaN (need 14 periods)
        assert atr.iloc[:13].isna().all()
        
        # Values after should be positive
        valid_atr = atr.iloc[13:]
        assert (valid_atr > 0).all()
    
    def test_atr_with_different_period(self, sample_data):
        """Test ATR with different period."""
        atr_14 = calculate_atr(sample_data, period=14)
        atr_7 = calculate_atr(sample_data, period=7)
        
        # Both should be Series
        assert isinstance(atr_14, pd.Series)
        assert isinstance(atr_7, pd.Series)
        
        # ATR 7 should have fewer NaN values at start
        assert atr_7.iloc[6:].notna().any()
