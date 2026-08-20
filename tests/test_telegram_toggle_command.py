"""Telegram /flags and /toggle — allowlisted flags only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trading_bot.utils.telegram_commands import TelegramCommandHandler


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(
        "trading_bot.services.telegram_settings.save_config_to_env_local",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "trading_bot.api.routes.activity.add_activity",
        lambda *a, **k: None,
    )
    async def _skip_snapshot(*a, **k):
        return None

    monkeypatch.setattr(
        "trading_bot.services.telegram_settings.try_save_config_snapshot",
        _skip_snapshot,
    )
    bot = SimpleNamespace(risk_manager=SimpleNamespace(risk_per_trade=0.01))
    cmd = TelegramCommandHandler(bot_instance=bot)
    cmd._reply = AsyncMock()
    return cmd


@pytest.fixture
def restore_flags():
    from trading_bot.config import settings

    trading = settings.trading
    snap = {
        "claude_signal_trust_mode": trading.claude_signal_trust_mode,
        "news_gates_enabled": trading.news_gates_enabled,
        "opportunity_scanner_enabled": trading.opportunity_scanner_enabled,
        "dry_run": trading.dry_run,
        "demo_data_collection_mode": trading.demo_data_collection_mode,
        "ict_confirmation_mode": trading.ict_confirmation_mode,
    }
    yield
    for key, value in snap.items():
        setattr(trading, key, value)


def test_flags_and_toggle_registered():
    handler = TelegramCommandHandler()
    for cmd in ("/flags", "/toggle", "/yes", "/no"):
        assert cmd in handler._commands


@pytest.mark.asyncio
async def test_flags_lists_allowlist(handler):
    await handler._cmd_flags([])
    text = handler._reply.await_args.args[0]
    assert "trust" in text
    assert "scanner" in text
    assert "strictkz" in text
    assert "ANTHROPIC" not in text
    assert "password" not in text.lower()


@pytest.mark.asyncio
async def test_toggle_unknown_rejected(handler):
    await handler._cmd_toggle(["api_key", "x"])
    text = handler._reply.await_args.args[0]
    assert "Unknown" in text


@pytest.mark.asyncio
async def test_toggle_news_flips(handler, restore_flags):
    from trading_bot.config import settings

    settings.trading.news_gates_enabled = True
    await handler._cmd_toggle(["news"])
    assert settings.trading.news_gates_enabled is False
    text = handler._reply.await_args.args[0]
    assert "news" in text
    assert "off" in text


@pytest.mark.asyncio
async def test_toggle_trust_active_requires_yes(handler, restore_flags):
    from trading_bot.config import settings

    settings.trading.claude_signal_trust_mode = "off"
    await handler._cmd_toggle(["trust", "active"])
    assert settings.trading.claude_signal_trust_mode == "off"
    text = handler._reply.await_args.args[0]
    assert "Confirm" in text
    assert handler._pending_change is not None
    assert handler._pending_change.name == "trust"
    code = handler._pending_change.code
    await handler._cmd_yes([code])
    assert settings.trading.claude_signal_trust_mode == "active"


@pytest.mark.asyncio
async def test_toggle_trust_off_applies_immediately(handler, restore_flags):
    from trading_bot.config import settings

    settings.trading.claude_signal_trust_mode = "active"
    await handler._cmd_toggle(["trust", "off"])
    assert settings.trading.claude_signal_trust_mode == "off"
    assert handler._pending_change is None


@pytest.mark.asyncio
async def test_toggle_scanner_needs_apply(handler, restore_flags):
    from trading_bot.config import settings

    settings.trading.opportunity_scanner_enabled = False
    await handler._cmd_toggle(["scanner", "on"])
    assert settings.trading.opportunity_scanner_enabled is True
    text = handler._reply.await_args.args[0]
    assert "/apply" in text


@pytest.mark.asyncio
async def test_yes_wrong_code(handler, restore_flags):
    from trading_bot.config import settings

    settings.trading.dry_run = True
    await handler._cmd_toggle(["dryrun", "off"])
    assert settings.trading.dry_run is True
    await handler._cmd_yes(["00"])
    assert settings.trading.dry_run is True
    text = handler._reply.await_args.args[0]
    assert "match" in text.lower() or "does not" in text.lower()


@pytest.mark.asyncio
async def test_yes_expired_does_not_apply(handler, restore_flags):
    from trading_bot.config import settings
    from trading_bot.services.telegram_settings import new_pending

    settings.trading.claude_signal_trust_mode = "off"
    pending = new_pending("setting", "trust", "active", now=0.0)
    pending.expires = 1.0
    handler._pending_change = pending
    await handler._cmd_yes([pending.code])
    assert settings.trading.claude_signal_trust_mode == "off"
    assert handler._pending_change is None
    text = handler._reply.await_args.args[0]
    assert "expired" in text.lower()


@pytest.mark.asyncio
async def test_no_cancels_pending(handler, restore_flags):
    from trading_bot.config import settings

    settings.trading.demo_data_collection_mode = False
    await handler._cmd_toggle(["demo", "on"])
    assert handler._pending_change is not None
    await handler._cmd_no([])
    assert handler._pending_change is None
    assert settings.trading.demo_data_collection_mode is False
