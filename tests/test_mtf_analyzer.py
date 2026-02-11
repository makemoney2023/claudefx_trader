"""
Tests for Multi-Timeframe (MTF) Analyzer.

Updated to match current MTFAnalyzer API:
- analyze() returns MTFAnalysisResult
- get_htf_bias() returns dict
- should_trade_direction(direction, mtf_result) takes MTFAnalysisResult, not dict
- _fetch_data() returns pd.DataFrame (not dict)
- No check_fvg_confluence, check_ob_confluence, calculate_bias_strength methods
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


def _make_ohlcv_df(rows=50, base_price=1.08, trend="bullish"):
    """Create a sample OHLCV DataFrame for testing."""
    dates = [datetime(2026, 2, 1) + timedelta(hours=i) for i in range(rows)]
    data = []
    price = base_price
    for i in range(rows):
        if trend == "bullish":
            drift = 0.0002
        elif trend == "bearish":
            drift = -0.0002
        else:
            drift = 0.0
        
        o = price
        h = price + 0.002
        l = price - 0.002
        c = price + drift
        data.append({"time": dates[i], "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    
    return pd.DataFrame(data)


class TestMTFAnalyzer:
    """Tests for multi-timeframe analysis."""
    
    def test_initialization(self):
        """Test MTF analyzer initializes with correct timeframes."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer
        
        mock_mt5 = AsyncMock()
        analyzer = MTFAnalyzer(mt5_client=mock_mt5)
        
        assert 'D1' in analyzer.TIMEFRAMES
        assert 'H4' in analyzer.TIMEFRAMES
        assert 'H1' in analyzer.TIMEFRAMES
        assert 'M15' in analyzer.TIMEFRAMES
    
    @pytest.mark.asyncio
    async def test_get_htf_bias_bullish(self):
        """Test HTF bias detection for bullish market."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer
        
        mock_mt5 = AsyncMock()
        analyzer = MTFAnalyzer(mt5_client=mock_mt5)
        
        # Mock _fetch_data to return bullish DataFrames
        bullish_df = _make_ohlcv_df(50, 1.08, "bullish")
        analyzer._fetch_data = AsyncMock(return_value=bullish_df)
        
        bias = await analyzer.get_htf_bias("EURUSD")
        
        assert bias is not None
        assert isinstance(bias, dict)
        assert "overall_bias" in bias
        assert "daily_trend" in bias or "can_trade_long" in bias
    
    @pytest.mark.asyncio
    async def test_get_htf_bias_bearish(self):
        """Test HTF bias detection for bearish market."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer
        
        mock_mt5 = AsyncMock()
        analyzer = MTFAnalyzer(mt5_client=mock_mt5)
        
        # Mock _fetch_data to return bearish DataFrames
        bearish_df = _make_ohlcv_df(50, 1.09, "bearish")
        analyzer._fetch_data = AsyncMock(return_value=bearish_df)
        
        bias = await analyzer.get_htf_bias("EURUSD")
        
        assert bias is not None
        assert isinstance(bias, dict)
    
    @pytest.mark.asyncio
    async def test_should_trade_direction_aligned(self):
        """Test trade direction validation when aligned with HTF."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias
        )
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        # Create a bullish MTFAnalysisResult (3-value system)
        bullish_result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long='preferred',
            can_trade_short='counter_trend'
        )
        
        # Long trade aligned with bullish bias - should be allowed (preferred)
        assert analyzer.should_trade_direction("long", bullish_result) == True
        
        # Short trade against bullish bias - should also be allowed (counter_trend is tradeable)
        assert analyzer.should_trade_direction("short", bullish_result) == True
    
    @pytest.mark.asyncio
    async def test_should_trade_direction_misaligned(self):
        """Test trade direction validation when misaligned with HTF."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias
        )
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        # Create a bearish MTFAnalysisResult (3-value system)
        bearish_result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BEARISH,
            alignment=True,
            can_trade_long='counter_trend',
            can_trade_short='preferred'
        )
        
        # Long trade against bearish bias - allowed (counter_trend is tradeable)
        assert analyzer.should_trade_direction("long", bearish_result) == True
        
        # Short trade aligned with bearish bias - allowed (preferred)
        assert analyzer.should_trade_direction("short", bearish_result) == True
    
    @pytest.mark.asyncio
    async def test_conflicting_htf_bias(self):
        """Test handling of conflicting HTF signals."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias
        )
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        # When HTF is neutral/conflicting, both directions are counter_trend
        neutral_result = MTFAnalysisResult(
            overall_bias=TimeframeBias.NEUTRAL,
            alignment=False,
            can_trade_long='counter_trend',
            can_trade_short='counter_trend'
        )
        
        result = analyzer.should_trade_direction("long", neutral_result)
        assert isinstance(result, bool)
        assert result == True  # counter_trend is tradeable


class TestTimeframeAnalysis:
    """Tests for individual timeframe analysis."""
    
    @pytest.mark.asyncio
    async def test_analyze_single_timeframe(self):
        """Test analysis of a single timeframe using _analyze_timeframe."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, TimeframeBias
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        # Create a valid DataFrame
        df = _make_ohlcv_df(50, 1.08, "bullish")
        
        result = analyzer._analyze_timeframe(df, "H4")
        
        assert result is not None
        assert result.timeframe == "H4"
        assert isinstance(result.bias, TimeframeBias)
        assert result.trend in ["bullish", "bearish", "ranging", "unknown"]
    
    @pytest.mark.asyncio
    async def test_analyze_all_timeframes(self):
        """Test analysis across all configured timeframes via analyze()."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, MTFAnalysisResult
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        # Mock _fetch_data to return proper DataFrames
        df = _make_ohlcv_df(50, 1.08, "bullish")
        analyzer._fetch_data = AsyncMock(return_value=df)
        
        result = await analyzer.analyze("EURUSD")
        
        assert isinstance(result, MTFAnalysisResult)
        assert result.overall_bias is not None
        assert isinstance(result.alignment, bool)
        assert isinstance(result.can_trade_long, str)
        assert result.can_trade_long in ('preferred', 'counter_trend', 'no_data')
        assert isinstance(result.can_trade_short, str)
        assert result.can_trade_short in ('preferred', 'counter_trend', 'no_data')
    
    @pytest.mark.asyncio
    async def test_analyze_with_none_data(self):
        """Test analyze handles None data gracefully."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, MTFAnalysisResult
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        # Mock _fetch_data to return None (simulating MT5 data failure)
        analyzer._fetch_data = AsyncMock(return_value=None)
        
        result = await analyzer.analyze("EURUSD")
        
        assert isinstance(result, MTFAnalysisResult)
        # With no data, should get unknown bias -> no_data
        assert result.can_trade_long == 'no_data'
        assert result.can_trade_short == 'no_data'


