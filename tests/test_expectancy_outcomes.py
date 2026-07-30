"""Phase 5: hierarchical expectancy, calibration, exit-policy comparison."""

from types import SimpleNamespace


class TestHierarchicalExpectancy:
    def test_pools_to_parent_when_sparse(self):
        from trading_bot.analysis.hierarchical_expectancy import (
            compute_hierarchical_expectancy,
        )

        trades = [
            # Narrow segment sparse (1 trade) — should pool to setup family
            {
                "setup_fingerprint": "continuation|long|market|london|trending|disp+htf",
                "family": "continuation",
                "symbol": "EURUSD",
                "session": "london",
                "regime": "trending",
                "direction": "long",
                "r_multiple": 1.5,
                "net_r": 1.4,
            },
            # Sibling under same family
            {
                "setup_fingerprint": "continuation|long|market|new_york|trending|disp+htf",
                "family": "continuation",
                "symbol": "GBPUSD",
                "session": "new_york",
                "regime": "trending",
                "direction": "long",
                "r_multiple": -1.0,
                "net_r": -1.1,
            },
            {
                "setup_fingerprint": "continuation|long|market|london|trending|disp",
                "family": "continuation",
                "symbol": "EURUSD",
                "session": "london",
                "regime": "trending",
                "direction": "long",
                "r_multiple": 2.0,
                "net_r": 1.8,
            },
        ]
        report = compute_hierarchical_expectancy(trades, min_sample=3)
        assert report["levels"]
        # Family level should have n>=3 and non-null expectancy
        family = next(r for r in report["levels"] if r["level"] == "family")
        assert family["n"] >= 3
        assert family["expectancy_net_r"] is not None
        # Leaf may be sparse with pooled fallback flagged
        leaves = [r for r in report["levels"] if r["level"] == "leaf"]
        assert any(r.get("pooled") for r in leaves) or all(
            r["n"] < 3 for r in leaves if not r.get("pooled")
        )

    def test_does_not_hard_block_from_ten_trade_threshold(self):
        from trading_bot.analysis.hierarchical_expectancy import segment_decision

        dec = segment_decision(
            n=8, expectancy_net_r=-0.2, min_sample_hard_block=10
        )
        assert dec["action"] == "advisory"
        assert dec["hard_block"] is False


class TestCalibrationMetrics:
    def test_perfect_calibration_low_brier(self):
        from trading_bot.analysis.calibration_metrics import (
            compute_calibration_report,
        )

        # 10 trades at 70% conf with ~70% wins
        samples = [{"confidence": 0.70, "won": i < 7} for i in range(10)]
        # 10 at 90% with 9 wins
        samples += [{"confidence": 0.90, "won": i < 9} for i in range(10)]
        report = compute_calibration_report(samples, min_bin=3)
        assert report["brier"] < 0.25
        assert report["n"] == 20
        assert "bins" in report
        assert report["monotonic"] is True

    def test_sparse_advisory(self):
        from trading_bot.analysis.calibration_metrics import (
            compute_calibration_report,
        )

        report = compute_calibration_report(
            [{"confidence": 0.8, "won": True}], min_bin=5
        )
        assert report["advisory"] is True


class TestExitPolicyCompare:
    def test_compares_variants_using_mfe_mae(self):
        from trading_bot.execution.exit_policy_compare import compare_exit_policies

        trades = [
            {
                "direction": "long",
                "entry": 100.0,
                "sl": 99.0,
                "tp": 103.0,
                "mfe_r": 2.5,
                "mae_r": 0.4,
                "realized_r": 1.0,
                "cost_r": 0.1,
            },
            {
                "direction": "long",
                "entry": 100.0,
                "sl": 99.0,
                "tp": 103.0,
                "mfe_r": 0.8,
                "mae_r": 1.0,
                "realized_r": -1.0,
                "cost_r": 0.1,
            },
        ]
        result = compare_exit_policies(trades)
        assert "baseline_ladder" in result["policies"]
        assert "full_target" in result["policies"]
        assert "partial_be" in result["policies"]
        for name, stats in result["policies"].items():
            assert "expectancy_net_r" in stats
            assert stats["n"] == 2
