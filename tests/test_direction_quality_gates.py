"""
Direction-quality gates:
- ICT confirmation default is disabled (Claude signals not killed on family confirms);
  mode=active still blocks incomplete passive retracements
- Wrong-zone entries (short discount / long premium) hard-block unless
  (HTF-aligned + displacement) OR (sweep + displacement)
"""

import pytest

from trading_bot.services.entry_gates import (
    ZoneGateSettings,
    evaluate_ict_confirmation_gate,
    evaluate_zone_gate,
)
from trading_bot.services.setup_fingerprint import SetupFingerprint


class TestIctConfirmationActive:
    def test_active_blocks_passive_without_displacement(self):
        """XAU log case: passive_retracement sell_limit, no sweep/displacement."""
        fp = SetupFingerprint(
            family="passive_retracement",
            tags=("mss", "htf", "fvg", "ob", "zone"),
            key="passive_retracement|short|sell_limit|unknown|trending_strong",
            has_sweep=False,
            has_mss=True,
            has_displacement=False,
            htf_aligned=True,
            zone_valid=True,
            direction="short",
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="sell_limit",
            mode="active",
        )
        assert out.blocked is True
        assert out.gate_id == "ict_confirmation"
        assert "displacement" in out.reason.lower()

    def test_active_allows_passive_with_displacement(self):
        fp = SetupFingerprint(
            family="passive_retracement",
            tags=("htf", "zone", "disp"),
            key="passive_retracement|short|sell_limit|ny|trending",
            has_sweep=False,
            has_mss=True,
            has_displacement=True,
            htf_aligned=True,
            zone_valid=True,
            direction="short",
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="sell_limit",
            mode="active",
        )
        assert out.blocked is False
        assert out.would_block is False


class TestWrongZoneRequiresSweepDisplacement:
    def test_short_at_49pct_blocked_without_confirm(self):
        """Prior bug: 49% treated as equilibrium and passed at 60% conf."""
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=1.84,
            retrace=0.49,
            zone_str="discount",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=False,
            has_displacement=False,
        )
        assert result.blocked is True
        assert "wrong_zone" in result.decision

    def test_short_discount_blocked_even_with_high_rr(self):
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.70,
            actual_rr=2.5,
            retrace=0.36,
            zone_str="discount",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=False,
            has_displacement=False,
        )
        assert result.blocked is True

    def test_short_discount_allowed_with_sweep_and_displacement(self):
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=1.84,
            retrace=0.36,
            zone_str="discount",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=True,
            has_displacement=True,
        )
        assert result.blocked is False
        assert "confirmed" in result.decision

    def test_long_premium_blocked_without_confirm(self):
        """Displacement alone without HTF alignment still blocks."""
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

    def test_long_premium_allowed_with_both_confirms(self):
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

    def test_htf_aligned_short_discount_continuation(self):
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=1.84,
            retrace=0.36,
            zone_str="discount",
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

    def test_htf_aligned_long_premium_continuation(self):
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
            has_sweep=False,
            has_displacement=True,
            htf_aligned=True,
        )
        assert result.blocked is False
        assert result.decision == "allowed_wrong_zone_continuation"

    def test_zone_aligned_short_premium_still_allowed(self):
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=2.0,
            retrace=0.72,
            zone_str="premium",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=False,
            has_displacement=False,
        )
        assert result.blocked is False
        assert result.decision == "allowed_zone_aligned"


class TestIctConfirmationDisabled:
    def test_disabled_passes_passive_buy_limit_without_displacement(self):
        """NY KZ XAU case: Claude buy_limit LONG must not die on displacement_origin."""
        fp = SetupFingerprint(
            family="passive_retracement",
            tags=("htf", "mss", "zone"),
            key="passive_retracement|long|buy_limit|ny|ranging",
            has_sweep=False,
            has_mss=True,
            has_displacement=False,
            htf_aligned=True,
            zone_valid=True,
            direction="long",
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="buy_limit",
            mode="disabled",
        )
        assert out.blocked is False
        assert out.decision == "disabled"

    def test_pipeline_disabled_mode_does_not_block(self, monkeypatch):
        from trading_bot.config import settings
        from trading_bot.services.gate_pipeline import _evaluate_ict_confirmation_for_ctx
        from trading_bot.services.trade_context import TradeContext

        monkeypatch.setattr(settings.trading, "ict_confirmation_mode", "disabled")
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.62,
            actual_rr=2.04,
            d1_bias="bullish",
            h4_bias="bullish",
            order_type="buy_limit",
            analysis_results={},
        )
        out = _evaluate_ict_confirmation_for_ctx(ctx)
        assert out.blocked is False


class TestIctConfirmationDefault:
    def test_config_default_is_disabled(self):
        from trading_bot.config import TradingSettings

        # Field default (conftest forces env TRADING_ICT_CONFIRMATION_MODE=shadow
        # for test isolation, so don't instantiate Settings() here)
        assert TradingSettings.model_fields["ict_confirmation_mode"].default == "disabled"
