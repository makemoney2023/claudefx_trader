"""Tests for execution coordinator."""

import pytest
from types import SimpleNamespace

from trading_bot.execution.trade_execution import (
    ExecutionCoordinator,
    adjust_sl_for_spread,
    auto_convert_to_pending,
    check_position_conflicts,
    evaluate_tick_refine,
    fix_limit_stop_labels,
    pending_expiration_minutes,
    validate_limit_zone,
)


class TestSpreadBuffer:
    def test_long_sl_moves_down(self):
        assert adjust_sl_for_spread(1.0800, "long", 0.00020) == pytest.approx(1.07990)

    def test_short_sl_moves_up(self):
        assert adjust_sl_for_spread(1.0900, "short", 0.00020) == pytest.approx(1.09010)


class TestTickRefine:
    def test_blocks_when_rr_degrades(self):
        result = evaluate_tick_refine(
            direction="long",
            entry_price=1.0850,
            current_price=1.0850,
            tick_bid=1.0860,
            tick_ask=1.0862,
            final_sl=1.0840,
            final_tp=1.0865,
            atr_14=0.0010,
        )
        assert result.allowed is False

    def test_allows_when_rr_still_ok(self):
        result = evaluate_tick_refine(
            direction="long",
            entry_price=1.0850,
            current_price=1.0850,
            tick_bid=1.0851,
            tick_ask=1.0853,
            final_sl=1.0800,
            final_tp=1.0950,
            atr_14=0.0100,
        )
        assert result.allowed is True


class TestPendingExpiration:
    def test_crypto_is_8_hours(self):
        assert pending_expiration_minutes(is_crypto=True, session_remaining=60) == 480

    def test_forex_clamped(self):
        assert pending_expiration_minutes(is_crypto=False, session_remaining=30) == 60
        assert pending_expiration_minutes(is_crypto=False, session_remaining=600) == 480


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

    def test_allows_sell_limit_when_retrace_in_premium(self):
        outcome = validate_limit_zone("sell_limit", 0.72)
        assert outcome.blocked is False


class TestEntryRetrace:
    """Limit-zone must use ENTRY retrace, not spot — anticipatory ICT limits."""

    def test_entry_retrace_from_swings(self):
        from trading_bot.execution.trade_execution import entry_retrace_pct

        # Range 4028–4120; entry 4110 ≈ 89% (premium) while spot can sit at 11%.
        analysis = {
            "premium_discount": {
                "swing_high": 4120.21,
                "swing_low": 4028.30,
                "retracement_percent": 0.11,
                "current_zone": "extreme_discount",
            }
        }
        retrace = entry_retrace_pct(analysis, entry_price=4110.0)
        assert retrace == pytest.approx((4110.0 - 4028.30) / (4120.21 - 4028.30), rel=1e-4)
        assert retrace > 0.70

    def test_prepare_allows_premium_sell_limit_while_spot_in_discount(self):
        """Prod failure: spot 11% blocked sell_limit even when entry was premium FVG."""
        sig = SimpleNamespace(
            direction="short",
            entry_price=4110.0,
            order_type="sell_limit",
            confidence=0.72,
            stop_loss=4120.0,
            take_profit=4065.0,
        )
        analysis = {
            "premium_discount": {
                "swing_high": 4120.21,
                "swing_low": 4028.30,
                "retracement_percent": 0.11,
                "current_zone": "extreme_discount",
            }
        }
        prep = ExecutionCoordinator().prepare_order(
            trade_signal=sig,
            current_price=4085.0,
            existing_positions=[],
            analysis_results=analysis,
        )
        assert prep.blocked is False
        assert prep.order_type == "sell_limit"

    def test_prepare_still_blocks_discount_sell_limit_entry(self):
        sig = SimpleNamespace(
            direction="short",
            entry_price=4035.0,  # still in discount of the range
            order_type="sell_limit",
            confidence=0.72,
            stop_loss=4050.0,
            take_profit=4000.0,
        )
        analysis = {
            "premium_discount": {
                "swing_high": 4120.21,
                "swing_low": 4028.30,
                "retracement_percent": 0.11,
                "current_zone": "extreme_discount",
            }
        }
        prep = ExecutionCoordinator().prepare_order(
            trade_signal=sig,
            current_price=4032.0,
            existing_positions=[],
            analysis_results=analysis,
        )
        assert prep.blocked is True
        assert prep.gate_id == "zone_block"
        assert "discount" in prep.reason.lower()


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
