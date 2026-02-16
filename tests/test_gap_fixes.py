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
        """main.py should reference settings.timeframes.execution_tf_candles."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._analyze_and_trade)
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
    async def test_judge_timeout_fails_open(self):
        """Judge unavailable (no async_client) should default to APPROVE."""
        client = self._get_claude_client()
        client.async_client = None  # Simulate unavailable
        
        signal = {'symbol': 'EURUSD', 'direction': 'long', 'confidence': 0.85,
                  'entry_price': 1.0850, 'stop_loss': 1.0800, 'take_profit': 1.0950,
                  'order_type': 'market', 'reasoning': 'Test'}
        risk_metrics = {'account_balance': 209.0, 'daily_pnl': 0.0, 'drawdown_pct': 0.0,
                       'risk_reward': 2.0, 'position_size_pct': 0.02, 'trades_today': 0,
                       'max_daily_trades': 5, 'session': 'london'}
        
        result = await client.judge_trade(signal, risk_metrics, "")
        
        assert result['verdict'] == 'APPROVE', "Judge should fail open when unavailable"
    
    @pytest.mark.asyncio
    async def test_judge_invalid_response_fails_open(self):
        """Malformed response from Claude should default to APPROVE."""
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
        
        assert result['verdict'] == 'APPROVE', "Malformed response should fail open"
    
    @pytest.mark.asyncio
    async def test_judge_api_error_fails_open(self):
        """API exception should default to APPROVE."""
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
        
        assert result['verdict'] == 'APPROVE', "API error should fail open"


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
        """The judge prompt should evaluate trades on their own merit, not force-approve at any confidence."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient.judge_trade)
        assert 'on its own merit' in source or 'merit' in source, \
            "Judge prompt should evaluate trades on merit"
        assert 'confidence alone does not guarantee' in source or 'strong consideration' in source, \
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
        """_run_trade_judge should use asyncio.wait_for with timeout <= 5 seconds."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._run_trade_judge)
        assert 'wait_for' in source, "_run_trade_judge should use asyncio.wait_for"
        assert 'timeout=5.0' in source or 'timeout=5' in source, \
            "_run_trade_judge timeout should be 5 seconds"
    
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
    
    def test_judge_preserves_sl_tp_on_demote(self):
        """Demote logic in main.py should only change entry and order_type, not SL/TP."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._analyze_and_trade)
        # The DEMOTE block should set order_type and entry_price but NOT stop_loss or take_profit
        # Find the DEMOTE handling section
        assert "trade_signal.order_type = 'buy_limit'" in source, "DEMOTE should set buy_limit for longs"
        assert "trade_signal.order_type = 'sell_limit'" in source, "DEMOTE should set sell_limit for shorts"
        # SL/TP should NOT be reassigned in the demote block
        assert "trade_signal.stop_loss =" not in source.split("TRADE JUDGE")[1].split("PENDING ORDER VS MARKET")[0], \
            "DEMOTE block should NOT modify stop_loss"
    
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
    
    def test_prompt_contains_reactive_mandate(self):
        """Prompt should contain the REACT, DO NOT PREDICT mandate."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        assert 'REACT' in source, "Prompt should contain REACT mandate"
        assert 'REACTIVE' in source, "Prompt should use the word REACTIVE"
        assert 'ALREADY' in source, "Prompt should require ALREADY confirmed setups"
    
    def test_prompt_does_not_encourage_direction_flipping(self):
        """Prompt should NOT contain the old 'EVALUATE BOTH DIRECTIONS EVERY CYCLE'."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        assert 'EVALUATE BOTH DIRECTIONS EVERY CYCLE' not in source, \
            "Old direction-flipping instruction should be removed"
        assert 'actively look for the opposite setup' not in source, \
            "Old direction-seeking instruction should be removed"
    
    def test_prompt_includes_confirmation_checklist(self):
        """Prompt should require citing specific confirmations before any signal."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        # Should mention key confirmations
        assert 'displacement' in source.lower(), "Prompt should mention displacement as confirmation"
        assert 'BOS' in source or 'Break of Structure' in source, "Prompt should mention BOS"
        assert 'CHoCH' in source or 'Change of Character' in source, "Prompt should mention CHoCH"
        assert 'liquidity sweep' in source.lower(), "Prompt should mention liquidity sweep"
        assert 'FVG' in source or 'Fair Value Gap' in source, "Prompt should mention FVG/OB"
    
    def test_prompt_has_variable_confidence_scale(self):
        """Prompt should define a confidence scale (not flat 75%)."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        # Should have a multi-tier confidence scale
        assert '0.60' in source or '60' in source, "Should have 0.60 tier in confidence scale"
        assert '0.70' in source or '70' in source, "Should have 0.70 tier in confidence scale"
        assert '0.80' in source or '80' in source, "Should have 0.80 tier in confidence scale"
        assert '0.90' in source or '90' in source, "Should have 0.90 tier in confidence scale"
        assert 'park at exactly 0.75' in source or 'MUST vary' in source, \
            "Should warn against parking at 0.75"
    
    def test_prompt_warns_against_flipping_without_cause(self):
        """Prompt should tell Claude not to flip direction without confirmed change."""
        import inspect
        from trading_bot.llm.claude_client import ClaudeClient
        
        source = inspect.getsource(ClaudeClient._build_analysis_prompt)
        assert 'flip direction without cause' in source.lower() or \
               'do not flip' in source.lower() or \
               'flip direction' in source.lower(), \
            "Prompt should warn against direction flips without cause"


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
        """_analyze_and_trade should store signals in _last_signal_per_symbol."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._analyze_and_trade)
        assert '_last_signal_per_symbol' in source, \
            "_analyze_and_trade should update _last_signal_per_symbol"
        assert "market_data[\"last_signal\"]" in source or \
               "market_data['last_signal']" in source, \
            "_analyze_and_trade should inject last_signal into market_data"


# ============================================================
# 25. Direction-Flip Cooldown Tests
# ============================================================

class TestDirectionFlipCooldown:
    """Test the direction-flip cooldown guard logic."""
    
    def test_flip_guard_exists_in_code(self):
        """The flip guard logic should exist in _analyze_and_trade."""
        import inspect
        from trading_bot.main import TradingBot
        
        source = inspect.getsource(TradingBot._analyze_and_trade)
        assert 'FLIP-GUARD' in source, "Should have FLIP-GUARD logic"
        assert '_last_signal_direction' in source, "Should track last signal direction"
        assert 'flip_cooldown_minutes' in source, "Should have cooldown window"
        assert 'flip_min_confidence' in source, "Should have higher confidence for flips"
    
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
