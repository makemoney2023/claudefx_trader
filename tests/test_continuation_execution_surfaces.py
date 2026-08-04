"""HTF+displacement continuation must clear conversion / ICT / pre-judge / disp parity."""

from types import SimpleNamespace

import pytest

from trading_bot.services.entry_gates import evaluate_ict_confirmation_gate
from trading_bot.services.opportunity_scanner import pre_judge_zone_block_reason
from trading_bot.services.parity_gates import (
    evaluate_displacement_parity,
    evaluate_zone_conversion,
)
from trading_bot.services.setup_fingerprint import (
    SetupFingerprint,
    is_htf_displacement_continuation,
)


class TestContinuationPredicate:
    def test_requires_both(self):
        assert is_htf_displacement_continuation(
            htf_aligned=True, has_displacement=True
        )
        assert not is_htf_displacement_continuation(
            htf_aligned=True, has_displacement=False
        )
        assert not is_htf_displacement_continuation(
            htf_aligned=False, has_displacement=True
        )


class TestZoneConversionContinuation:
    def test_htf_disp_keeps_market_when_zone_invalid(self):
        pd = SimpleNamespace(swing_high=4100.0, swing_low=4000.0)
        out = evaluate_zone_conversion(
            zone_valid=False,
            zone_reason="Short entry in DISCOUNT zone (46%)",
            order_type="market",
            direction="short",
            current_entry=4046.0,
            current_price=4046.0,
            pd_analysis=pd,
            htf_aligned=True,
            has_displacement=True,
        )
        assert out.blocked is False
        assert out.action == "unchanged"
        assert "continuation" in "".join(out.gate_path)

    def test_without_continuation_still_converts(self):
        pd = SimpleNamespace(swing_high=4100.0, swing_low=4000.0)
        out = evaluate_zone_conversion(
            zone_valid=False,
            zone_reason="Short entry in DISCOUNT zone (46%)",
            order_type="market",
            direction="short",
            current_entry=4046.0,
            current_price=4046.0,
            pd_analysis=pd,
            htf_aligned=False,
            has_displacement=True,
        )
        assert out.action == "convert_pending"


class TestDisplacementParityContinuation:
    def test_htf_disp_allows_market_without_distribution(self):
        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
            htf_aligned=True,
            has_displacement=True,
        )
        assert out.blocked is False
        assert out.action == "allow_market"

    def test_without_continuation_still_rejects(self):
        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
            htf_aligned=False,
            has_displacement=False,
        )
        assert out.blocked is True


class TestIctConfirmationContinuation:
    def test_continuation_allows_without_mss_when_htf_disp(self):
        fp = SetupFingerprint(
            family="continuation",
            tags=("htf", "disp"),
            key="continuation|short|market|asian|trending",
            has_sweep=False,
            has_mss=False,
            has_displacement=True,
            htf_aligned=True,
            zone_valid=False,
            direction="short",
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp, order_type="market", mode="active"
        )
        assert out.blocked is False
        assert "continuation" in out.decision

    def test_passive_skips_zone_when_htf_disp(self):
        fp = SetupFingerprint(
            family="passive_retracement",
            tags=("htf", "disp"),
            key="passive_retracement|short|sell_limit|asian|trending",
            has_sweep=False,
            has_mss=False,
            has_displacement=True,
            htf_aligned=True,
            zone_valid=False,
            direction="short",
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp, order_type="sell_limit", mode="active"
        )
        assert out.blocked is False

    def test_continuation_still_needs_disp(self):
        fp = SetupFingerprint(
            family="continuation",
            tags=("htf",),
            key="continuation|short|market|asian|trending",
            has_sweep=False,
            has_mss=False,
            has_displacement=False,
            htf_aligned=True,
            zone_valid=True,
            direction="short",
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp, order_type="market", mode="active"
        )
        assert out.blocked is True


class TestPreJudgeContinuation:
    def test_market_short_deep_discount_allowed_on_continuation(self):
        reason = pre_judge_zone_block_reason(
            order_type="market",
            direction="short",
            retrace_pct=0.30,
            htf_aligned=True,
            has_displacement=True,
        )
        assert reason is None

    def test_market_short_deep_discount_blocked_without_continuation(self):
        reason = pre_judge_zone_block_reason(
            order_type="market",
            direction="short",
            retrace_pct=0.30,
            htf_aligned=False,
            has_displacement=True,
        )
        assert reason is not None
        assert "discount" in reason.lower() or "zone" in reason.lower()

    def test_extreme_sell_limit_still_blocked(self):
        reason = pre_judge_zone_block_reason(
            order_type="sell_limit",
            direction="short",
            retrace_pct=0.20,
            htf_aligned=True,
            has_displacement=True,
        )
        assert reason is not None
