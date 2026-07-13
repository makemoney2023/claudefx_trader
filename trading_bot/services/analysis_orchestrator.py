"""Core ICT analysis assembly shared by live and simulation paths."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..analysis.fair_value_gap import FVGDetector
from ..analysis.liquidity import LiquidityMapper
from ..analysis.market_structure import MarketStructureAnalyzer
from ..analysis.order_blocks import OrderBlockDetector
from ..analysis.volume_analysis import VolumeAnalyzer
from ..config import get_symbol_spec


class AnalysisOrchestrator:
    """Runs core ICT analysis on execution-timeframe OHLCV."""

    def run_core_analysis(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> Dict[str, Any]:
        pip = get_symbol_spec(symbol).pip_size
        results: Dict[str, Any] = {
            "market_structure": MarketStructureAnalyzer().analyze(df),
            "fvg": FVGDetector(pip_value=pip).detect(df),
            "order_blocks": OrderBlockDetector().detect(df),
            "liquidity": LiquidityMapper(pip_value=pip).analyze(df),
        }
        try:
            volume_analysis = VolumeAnalyzer().analyze(df)
            results["volume"] = (
                volume_analysis.to_dict()
                if hasattr(volume_analysis, "to_dict")
                else volume_analysis
            )
        except Exception:
            results["volume"] = {}
        return results

    async def fetch_and_analyze(
        self,
        data_fetcher,
        symbol: str,
        timeframe: str,
        count: int,
    ) -> tuple[Optional[pd.DataFrame], Dict[str, Any]]:
        df = await data_fetcher.get_ohlcv(
            symbol=symbol, timeframe=timeframe, count=count
        )
        if df is None or df.empty:
            return df, {}
        return df, self.run_core_analysis(symbol, df)
