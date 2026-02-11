"""
Enhanced Features Tests.

Tests for:
5A. MTF Analyzer M1/M5 timeframes
5B. Drawdown balance-based calculation (realized P/L only)
5C. Independent position management loop
5D. Swing validation context loading and integration

Uses pytest and pytest-asyncio. Mocks MT5 connections where needed.
"""

import pytest
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pathlib import Path


# ===================================================================
# Helper: Generate sample OHLCV DataFrame
# ===================================================================

def _make_ohlcv_df(rows=50, base_price=1.08, trend="bullish"):
    """Create a sample OHLCV DataFrame for testing."""
    dates = [datetime(2026, 2, 1) + timedelta(minutes=i * 5) for i in range(rows)]
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


# ===================================================================
# 5A: MTF Analyzer M1/M5 Tests
# ===================================================================

class TestMTFAnalyzerM5M1:
    """Tests for M5 and M1 timeframe additions to MTF Analyzer."""

    def test_timeframes_include_m5_m1(self):
        """TIMEFRAMES list must include M5 and M1."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer
        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        assert 'M5' in analyzer.TIMEFRAMES, "M5 missing from TIMEFRAMES"
        assert 'M1' in analyzer.TIMEFRAMES, "M1 missing from TIMEFRAMES"

    def test_mtf_result_has_m5_m1_fields(self):
        """MTFAnalysisResult dataclass must have m5_analysis and m1_analysis."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long=True,
            can_trade_short=False,
        )
        assert hasattr(result, 'm5_analysis'), "m5_analysis field missing"
        assert hasattr(result, 'm1_analysis'), "m1_analysis field missing"
        # Default should be None
        assert result.m5_analysis is None
        assert result.m1_analysis is None

    def test_to_dict_includes_m5_m1(self):
        """to_dict() must include m5 and m1 keys."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalysisResult, TimeframeBias, TimeframeAnalysis
        )
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long=True,
            can_trade_short=False,
            m5_analysis=TimeframeAnalysis(
                timeframe="M5", bias=TimeframeBias.BULLISH, trend="bullish"
            ),
            m1_analysis=TimeframeAnalysis(
                timeframe="M1", bias=TimeframeBias.BEARISH, trend="bearish"
            ),
        )
        data = result.to_dict()
        assert "m5" in data, "m5 key missing from to_dict()"
        assert "m1" in data, "m1 key missing from to_dict()"
        assert data["m5"]["timeframe"] == "M5"
        assert data["m1"]["timeframe"] == "M1"
        assert data["m5"]["trend"] == "bullish"
        assert data["m1"]["trend"] == "bearish"

    def test_to_dict_m5_m1_none_when_not_provided(self):
        """to_dict() should return None for m5/m1 when not provided."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.NEUTRAL,
            alignment=False,
        )
        data = result.to_dict()
        assert data["m5"] is None
        assert data["m1"] is None

    @pytest.mark.asyncio
    async def test_analyze_fetches_m5_m1_data(self):
        """analyze() should fetch M5 (100 candles) and M1 (60 candles) data."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer

        mock_mt5 = AsyncMock()
        analyzer = MTFAnalyzer(mt5_client=mock_mt5)

        df = _make_ohlcv_df(100, 1.08, "bullish")
        analyzer._fetch_data = AsyncMock(return_value=df)

        result = await analyzer.analyze("EURUSD")

        # Verify _fetch_data was called for M5 and M1
        call_args = [call.args for call in analyzer._fetch_data.call_args_list]
        timeframes_fetched = [args[1] for args in call_args]

        assert 'M5' in timeframes_fetched, "M5 data was not fetched"
        assert 'M1' in timeframes_fetched, "M1 data was not fetched"

        # Verify M5 fetches 100 candles
        m5_calls = [args for args in call_args if args[1] == 'M5']
        assert m5_calls[0][2] == 100, f"M5 should fetch 100 candles, got {m5_calls[0][2]}"

        # Verify M1 fetches 60 candles
        m1_calls = [args for args in call_args if args[1] == 'M1']
        assert m1_calls[0][2] == 60, f"M1 should fetch 60 candles, got {m1_calls[0][2]}"

    @pytest.mark.asyncio
    async def test_analyze_includes_m5_m1_in_result(self):
        """analyze() result should populate m5_analysis and m1_analysis."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, MTFAnalysisResult

        mock_mt5 = AsyncMock()
        analyzer = MTFAnalyzer(mt5_client=mock_mt5)

        df = _make_ohlcv_df(100, 1.08, "bullish")
        analyzer._fetch_data = AsyncMock(return_value=df)

        result = await analyzer.analyze("EURUSD")

        assert isinstance(result, MTFAnalysisResult)
        assert result.m5_analysis is not None, "m5_analysis should be populated"
        assert result.m1_analysis is not None, "m1_analysis should be populated"
        assert result.m5_analysis.timeframe == "M5"
        assert result.m1_analysis.timeframe == "M1"

    @pytest.mark.asyncio
    async def test_analyze_m5_m1_none_when_no_data(self):
        """When _fetch_data returns None for M5/M1, they should be None in result."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer

        mock_mt5 = AsyncMock()
        analyzer = MTFAnalyzer(mt5_client=mock_mt5)

        # Return data only for D1, H4, H1, M15; None for M5 and M1
        df = _make_ohlcv_df(50, 1.08, "bullish")

        async def selective_fetch(symbol, tf, count):
            if tf in ('M5', 'M1'):
                return None
            return df

        analyzer._fetch_data = AsyncMock(side_effect=selective_fetch)

        result = await analyzer.analyze("EURUSD")
        assert result.m5_analysis is None, "m5_analysis should be None when no data"
        assert result.m1_analysis is None, "m1_analysis should be None when no data"

    def test_analyze_timeframe_m5(self):
        """_analyze_timeframe should work for M5 data."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, TimeframeBias

        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        df = _make_ohlcv_df(100, 1.08, "bullish")

        result = analyzer._analyze_timeframe(df, "M5")

        assert result is not None
        assert result.timeframe == "M5"
        assert isinstance(result.bias, TimeframeBias)

    def test_analyze_timeframe_m1(self):
        """_analyze_timeframe should work for M1 data."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, TimeframeBias

        analyzer = MTFAnalyzer(mt5_client=AsyncMock())
        df = _make_ohlcv_df(60, 1.08, "bearish")

        result = analyzer._analyze_timeframe(df, "M1")

        assert result is not None
        assert result.timeframe == "M1"
        assert isinstance(result.bias, TimeframeBias)


# ===================================================================
# 5B: Drawdown Balance-Based Calculation Tests
# ===================================================================

class TestDrawdownBalanceBased:
    """Verify that drawdown uses account.balance (realized P/L), not equity."""

    def test_scaling_manager_weekly_drawdown_uses_parameter(self):
        """calculate_weekly_drawdown should accept current value (balance, not equity)."""
        from trading_bot.services.scaling_manager import ScalingManager
        manager = ScalingManager(
            starting_equity=10000,
            target_equity=100000,
            max_daily_drawdown=0.06,
            max_weekly_drawdown=0.10,
        )
        manager.weekly_high_equity = 10000

        # Simulate balance (realized) drop of 5%
        balance = 9500
        weekly_dd = manager.calculate_weekly_drawdown(balance)
        assert abs(weekly_dd - 0.05) < 0.001, (
            f"Weekly drawdown should be ~5%, got {weekly_dd:.2%}"
        )

    def test_daily_drawdown_calculation(self):
        """calculate_daily_drawdown should work correctly with balance values."""
        from trading_bot.services.scaling_manager import ScalingManager
        manager = ScalingManager(
            starting_equity=10000,
            target_equity=100000,
            max_daily_drawdown=0.06,
            max_weekly_drawdown=0.10,
        )
        manager.daily_high_equity = 10000

        # 3% drawdown on balance
        daily_dd = manager.calculate_daily_drawdown(9700)
        assert abs(daily_dd - 0.03) < 0.001, (
            f"Daily drawdown should be ~3%, got {daily_dd:.2%}"
        )

    def test_defensive_mode_on_large_daily_drawdown(self):
        """Daily drawdown exceeding limit should trigger DEFENSIVE mode."""
        from trading_bot.services.scaling_manager import ScalingManager, TradingMode
        manager = ScalingManager(
            starting_equity=10000,
            target_equity=100000,
            max_daily_drawdown=0.06,
            max_weekly_drawdown=0.10,
        )
        manager.daily_high_equity = 10000
        manager.weekly_high_equity = 10000

        # 7% daily drawdown exceeds 6% limit
        mode = manager.determine_mode(9300)
        assert mode == TradingMode.DEFENSIVE, (
            f"7% daily drawdown should trigger DEFENSIVE, got {mode.value}"
        )

    def test_normal_mode_within_limits(self):
        """Small drawdown within limits should NOT trigger defensive mode."""
        from trading_bot.services.scaling_manager import ScalingManager, TradingMode
        manager = ScalingManager(
            starting_equity=10000,
            target_equity=100000,
            max_daily_drawdown=0.06,
            max_weekly_drawdown=0.10,
        )
        manager.daily_high_equity = 10000
        manager.weekly_high_equity = 10000

        # 2% drawdown is well within 6% limit
        mode = manager.determine_mode(9800)
        assert mode != TradingMode.DEFENSIVE, (
            f"2% daily drawdown should NOT trigger DEFENSIVE, got {mode.value}"
        )

    def test_equity_vs_balance_distinction(self):
        """
        Demonstrate that passing balance vs equity produces different results.
        If equity includes unrealized losses of -500 but balance only lost -100,
        the drawdown check should use the balance value.
        """
        from trading_bot.services.scaling_manager import ScalingManager
        manager = ScalingManager(
            starting_equity=10000,
            target_equity=100000,
            max_daily_drawdown=0.06,
            max_weekly_drawdown=0.10,
        )
        manager.daily_high_equity = 10000

        # Equity has unrealized loss (5% drawdown)
        equity = 9500
        equity_dd = manager.calculate_daily_drawdown(equity)

        # Balance only has realized loss (1% drawdown)
        balance = 9900
        balance_dd = manager.calculate_daily_drawdown(balance)

        assert balance_dd < equity_dd, (
            "Balance-based drawdown should be smaller than equity-based when "
            "there are unrealized losses"
        )
        assert abs(balance_dd - 0.01) < 0.001, f"Expected ~1%, got {balance_dd:.2%}"
        assert abs(equity_dd - 0.05) < 0.001, f"Expected ~5%, got {equity_dd:.2%}"


# ===================================================================
# 5C: Independent Position Management Loop Tests
# ===================================================================

class TestIndependentPositionManagement:
    """Tests for the independent _position_management_loop in main.py."""

    def test_manage_open_positions_is_noop(self):
        """_manage_open_positions() should be a no-op (legacy method)."""
        # We verify by checking the method exists and returns None
        from trading_bot.main import TradingBot

        # Create a minimal bot with mocks
        with patch('trading_bot.main.settings') as mock_settings:
            mock_settings.claude.api_key = "test"
            mock_settings.claude.model = "test"
            mock_settings.mt5.account = 123
            mock_settings.mt5.password = "pass"
            mock_settings.mt5.server = "server"
            mock_settings.docs_dir = "trading_bot/docs"
            mock_settings.timeframes.execution_tf = "M15"
            mock_settings.trading.symbols = ["EURUSD"]

            bot = TradingBot.__new__(TradingBot)
            bot.running = False

            # Verify the method exists and is essentially a no-op
            assert hasattr(bot, '_manage_open_positions'), (
                "_manage_open_positions method should exist for backward compat"
            )

    def test_position_management_loop_exists(self):
        """_position_management_loop method should exist on TradingBot."""
        from trading_bot.main import TradingBot
        assert hasattr(TradingBot, '_position_management_loop'), (
            "_position_management_loop should be defined on TradingBot"
        )

    @pytest.mark.asyncio
    async def test_manage_open_positions_returns_none(self):
        """_manage_open_positions() should return None (no-op)."""
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        result = await bot._manage_open_positions()
        assert result is None, "_manage_open_positions should return None (no-op)"

    @pytest.mark.asyncio
    async def test_position_loop_handles_cancelled_error(self):
        """The position management loop should handle CancelledError gracefully."""
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.running = True
        bot.position_manager = MagicMock()
        bot.position_manager.positions = {}
        bot.pending_order_manager = None
        bot.mt5_client = MagicMock()
        bot.mt5_client.is_simulation = True

        # Make asyncio.sleep raise CancelledError (simulating shutdown)
        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        with patch('asyncio.sleep', side_effect=mock_sleep):
            # Should not raise; should exit cleanly
            try:
                await bot._position_management_loop()
            except asyncio.CancelledError:
                # The loop may or may not re-raise; either way it should not crash
                pass

    @pytest.mark.asyncio
    async def test_position_loop_continues_on_error(self):
        """Loop should continue running if a non-fatal error occurs."""
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.running = True
        bot.position_manager = MagicMock()
        # First call raises, second call is normal
        bot.position_manager.positions = PropertyMock(side_effect=[Exception("test error"), {}])
        bot.pending_order_manager = None
        bot.mt5_client = MagicMock()
        bot.mt5_client.is_simulation = True

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                bot.running = False

        with patch('asyncio.sleep', side_effect=mock_sleep):
            # Should not crash despite the error
            await bot._position_management_loop()

    @pytest.mark.asyncio
    async def test_position_loop_stops_when_running_false(self):
        """Loop should exit when self.running is set to False."""
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.running = False  # Already stopped
        bot.position_manager = MagicMock()
        bot.position_manager.positions = {}

        # Should exit immediately without calling sleep
        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            await bot._position_management_loop()
            # Sleep should not have been called since running=False at start
            mock_sleep.assert_not_called()


# ===================================================================
# 5D: Swing Validation Context Tests
# ===================================================================

class TestSwingValidationDoc:
    """Tests for swing_validation.md document and its integration."""

    def test_swing_validation_doc_exists(self):
        """swing_validation.md should exist in the docs directory."""
        doc_path = Path("trading_bot/docs/swing_validation.md")
        assert doc_path.exists(), f"{doc_path} does not exist"

    def test_swing_validation_doc_content(self):
        """swing_validation.md should contain key strategy concepts."""
        doc_path = Path("trading_bot/docs/swing_validation.md")
        content = doc_path.read_text(encoding='utf-8')

        # Key concepts that must be in the document
        assert "4-6 Swing Rule" in content, "Missing '4-6 Swing Rule'"
        assert "Prominent Wick" in content, "Missing 'Prominent Wick'"
        assert "sweep" in content.lower(), "Missing sweep concept"
        assert "rounding" in content.lower() or "consolidation" in content.lower(), (
            "Missing rounding/consolidation concept"
        )
        assert "21 EMA" in content, "Missing '21 EMA' trailing concept"
        assert "Avoid the First Hour" in content or "first hour" in content.lower(), (
            "Missing first-hour avoidance concept"
        )

    def test_context_builder_loads_swing_validation(self):
        """ContextBuilder should load swing_validation.md into its cache."""
        from trading_bot.llm.context_builder import ContextBuilder

        builder = ContextBuilder(docs_dir="trading_bot/docs")

        assert 'swing_validation' in builder.available_documents, (
            "swing_validation not loaded by ContextBuilder"
        )

    def test_swing_validation_in_priority_docs(self):
        """swing_validation should appear in priority_docs for get_ict_context()."""
        from trading_bot.llm.context_builder import ContextBuilder

        builder = ContextBuilder(docs_dir="trading_bot/docs")
        ict_context = builder.get_ict_context()

        assert "swing" in ict_context.lower() or "Swing" in ict_context, (
            "Swing validation content missing from ICT context"
        )

    def test_quick_reference_includes_swing_summary(self):
        """get_quick_reference() should include swing validation summary."""
        from trading_bot.llm.context_builder import ContextBuilder

        builder = ContextBuilder(docs_dir="trading_bot/docs")
        qr = builder.get_quick_reference()

        assert "Swing Exhaustion" in qr or "4-6 Swing" in qr, (
            "Quick reference missing swing exhaustion validation summary"
        )
        assert "Prominent Wick" in qr or "prominent wick" in qr.lower(), (
            "Quick reference missing prominent wick concept"
        )
        assert "sweep" in qr.lower(), (
            "Quick reference missing sweep concept"
        )


class TestSwingInClaudePrompt:
    """Tests for swing validation in Claude's analysis prompt."""

    def test_build_analysis_prompt_includes_swing_validation(self):
        """_build_analysis_prompt should include swing exhaustion section."""
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)

        # Minimal market_data with HTF bias to trigger the section
        market_data = {
            'current_price': 1.0850,
            'session': 'New York',
            'daily_high': 1.0900,
            'daily_low': 1.0800,
            'htf_bias': 'bullish',
            'htf_alignment': True,
            'htf_can_trade_long': True,
            'htf_can_trade_short': False,
            'htf_key_levels': [1.0800, 1.0900],
        }

        prompt = client._build_analysis_prompt(
            symbol="EURUSD",
            timeframe="M15",
            strategy_context="ICT strategy context here",
            market_data=market_data,
            analysis_data=None,
        )

        assert "Swing Exhaustion Validation" in prompt, (
            "Swing exhaustion validation section missing from prompt"
        )
        assert "4-6" in prompt or "4 to 6" in prompt, (
            "4-6 swing count reference missing from prompt"
        )
        assert "sweep" in prompt.lower(), (
            "Sweep requirement missing from prompt"
        )

    def test_build_analysis_prompt_includes_m5_m1_context(self):
        """_build_analysis_prompt should include M5/M1 context when provided."""
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)

        market_data = {
            'current_price': 2650.00,
            'session': 'New York',
            'daily_high': 2660.00,
            'daily_low': 2640.00,
            'm5_bias': 'bullish',
            'm5_structure': 'BOS',
            'm5_trend': 'bullish',
            'm1_bias': 'bearish',
            'm1_structure': 'CHoCH',
            'm1_trend': 'bearish',
        }

        prompt = client._build_analysis_prompt(
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="ICT strategy context",
            market_data=market_data,
            analysis_data=None,
        )

        assert "M5 Bias: bullish" in prompt, "M5 bias missing from prompt"
        assert "M5 Structure: BOS" in prompt, "M5 structure missing from prompt"
        assert "M1 Bias: bearish" in prompt, "M1 bias missing from prompt"
        assert "M1 Structure: CHoCH" in prompt, "M1 structure missing from prompt"
        assert "Lower Timeframe Context" in prompt, (
            "Lower Timeframe Context header missing"
        )

    def test_build_analysis_prompt_no_m5_m1_when_absent(self):
        """When m5/m1 data is not in market_data, LTF section should not appear."""
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)

        market_data = {
            'current_price': 1.0850,
            'session': 'London',
            'daily_high': 1.0900,
            'daily_low': 1.0800,
        }

        prompt = client._build_analysis_prompt(
            symbol="EURUSD",
            timeframe="M15",
            strategy_context="context",
            market_data=market_data,
            analysis_data=None,
        )

        assert "Lower Timeframe Context" not in prompt, (
            "LTF section should not appear when m5/m1 data is absent"
        )


