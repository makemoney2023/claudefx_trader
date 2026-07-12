"""Tests for walk-forward optimizer guardrails."""

from trading_bot.backtesting.optimizer import evaluate_optimizer_guardrails


class TestOptimizerGuardrails:
    def test_recommend_apply_when_clean(self):
        ok, warnings = evaluate_optimizer_guardrails(
            out_of_sample_trades=20,
            out_of_sample_sharpe=1.2,
            parameter_stability=0.85,
            fold_flags=[],
            grid_combinations=432,
        )
        assert ok is True
        assert all("advisory" in w for w in warnings)

    def test_blocks_low_oos_count(self):
        ok, warnings = evaluate_optimizer_guardrails(
            out_of_sample_trades=5,
            out_of_sample_sharpe=1.0,
            parameter_stability=0.9,
            fold_flags=[],
            grid_combinations=100,
        )
        assert ok is False
        assert any("OOS trade count" in w for w in warnings)

    def test_blocks_degraded_fold(self):
        ok, warnings = evaluate_optimizer_guardrails(
            out_of_sample_trades=20,
            out_of_sample_sharpe=0.5,
            parameter_stability=0.9,
            fold_flags=["[OOS_DEGRADED]"],
            grid_combinations=432,
        )
        assert ok is False
