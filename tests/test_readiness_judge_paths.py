"""
Wave 2 Task 4 — shared fail-closed judge policy for regular and reversal paths.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.execution.risk_manager import RiskManager
from trading_bot.execution.scaling_position_sizer import PositionSizeResult
from trading_bot.llm.claude_client import TradeSignal
from trading_bot.services.trade_judge import JudgeVerdict, JudgeOutcome
from trading_bot.services.trade_reservations import TradeReservationLedger


def _signal(**overrides):
    base = dict(
        direction="long",
        confidence=0.82,
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
        risk_reward=2.0,
        reasoning="Test signal",
        order_type="market",
        trade_type="intraday",
    )
    base.update(overrides)
    return TradeSignal(**base)


def _position_size(lots=0.05):
    return PositionSizeResult(
        lots=lots,
        risk_amount=20.0,
        risk_percent=0.02,
        tier_name="$1K-$2.5K",
        base_lots=0.01,
        adjustments=[],
    )


@pytest.fixture
def counters():
    return {"daily_trades": 0}


@pytest.fixture
def risk_manager():
    return RiskManager(risk_per_trade=0.01, max_daily_risk=0.06)


@pytest.fixture
def ledger(risk_manager, counters):
    return TradeReservationLedger(
        risk_manager=risk_manager,
        get_daily_trades=lambda: counters["daily_trades"],
        set_daily_trades=lambda v: counters.update(daily_trades=v),
    )


@pytest.fixture
def bot(ledger, risk_manager, counters):
    from trading_bot.main import TradingBot

    bot = TradingBot.__new__(TradingBot)
    bot.claude_client = MagicMock()
    bot.claude_client.api_key = "test-key"
    bot.claude_client.async_client = AsyncMock()
    bot.learning_service = None
    bot.mt5_client = AsyncMock()
    bot.mt5_client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(balance=2000.0, equity=2000.0)
    )
    bot.session_analytics = None
    bot.scaling_manager = None
    bot.daily_pnl = 0.0
    bot.daily_trades = counters["daily_trades"]
    bot.reservation_ledger = ledger
    bot.risk_manager = risk_manager
    bot.order_manager = AsyncMock()
    bot.order_manager.place_market_order = AsyncMock()
    bot.order_manager.place_pending_order = AsyncMock()
    return bot


class TestRegularJudgeOrchestrator:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mock_setup,expected_verdict",
        [
            ("absent_client", JudgeVerdict.UNAVAILABLE),
            ("timeout", JudgeVerdict.UNAVAILABLE),
            ("exception", JudgeVerdict.UNAVAILABLE),
            ("malformed", JudgeVerdict.UNAVAILABLE),
            ("approve", JudgeVerdict.APPROVE),
            ("demote", JudgeVerdict.DEMOTE),
            ("reject", JudgeVerdict.REJECT),
        ],
    )
    async def test_run_trade_judge_outcomes(self, bot, mock_setup, expected_verdict):
        if mock_setup == "absent_client":
            bot.claude_client = None
        elif mock_setup == "timeout":
            bot.claude_client.judge_trade = AsyncMock(side_effect=asyncio.TimeoutError())
        elif mock_setup == "exception":
            bot.claude_client.judge_trade = AsyncMock(side_effect=RuntimeError("API down"))
        elif mock_setup == "malformed":
            bot.claude_client.judge_trade = AsyncMock(return_value={"verdict": "MAYBE"})
        elif mock_setup == "approve":
            bot.claude_client.judge_trade = AsyncMock(
                return_value={
                    "verdict": "APPROVE",
                    "reason": "Clean setup",
                    "suggested_entry": None,
                    "risk_flags": [],
                }
            )
        elif mock_setup == "demote":
            bot.claude_client.judge_trade = AsyncMock(
                return_value={
                    "verdict": "DEMOTE",
                    "reason": "Wait for pullback",
                    "suggested_entry": 1.0835,
                    "risk_flags": ["entry_aggressive"],
                }
            )
        else:
            bot.claude_client.judge_trade = AsyncMock(
                return_value={
                    "verdict": "REJECT",
                    "reason": "Known loser pattern",
                    "suggested_entry": None,
                    "risk_flags": ["pattern_match"],
                }
            )

        outcome = await bot._run_trade_judge(
            "EURUSD", _signal(), _position_size(), current_price=1.0850
        )

        assert isinstance(outcome, JudgeOutcome)
        assert outcome.verdict == expected_verdict

    @pytest.mark.asyncio
    async def test_unavailable_blocks_and_releases_reservation(self, bot, ledger, counters):
        bot.claude_client = None
        reservation = ledger.reserve("EURUSD", risk_percent=0.01)
        ledger.commit_risk(reservation)
        assert counters["daily_trades"] == 1

        outcome = await bot._run_trade_judge(
            "EURUSD", _signal(), _position_size(), current_price=1.0850
        )
        blocked = bot._apply_judge_outcome(outcome, reservation)

        assert blocked is True
        assert outcome.blocks_execution()
        assert counters["daily_trades"] == 0

    @pytest.mark.asyncio
    async def test_reject_blocks_and_releases_reservation(self, bot, ledger, counters):
        bot.claude_client.judge_trade = AsyncMock(
            return_value={
                "verdict": "REJECT",
                "reason": "Bad pattern",
                "risk_flags": [],
            }
        )
        reservation = ledger.reserve("EURUSD", risk_percent=0.01)
        ledger.commit_risk(reservation)

        outcome = await bot._run_trade_judge(
            "EURUSD", _signal(), _position_size(), current_price=1.0850
        )
        blocked = bot._apply_judge_outcome(outcome, reservation)

        assert blocked is True
        assert counters["daily_trades"] == 0

    @pytest.mark.asyncio
    async def test_explicit_demote_allows_execution_path(self, bot):
        bot.claude_client.judge_trade = AsyncMock(
            return_value={
                "verdict": "DEMOTE",
                "reason": "Pullback preferred",
                "suggested_entry": 1.0835,
                "risk_flags": [],
            }
        )
        outcome = await bot._run_trade_judge(
            "EURUSD", _signal(), _position_size(), current_price=1.0850
        )
        blocked = bot._apply_judge_outcome(outcome, reservation=None)

        assert blocked is False
        assert outcome.allows_demote_execution()

    @pytest.mark.asyncio
    async def test_approve_allows_execution_path(self, bot):
        bot.claude_client.judge_trade = AsyncMock(
            return_value={
                "verdict": "APPROVE",
                "reason": "Looks good",
                "risk_flags": [],
            }
        )
        outcome = await bot._run_trade_judge(
            "EURUSD", _signal(), _position_size(), current_price=1.0850
        )
        blocked = bot._apply_judge_outcome(outcome, reservation=None)

        assert blocked is False
        assert outcome.verdict == JudgeVerdict.APPROVE


class TestReversalJudgeOrchestrator:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mock_setup,expected_verdict",
        [
            ("absent_client", JudgeVerdict.UNAVAILABLE),
            ("timeout", JudgeVerdict.UNAVAILABLE),
            ("malformed", JudgeVerdict.UNAVAILABLE),
            ("approve", JudgeVerdict.APPROVE),
            ("demote", JudgeVerdict.DEMOTE),
            ("reject", JudgeVerdict.REJECT),
        ],
    )
    async def test_reversal_judge_outcomes(self, bot, mock_setup, expected_verdict):
        if mock_setup == "absent_client":
            bot.claude_client = None
        elif mock_setup == "timeout":
            bot.claude_client.judge_trade = AsyncMock(side_effect=asyncio.TimeoutError())
        elif mock_setup == "malformed":
            bot.claude_client.judge_trade = AsyncMock(return_value={"foo": "bar"})
        elif mock_setup == "approve":
            bot.claude_client.judge_trade = AsyncMock(
                return_value={"verdict": "APPROVE", "reason": "ok", "risk_flags": []}
            )
        elif mock_setup == "demote":
            bot.claude_client.judge_trade = AsyncMock(
                return_value={
                    "verdict": "DEMOTE",
                    "reason": "limit entry",
                    "suggested_entry": 1.0865,
                    "risk_flags": [],
                }
            )
        else:
            bot.claude_client.judge_trade = AsyncMock(
                return_value={"verdict": "REJECT", "reason": "no edge", "risk_flags": []}
            )

        outcome = await bot._run_reversal_trade_judge(
            symbol="EURUSD",
            trade_signal=_signal(direction="short"),
            current_price=1.0850,
            risk_metrics={"risk_reward": 2.0},
        )

        assert outcome.verdict == expected_verdict

    @pytest.mark.asyncio
    async def test_reversal_unavailable_blocks_execution(self, bot, ledger, counters):
        bot.claude_client = None
        reservation = ledger.reserve("EURUSD", signal_id="reversal:1", risk_percent=0.01)
        ledger.commit_risk(reservation)

        outcome = await bot._run_reversal_trade_judge(
            symbol="EURUSD",
            trade_signal=_signal(direction="short"),
            current_price=1.0850,
            risk_metrics={"risk_reward": 2.0},
        )
        blocked = bot._apply_judge_outcome(outcome, reservation)

        assert blocked is True
        assert counters["daily_trades"] == 0
        bot.order_manager.place_market_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_regular_and_reversal_share_adapter(self, bot):
        bot.claude_client.judge_trade = AsyncMock(
            return_value={"verdict": "APPROVE", "reason": "shared", "risk_flags": []}
        )

        regular = await bot._run_trade_judge(
            "EURUSD", _signal(), _position_size(), current_price=1.0850
        )
        reversal = await bot._run_reversal_trade_judge(
            symbol="EURUSD",
            trade_signal=_signal(direction="short"),
            current_price=1.0850,
            risk_metrics={"risk_reward": 2.0},
        )

        assert type(regular) is type(reversal) is JudgeOutcome
        assert regular.verdict == reversal.verdict == JudgeVerdict.APPROVE


class TestReversalDemotePolicy:
    @pytest.fixture
    def demote_bot(self, bot, ledger):
        from trading_bot.services.gate_funnel import GateFunnel

        bot.gate_funnel = MagicMock(spec=GateFunnel)
        bot.gate_funnel.record_decision = AsyncMock(return_value="dec-reversal-demote")
        bot.reservation_ledger = ledger
        bot.mt5_client.get_account_info = AsyncMock(
            return_value=SimpleNamespace(equity=2000.0, balance=2000.0)
        )
        bot.pending_order_manager = AsyncMock()
        bot.pending_order_manager.add_order = AsyncMock()
        bot.position_manager = MagicMock()
        bot.position_manager.get_positions_by_symbol = MagicMock(return_value=[])
        bot.data_fetcher = AsyncMock()
        bot.data_fetcher.get_ohlcv = AsyncMock(
            return_value=__import__("pandas").DataFrame(
                {"close": [1.0850], "open": [1.0848], "high": [1.0855], "low": [1.0845]}
            )
        )
        bot._generate_chart_image = AsyncMock(return_value="base64chart")
        bot.context_builder = MagicMock()
        bot.context_builder.get_ict_context = MagicMock(return_value="ctx")
        bot._reversal_cooldowns = {}
        bot.daily_trades = 0
        bot.daily_pnl = 0.0
        bot.learning_service = None
        bot.scaling_manager = None
        bot.order_manager = AsyncMock()
        bot._place_pending_with_final_risk = AsyncMock(
            return_value=MagicMock(success=True, ticket=99001, order_id=99001)
        )
        bot._place_market_with_final_risk = AsyncMock()
        bot._record_terminal_decision = AsyncMock(return_value="dec-id")
        bot._check_drawdown_circuit_breaker = AsyncMock(return_value=False)
        return bot

    def _closed_position(self):
        return SimpleNamespace(
            symbol="EURUSD",
            direction="long",
            ticket=12345,
            entry_price=1.0850,
            current_price=1.0860,
            peak_r_multiple=1.5,
            current_r_multiple=0.8,
            unrealized_pnl=10.0,
            close_reason="giveback",
            trade_type="intraday",
        )

    @pytest.mark.asyncio
    async def test_reversal_demote_uses_pending_not_full_market(self, demote_bot):
        from trading_bot.llm.claude_client import AnalysisResult, TradeSignal

        demote_bot.claude_client.analyze_chart_async = AsyncMock(
            return_value=AnalysisResult(
                signal=TradeSignal(
                    direction="short",
                    confidence=0.82,
                    entry_price=1.0860,
                    stop_loss=1.0900,
                    take_profit=1.0800,
                    risk_reward=1.5,
                    reasoning="structural reversal",
                    order_type="market",
                    trade_type="intraday",
                ),
                raw_response="{}",
                analysis_summary="reversal",
                key_levels={},
                warnings=[],
            )
        )
        demote_bot.claude_client.judge_trade = AsyncMock(
            return_value={
                "verdict": "DEMOTE",
                "reason": "wait for pullback",
                "suggested_entry": 1.0870,
                "risk_flags": [],
            }
        )

        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.max_daily_trades = 10
            mock_settings.timeframes.execution_tf = "M15"
            mock_settings.timeframes.execution_tf_candles = 100
            await demote_bot._analyze_reversal_entry(self._closed_position())

        demote_bot._place_market_with_final_risk.assert_not_called()
        demote_bot._place_pending_with_final_risk.assert_called_once()
        call_kwargs = demote_bot._place_pending_with_final_risk.call_args.kwargs
        assert call_kwargs["lots"] < 0.01 or call_kwargs["order_type"] in (
            "sell_limit",
            "buy_limit",
        )

    @pytest.mark.asyncio
    async def test_reversal_demote_records_judge_demote(self, demote_bot):
        from trading_bot.llm.claude_client import AnalysisResult, TradeSignal

        demote_bot.claude_client.analyze_chart_async = AsyncMock(
            return_value=AnalysisResult(
                signal=TradeSignal(
                    direction="short",
                    confidence=0.82,
                    entry_price=1.0860,
                    stop_loss=1.0900,
                    take_profit=1.0800,
                    risk_reward=1.5,
                    reasoning="structural reversal",
                    order_type="market",
                    trade_type="intraday",
                ),
                raw_response="{}",
                analysis_summary="reversal",
                key_levels={},
                warnings=[],
            )
        )
        demote_bot.claude_client.judge_trade = AsyncMock(
            return_value={
                "verdict": "DEMOTE",
                "reason": "limit entry preferred",
                "suggested_entry": 1.0870,
                "risk_flags": ["entry_aggressive"],
            }
        )

        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.max_daily_trades = 10
            mock_settings.timeframes.execution_tf = "M15"
            mock_settings.timeframes.execution_tf_candles = 100
            await demote_bot._analyze_reversal_entry(self._closed_position())

        demote_bot._record_terminal_decision.assert_any_call(
            "judge_demote",
            "EURUSD",
            direction="short",
            entry=pytest.approx(1.0870, rel=1e-3),
            sl=pytest.approx(1.0900, rel=1e-3),
            tp=pytest.approx(1.0800, rel=1e-3),
            confidence=0.82,
            reason="limit entry preferred",
            judge_verdict="DEMOTE",
            details={
                "risk_flags": ["entry_aggressive"],
                "order_type": "sell_limit",
                "reversal": True,
            },
        )

    @pytest.mark.asyncio
    async def test_reversal_judge_reject_records_decision(self, demote_bot):
        from trading_bot.llm.claude_client import AnalysisResult, TradeSignal

        demote_bot.claude_client.analyze_chart_async = AsyncMock(
            return_value=AnalysisResult(
                signal=TradeSignal(
                    direction="short",
                    confidence=0.82,
                    entry_price=1.0860,
                    stop_loss=1.0900,
                    take_profit=1.0800,
                    risk_reward=1.5,
                    reasoning="structural reversal",
                    order_type="market",
                    trade_type="intraday",
                ),
                raw_response="{}",
                analysis_summary="reversal",
                key_levels={},
                warnings=[],
            )
        )
        demote_bot.claude_client.judge_trade = AsyncMock(
            return_value={
                "verdict": "REJECT",
                "reason": "weak reversal",
                "risk_flags": ["no_displacement"],
            }
        )
        demote_bot._place_market_with_final_risk = AsyncMock()
        demote_bot._place_pending_with_final_risk = AsyncMock()

        with patch("trading_bot.main.settings") as mock_settings:
            mock_settings.trading.max_daily_trades = 10
            mock_settings.timeframes.execution_tf = "M15"
            mock_settings.timeframes.execution_tf_candles = 100
            await demote_bot._analyze_reversal_entry(self._closed_position())

        demote_bot._record_terminal_decision.assert_any_call(
            "judge_reject",
            "EURUSD",
            direction="short",
            entry=pytest.approx(1.0860),
            sl=pytest.approx(1.0900),
            tp=pytest.approx(1.0800),
            confidence=0.82,
            reason="weak reversal",
            judge_verdict="REJECT",
            details={"risk_flags": ["no_displacement"]},
        )
        demote_bot._place_market_with_final_risk.assert_not_called()
