"""
Tests for the DataFetcher cache behavior.

The MT5 client returns bar times as timezone-aware UTC ISO strings, so the
cached DataFrame index is tz-aware. Cache validity checks must not mix
tz-aware timestamps with naive datetime.now() (raises TypeError and kills
the symbol's analysis on every cycle after the first fetch).
"""

import pandas as pd
import pytest
from datetime import datetime, timedelta, timezone

from trading_bot.mt5.data_fetcher import DataFetcher


def _make_df(last_bar_time: datetime) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [100],
        },
        index=pd.DatetimeIndex([last_bar_time], name="time"),
    )
    return df


class FakeMT5Client:
    """Mimics MT5Client.get_ohlcv_data output: tz-aware UTC ISO time strings."""

    def __init__(self):
        self.call_count = 0

    async def get_ohlcv_data(self, symbol, timeframe, count):
        self.call_count += 1
        return [
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "tick_volume": 100,
            }
        ]


class TestCacheValidity:
    def test_tz_aware_fresh_bar_is_valid(self):
        """A tz-aware last bar within the candle period must be a valid cache hit."""
        fetcher = DataFetcher()
        df = _make_df(datetime.now(timezone.utc))
        assert fetcher._is_cache_valid(df, "H1") is True

    def test_tz_aware_stale_bar_is_invalid(self):
        fetcher = DataFetcher()
        df = _make_df(datetime.now(timezone.utc) - timedelta(hours=5))
        assert fetcher._is_cache_valid(df, "H1") is False

    def test_naive_fresh_bar_is_valid(self):
        """Naive timestamps (assumed UTC) must still work."""
        fetcher = DataFetcher()
        df = _make_df(datetime.now(timezone.utc).replace(tzinfo=None))
        assert fetcher._is_cache_valid(df, "H1") is True

    def test_naive_stale_bar_is_invalid(self):
        fetcher = DataFetcher()
        df = _make_df(
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
        )
        assert fetcher._is_cache_valid(df, "H1") is False

    def test_empty_df_is_invalid(self):
        fetcher = DataFetcher()
        assert fetcher._is_cache_valid(pd.DataFrame(), "H1") is False


class TestCachedFetchDoesNotRaise:
    @pytest.mark.asyncio
    async def test_second_fetch_uses_cache_without_error(self):
        """Regression: second get_ohlcv with tz-aware cached data raised TypeError."""
        client = FakeMT5Client()
        fetcher = DataFetcher(client)

        df1 = await fetcher.get_ohlcv("EURUSD", "H1", count=1)
        assert df1 is not None

        df2 = await fetcher.get_ohlcv("EURUSD", "H1", count=1)
        assert df2 is not None
        # Fresh bar within the H1 window: second call must be served from cache
        assert client.call_count == 1
