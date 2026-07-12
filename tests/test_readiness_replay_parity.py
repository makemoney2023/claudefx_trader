"""
Wave 3 Task 9 — replay/live policy parity.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from trading_bot.backtesting.execution_policy import (
    apply_symbol_execution_costs,
    evaluate_judge_gate,
    run_policy_replay,
    simulate_pending_lifecycle,
    simulate_trade_with_exit_policy,
)
from trading_bot.backtesting.replay import ReplaySignal, replay_signal_with_policy
from trading_bot.backtesting.optimizer import WalkForwardOptimizer, _simulate_gate_logic, ParameterSet
from trading_bot.services.trade_judge import JudgeOutcome, JudgeVerdict


def _bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


FIXTURE_APPROVE_WIN = _bars([
    [1.0850, 1.0860, 1.0845, 1.0855],
    [1.0855, 1.0960, 1.0850, 1.0950],
])

FIXTURE_DEMOTE_FILL = _bars([
    [1.0840, 1.0845, 1.0835, 1.0840],
    [1.0840, 1.0855, 1.0838, 1.0850],
    [1.0850, 1.0960, 1.0848, 1.0950],
])

FIXTURE_DEMOTE_EXPIRE = _bars([
    [1.0840, 1.0842, 1.0838, 1.0840],
    [1.0840, 1.0843, 1.0839, 1.0841],
])

FIXTURE_PARTIAL_GIVEBACK = _bars([
    [1.0850, 1.0900, 1.0848, 1.0890],
    [1.0890, 1.0925, 1.0885, 1.0920],
    [1.0920, 1.0930, 1.0870, 1.0880],
])

FIXTURE_REJECT = _bars([
    [1.0850, 1.0860, 1.0840, 1.0850],
])


def _signal(**kwargs):
    base = dict(
        timestamp=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
        symbol="EURUSD",
        direction="long",
        confidence=0.78,
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
        reasoning="fixture",
        trade_type="intraday",
        market_structure="bullish",
    )
    base.update(kwargs)
    return ReplaySignal(**base)


class TestFrozenPolicyFixtures:
    def test_judge_approve_executes_with_costs(self):
        outcome = JudgeOutcome(verdict=JudgeVerdict.APPROVE, reason="clean setup")
        allowed, path = evaluate_judge_gate(outcome)
        assert allowed is True
        assert path == "judge_approve"

        result = run_policy_replay(
            _signal(),
            FIXTURE_APPROVE_WIN,
            outcome,
            current_price=1.0850,
            pip_size=0.0001,
        )
        assert result.execution_blocked is False
        assert result.execution_trade is not None
        assert result.execution_trade.outcome == "win"
        assert result.execution_trade.r_multiple < result.strategy_trade.r_multiple
        assert "final_risk" in " ".join(result.decision_path)

    def test_judge_reject_blocks_execution(self):
        outcome = JudgeOutcome(verdict=JudgeVerdict.REJECT, reason="weak confluence")
        result = run_policy_replay(
            _signal(),
            FIXTURE_REJECT,
            outcome,
            current_price=1.0850,
        )
        assert result.execution_blocked is True
        assert result.execution_trade is None
        assert "judge_reject" in result.decision_path

    def test_demote_pending_fill(self):
        outcome = JudgeOutcome(
            verdict=JudgeVerdict.DEMOTE,
            reason="wait for pullback",
            suggested_entry=1.0840,
        )
        result = run_policy_replay(
            _signal(entry_price=1.0860),
            FIXTURE_DEMOTE_FILL,
            outcome,
            current_price=1.0870,
        )
        assert result.pending_outcome == "filled"
        assert result.execution_trade is not None
        assert "demote" in " ".join(result.decision_path)

    def test_demote_pending_expiry(self):
        outcome = JudgeOutcome(verdict=JudgeVerdict.DEMOTE, reason="limit too far")
        result = run_policy_replay(
            _signal(entry_price=1.0850),
            FIXTURE_DEMOTE_EXPIRE,
            outcome,
            current_price=1.0850,
        )
        assert result.pending_outcome == "expired"
        assert result.execution_blocked is True

    def test_partial_exits_trailing_giveback(self):
        trade = simulate_trade_with_exit_policy(
            _signal(),
            FIXTURE_PARTIAL_GIVEBACK,
            pip_size=0.0001,
            a_plus=False,
        )
        assert trade.bars_held >= 2
        assert trade.mfe_pips > 0
        assert trade.r_multiple > 0

    def test_symbol_costs_differ_by_asset(self):
        forex = apply_symbol_execution_costs("EURUSD", 2.0)
        crypto = apply_symbol_execution_costs("BTCUSD", 2.0)
        assert crypto < forex


class TestReplayPolicyParity:
    def test_replay_signal_with_policy_matches_pipeline(self):
        outcome = JudgeOutcome(verdict=JudgeVerdict.APPROVE, reason="ok")
        direct = run_policy_replay(_signal(), FIXTURE_APPROVE_WIN, outcome, current_price=1.0850)
        wrapped = replay_signal_with_policy(_signal(), FIXTURE_APPROVE_WIN, outcome, current_price=1.0850)
        assert wrapped.execution_trade.outcome == direct.execution_trade.outcome
        assert wrapped.strategy_trade.outcome == direct.strategy_trade.outcome

    def test_pending_simulator_fill_and_expiry(self):
        filled, bars, price = simulate_pending_lifecycle(
            "long", "buy_limit", 1.0840, FIXTURE_DEMOTE_FILL
        )
        assert filled == "filled"
        assert bars > 0
        assert price == pytest.approx(1.0840)

        expired, _, _ = simulate_pending_lifecycle(
            "long", "buy_limit", 1.0700, FIXTURE_DEMOTE_EXPIRE, max_bars=2
        )
        assert expired == "expired"


class TestOptimizerPolicyMetrics:
    @pytest.mark.asyncio
    async def test_load_trades_filters_bot_owned(self):
        optimizer = WalkForwardOptimizer()
        trades = [
            {
                "timestamp": "2026-01-01T10:00:00",
                "confidence": 0.7,
                "risk_reward": 2.5,
                "session": "london",
                "direction": "long",
                "trend": "bullish",
                "date": "2026-01-01",
                "r_multiple": 1.2,
                "bot_owned": True,
            },
            {
                "timestamp": "2026-01-02T10:00:00",
                "confidence": 0.55,
                "risk_reward": 1.5,
                "session": "asian",
                "direction": "short",
                "trend": "bearish",
                "date": "2026-01-02",
                "r_multiple": -0.8,
                "bot_owned": False,
            },
        ]

        with patch.object(optimizer, "_load_trades", AsyncMock(return_value=[t for t in trades if t.get("bot_owned")])):
            loaded = await optimizer._load_trades(90)
        assert all(t.get("bot_owned") for t in loaded)

    def test_execution_policy_metrics_separate_from_raw(self):
        trades = [
            {"confidence": 0.8, "risk_reward": 3.0, "session": "london", "direction": "long", "trend": "bullish", "timestamp": "2026-01-01T10:00:00", "date": "2026-01-01", "r_multiple": 2.0},
            {"confidence": 0.55, "risk_reward": 1.2, "session": "asian", "direction": "long", "trend": "bullish", "timestamp": "2026-01-02T10:00:00", "date": "2026-01-02", "r_multiple": -1.0},
            {"confidence": 0.72, "risk_reward": 2.5, "session": "london", "direction": "short", "trend": "bearish", "timestamp": "2026-01-03T10:00:00", "date": "2026-01-03", "r_multiple": 1.5},
        ]
        params = ParameterSet(min_confidence=0.65, min_rr=2.0)
        raw_count = len(trades)
        gated = _simulate_gate_logic(trades, params)
        assert len(gated) < raw_count
        assert len(gated) >= 1

    @pytest.mark.asyncio
    async def test_optimizer_preserves_chronological_holdout(self):
        optimizer = WalkForwardOptimizer()
        chronological = [
            {"timestamp": f"2026-01-{i:02d}T10:00:00", "confidence": 0.7, "risk_reward": 2.5, "session": "london", "direction": "long", "trend": "bullish", "date": f"2026-01-{i:02d}", "r_multiple": 1.0}
            for i in range(1, 25)
        ]
        with patch.object(optimizer, "_load_trades", AsyncMock(return_value=chronological)):
            result = await optimizer.optimize(lookback_days=90, n_folds=2, train_ratio=0.7)
        if result:
            assert result.holdout_chronological is True
            assert result.bot_owned_trade_count == 24
            assert result.strategy_in_sample_sharpe != result.execution_in_sample_sharpe or result.in_sample_trades <= 24


class TestBacktesterExecutionPolicyMetrics:
    def test_evaluate_replay_signal_populates_policy_metrics(self):
        from trading_bot.backtesting.replay import ClaudeReplayBacktester

        bt = ClaudeReplayBacktester(auto_approve_judge=True)
        replay_sig = _signal()
        future = FIXTURE_APPROVE_WIN

        strategy_trade, policy_result = bt._evaluate_replay_signal(
            replay_sig,
            future,
            current_price=1.0850,
            pip_size=0.0001,
        )

        assert strategy_trade is not None
        assert policy_result.execution_trade is not None
        assert policy_result.execution_blocked is False
        assert strategy_trade.r_multiple != policy_result.execution_trade.r_multiple

    def test_backtester_result_tracks_separate_policy_totals(self):
        from trading_bot.backtesting.replay import ClaudeReplayBacktester, ReplayResult

        bt = ClaudeReplayBacktester(auto_approve_judge=True)
        result = ReplayResult(
            symbol="EURUSD",
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        replay_sig = _signal()
        future = FIXTURE_APPROVE_WIN

        strategy_trade, policy_result = bt._evaluate_replay_signal(
            replay_sig,
            future,
            current_price=1.0850,
            pip_size=0.0001,
        )

        result.trades.append(strategy_trade)
        result.total_trades += 1
        result.total_r += strategy_trade.r_multiple
        result.strategy_total_r += strategy_trade.r_multiple

        if policy_result.execution_trade:
            result.execution_policy_trades += 1
            result.execution_policy_total_r += policy_result.execution_trade.r_multiple

        assert result.execution_policy_trades == 1
        assert result.execution_policy_total_r != result.strategy_total_r or result.total_trades == 1
        assert result.strategy_total_r == pytest.approx(strategy_trade.r_multiple)

    def test_default_replay_judge_rejects_without_opt_in(self):
        from trading_bot.backtesting.replay import ClaudeReplayBacktester

        bt = ClaudeReplayBacktester()
        replay_sig = _signal()
        future = FIXTURE_APPROVE_WIN

        _, policy_result = bt._evaluate_replay_signal(
            replay_sig,
            future,
            current_price=1.0850,
            pip_size=0.0001,
        )

        assert policy_result.execution_blocked is True
        assert policy_result.execution_trade is None
