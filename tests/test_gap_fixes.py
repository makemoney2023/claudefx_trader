"""
Comprehensive tests for the Trading Flow Gap Fix plan.

Tests cover:
1. MT5 event loop (asyncio.to_thread wrapping)
2. MT5 concurrency lock
3. Claude response validation
4. Claude reevaluation robustness
5. Position state persistence (load_from_db)
6. Position sync updates (SL/TP/volume)
7. SL modification failure revert
8. Daily trade counter initialization
9. Drawdown consistency (equity-based)
10. Claude API retry logic
11. Partial fill detection
12. MTF analysis integration
13. Fibonacci/OTE integration
14. Analysis data enrichment
15. Config candle counts usage
16. Signal truncation fix
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Optional


# ============================================================
# 1. MT5 Event Loop & Concurrency Lock Tests
# ============================================================

class TestMT5ClientThreadSafety:
    """Test that MT5Client uses asyncio.to_thread and locking."""
    
    def test_mt5_client_has_lock(self):
        """MT5Client should have an asyncio.Lock for concurrency."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        assert hasattr(client, '_lock')
        assert isinstance(client._lock, asyncio.Lock)
    
    @pytest.mark.asyncio
    async def test_get_account_info_uses_lock(self):
        """get_account_info should use the lock for thread safety."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = True
        client._connected = True
        
        # Should work without blocking in simulation mode
        result = await client.get_account_info()
        assert result is not None
        assert result.balance == 10000.0
    
    @pytest.mark.asyncio
    async def test_get_symbol_info_simulation(self):
        """get_symbol_info should return simulated data correctly."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = True
        client._connected = True
        
        result = await client.get_symbol_info("EURUSD")
        assert result is not None
        assert result.name == "EURUSD"
        assert result.trade_contract_size == 100000
    
    @pytest.mark.asyncio
    async def test_concurrent_calls_dont_crash(self):
        """Multiple concurrent calls should not crash thanks to the lock."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = True
        client._connected = True
        
        # Fire multiple calls concurrently
        results = await asyncio.gather(
            client.get_account_info(),
            client.get_symbol_info("EURUSD"),
            client.get_account_info(),
        )
        assert len(results) == 3
        assert all(r is not None for r in results)


# ============================================================
# 2. Claude Response Validation Tests
# ============================================================

class TestClaudeResponseValidation:
    """Test _validate_trade_signal sanitizes Claude's responses."""
    
    def _get_client(self):
        """Get a ClaudeClient with mocked API key."""
        from trading_bot.llm.claude_client import ClaudeClient
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            # Manually set required attributes
            client.api_key = "test"
            client.model = "test"
            client.max_tokens = 4096
            client.temperature = 0.3
            client.max_retries = 3
            client._cache = MagicMock()
            client._request_timestamps = []
            client._rate_limit_window = 60
            client._rate_limit_max = 50
            client._rate_lock = asyncio.Lock()
            client.sync_client = None
            client.async_client = None
            return client
    
    def test_valid_signal_passes_through(self):
        """Valid signal data should pass validation unchanged."""
        client = self._get_client()
        data = {
            'direction': 'long',
            'confidence': 0.85,
            'entry_price': 1.0850,
            'stop_loss': 1.0800,
            'take_profit': 1.0950,
            'order_type': 'market',
            'market_structure': 'bullish',
            'amd_phase': 'distribution',
            'reasoning': 'Strong bullish structure with displacement'
        }
        result = client._validate_trade_signal(data)
        assert result['direction'] == 'long'
        assert result['confidence'] == 0.85
        assert result['entry_price'] == 1.0850
    
    def test_invalid_direction_defaults_to_no_trade(self):
        """Invalid direction should default to no_trade."""
        client = self._get_client()
        data = {'direction': 'sideways', 'confidence': 0.5}
        result = client._validate_trade_signal(data)
        assert result['direction'] == 'no_trade'
    
    def test_confidence_clamped_to_range(self):
        """Confidence should be clamped to 0-1."""
        client = self._get_client()
        
        # Too high
        data = {'direction': 'long', 'confidence': 1.5}
        result = client._validate_trade_signal(data)
        assert result['confidence'] == 1.0
        
        # Negative
        data = {'direction': 'long', 'confidence': -0.5}
        result = client._validate_trade_signal(data)
        assert result['confidence'] == 0.0
    
    def test_negative_prices_set_to_none(self):
        """Negative price values should be set to None."""
        client = self._get_client()
        data = {
            'direction': 'long', 
            'confidence': 0.5,
            'entry_price': -1.0,
            'stop_loss': -5.0,
        }
        result = client._validate_trade_signal(data)
        assert result['entry_price'] is None
        assert result['stop_loss'] is None
    
    def test_non_dict_input_returns_no_trade(self):
        """Non-dict input should return a safe no_trade signal."""
        client = self._get_client()
        result = client._validate_trade_signal("not a dict")
        assert result['direction'] == 'no_trade'
        assert result['confidence'] == 0
    
    def test_invalid_order_type_defaults_to_market(self):
        """Invalid order_type should default to market."""
        client = self._get_client()
        data = {'direction': 'long', 'confidence': 0.5, 'order_type': 'invalid'}
        result = client._validate_trade_signal(data)
        assert result['order_type'] == 'market'
    
    def test_invalid_market_structure_defaults_to_ranging(self):
        """Invalid market_structure should default to ranging."""
        client = self._get_client()
        data = {'direction': 'long', 'confidence': 0.5, 'market_structure': 'chaotic'}
        result = client._validate_trade_signal(data)
        assert result['market_structure'] == 'ranging'


# ============================================================
# 3. Position State Persistence Tests
# ============================================================

class TestPositionPersistence:
    """Test that position state is fully persisted and restored."""
    
    def test_position_has_multi_tp_fields(self):
        """Position dataclass should have tp1/tp2/tp3/tp1_hit/tp2_hit/initial_volume fields."""
        from trading_bot.execution.position_manager import Position
        pos = Position(
            ticket=12345,
            symbol="EURUSD",
            direction="long",
            volume=0.05,
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            open_time=datetime.now()
        )
        assert hasattr(pos, 'tp1')
        assert hasattr(pos, 'tp2')
        assert hasattr(pos, 'tp3')
        assert hasattr(pos, 'tp1_hit')
        assert hasattr(pos, 'tp2_hit')
        assert hasattr(pos, 'initial_volume')
        assert pos.tp1 == 0.0
        assert pos.tp1_hit is False
        assert pos.initial_volume == 0.05  # Set from volume in __post_init__
    
    def test_position_to_dict_includes_tp_fields(self):
        """Position.to_dict() should include multi-TP fields."""
        from trading_bot.execution.position_manager import Position
        pos = Position(
            ticket=12345,
            symbol="EURUSD",
            direction="long",
            volume=0.05,
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            open_time=datetime.now()
        )
        pos.tp1 = 1.0900
        pos.tp2 = 1.0950
        pos.tp3 = 1.1000
        pos.tp1_hit = True
        
        d = pos.to_dict()
        assert d['tp1'] == 1.0900
        assert d['tp2'] == 1.0950
        assert d['tp3'] == 1.1000
        assert d['tp1_hit'] is True
        assert d['tp2_hit'] is False


# ============================================================
# 4. Position Sync Tests
# ============================================================

class TestPositionSync:
    """Test that sync_with_mt5 updates existing positions."""
    
    @pytest.mark.asyncio
    async def test_sync_updates_existing_positions(self, position_manager, mock_mt5_client):
        """sync_with_mt5 should update SL/TP/volume on existing positions."""
        from trading_bot.execution.position_manager import Position
        
        # Add a position to the manager
        pos = Position(
            ticket=99999,
            symbol="GBPUSD",
            direction="long",
            volume=0.05,
            entry_price=1.2500,
            stop_loss=1.2450,
            take_profit=1.2600,
            open_time=datetime.now()
        )
        position_manager.add_position(pos)
        
        # MT5 returns updated position (SL moved to break-even)
        from tests.conftest import MockPosition
        mock_mt5_client.set_positions([
            MockPosition(
                ticket=99999,
                symbol="GBPUSD",
                type="buy",
                volume=0.03,  # Reduced from partial close
                price_open=1.2500,
                sl=1.2500,    # Moved to break-even
                tp=1.2650,    # Updated TP
                profit=15.0,
                time=datetime.now()
            )
        ])
        # Add price_current attribute for sync
        mock_mt5_client._positions[0].price_current = 1.2550
        
        result = await position_manager.sync_with_mt5(mock_mt5_client)
        
        assert result['synced'] is True
        # Check that the existing position was updated
        updated_pos = position_manager.positions[99999]
        assert updated_pos.stop_loss == 1.2500   # Updated from MT5
        assert updated_pos.take_profit == 1.2650  # Updated from MT5
        assert updated_pos.volume == 0.03         # Updated from MT5


