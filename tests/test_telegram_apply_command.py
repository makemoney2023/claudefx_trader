"""Telegram /apply and TradingBot.apply_runtime_config."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot.utils.telegram_commands import TelegramCommandHandler


def test_apply_registered():
    handler = TelegramCommandHandler()
    assert "/apply" in handler._commands


@pytest.mark.asyncio
async def test_apply_without_bot():
    handler = TelegramCommandHandler(bot_instance=None)
    handler._reply = AsyncMock()
    await handler._cmd_apply([])
    text = handler._reply.await_args.args[0]
    assert "not running" in text.lower() or "start" in text.lower()


@pytest.mark.asyncio
async def test_apply_calls_bot_runtime(monkeypatch):
    bot = SimpleNamespace(
        apply_runtime_config=AsyncMock(
            return_value={
                "scanner": "started",
                "sessions": ["london", "new_york", "london_close"],
                "symbols_synced": ["XAUUSD"],
                "symbols_failed": [],
            }
        )
    )
    handler = TelegramCommandHandler(bot_instance=bot)
    handler._reply = AsyncMock()
    await handler._cmd_apply([])
    bot.apply_runtime_config.assert_awaited_once()
    text = handler._reply.await_args.args[0]
    assert "started" in text
    assert "XAUUSD" in text
    assert "london" in text


@pytest.mark.asyncio
async def test_apply_runtime_config_starts_and_stops_scanner(monkeypatch):
    from trading_bot.config import settings
    from trading_bot.main import TradingBot

    bot = TradingBot()
    bot.running = True
    bot.strategy = SimpleNamespace()
    bot.opportunity_scanner = SimpleNamespace()
    bot.mt5_client = None

    async def fake_loop():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(bot, "_opportunity_scan_loop", fake_loop)
    monkeypatch.setattr(settings.trading, "opportunity_scanner_enabled", True)
    monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")
    monkeypatch.setattr(settings, "strict_ict_sessions", True)

    result = await bot.apply_runtime_config()
    assert result["scanner"] == "started"
    assert bot._opportunity_scan_task is not None
    assert not bot._opportunity_scan_task.done()
    assert result["sessions"] == ["london", "new_york", "london_close"]
    assert bot.strategy.kill_zone_checker is bot.kill_zone_checker
    assert bot.opportunity_scanner.kill_zone_checker is bot.kill_zone_checker

    monkeypatch.setattr(settings.trading, "opportunity_scanner_enabled", False)
    monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")
    result = await bot.apply_runtime_config()
    assert result["scanner"] == "stopped"
    assert bot._opportunity_scan_task is None


@pytest.mark.asyncio
async def test_apply_runtime_config_syncs_mt5_specs(monkeypatch):
    from trading_bot.config import settings
    from trading_bot.main import TradingBot

    bot = TradingBot()
    bot.running = False
    bot.strategy = SimpleNamespace()
    bot.opportunity_scanner = SimpleNamespace()

    info = SimpleNamespace(
        trade_contract_size=100000,
        point=0.00001,
        digits=5,
        trade_tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        swap_long=-1.0,
        swap_short=0.5,
    )
    bot.mt5_client = SimpleNamespace(get_symbol_info=AsyncMock(return_value=info))
    monkeypatch.setattr(settings.trading, "symbols", ["EURUSD"])
    monkeypatch.setattr(settings.trading, "opportunity_scanner_enabled", False)
    monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")

    updated = {}

    def fake_update(**kwargs):
        updated.update(kwargs)

    monkeypatch.setattr(
        "trading_bot.config.update_symbol_spec_from_mt5",
        fake_update,
    )
    result = await bot.apply_runtime_config()
    assert result["symbols_synced"] == ["EURUSD"]
    assert updated.get("symbol") == "EURUSD"
    assert result["scanner"] == "idle"


@pytest.mark.asyncio
async def test_help_includes_new_commands():
    handler = TelegramCommandHandler()
    handler._reply = AsyncMock()
    await handler._cmd_help([])
    text = handler._reply.await_args.args[0]
    assert "/flags" in text
    assert "/toggle" in text
    assert "/set" in text
    assert "/apply" in text
    assert "/yes" in text
