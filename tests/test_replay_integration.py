"""Integration tests for replay/live gate parity and Claude judge wiring."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from trading_bot.backtesting.replay import ClaudeReplayBacktester
from trading_bot.services.entry_gates import ZoneGateSettings
from trading_bot.services.post_claude_gates import PostClaudeGateInput, run_post_claude_gates
from trading_bot.services.signal_normalizer import NormalizedSignal
from trading_bot.services.trade_judge import JudgeVerdict


def _signal(**kwargs):
    base = dict(
        direction="long",
        confidence=0.82,
        entry_price=1.0850,
        stop_loss=1.0840,
        take_profit=1.0950,
        trade_type="intraday",
        reasoning="fixture",
        order_type="market",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _norm(**kwargs):
    base = dict(entry=1.0850, sl=1.0840, tp=1.0950, direction="long", rejected=False)
    base.update(kwargs)
    return NormalizedSignal(**base)


def _df():
    rows = []
    for i in range(30):
        p = 1.08 + i * 0.0001
        rows.append({"open": p, "high": p + 0.0010, "low": p - 0.0005, "close": p + 0.0002})
    return pd.DataFrame(rows)


def _gate_input(**kwargs):
    sig = _signal()
    base = dict(
        symbol="EURUSD",
        trade_signal=sig,
        norm=_norm(),
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
    base.update(kwargs)
    return PostClaudeGateInput(**base)


class TestLiveReplayGatePathParity:
    def test_phased_live_matches_replay_complete(self):
        """Live phased gates and replay one-shot should produce identical paths."""
        inp = _gate_input()
        replay = run_post_claude_gates(inp, stop_after="complete")

        price = run_post_claude_gates(inp, stop_after="price")
        entry = run_post_claude_gates(
            inp,
            start_at="entry",
            stop_after="entry",
            gate_path=price.gate_path,
            carry=price,
        )
        live = run_post_claude_gates(
            inp,
            start_at="permission",
            stop_after="complete",
            ctx=entry.pipeline_ctx,
            gate_path=entry.gate_path,
            carry=entry,
        )

        assert replay.blocked == live.blocked
        assert replay.gate_id == live.gate_id
        assert replay.gate_path == live.gate_path
        assert replay.confidence == pytest.approx(live.confidence)

    def test_flip_guard_blocks_same_in_both_paths(self):
        last = {
            "EURUSD": (
                "long",
                datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        }
        inp = _gate_input(
            norm=_norm(direction="short"),
            trade_signal=_signal(direction="short", confidence=0.75),
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[], bearish_fvgs=[1]),
                "order_blocks": SimpleNamespace(bullish_obs=[], bearish_obs=[1]),
            },
            last_signal_direction=last,
        )
        replay = run_post_claude_gates(inp, stop_after="complete")
        price = run_post_claude_gates(inp, stop_after="price")
        entry = run_post_claude_gates(
            inp, start_at="entry", stop_after="entry", gate_path=price.gate_path, carry=price
        )
        live = run_post_claude_gates(
            inp,
            start_at="permission",
            stop_after="complete",
            ctx=entry.pipeline_ctx,
            gate_path=entry.gate_path,
            carry=entry,
        )
        assert replay.blocked is True
        assert live.blocked is True
        assert replay.gate_id == live.gate_id == "direction_flip"


class TestReplayJudgeIntegration:
    @pytest.mark.asyncio
    async def test_invoke_judge_calls_claude(self):
        mock_claude = MagicMock()
        mock_claude.api_key = "test-key"
        mock_claude.async_client = AsyncMock()
        mock_claude.judge_trade = AsyncMock(
            return_value={
                "verdict": "APPROVE",
                "reason": "Clean setup",
                "suggested_entry": None,
                "risk_flags": [],
            }
        )
        bt = ClaudeReplayBacktester(claude_client=mock_claude, invoke_judge=True)
        sig = _signal()
        outcome = await bt.invoke_judge_for_signal(
            "EURUSD", sig, current_price=1.0850, session_name="london"
        )
        assert outcome.verdict == JudgeVerdict.APPROVE
        mock_claude.judge_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoke_judge_reject_blocks_policy_execution(self):
        from trading_bot.backtesting.replay import ReplaySignal
        from trading_bot.services.trade_judge import JudgeOutcome

        bt = ClaudeReplayBacktester()
        replay_sig = ReplaySignal(
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
        future = pd.DataFrame(
            [[1.0850, 1.0960, 1.0845, 1.0950], [1.0950, 1.0960, 1.0840, 1.0850]],
            columns=["open", "high", "low", "close"],
        )
        reject = JudgeOutcome(verdict=JudgeVerdict.REJECT, reason="weak setup")
        _, policy = bt._evaluate_replay_signal(
            replay_sig, future, current_price=1.0850, judge_outcome=reject
        )
        assert policy.execution_blocked is True
        assert policy.judge_verdict == "REJECT"

    def test_default_without_invoke_judge_still_rejects(self):
        bt = ClaudeReplayBacktester(invoke_judge=False, auto_approve_judge=False)
        assert bt._default_judge_outcome.verdict == JudgeVerdict.REJECT

    def test_auto_approve_still_works_when_invoke_disabled(self):
        bt = ClaudeReplayBacktester(invoke_judge=False, auto_approve_judge=True)
        assert bt._default_judge_outcome.verdict == JudgeVerdict.APPROVE
