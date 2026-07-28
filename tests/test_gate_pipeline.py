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
        ctx = _ctx(post_cooldown=True, confidence=0.59)
        outcome = evaluate_structure_and_quality_gates(ctx)
        assert outcome.blocked is True

    def test_low_confluence_allows_at_60(self):
        ctx = _ctx(confidence=0.60, analysis_results={"volume": {"relative_volume": 1.0}})
        outcome = evaluate_structure_and_quality_gates(ctx, is_kill_zone=True)
        assert outcome.blocked is False


class TestFlipGuard:
    def test_blocks_low_confidence_flip(self):
        from datetime import datetime, timedelta, timezone

        from trading_bot.services.scaling_gates import evaluate_flip_guard

        last = {
            "EURUSD": (
                "long",
                datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        }
        outcome = evaluate_flip_guard(
            symbol="EURUSD",
            direction="short",
            confidence=0.75,
            last_signal_direction=last,
        )
        assert outcome.blocked is True
        assert outcome.gate_id == "direction_flip"

    def test_allows_reversal_reentry_bypass(self):
        from datetime import datetime, timezone

        from trading_bot.services.scaling_gates import evaluate_flip_guard

        outcome = evaluate_flip_guard(
            symbol="EURUSD",
            direction="short",
            confidence=0.70,
            last_signal_direction={"EURUSD": ("long", datetime.now(timezone.utc))},
            reversal_reentry=True,
        )
        assert outcome.blocked is False
        assert outcome.gate_path == ["flip_guard_bypass_reversal"]

    def test_handles_naive_last_signal_time_from_replay(self):
        """Replay stores tz-naive snapshot times; flip guard must not crash."""
        from datetime import datetime, timezone

        from trading_bot.services.scaling_gates import evaluate_flip_guard

        last = {"XAUUSD": ("short", datetime(2026, 5, 5, 8, 0, 0))}  # naive
        outcome = evaluate_flip_guard(
            symbol="XAUUSD",
            direction="long",
            confidence=0.75,
            last_signal_direction=last,
            as_of=datetime(2026, 5, 5, 8, 5, 0, tzinfo=timezone.utc),
        )
        assert outcome.blocked is True
        assert outcome.gate_id == "direction_flip"

    def test_uses_as_of_for_cooldown_window(self):
        """Replay must measure cooldown from snapshot time, not wall clock."""
        from datetime import datetime, timezone

        from trading_bot.services.scaling_gates import evaluate_flip_guard

        last = {
            "XAUUSD": ("short", datetime(2026, 5, 5, 6, 0, 0, tzinfo=timezone.utc))
        }
        # 20 minutes later in replay → cooldown expired (default 15m)
        outcome = evaluate_flip_guard(
            symbol="XAUUSD",
            direction="long",
            confidence=0.75,
            last_signal_direction=last,
            as_of=datetime(2026, 5, 5, 6, 20, 0, tzinfo=timezone.utc),
        )
        assert outcome.blocked is False
        assert outcome.gate_path == ["flip_guard"]
