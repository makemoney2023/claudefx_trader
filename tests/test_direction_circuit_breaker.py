"""Tests for the same-direction daily circuit breaker (live/replay parity).

After N consecutive same-direction losses on a symbol in one UTC day, block
further trades in that direction until the next day.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from trading_bot.services.direction_circuit_breaker import (
    DirectionCircuitBreakerSettings,
    DirectionLossTracker,
    evaluate_direction_circuit_breaker,
)
from trading_bot.services.entry_gates import ZoneGateSettings
from trading_bot.services.post_claude_gates import (
    PostClaudeGateInput,
    run_post_claude_gates,
)
from trading_bot.services.signal_normalizer import NormalizedSignal


D1 = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
D1_LATER = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
D2 = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)


class TestDirectionLossTracker:
    def test_counts_consecutive_same_direction_losses(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        t.record("EURUSD", "long", "loss", D1_LATER)
        assert t.consecutive_losses("EURUSD", "long", D1_LATER) == 2

    def test_win_resets_streak(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        t.record("EURUSD", "long", "win", D1_LATER)
        assert t.consecutive_losses("EURUSD", "long", D1_LATER) == 0

    def test_streak_resets_on_new_utc_day(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        t.record("EURUSD", "long", "loss", D1_LATER)
        assert t.consecutive_losses("EURUSD", "long", D2) == 0

    def test_directions_tracked_independently(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        t.record("EURUSD", "long", "loss", D1_LATER)
        assert t.consecutive_losses("EURUSD", "short", D1_LATER) == 0

    def test_symbols_tracked_independently(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        assert t.consecutive_losses("GBPUSD", "long", D1) == 0

    def test_timeout_outcome_does_not_change_streak(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        t.record("EURUSD", "long", "timeout", D1_LATER)
        assert t.consecutive_losses("EURUSD", "long", D1_LATER) == 1

    def test_handles_naive_replay_timestamps(self):
        t = DirectionLossTracker()
        naive = datetime(2026, 7, 29, 9, 0)
        t.record("EURUSD", "long", "loss", naive)
        t.record("EURUSD", "long", "loss", naive)
        assert t.consecutive_losses("EURUSD", "long", D1_LATER) == 2

    def test_normalizes_buy_sell_to_long_short(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "buy", "loss", D1)
        t.record("EURUSD", "long", "loss", D1_LATER)
        assert t.consecutive_losses("EURUSD", "long", D1_LATER) == 2


class TestTrackerPersistence:
    def test_round_trip_preserves_streaks(self):
        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        t.record("EURUSD", "long", "loss", D1_LATER)
        t.record("GBPUSD", "short", "loss", D1)

        restored = DirectionLossTracker.from_dict(t.to_dict())
        assert restored.consecutive_losses("EURUSD", "long", D1_LATER) == 2
        assert restored.consecutive_losses("GBPUSD", "short", D1_LATER) == 1

    def test_to_dict_is_json_serializable(self):
        import json

        t = DirectionLossTracker()
        t.record("EURUSD", "long", "loss", D1)
        assert json.loads(json.dumps(t.to_dict())) == t.to_dict()

    def test_from_dict_skips_malformed_entries(self):
        restored = DirectionLossTracker.from_dict(
            {
                "EURUSD|long": {"date": D1.date().isoformat(), "streak": 2},
                "bad-key": {"date": "nope", "streak": "x"},
                "GBPUSD|short": "not-a-dict",
            }
        )
        assert restored.consecutive_losses("EURUSD", "long", D1) == 2
        assert restored.consecutive_losses("GBPUSD", "short", D1) == 0

    def test_from_dict_handles_none(self):
        restored = DirectionLossTracker.from_dict(None)
        assert restored.consecutive_losses("EURUSD", "long", D1) == 0


class TestCircuitBreakerGate:
    def test_blocks_at_max_losses(self):
        outcome = evaluate_direction_circuit_breaker(
            symbol="EURUSD",
            direction="long",
            consecutive_losses=2,
            settings=DirectionCircuitBreakerSettings(max_consecutive_losses=2),
        )
        assert outcome.blocked is True
        assert outcome.gate_id == "direction_circuit_breaker"
        assert "LONG" in outcome.reason

    def test_passes_below_max(self):
        outcome = evaluate_direction_circuit_breaker(
            symbol="EURUSD",
            direction="long",
            consecutive_losses=1,
            settings=DirectionCircuitBreakerSettings(max_consecutive_losses=2),
        )
        assert outcome.blocked is False

    def test_disabled_when_max_is_zero(self):
        outcome = evaluate_direction_circuit_breaker(
            symbol="EURUSD",
            direction="long",
            consecutive_losses=5,
            settings=DirectionCircuitBreakerSettings(max_consecutive_losses=0),
        )
        assert outcome.blocked is False


def _signal(**kwargs):
    base = dict(
        direction="long",
        confidence=0.82,
        entry_price=1.0850,
        stop_loss=1.0840,
        take_profit=1.0950,
        trade_type="intraday",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _norm():
    return NormalizedSignal(
        entry=1.0850, sl=1.0840, tp=1.0950, direction="long", rejected=False
    )


def _df_with_atr():
    rows = []
    for i in range(30):
        p = 1.08 + i * 0.0001
        rows.append(
            {"open": p, "high": p + 0.0010, "low": p - 0.0005, "close": p + 0.0002}
        )
    return pd.DataFrame(rows)


class TestPostClaudeChainIntegration:
    def _base_input(self, **kwargs):
        base = dict(
            symbol="EURUSD",
            trade_signal=_signal(),
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

    def test_blocks_when_streak_at_max(self):
        result = run_post_claude_gates(
            self._base_input(direction_loss_streak=2), stop_after="complete"
        )
        assert result.blocked is True
        assert result.gate_id == "direction_circuit_breaker"

    def test_passes_when_streak_below_max(self):
        result = run_post_claude_gates(
            self._base_input(direction_loss_streak=1), stop_after="complete"
        )
        assert result.blocked is False

    def test_default_streak_zero_passes(self):
        result = run_post_claude_gates(self._base_input(), stop_after="complete")
        assert result.blocked is False