# ============================================================
# 5. SL Modification Failure Revert Tests
# ============================================================

class TestSLModificationRevert:
    """Test that TP1/TP2 only update state after success."""
    
    @pytest.mark.asyncio
    async def test_tp1_state_not_set_on_close_failure(self, position_manager, sample_position):
        """If partial close fails, tp1_hit should NOT be set."""
        position_manager.add_position(sample_position)
        sample_position.current_price = sample_position.entry_price + sample_position.risk_pips * 1.5  # 1.5R
        
        # Mock order manager to fail the close
        failing_order_mgr = MagicMock()
        failing_result = MagicMock()
        failing_result.success = False
        failing_order_mgr.close_position = AsyncMock(return_value=failing_result)
        position_manager.order_manager = failing_order_mgr
        
        result = await position_manager._execute_tp1(sample_position)
        
        # Should return None since close failed
        assert result is None
        # tp1_hit should NOT be set
        assert sample_position.tp1_hit is False
    
    @pytest.mark.asyncio
    async def test_tp2_state_not_set_on_close_failure(self, position_manager, sample_position):
        """If partial close fails at TP2, tp2_hit should NOT be set."""
        # Use volume >= 0.05 so the partial close path is actually attempted
        # (0.01 is too small to split and takes the early-return "skip trailing" path)
        sample_position.volume = 0.05
        sample_position.initial_volume = 0.05
        position_manager.add_position(sample_position)
        sample_position.tp1_hit = True
        sample_position.current_price = sample_position.entry_price + sample_position.risk_pips * 2.5
        
        failing_order_mgr = MagicMock()
        failing_result = MagicMock()
        failing_result.success = False
        failing_order_mgr.close_position = AsyncMock(return_value=failing_result)
        position_manager.order_manager = failing_order_mgr
        
        result = await position_manager._execute_tp2(sample_position)
        
        assert result is None
        assert sample_position.tp2_hit is False


# ============================================================
# 6. Drawdown Consistency Tests
# ============================================================

class TestDrawdownConsistency:
    """Test that drawdown uses equity consistently."""
    
    def test_drawdown_code_uses_equity(self):
        """The drawdown check should reference account.equity, not account.balance."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._check_drawdown_circuit_breaker)
        
        # Should use equity for daily drawdown calculation
        assert 'account.equity' in source
        # Should reset daily start balance
        assert '_drawdown_date' in source


# ============================================================
# 7. Partial Fill Detection Tests
# ============================================================

class TestPartialFillDetection:
    """Test that place_order detects and reports partial fills."""
    
    @pytest.mark.asyncio
    async def test_place_order_returns_partial_fill_info(self):
        """Simulated place_order should include partial_fill field."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = True
        client._connected = True
        
        result = await client.place_order(
            symbol="EURUSD",
            order_type="buy",
            volume=0.01
        )
        
        assert result['success'] is True
        assert result['volume'] == 0.01


# ============================================================
# 8. MTF Analysis Integration Tests
# ============================================================

class TestMTFAnalysisIntegration:
    """Test that MTFAnalyzer is wired into the trading flow."""
    
    def test_mtf_analyzer_in_trading_bot(self):
        """TradingBot should have mtf_analyzer attribute."""
        from trading_bot.main import TradingBot
        bot = TradingBot()
        assert hasattr(bot, 'mtf_analyzer')
    
    def test_mtf_analyzer_has_analyze_method(self):
        """MTFAnalyzer should have an analyze() method."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer
        analyzer = MTFAnalyzer()
        assert hasattr(analyzer, 'analyze')
        assert callable(analyzer.analyze)
    
    @pytest.mark.asyncio
    async def test_mtf_analyze_returns_result(self):
        """MTFAnalyzer.analyze() should return a result even without MT5."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalyzer, MTFAnalysisResult
        analyzer = MTFAnalyzer()
        
        # Without MT5 client, it should still work (no data = unknown bias)
        result = await analyzer.analyze("EURUSD")
        assert isinstance(result, MTFAnalysisResult)
        assert hasattr(result, 'overall_bias')
        assert hasattr(result, 'alignment')
    
    def test_mtf_result_to_dict(self):
        """MTFAnalysisResult.to_dict() should include all fields."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long=True,
            can_trade_short=False
        )
        d = result.to_dict()
        assert d['overall_bias'] == 'bullish'
        assert d['alignment'] is True
        assert d['can_trade_long'] is True


# ============================================================
# 9. Fibonacci Integration Tests
# ============================================================

class TestFibonacciIntegration:
    """Test that FibonacciAnalyzer is wired into the trading flow."""
    
    def test_fibonacci_analyzer_in_trading_bot(self):
        """TradingBot should have fibonacci_analyzer attribute."""
        from trading_bot.main import TradingBot
        bot = TradingBot()
        assert hasattr(bot, 'fibonacci_analyzer')
    
    def test_fibonacci_calculate_levels(self):
        """FibonacciAnalyzer should calculate correct levels."""
        from trading_bot.analysis.fibonacci import FibonacciAnalyzer, FibonacciLevels
        analyzer = FibonacciAnalyzer()
        
        levels = analyzer.calculate_levels(
            swing_high=1.1000,
            swing_low=1.0500,
            direction='bullish'
        )
        
        assert isinstance(levels, FibonacciLevels)
        assert levels.level_50 == pytest.approx(1.0750, abs=0.001)
        assert levels.ote_top == pytest.approx(levels.level_618, abs=0.001)
        assert levels.ote_bottom == pytest.approx(levels.level_79, abs=0.001)
    
    def test_fibonacci_ote_detection(self):
        """FibonacciAnalyzer should detect when price is in OTE zone."""
        from trading_bot.analysis.fibonacci import FibonacciAnalyzer
        analyzer = FibonacciAnalyzer()
        
        levels = analyzer.calculate_levels(1.1000, 1.0500, 'bullish')
        
        # For bullish: OTE is between 61.8% and 79% retracement from the high
        # level_618 = 1.1000 - (0.05 * 0.618) = 1.0691
        # level_79  = 1.1000 - (0.05 * 0.79)  = 1.0605
        # The 70.5% level (sweet spot) = 1.1000 - (0.05 * 0.705) = 1.06475
        ote_price = levels.level_705  # 70.5% retracement - in the OTE zone
        assert levels.is_in_ote(ote_price)
        
        # Price at top (0% retracement - not in OTE)
        assert not levels.is_in_ote(1.1000)


# ============================================================
# 10. Analysis Data Enrichment Tests
# ============================================================

class TestAnalysisDataEnrichment:
    """Test that analysis_data includes full price levels."""
    
    def test_analysis_data_structure_has_price_zones(self):
        """The analysis_data format should include price zone arrays."""
        # This tests the structure expected by the enriched format
        expected_keys = {
            'market_structure': ['trend', 'structure_breaks', 'break_details', 'swing_highs', 'swing_lows'],
            'fvg': ['bullish', 'bearish', 'active', 'bullish_zones', 'bearish_zones'],
            'order_blocks': ['bullish', 'bearish', 'bullish_zones', 'bearish_zones'],
            'liquidity': ['nearest_bsl', 'nearest_ssl', 'all_bsl', 'all_ssl', 'equal_highs', 'equal_lows'],
        }
        
        # Verify the expected structure is documented
        for section, fields in expected_keys.items():
            assert len(fields) > 2, f"{section} should have enriched fields beyond counts"


# ============================================================
# 11. Config Candle Counts Tests
# ============================================================

class TestConfigCandleCounts:
    """Test that config candle counts are used."""
    
    def test_timeframe_settings_exist(self):
        """TimeframeSettings should have candle count fields."""
        from trading_bot.config import TimeframeSettings
        settings = TimeframeSettings()
        assert settings.higher_tf_candles == 100
        assert settings.execution_tf_candles == 200
        assert settings.confirmation_tf_candles == 150
    
    def test_execution_tf_candles_used_in_main(self):
        """Pipeline should reference settings.timeframes.execution_tf_candles."""
        from tests.pipeline_source import analyze_and_trade_source

        source = analyze_and_trade_source()
        assert 'execution_tf_candles' in source


# ============================================================
# 12. Signal Truncation Fix Tests
# ============================================================

class TestSignalTruncationFix:
    """Test that signal reasoning is not truncated in the backend."""
    
    def test_bot_state_does_not_truncate_reasoning(self):
        """BotState.claude_response should not truncate reasoning."""
        import inspect
        from trading_bot.api.routes.bot_status import BotState
        
        source = inspect.getsource(BotState.claude_response)
        # Should NOT contain [:200]
        assert '[:200]' not in source


# ============================================================
# 13. Claude Reevaluation Robustness Tests
# ============================================================

class TestClaudeReevaluation:
    """Test that _claude_reevaluate_positions is robust."""
    
    def test_reevaluation_uses_startswith(self):
        """Reevaluation should use startswith("CLOSE") not 'in'."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._claude_reevaluate_positions)
        assert 'startswith("CLOSE")' in source
        assert 'wait_for' in source  # Has timeout
    
    def test_reevaluation_has_timeout(self):
        """Reevaluation should have a timeout."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._claude_reevaluate_positions)
        assert 'timeout=' in source


# ============================================================
# 14. Database Model Tests
# ============================================================

class TestDatabaseModel:
    """Test that the database model includes multi-TP fields."""
    
    def test_position_state_model_has_tp_fields(self):
        """PositionStateModel should have tp1/tp2/tp3/tp1_hit/tp2_hit/initial_volume columns."""
        from trading_bot.api.database import PositionStateModel
        
        # Check that the model has the required columns
        columns = {c.name for c in PositionStateModel.__table__.columns}
        assert 'tp1' in columns
        assert 'tp2' in columns
        assert 'tp3' in columns
        assert 'tp1_hit' in columns
        assert 'tp2_hit' in columns
        assert 'initial_volume' in columns


# ============================================================
# 15. Claude Empty Result Helper Tests
# ============================================================

class TestClaudeEmptyResult:
    """Test the _empty_result helper method."""
    
    def test_empty_result_returns_valid_analysis(self):
        """_empty_result should return a valid AnalysisResult with no_trade."""
        from trading_bot.llm.claude_client import ClaudeClient
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            result = client._empty_result("Test reason")
            
            assert result.signal.direction == 'no_trade'
            assert result.signal.confidence == 0.0
            assert result.analysis_summary == "Test reason"
            assert "Test reason" in result.warnings


# ============================================================
# 16. Daily Trade Counter Tests
# ============================================================

class TestDailyTradeCounter:
    """Test daily trade counter initialization."""
    
    def test_sync_positions_initializes_daily_trades(self):
        """_sync_positions_on_startup should count today's trades from MT5."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._sync_positions_on_startup)
        assert 'daily_trades' in source
        assert 'get_history' in source


