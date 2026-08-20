"""Telegram /activity shows last Claude signal + pass/fail analysis."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trading_bot.utils.telegram_commands import (
    TelegramCommandHandler,
    classify_signal_outcome,
    format_last_signal_activity,
    build_activity_last_signal_reply,
)


class TestClassifyOutcome:
    def test_pass_fill(self):
        assert classify_signal_outcome("market_filled") == "PASS"
        assert classify_signal_outcome("pending_placed") == "PASS"

    def test_fail_block(self):
        assert classify_signal_outcome("mechanical_reject") == "FAIL"
        assert classify_signal_outcome("no_trade") == "FAIL"
        assert classify_signal_outcome("judge_reject") == "FAIL"

    def test_demote_and_pending(self):
        assert classify_signal_outcome("judge_demote") == "DEMOTE"
        assert classify_signal_outcome(None) == "PENDING"
        assert classify_signal_outcome("") == "PENDING"


class TestFormatLastSignal:
    def test_includes_full_reasoning_and_fail(self):
        text = format_last_signal_activity(
            {
                "symbol": "XAUUSD",
                "direction": "long",
                "confidence": 0.72,
                "risk_reward": 2.4,
                "trade_type": "intraday",
                "order_type": "buy_limit",
                "market_structure": "bullish",
                "amd_phase": "distribution",
                "entry_price": 3385.2,
                "stop_loss": 3378.1,
                "take_profit": 3402.4,
                "order_blocks": ["bullish OB 3384"],
                "fvg_zones": ["bullish FVG 3386-3388"],
                "liquidity_targets": ["BSL 3410"],
                "warnings": ["thin volume"],
                "reasoning": "Sweep of SSL then displacement into discount FVG. SL below the sweep low.",
                "timestamp": "2026-08-20T11:12:00+00:00",
            },
            {
                "outcome_type": "mechanical_reject",
                "gate_id": "spread_block",
                "reason": "Spread too wide vs ATR",
                "judge_verdict": "",
            },
        )
        assert "XAUUSD" in text
        assert "LONG" in text
        assert "FAIL" in text
        assert "spread_block" in text
        assert "Spread too wide vs ATR" in text
        assert "Sweep of SSL then displacement into discount FVG" in text
        assert "buy_limit" in text
        assert "thin volume" in text
        assert "Recent Activity" not in text

    def test_escapes_html_in_reasoning(self):
        text = format_last_signal_activity(
            {
                "symbol": "EURUSD",
                "direction": "short",
                "reasoning": "If entry < 1.08 AND R:R > 2 then short.",
            },
            {"outcome_type": "market_filled", "reason": "ok"},
        )
        assert "<" not in text or "&lt;" in text
        assert "1.08" in text
        assert "PASS" in text

    def test_empty_signal(self):
        assert "No recent Claude signal" in format_last_signal_activity(None)


@pytest.mark.asyncio
async def test_activity_command_uses_bot_cache_not_feed():
    bot = SimpleNamespace(
        _last_claude_signal={
            "symbol": "XAUUSD",
            "direction": "short",
            "confidence": 0.81,
            "reasoning": "NY open Judas swing into premium, sell the FVG.",
            "trade_type": "intraday",
            "order_type": "sell_limit",
            "entry_price": 3401.5,
            "stop_loss": 3408.0,
            "take_profit": 3385.0,
            "risk_reward": 2.5,
            "timestamp": "2026-08-20T11:30:00+00:00",
        },
        _last_signal_outcome={
            "symbol": "XAUUSD",
            "outcome_type": "pending_placed",
            "gate_id": "",
            "reason": "Limit working at FVG",
            "judge_verdict": "APPROVE",
        },
        _last_signal_per_symbol={},
    )
    handler = TelegramCommandHandler(bot_instance=bot)
    handler._reply = AsyncMock()
    await handler._cmd_activity([])
    text = handler._reply.await_args.args[0]
    assert "NY open Judas swing into premium" in text
    assert "PASS" in text
    assert "APPROVE" in text
    assert "[SIG]" not in text
    assert "Recent Activity" not in text


@pytest.mark.asyncio
async def test_activity_command_empty():
    handler = TelegramCommandHandler(bot_instance=SimpleNamespace(
        _last_claude_signal=None,
        _last_signal_outcome=None,
        _last_signal_per_symbol={},
    ))
    handler._reply = AsyncMock()

    async def _no_db(*_a, **_k):
        return None

    import trading_bot.utils.telegram_commands as mod

    orig_sig = mod._latest_db_signal
    orig_dec = mod._latest_decision
    orig_mem = mod._latest_memory_signal
    mod._latest_db_signal = _no_db
    mod._latest_decision = _no_db
    mod._latest_memory_signal = lambda _bot: None
    try:
        await handler._cmd_activity([])
    finally:
        mod._latest_db_signal = orig_sig
        mod._latest_decision = orig_dec
        mod._latest_memory_signal = orig_mem

    text = handler._reply.await_args.args[0]
    assert "No recent Claude signal" in text


@pytest.mark.asyncio
async def test_build_reply_prefers_cached_signal():
    bot = SimpleNamespace(
        _last_claude_signal={
            "symbol": "GBPUSD",
            "direction": "long",
            "reasoning": "Full analysis lives here.",
        },
        _last_signal_outcome={
            "symbol": "GBPUSD",
            "outcome_type": "no_trade",
            "reason": "Claude said no_trade",
        },
        _last_signal_per_symbol={},
    )
    text = await build_activity_last_signal_reply(bot)
    assert "Full analysis lives here." in text
    assert "FAIL" in text
    assert "Claude said no_trade" in text


def test_activity_command_registered_with_new_help():
    handler = TelegramCommandHandler(bot_instance=None)
    assert "/activity" in handler._commands
    assert "Claude" in handler._commands["/activity"][1] or "pass" in handler._commands["/activity"][1].lower()
