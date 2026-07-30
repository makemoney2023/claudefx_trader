"""Tests for shared post-Claude gate chain (live/replay parity)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from trading_bot.services.entry_gates import ZoneGateSettings
from trading_bot.services.gate_pipeline import evaluate_entry_gates
from trading_bot.services.post_claude_gates import (
    PostClaudeGateInput,
    PostClaudeGateSettings,
    SecondaryModifierInput,
    apply_atr_sl_adjustment,
    build_reject_details,
    compute_actual_rr,
    evaluate_rr_hard_floor,
    is_counter_trend_scalp_signal,
    resolve_min_rr,
    resolve_session_at_time,
    run_post_claude_gates,
)
from trading_bot.services.signal_normalizer import NormalizedSignal
from trading_bot.services.trade_context import TradeContext


def _signal(**kwargs):
    base = dict(
        direction="long",
        confidence=0.78,
        entry_price=1.0850,
        stop_loss=1.0840,
        take_profit=1.0950,
        trade_type="intraday",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _norm(**kwargs):
    base = dict(
        entry=1.0850,
        sl=1.0840,
        tp=1.0950,
        direction="long",
        rejected=False,
    )
    base.update(kwargs)
    return NormalizedSignal(**base)


def _df_with_atr():
    rows = []
    for i in range(30):
        p = 1.08 + i * 0.0001
        rows.append({"open": p, "high": p + 0.0010, "low": p - 0.0005, "close": p + 0.0002})
    return pd.DataFrame(rows)


class TestPriceGates:
    def test_atr_sl_widen_blocks_low_rr(self):
        sig = _signal()
        sl, rr, block = apply_atr_sl_adjustment(
            entry=1.0850,
            sl=1.0848,
            tp=1.0860,
            direction="long",
            atr_val=0.0020,
            trade_signal=sig,
        )
        assert block is not None
        assert block.gate_id == "atr_sl_block"
        assert sl < 1.0848

    def test_rr_hard_floor_blocks_aggressive_off(self):
        block = evaluate_rr_hard_floor(1.2, min_rr=2.0, is_aggressive=False)
        assert block is not None
        assert block.gate_id == "rr_hard_floor"

    def test_rr_hard_floor_allows_borderline_for_judge(self):
        block = evaluate_rr_hard_floor(1.6, min_rr=2.0, is_aggressive=False)
        assert block is None

    def test_counter_trend_scalp_flag_and_pipeline_block(self):
        """Flag detection stays in post-Claude; enforcement moved to the
        pipeline's direction-alignment gate."""
        assert is_counter_trend_scalp_signal(
            trade_type="scalp", d1_bias="bullish", direction="short",
        ) is True
        assert is_counter_trend_scalp_signal(
            trade_type="intraday", d1_bias="bullish", direction="short",
        ) is False

        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = TradeContext(
            symbol="EURUSD",
            direction="short",
            confidence=0.75,
            actual_rr=1.5,
            d1_bias="bullish",
            trade_type="scalp",
        )
        outcome = evaluate_direction_alignment_gate(ctx, scalp_rr_floor=2.0)
        assert outcome.blocked is True
        assert outcome.gate_id == "direction_alignment"


class TestSessionResolution:
    def test_kill_zone_checker_uses_snapshot_time(self):
        checker = MagicMock()
        checker.get_current_session.return_value = SimpleNamespace(
            session_name="London",
            is_kill_zone=True,
        )
        ts = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
        name, is_kz = resolve_session_at_time(ts, checker)
        assert name == "london"
        assert is_kz is True
        checker.get_current_session.assert_called_once_with(ts)


class TestPostClaudeGateChain:
    def _base_input(self, **kwargs):
        sig = _signal(confidence=0.82)
        base = dict(
            symbol="EURUSD",
            trade_signal=sig,
            norm=_norm(),
            market_data={"d1_bias": "bullish"},
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
                "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
            },
            pd_analysis=None,
            current_price=1.0850,
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
            is_kill_zone=True,
            session_name="london",
            last_signal_direction={},
            direction_flipped=False,
            df=_df_with_atr(),
        )
        base.update(kwargs)
        return PostClaudeGateInput(**base)

    def test_complete_chain_passes_clean_setup(self):
        result = run_post_claude_gates(self._base_input(), stop_after="complete")
        assert result.blocked is False
        assert "post_claude_gates_complete" in result.gate_path

    def test_flip_guard_blocks_low_confidence_flip(self):
        last = {
            "EURUSD": (
                "long",
                datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        }
        inp = self._base_input(
            norm=_norm(direction="short"),
            trade_signal=_signal(direction="short", confidence=0.75),
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[], bearish_fvgs=[1]),
                "order_blocks": SimpleNamespace(bullish_obs=[], bearish_obs=[1]),
            },
            last_signal_direction=last,
        )
        result = run_post_claude_gates(inp, stop_after="complete")
        assert result.blocked is True
        assert result.gate_id == "direction_flip"

    def test_replay_uses_session_kill_zone_not_hardcoded_true(self):
        checker = MagicMock()
        checker.get_current_session.return_value = SimpleNamespace(
            session_name="Asian",
            is_kill_zone=False,
        )
        ts = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)
        inp = self._base_input(
            snapshot_time=ts,
            session_name="",
            is_kill_zone=False,
        )
        result = run_post_claude_gates(
            inp,
            stop_after="entry",
            kill_zone_checker=checker,
        )
        assert result.blocked is False
        checker.get_current_session.assert_called()

    def test_build_reject_details_includes_signal_fields(self):
        details = build_reject_details(
            gate_path=["entry_gates", "min_confidence"],
            direction="long",
            entry=1.085,
            sl=1.08,
            tp=1.095,
            confidence=0.72,
        )
        assert details["gate_path"] == ["entry_gates", "min_confidence"]
        assert details["direction"] == "long"
        assert details["confidence"] == pytest.approx(0.72)


class TestTelemetryWiring:
    def test_handle_pipeline_gate_block_records_signal_fields(self):
        import inspect
        from trading_bot.main import TradingBot

        source = inspect.getsource(TradingBot._handle_pipeline_gate_block)
        assert "direction=" in source or "ctx.direction" in source
        assert "entry=" in source or "ctx." in source
        assert "build_reject_details" in source or '"gate_path"' in source
