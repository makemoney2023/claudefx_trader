"""TDD: metals displacement continuation — wakeup, pre-Claude bypass, entry plan."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trading_bot.services.analysis_cooldown import (
    DISPLACEMENT_WAKEUP_MIN_ELAPSED,
    has_recent_m5_displacement,
    last_closed_bar_index,
    recent_m5_displacement_direction,
    should_check_metal_displacement_wakeup,
    should_run_analysis,
)
from trading_bot.services.entry_gates import evaluate_m15_gate
from trading_bot.services.trade_context import TradeContext
from trading_bot.utils.win_optimization import (
    plan_displacement_continuation_entry,
    pre_claude_viability,
    repair_displacement_limit_levels,
)


class TestRecentM5DisplacementDirection:
    def test_returns_bearish_for_recent_strong_disp(self):
        analysis = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(
                    index=19,
                    atr_multiple=1.8,
                    direction="bearish",
                    is_strong=True,
                ),
            ]
        )
        assert (
            recent_m5_displacement_direction(analysis, last_bar_index=20)
            == "bearish"
        )

    def test_returns_none_when_stale(self):
        analysis = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(
                    index=10,
                    atr_multiple=2.0,
                    direction="bearish",
                    is_strong=True,
                ),
            ]
        )
        assert (
            recent_m5_displacement_direction(analysis, last_bar_index=20)
            is None
        )

    def test_prefers_most_recent_qualifying(self):
        analysis = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(
                    index=17,
                    atr_multiple=1.6,
                    direction="bullish",
                    is_strong=True,
                ),
                SimpleNamespace(
                    index=19,
                    atr_multiple=1.7,
                    direction="bearish",
                    is_strong=True,
                ),
            ]
        )
        assert (
            recent_m5_displacement_direction(analysis, last_bar_index=20)
            == "bearish"
        )


class TestMetalWakeupOutsideKillZone:
    def test_metals_check_wakeup_while_on_cooldown(self):
        assert should_check_metal_displacement_wakeup("XAUUSD", on_cooldown=True)
        assert should_check_metal_displacement_wakeup("XAGUSD", on_cooldown=True)

    def test_forex_never_checks_metal_wakeup(self):
        assert (
            should_check_metal_displacement_wakeup("EURUSD", on_cooldown=True)
            is False
        )

    def test_not_on_cooldown_skips_check(self):
        assert (
            should_check_metal_displacement_wakeup("XAUUSD", on_cooldown=False)
            is False
        )

    def test_wakeup_still_fires_analysis_inside_default_cooldown(self):
        now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=DISPLACEMENT_WAKEUP_MIN_ELAPSED)
        run, reason = should_run_analysis(
            last_run=last,
            now=now,
            cooldown_seconds=180,
            displacement_wakeup=True,
        )
        assert run is True
        assert reason == "m5_displacement_wakeup"


class TestPreClaudeDisplacementBypass:
    """Any-session dump pattern: HTF short OK, M15 still bullish — disp unlocks short."""

    def test_bearish_disp_unlocks_short_when_m15_bullish(self):
        result = pre_claude_viability(
            d1_bias="bearish",
            h4_bias="bearish",
            m15_bias="bullish",
            amd_phase="accumulation",
            displacement_direction="bearish",
        )
        assert result.proceed is True

    def test_bullish_disp_does_not_unlock_short(self):
        result = pre_claude_viability(
            d1_bias="bearish",
            h4_bias="bearish",
            m15_bias="bullish",
            amd_phase="accumulation",
            displacement_direction="bullish",
        )
        assert result.proceed is False

    def test_htf_dual_oppose_still_blocks_disp_side(self):
        # Bearish disp cannot force a short when D1+H4 are both bullish.
        result = pre_claude_viability(
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bearish",
            amd_phase="accumulation",
            displacement_direction="bearish",
        )
        assert result.proceed is False

    def test_no_disp_keeps_today_skip(self):
        result = pre_claude_viability(
            d1_bias="bearish",
            h4_bias="bearish",
            m15_bias="bullish",
            amd_phase="accumulation",
        )
        assert result.proceed is False


class TestDisplacementContinuationEntryPlan:
    def test_prefers_limit_when_origin_zone_available(self):
        plan = plan_displacement_continuation_entry(
            "short",
            current_price=4040.0,
            atr=10.0,
            origin_price=4067.0,
            has_origin_zone=True,
        )
        assert plan.action == "limit"
        assert plan.order_type == "sell_limit"
        assert plan.entry_price == pytest.approx(4067.0)
        assert plan.setup_tag == "displacement_continuation"

    def test_allows_market_when_still_expanding_within_1atr(self):
        plan = plan_displacement_continuation_entry(
            "short",
            current_price=4060.0,
            atr=10.0,
            origin_price=4067.0,
            has_origin_zone=False,
        )
        assert plan.action == "market"
        assert plan.order_type == "market"

    def test_skips_when_excursion_beyond_1_5atr(self):
        plan = plan_displacement_continuation_entry(
            "short",
            current_price=4040.0,
            atr=10.0,
            origin_price=4067.0,
            has_origin_zone=False,
        )
        assert plan.action == "skip"
        assert "1.5" in plan.reason or "chase" in plan.reason.lower()

    def test_long_limit_uses_buy_limit(self):
        plan = plan_displacement_continuation_entry(
            "long",
            current_price=4075.0,
            atr=10.0,
            origin_price=4067.0,
            has_origin_zone=True,
        )
        assert plan.action == "limit"
        assert plan.order_type == "buy_limit"
        assert plan.entry_price == pytest.approx(4067.0)


class TestHasRecentStillWorks:
    def test_strong_flag_counts(self):
        analysis = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(index=20, atr_multiple=1.2, is_strong=True),
            ]
        )
        assert has_recent_m5_displacement(analysis, last_bar_index=20)


class TestLastClosedBarIndex:
    def test_matches_exclude_forming_candle(self):
        # Raw MT5 frame includes forming candle; detector indexes on stripped frame.
        assert last_closed_bar_index(40) == 38
        assert last_closed_bar_index(2) == 0
        assert last_closed_bar_index(1) == 0


class TestM15GateDisplacementBypass:
    def test_fresh_disp_allows_market_vs_opposing_m15(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.70,
            actual_rr=2.0,
            order_type="market",
            d1_bias="bearish",
            h4_bias="bearish",
            m15_bias="bullish",
            amd_phase="accumulation",
            analysis_results={"fresh_displacement_direction": "bearish"},
        )
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is False

    def test_wrong_disp_direction_still_blocks_market(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="short",
            confidence=0.70,
            actual_rr=2.0,
            order_type="market",
            d1_bias="bearish",
            h4_bias="bearish",
            m15_bias="bullish",
            amd_phase="accumulation",
            analysis_results={"fresh_displacement_direction": "bullish"},
        )
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is True


class TestRepairDisplacementLimitLevels:
    def test_short_limit_moves_sl_above_entry(self):
        entry, sl, tp = repair_displacement_limit_levels(
            "short",
            entry=4067.0,
            stop_loss=4050.0,
            take_profit=4020.0,
            displacement_extreme=4070.0,
        )
        assert entry == pytest.approx(4067.0)
        assert sl > entry
        assert sl >= 4070.0
        assert tp < entry

    def test_long_limit_moves_sl_below_entry(self):
        entry, sl, tp = repair_displacement_limit_levels(
            "long",
            entry=4067.0,
            stop_loss=4080.0,
            take_profit=4100.0,
            displacement_extreme=4060.0,
        )
        assert entry == pytest.approx(4067.0)
        assert sl < entry
        assert sl <= 4060.0
        assert tp > entry
