"""Post-APPROVE execution safety: broker failures and non-critical I/O isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.config import get_symbol_spec
from trading_bot.execution.order_manager import OrderResult, OrderStatus
from trading_bot.execution.risk_manager import RiskManager
from trading_bot.execution.trade_execution import ExecutionCoordinator
from trading_bot.main import TradingBot
from trading_bot.services.analyze_and_trade_runner import safe_persist_judge_signal


def _bot_for_place():
    bot = TradingBot.__new__(TradingBot)
    bot.risk_manager = RiskManager(risk_per_trade=0.02, max_daily_risk=0.06)
    bot.mt5_client = AsyncMock()
    bot.mt5_client.get_tick = AsyncMock(
        return_value=SimpleNamespace(ask=1.0850, bid=1.0848)
    )
    bot.order_manager = AsyncMock()
    return bot


class TestMarketBrokerFailureNotFinalRisk:
    @pytest.mark.asyncio
    async def test_place_market_returns_failed_order_result(self):
        bot = _bot_for_place()
        failed = OrderResult(
            success=False,
            order_id=None,
            ticket=None,
            status=OrderStatus.REJECTED,
            message="TP rejected by broker",
        )
        bot.order_manager.place_market_order = AsyncMock(return_value=failed)
        bot._enforce_final_risk_before_order = MagicMock(
            return_value=(0.01, 0.01, None)
        )

        result = await bot._place_market_with_final_risk(
            symbol="EURUSD",
            direction="short",
            lots=0.01,
            stop_loss=1.0900,
            take_profit=1.0750,
            account_equity=1000.0,
            symbol_spec=get_symbol_spec("EURUSD"),
        )

        assert result is not None
        assert result.success is False
        assert "TP rejected" in result.message

    @pytest.mark.asyncio
    async def test_place_market_still_returns_none_on_risk_block(self):
        bot = _bot_for_place()
        bot._enforce_final_risk_before_order = MagicMock(
            return_value=(0.0, 0.0, "risk exceeds cap")
        )
        bot.order_manager.place_market_order = AsyncMock()

        result = await bot._place_market_with_final_risk(
            symbol="EURUSD",
            direction="short",
            lots=0.50,
            stop_loss=1.0900,
            take_profit=1.0750,
            account_equity=1000.0,
            symbol_spec=get_symbol_spec("EURUSD"),
        )

        assert result is None
        bot.order_manager.place_market_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_passes_broker_failure_not_final_risk_block(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "dry_run", False)

        failed = OrderResult(
            success=False,
            order_id=None,
            ticket=None,
            status=OrderStatus.REJECTED,
            message="requote / no prices",
        )
        bot = MagicMock()
        bot._enforce_final_risk_before_order = MagicMock(
            return_value=(0.01, 0.01, None)
        )
        bot._place_market_with_final_risk = AsyncMock(return_value=failed)

        trade_signal = SimpleNamespace(
            direction="short",
            entry_price=1.0850,
            stop_loss=1.0900,
            take_profit=1.0750,
            confidence=0.75,
            amd_phase="distribution",
            order_type="market",
        )
        position_size = SimpleNamespace(lots=0.01)
        size_result = SimpleNamespace(lots=0.01, risk_percent=0.01)
        account_info = SimpleNamespace(equity=1000.0)

        with patch(
            "trading_bot.execution.trade_execution.verify_post_sizing_risk",
            return_value=(0.01, 0.01, None),
        ):
            result = await ExecutionCoordinator().execute(
                bot=bot,
                symbol="EURUSD",
                trade_signal=trade_signal,
                order_type="market",
                entry_price=1.0850,
                current_price=1.0850,
                position_size=position_size,
                size_result=size_result,
                account_info=account_info,
                market_data={"atr_14": 0.001},
                is_crypto=False,
                trade_reservation=None,
            )

        assert result.blocked is False
        assert result.gate_id != "final_risk_block"
        assert result.broker_result is failed
        assert result.broker_result.success is False


class TestSafePersistJudgeSignal:
    @pytest.mark.asyncio
    async def test_persist_failure_does_not_raise(self):
        async def boom(**_kwargs):
            raise RuntimeError("db down")

        # Must not raise — execution path continues after APPROVE
        await safe_persist_judge_signal(
            boom,
            symbol="XAUUSD",
            direction="short",
            confidence=0.7,
        )

    @pytest.mark.asyncio
    async def test_persist_success_awaits_save(self):
        save = AsyncMock()
        await safe_persist_judge_signal(
            save,
            symbol="XAUUSD",
            direction="short",
            confidence=0.7,
        )
        save.assert_awaited_once()

    def test_runner_uses_safe_persist_on_approve_path(self):
        import inspect
        from trading_bot.services import analyze_and_trade_runner

        src = inspect.getsource(analyze_and_trade_runner.run_analyze_and_trade)
        assert "safe_persist_judge_signal" in src
        assert src.count("safe_persist_judge_signal") >= 2  # APPROVE + DEMOTE at least
