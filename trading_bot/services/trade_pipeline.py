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
        bot._analysis_orchestrator = self.analysis
        self.claude_stage: Optional[ClaudeAnalysisStage] = None
        bot._trade_pipeline = self

    def claude(self) -> ClaudeAnalysisStage:
        if self.claude_stage is None:
            self.claude_stage = ClaudeAnalysisStage(self.bot.claude_client)
            self.bot._claude_stage = self.claude_stage
        return self.claude_stage

    def normalize_signal(self, trade_signal, claude_result, current_price, symbol):
        return normalize_signal_prices(
            trade_signal, claude_result, current_price, symbol
        )

    def run_entry_gates(self, ctx, **kwargs):
        return evaluate_entry_gates(ctx, **kwargs)

    def run_permission_gates(self, ctx, **kwargs):
        return evaluate_trade_permission_gates(ctx, **kwargs)

    async def run(self, symbol: str, is_crypto: bool = False) -> None:
        """Execute the full analyze-and-trade pipeline for one symbol."""
        from .analyze_and_trade_runner import run_analyze_and_trade

        await run_analyze_and_trade(self.bot, symbol, is_crypto=is_crypto)
