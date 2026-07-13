"""Claude chart analysis stage (prompt + API call wrapper)."""

from __future__ import annotations

from typing import Any, Dict, Optional


class ClaudeAnalysisStage:
    """Thin wrapper around Claude chart analysis for pipeline use."""

    def __init__(self, claude_client):
        self.claude_client = claude_client

    async def analyze(
        self,
        *,
        chart_image_base64: str,
        symbol: str,
        strategy_context: str,
        market_data: dict,
        analysis_data: Optional[dict] = None,
        timeframe: str = "M15",
    ) -> Any:
        return await self.claude_client.analyze_chart_async(
            chart_image_base64=chart_image_base64,
            symbol=symbol,
            timeframe=timeframe,
            strategy_context=strategy_context,
            market_data=market_data,
            analysis_data=analysis_data,
        )
