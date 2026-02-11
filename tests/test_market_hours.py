"""
Tests for market hours utility.
Covers metals (XAUUSD, XAGUSD) Sunday open, daily breaks, and weekend closures.
"""

import pytest
from datetime import datetime, time

from trading_bot.utils.market_hours import (
    get_market_type,
    is_market_open,
    get_next_market_open,
    should_avoid_new_trades,
    MarketType,
)


# ────────────────────────────────────────────
# Market type detection
# ────────────────────────────────────────────

class TestMarketTypeDetection:
    def test_gold_is_metals(self):
        assert get_market_type("XAUUSD") == MarketType.METALS

    def test_silver_is_metals(self):
        assert get_market_type("XAGUSD") == MarketType.METALS

    def test_gold_alias_is_metals(self):
        assert get_market_type("GOLD") == MarketType.METALS

    def test_silver_alias_is_metals(self):
        assert get_market_type("SILVER") == MarketType.METALS

    def test_eurusd_is_forex(self):
        assert get_market_type("EURUSD") == MarketType.FOREX

    def test_btcusd_is_crypto(self):
        assert get_market_type("BTCUSD") == MarketType.CRYPTO

    def test_dangerous_btc_pair_is_forex(self):
        """BTC-quoted pairs must be treated as forex (closed weekends) for safety."""
        assert get_market_type("ETHBTC") == MarketType.FOREX


# ────────────────────────────────────────────
# Metals Sunday open
# ────────────────────────────────────────────

class TestMetalsSundayOpen:
    """Sunday open for metals is 22:00 UTC (5 PM EST in winter)."""

    def test_sunday_before_open_is_closed(self):
        """Sunday 21:00 UTC (before 22:00) -> closed."""
        sunday_before = datetime(2026, 2, 8, 21, 0, 0)  # Sunday 21:00 UTC
        is_open, reason = is_market_open("XAUUSD", sunday_before)
        assert is_open is False
        assert "Sunday before open" in reason

    def test_sunday_at_open_is_open(self):
        """Sunday 22:01 UTC (right after open) -> open, NOT blocked by daily break."""
        sunday_open = datetime(2026, 2, 8, 22, 1, 0)  # Sunday 22:01 UTC
        is_open, reason = is_market_open("XAUUSD", sunday_open)
        assert is_open is True

    def test_sunday_at_2230_is_open(self):
        """Sunday 22:30 UTC -> should be open (inside the daily break window but Sunday is exempt)."""
        sunday_mid = datetime(2026, 2, 8, 22, 30, 0)  # Sunday 22:30 UTC
        is_open, reason = is_market_open("XAUUSD", sunday_mid)
        assert is_open is True

    def test_sunday_at_2300_is_open(self):
        """Sunday 23:00 UTC -> open."""
        sunday_late = datetime(2026, 2, 8, 23, 0, 0)  # Sunday 23:00 UTC
        is_open, reason = is_market_open("XAUUSD", sunday_late)
        assert is_open is True

    def test_silver_sunday_open(self):
        """Silver should follow the same Sunday open as gold."""
        sunday_open = datetime(2026, 2, 8, 22, 5, 0)
        is_open, _ = is_market_open("XAGUSD", sunday_open)
        assert is_open is True

    def test_sunday_early_morning_closed(self):
        """Sunday 10:00 UTC -> closed (before 22:00 open)."""
        sunday_early = datetime(2026, 2, 8, 10, 0, 0)
        is_open, _ = is_market_open("XAUUSD", sunday_early)
        assert is_open is False


# ────────────────────────────────────────────
# Metals daily break (Mon-Thu only)
# ────────────────────────────────────────────

