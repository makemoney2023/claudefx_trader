"""Tests for execution coordinator."""

import pytest
from types import SimpleNamespace

from trading_bot.execution.trade_execution import (
    ExecutionCoordinator,
    auto_convert_to_pending,
    check_position_conflicts,
    fix_limit_stop_labels,
    validate_limit_zone,
)


class TestPositionConflicts:
    def test_blocks_opposite_direction(self):
        positions = [SimpleNamespace(ticket=1, direction="short")]
        outcome = check_position_conflicts(positions, "long")
        assert outcome.blocked is True
        assert outcome.gate_id == "position_conflict"

    def test_blocks_stacking(self):
        positions = [SimpleNamespace(ticket=1, direction="long")]
        outcome = check_position_conflicts(positions, "long")
        assert outcome.blocked is True
        assert outcome.gate_id == "position_stacking"


class TestOrderNormalization:
    def test_market_converts_to_buy_limit(self):
        ot = auto_convert_to_pending("market", "long", 1.0800, 1.0850)
        assert ot == "buy_limit"

    def test_fix_buy_limit_above_market(self):
        assert fix_limit_stop_labels("buy_limit", 1.0900, 1.0850) == "buy_stop"


class TestLimitZone:
    def test_blocks_buy_limit_in_premium(self):
        outcome = validate_limit_zone("buy_limit", 0.75)
        assert outcome.blocked is True


class TestExecutionCoordinator:
    def test_prepare_order_passes_clean(self):
        sig = SimpleNamespace(
            direction="long",
            entry_price=1.0850,
            order_type="market",
            confidence=0.75,
            stop_loss=1.0800,
            take_profit=1.0950,
        )
        prep = ExecutionCoordinator().prepare_order(
            trade_signal=sig,
            current_price=1.0850,
            existing_positions=[],
            analysis_results={},
        )
        assert prep.blocked is False
