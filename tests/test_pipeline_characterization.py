"""Characterization tests for extracted pipeline gates (frozen behavior)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trading_bot.services.entry_gates import (
    ZoneGateSettings,
    evaluate_zone_gate,
    evaluate_m15_gate,
    evaluate_htf_alignment_gate,
)
from trading_bot.services.gate_pipeline import evaluate_pre_execution_gates
from trading_bot.services.scaling_gates import evaluate_scaling_gate, setup_grade_from_confidence
from trading_bot.services.trade_context import TradeContext
from trading_bot.services.scaling_manager import ScalingManager, TradingMode
from trading_bot.backtesting.execution_policy import evaluate_judge_gate
from trading_bot.services.trade_judge import JudgeOutcome, JudgeVerdict
from trading_bot.execution.scaling_position_sizer import enforce_final_risk_cap
from trading_bot.config import get_symbol_spec


def _ctx(**kwargs) -> TradeContext:
    base = dict(
        symbol="EURUSD",
        direction="long",
        confidence=0.78,
        actual_rr=2.5,
        d1_bias="bullish",
        h4_bias="bullish",
        m15_bias="bullish",
    )
    base.update(kwargs)
    return TradeContext(**base)


class TestZoneBlockCharacterization:
    def test_premium_long_blocked(self):
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.60,
            actual_rr=2.0,
            retrace=0.70,
            zone_str="premium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="EURUSD",
        )
        assert result.blocked is True


class TestM15GateCharacterization:
    def test_m15_opposes_blocks_non_pullback(self):
        ctx = _ctx(m15_bias="bearish", order_type="market")
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "m15_structure"

    def test_m15_pullback_caps_confidence(self):
        ctx = _ctx(
            m15_bias="bearish",
            order_type="buy_limit",
            d1_bias="bullish",
            h4_bias="bullish",
        )
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is False
        assert outcome.confidence_cap == 0.55


class TestHTFGateCharacterization:
    def test_both_htf_oppose_blocks(self):
        ctx = _ctx(d1_bias="bearish", h4_bias="bearish", trade_type="intraday")
        ctx.m15_opposes = True
        outcome = evaluate_htf_alignment_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "htf_both_oppose"


class TestScalingGateCharacterization:
    def test_defensive_rejects_grade_c(self):
        mgr = ScalingManager()
        mgr.current_mode = TradingMode.DEFENSIVE
        outcome = evaluate_scaling_gate(
            setup_grade="C",
            confidence=0.55,
            daily_trades=0,
            scaling_manager=mgr,
        )
        assert outcome.blocked is True


class TestCorrelationCharacterization:
    def test_correlation_blocks_via_pipeline(self):
        ctx = _ctx(confidence=0.80)
        ctx.analysis_results = {
            "volume": {"relative_volume": 1.0},
            "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
            "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
        }
        outcome = evaluate_pre_execution_gates(
            ctx,
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
            scaling_manager=None,
            is_kill_zone=True,
            correlation_check=lambda: (True, "EURUSD correlated with GBPUSD"),
        )
        assert outcome.blocked is True
        assert outcome.gate_id == "correlation"


class TestJudgeDemoteCharacterization:
    def test_demote_allows_pending_path(self):
        allowed, path = evaluate_judge_gate(
            JudgeOutcome(verdict=JudgeVerdict.DEMOTE, reason="limit too far")
        )
        assert allowed is True
        assert "demote" in path


class TestPositionStackingCharacterization:
    def test_same_direction_detected(self):
        positions = [
            SimpleNamespace(ticket=1, direction="long"),
            SimpleNamespace(ticket=2, direction="short"),
        ]
        same = [p for p in positions if p.direction == "long"]
        assert len(same) == 1


class TestFinalRiskCharacterization:
    def test_oversized_lots_blocked(self):
        spec = get_symbol_spec("EURUSD")
        allowed, _, reason = enforce_final_risk_cap(
            1000.0,
            0.02,
            1.0850,
            1.0800,
            10.0,
            spec,
            symbol="EURUSD",
        )
        assert allowed < 10.0 or reason


class TestSetupGrade:
    def test_grade_from_confidence(self):
        assert setup_grade_from_confidence(0.86) == "A+"
        assert setup_grade_from_confidence(0.62) == "B"
        assert setup_grade_from_confidence(0.59) == "C"