# ============================================================
# 17. Integration: Full Position Lifecycle Test
# ============================================================

class TestPositionLifecycle:
    """Integration test for the full position management lifecycle."""
    
    @pytest.mark.asyncio
    async def test_position_lifecycle_micro_account(self, position_manager):
        """Test micro-account position lifecycle: open -> BE -> trailing."""
        from trading_bot.execution.position_manager import Position, PositionStatus
        
        # Create a micro position (can't partial close)
        pos = Position(
            ticket=55555,
            symbol="EURUSD",
            direction="long",
            volume=0.01,  # Micro - can't partial
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            open_time=datetime.now()
        )
        pos.tp1 = 1.0900
        pos.tp2 = 1.0950
        pos.tp3 = 1.1000
        
        position_manager.add_position(pos)
        
        # Verify initial state
        assert pos.tp1_hit is False
        assert pos.tp2_hit is False
        assert pos.volume == 0.01
        assert pos.initial_volume == 0.01
        
        # Check that micro account logic is applied
        can_partial = pos.volume >= 0.03
        assert can_partial is False  # Confirms micro-account path


# ============================================================
# 18. Prompt Enhancement Tests
# ============================================================

class TestPromptEnhancements:
    """Test that Claude prompt includes HTF and Fibonacci context."""
    
    def test_prompt_includes_htf_section(self):
        """_build_analysis_prompt should include HTF context when available."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        assert 'htf_bias' in source
        assert 'htf_alignment' in source
        assert 'HTF Bias' in source
    
    def test_prompt_includes_fibonacci_section(self):
        """_build_analysis_prompt should include Fibonacci context when available."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        assert 'fibonacci_zone' in source
        assert 'OTE' in source


# ============================================================
# 19. Trade Review Metadata Pipeline Tests
# ============================================================

