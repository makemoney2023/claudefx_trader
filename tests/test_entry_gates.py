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
    def test_blocks_wrong_zone_without_sweep_displacement(self):
        """Long from premium needs sweep+displacement — conf/RR alone is not enough."""
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
        assert "wrong_zone" in result.decision

    def test_allows_wrong_zone_with_sweep_and_displacement(self):
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
            has_sweep=True,
            has_displacement=True,
        )
        assert result.blocked is False
        assert "confirmed" in result.decision

    def test_blocks_short_from_discount_even_with_high_rr(self):
        """High conf/RR no longer bypasses wrong-zone without structure confirms."""
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
        assert result.blocked is True
        assert "wrong_zone" in result.decision

    def test_blocks_soft_wrong_zone_at_55pct(self):
        """Long above 50% is wrong-zone (no more equilibrium bypass at 60%)."""
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
        assert result.blocked is True
        assert "wrong_zone" in result.decision

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

    def test_htf_aligned_short_discount_displacement_allows_continuation(self):
        """Live log case: SHORT @ 46% with disp, no sweep, HTF bearish."""
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=1.99,
            retrace=0.46,
            zone_str="equilibrium",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=False,
            has_displacement=True,
            htf_aligned=True,
        )
        assert result.blocked is False
        assert result.decision == "allowed_wrong_zone_continuation"

    def test_htf_aligned_long_premium_displacement_allows_continuation(self):
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.65,
            actual_rr=2.0,
            retrace=0.70,
            zone_str="premium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=False,
            has_displacement=True,
            htf_aligned=True,
        )
        assert result.blocked is False
        assert result.decision == "allowed_wrong_zone_continuation"

    def test_displacement_only_without_htf_still_blocks(self):
        """Continuation bypass requires HTF alignment — disp alone is not enough."""
        result = evaluate_zone_gate(
            direction="long",
            confidence=0.70,
            actual_rr=2.5,
            retrace=0.70,
            zone_str="premium",
            d1_bias="bullish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="EURUSD",
            has_sweep=False,
            has_displacement=True,
            htf_aligned=False,
        )
        assert result.blocked is True
        assert "wrong_zone" in result.decision


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

    def test_accepts_amd_cycle_state_object(self):
        """Live path overwrites amd_cycle with AMDCycleState; gate must not crash."""
        from trading_bot.analysis.amd_cycle import AMDCycleState, AMDPhase

        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.72,
            actual_rr=2.01,
            analysis_results={
                "amd_cycle": AMDCycleState(
                    phase=AMDPhase.DISTRIBUTION,
                    accumulation_high=None,
                    accumulation_low=None,
                    manipulation_extreme=None,
                    manipulation_direction=None,
                    expected_direction="bearish",
                    phase_start_time=None,
                    confidence=0.3,
                )
            },
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
    def test_blocks_weak_hour_below_70(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.69,
        )
        assert blocked is True

    def test_allows_weak_hour_at_70(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.70,
        )
        assert blocked is False


class TestVolatileRegime:
    def test_blocks_below_70_in_volatile_ranging(self):
        blocked, _ = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.69,
        )
        assert blocked is True

    def test_allows_70_in_volatile_ranging(self):
        blocked, _ = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.70,
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
