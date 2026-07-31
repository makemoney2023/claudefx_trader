"""Tests for signal normalizer."""

import pytest
from types import SimpleNamespace

from trading_bot.services.post_claude_gates import compute_actual_rr, resolve_min_rr
from trading_bot.services.signal_normalizer import (
    normalize_signal_prices,
    recover_missing_entry_from_mechanical,
)


class TestNormalizeSignalPrices:
    def test_flips_direction_when_levels_say_long(self):
        sig = SimpleNamespace(
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            direction="short",
        )
        result = normalize_signal_prices(sig, SimpleNamespace(key_levels={}), 1.0850)
        assert result.direction == "long"
        assert result.direction_flipped is True

    def test_rejects_missing_sl(self):
        sig = SimpleNamespace(
            entry_price=1.0850,
            stop_loss=None,
            take_profit=1.0950,
            direction="long",
        )
        result = normalize_signal_prices(sig, SimpleNamespace(key_levels={}), 1.0850)
        assert result.rejected is True

    def test_no_trade_allows_missing_sl_tp(self):
        """no_trade is a valid decision — do not reject for missing prices."""
        sig = SimpleNamespace(
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            direction="no_trade",
        )
        result = normalize_signal_prices(sig, SimpleNamespace(key_levels={}), 1.0850)
        assert result.rejected is False
        assert result.direction == "no_trade"


class TestRecoverMissingEntryFromMechanical:
    def test_fills_entry_when_direction_agrees_and_levels_coherent(self):
        entry = recover_missing_entry_from_mechanical(
            direction="short",
            stop_loss=4110.0,
            take_profit=4081.5,
            mechanical_setup={
                "direction": "short",
                "entry_zone": {"optimal": 4104.5, "high": 4106.0, "low": 4103.0},
            },
        )
        assert entry == pytest.approx(4104.5)

    def test_rejects_when_mechanical_direction_disagrees(self):
        entry = recover_missing_entry_from_mechanical(
            direction="short",
            stop_loss=4110.0,
            take_profit=4081.5,
            mechanical_setup={
                "direction": "long",
                "entry_zone": {"optimal": 4104.5},
            },
        )
        assert entry is None

    def test_rejects_when_mech_entry_outside_claude_sl_tp(self):
        entry = recover_missing_entry_from_mechanical(
            direction="short",
            stop_loss=4110.0,
            take_profit=4081.5,
            mechanical_setup={
                "direction": "short",
                "entry_zone": {"optimal": 4125.0},
            },
        )
        assert entry is None


class TestRRHelpers:
    def test_compute_actual_rr(self):
        assert compute_actual_rr(1.10, 1.08, 1.14) == pytest.approx(2.0)

    def test_min_rr_forex_intraday(self):
        assert resolve_min_rr("EURUSD", "intraday") == 2.0
