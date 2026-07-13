"""All position-close paths must run the unified close lifecycle.

Regression: Claude re-eval CLOSE, margin emergency close, and
emergency_close_all removed positions from tracking without calling
_handle_position_close — skipping streaks, scaling-manager records,
reservation risk release, learning reviews, and P/L journaling.
"""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot.main import TradingBot


def _position(ticket=101, symbol="EURUSD", pnl=-25.0):
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        direction="long",
        volume=0.10,
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
        open_time=datetime.now(timezone.utc),
        unrealized_pnl=pnl,
        current_price=1.0830,
        close_reason="",
        closed_profit_loss=None,
    )


def _mock_bot(positions):
    bot = MagicMock()
    bot.position_manager.positions = {p.ticket: p for p in positions}
    bot.position_manager.get_all_positions.return_value = list(positions)
    bot.position_manager.remove_position = MagicMock()
    bot.order_manager.close_position = AsyncMock(
        return_value=SimpleNamespace(success=True, message="ok")
    )
    bot._handle_position_close = AsyncMock()
    return bot


class TestCloseLargestLoser:
    @pytest.mark.asyncio
    async def test_runs_close_lifecycle_before_removal(self):
        loser = _position(pnl=-50.0)
        bot = _mock_bot([loser])

        await TradingBot._close_largest_loser(bot)

        bot._handle_position_close.assert_awaited_once_with(loser)
        bot.position_manager.remove_position.assert_called_once_with(loser.ticket)
        assert loser.close_reason == "margin_emergency"

    @pytest.mark.asyncio
    async def test_no_lifecycle_when_close_fails(self):
        loser = _position(pnl=-50.0)
        bot = _mock_bot([loser])
        bot.order_manager.close_position = AsyncMock(
            return_value=SimpleNamespace(success=False, message="rejected")
        )

        await TradingBot._close_largest_loser(bot)

        bot._handle_position_close.assert_not_awaited()
        bot.position_manager.remove_position.assert_not_called()


class TestEmergencyCloseAll:
    @pytest.mark.asyncio
    async def test_runs_close_lifecycle_for_each_position(self):
        p1 = _position(ticket=101, symbol="EURUSD")
        p2 = _position(ticket=102, symbol="XAUUSD")
        bot = _mock_bot([p1, p2])

        await TradingBot.emergency_close_all(bot, reason="flash crash")

        assert bot._handle_position_close.await_count == 2
        assert bot.position_manager.remove_position.call_count == 2
        assert p1.close_reason == "emergency_close"
        assert p2.close_reason == "emergency_close"


class TestClaudeReevalClose:
    def test_close_branch_calls_close_lifecycle(self):
        src = inspect.getsource(TradingBot._claude_reevaluate_positions)
        close_branch = src.split('if decision == "CLOSE"')[1].split(
            'elif decision == "TIGHTEN"'
        )[0]
        assert "_handle_position_close" in close_branch
        assert "claude_close" in close_branch
