"""Wiring tests for edge policies: runner gate/sizing, exit overrides,
funnel recommendations, and fill telemetry."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot.execution.position_manager import Position, PositionManager
from trading_bot.main import TradingBot


class TestExitOverrideWiring:
    def _position(self, symbol="EURUSD"):
        return Position(
            ticket=1,
            symbol=symbol,
            direction="long",
            volume=0.10,
            entry_price=1.0000,
            stop_loss=0.9900,
            take_profit=1.0300,
            open_time=datetime.now(timezone.utc),
        )

    def test_default_triggers_without_override(self):
        mgr = PositionManager()
        pos = self._position()
        assert mgr._tp1_trigger_r(pos) == mgr.break_even_trigger_r
        assert mgr._tp2_trigger_r(pos) == mgr.trailing_start_r

    def test_override_changes_triggers_for_symbol_only(self):
        mgr = PositionManager()
        mgr.set_exit_overrides("EURUSD", tp1_r=1.2, tp2_r=2.4)
        assert mgr._tp1_trigger_r(self._position("EURUSD")) == 1.2
        assert mgr._tp2_trigger_r(self._position("EURUSD")) == 2.4
        assert mgr._tp1_trigger_r(self._position("XAUUSD")) == mgr.break_even_trigger_r

    @pytest.mark.asyncio
    async def test_refresh_applies_overrides_from_excursion(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "symbols", ["EURUSD"])
        bot = MagicMock()
        bot.position_manager = PositionManager()

        excursion = SimpleNamespace(median_winner_mfe_r=3.0, winner_sample=20)
        with_mock = AsyncMock(return_value=excursion)
        monkeypatch.setattr(
            "trading_bot.analysis.excursion_analysis.ExcursionAnalyzer.compute",
            with_mock,
        )

        await TradingBot._refresh_exit_overrides(bot)

        overrides = bot.position_manager.symbol_exit_overrides.get("EURUSD")
        assert overrides == {"tp1_r": 1.2, "tp2_r": 2.4}


class TestRunnerWiring:
    def test_runner_wires_playbook_gate_and_ensemble_sizing(self):
        from trading_bot.services import analyze_and_trade_runner

        src = inspect.getsource(analyze_and_trade_runner)
        assert "evaluate_playbook_gate" in src
        assert "mech_agreement_size_multiplier" in src
        assert "playbook_block" in src

    def test_fill_handler_records_slippage_and_regime(self):
        from trading_bot.execution import trade_fill_handler

        src = inspect.getsource(trade_fill_handler)
        assert "compute_slippage" in src
        assert "slippage" in src
        assert "regime" in src


class TestFunnelRecommendations:
    def test_analytics_includes_tuning_recommendations(self):
        from trading_bot.services.gate_funnel import GateFunnel

        src = inspect.getsource(GateFunnel.get_aggregate_analytics)
        assert "tuning_recommendations" in src


class TestSetupStats:
    def test_learning_service_exposes_structured_stats(self):
        from trading_bot.services.trade_learning_service import TradeLearningService

        assert hasattr(TradeLearningService, "get_setup_stats")
        assert inspect.iscoroutinefunction(TradeLearningService.get_setup_stats)
