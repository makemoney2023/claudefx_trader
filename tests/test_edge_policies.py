"""Tests for data-driven edge policies (agreement sizing, playbook gate,
exit tuning, slippage measurement, gate tuning recommendations)."""

import pytest

from trading_bot.services.edge_policies import (
    build_gate_tuning_recommendations,
    compute_slippage,
    evaluate_playbook_gate,
    exit_trigger_overrides_from_excursion,
    mech_agreement_size_multiplier,
)


class TestMechAgreementSizing:
    def test_agreement_sizes_up(self):
        mech = {"direction": "long", "confidence": 0.7}
        decision = mech_agreement_size_multiplier(mech, "long")
        assert decision.multiplier > 1.0
        assert decision.label == "agree"

    def test_disagreement_cuts_size(self):
        mech = {"direction": "short", "confidence": 0.7}
        decision = mech_agreement_size_multiplier(mech, "long")
        assert decision.multiplier < 1.0
        assert decision.label == "disagree"

    def test_no_mechanical_baseline_is_neutral(self):
        decision = mech_agreement_size_multiplier(None, "long")
        assert decision.multiplier == 1.0
        assert decision.label == "no_baseline"

    def test_mech_no_trade_is_neutral(self):
        mech = {"direction": "no_trade", "confidence": 0.0}
        decision = mech_agreement_size_multiplier(mech, "long")
        assert decision.multiplier == 1.0


class TestPlaybookGate:
    def _rows(self):
        return [
            {"symbol": "EURUSD", "direction": "long", "session": "london",
             "setup": "intraday", "sample": 15, "win_rate": 0.20, "avg_r": -0.8},
            {"symbol": "EURUSD", "direction": "short", "session": "london",
             "setup": "intraday", "sample": 12, "win_rate": 0.67, "avg_r": 1.2},
            {"symbol": "XAUUSD", "direction": "long", "session": "asian",
             "setup": "scalp", "sample": 4, "win_rate": 0.0, "avg_r": -1.0},
        ]

    def test_blocks_proven_negative_combo(self):
        result = evaluate_playbook_gate(
            self._rows(), "EURUSD", "long", "london", trade_type="intraday"
        )
        assert result.blocked is True
        assert "20%" in result.reason

    def test_allows_winning_combo(self):
        result = evaluate_playbook_gate(
            self._rows(), "EURUSD", "short", "london", trade_type="intraday"
        )
        assert result.blocked is False

    def test_insufficient_sample_fails_open(self):
        result = evaluate_playbook_gate(
            self._rows(), "XAUUSD", "long", "asian", trade_type="scalp"
        )
        assert result.blocked is False

    def test_unknown_combo_fails_open(self):
        result = evaluate_playbook_gate(
            self._rows(), "GBPUSD", "long", "new_york", trade_type="intraday"
        )
        assert result.blocked is False

    def test_empty_stats_fails_open(self):
        result = evaluate_playbook_gate([], "EURUSD", "long", "london")
        assert result.blocked is False


class TestExitTriggerOverrides:
    def test_high_mfe_extends_triggers(self):
        overrides = exit_trigger_overrides_from_excursion(3.0, sample_size=20)
        assert overrides is not None
        assert overrides["tp1_r"] == pytest.approx(1.2)   # 0.4 * 3.0
        assert overrides["tp2_r"] == pytest.approx(2.4)   # 0.8 * 3.0

    def test_low_mfe_clamped_to_floor(self):
        overrides = exit_trigger_overrides_from_excursion(1.0, sample_size=20)
        assert overrides["tp1_r"] == pytest.approx(0.8)   # floor
        assert overrides["tp2_r"] == pytest.approx(1.6)   # floor

    def test_extreme_mfe_clamped_to_ceiling(self):
        overrides = exit_trigger_overrides_from_excursion(6.0, sample_size=20)
        assert overrides["tp1_r"] == pytest.approx(1.5)   # ceiling
        assert overrides["tp2_r"] == pytest.approx(3.0)   # ceiling

    def test_insufficient_sample_returns_none(self):
        assert exit_trigger_overrides_from_excursion(3.0, sample_size=5) is None

    def test_zero_mfe_returns_none(self):
        assert exit_trigger_overrides_from_excursion(0.0, sample_size=20) is None

    def test_tp2_always_above_tp1(self):
        for mfe in (0.5, 1.5, 2.5, 4.0, 8.0):
            o = exit_trigger_overrides_from_excursion(mfe, sample_size=20)
            if o:
                assert o["tp2_r"] >= o["tp1_r"] + 0.5


class TestSlippage:
    def test_long_adverse_fill_positive(self):
        # Buying: filled higher than requested = adverse
        assert compute_slippage("long", 1.0850, 1.0853) == pytest.approx(0.0003)

    def test_long_favorable_fill_negative(self):
        assert compute_slippage("long", 1.0850, 1.0848) == pytest.approx(-0.0002)

    def test_short_adverse_fill_positive(self):
        # Selling: filled lower than requested = adverse
        assert compute_slippage("short", 1.0850, 1.0847) == pytest.approx(0.0003)

    def test_missing_prices_zero(self):
        assert compute_slippage("long", 0.0, 1.0850) == 0.0
        assert compute_slippage("long", 1.0850, None) == 0.0


class TestGateTuningRecommendations:
    def test_high_false_rejection_flags_ease(self):
        gate_expectancy = {
            "zone_block": {"count": 25, "false_rejection_rate": 0.60, "avg_hypothetical_r": 1.1},
        }
        recs = build_gate_tuning_recommendations(gate_expectancy)
        assert recs["zone_block"]["action"] == "ease"

    def test_low_false_rejection_flags_keep(self):
        gate_expectancy = {
            "min_confidence": {"count": 40, "false_rejection_rate": 0.10, "avg_hypothetical_r": -0.5},
        }
        recs = build_gate_tuning_recommendations(gate_expectancy)
        assert recs["min_confidence"]["action"] == "keep"

    def test_small_sample_flags_insufficient(self):
        gate_expectancy = {
            "flip_guard": {"count": 3, "false_rejection_rate": 1.0, "avg_hypothetical_r": 2.0},
        }
        recs = build_gate_tuning_recommendations(gate_expectancy)
        assert recs["flip_guard"]["action"] == "insufficient_data"

    def test_unresolved_rate_flags_insufficient(self):
        gate_expectancy = {
            "spread_block": {"count": 30, "false_rejection_rate": None, "avg_hypothetical_r": None},
        }
        recs = build_gate_tuning_recommendations(gate_expectancy)
        assert recs["spread_block"]["action"] == "insufficient_data"
