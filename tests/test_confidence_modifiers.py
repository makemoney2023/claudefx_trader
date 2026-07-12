"""Tests for structured confidence modifiers."""

import pytest

from trading_bot.services.confidence_modifiers import (
    SecondaryModifierContext,
    apply_secondary_modifiers,
    confidence_decision_to_dict,
)


class TestSecondaryModifiers:
    def test_retail_contrarian_boost(self):
        decision = apply_secondary_modifiers(
            0.75,
            SecondaryModifierContext(
                direction="long",
                symbol="EURUSD",
                retail_contrarian="long",
            ),
        )
        assert decision.final == pytest.approx(0.80, abs=0.01)

    def test_secondary_boost_capped_at_10_percent(self):
        ctx = SecondaryModifierContext(
            direction="long",
            symbol="XAUUSD",
            vix_risk_mode="risk_off",
            intermarket={"risk_environment": "strong_risk_off"},
            seasonal={
                "current_month_bias": "bullish",
                "historical_accuracy": 80,
            },
        )
        decision = apply_secondary_modifiers(0.70, ctx)
        assert decision.final <= 0.80 + 0.001

    def test_to_dict_serializable(self):
        decision = apply_secondary_modifiers(
            0.70,
            SecondaryModifierContext(direction="short", symbol="EURUSD"),
        )
        d = confidence_decision_to_dict(decision)
        assert "base" in d and "final" in d