class TestMetalsDailyBreak:
    """Daily maintenance break 22:00-23:00 UTC applies Mon-Thu only."""

    def test_monday_during_break_is_closed(self):
        """Monday 22:30 UTC -> closed (daily break)."""
        mon_break = datetime(2026, 2, 9, 22, 30, 0)  # Monday
        is_open, reason = is_market_open("XAUUSD", mon_break)
        assert is_open is False
        assert "maintenance break" in reason

    def test_tuesday_during_break_is_closed(self):
        """Tuesday 22:15 UTC -> closed."""
        tue_break = datetime(2026, 2, 10, 22, 15, 0)  # Tuesday
        is_open, reason = is_market_open("XAUUSD", tue_break)
        assert is_open is False

    def test_wednesday_during_break_is_closed(self):
        """Wednesday 22:00 UTC -> closed."""
        wed_break = datetime(2026, 2, 11, 22, 0, 0)  # Wednesday
        is_open, reason = is_market_open("XAUUSD", wed_break)
        assert is_open is False

    def test_thursday_during_break_is_closed(self):
        """Thursday 22:45 UTC -> closed."""
        thu_break = datetime(2026, 2, 12, 22, 45, 0)  # Thursday
        is_open, reason = is_market_open("XAUUSD", thu_break)
        assert is_open is False

    def test_monday_before_break_is_open(self):
        """Monday 21:59 UTC -> open (just before break)."""
        mon_pre = datetime(2026, 2, 9, 21, 59, 0)  # Monday
        is_open, _ = is_market_open("XAUUSD", mon_pre)
        assert is_open is True

    def test_tuesday_after_break_is_open(self):
        """Tuesday 23:01 UTC -> open (just after break)."""
        tue_post = datetime(2026, 2, 10, 23, 1, 0)  # Tuesday
        is_open, _ = is_market_open("XAUUSD", tue_post)
        assert is_open is True

    def test_midweek_normal_hours_open(self):
        """Wednesday 14:00 UTC -> open (normal trading hours)."""
        wed_mid = datetime(2026, 2, 11, 14, 0, 0)
        is_open, _ = is_market_open("XAUUSD", wed_mid)
        assert is_open is True


# ────────────────────────────────────────────
# Weekend closures
# ────────────────────────────────────────────

class TestWeekendClosure:
    def test_saturday_is_closed(self):
        """Saturday -> always closed for metals."""
        saturday = datetime(2026, 2, 7, 12, 0, 0)  # Saturday
        is_open, reason = is_market_open("XAUUSD", saturday)
        assert is_open is False
        assert "Saturday" in reason

    def test_friday_before_close_is_open(self):
        """Friday 21:00 UTC -> open (before 22:00 close)."""
        fri_open = datetime(2026, 2, 13, 21, 0, 0)  # Friday
        is_open, _ = is_market_open("XAUUSD", fri_open)
        assert is_open is True

    def test_friday_after_close_is_closed(self):
        """Friday 22:00 UTC -> closed."""
        fri_closed = datetime(2026, 2, 13, 22, 0, 0)  # Friday
        is_open, reason = is_market_open("XAUUSD", fri_closed)
        assert is_open is False
        assert "Friday" in reason


# ────────────────────────────────────────────
# Next market open
# ────────────────────────────────────────────

class TestNextMarketOpen:
    def test_saturday_next_open_is_sunday(self):
        """From Saturday, next open should be Sunday 22:00 UTC."""
        saturday = datetime(2026, 2, 7, 12, 0, 0)
        next_open = get_next_market_open("XAUUSD", saturday)
        assert next_open is not None
        assert next_open.weekday() == 6  # Sunday
        assert next_open.hour == 22
        assert next_open.minute == 0

    def test_sunday_before_open_next_is_today(self):
        """Sunday before 22:00 -> next open is later today."""
        sunday_early = datetime(2026, 2, 8, 15, 0, 0)
        next_open = get_next_market_open("XAUUSD", sunday_early)
        assert next_open is not None
        assert next_open.day == 8  # Same day
        assert next_open.hour == 22

    def test_already_open_returns_none(self):
        """When market is open, returns None."""
        monday = datetime(2026, 2, 9, 14, 0, 0)
        next_open = get_next_market_open("XAUUSD", monday)
        assert next_open is None

    def test_crypto_always_none(self):
        """Crypto is always open, so next open is always None."""
        assert get_next_market_open("BTCUSD") is None


# ────────────────────────────────────────────
# Forex for comparison
# ────────────────────────────────────────────

class TestForexHours:
    def test_forex_no_daily_break(self):
        """Forex should NOT have a daily break on Monday at 22:30."""
        mon_night = datetime(2026, 2, 9, 22, 30, 0)
        is_open, _ = is_market_open("EURUSD", mon_night)
        assert is_open is True

    def test_forex_sunday_open(self):
        """Forex opens Sunday 22:00 UTC, same as metals."""
        sunday_open = datetime(2026, 2, 8, 22, 5, 0)
        is_open, _ = is_market_open("EURUSD", sunday_open)
        assert is_open is True

    def test_forex_saturday_closed(self):
        saturday = datetime(2026, 2, 7, 12, 0, 0)
        is_open, _ = is_market_open("EURUSD", saturday)
        assert is_open is False
