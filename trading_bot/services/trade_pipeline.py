"""Trade pipeline orchestrator — stages for analyze-and-trade flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .analysis_orchestrator import AnalysisOrchestrator
from .claude_analysis_stage import ClaudeAnalysisStage
from .gate_pipeline import evaluate_entry_gates, evaluate_trade_permission_gates
from .signal_normalizer import normalize_signal_prices

if TYPE_CHECKING:
    from ..main import TradingBot


class TradePipeline:
    """
    Structural pipeline for _analyze_and_trade.

    Stages are invoked by TradingBot; heavy logic remains in dedicated modules.
    """

    def __init__(self, bot: "TradingBot"):
        self.bot = bot
        self.analysis = AnalysisOrchestrator()
        self.claude_stage: Optional[ClaudeAnalysisStage] = None

    def claude(self) -> ClaudeAnalysisStage:
        if self.claude_stage is None:
            self.claude_stage = ClaudeAnalysisStage(self.bot.claude_client)
        return self.claude_stage

    def normalize_signal(self, trade_signal, claude_result, current_price, symbol):
        return normalize_signal_prices(
            trade_signal, claude_result, current_price, symbol
        )

    def run_entry_gates(self, ctx, **kwargs):
        return evaluate_entry_gates(ctx, **kwargs)

    def run_permission_gates(self, ctx, **kwargs):
        return evaluate_trade_permission_gates(ctx, **kwargs)