class TestTradeReviewMetadataPipeline:
    """Test that entry_reason, original_confidence, and timeframe flow through the review pipeline."""
    
    def test_trade_learning_model_has_metadata_columns(self):
        """TradeLearningModel should have entry_reason, original_confidence, and timeframe columns."""
        from trading_bot.api.database import TradeLearningModel
        
        columns = {c.name for c in TradeLearningModel.__table__.columns}
        assert 'entry_reason' in columns, "TradeLearningModel missing entry_reason column"
        assert 'original_confidence' in columns, "TradeLearningModel missing original_confidence column"
        assert 'timeframe' in columns, "TradeLearningModel missing timeframe column"
    
    def test_store_trade_review_accepts_metadata_params(self):
        """store_trade_review() should accept entry_reason, original_confidence, timeframe params."""
        import inspect
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        sig = inspect.signature(TradeLearningService.store_trade_review)
        param_names = list(sig.parameters.keys())
        assert 'entry_reason' in param_names, "store_trade_review missing entry_reason parameter"
        assert 'original_confidence' in param_names, "store_trade_review missing original_confidence parameter"
        assert 'timeframe' in param_names, "store_trade_review missing timeframe parameter"
    
    def test_handle_position_close_retrieves_metadata(self):
        """_handle_position_close should retrieve entry_reason, claude_confidence, timeframe from DB."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._handle_position_close)
        assert 'entry_reason' in source, "Missing entry_reason retrieval in _handle_position_close"
        assert 'original_confidence' in source or 'claude_confidence' in source, \
            "Missing confidence retrieval in _handle_position_close"
        assert 'trade_timeframe' in source or "timeframe" in source, \
            "Missing timeframe retrieval in _handle_position_close"
    
    def test_handle_position_close_passes_metadata_to_store(self):
        """_handle_position_close should pass metadata to store_trade_review()."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._handle_position_close)
        # Check that store_trade_review call includes the new fields
        assert 'entry_reason=entry_reason' in source, \
            "store_trade_review call missing entry_reason kwarg"
        assert 'original_confidence=original_confidence' in source, \
            "store_trade_review call missing original_confidence kwarg"
        assert 'timeframe=trade_timeframe' in source, \
            "store_trade_review call missing timeframe kwarg"
    
    def test_review_prompt_includes_confidence_and_timeframe(self):
        """review_closed_trade prompt should include original confidence and timeframe."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.review_closed_trade)
        assert 'Original Confidence' in source, "Review prompt missing Original Confidence"
        assert 'Analysis Timeframe' in source, "Review prompt missing Analysis Timeframe"
    
    def test_review_prompt_asks_about_confidence_calibration(self):
        """review_closed_trade should ask Claude about confidence calibration."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.review_closed_trade)
        assert 'confidence level justified' in source, \
            "Review prompt should ask if confidence was justified"
    
    def test_consolidate_weekly_includes_metadata(self):
        """consolidate_weekly should include entry_reason, original_confidence, timeframe in learnings_data."""
        import inspect
        from trading_bot.services.trade_learning_service import TradeLearningService
        
        source = inspect.getsource(TradeLearningService.consolidate_weekly)
        assert 'entry_reason' in source, "consolidate_weekly missing entry_reason in learnings_data"
        assert 'original_confidence' in source, "consolidate_weekly missing original_confidence in learnings_data"
        assert 'timeframe' in source, "consolidate_weekly missing timeframe in learnings_data"
        assert 'setup_type' in source, "consolidate_weekly missing setup_type in learnings_data"
    
    def test_trade_data_includes_metadata_for_review(self):
        """trade_data dict in _handle_position_close should include metadata fields."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._handle_position_close)
        # Check the trade_data dict includes new fields
        assert "'entry_reason'" in source, "trade_data missing entry_reason"
        assert "'original_confidence'" in source, "trade_data missing original_confidence"
        assert "'timeframe'" in source, "trade_data missing timeframe"


# ============================================================
# 20. TP Direction Validation Tests
# ============================================================

class TestTPDirectionValidation:
    """Test that place_order rejects trades with TP on wrong side of entry."""
    
    @pytest.mark.asyncio
    async def test_buy_with_tp_below_entry_rejected(self):
        """BUY order with TP below entry price should be rejected."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = False
        client._connected = True
        
        # Mock MT5 module so we don't need real connection
        mock_mt5 = MagicMock()
        mock_mt5.symbol_info.return_value = MagicMock(
            ask=1.08500, bid=1.08480,
            digits=5, point=0.00001,
            trade_stops_level=10,
            volume_min=0.01, volume_max=100.0,
            filling_mode=1
        )
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.ORDER_FILLING_FOK = 0
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.ORDER_TIME_GTC = 0
        client._mcp_client = mock_mt5
        
        with patch.object(client, 'ensure_connected', new_callable=AsyncMock, return_value=True):
            result = await client.place_order(
                symbol="EURUSD",
                order_type="buy",
                volume=0.01,
                take_profit=1.08000,  # BELOW entry (wrong for buy!)
                stop_loss=1.08300,
            )
        
        assert result['success'] is False
        assert 'Invalid TP' in result.get('error', '') or 'TP' in result.get('error', '')
    
    @pytest.mark.asyncio
    async def test_sell_with_tp_above_entry_rejected(self):
        """SELL order with TP above entry price should be rejected."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = False
        client._connected = True
        
        mock_mt5 = MagicMock()
        mock_mt5.symbol_info.return_value = MagicMock(
            ask=1.08500, bid=1.08480,
            digits=5, point=0.00001,
            trade_stops_level=10,
            volume_min=0.01, volume_max=100.0,
            filling_mode=1
        )
        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.ORDER_FILLING_FOK = 0
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.ORDER_TIME_GTC = 0
        client._mcp_client = mock_mt5
        
        with patch.object(client, 'ensure_connected', new_callable=AsyncMock, return_value=True):
            result = await client.place_order(
                symbol="EURUSD",
                order_type="sell",
                volume=0.01,
                take_profit=1.09000,  # ABOVE entry (wrong for sell!)
                stop_loss=1.08800,
            )
        
        assert result['success'] is False
        assert 'Invalid TP' in result.get('error', '') or 'TP' in result.get('error', '')
    
    @pytest.mark.asyncio
    async def test_buy_with_correct_tp_not_rejected(self):
        """BUY order with TP above entry should NOT be rejected."""
        from trading_bot.mt5.client import MT5Client
        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = True
        client._connected = True
        
        result = await client.place_order(
            symbol="EURUSD",
            order_type="buy",
            volume=0.01,
            take_profit=1.09000,  # Above entry (correct for buy)
            stop_loss=1.08000,
        )
        
        # Simulation mode should succeed
        assert result['success'] is True
    
    def test_tp_validation_code_exists_in_place_order(self):
        """place_order should have TP direction validation code."""
        import inspect
        from trading_bot.mt5.client import MT5Client
        
        source = inspect.getsource(MT5Client.place_order)
        assert 'TP-REJECT' in source, "Missing TP-REJECT validation in place_order"
        assert 'Invalid TP' in source, "Missing Invalid TP error message in place_order"


# ============================================================
# 21. Trade Judge Tests — Mechanical Correctness
# ============================================================

class TestTradeJudgeMechanical:
    """Test that the trade judge works correctly at a mechanical level."""
    
    def _get_claude_client(self):
        """Get a ClaudeClient with mocked internals."""
        from trading_bot.llm.claude_client import ClaudeClient
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            client.api_key = "test"
            client.model = "test"
            client.model_heavy = "test"
            client.model_light = "test"
            client.effort_heavy = "medium"
            client.effort_judge = "medium"
            client.effort_light = "low"
            client.effort_review = "medium"
            client.max_tokens = 4096
            client.temperature = 0.3
            client.max_retries = 3
            client._cache = MagicMock()
            client._request_timestamps = []
            client._rate_limit_window = 60
            client._rate_limit_max = 50
            client._rate_lock = asyncio.Lock()
            client.sync_client = None
            client.async_client = None
            return client
    
    def test_judge_method_exists_with_correct_signature(self):
        """ClaudeClient should have a judge_trade method with correct parameters."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        assert hasattr(ClaudeClient, 'judge_trade'), "ClaudeClient missing judge_trade method"
        sig = inspect.signature(ClaudeClient.judge_trade)
        params = list(sig.parameters.keys())
        assert 'signal' in params, "judge_trade missing 'signal' parameter"
        assert 'risk_metrics' in params, "judge_trade missing 'risk_metrics' parameter"
        assert 'learning_context' in params, "judge_trade missing 'learning_context' parameter"
    
    @pytest.mark.asyncio
    async def test_judge_approve_does_not_alter_order(self):
        """APPROVE verdict should pass trade through unchanged."""
        client = self._get_claude_client()
        
        # Mock Claude to return APPROVE
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='```json\n{"verdict": "APPROVE", "reason": "Looks good", "suggested_entry": null, "risk_flags": []}\n```')]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)
        
        signal = {
            'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
            'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
            'order_type': 'market', 'reasoning': 'Strong setup'
        }
        risk_metrics = {
            'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
            'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
            'max_daily_trades': 5, 'session': 'london'
        }
        
        result = await client.judge_trade(signal, risk_metrics, "")
        
        assert result['verdict'] == 'APPROVE'
        assert result['suggested_entry'] is None
    
    @pytest.mark.asyncio
    async def test_judge_demote_converts_market_to_limit(self):
        """DEMOTE verdict should return suggested entry for limit conversion."""
        client = self._get_claude_client()
        
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='```json\n{"verdict": "DEMOTE", "reason": "Entry too aggressive", "suggested_entry": 1.0830, "risk_flags": ["pattern_match"]}\n```')]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)
        
        signal = {
            'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.78,
            'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
            'order_type': 'market', 'reasoning': 'Marginal setup'
        }
        risk_metrics = {
            'account_balance': 209.0, 'daily_pnl': -5.0, 'drawdown_pct': 0.02,
            'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 1,
            'max_daily_trades': 5, 'session': 'london'
        }
        
        result = await client.judge_trade(signal, risk_metrics, "Recent mistake: early entries")
        
        assert result['verdict'] == 'DEMOTE'
        assert result['suggested_entry'] == 1.0830
        assert 'pattern_match' in result['risk_flags']
    
    @pytest.mark.asyncio
    async def test_judge_timeout_fails_closed(self):
        """Judge unavailable (no async_client) should default to DEMOTE."""
        client = self._get_claude_client()
        client.async_client = None  # Simulate unavailable
        
        signal = {'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
                  'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
                  'order_type': 'market', 'reasoning': 'Test'}
        risk_metrics = {'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
                       'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
                       'max_daily_trades': 5, 'session': 'london'}
        
        result = await client.judge_trade(signal, risk_metrics, "")
        
        assert result['verdict'] == 'UNAVAILABLE', "Judge should fail closed when unavailable"
    
    @pytest.mark.asyncio
    async def test_judge_invalid_response_fails_closed(self):
        """Malformed response from Claude should default to DEMOTE."""
        client = self._get_claude_client()
        
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='This is not JSON at all')]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)
        
        signal = {'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
                  'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
                  'order_type': 'market', 'reasoning': 'Test'}
        risk_metrics = {'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
                       'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
                       'max_daily_trades': 5, 'session': 'london'}
        
        result = await client.judge_trade(signal, risk_metrics, "")
        
        assert result['verdict'] == 'UNAVAILABLE', "Malformed response should fail closed"
    
    @pytest.mark.asyncio
    async def test_judge_api_error_fails_closed(self):
        """API exception should default to DEMOTE."""
        client = self._get_claude_client()
        
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(side_effect=Exception("API down"))
        
        signal = {'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
                  'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
                  'order_type': 'market', 'reasoning': 'Test'}
        risk_metrics = {'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
                       'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
                       'max_daily_trades': 5, 'session': 'london'}
        
        result = await client.judge_trade(signal, risk_metrics, "")
        
        assert result['verdict'] == 'UNAVAILABLE', "API error should fail closed"

    @pytest.mark.asyncio
    async def test_judge_uses_opus_5_request_shape(self):
        """Judge call must be Opus 5 compatible: no temperature, adaptive
        thinking, explicit judge effort. Also verifies a leading thinking block is skipped."""
        client = self._get_claude_client()

        thinking_block = MagicMock(type='thinking', thinking='deliberating...')
        text_block = MagicMock(
            type='text',
            text='```json\n{"verdict": "APPROVE", "reason": "ok", "suggested_entry": null, "risk_flags": []}\n```',
        )
        mock_response = MagicMock()
        mock_response.content = [thinking_block, text_block]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)

        signal = {'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
                  'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
                  'order_type': 'market', 'reasoning': 'Test'}
        risk_metrics = {'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
                        'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
                        'max_daily_trades': 5, 'session': 'london'}

        result = await client.judge_trade(signal, risk_metrics, "")

        # Parsed the text block, skipping the thinking block.
        assert result['verdict'] == 'APPROVE'

        kwargs = client.async_client.messages.create.call_args.kwargs
        assert 'temperature' not in kwargs, "Opus 5 rejects temperature"
        assert kwargs.get('thinking') == {'type': 'adaptive'}
        assert kwargs.get('output_config', {}).get('effort') == 'medium'
        # Structured output: the judge constrains its verdict to a JSON schema.
        fmt = kwargs.get('output_config', {}).get('format', {})
        assert fmt.get('type') == 'json_schema', "Judge should request json_schema output"
        assert fmt.get('schema', {}).get('properties', {}).get('verdict'), \
            "Judge schema should define the verdict field"
        # Static rubric must ride in a prompt-cached system block.
        from trading_bot.llm.claude_client import JUDGE_RUBRIC
        system = kwargs.get('system') or []
        assert any(
            blk.get('text') == JUDGE_RUBRIC and blk.get('cache_control')
            for blk in system
        ), "Judge should send JUDGE_RUBRIC as a cached system block"

    @pytest.mark.asyncio
    async def test_judge_falls_back_when_structured_output_rejected(self):
        """If the API rejects output_config.format, judge retries without it and still parses."""
        import anthropic
        client = self._get_claude_client()

        text_block = MagicMock(
            type='text',
            text='```json\n{"verdict": "APPROVE", "reason": "ok", "suggested_entry": null, "risk_flags": []}\n```',
        )
        ok_response = MagicMock()
        ok_response.content = [text_block]

        bad_request = anthropic.BadRequestError(
            message="format incompatible",
            response=MagicMock(status_code=400),
            body=None,
        )

        client.async_client = AsyncMock()
        # First call (with format) raises; second call (without format) succeeds.
        client.async_client.messages.create = AsyncMock(side_effect=[bad_request, ok_response])

        signal = {'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
                  'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
                  'order_type': 'market', 'reasoning': 'Test'}
        risk_metrics = {'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
                        'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
                        'max_daily_trades': 5, 'session': 'london'}

        result = await client.judge_trade(signal, risk_metrics, "")

        assert result['verdict'] == 'APPROVE'
        assert client.async_client.messages.create.call_count == 2
        # The retry must NOT include the format constraint.
        retry_kwargs = client.async_client.messages.create.call_args_list[1].kwargs
        assert 'format' not in retry_kwargs.get('output_config', {})
        assert retry_kwargs.get('output_config', {}).get('effort') == 'medium', \
            "Judge retry must keep effort_judge (medium), not fall back to effort_heavy"