class TestMTFResultObject:
    """Tests for MTFAnalysisResult dataclass."""
    
    def test_mtf_result_to_dict(self):
        """Test MTFAnalysisResult serialization."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalysisResult, TimeframeBias, TimeframeAnalysis
        )
        
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long='preferred',
            can_trade_short='counter_trend',
            htf_key_levels=[1.0800, 1.0900]
        )
        
        data = result.to_dict()
        
        assert data["overall_bias"] == "bullish"
        assert data["alignment"] == True
        assert data["can_trade_long"] == 'preferred'
        assert data["can_trade_short"] == 'counter_trend'
        assert len(data["htf_key_levels"]) == 2
    
    def test_bias_strength_from_alignment(self):
        """Test that alignment indicates bias strength."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalysisResult, TimeframeBias, TimeframeAnalysis
        )
        
        # All aligned = strong bias
        strong = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            daily_analysis=TimeframeAnalysis(timeframe="D1", bias=TimeframeBias.BULLISH, trend="bullish"),
            h4_analysis=TimeframeAnalysis(timeframe="H4", bias=TimeframeBias.BULLISH, trend="bullish"),
            h1_analysis=TimeframeAnalysis(timeframe="H1", bias=TimeframeBias.BULLISH, trend="bullish"),
            can_trade_long='preferred',
            can_trade_short='counter_trend'
        )
        
        assert strong.alignment == True
        assert strong.can_trade_long == 'preferred'
        
        # Conflicting = weak bias, no alignment
        weak = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=False,
            daily_analysis=TimeframeAnalysis(timeframe="D1", bias=TimeframeBias.BULLISH, trend="bullish"),
            h4_analysis=TimeframeAnalysis(timeframe="H4", bias=TimeframeBias.BEARISH, trend="bearish"),
            can_trade_long='counter_trend',
            can_trade_short='counter_trend'
        )
        
        assert weak.alignment == False
        assert weak.can_trade_long == 'counter_trend'


