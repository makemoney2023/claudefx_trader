"""
Regression tests for algorithmic gate gaps:
1. ICT fingerprint zone_valid computed from PD retrace
2. has_displacement recognizes directional impulse, not only distribution_confirmed
3. TOD / volatile-regime require elevated confidence (above execution floor)
4. HTF-aligned M15 pullback limits are allowed with a soft cap (not hard-blocked)
"""

from types import SimpleNamespace

import pytest

from trading_bot.services.entry_gates import (
    evaluate_m15_gate,
    evaluate_tod_gate,
    evaluate_volatile_regime_gate,
)
from trading_bot.services.gate_pipeline import _evaluate_ict_confirmation_for_ctx
from trading_bot.services.setup_fingerprint import has_displacement
from trading_bot.services.trade_context import TradeContext


def _pd(retrace: float, zone: str = "discount"):
    return SimpleNamespace(
        retracement_percent=retrace,
        current_zone=SimpleNamespace(value=zone),
    )


class TestZoneValidWiredIntoIctFingerprint:
    def test_short_in_discount_marks_zone_invalid(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "ict_confirmation_mode", "active")

        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.70,
            actual_rr=2.2,
            order_type="sell_limit",
            d1_bias="bearish",
            h4_bias="bearish",
            pd_analysis=_pd(0.33, "discount"),
            analysis_results={
                "displacement": {
                    "distribution_confirmed": True,
                    "distribution_direction": "bearish",
                    "last_bearish": {"index": 1},
                    "recent_displacements": [{"direction": "bearish"}],
                },
                "market_structure": {
                    "structure_breaks": [{"type": "choch_bearish"}]
                },
                "liquidity": {},
            },
        )
        out = _evaluate_ict_confirmation_for_ctx(ctx)
        fp = ctx.analysis_results["setup_fingerprint"]
        assert fp["zone_valid"] is False
        assert out.blocked is True
        assert "valid_zone" in (out.reason or "").lower() or "zone" in (out.reason or "").lower()

    def test_short_in_premium_marks_zone_valid(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "ict_confirmation_mode", "active")
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.70,
            actual_rr=2.2,
            order_type="sell_limit",
            d1_bias="bearish",
            h4_bias="bearish",
            pd_analysis=_pd(0.72, "premium"),
            analysis_results={
                "displacement": {
                    "distribution_confirmed": True,
                    "distribution_direction": "bearish",
                    "last_bearish": {"index": 1},
                    "recent_displacements": [{"direction": "bearish"}],
                },
                "market_structure": {
                    "structure_breaks": [{"type": "choch_bearish"}]
                },
                "liquidity": {},
            },
        )
        _evaluate_ict_confirmation_for_ctx(ctx)
        fp = ctx.analysis_results["setup_fingerprint"]
        assert fp["zone_valid"] is True


class TestDisplacementBroadened:
    def test_last_bearish_counts_for_short(self):
        ar = {
            "displacement": {
                "distribution_confirmed": False,
                "last_bearish": {"index": 10, "direction": "bearish"},
                "last_bullish": None,
                "recent_displacements": [{"direction": "bearish"}],
            }
        }
        assert has_displacement(ar, direction="short") is True

    def test_last_bullish_does_not_count_for_short(self):
        ar = {
            "displacement": {
                "distribution_confirmed": False,
                "last_bearish": None,
                "last_bullish": {"index": 10, "direction": "bullish"},
                "recent_displacements": [{"direction": "bullish"}],
            }
        }
        assert has_displacement(ar, direction="short") is False

    def test_distribution_confirmed_still_counts(self):
        ar = {"displacement": {"distribution_confirmed": True, "recent_displacements": []}}
        assert has_displacement(ar, direction="long") is True


class TestElevatedTodAndRegimeFloors:
    def test_tod_blocks_60_in_weak_hour(self):
        blocked, reason = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.60,
        )
        assert blocked is True
        assert "70%" in reason or "0.70" in reason or "70" in reason

    def test_tod_allows_70_in_weak_hour(self):
        blocked, _ = evaluate_tod_gate(
            utc_hour=12,
            weak_hours=(12, 13),
            confidence=0.70,
        )
        assert blocked is False

    def test_volatile_blocks_60(self):
        blocked, reason = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.60,
        )
        assert blocked is True
        assert "70" in reason

    def test_volatile_allows_70(self):
        blocked, _ = evaluate_volatile_regime_gate(
            regime_type="volatile_ranging",
            confidence=0.70,
        )
        assert blocked is False


class TestM15PullbackAllowed:
    def test_htf_aligned_limit_pullback_passes_with_soft_cap(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.72,
            actual_rr=2.5,
            order_type="buy_limit",
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bearish",
            amd_phase="distribution",
        )
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is False
        assert outcome.confidence_cap == pytest.approx(0.68)

    def test_pullback_below_quality_floor_still_blocks(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.62,
            actual_rr=2.5,
            order_type="buy_limit",
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bearish",
        )
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id in ("m15_pullback_cap", "m15_pullback_quality")
