"""Phase 2: cost-adjusted net R:R, spread state, fill validation."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class TestComputeNetRr:
    def test_net_rr_subtracts_spread_cost(self):
        from trading_bot.services.net_rr import compute_net_rr

        # Entry 100, SL 99, TP 103 → raw 3.0R; metal spread haircut 0.05
        result = compute_net_rr(
            entry=100.0, sl=99.0, tp=103.0, symbol="XAUUSD",
        )
        assert result.raw_rr == pytest.approx(3.0)
        assert result.net_rr == pytest.approx(2.95, abs=0.01)
        assert result.cost_r > 0

    def test_net_rr_uses_live_spread_when_provided(self):
        from trading_bot.services.net_rr import compute_net_rr

        # Live spread of 0.5 on SL dist 1.0 = 0.5R cost (plus category floor)
        result = compute_net_rr(
            entry=2000.0, sl=1990.0, tp=2030.0, symbol="XAUUSD",
            spread=5.0,  # 0.5R of the 10-point SL
        )
        assert result.raw_rr == pytest.approx(3.0)
        assert result.net_rr < result.raw_rr - 0.4

    def test_zero_sl_returns_zero(self):
        from trading_bot.services.net_rr import compute_net_rr

        result = compute_net_rr(entry=100.0, sl=100.0, tp=110.0, symbol="EURUSD")
        assert result.raw_rr == 0.0
        assert result.net_rr == 0.0


class TestEvaluateNetRrFloor:
    def test_blocks_when_net_below_floor(self):
        from trading_bot.services.net_rr import evaluate_net_rr_floor

        outcome = evaluate_net_rr_floor(
            net_rr=1.2, min_rr=2.0, is_aggressive=False
        )
        assert outcome is not None
        assert outcome.blocked is True
        assert outcome.gate_id == "net_rr_floor"

    def test_passes_when_net_above_min(self):
        from trading_bot.services.net_rr import evaluate_net_rr_floor

        outcome = evaluate_net_rr_floor(
            net_rr=2.5, min_rr=2.0, is_aggressive=False
        )
        assert outcome is None


class TestSpreadState:
    def test_normal_when_under_threshold(self):
        from trading_bot.services.spread_policy import evaluate_spread_state

        state = evaluate_spread_state("XAUUSD", spread=0.30)
        assert state.state == "normal"
        assert state.allows_trading is True

    def test_blocked_when_over_max(self):
        from trading_bot.services.spread_policy import evaluate_spread_state

        state = evaluate_spread_state("XAUUSD", spread=1.50)
        assert state.state == "blocked"
        assert state.allows_trading is False

    def test_elevated_near_threshold(self):
        from trading_bot.services.spread_policy import evaluate_spread_state

        # XAUUSD max 0.80; 70%+ = elevated
        state = evaluate_spread_state("XAUUSD", spread=0.60)
        assert state.state == "elevated"
        assert state.allows_trading is True

    def test_unavailable_fails_closed_in_live(self):
        from trading_bot.services.spread_policy import evaluate_spread_state

        state = evaluate_spread_state(
            "XAUUSD", spread=None, unavailable=True, live_mode=True
        )
        assert state.state == "unavailable"
        assert state.allows_trading is False

    def test_unavailable_fails_open_in_demo(self):
        from trading_bot.services.spread_policy import evaluate_spread_state

        state = evaluate_spread_state(
            "XAUUSD", spread=None, unavailable=True, live_mode=False
        )
        assert state.allows_trading is True


class TestFillProtectionValidation:
    def test_sl_tp_drift_blocks(self):
        from trading_bot.execution.fill_validation import validate_broker_protections

        result = validate_broker_protections(
            direction="long",
            intended_sl=1990.0,
            intended_tp=2030.0,
            broker_sl=1980.0,  # drifted
            broker_tp=2030.0,
            tolerance_price=0.5,
        )
        assert result.ok is False
        assert "sl" in result.reason.lower()

    def test_within_tolerance_passes(self):
        from trading_bot.execution.fill_validation import validate_broker_protections

        result = validate_broker_protections(
            direction="long",
            intended_sl=1990.0,
            intended_tp=2030.0,
            broker_sl=1990.2,
            broker_tp=2030.1,
            tolerance_price=0.5,
        )
        assert result.ok is True

    def test_partial_fill_rescales_risk(self):
        from trading_bot.execution.fill_validation import evaluate_partial_fill

        result = evaluate_partial_fill(
            requested_lots=1.0,
            filled_lots=0.4,
            min_fill_ratio=0.5,
        )
        assert result.accept is False
        assert result.gate_id == "partial_fill"

    def test_partial_fill_above_ratio_accepted(self):
        from trading_bot.execution.fill_validation import evaluate_partial_fill

        result = evaluate_partial_fill(
            requested_lots=1.0,
            filled_lots=0.7,
            min_fill_ratio=0.5,
        )
        assert result.accept is True
        assert result.filled_lots == 0.7
