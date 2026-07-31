"""Tests for timezone-safe datetime helpers and known crash sites."""

from datetime import datetime, timezone

from trading_bot.utils.datetime_utils import as_utc, parse_iso_utc, utc_now


class TestAsUtc:
    def test_naive_becomes_utc_aware(self):
        naive = datetime(2026, 7, 29, 22, 44, 0)
        result = as_utc(naive)
        assert result.tzinfo is not None
        assert result == datetime(2026, 7, 29, 22, 44, 0, tzinfo=timezone.utc)

    def test_aware_converts_to_utc(self):
        from datetime import timedelta

        eastern = timezone(timedelta(hours=-4))
        local = datetime(2026, 7, 29, 18, 44, 0, tzinfo=eastern)
        result = as_utc(local)
        assert result == datetime(2026, 7, 29, 22, 44, 0, tzinfo=timezone.utc)

    def test_subtract_naive_from_aware_via_helper(self):
        # Reproduces the Firecrawl cycle crash:
        # datetime.now(timezone.utc) - datetime.min
        now = utc_now()
        elapsed = (now - as_utc(datetime.min)).total_seconds()
        assert elapsed > 0


class TestParseIsoUtc:
    def test_parses_aware_iso(self):
        dt = parse_iso_utc("2026-07-29T22:44:00+00:00")
        assert dt == datetime(2026, 7, 29, 22, 44, 0, tzinfo=timezone.utc)

    def test_parses_naive_iso_as_utc(self):
        dt = parse_iso_utc("2026-07-29T22:44:00")
        assert dt.tzinfo is not None
        assert dt.hour == 22

    def test_invalid_returns_none(self):
        assert parse_iso_utc("not-a-date") is None
        assert parse_iso_utc(None) is None


class TestFirecrawlRefreshInit:
    """The trading cycle used naive datetime.min against UTC-aware now."""

    def test_datetime_min_must_be_normalized_before_subtract(self):
        now = datetime.now(timezone.utc)
        last = datetime.min  # naive — what main.py used to set
        try:
            _ = (now - last).total_seconds()
            raised = False
        except TypeError as e:
            raised = True
            assert "offset-naive and offset-aware" in str(e)
        assert raised is True

        # Fixed path
        elapsed = (now - as_utc(last)).total_seconds()
        assert elapsed > 0


class TestLossCooldownCompare:
    def test_naive_restored_cooldown_compares_safely(self):
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        # Restored from older state files as naive local/iso without tz
        cooldown_expiry = (now + timedelta(minutes=20)).replace(tzinfo=None)
        remaining = (as_utc(cooldown_expiry) - now).total_seconds() / 60
        assert remaining > 0


class TestSyncTradeHistoryDatetimeCompare:
    """
    Trade sync STEP 1 crashed with:
      Could not update trade 10086631: can't compare offset-naive and offset-aware datetimes

    MT5 deal.time is UTC-aware; SQLite TradeModel.entry_time is often naive.
    """

    def test_naive_entry_vs_aware_exit_raises_without_as_utc(self):
        exit_dt = datetime(2026, 7, 31, 14, 50, tzinfo=timezone.utc)
        open_dt = datetime(2026, 7, 31, 14, 30)  # naive from DB
        try:
            _ = exit_dt < open_dt
            raised = False
        except TypeError as e:
            raised = True
            assert "offset-naive and offset-aware" in str(e)
        assert raised is True

    def test_as_utc_makes_sync_compare_safe(self):
        exit_dt = datetime(2026, 7, 31, 14, 50, tzinfo=timezone.utc)
        open_dt = datetime(2026, 7, 31, 14, 30)  # naive from DB
        assert as_utc(exit_dt) > as_utc(open_dt)

    def test_sync_trade_history_uses_as_utc_before_compare(self):
        import inspect
        from trading_bot.main import TradingBot

        source = inspect.getsource(TradingBot._sync_trade_history)
        assert "as_utc" in source, (
            "_sync_trade_history must normalize datetimes with as_utc before comparing"
        )
        # The open/exit ordering check must normalize both sides
        assert "as_utc(_exit_dt)" in source or "as_utc(_open_dt)" in source
