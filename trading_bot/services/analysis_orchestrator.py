"""Core ICT analysis assembly shared by live and simulation paths."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from ..analysis.fair_value_gap import FVGDetector
from ..analysis.liquidity import LiquidityMapper
from ..analysis.market_structure import MarketStructureAnalyzer
from ..analysis.order_blocks import OrderBlockDetector
from ..analysis.volume_analysis import VolumeAnalyzer
from ..config import get_symbol_spec
from ..utils.logging import get_logger

logger = get_logger(__name__)

try:
    from ..api.database import DB_AVAILABLE
except ImportError:
    DB_AVAILABLE = False


@dataclass
class ChartPackage:
    mtf_dfs: Dict[str, pd.DataFrame] = field(default_factory=dict)
    additional_charts: List[dict] = field(default_factory=list)
    trade_markers: List[dict] = field(default_factory=list)
    vp_data: Optional[dict] = None
    bar_extreme_zones: List[dict] = field(default_factory=list)
    bar_extreme_results: Dict[str, dict] = field(default_factory=dict)
    reactive_levels: List[Any] = field(default_factory=list)


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

    async def build_chart_package(
        self,
        bot: Any,
        *,
        symbol: str,
        df: pd.DataFrame,
        generate_chart_image,
    ) -> ChartPackage:
        """Fetch MTF data and build composite + LTF chart images for Claude."""
        package = ChartPackage()
        try:
            for timeframe, count in [
                ("D1", 60),
                ("H4", 100),
                ("H1", 100),
                ("M5", 100),
                ("M1", 100),
            ]:
                tf_df = await bot.data_fetcher.get_ohlcv(
                    symbol=symbol, timeframe=timeframe, count=count
                )
                if tf_df is not None and not tf_df.empty:
                    package.mtf_dfs[timeframe] = tf_df

            package.trade_markers = await self._fetch_trade_markers(symbol)
            package.reactive_levels = await self._fetch_reactive_levels(bot, symbol)
            package.vp_data = self._compute_volume_profile(df, symbol)
            package.bar_extreme_zones, package.bar_extreme_results = (
                self._detect_bar_extreme_zones(df, package.mtf_dfs)
            )

            composite_base64 = await self._build_composite_chart(
                symbol=symbol,
                df=df,
                mtf_dfs=package.mtf_dfs,
                trade_markers=package.trade_markers,
                vp_data=package.vp_data,
                reactive_levels=package.reactive_levels,
                bar_extreme_zones=package.bar_extreme_zones,
            )

            for ltf in ("M5", "M1"):
                ltf_df = package.mtf_dfs.get(ltf)
                if ltf_df is not None and not ltf_df.empty:
                    ltf_chart = await generate_chart_image(ltf_df, symbol, timeframe=ltf)
                    if ltf_chart:
                        package.additional_charts.append(
                            {"base64": ltf_chart, "timeframe": ltf}
                        )

            if composite_base64:
                package.additional_charts.insert(
                    0,
                    {
                        "base64": composite_base64,
                        "timeframe": "COMPOSITE (D1/H4/H1/M15/M5)",
                    },
                )

            if package.additional_charts:
                logger.info(
                    f"Sending {len(package.additional_charts)} charts for {symbol} "
                    f"(composite + LTF)"
                )
        except Exception as exc:
            logger.warning(f"Failed to generate multi-TF charts for {symbol}: {exc}")
        return package

    async def _fetch_trade_markers(self, symbol: str) -> List[dict]:
        markers: List[dict] = []
        if not DB_AVAILABLE:
            return markers
        try:
            from ..api.database import async_session_maker, TradeModel
            from sqlalchemy import desc, select

            async with async_session_maker() as session:
                query = (
                    select(TradeModel)
                    .where(TradeModel.symbol == symbol)
                    .order_by(desc(TradeModel.timestamp))
                    .limit(5)
                )
                rows = (await session.execute(query)).scalars().all()
                for row in rows:
                    markers.append(
                        {
                            "time": row.entry_time or row.timestamp,
                            "price": row.entry_price,
                            "direction": row.direction,
                            "outcome": "win" if (row.profit_loss or 0) > 0 else "loss",
                            "label": (
                                f"{'+' if (row.r_multiple or 0) >= 0 else ''}"
                                f"{(row.r_multiple or 0):.1f}R"
                            ),
                        }
                    )
            if markers:
                logger.info(f"[MARKERS] {symbol}: {len(markers)} trade markers for chart")
        except Exception as exc:
            logger.debug(f"[MARKERS] Could not fetch trade markers for {symbol}: {exc}")
        return markers

    async def _fetch_reactive_levels(self, bot: Any, symbol: str) -> List[Any]:
        try:
            if bot.learning_service:
                levels = await bot.learning_service.get_reactive_levels(
                    symbol, lookback_days=90
                )
                if levels:
                    logger.info(f"[REACTIVE] {symbol}: {len(levels)} reactive levels found")
                return levels or []
        except Exception as exc:
            logger.debug(f"[REACTIVE] Error fetching reactive levels for {symbol}: {exc}")
        return []

    def _compute_volume_profile(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        try:
            from ..analysis.volume_profile import compute_volume_profile

            vp_data = compute_volume_profile(df, num_bins=50)
            if vp_data:
                logger.info(
                    f"[VP] {symbol}: POC={vp_data['poc']:.5f}, "
                    f"VAH={vp_data['vah']:.5f}, VAL={vp_data['val']:.5f}"
                )
            return vp_data
        except Exception as exc:
            logger.debug(f"[VP] Volume profile error for {symbol}: {exc}")
            return None

    def _detect_bar_extreme_zones(
        self,
        df: pd.DataFrame,
        mtf_dfs: Dict[str, pd.DataFrame],
    ) -> tuple[List[dict], Dict[str, dict]]:
        zones: List[dict] = []
        results: Dict[str, dict] = {}
        try:
            from ..analysis.bar_extreme_zones import BarExtremeZoneDetector

            detector = BarExtremeZoneDetector()
            current_price = float(df["close"].iloc[-1])
            for timeframe, tf_df in [
                ("D1", mtf_dfs.get("D1")),
                ("H1", mtf_dfs.get("H1")),
                ("M15", df),
                ("M5", mtf_dfs.get("M5")),
            ]:
                if tf_df is None or len(tf_df) <= 20:
                    continue
                result = detector.detect(tf_df, current_price, timeframe)
                results[f"bar_extreme_{timeframe.lower()}"] = result.to_dict()
                if result.supply_zone:
                    zones.append(
                        {
                            "top": result.supply_zone.top,
                            "bottom": result.supply_zone.bottom,
                            "type": "supply",
                            "tf": timeframe,
                        }
                    )
                if result.demand_zone:
                    zones.append(
                        {
                            "top": result.demand_zone.top,
                            "bottom": result.demand_zone.bottom,
                            "type": "demand",
                            "tf": timeframe,
                        }
                    )
            if zones:
                logger.info(
                    f"[BAR_EXTREME] zones across "
                    f"{len(set(zone['tf'] for zone in zones))} timeframes"
                )
        except Exception as exc:
            logger.debug(f"[BAR_EXTREME] Error: {exc}")
        return zones, results

    async def _build_composite_chart(
        self,
        *,
        symbol: str,
        df: pd.DataFrame,
        mtf_dfs: Dict[str, pd.DataFrame],
        trade_markers: List[dict],
        vp_data: Optional[dict],
        reactive_levels: List[Any],
        bar_extreme_zones: List[dict],
    ) -> Optional[str]:
        try:
            from ..utils.chart_screenshot import create_composite_chart

            panels = []
            for panel_tf, panel_df in [
                ("D1", mtf_dfs.get("D1")),
                ("H4", mtf_dfs.get("H4")),
                ("H1", mtf_dfs.get("H1")),
                ("M15", df),
                ("M5", mtf_dfs.get("M5")),
            ]:
                if panel_df is not None and not panel_df.empty:
                    panels.append(
                        {"timeframe": panel_tf, "df": panel_df, "overlays": {}}
                    )
            if len(panels) < 2:
                return None

            kwargs: Dict[str, Any] = {}
            if trade_markers:
                kwargs["trade_markers"] = trade_markers
            if vp_data:
                kwargs["volume_profile"] = vp_data
            if reactive_levels:
                kwargs["reactive_levels"] = reactive_levels
            if bar_extreme_zones:
                kwargs["bar_extreme_zones"] = bar_extreme_zones

            composite = await asyncio.to_thread(
                create_composite_chart, panels, symbol, **kwargs
            )
            if composite:
                logger.info(
                    f"[COMPOSITE] {symbol}: Generated {len(panels)}-panel composite chart"
                )
            return composite
        except Exception as exc:
            logger.warning(f"[COMPOSITE] {symbol}: Failed to generate composite: {exc}")
            return None
