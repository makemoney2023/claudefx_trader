"""Tests for OTE pullback entry conversion (zone/displacement gates).

Regression: zone-blocked longs were converted to buy limits at
swing_low + 0.786*range — the PREMIUM side of the range — instead of the
discount-side OTE. Longs must pull back into discount; shorts into premium.
"""

import pytest

from trading_bot.utils.win_optimization import (
    ote_pullback_entry,
    rebase_sl_tp_for_new_entry,
)


SWING_LOW = 1.0000
SWING_HIGH = 1.0100  # range = 0.0100


class TestOtePullbackEntry:
    def test_long_entry_is_in_discount_half(self):
        entry = ote_pullback_entry("long", SWING_HIGH, SWING_LOW)
        equilibrium = SWING_LOW + (SWING_HIGH - SWING_LOW) * 0.5
        assert 0 < entry < equilibrium

    def test_long_entry_is_79pct_pullback_from_high(self):
        entry = ote_pullback_entry("long", SWING_HIGH, SWING_LOW)
        assert entry == pytest.approx(SWING_HIGH - 0.0100 * 0.786)

    def test_short_entry_is_in_premium_half(self):
        entry = ote_pullback_entry("short", SWING_HIGH, SWING_LOW)
        equilibrium = SWING_LOW + (SWING_HIGH - SWING_LOW) * 0.5
        assert entry > equilibrium

    def test_short_entry_matches_legacy_62pct_level(self):
        # Shorts were already correct (swing_low + 0.618*range); preserve behavior.
        entry = ote_pullback_entry("short", SWING_HIGH, SWING_LOW)
        assert entry == pytest.approx(SWING_LOW + 0.0100 * 0.618)

    def test_degenerate_range_returns_zero(self):
        assert ote_pullback_entry("long", 1.0, 1.0) == 0.0
        assert ote_pullback_entry("short", 1.0, 1.5) == 0.0


class TestRebaseSlTp:
    def test_offsets_preserved_for_long(self):
        # Original: entry 1.0080, SL 1.0060 (-20 pips), TP 1.0130 (+50 pips)
        new_sl, new_tp = rebase_sl_tp_for_new_entry(
            stop_loss=1.0060, take_profit=1.0130,
            old_entry=1.0080, new_entry=1.0021,
        )
        assert new_sl == pytest.approx(1.0001)
        assert new_tp == pytest.approx(1.0071)

    def test_sl_side_preserved(self):
        # SL below entry for a long stays below the new entry after rebase.
        new_sl, new_tp = rebase_sl_tp_for_new_entry(
            stop_loss=1.0060, take_profit=1.0130,
            old_entry=1.0080, new_entry=1.0021,
        )
        assert new_sl < 1.0021 < new_tp

    def test_missing_levels_pass_through(self):
        new_sl, new_tp = rebase_sl_tp_for_new_entry(
            stop_loss=0.0, take_profit=1.0130,
            old_entry=1.0080, new_entry=1.0021,
        )
        assert new_sl == 0.0
        assert new_tp == pytest.approx(1.0071)

    def test_rr_unchanged_by_rebase(self):
        old_entry, sl, tp = 1.0080, 1.0060, 1.0130
        old_rr = abs(tp - old_entry) / abs(old_entry - sl)
        new_entry = 1.0021
        new_sl, new_tp = rebase_sl_tp_for_new_entry(
            stop_loss=sl, take_profit=tp,
            old_entry=old_entry, new_entry=new_entry,
        )
        new_rr = abs(new_tp - new_entry) / abs(new_entry - new_sl)
        assert new_rr == pytest.approx(old_rr)


class TestRunnerUsesFixedConversion:
    def test_runner_no_longer_uses_inverted_ote_fields(self):
        import inspect
        from trading_bot.services import analyze_and_trade_runner

        src = inspect.getsource(analyze_and_trade_runner)
        # The raw premium-side fields must not be used for entry conversion.
        assert "pd_analysis.ote_low" not in src
        assert "pd_analysis.ote_high" not in src
        assert "ote_pullback_entry" in src