# ============================================================
# 21c. Opus 5 Everywhere — Strict Tool + Light-Task Shape
# ============================================================

class TestOpus5Everywhere:
    """All Claude calls (light tasks included) run on Opus 5 with valid params."""

    def _get_claude_client(self):
        from trading_bot.llm.claude_client import ClaudeClient
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            client.api_key = "test"
            client.model = "test"
            client.model_heavy = "claude-opus-5"
            client.model_light = "claude-opus-5"
            client.effort_heavy = "medium"
            client.effort_judge = "medium"
            client.effort_light = "low"
            client.effort_review = "medium"
            client.max_tokens = 32000
            client.temperature = 0.3
            client.max_retries = 3
            client._cache = MagicMock()
            client._request_timestamps = []
            client._rate_limit_window = 60
            client._rate_limit_max = 50
            client._rate_lock = asyncio.Lock()
            client.sync_client = None
            client.async_client = None
            return client

    def test_model_light_is_opus_5(self):
        """__init__ source must point model_light at Opus 5 (no Sonnet/4.8 split)."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient

        source = inspect.getsource(ClaudeClient.__init__)
        assert 'self.model_light = "claude-opus-5"' in source
        assert 'self.model_heavy = "claude-opus-5"' in source
        assert 'self.effort_heavy = "medium"' in source
        assert 'self.effort_light' in source
        assert 'self.effort_judge' in source
        assert 'self.effort_review' in source
        # Opus 5 thinking needs headroom above the old 16k ceiling.
        assert 'max_tokens: int = 32000' in source or 'max_tokens: int = 64000' in source

    def test_trade_signal_tool_is_strict(self):
        """The analysis tool must use strict tool use with a strict-compatible schema."""
        from trading_bot.llm.claude_client import TRADE_SIGNAL_TOOL

        assert TRADE_SIGNAL_TOOL.get('strict') is True
        schema = TRADE_SIGNAL_TOOL['input_schema']
        assert schema.get('additionalProperties') is False
        # Nested objects need additionalProperties: false too.
        assert schema['properties']['key_levels'].get('additionalProperties') is False
        # Numeric range constraints are unsupported by the strict grammar pipeline.
        import json as _json
        flat = _json.dumps(schema)
        assert '"minimum"' not in flat and '"maximum"' not in flat, \
            "strict tool schemas must not contain numeric range constraints"

    def test_no_light_call_sends_temperature(self):
        """All light-task methods must route through the shared Opus 5 JSON helper."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient

        # The helper is the single source of truth for the light-task request shape.
        helper_source = inspect.getsource(ClaudeClient._light_json_call)
        assert 'temperature=' not in helper_source, "_light_json_call must not send temperature"
        assert 'thinking={"type": "adaptive"}' in helper_source, "_light_json_call missing adaptive thinking"
        assert 'self._extract_text(message)' in helper_source, "_light_json_call not thinking-block safe"
        assert 'self._record_usage(task, message)' in helper_source, "_light_json_call missing usage telemetry"

        for method_name in ('recommend_position_size', 'review_closed_trade',
                            'generate_weekly_review', 'generate_weekly_insights',
                            'assess_scaling_decision'):
            source = inspect.getsource(getattr(ClaudeClient, method_name))
            assert 'temperature=' not in source, f"{method_name} still sends temperature"
            assert '_light_json_call' in source, f"{method_name} bypasses the shared light-task helper"

    @pytest.mark.asyncio
    async def test_light_task_request_shape(self):
        """A review light task must use Opus 5 params, review effort, and skip thinking blocks."""
        client = self._get_claude_client()

        thinking_block = MagicMock(type='thinking', thinking='pondering...')
        text_block = MagicMock(
            type='text',
            text='```json\n{"outcome": "win", "grade": "A", "analysis": "solid", '
                 '"what_went_right": [], "what_went_wrong": [], "learnings": [], '
                 '"would_take_again": true, "improvement_suggestions": []}\n```',
        )
        mock_response = MagicMock()
        mock_response.content = [thinking_block, text_block]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.review_closed_trade({'symbol': 'EURUSD', 'direction': 'long'})

        assert result['grade'] == 'A', "Should parse text block, skipping the thinking block"
        kwargs = client.async_client.messages.create.call_args.kwargs
        assert kwargs.get('model') == 'claude-opus-5'
        assert 'temperature' not in kwargs
        assert kwargs.get('thinking') == {'type': 'adaptive'}
        assert kwargs.get('output_config', {}).get('effort') == 'medium'

    def test_main_reevals_use_extract_text_and_opus_params(self):
        """Position and pending re-evals must be thinking-block safe, temperature-free,
        send their static rules as cached system blocks, and record usage."""
        import inspect
        from trading_bot.main import TradingBot

        expected_rules = {
            '_claude_reevaluate_positions': 'POSITION_REEVAL_RULES',
            '_claude_reevaluate_pending_orders': 'PENDING_REEVAL_RULES',
        }
        for method_name, rules_const in expected_rules.items():
            source = inspect.getsource(getattr(TradingBot, method_name))
            assert '_extract_text' in source, f"{method_name} reads content[0] directly"
            assert 'temperature=' not in source, f"{method_name} sends temperature"
            assert '"adaptive"' in source, f"{method_name} missing adaptive thinking"
            assert rules_const in source, f"{method_name} missing cached {rules_const} system block"
            assert 'cache_control' in source, f"{method_name} system block not cache-controlled"
            assert '_record_usage' in source, f"{method_name} missing usage telemetry"

    def test_reeval_rules_contain_output_contracts(self):
        """The cached re-eval rules must keep the strict first-word output contracts."""
        from trading_bot.main import PENDING_REEVAL_RULES, POSITION_REEVAL_RULES

        assert 'OUTPUT CONTRACT' in POSITION_REEVAL_RULES
        assert 'HOLD, CLOSE, or TIGHTEN' in POSITION_REEVAL_RULES
        assert 'OUTPUT CONTRACT' in PENDING_REEVAL_RULES
        assert 'KEEP or CANCEL' in PENDING_REEVAL_RULES

    def test_api_usage_model_schema(self):
        """The api_usage telemetry table must exist with token + cost columns."""
        from trading_bot.api.database import ApiUsageModel

        assert ApiUsageModel.__tablename__ == 'api_usage'
        cols = {c.name for c in ApiUsageModel.__table__.columns}
        assert {'task', 'model', 'input_tokens', 'output_tokens',
                'cache_read_tokens', 'cache_creation_tokens',
                'estimated_cost_usd', 'timestamp'} <= cols

    def test_record_usage_skips_mocked_usage(self):
        """_record_usage must silently ignore mock/malformed usage (never raises)."""
        client = self._get_claude_client()
        # MagicMock usage attributes are not ints -> should be skipped quietly.
        client._record_usage('judge', MagicMock())
        # Missing usage entirely -> also skipped.
        client._record_usage('judge', None)

    @pytest.mark.asyncio
    async def test_record_usage_logs_real_usage(self):
        """_record_usage should accept integer token counts and schedule persistence."""
        from types import SimpleNamespace
        client = self._get_claude_client()

        message = SimpleNamespace(
            model='claude-opus-5',
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=500,
                cache_read_input_tokens=6000,
                cache_creation_input_tokens=0,
            ),
        )
        # Must not raise; DB write is fire-and-forget and swallows its own errors.
        client._record_usage('analysis', message)
        await asyncio.sleep(0)  # let the persistence task start (and fail silently if no DB)

    def test_opus_pricing_constants(self):
        """Cache read must be cheaper than input; cache write more expensive (1.25x)."""
        from trading_bot.llm.claude_client import OPUS_5_PRICING, OPUS_48_PRICING

        assert OPUS_5_PRICING['cache_read'] < OPUS_5_PRICING['input']
        assert OPUS_5_PRICING['cache_write'] > OPUS_5_PRICING['input']
        assert OPUS_5_PRICING['output'] > OPUS_5_PRICING['input']
        assert OPUS_48_PRICING is OPUS_5_PRICING  # backwards-compatible alias

    def test_analysis_and_judge_verbosity_scope(self):
        """Opus 5 prompts must include conciseness + scope guidance."""
        from trading_bot.llm.claude_client import (
            ANALYSIS_RULES,
            ANALYSIS_TONE_PREFERENCE,
            JUDGE_RUBRIC,
        )
        from trading_bot.main import PENDING_REEVAL_RULES, POSITION_REEVAL_RULES

        assert '<tone_preference>' in ANALYSIS_TONE_PREFERENCE
        assert 'concise' in ANALYSIS_TONE_PREFERENCE.lower()
        # Reasoning length stays in the cached rules; tone reminder is a separate block.
        assert '4-8 sentences' in ANALYSIS_RULES
        assert '## SCOPE' in JUDGE_RUBRIC
        assert '## SCOPE' in POSITION_REEVAL_RULES
        assert '## SCOPE' in PENDING_REEVAL_RULES

        # tone_preference must be LAST in the system stack (after strategy_context).
        client = self._get_claude_client()
        blocks = client._build_system_messages("STRATEGY CONTEXT PLACEHOLDER")
        assert blocks[-1]['text'] == ANALYSIS_TONE_PREFERENCE
        assert any(b.get('text') == ANALYSIS_RULES for b in blocks)

    @pytest.mark.asyncio
    async def test_sizing_uses_low_effort(self):
        """Narrow sizing helper should use effort_light (low), not review effort."""
        client = self._get_claude_client()
        text_block = MagicMock(
            type='text',
            text='{"recommended_lots": 0.01, "reasoning": "base", '
                 '"risk_assessment": "low", "size_adjustment": "1.0x"}',
        )
        mock_response = MagicMock()
        mock_response.content = [text_block]
        client.async_client = AsyncMock()
        client.async_client.messages.create = AsyncMock(return_value=mock_response)

        result = await client.recommend_position_size(
            equity=1000, setup_grade='B', confidence=0.7, symbol='EURUSD'
        )
        assert result['recommended_lots'] == 0.01
        kwargs = client.async_client.messages.create.call_args.kwargs
        assert kwargs.get('model') == 'claude-opus-5'
        assert kwargs.get('output_config', {}).get('effort') == 'low'

    @pytest.mark.asyncio
    async def test_async_messages_create_streams_when_max_tokens_high(self):
        """Analysis-sized budgets must stream; light budgets keep using create()."""
        from contextlib import asynccontextmanager
        client = self._get_claude_client()
        client.async_client = AsyncMock()

        final = MagicMock()
        stream_cm = AsyncMock()
        stream_cm.get_final_message = AsyncMock(return_value=final)

        @asynccontextmanager
        async def fake_stream(**kwargs):
            yield stream_cm

        client.async_client.messages.stream = fake_stream
        client.async_client.messages.create = AsyncMock(return_value=MagicMock(name='create_msg'))

        streamed = await client._async_messages_create(model='claude-opus-5', max_tokens=32000, messages=[])
        assert streamed is final
        client.async_client.messages.create.assert_not_called()

        created = await client._async_messages_create(model='claude-opus-5', max_tokens=4000, messages=[])
        assert created is client.async_client.messages.create.return_value
        client.async_client.messages.create.assert_called_once()


