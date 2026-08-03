"""Telegram /scan command for the opportunity scanner."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_scan_command_registered():
    from trading_bot.utils.telegram_commands import TelegramCommandHandler

    handler = TelegramCommandHandler(bot_instance=None)
    assert "/scan" in handler._commands


@pytest.mark.asyncio
async def test_scan_without_bot_replies_error():
    from trading_bot.utils.telegram_commands import TelegramCommandHandler

    handler = TelegramCommandHandler(bot_instance=None)
    handler._reply = AsyncMock()
    await handler._cmd_scan([])
    handler._reply.assert_awaited()
    text = handler._reply.await_args.args[0]
    assert "not" in text.lower() or "start" in text.lower()


@pytest.mark.asyncio
async def test_scan_without_scanner_replies_error():
    from trading_bot.utils.telegram_commands import TelegramCommandHandler

    bot = SimpleNamespace(opportunity_scanner=None)
    handler = TelegramCommandHandler(bot_instance=bot)
    handler._reply = AsyncMock()
    await handler._cmd_scan([])
    text = handler._reply.await_args.args[0]
    assert "scanner" in text.lower() or "not" in text.lower()


@pytest.mark.asyncio
async def test_scan_starts_and_reports_results(monkeypatch):
    from trading_bot.utils.telegram_commands import TelegramCommandHandler
    from trading_bot.services.opportunity_scanner import Opportunity

    results = [
        Opportunity(
            symbol="EURUSD",
            has_setup=True,
            direction="long",
            score=1.2,
            promotable=True,
            reason="promotable",
            confidence=0.7,
            risk_reward=1.8,
            zone_ok=True,
            spread_ok=True,
            in_kill_zone=True,
        ),
        Opportunity(
            symbol="AUDUSD",
            has_setup=True,
            direction="short",
            score=0.9,
            promotable=False,
            reason="zone_misaligned",
            confidence=0.6,
            risk_reward=1.9,
            zone_ok=False,
            spread_ok=True,
            in_kill_zone=True,
        ),
    ]
    scanner = MagicMock()
    scanner.scan_in_progress = False
    scanner.scan_once = AsyncMock(return_value=results)
    scanner.hot = MagicMock()
    scanner.hot.to_list.return_value = [
        {
            "symbol": "EURUSD",
            "score": 1.2,
            "direction": "long",
            "ttl_minutes_remaining": 60,
        }
    ]
    bot = SimpleNamespace(opportunity_scanner=scanner)
    handler = TelegramCommandHandler(bot_instance=bot)
    handler._reply = AsyncMock()

    class _Trading:
        symbols = ["XAUUSD", "BTCUSD"]

    class _Settings:
        trading = _Trading()

    import trading_bot.config as config_mod

    monkeypatch.setattr(config_mod, "settings", _Settings())

    await handler._cmd_scan([])

    assert scanner.scan_once.await_count == 1
    assert handler._reply.await_count >= 2  # started + results
    started = handler._reply.await_args_list[0].args[0]
    assert "scan" in started.lower()
    final = handler._reply.await_args_list[-1].args[0]
    assert "EURUSD" in final
    assert "hot" in final.lower() or "promot" in final.lower()


@pytest.mark.asyncio
async def test_scan_already_running():
    from trading_bot.utils.telegram_commands import TelegramCommandHandler

    scanner = MagicMock()
    scanner.scan_in_progress = True
    scanner.scan_once = AsyncMock()
    bot = SimpleNamespace(opportunity_scanner=scanner)
    handler = TelegramCommandHandler(bot_instance=bot)
    handler._reply = AsyncMock()

    await handler._cmd_scan([])

    scanner.scan_once.assert_not_awaited()
    assert "already" in handler._reply.await_args.args[0].lower()
