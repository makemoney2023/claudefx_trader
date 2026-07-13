"""Volatility spike response: detector coverage + defensive actions.

Covers:
1. Detector checks configured trading symbols (not hardcoded majors).
2. Spike response: pause new entries, cancel pendings on spiking symbols,
   tighten giveback protection — never close positions outright.
3. Runner blocks new entries while the pause window is active.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from trading_bot.config import settings
from trading_bot.execution.position_manager import Position, PositionManager, PositionStatus
from trading_bot.main import TradingBot


def _calm_df(rows: int = 50) -> pd.DataFrame:
    data = []
    for i in range(rows):
        p = 1.08 + i * 0.00001
        data.append({"open": p, "high": p + 0.0004, "low": p - 0.0004, "close": p})
    return pd.DataFrame(data)


def _spike_df(rows: int = 50) -> pd.DataFrame:
    df_rows = []
    for i in range(rows - 1):
        p = 1.08
        df_rows.append({"open": p, "high": p + 0.0004, "low": p - 0.0004, "close": p})
    # Final candle: 10x the normal range
    df_rows.append({"open": 1.08, "high": 1.088, "low": 1.08, "close": 1.0875})
    return pd.DataFrame(df_rows)


def _detector_bot(dfs_by_symbol):
    bot = MagicMock()

    async def _get_ohlcv(symbol, timeframe, count):
        return dfs_by_symbol.get(symbol)

    bot.data_fetcher.get_ohlcv = AsyncMock(side_effect=_get_ohlcv)
    bot.position_manager.get_all_positions.return_value = []
    return bot


class TestVolatilityDetector:
    @pytest.mark.asyncio
    async def test_checks_configured_symbols_not_hardcoded_majors(self, monkeypatch):
        monkeypatch.setattr(settings.trading, "symbols", ["XAUUSD", "BTCUSD"])
        bot = _detector_bot({"XAUUSD": _spike_df(), "BTCUSD": _calm_df()})

        alert = await TradingBot._check_volatility(bot)

        assert alert is not None
        assert "XAUUSD" in alert["symbols"]
        assert "BTCUSD" not in alert["symbols"]

    @pytest.mark.asyncio
    async def test_includes_open_position_symbols(self, monkeypatch):
        monkeypatch.setattr(settings.trading, "symbols", ["EURUSD"])
        bot = _detector_bot({"EURUSD": _calm_df(), "US30": _spike_df()})
        bot.position_manager.get_all_positions.return_value = [
            SimpleNamespace(symbol="US30")
        ]

        alert = await TradingBot._check_volatility(bot)

        assert alert is not None
        assert "US30" in alert["symbols"]

    @pytest.mark.asyncio
    async def test_returns_none_when_calm(self, monkeypatch):
        monkeypatch.setattr(settings.trading, "symbols", ["EURUSD"])
        bot = _detector_bot({"EURUSD": _calm_df()})

        alert = await TradingBot._check_volatility(bot)

        assert alert is None


class TestVolatilityHandler:
    def _bot(self):
        bot = MagicMock()
        bot.VOLATILITY_PAUSE_MINUTES = TradingBot.VOLATILITY_PAUSE_MINUTES
        bot._volatility_spike_expiry = {}
        bot._volatility_pause_until = None
        order = SimpleNamespace(ticket=555, symbol="XAUUSD", direction="long", price=2400.0)
        bot.pending_order_manager.get_active_orders.return_value = [order]
        bot.pending_order_manager.cancel_order = AsyncMock(return_value=True)
        bot.position_manager = MagicMock()
        return bot

    @pytest.mark.asyncio
    async def test_spike_pauses_entries_and_tightens_protection(self):
        bot = self._bot()
        with patch("trading_bot.main.notify", new=AsyncMock()), \
             patch("trading_bot.api.routes.activity.add_activity"):
            await TradingBot._handle_high_volatility(
                bot, {"message": "XAUUSD: Range 8.0 is 10.0x ATR", "symbols": ["XAUUSD"]}
            )

        assert bot._volatility_pause_until is not None
        assert bot._volatility_pause_until > datetime.now(timezone.utc)
        assert bot.position_manager.volatility_tighten_until == bot._volatility_pause_until

    @pytest.mark.asyncio
    async def test_spike_cancels_pending_orders_on_spiking_symbol(self):
        bot = self._bot()
        with patch("trading_bot.main.notify", new=AsyncMock()), \
             patch("trading_bot.api.routes.activity.add_activity"):
            await TradingBot._handle_high_volatility(
                bot, {"message": "spike", "symbols": ["XAUUSD"]}
            )

        bot.pending_order_manager.get_active_orders.assert_called_with(symbol="XAUUSD")
        bot.pending_order_manager.cancel_order.assert_awaited_once_with(
            555, reason="volatility_spike"
        )

    @pytest.mark.asyncio
    async def test_no_positions_are_closed(self):
        bot = self._bot()
        with patch("trading_bot.main.notify", new=AsyncMock()), \
             patch("trading_bot.api.routes.activity.add_activity"):
            await TradingBot._handle_high_volatility(
                bot, {"message": "spike", "symbols": ["XAUUSD"]}
            )

        bot.order_manager.close_position.assert_not_called()


class TestRunnerEntryPause:
    @pytest.mark.asyncio
    async def test_new_entries_blocked_during_pause_window(self):
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        bot.data_fetcher.get_ohlcv = AsyncMock()

        with patch("trading_bot.api.routes.activity.add_activity"):
            await run_analyze_and_trade(bot, "EURUSD")

        bot.data_fetcher.get_ohlcv.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_pause_does_not_block(self):
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        bot.BLOCKED_PAIRS = ["EURUSD"]  # stop the flow at the next gate

        with patch("trading_bot.api.routes.activity.add_activity"):
            await run_analyze_and_trade(bot, "EURUSD")

        # Reached the blocked-pairs gate => pause did not block
        bot.mt5_client.is_simulation.__bool__.assert_not_called()


class TestGivebackTightening:
    def _position(self, current_price: float) -> Position:
        pos = Position(
            ticket=777,
            symbol="EURUSD",
            direction="long",
            volume=0.10,
            entry_price=1.0000,
            stop_loss=1.0025,
            take_profit=1.1000,  # far TP so near-TP protection stays unarmed
            open_time=datetime.now(timezone.utc),
        )
        pos.initial_sl = 0.9900  # risk = 100 pips
        pos.be_triggered = True
        pos.peak_r_multiple = 1.6
        pos.current_price = current_price
        return pos

    @pytest.mark.asyncio
    async def test_normal_threshold_holds_at_47pct_giveback(self):
        mgr = PositionManager(order_manager=MagicMock())
        pos = self._position(current_price=1.0085)  # 0.85R -> 47% giveback
        action = await mgr._check_profit_protection(pos, pos.current_r_multiple)
        assert action is None

    @pytest.mark.asyncio
    async def test_tightened_threshold_closes_at_47pct_giveback(self):
        order_manager = MagicMock()
        order_manager.close_position = AsyncMock(
            return_value=SimpleNamespace(success=True)
        )
        mgr = PositionManager(order_manager=order_manager)
        mgr.volatility_tighten_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        pos = self._position(current_price=1.0085)
        mgr.positions[pos.ticket] = pos

        action = await mgr._check_profit_protection(pos, pos.current_r_multiple)

        assert action is not None
        assert action["close_reason"] == "giveback_protection"

    @pytest.mark.asyncio
    async def test_expired_tightening_reverts_to_normal(self):
        mgr = PositionManager(order_manager=MagicMock())
        mgr.volatility_tighten_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        pos = self._position(current_price=1.0085)
        action = await mgr._check_profit_protection(pos, pos.current_r_multiple)
        assert action is None
