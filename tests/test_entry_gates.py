"""Tests for shared entry gates."""

import pytest

from trading_bot.services.entry_gates import (
    ZoneGateSettings,
    evaluate_amd_distribution_gate,
    evaluate_confluence_gate,
    evaluate_direction_alignment_gate,
    evaluate_post_cooldown_gate,
    evaluate_tod_gate,
    evaluate_volatile_regime_gate,
    evaluate_zone_gate,
    should_use_zone_gate,
)
from trading_bot.services.trade_context import TradeContext


class TestZoneGate:
    def test_blocks_misaligned_below_60_or_low_rr(self):
        """Misaligned needs >=60% conf AND min RR; 60% with weak RR still blocks."""
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.60,
            actual_rr=1.9,
            retrace=0.70,
            zone_str="premium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="EURUSD",
        )
        assert result.blocked is True
        assert "blocked_misaligned" in result.decision

    def test_allows_misaligned_at_60_with_rr(self):
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
        assert result.blocked is False
        assert result.decision == "allowed_misaligned_high_conf"

    def test_allows_short_from_discount_at_rr_2_2(self):
        """Replay case: SHORT from discount (36%) with conf=63% RR=2.2 should pass."""
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.63,
            actual_rr=2.2,
            retrace=0.36,
            zone_str="discount",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
        )
        assert result.blocked is False
        assert result.decision == "allowed_misaligned_high_conf"

    def test_allows_equilibrium_at_60(self):
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.60,
            actual_rr=2.0,
            retrace=0.55,
            zone_str="equilibrium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
        )
        assert result.blocked is False
        assert result.decision == "allowed_equilibrium"

    def test_blocks_equilibrium_below_60(self):
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.59,
            actual_rr=2.0,
            retrace=0.55,
            zone_str="equilibrium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
        )
        assert result.blocked is True
        assert "blocked_equilibrium" in result.decision

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
            confidence=0.50,
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


class TestAmdDistributionGate:
    def test_blocks_distribution_below_rr_2_0(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.70,
            actual_rr=1.9,
            analysis_results={"amd_cycle": {"phase": "distribution"}},
        )
        outcome = evaluate_amd_distribution_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "amd_distribution_rr"

    def test_allows_distribution_at_rr_2_0(self):
        """Replay case: distribution + RR ~2.0-2.3 should no longer hard-block."""
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.70,
            actual_rr=2.0,
            analysis_results={"amd_cycle": {"phase": "distribution"}},
        )
        outcome = evaluate_amd_distribution_gate(ctx)
        assert outcome.blocked is False
        assert outcome.confidence_cap == 0.60


class TestDirectionAlignmentGateBasics:
    """Consolidated direction gate (replaced the legacy D1 gate)."""

    def test_blocks_counter_d1_weak_setup(self):
        ctx = TradeContext(
            symbol="EURUSD",
            direction="short",
            confidence=0.55,
            actual_rr=2.0,
            d1_bias="bullish",
        )
        outcome = evaluate_direction_alignment_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "direction_alignment"

    def test_allows_counter_d1_at_60_with_rr(self):
        ctx = TradeContext(
            symbol="EURUSD",
            direction="short",
            confidence=0.60,
            actual_rr=3.0,
            d1_bias="bullish",
        )
        outcome = evaluate_direction_alignment_gate(ctx)
        assert outcome.blocked is False


class TestTodGate:
    def test_blocks_weak_hour_below_60(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.59,
        )
        assert blocked is True

    def test_allows_weak_hour_at_60(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.60,
        )
        assert blocked is False


class TestVolatileRegime:
    def test_blocks_below_60_in_volatile_ranging(self):
        blocked, _ = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.59,
        )
        assert blocked is True

    def test_allows_60_in_volatile_ranging(self):
        blocked, _ = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.60,
        )
        assert blocked is False


class TestShouldUseZoneGate:
    def test_skips_counter_scalp(self):
        assert should_use_zone_gate(True, "active", "EURUSD", (), is_counter_trend_scalp=True) is False


class TestConfluenceAndCooldownAt60:
    def test_confluence_override_allows_60(self):
        ctx = TradeContext(symbol="XAUUSD", direction="long", confidence=0.60)
        outcome = evaluate_confluence_gate(
            ctx,
            confluence_count=0,
            min_confluence=2,
            confidence_override=0.60,
        )
        assert outcome.blocked is False

    def test_confluence_override_blocks_below_60(self):
        ctx = TradeContext(symbol="XAUUSD", direction="long", confidence=0.59)
        outcome = evaluate_confluence_gate(
            ctx,
            confluence_count=0,
            min_confluence=2,
            confidence_override=0.60,
        )
        assert outcome.blocked is True

    def test_post_cooldown_allows_60(self):
        ctx = TradeContext(
            symbol="XAUUSD", direction="long", confidence=0.60, post_cooldown=True
        )
        outcome = evaluate_post_cooldown_gate(ctx)
        assert outcome.blocked is False

    def test_post_cooldown_blocks_below_60(self):
        ctx = TradeContext(
            symbol="XAUUSD", direction="long", confidence=0.59, post_cooldown=True
        )
        outcome = evaluate_post_cooldown_gate(ctx)
        assert outcome.blocked is True
