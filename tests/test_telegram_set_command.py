"""Telegram /set, /symbol, and writable /mode."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trading_bot.services.scaling_manager import TradingMode
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
    bot = SimpleNamespace(
        risk_manager=SimpleNamespace(risk_per_trade=0.01),
        scaling_manager=SimpleNamespace(
            current_mode=TradingMode.NORMAL,
            current_tier="t1",
            current_risk_pct=0.01,
        ),
        _telegram_mode_lock="",
        BLOCKED_PAIRS=["ETHBTC"],
    )
    cmd = TelegramCommandHandler(bot_instance=bot)
    cmd._reply = AsyncMock()
    return cmd


@pytest.fixture
def restore_values():
    from trading_bot.config import settings

    trading = settings.trading
    snap = {
        "risk_per_trade": trading.risk_per_trade,
        "max_position_size": trading.max_position_size,
        "max_total_exposure": trading.max_total_exposure,
        "max_daily_trades": trading.max_daily_trades,
        "min_risk_reward": trading.min_risk_reward,
        "gate_min_confidence": trading.gate_min_confidence,
        "claude_ny_lead_minutes": trading.claude_ny_lead_minutes,
        "opportunity_scanner_hot_list_size": trading.opportunity_scanner_hot_list_size,
        "symbols": list(trading.symbols),
        "telegram_mode_lock": getattr(trading, "telegram_mode_lock", ""),
    }
    yield
    for key, value in snap.items():
        setattr(trading, key, value)


def test_set_symbol_mode_registered():
    handler = TelegramCommandHandler()
    for cmd in ("/set", "/symbol", "/mode"):
        assert cmd in handler._commands


@pytest.mark.asyncio
async def test_set_lists_values(handler):
    await handler._cmd_set([])
    text = handler._reply.await_args.args[0]
    assert "risk" in text
    assert "maxlot" in text
    assert "lot" not in text.split("maxlot")[0] or "No raw lot" in text


@pytest.mark.asyncio
async def test_set_risk_percent(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.risk_per_trade = 0.01
    await handler._cmd_set(["risk", "0.5%"])
    assert settings.trading.risk_per_trade == pytest.approx(0.005)
    assert handler._bot.risk_manager.risk_per_trade == pytest.approx(0.005)


@pytest.mark.asyncio
async def test_set_risk_over_1_5_requires_yes(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.risk_per_trade = 0.01
    await handler._cmd_set(["risk", "2%"])
    assert settings.trading.risk_per_trade == pytest.approx(0.01)
    assert handler._pending_change is not None
    code = handler._pending_change.code
    await handler._cmd_yes([code])
    assert settings.trading.risk_per_trade == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_set_risk_rejects_above_max(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.risk_per_trade = 0.01
    await handler._cmd_set(["risk", "5%"])
    assert settings.trading.risk_per_trade == pytest.approx(0.01)
    text = handler._reply.await_args.args[0]
    assert "max" in text.lower()


@pytest.mark.asyncio
async def test_set_unknown_rejected(handler):
    await handler._cmd_set(["password", "x"])
    text = handler._reply.await_args.args[0]
    assert "Unknown" in text


@pytest.mark.asyncio
async def test_symbol_add_and_block(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.symbols = ["XAUUSD"]
    await handler._cmd_symbol(["add", "EURUSD"])
    assert "EURUSD" in settings.trading.symbols
    text = handler._reply.await_args.args[0]
    assert "/apply" in text

    await handler._cmd_symbol(["add", "ETHBTC"])
    assert "ETHBTC" not in settings.trading.symbols
    blocked = handler._reply.await_args.args[0]
    assert "Blocked" in blocked


@pytest.mark.asyncio
async def test_symbol_rm(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.symbols = ["XAUUSD", "EURUSD"]
    await handler._cmd_symbol(["rm", "EURUSD"])
    assert settings.trading.symbols == ["XAUUSD"]


@pytest.mark.asyncio
async def test_mode_conservative_locks(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.telegram_mode_lock = ""
    await handler._cmd_mode(["conservative"])
    assert settings.trading.telegram_mode_lock == "conservative"
    assert handler._bot._telegram_mode_lock == "conservative"
    assert handler._bot.scaling_manager.current_mode == TradingMode.CONSERVATIVE


@pytest.mark.asyncio
async def test_mode_aggressive_requires_yes(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.telegram_mode_lock = ""
    await handler._cmd_mode(["aggressive"])
    assert settings.trading.telegram_mode_lock == ""
    assert handler._pending_change is not None
    await handler._cmd_yes([handler._pending_change.code])
    assert settings.trading.telegram_mode_lock == "aggressive"
    assert handler._bot.scaling_manager.current_mode == TradingMode.AGGRESSIVE


@pytest.mark.asyncio
async def test_mode_auto_clears_lock(handler, restore_values):
    from trading_bot.config import settings

    settings.trading.telegram_mode_lock = "normal"
    handler._bot._telegram_mode_lock = "normal"
    await handler._cmd_mode(["auto"])
    assert settings.trading.telegram_mode_lock == ""
    assert handler._bot._telegram_mode_lock == ""


@pytest.mark.asyncio
async def test_mode_does_not_override_defensive(handler, restore_values):
    handler._bot.scaling_manager.current_mode = TradingMode.DEFENSIVE
    await handler._cmd_mode(["conservative"])
    assert handler._bot.scaling_manager.current_mode == TradingMode.DEFENSIVE
    assert handler._bot._telegram_mode_lock == "conservative"