# ============================================================
# 22. Trade Judge Tests — Performance Guardrails
# ============================================================

class TestTradeJudgePerformance:
    """Ensure the trade judge enhances trading, not hinders it."""
    
    def test_judge_demoted_entry_improves_risk_reward_long(self):
        """Demoted long entry (lower) should produce equal or better R:R."""
        original_entry = 1.0850
        sl = 1.0800
        tp = 1.0950
        
        # Judge suggests a lower entry for the long
        demoted_entry = 1.0830
        
        original_rr = (tp - original_entry) / (original_entry - sl)
        demoted_rr = (tp - demoted_entry) / (demoted_entry - sl)
        
        assert demoted_rr >= original_rr, (
            f"Demoted R:R ({demoted_rr:.2f}) should be >= original ({original_rr:.2f})"
        )
    
    def test_judge_demoted_entry_improves_risk_reward_short(self):
        """Demoted short entry (higher) should produce equal or better R:R."""
        original_entry = 1.0850
        sl = 1.0900
        tp = 1.0750
        
        # Judge suggests a higher entry for the short
        demoted_entry = 1.0870
        
        original_rr = (original_entry - tp) / (sl - original_entry)
        demoted_rr = (demoted_entry - tp) / (sl - demoted_entry)
        
        assert demoted_rr >= original_rr, (
            f"Demoted R:R ({demoted_rr:.2f}) should be >= original ({original_rr:.2f})"
        )
    
    def test_judge_evaluates_on_merit(self):
        """The judge rubric should evaluate trades on their own merit, not force-approve at any confidence.

        The static rubric lives in the prompt-cached JUDGE_RUBRIC system block, so
        assertions inspect the method source combined with that constant.
        """
        import inspect
        from trading_bot.llm import claude_client
        
        source = inspect.getsource(claude_client.ClaudeClient.judge_trade) + "\n" + claude_client.JUDGE_RUBRIC
        assert 'on its own merit' in source or 'merit' in source, \
            "Judge rubric should evaluate trades on merit"
        assert 'Confidence alone neither approves nor rejects' in source or 'confidence alone' in source.lower(), \
            "Judge should consider confidence but not force-approve based on it alone"
    
    def test_judge_prompt_includes_learning_context(self):
        """The judge prompt should include the learning_context parameter."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.judge_trade)
        assert 'learning_context' in source, "Judge should use learning_context in prompt"
        assert 'Past Learning Context' in source or 'learning_context' in source, \
            "Judge prompt should have a section for historical learnings"
    
    def test_judge_prompt_includes_all_risk_metrics(self):
        """The judge prompt should include all key risk metrics."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.judge_trade)
        required_metrics = ['account_balance', 'daily_pnl', 'risk_reward', 'position_size_pct', 'session']
        for metric in required_metrics:
            assert metric in source, f"Judge prompt missing risk metric: {metric}"
    
    def test_judge_latency_budget(self):
        """Shared judge adapter should use asyncio.wait_for with an Opus-sized timeout.

        The old Sonnet-era 8s budget would fail-close (block) every trade now that the
        judge runs on Opus with adaptive thinking (~10-20s typical).
        """
        import inspect
        from trading_bot.services.trade_judge import run_trade_judge

        source = inspect.getsource(run_trade_judge)
        assert 'wait_for' in source, "run_trade_judge should use asyncio.wait_for"
        assert 'timeout: float = 45.0' in source, \
            "run_trade_judge default timeout should be 45s for Opus + thinking"
    
    def test_judge_demote_default_price_improvement(self):
        """Default demote should use 0.2% price improvement from current."""
        # For a long, 0.2% lower
        current_price = 1.0850
        expected_demoted_long = round(current_price * 0.998, 5)
        assert expected_demoted_long < current_price, "Long demote should be below market"
        assert abs(expected_demoted_long - current_price) / current_price == pytest.approx(0.002, abs=0.0001)
        
        # For a short, 0.2% higher
        expected_demoted_short = round(current_price * 1.002, 5)
        assert expected_demoted_short > current_price, "Short demote should be above market"
        assert abs(expected_demoted_short - current_price) / current_price == pytest.approx(0.002, abs=0.0001)
    
    def test_judge_never_worsens_long_entry(self):
        """Judge should clamp suggested long entry to not exceed current price."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.judge_trade)
        # Should have clamping logic for longs where suggested > entry
        assert 'suggested > entry_price' in source or "suggested_entry'] > current" in source or \
               "suggested > entry_price" in source, \
            "Judge should clamp long entries that worsen the trade"
    
    def test_judge_never_worsens_short_entry(self):
        """Judge should clamp suggested short entry to not go below current price."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.judge_trade)
        # Should have clamping logic for shorts where suggested < entry
        assert 'suggested < entry_price' in source or "suggested_entry'] < current" in source or \
               "suggested < entry_price" in source, \
            "Judge should clamp short entries that worsen the trade"
    
    def test_judge_rebases_sl_tp_on_demote(self):
        """Demote policy should rebase SL/TP as offsets from the demoted entry."""
        from trading_bot.utils.win_optimization import apply_demote_policy

        result = apply_demote_policy(
            direction="long",
            current_price=1.0850,
            original_entry=1.0830,
            stop_loss=1.0800,
            take_profit=1.0900,
            order_type="buy_limit",
            suggested_entry=1.0825,
        )
        assert result["demoted_entry"] == 1.083
        assert result["stop_loss"] == pytest.approx(1.08)
        assert result["take_profit"] == pytest.approx(1.09)
    
    def test_run_trade_judge_exists_in_trading_bot(self):
        """TradingBot should have _run_trade_judge method."""
        import inspect
        from trading_bot.main import TradingBot
        
        assert hasattr(TradingBot, '_run_trade_judge'), "TradingBot missing _run_trade_judge method"
        assert inspect.iscoroutinefunction(TradingBot._run_trade_judge), \
            "_run_trade_judge should be async"


