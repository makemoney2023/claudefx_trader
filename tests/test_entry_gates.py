"""Tests for shared entry gates."""

import pytest

from trading_bot.services.entry_gates import (
    ZoneGateSettings,
    evaluate_legacy_d1_gate,
    evaluate_tod_gate,
    evaluate_volatile_regime_gate,
    evaluate_zone_gate,
    should_use_zone_gate,
)


class TestZoneGate:
    def test_blocks_misaligned_low_confidence(self):
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
        assert "blocked_misaligned" in result.decision

    def test_allows_zone_aligned(self):
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.75,
            actual_rr=2.0,
            retrace=0.40,
            zone_str="discount",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="EURUSD",
        )
        assert result.blocked is False
        assert result.decision == "allowed_zone_aligned"

    def test_shadow_mode_does_not_block(self):
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.60,
            actual_rr=2.0,
            retrace=0.70,
            zone_str="premium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(gate_mode="shadow"),
            symbol="EURUSD",
        )
        assert result.blocked is False
        assert result.shadow_only is True


class TestLegacyD1Gate:
    def test_blocks_counter_d1_weak_setup(self):
        blocked, reason = evaluate_legacy_d1_gate(
            direction="short",
            confidence=0.65,
            actual_rr=2.0,
            d1_bias="bullish",
        )
        assert blocked is True
        assert "DIRECTION-GATE" in reason


class TestTodGate:
    def test_blocks_weak_hour_low_confidence(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.60,
        )
        assert blocked is True

    def test_allows_weak_hour_high_confidence(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.75,
        )
        assert blocked is False


class TestVolatileRegime:
    def test_blocks_low_confidence_in_volatile_ranging(self):
        blocked, _ = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.65,
        )
        assert blocked is True


class TestShouldUseZoneGate:
    def test_skips_counter_scalp(self):
        assert should_use_zone_gate(True, "active", "EURUSD", (), is_counter_trend_scalp=True) is False
