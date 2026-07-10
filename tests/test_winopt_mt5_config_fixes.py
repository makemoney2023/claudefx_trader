"""Tests for winopt MT5 client and config critical fixes (E2E #4, #5, #6)."""

import asyncio
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# A) UTC timestamps (E2E #6)
# ---------------------------------------------------------------------------

class TestUtcTimestamps:
    """OHLC bars and MT5 time fields must use UTC, not local time."""

    @pytest.mark.asyncio
    async def test_ohlcv_bar_time_is_utc_isoformat(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = False
        client._connected = True

        ts = 1704067200  # 2024-01-01 00:00:00 UTC
        bars = [{
            'time': ts,
            'open': 1.0,
            'high': 1.1,
            'low': 0.9,
            'close': 1.05,
            'tick_volume': 100,
        }]

        mock_mt5 = SimpleNamespace(
            TIMEFRAME_M1=1,
            TIMEFRAME_M5=5,
            TIMEFRAME_M15=15,
            TIMEFRAME_M30=30,
            TIMEFRAME_H1=16385,
            TIMEFRAME_H4=16388,
            TIMEFRAME_D1=16408,
            TIMEFRAME_W1=32769,
            TIMEFRAME_MN1=49153,
            copy_rates_from_pos=lambda *a, **k: bars,
        )
        client._mcp_client = mock_mt5

        async def route_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch('trading_bot.mt5.client.asyncio.to_thread', side_effect=route_to_thread):
            with patch.dict('sys.modules', {'MetaTrader5': mock_mt5}):
                result = await client.get_ohlcv_data("EURUSD", "H1", count=1)

        assert result is not None
        assert len(result) == 1
        assert result[0]['time'] == datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        assert '+00:00' in result[0]['time'] or result[0]['time'].endswith('Z')

    @pytest.mark.asyncio
    async def test_position_time_is_utc_aware(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = False
        client._connected = True

        ts = 1704067200
        mock_pos = SimpleNamespace(
            ticket=42,
            symbol="EURUSD",
            type=0,
            volume=0.01,
            price_open=1.1,
            price_current=1.1005,
            sl=0.0,
            tp=0.0,
            profit=0.5,
            magic=100,
            comment="test",
            time=ts,
        )

        mock_mt5 = SimpleNamespace(positions_get=lambda **k: [mock_pos])
        client._mcp_client = mock_mt5

        async def route_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch('trading_bot.mt5.client.asyncio.to_thread', side_effect=route_to_thread):
            positions = await client.get_positions()

        assert len(positions) == 1
        assert positions[0].time.tzinfo == timezone.utc
        assert positions[0].time == datetime.fromtimestamp(ts, tz=timezone.utc)


# ---------------------------------------------------------------------------
# B) Market order ticket (E2E #5)
# ---------------------------------------------------------------------------

class TestMarketOrderTicket:
    """Market orders must return position ticket, not deal ticket."""

    @pytest.mark.asyncio
    async def test_market_order_ticket_uses_position_not_deal(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = False
        client._connected = True

        position_ticket = 12345
        deal_ticket = 99999

        symbol_info = SimpleNamespace(
            ask=1.10000,
            bid=1.09990,
            digits=5,
            point=0.00001,
            trade_stops_level=0,
            expiration_mode=1,
            filling_mode=1,
        )

        order_result = SimpleNamespace(
            retcode=10009,
            order=position_ticket,
            deal=deal_ticket,
            price=1.10000,
            volume=0.01,
            comment="done",
        )

        mock_mt5 = SimpleNamespace(
            TRADE_RETCODE_DONE=10009,
            TRADE_RETCODE_PLACED=10008,
            TRADE_ACTION_DEAL=1,
            TRADE_ACTION_PENDING=5,
            ORDER_TIME_GTC=0,
            ORDER_TYPE_BUY=0,
            ORDER_TYPE_SELL=1,
            symbol_info=lambda symbol: symbol_info,
            order_send=lambda request: order_result,
        )
        client._mcp_client = mock_mt5

        async def route_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(client, 'ensure_connected', new=AsyncMock(return_value=True)):
            with patch('trading_bot.mt5.client.asyncio.to_thread', side_effect=route_to_thread):
                result = await client.place_order(
                    symbol="EURUSD",
                    order_type="buy",
                    volume=0.01,
                    magic=100,
                )

        assert result['success'] is True
        assert result['ticket'] == position_ticket
        assert result['ticket'] != deal_ticket
        assert result['order_id'] == position_ticket

    @pytest.mark.asyncio
    async def test_simulation_place_order_still_works(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client(login=12345, password="test", server="TestServer")
        client._use_simulation = True
        client._connected = True

        result = await client.place_order(
            symbol="EURUSD",
            order_type="buy",
            volume=0.01,
        )

        assert result['success'] is True
        assert result.get('simulated') is True
        assert 'ticket' in result


# ---------------------------------------------------------------------------
# C) JPY pip_value cap (E2E #4)
# ---------------------------------------------------------------------------

class TestJpyPipValueCap:
    """JPY-style pip_value corruption (exactly 1000) must fall back to default."""

    def setup_method(self):
        import trading_bot.config as config
        config._MT5_RUNTIME_SPECS.clear()

    def test_jpy_pip_value_exactly_1000_rejected(self):
        import trading_bot.config as config

        # 0.01 pip * 100000 contract = 1000 (corrupt JPY calc)
        config.update_symbol_spec_from_mt5(
            symbol="USDJPY",
            trade_contract_size=100000,
            point=0.001,
            digits=3,
        )

        spec = config.get_symbol_spec("USDJPY")
        assert spec.pip_value == 9.0
        assert spec.pip_value != 1000

    def test_valid_pip_value_below_1000_accepted(self):
        import trading_bot.config as config

        config.update_symbol_spec_from_mt5(
            symbol="EURUSD",
            trade_contract_size=100000,
            point=0.00001,
            digits=5,
        )

        spec = config.get_symbol_spec("EURUSD")
        assert spec.pip_value == 10.0
