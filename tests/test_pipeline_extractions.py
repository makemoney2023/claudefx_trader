"""Tests for final pipeline module extractions."""

import inspect

from tests.pipeline_source import analyze_and_trade_source
from trading_bot.execution.trade_fill_handler import TradeFillHandler
from trading_bot.services.claude_analysis_stage import ClaudeAnalysisStage
from trading_bot.services.expanded_analysis import run_expanded_analysis


class TestFinalExtractions:
    def test_expanded_analysis_module_exists(self):
        assert inspect.iscoroutinefunction(run_expanded_analysis)

    def test_claude_stage_has_run_stage(self):
        assert "run_stage" in inspect.getsource(ClaudeAnalysisStage)

    def test_fill_handler_exists(self):
        assert inspect.iscoroutinefunction(TradeFillHandler.handle_result)

    def test_runner_delegates_to_modules(self):
        src = analyze_and_trade_source()
        assert "run_expanded_analysis" in src
        assert "run_stage" in src
        assert "TradeFillHandler.handle_result" in src