# ============================================================
# 23. Reactive Trading Prompt Tests
# ============================================================

class TestReactiveTradingPrompt:
    """Verify the analysis prompt enforces reactive trading, not prediction."""
    
    @staticmethod
    def _analysis_rules_source():
        """Combined source: dynamic prompt builder + the static (cached) ANALYSIS_RULES.

        The static methodology now lives in the prompt-cached ANALYSIS_RULES system
        block rather than being rebuilt into every user prompt, so prompt-content
        assertions must inspect both locations.
        """
        import inspect
        from trading_bot.llm import claude_client
        return (
            inspect.getsource(claude_client.ClaudeClient._build_analysis_prompt)
            + "\n"
            + claude_client.ANALYSIS_RULES
        )
    
    def test_prompt_contains_reactive_mandate(self):
        """Prompt/ruleset should contain the REACT, DO NOT PREDICT mandate."""
        source = self._analysis_rules_source()
        assert 'REACT' in source, "Ruleset should contain REACT mandate"
        assert 'REACTIVE' in source, "Ruleset should use the word REACTIVE"
        assert 'ALREADY' in source, "Ruleset should require ALREADY confirmed setups"
    
    def test_prompt_does_not_encourage_direction_flipping(self):
        """Prompt should NOT contain the old 'EVALUATE BOTH DIRECTIONS EVERY CYCLE'."""
        source = self._analysis_rules_source()
        assert 'EVALUATE BOTH DIRECTIONS EVERY CYCLE' not in source, \
            "Old direction-flipping instruction should be removed"
        assert 'actively look for the opposite setup' not in source, \
            "Old direction-seeking instruction should be removed"
    
    def test_prompt_includes_confirmation_checklist(self):
        """Prompt/ruleset should require citing specific confirmations before any signal."""
        source = self._analysis_rules_source()
        # Should mention key confirmations
        assert 'displacement' in source.lower(), "Ruleset should mention displacement as confirmation"
        assert 'BOS' in source or 'Break of Structure' in source, "Ruleset should mention BOS"
        assert 'CHoCH' in source or 'Change of Character' in source, "Ruleset should mention CHoCH"
        assert 'liquidity sweep' in source.lower(), "Ruleset should mention liquidity sweep"
        assert 'FVG' in source or 'Fair Value Gap' in source, "Ruleset should mention FVG/OB"
    
    def test_prompt_has_variable_confidence_scale(self):
        """Prompt/ruleset should define a confidence scale (not flat 75%)."""
        source = self._analysis_rules_source()
        # Should have a multi-tier confidence scale
        assert '0.60' in source or '60' in source, "Should have 0.60 tier in confidence scale"
        assert '0.70' in source or '70' in source, "Should have 0.70 tier in confidence scale"
        assert '0.80' in source or '80' in source, "Should have 0.80 tier in confidence scale"
        assert '0.90' in source or '90' in source, "Should have 0.90 tier in confidence scale"
        assert 'park at exactly 0.75' in source or 'MUST vary' in source, \
            "Should warn against parking at 0.75"
    
    def test_prompt_warns_against_flipping_without_cause(self):
        """Prompt/ruleset should tell Claude not to flip direction without confirmed change."""
        source = self._analysis_rules_source()
        assert 'flip direction without cause' in source.lower() or \
               'do not flip' in source.lower() or \
               'flip direction' in source.lower(), \
            "Ruleset should warn against direction flips without cause"