class TestContextForClaude:
    """Tests for building Claude context from MTF analysis."""
    
    def test_get_context_for_claude(self):
        """Test building context string for Claude."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias, TimeframeAnalysis
        )
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            daily_analysis=TimeframeAnalysis(
                timeframe="D1", bias=TimeframeBias.BULLISH,
                trend="bullish", confidence=0.8
            ),
            h4_analysis=TimeframeAnalysis(
                timeframe="H4", bias=TimeframeBias.BULLISH,
                trend="bullish", confidence=0.7
            ),
            can_trade_long='preferred',
            can_trade_short='counter_trend',
            htf_key_levels=[1.0800, 1.0900]
        )
        
        context = analyzer.get_context_for_claude(result)
        
        assert "BULLISH" in context
        assert "YES" in context  # Alignment
        assert "Daily" in context or "D1" in context
    
    def test_get_context_for_claude_includes_m5_m1(self):
        """Test that context string includes M5 and M1 when provided."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias, TimeframeAnalysis
        )
        
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            daily_analysis=TimeframeAnalysis(
                timeframe="D1", bias=TimeframeBias.BULLISH,
                trend="bullish", confidence=0.8
            ),
            h4_analysis=TimeframeAnalysis(
                timeframe="H4", bias=TimeframeBias.BULLISH,
                trend="bullish", confidence=0.7
            ),
            m15_analysis=TimeframeAnalysis(
                timeframe="M15", bias=TimeframeBias.BULLISH,
                trend="bullish", confidence=0.65
            ),
            m5_analysis=TimeframeAnalysis(
                timeframe="M5", bias=TimeframeBias.BEARISH,
                trend="bearish", confidence=0.6
            ),
            m1_analysis=TimeframeAnalysis(
                timeframe="M1", bias=TimeframeBias.NEUTRAL,
                trend="ranging", confidence=0.5
            ),
            can_trade_long='preferred',
            can_trade_short='counter_trend',
        )
        
        context = analyzer.get_context_for_claude(result)
        
        assert "M15" in context, "M15 missing from context"
        assert "M5" in context, "M5 missing from context"
        assert "M1" in context, "M1 missing from context"
        assert "bearish" in context  # M5 trend
        assert "ranging" in context  # M1 trend


# Fixtures
@pytest.fixture
def mock_mt5_with_data():
    """Create MT5 mock with sample OHLCV data."""
    mock_mt5 = AsyncMock()
    return mock_mt5


@pytest.fixture
def analyzer_with_mocks(mock_mt5_with_data):
    """Create MTF analyzer with mocked dependencies."""
    from trading_bot.analysis.mtf_analyzer import MTFAnalyzer
    
    analyzer = MTFAnalyzer(mt5_client=mock_mt5_with_data)
    
    # Mock _fetch_data to return proper DataFrames
    df = _make_ohlcv_df(50, 1.08, "bullish")
    analyzer._fetch_data = AsyncMock(return_value=df)
    
    return analyzer
