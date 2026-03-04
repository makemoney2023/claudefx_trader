"""
Tests for WebSocket infrastructure.

Covers:
- ConnectionManager channel management
- Authentication via query parameter
- Broadcast functions (trade, price, analysis, activity)
- Activity channel auto-broadcast wiring
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from trading_bot.api.websocket import (
    ConnectionManager,
    broadcast_trade_update,
    broadcast_price_update,
    broadcast_analysis_update,
    broadcast_activity,
    _authenticate_websocket,
    manager,
)


class MockWebSocket:
    """Minimal mock for FastAPI WebSocket."""

    def __init__(self, query_params=None):
        self.query_params = query_params or {}
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self.sent_texts = []
        self.sent_jsons = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_text(self, data: str):
        self.sent_texts.append(data)

    async def send_json(self, data: dict):
        self.sent_jsons.append(data)


# --- ConnectionManager Tests ---

class TestConnectionManager:

    def test_initial_channels(self):
        cm = ConnectionManager()
        assert "trades" in cm.active_connections
        assert "prices" in cm.active_connections
        assert "analysis" in cm.active_connections
        assert "activity" in cm.active_connections
        assert "all" in cm.active_connections

    @pytest.mark.asyncio
    async def test_connect_adds_to_channel(self):
        cm = ConnectionManager()
        ws = MockWebSocket()
        await cm.connect(ws, "trades")
        assert ws.accepted
        assert ws in cm.active_connections["trades"]

    @pytest.mark.asyncio
    async def test_connect_creates_unknown_channel(self):
        cm = ConnectionManager()
        ws = MockWebSocket()
        await cm.connect(ws, "custom_channel")
        assert ws in cm.active_connections["custom_channel"]

    def test_disconnect_removes_from_channel(self):
        cm = ConnectionManager()
        ws = MockWebSocket()
        cm.active_connections["trades"].add(ws)
        cm.disconnect(ws, "trades")
        assert ws not in cm.active_connections["trades"]

    def test_disconnect_nonexistent_is_safe(self):
        cm = ConnectionManager()
        ws = MockWebSocket()
        cm.disconnect(ws, "trades")

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_channel(self):
        cm = ConnectionManager()
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        cm.active_connections["trades"] = {ws1, ws2}
        cm.active_connections["all"] = set()

        await cm.broadcast("trades", {"event": "test"})

        for ws in [ws1, ws2]:
            assert len(ws.sent_texts) == 1
            parsed = json.loads(ws.sent_texts[0])
            assert parsed["event"] == "test"

    @pytest.mark.asyncio
    async def test_broadcast_also_sends_to_all_channel(self):
        cm = ConnectionManager()
        ws_trades = MockWebSocket()
        ws_all = MockWebSocket()
        cm.active_connections["trades"] = {ws_trades}
        cm.active_connections["all"] = {ws_all}

        await cm.broadcast("trades", {"event": "test"})

        assert len(ws_trades.sent_texts) == 1
        assert len(ws_all.sent_texts) == 1

    @pytest.mark.asyncio
    async def test_broadcast_to_all_does_not_duplicate(self):
        cm = ConnectionManager()
        ws_all = MockWebSocket()
        cm.active_connections["all"] = {ws_all}

        await cm.broadcast("all", {"event": "test"})
        assert len(ws_all.sent_texts) == 1

    @pytest.mark.asyncio
    async def test_broadcast_cleans_dead_connections(self):
        cm = ConnectionManager()
        dead_ws = MockWebSocket()
        dead_ws.send_text = AsyncMock(side_effect=Exception("Connection lost"))
        cm.active_connections["trades"] = {dead_ws}
        cm.active_connections["all"] = set()

        await cm.broadcast("trades", {"event": "test"})

        assert dead_ws not in cm.active_connections["trades"]

    def test_get_connection_count_by_channel(self):
        cm = ConnectionManager()
        cm.active_connections["trades"] = {MockWebSocket(), MockWebSocket()}
        cm.active_connections["all"] = {MockWebSocket()}
        assert cm.get_connection_count("trades") == 2
        assert cm.get_connection_count("all") == 1
        assert cm.get_connection_count("nonexistent") == 0

    def test_get_connection_count_total(self):
        cm = ConnectionManager()
        cm.active_connections["trades"] = {MockWebSocket()}
        cm.active_connections["prices"] = {MockWebSocket(), MockWebSocket()}
        cm.active_connections["analysis"] = set()
        cm.active_connections["activity"] = set()
        cm.active_connections["all"] = set()
        assert cm.get_connection_count() == 3


# --- Authentication Tests ---

class TestWebSocketAuth:

    @pytest.mark.asyncio
    async def test_auth_accepts_valid_key(self):
        with patch("trading_bot.api.websocket.get_api_key", return_value="valid_key"):
            ws = MockWebSocket(query_params={"api_key": "valid_key"})
            result = await _authenticate_websocket(ws)
            assert result is True
            assert not ws.closed

    @pytest.mark.asyncio
    async def test_auth_rejects_invalid_key(self):
        with patch("trading_bot.api.websocket.get_api_key", return_value="valid_key"):
            ws = MockWebSocket(query_params={"api_key": "wrong_key"})
            result = await _authenticate_websocket(ws)
            assert result is False
            assert ws.closed
            assert ws.close_code == 4001

    @pytest.mark.asyncio
    async def test_auth_rejects_missing_key(self):
        with patch("trading_bot.api.websocket.get_api_key", return_value="valid_key"):
            ws = MockWebSocket(query_params={})
            result = await _authenticate_websocket(ws)
            assert result is False
            assert ws.closed

    @pytest.mark.asyncio
    async def test_auth_allows_when_no_key_configured(self):
        with patch("trading_bot.api.websocket.get_api_key", return_value=None):
            ws = MockWebSocket(query_params={})
            result = await _authenticate_websocket(ws)
            assert result is True
            assert not ws.closed


# --- Broadcast Function Tests ---

class TestBroadcastFunctions:

    @pytest.mark.asyncio
    async def test_broadcast_trade_update(self):
        ws = MockWebSocket()
        manager.active_connections["trades"] = {ws}
        manager.active_connections["all"] = set()

        await broadcast_trade_update({"event": "trade_opened", "ticket": 123, "symbol": "EURUSD"})

        assert len(ws.sent_texts) == 1
        msg = json.loads(ws.sent_texts[0])
        assert msg["type"] == "trade_update"
        assert msg["data"]["event"] == "trade_opened"
        assert msg["data"]["ticket"] == 123
        assert "timestamp" in msg

        manager.active_connections["trades"] = set()

    @pytest.mark.asyncio
    async def test_broadcast_price_update(self):
        ws = MockWebSocket()
        manager.active_connections["prices"] = {ws}
        manager.active_connections["all"] = set()

        await broadcast_price_update("EURUSD", 1.1050, 1.1052)

        msg = json.loads(ws.sent_texts[0])
        assert msg["type"] == "price_update"
        assert msg["data"]["symbol"] == "EURUSD"
        assert msg["data"]["bid"] == 1.1050
        assert msg["data"]["ask"] == 1.1052
        assert abs(msg["data"]["spread"] - 0.0002) < 1e-10

        manager.active_connections["prices"] = set()

    @pytest.mark.asyncio
    async def test_broadcast_analysis_update(self):
        ws = MockWebSocket()
        manager.active_connections["analysis"] = {ws}
        manager.active_connections["all"] = set()

        await broadcast_analysis_update("GBPUSD", {
            "direction": "long",
            "confidence": 0.85,
            "rr_ratio": 3.2
        })

        msg = json.loads(ws.sent_texts[0])
        assert msg["type"] == "analysis_update"
        assert msg["data"]["symbol"] == "GBPUSD"
        assert msg["data"]["direction"] == "long"
        assert msg["data"]["confidence"] == 0.85

        manager.active_connections["analysis"] = set()

    @pytest.mark.asyncio
    async def test_broadcast_activity(self):
        ws = MockWebSocket()
        manager.active_connections["activity"] = {ws}
        manager.active_connections["all"] = set()

        await broadcast_activity({
            "type": "trade_opened",
            "symbol": "XAUUSD",
            "message": "Opened long XAUUSD",
        })

        msg = json.loads(ws.sent_texts[0])
        assert msg["type"] == "activity"
        assert msg["data"]["symbol"] == "XAUUSD"
        assert "timestamp" in msg

        manager.active_connections["activity"] = set()

    @pytest.mark.asyncio
    async def test_broadcast_reaches_all_channel(self):
        ws_activity = MockWebSocket()
        ws_all = MockWebSocket()
        manager.active_connections["activity"] = {ws_activity}
        manager.active_connections["all"] = {ws_all}

        await broadcast_activity({"type": "info", "message": "test"})

        assert len(ws_activity.sent_texts) == 1
        assert len(ws_all.sent_texts) == 1

        manager.active_connections["activity"] = set()
        manager.active_connections["all"] = set()


# --- Activity Wiring Test ---

class TestActivityWiring:

    @pytest.mark.asyncio
    async def test_add_activity_triggers_broadcast(self):
        with patch("trading_bot.api.routes.activity.broadcast_activity", new_callable=AsyncMock) as mock_broadcast:
            from trading_bot.api.routes.activity import add_activity
            add_activity("trade_opened", "Opened EURUSD long", symbol="EURUSD")

            await asyncio.sleep(0.1)
            mock_broadcast.assert_called_once()
            call_data = mock_broadcast.call_args[0][0]
            assert call_data["type"] == "trade_opened"
            assert call_data["symbol"] == "EURUSD"