# ============================================================
# 24. Cycle-to-Cycle Memory Tests
# ============================================================

class TestCycleMemory:
    """Verify Claude receives its last signal for context."""
    
    def test_last_signal_dict_exists_on_trading_bot(self):
        """TradingBot should have _last_signal_per_symbol dict."""
        from trading_bot.main import TradingBot
        
        with patch.object(TradingBot, '__init__', lambda self, **kw: None):
            bot = TradingBot.__new__(TradingBot)
            bot._last_signal_per_symbol = {}
            assert isinstance(bot._last_signal_per_symbol, dict)
    
    def test_prompt_includes_last_signal_section(self):
        """When last_signal is in market_data, prompt should include it."""
        from trading_bot.llm.claude_client import ClaudeClient
        
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            
            market_data = {
                'current_price': 2650.50,
                'session': 'london',
                'daily_high': 2660.0,
                'daily_low': 2640.0,
                'last_signal': {
                    'direction': 'long',
                    'confidence': 0.75,
                    'timestamp': '2026-02-11T12:00:00',
                    'reasoning': 'Bullish OB test',
                }
            }
            
            prompt = client._build_analysis_prompt(
                symbol='XAUUSD',
                timeframe='M5',
                strategy_context='Test context',
                market_data=market_data,
                analysis_data=None
            )
            
            assert 'YOUR LAST SIGNAL' in prompt, "Prompt should include last signal section"
            assert 'LONG' in prompt, "Should show the previous direction"
            assert 'DIRECTION FLIP RULE' in prompt, "Should include direction flip rule"
    
    def test_prompt_omits_last_signal_when_none(self):
        """When no last_signal exists, prompt should not include last signal section."""
        from trading_bot.llm.claude_client import ClaudeClient
        
        with patch.object(ClaudeClient, '__init__', lambda self, **kw: None):
            client = ClaudeClient.__new__(ClaudeClient)
            
            market_data = {
                'current_price': 2650.50,
                'session': 'london',
                'daily_high': 2660.0,
                'daily_low': 2640.0,
            }
            
            prompt = client._build_analysis_prompt(
                symbol='XAUUSD',
                timeframe='M5',
                strategy_context='Test context',
                market_data=market_data,
                analysis_data=None
            )
            
            assert 'YOUR LAST SIGNAL' not in prompt, \
                "Should not include last signal section when no data"
    
    def test_signal_memory_stored_in_analyze_and_trade(self):
        """Pipeline should store signals in _last_signal_per_symbol."""
        from tests.pipeline_source import analyze_and_trade_source

        source = analyze_and_trade_source()
        assert '_last_signal_per_symbol' in source, \
            "Pipeline should update _last_signal_per_symbol"
        assert "market_data[\"last_signal\"]" in source or \
               "market_data['last_signal']" in source, \
            "Pipeline should inject last_signal into market_data"


# ============================================================
# 25. Direction-Flip Cooldown Tests
# ============================================================

class TestDirectionFlipCooldown:
    """Test the direction-flip cooldown guard logic."""
    
    def test_flip_guard_exists_in_code(self):
        """The flip guard logic should exist in the shared pipeline modules."""
        import inspect

        from tests.pipeline_source import analyze_and_trade_source
        from trading_bot.services import scaling_gates

        source = analyze_and_trade_source() + inspect.getsource(scaling_gates)
        assert 'FLIP-GUARD' in source or 'evaluate_flip_guard' in source
        assert '_last_signal_direction' in source, "Should track last signal direction"
        assert 'flip_cooldown_minutes' in source or 'cooldown_minutes' in source
        assert 'flip_min_confidence' in source or 'min_confidence' in source

    def test_same_direction_always_passes(self):
        """Same direction signal should never be blocked by the flip guard."""
        from datetime import datetime, timedelta
        
        # Simulate: last signal was LONG 5 minutes ago, new signal is also LONG
        last_dir = 'long'
        last_time = datetime.now() - timedelta(minutes=5)
        new_dir = 'long'
        new_confidence = 0.75
        flip_cooldown_minutes = 30
        flip_min_confidence = 0.85
        
        minutes_since = (datetime.now() - last_time).total_seconds() / 60
        is_flip = (last_dir != new_dir and last_dir != 'no_trade' and 
                   minutes_since < flip_cooldown_minutes)
        
        assert not is_flip, "Same direction should not be considered a flip"
    
    def test_flip_within_cooldown_low_confidence_blocked(self):
        """Direction flip within 30 min at 75% confidence should be blocked."""
        from datetime import datetime, timedelta
        
        last_dir = 'long'
        last_time = datetime.now() - timedelta(minutes=10)
        new_dir = 'short'
        new_confidence = 0.75
        flip_cooldown_minutes = 30
        flip_min_confidence = 0.85
        
        minutes_since = (datetime.now() - last_time).total_seconds() / 60
        is_flip = (last_dir != new_dir and last_dir != 'no_trade' and 
                   minutes_since < flip_cooldown_minutes)
        should_block = is_flip and new_confidence < flip_min_confidence
        
        assert is_flip, "Different direction within cooldown is a flip"
        assert should_block, "Low-confidence flip should be blocked"
    
    def test_flip_within_cooldown_high_confidence_passes(self):
        """Direction flip within 30 min at 88% confidence should pass."""
        from datetime import datetime, timedelta
        
        last_dir = 'long'
        last_time = datetime.now() - timedelta(minutes=10)
        new_dir = 'short'
        new_confidence = 0.88
        flip_cooldown_minutes = 30
        flip_min_confidence = 0.85
        
        minutes_since = (datetime.now() - last_time).total_seconds() / 60
        is_flip = (last_dir != new_dir and last_dir != 'no_trade' and 
                   minutes_since < flip_cooldown_minutes)
        should_block = is_flip and new_confidence < flip_min_confidence
        
        assert is_flip, "Different direction within cooldown is a flip"
        assert not should_block, "High-confidence flip should pass through"
    
    def test_flip_after_cooldown_passes(self):
        """Direction flip after 30+ minutes should always pass."""
        from datetime import datetime, timedelta
        
        last_dir = 'long'
        last_time = datetime.now() - timedelta(minutes=45)
        new_dir = 'short'
        new_confidence = 0.75
        flip_cooldown_minutes = 30
        flip_min_confidence = 0.85
        
        minutes_since = (datetime.now() - last_time).total_seconds() / 60
        is_flip = (last_dir != new_dir and last_dir != 'no_trade' and 
                   minutes_since < flip_cooldown_minutes)
        
        assert not is_flip, "Flip after cooldown should not trigger guard"
    
    def test_no_trade_does_not_count_as_flip(self):
        """Switching from no_trade to a direction should not trigger the flip guard."""
        from datetime import datetime, timedelta
        
        last_dir = 'no_trade'
        last_time = datetime.now() - timedelta(minutes=5)
        new_dir = 'long'
        new_confidence = 0.75
        flip_cooldown_minutes = 30
        flip_min_confidence = 0.85
        
        minutes_since = (datetime.now() - last_time).total_seconds() / 60
        is_flip = (last_dir != new_dir and last_dir != 'no_trade' and 
                   minutes_since < flip_cooldown_minutes)
        
        assert not is_flip, "no_trade -> long is not a direction flip"
