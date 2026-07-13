"""Trade pipeline orchestrator — stages for analyze-and-trade flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .analysis_orchestrator import AnalysisOrchestrator
from .claude_analysis_stage import ClaudeAnalysisStage

if TYPE_CHECKING:
    from ..main import TradingBot


class TradePipeline:
    """
    Structural pipeline for _analyze_and_trade.

    Owns the AnalysisOrchestrator and ClaudeAnalysisStage; the full flow is
    executed by analyze_and_trade_runner.run_analyze_and_trade, which calls
    shared gate/normalizer modules directly.
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
        return self.claude_stage

    async def run(self, symbol: str, is_crypto: bool = False) -> None:
        """Execute the full analyze-and-trade pipeline for one symbol."""
        from .analyze_and_trade_runner import run_analyze_and_trade

        await run_analyze_and_trade(self.bot, symbol, is_crypto=is_crypto)
