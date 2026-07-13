"""Tests for final pipeline module extractions."""

import inspect

from tests.pipeline_source import analyze_and_trade_source
from trading_bot.execution.trade_fill_handler import TradeFillHandler
from trading_bot.services.claude_analysis_stage import ClaudeAnalysisStage
from trading_bot.services.expanded_analysis import run_expanded_analysis


class TestFinalExtractions:
    def test_expanded_analysis_module_exists(self):
        assert inspect.iscoroutinefunction(run_expanded_analysis)

    def test_expanded_result_has_trade_lock_fields(self):
        from trading_bot.services.expanded_analysis import ExpandedAnalysisResult

        fields = {f.name for f in ExpandedAnalysisResult.__dataclass_fields__.values()}
        assert "amd_state" in fields
        assert "breaker_blocks" in fields
        assert "nwog_target" in fields

    def test_claude_stage_has_run_stage(self):
        assert "run_stage" in inspect.getsource(ClaudeAnalysisStage)
        assert "return ClaudeStageResult" in inspect.getsource(ClaudeAnalysisStage.run_stage)

    def test_fill_handler_release_method_name(self):
        src = inspect.getsource(TradeFillHandler.handle_result)
        assert "_release_trade_reservation" in src
        assert "_releasetrade_reservation" not in src

    def test_simple_position_size_uses_self_lots(self):
        src = analyze_and_trade_source()
        assert "self.lots = lots" in src
        assert "bot.lots = lots" not in src