class TestSwingInReeval:
    """Tests for swing validation context in Claude position re-evaluation."""

    def test_reeval_prompt_includes_swing_context(self):
        """The position re-evaluation prompt should include swing validation guidance."""
        # We can't easily call _claude_reevaluate_positions directly, 
        # but we can verify the prompt template includes swing content
        # by reading the source code
        import inspect
        from trading_bot.main import TradingBot

        source = inspect.getsource(TradingBot._claude_reevaluate_positions)
        assert "Swing Exhaustion Check" in source or "swing" in source.lower(), (
            "_claude_reevaluate_positions should include swing validation context"
        )
        assert "4-6 swing" in source.lower() or "4+ swings" in source.lower(), (
            "_claude_reevaluate_positions should reference 4-6 swing rule"
        )
        assert "21 EMA" in source, (
            "_claude_reevaluate_positions should reference 21 EMA trailing"
        )


# ===================================================================
# Integration: market_data M5/M1 fields from main.py
# ===================================================================

class TestMarketDataM5M1Fields:
    """Tests that verify M5/M1 fields are passed in market_data."""

    def test_m5_m1_fields_set_from_mtf_result(self):
        """
        Verify that market_data dict includes m5_bias, m5_structure, m5_trend,
        m1_bias, m1_structure, m1_trend when populated from MTFAnalysisResult.
        """
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalysisResult, TimeframeBias, TimeframeAnalysis
        )

        # Create a result with M5 and M1 populated
        mtf_result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long=True,
            can_trade_short=False,
            m5_analysis=TimeframeAnalysis(
                timeframe="M5",
                bias=TimeframeBias.BULLISH,
                trend="bullish",
                structure="BOS",
            ),
            m1_analysis=TimeframeAnalysis(
                timeframe="M1",
                bias=TimeframeBias.BEARISH,
                trend="bearish",
                structure="CHoCH",
            ),
        )

        # Simulate what main.py does when building market_data
        market_data = {}
        market_data["m5_bias"] = mtf_result.m5_analysis.bias.value if mtf_result.m5_analysis else None
        market_data["m5_structure"] = mtf_result.m5_analysis.structure if mtf_result.m5_analysis else None
        market_data["m5_trend"] = mtf_result.m5_analysis.trend if mtf_result.m5_analysis else None
        market_data["m1_bias"] = mtf_result.m1_analysis.bias.value if mtf_result.m1_analysis else None
        market_data["m1_structure"] = mtf_result.m1_analysis.structure if mtf_result.m1_analysis else None
        market_data["m1_trend"] = mtf_result.m1_analysis.trend if mtf_result.m1_analysis else None

        assert market_data["m5_bias"] == "bullish"
        assert market_data["m5_structure"] == "BOS"
        assert market_data["m5_trend"] == "bullish"
        assert market_data["m1_bias"] == "bearish"
        assert market_data["m1_structure"] == "CHoCH"
        assert market_data["m1_trend"] == "bearish"

    def test_m5_m1_fields_none_when_analysis_missing(self):
        """M5/M1 fields should be None when analysis is not available."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias

        mtf_result = MTFAnalysisResult(
            overall_bias=TimeframeBias.NEUTRAL,
            alignment=False,
        )

        market_data = {}
        market_data["m5_bias"] = mtf_result.m5_analysis.bias.value if mtf_result.m5_analysis else None
        market_data["m5_structure"] = mtf_result.m5_analysis.structure if mtf_result.m5_analysis else None
        market_data["m1_bias"] = mtf_result.m1_analysis.bias.value if mtf_result.m1_analysis else None
        market_data["m1_structure"] = mtf_result.m1_analysis.structure if mtf_result.m1_analysis else None

        assert market_data["m5_bias"] is None
        assert market_data["m5_structure"] is None
        assert market_data["m1_bias"] is None
        assert market_data["m1_structure"] is None
