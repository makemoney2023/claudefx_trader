"""Phase 6: correlation group exposure + promotion evaluator."""

from types import SimpleNamespace


class TestGroupExposureSizing:
    def test_group_cap_uses_risk_dollars_not_hardcoded(self):
        from trading_bot.services.correlation_service import (
            CorrelationService,
            OpenPosition,
        )

        svc = CorrelationService()
        # EURUSD and GBPUSD are highly correlated in defaults
        svc._open_positions["EURUSD"] = OpenPosition(
            symbol="EURUSD", volume=0.10, direction="long"
        )
        # Risk dollars for open EURUSD: use entry/sl distance
        remaining_lots = svc.get_max_allowed_lots_by_risk(
            symbol="GBPUSD",
            account_equity=10_000.0,
            entry_price=1.1000,
            stop_loss=1.0950,
            max_group_risk_pct=0.02,  # 2% of equity = $200 group risk
            open_risk_by_symbol={"EURUSD": 150.0},  # $150 already at risk
        )
        # Remaining group risk $50; GBPUSD SL dist 50 pips, pip_value ~10 → 0.1 lot
        assert remaining_lots > 0
        assert remaining_lots <= 0.10 + 1e-9

    def test_shadow_group_multiplier_never_zero_when_pairwise_ok(self):
        from trading_bot.services.correlation_service import CorrelationService

        svc = CorrelationService()
        mult = svc.get_combined_size_multiplier(
            symbol="XAUUSD",
            pairwise_mult=1.0,
            group_mult=0.0,
            mode="shadow",
        )
        assert mult == 1.0  # shadow does not apply group cap

    def test_active_combines_min(self):
        from trading_bot.services.correlation_service import CorrelationService

        svc = CorrelationService()
        mult = svc.get_combined_size_multiplier(
            symbol="EURUSD",
            pairwise_mult=0.5,
            group_mult=0.25,
            mode="active",
        )
        assert mult == 0.25


class TestPromotionEvaluator:
    def test_rejects_parity_mismatch(self):
        from trading_bot.backtesting.promotion import evaluate_promotion

        result = evaluate_promotion(
            parity_mismatches=1,
            paper_trades=120,
            candidate_net_expectancy=0.3,
            baseline_net_expectancy=0.1,
            bootstrap_ci_low=0.05,
            candidate_profit_factor=1.4,
            baseline_profit_factor=1.3,
            candidate_max_dd=0.08,
            baseline_max_dd=0.10,
            fill_success_rate=0.95,
            baseline_fill_success_rate=0.94,
            symbol_concentration=0.4,
            unresolved_data_quality_gaps=0,
        )
        assert result.promoted is False
        assert "parity" in result.reasons[0].lower()

    def test_promotes_when_all_criteria_pass(self):
        from trading_bot.backtesting.promotion import evaluate_promotion

        result = evaluate_promotion(
            parity_mismatches=0,
            paper_trades=120,
            candidate_net_expectancy=0.35,
            baseline_net_expectancy=0.20,
            bootstrap_ci_low=0.05,  # positive paired improvement CI
            candidate_profit_factor=1.5,
            baseline_profit_factor=1.4,
            candidate_max_dd=0.09,
            baseline_max_dd=0.10,
            fill_success_rate=0.96,
            baseline_fill_success_rate=0.95,
            symbol_concentration=0.35,
            unresolved_data_quality_gaps=0,
        )
        assert result.promoted is True
        assert result.rollback_config_key

    def test_rejects_single_symbol_dependence(self):
        from trading_bot.backtesting.promotion import evaluate_promotion

        result = evaluate_promotion(
            parity_mismatches=0,
            paper_trades=150,
            candidate_net_expectancy=0.5,
            baseline_net_expectancy=0.2,
            bootstrap_ci_low=0.1,
            candidate_profit_factor=1.6,
            baseline_profit_factor=1.4,
            candidate_max_dd=0.08,
            baseline_max_dd=0.10,
            fill_success_rate=0.97,
            baseline_fill_success_rate=0.95,
            symbol_concentration=0.85,
            unresolved_data_quality_gaps=0,
        )
        assert result.promoted is False
        assert any("symbol" in r.lower() for r in result.reasons)
