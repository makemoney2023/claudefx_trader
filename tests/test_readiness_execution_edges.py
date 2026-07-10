"""
Wave 2 Task 6 — news freshness, MT5 identity, UTC, auth, signal coherence.
"""

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from trading_bot.llm.claude_client import ClaudeClient
from trading_bot.utils.win_optimization import validate_signal_coherence


class TestNewsFreshness:
    @pytest.mark.asyncio
    async def test_failed_refresh_keeps_stale_timestamp(self, news_service):
        stale_time = datetime.now(timezone.utc) - timedelta(hours=3)
        news_service.set_events(
            [
                {
                    "title": "CPI",
                    "datetime": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                    "impact": "high",
                    "currency": "USD",
                }
            ]
        )
        news_service._last_fetch = stale_time

        injected = MagicMock()
        injected.get_economic_calendar = AsyncMock(return_value=[])
        news_service.set_intelligence_client(injected)

        with patch.object(news_service, "_fetch_from_forexfactory", AsyncMock(return_value=[])):
            result = await news_service.refresh_calendar(force=True)

        assert result is False
        assert news_service._last_fetch == stale_time
        assert news_service.is_calendar_unreliable() is True

    @pytest.mark.asyncio
    async def test_successful_refresh_updates_timestamp(self, news_service):
        old_fetch = datetime.now(timezone.utc) - timedelta(hours=3)
        news_service._last_fetch = old_fetch
        fresh_events = [
            {
                "title": "NFP",
                "datetime": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                "impact": "high",
                "currency": "USD",
            }
        ]
        injected = MagicMock()
        injected.get_economic_calendar = AsyncMock(return_value=fresh_events)
        news_service.set_intelligence_client(injected)

        with patch.object(news_service, "_fetch_from_forexfactory", AsyncMock(return_value=[])):
            result = await news_service.refresh_calendar(force=True)

        assert result is True
        assert news_service._last_fetch > old_fetch
        assert news_service._events[0]["title"] == "NFP"


class TestMT5PositionIdentity:
    @pytest.mark.asyncio
    async def test_market_fill_resolves_position_ticket_not_order_ticket(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client.__new__(MT5Client)
        client._use_simulation = True
        client._connected = True

        order_ticket = 90001
        position_ticket = 70001
        client._resolve_position_ticket_from_history = AsyncMock(
            return_value=position_ticket
        )

        resolved = await client.resolve_fill_position_ticket(
            symbol="EURUSD",
            order_ticket=order_ticket,
            deal_ticket=80001,
        )
        assert resolved == position_ticket
        assert resolved != order_ticket


class TestUTCConversions:
    def test_mt5_history_timestamps_are_utc_aware(self):
        from trading_bot.mt5.client import MT5Client

        client = MT5Client.__new__(MT5Client)
        client._use_simulation = True
        client._connected = True

        ts = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        converted = client._to_utc_datetime(ts.timestamp())

        assert converted.tzinfo is not None
        assert converted.tzinfo.utcoffset(converted) == timedelta(0)


class TestSignalCoherence:
    def test_incoherent_short_levels_rejected_before_repair(self):
        ok, reason = validate_signal_coherence(
            entry=1.0850,
            sl=1.0800,
            tp=1.0950,
            direction="short",
        )
        assert ok is False
        assert "incoherent" in reason.lower()

    def test_claude_client_rejects_incoherent_before_swap(self):
        client = ClaudeClient.__new__(ClaudeClient)
        tool_input = {
            "direction": "short",
            "confidence": 0.8,
            "entry_price": 1.0850,
            "stop_loss": 1.0800,
            "take_profit": 1.0950,
            "trade_type": "intraday",
            "order_type": "market",
            "market_structure": "bearish",
            "reasoning": "test",
        }
        result = client._validate_tool_input(tool_input)
        assert result["direction"] == "no_trade"
        assert result["confidence"] == 0.0


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setenv("BOT_API_KEY", "wave2-test-key")
    from trading_bot.api.auth import get_api_key

    get_api_key.cache_clear() if hasattr(get_api_key, "cache_clear") else None
    from trading_bot.api.main import app

    return TestClient(app), "wave2-test-key"


class TestAuthNeverLogsGeneratedKeys:
    def test_get_api_key_does_not_log_generated_secret(self, monkeypatch, caplog):
        monkeypatch.delenv("BOT_API_KEY", raising=False)
        import trading_bot.api.auth as auth_module

        auth_module._API_KEY = None
        with patch.object(auth_module.logger, "warning") as mock_warn:
            with patch("builtins.open", side_effect=OSError("no write")):
                key = auth_module.get_api_key()
        assert key
        for call in mock_warn.call_args_list:
            assert key not in str(call)

    def test_api_key_fingerprint_is_non_reversible(self):
        from trading_bot.api.auth import api_key_fingerprint

        secret = "super-secret-bot-key-value"
        fp = api_key_fingerprint(secret)
        assert secret not in fp
        assert len(fp) == 12

    def test_main_startup_does_not_log_plaintext_api_key(self):
        import trading_bot.api.main as main_module

        source = inspect.getsource(main_module.lifespan)
        assert "API Key for protected endpoints: {api_key}" not in source
        assert "api_key_fingerprint" in source or "is_api_key_configured" in source


class TestExecutionEdges:
    def test_position_tracks_order_ticket_separately(self):
        from trading_bot.execution.position_manager import Position

        pos = Position(
            ticket=70001,
            symbol="EURUSD",
            direction="long",
            volume=0.05,
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            open_time=datetime.now(timezone.utc),
            order_ticket=90001,
        )
        assert pos.ticket != pos.order_ticket
