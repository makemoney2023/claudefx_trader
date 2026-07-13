"""Tests for signal normalizer."""

import pytest
from types import SimpleNamespace

from trading_bot.services.signal_normalizer import (
    compute_actual_rr,
    min_rr_for_symbol,
    normalize_signal_prices,
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


class TestRRHelpers:
    def test_compute_actual_rr(self):
        assert compute_actual_rr(1.10, 1.08, 1.14) == pytest.approx(2.0)

    def test_min_rr_forex_intraday(self):
        assert min_rr_for_symbol("EURUSD", "intraday", 2.0) == 2.0
