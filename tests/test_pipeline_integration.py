"""Mocked end-to-end integration tests for pipeline runtime paths."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from trading_bot.execution.trade_fill_handler import TradeFillHandler
from trading_bot.services.claude_analysis_stage import ClaudeAnalysisStage, ClaudeStageResult


def _df(rows: int = 30) -> pd.DataFrame:
    data = []
    for i in range(rows):
        p = 1.08 + i * 0.0001
        data.append(
            {"open": p, "high": p + 0.0010, "low": p - 0.0005, "close": p + 0.0002}
        )
    return pd.DataFrame(data)


def _minimal_bot() -> MagicMock:
    bot = MagicMock()
    bot.context_builder.get_ict_context.return_value = "fixture-context"
    bot.mt5_client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(equity=2000.0, balance=2000.0)
    )
    bot.mt5_client.get_symbol_info = AsyncMock(return_value=None)
    bot.position_sizer = None
    bot.scaling_manager = None
    bot.regime_classifier = None
    bot.session_analytics = None
    bot.news_service = None
    bot.correlation_service = None
    bot.learning_service = None
    bot.firecrawl_service = None
    bot.precious_metals_analyzer = None
    bot.data_fetcher = None
    bot.silver_bullet_detector = None
    bot._last_mtf_results = {}
    bot._last_signal_per_symbol = {}
    bot._generate_chart_image = AsyncMock(return_value=None)
    bot._print_analysis_summary = MagicMock()
    bot._save_signal = MagicMock()
    bot._record_terminal_decision = AsyncMock()
    bot.PRECIOUS_METALS = []
    return bot


def _claude_result(direction: str, **signal_kwargs):
    signal = SimpleNamespace(
        direction=direction,
        confidence=signal_kwargs.pop("confidence", 0.82),
        entry_price=signal_kwargs.pop("entry_price", 1.0850),
        stop_loss=signal_kwargs.pop("stop_loss", 1.0840),
        take_profit=signal_kwargs.pop("take_profit", 1.0950),
        reasoning=signal_kwargs.pop("reasoning", "fixture setup"),
        trade_type=signal_kwargs.pop("trade_type", "intraday"),
        market_structure=signal_kwargs.pop("market_structure", "bullish"),
        risk_reward=signal_kwargs.pop("risk_reward", 2.5),
        order_type=signal_kwargs.pop("order_type", "market"),
        **signal_kwargs,
    )
    return SimpleNamespace(signal=signal, raw_response="fixture")


def _run_stage_kwargs(symbol: str = "EURUSD"):
    fvg = SimpleNamespace(bullish_fvgs=[], bearish_fvgs=[])
    ob = SimpleNamespace(bullish_obs=[], bearish_obs=[])
    return dict(
        symbol=symbol,
        df=_df(),
        analysis_results={
            "volume": {"relative_volume": 1.0},
            "fvg": fvg,
            "order_blocks": ob,
        },
        chart_base64="base64-chart",
        additional_charts=[],
        vp_data=None,
        bar_extreme_results={},
        mtf_dfs={},
        pd_analysis=None,
        mtf_result=None,
        dxy_confirmation=None,
        retail_contrarian=None,
        vix_risk_mode=None,
        currency_strength_recommendation=None,
        current_price=1.0850,
    )


class TestClaudeStageRunStage:
    @pytest.mark.asyncio
    async def test_run_stage_success_returns_claude_stage_result(self):
        bot = _minimal_bot()
        stage = ClaudeAnalysisStage(claude_client=MagicMock())
        with patch.object(
            stage,
            "analyze",
            AsyncMock(return_value=_claude_result("long")),
        ), patch(
            "trading_bot.services.claude_analysis_stage.asyncio.create_task"
        ), patch(
            "trading_bot.services.claude_analysis_stage.broadcast_analysis_update",
            new=AsyncMock(),
        ), patch(
            "trading_bot.api.routes.activity.add_activity"
        ):
            result = await stage.run_stage(bot, **_run_stage_kwargs())

        assert isinstance(result, ClaudeStageResult)
        assert result.stop_pipeline is False
        assert result.trade_signal.direction == "long"
        bot._save_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stage_no_trade_stops_pipeline(self):
        bot = _minimal_bot()
        stage = ClaudeAnalysisStage(claude_client=MagicMock())
        with patch.object(
            stage,
            "analyze",
            AsyncMock(return_value=_claude_result("no_trade", confidence=0.0)),
        ), patch(
            "trading_bot.services.claude_analysis_stage.asyncio.create_task"
        ), patch(
            "trading_bot.services.claude_analysis_stage.broadcast_analysis_update",
            new=AsyncMock(),
        ), patch(
            "trading_bot.api.routes.activity.add_activity"
        ):
            result = await stage.run_stage(bot, **_run_stage_kwargs())

        assert isinstance(result, ClaudeStageResult)
        assert result.stop_pipeline is True
        assert result.trade_signal.direction == "no_trade"
        bot._record_terminal_decision.assert_awaited_once()
        call_args = bot._record_terminal_decision.await_args
        assert call_args.args[0] == "no_trade"


class TestTradeFillHandlerReservationRelease:
    @pytest.mark.asyncio
    async def test_execution_failure_releases_reservation_when_not_reconciled(self):
        bot = MagicMock()
        bot._reconcile_fill_after_ambiguous_order = AsyncMock(return_value=None)
        bot._record_terminal_decision = AsyncMock()
        bot._release_trade_reservation = MagicMock()
        reservation = object()
        result = SimpleNamespace(success=False, message="broker rejected")

        with patch("trading_bot.api.routes.activity.add_activity"):
            await TradeFillHandler.handle_result(
                bot,
                symbol="EURUSD",
                result=result,
                order_type="market",
                entry_price=1.0850,
                current_price=1.0850,
                trade_signal=SimpleNamespace(
                    direction="long",
                    entry_price=1.0850,
                    stop_loss=1.0840,
                    take_profit=1.0950,
                    confidence=0.8,
                ),
                position_size=SimpleNamespace(lots=0.1),
                size_result=SimpleNamespace(risk_percent=0.01),
                account_info=SimpleNamespace(equity=2000.0),
                trade_reservation=reservation,
                signal_hash="hash",
                final_sl=1.0840,
                final_tp=1.0950,
                final_entry=1.0850,
                judge_verdict={"verdict": "APPROVE"},
                confluence_factors=[],
                confluence_count=0,
                setup_grade="A",
                take_profit_levels=None,
                save_trade_to_db=AsyncMock(),
            )

        bot._release_trade_reservation.assert_called_once_with(reservation)
        bot._record_terminal_decision.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execution_failure_keeps_reservation_when_reconciled(self):
        bot = MagicMock()
        bot._reconcile_fill_after_ambiguous_order = AsyncMock(return_value=12345)
        bot._record_terminal_decision = AsyncMock()
        bot._release_trade_reservation = MagicMock()
        reservation = object()
        result = SimpleNamespace(success=False, message="timeout")

        with patch("trading_bot.api.routes.activity.add_activity"):
            await TradeFillHandler.handle_result(
                bot,
                symbol="EURUSD",
                result=result,
                order_type="market",
                entry_price=1.0850,
                current_price=1.0850,
                trade_signal=SimpleNamespace(
                    direction="long",
                    entry_price=1.0850,
                    stop_loss=1.0840,
                    take_profit=1.0950,
                    confidence=0.8,
                ),
                position_size=SimpleNamespace(lots=0.1),
                size_result=SimpleNamespace(risk_percent=0.01),
                account_info=SimpleNamespace(equity=2000.0),
                trade_reservation=reservation,
                signal_hash="hash",
                final_sl=1.0840,
                final_tp=1.0950,
                final_entry=1.0850,
                judge_verdict={"verdict": "APPROVE"},
                confluence_factors=[],
                confluence_count=0,
                setup_grade="A",
                take_profit_levels=None,
                save_trade_to_db=AsyncMock(),
            )

        bot._release_trade_reservation.assert_not_called()


class TestPhasedLiveGatePath:
    def test_run_phased_live_matches_replay_complete(self):
        from trading_bot.backtesting.replay import run_phased_live_gates
        from trading_bot.services.entry_gates import ZoneGateSettings
        from trading_bot.services.post_claude_gates import (
            PostClaudeGateInput,
            run_post_claude_gates,
        )
        from trading_bot.services.signal_normalizer import NormalizedSignal

        sig = SimpleNamespace(
            direction="long",
            confidence=0.82,
            entry_price=1.0850,
            stop_loss=1.0840,
            take_profit=1.0950,
            trade_type="intraday",
            order_type="market",
        )
        inp = PostClaudeGateInput(
            symbol="EURUSD",
            trade_signal=sig,
            norm=NormalizedSignal(
                entry=1.0850, sl=1.0840, tp=1.0950, direction="long", rejected=False
            ),
            market_data={"d1_bias": "bullish"},
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
                "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
            },
            current_price=1.0850,
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
            is_kill_zone=True,
            session_name="london",
            last_signal_direction={},
            direction_flipped=False,
            df=_df(),
        )
        replay = run_post_claude_gates(inp, stop_after="complete")
        live = run_phased_live_gates(inp)
        assert replay.blocked == live.blocked
        assert replay.gate_id == live.gate_id
        assert replay.gate_path == live.gate_path
