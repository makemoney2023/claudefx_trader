"""
Tests for wiring ICTStrategy in as a mechanical advisory and adding the H4
chart panel to Claude's composite view.

1. ICTStrategy runs as an advisory cross-check (not an execution driver):
   its setup dict is exposed to Claude and agreement is logged.
2. The composite chart supports 5+ panels so H4 (the core ICT structure
   timeframe) is rendered visually, not just as text bias.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock


def _ohlcv_df(n=30, base=1.10, freq="4h"):
    idx = pd.date_range(start="2026-01-01", periods=n, freq=freq)
    rng = np.random.default_rng(7)
    close = base + np.cumsum(rng.normal(0, 0.0005, n))
    high = close + 0.0008
    low = close - 0.0008
    open_ = close + rng.normal(0, 0.0002, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 100},
        index=idx,
    )


# ================================================================
# 1. Mechanical advisory helper
# ================================================================

class TestMechanicalSetupAdvisory:
    def _bot(self):
        from trading_bot.main import TradingBot
        return TradingBot.__new__(TradingBot)

    def test_returns_setup_dict_and_tags_symbol(self):
        bot = self._bot()
        captured = {}

        def fake_analyze(htf_data, ltf_data, symbol, htf_name="H4", ltf_name="M15"):
            captured["symbol_attr"] = ltf_data.attrs.get("symbol")
            captured["htf_name"] = htf_name
            setup = MagicMock()
            setup.to_dict.return_value = {"direction": "long", "confidence": 0.65}
            return setup

        bot.strategy = MagicMock()
        bot.strategy.analyze = fake_analyze

        result = bot._mechanical_setup_advisory("EURUSD", _ohlcv_df(), _ohlcv_df(freq="15min"))
        assert result == {"direction": "long", "confidence": 0.65}
        # SL buffer logic in ICTStrategy reads df.attrs['symbol']
        assert captured["symbol_attr"] == "EURUSD"
        assert captured["htf_name"] == "H4"

    def test_returns_none_when_no_setup(self):
        bot = self._bot()
        bot.strategy = MagicMock()
        bot.strategy.analyze = MagicMock(return_value=None)
        assert bot._mechanical_setup_advisory("EURUSD", _ohlcv_df(), _ohlcv_df()) is None

    def test_returns_none_on_error(self):
        bot = self._bot()
        bot.strategy = MagicMock()
        bot.strategy.analyze = MagicMock(side_effect=RuntimeError("boom"))
        assert bot._mechanical_setup_advisory("EURUSD", _ohlcv_df(), _ohlcv_df()) is None

    def test_returns_none_without_strategy_or_data(self):
        bot = self._bot()
        bot.strategy = None
        assert bot._mechanical_setup_advisory("EURUSD", _ohlcv_df(), _ohlcv_df()) is None

        bot.strategy = MagicMock()
        assert bot._mechanical_setup_advisory("EURUSD", None, _ohlcv_df()) is None
        assert bot._mechanical_setup_advisory("EURUSD", _ohlcv_df(), None) is None


# ================================================================
# 2. Wiring into the live pipeline (source-level, following repo convention)
# ================================================================

class TestPipelineWiring:
    def _main_source(self):
        from tests.pipeline_source import pipeline_source
        return pipeline_source()

    def _claude_source(self):
        import trading_bot.llm.claude_client as cc
        return Path(cc.__file__).read_text()

    def test_h4_in_multi_timeframe_fetch(self):
        import inspect

        from trading_bot.services.analysis_orchestrator import AnalysisOrchestrator

        src = self._main_source() + inspect.getsource(AnalysisOrchestrator.build_chart_package)
        assert '("H4", 100)' in src or "('H4', 100)" in src, "H4 must be fetched for the composite chart"

    def test_mechanical_setup_flows_to_market_data(self):
        src = self._main_source()
        assert "_mechanical_setup_advisory(" in src
        assert 'market_data["mechanical_ict_setup"]' in src

    def test_prompt_builder_renders_mechanical_setup(self):
        src = self._claude_source()
        assert "mechanical_ict_setup" in src, (
            "prompt builder must render the mechanical baseline for Claude"
        )

    def test_agreement_telemetry_logged(self):
        src = self._main_source()
        assert "MECH-VS-CLAUDE" in src


# ================================================================
# 3. Composite chart supports 5+ panels (H4 included)
# ================================================================

class TestCompositeFivePanels:
    def _png_size(self, b64):
        import base64, io
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        return img.size

    def test_five_panel_composite_renders_larger_grid(self):
        """5 panels must not be silently capped to a 2x2 grid (H4 dropped)."""
        from trading_bot.utils.chart_screenshot import create_composite_chart

        four = []
        for tf, freq in [("D1", "1D"), ("H1", "1h"), ("M15", "15min"), ("M5", "5min")]:
            four.append({"timeframe": tf, "df": _ohlcv_df(n=20, freq=freq), "overlays": {}})
        five = [{"timeframe": "H4", "df": _ohlcv_df(n=20, freq="4h"), "overlays": {}}] + four

        b64_four = create_composite_chart(four, "EURUSD")
        b64_five = create_composite_chart(five, "EURUSD")
        assert isinstance(b64_five, str) and len(b64_five) > 1000

        w4, h4_ = self._png_size(b64_four)
        w5, h5 = self._png_size(b64_five)
        assert h5 > h4_, "5-panel composite must add a third row, not drop a panel"

    def test_four_panel_composite_still_works(self):
        from trading_bot.utils.chart_screenshot import create_composite_chart

        panels = []
        for tf, freq in [("D1", "1D"), ("H1", "1h"), ("M15", "15min"), ("M5", "5min")]:
            panels.append({"timeframe": tf, "df": _ohlcv_df(n=20, freq=freq), "overlays": {}})

        b64 = create_composite_chart(panels, "EURUSD")
        assert isinstance(b64, str) and len(b64) > 1000
