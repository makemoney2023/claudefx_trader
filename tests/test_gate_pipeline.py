"""Tests for ordered gate pipeline."""

from types import SimpleNamespace

import pytest

from trading_bot.services.entry_gates import ZoneGateSettings
from trading_bot.services.gate_pipeline import (
    count_confluence,
    evaluate_pre_execution_gates,
    evaluate_structure_and_quality_gates,
)
from trading_bot.services.trade_context import TradeContext
from trading_bot.services.scaling_manager import ScalingManager, TradingMode


def _ctx(**kwargs) -> TradeContext:
    base = dict(
        symbol="EURUSD",
        direction="long",
        confidence=0.78,
        actual_rr=2.5,
        d1_bias="bullish",
        h4_bias="bullish",
        m15_bias="bullish",
        analysis_results={"volume": {"relative_volume": 1.0}},
    )
    base.update(kwargs)
    return TradeContext(**base)


class TestGatePipeline:
    def test_passes_clean_setup(self):
        ctx = _ctx(confidence=0.80)
        ctx.analysis_results = {
            "volume": {"relative_volume": 1.0},
            "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
            "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
        }
        mgr = ScalingManager()
        mgr.current_mode = TradingMode.NORMAL
        outcome = evaluate_pre_execution_gates(
            ctx,
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
            scaling_manager=mgr,
            daily_trades=0,
            is_kill_zone=True,
        )
        assert outcome.blocked is False

    def test_volume_dead_market_blocks(self):
        ctx = _ctx(analysis_results={"volume": {"relative_volume": 0.2}})
        outcome = evaluate_structure_and_quality_gates(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "volume_dead_market"

    def test_confluence_count_empty(self):
        count, factors = count_confluence(_ctx())
        assert count == 0
        assert factors == []

    def test_off_hours_blocks_low_rr(self):
        ctx = _ctx(off_hours_mode=True, actual_rr=2.0)
        outcome = evaluate_structure_and_quality_gates(ctx)
        assert outcome.blocked is True

    def test_post_cooldown_blocks_low_confidence(self):
        ctx = _ctx(post_cooldown=True, confidence=0.70)
        outcome = evaluate_structure_and_quality_gates(ctx)
        assert outcome.blocked is True
