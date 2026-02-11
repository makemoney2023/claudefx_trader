"""
Tests for Fair Value Gap Detector.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from trading_bot.analysis.fair_value_gap import (
    FVGDetector,
    FairValueGap,
    FVGType,
    FVGStatus,
    FVGAnalysis
)


@pytest.fixture
def sample_data_with_bullish_fvg():
    """Create sample data with a clear bullish FVG."""
    dates = pd.date_range(start='2024-01-01', periods=10, freq='1h')
    
    # Create data with bullish FVG at index 4-6
    # Candle 4 high < Candle 6 low = gap
    data = {
        'open':  [1.0800, 1.0810, 1.0820, 1.0830, 1.0825, 1.0900, 1.0950, 1.0940, 1.0945, 1.0950],
        'high':  [1.0815, 1.0825, 1.0835, 1.0840, 1.0835, 1.0920, 1.0960, 1.0955, 1.0960, 1.0965],
        'low':   [1.0795, 1.0805, 1.0815, 1.0825, 1.0820, 1.0890, 1.0940, 1.0930, 1.0935, 1.0940],
        'close': [1.0810, 1.0820, 1.0830, 1.0835, 1.0830, 1.0910, 1.0955, 1.0950, 1.0955, 1.0960],
        'volume': [100] * 10
    }
    
    # Ensure FVG: candle 4 high (1.0835) < candle 6 low (1.0940)
    # This creates a gap from 1.0835 to 1.0890
    data['high'][4] = 1.0835
    data['low'][6] = 1.0890
    
    df = pd.DataFrame(data, index=dates)
    return df


@pytest.fixture
def sample_data_with_bearish_fvg():
    """Create sample data with a clear bearish FVG."""
    dates = pd.date_range(start='2024-01-01', periods=10, freq='1h')
    
    # Create data with bearish FVG
    # Candle 4 low > Candle 6 high = gap
    data = {
        'open':  [1.0900, 1.0890, 1.0880, 1.0870, 1.0875, 1.0800, 1.0750, 1.0760, 1.0755, 1.0750],
        'high':  [1.0915, 1.0905, 1.0895, 1.0885, 1.0880, 1.0815, 1.0765, 1.0775, 1.0770, 1.0765],
        'low':   [1.0885, 1.0875, 1.0865, 1.0855, 1.0860, 1.0790, 1.0740, 1.0750, 1.0745, 1.0740],
        'close': [1.0890, 1.0880, 1.0870, 1.0860, 1.0865, 1.0795, 1.0745, 1.0755, 1.0750, 1.0745],
        'volume': [100] * 10
    }
    
    # Ensure FVG: candle 4 low (1.0860) > candle 6 high (1.0815)
    # This creates a gap from 1.0815 to 1.0860
    data['low'][4] = 1.0860
    data['high'][6] = 1.0815
    
    df = pd.DataFrame(data, index=dates)
    return df


@pytest.fixture
def detector():
    """Create a FVGDetector instance."""
    return FVGDetector(min_gap_pips=3.0, min_body_percentage=0.3, pip_value=0.0001)


class TestFVGDetector:
    """Tests for FVGDetector class."""
    
    def test_initialization(self, detector):
        """Test detector initialization."""
        assert detector.min_gap_pips == 3.0
        assert detector.min_body_percentage == 0.3
        assert detector.pip_value == 0.0001
    
    def test_detect_returns_fvg_analysis(self, detector, sample_data_with_bullish_fvg):
        """Test that detect returns FVGAnalysis object."""
        result = detector.detect(sample_data_with_bullish_fvg)
        
        assert isinstance(result, FVGAnalysis)
        assert hasattr(result, 'bullish_fvgs')
        assert hasattr(result, 'bearish_fvgs')
        assert hasattr(result, 'active_fvgs')
    
    def test_bullish_fvg_detection(self, detector, sample_data_with_bullish_fvg):
        """Test that bullish FVG is detected."""
        result = detector.detect(sample_data_with_bullish_fvg)
        
        # Should detect at least one bullish FVG (data crafted with clear bullish FVG)
        assert len(result.bullish_fvgs) > 0
    
    def test_bearish_fvg_detection(self, detector, sample_data_with_bearish_fvg):
        """Test that bearish FVG is detected."""
        result = detector.detect(sample_data_with_bearish_fvg)
        
        # Should detect at least one bearish FVG (data crafted with clear bearish FVG)
        assert len(result.bearish_fvgs) > 0
    
    def test_fvg_properties(self):
        """Test FairValueGap properties."""
        fvg = FairValueGap(
            type=FVGType.BULLISH,
            index=5,
            top=1.0890,
            bottom=1.0835,
            status=FVGStatus.UNFILLED
        )
        
        # Test midpoint calculation
        expected_midpoint = (1.0890 + 1.0835) / 2
        assert fvg.midpoint == pytest.approx(expected_midpoint)
        
        # Test validity
        assert fvg.is_valid == True
    
    def test_fvg_to_dict(self):
        """Test FairValueGap to_dict method."""
        fvg = FairValueGap(
            type=FVGType.BULLISH,
            index=5,
            top=1.0890,
            bottom=1.0835,
            status=FVGStatus.UNFILLED,
            gap_size=0.0055
        )
        
        result = fvg.to_dict()
        
        assert isinstance(result, dict)
        assert result['type'] == 'bullish'
        assert result['index'] == 5
        assert result['top'] == 1.0890
        assert result['bottom'] == 1.0835
    
    def test_empty_dataframe_handling(self, detector):
        """Test handling of empty DataFrame."""
        empty_df = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        
        result = detector.detect(empty_df)
        assert isinstance(result, FVGAnalysis)
        assert len(result.bullish_fvgs) == 0
        assert len(result.bearish_fvgs) == 0
    
    def test_small_dataframe_handling(self, detector):
        """Test handling of DataFrame with too few rows."""
        dates = pd.date_range(start='2024-01-01', periods=2, freq='1h')
        small_df = pd.DataFrame({
            'open': [1.0800, 1.0810],
            'high': [1.0815, 1.0820],
            'low': [1.0795, 1.0805],
            'close': [1.0810, 1.0815],
            'volume': [100, 150]
        }, index=dates)
        
        result = detector.detect(small_df)
        assert isinstance(result, FVGAnalysis)


class TestFVGTypes:
    """Tests for FVG types and status."""
    
    def test_fvg_type_values(self):
        """Test FVGType enum values."""
        assert FVGType.BULLISH.value == "bullish"
        assert FVGType.BEARISH.value == "bearish"
    
    def test_fvg_status_values(self):
        """Test FVGStatus enum values."""
        assert FVGStatus.UNFILLED.value == "unfilled"
        assert FVGStatus.PARTIALLY_FILLED.value == "partially_filled"
        assert FVGStatus.FILLED.value == "filled"


class TestFVGAnalysis:
    """Tests for FVGAnalysis dataclass."""
    
    def test_total_fvgs_property(self):
        """Test total_fvgs property."""
        fvg1 = FairValueGap(type=FVGType.BULLISH, index=5, top=1.09, bottom=1.08)
        fvg2 = FairValueGap(type=FVGType.BEARISH, index=10, top=1.10, bottom=1.09)
        
        analysis = FVGAnalysis(
            bullish_fvgs=[fvg1],
            bearish_fvgs=[fvg2]
        )
        
        assert analysis.total_fvgs == 2
    
    def test_to_dict_method(self):
        """Test FVGAnalysis to_dict method."""
        fvg = FairValueGap(type=FVGType.BULLISH, index=5, top=1.09, bottom=1.08)
        
        analysis = FVGAnalysis(
            bullish_fvgs=[fvg],
            bearish_fvgs=[],
            active_fvgs=[fvg]
        )
        
        result = analysis.to_dict()
        
        assert isinstance(result, dict)
        assert 'bullish_fvgs' in result
        assert 'bearish_fvgs' in result
        assert 'total_fvgs' in result
