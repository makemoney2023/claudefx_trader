"""Ensure Claude replay wires strategy_mode=replay for on-demand doc lookup."""

import inspect

from trading_bot.backtesting import replay as replay_mod


def test_replay_run_calls_analyze_with_strategy_mode_replay():
    source = inspect.getsource(replay_mod.ClaudeReplayBacktester.run)
    assert 'strategy_mode="replay"' in source
    assert "context_builder=context_builder" in source
    # Full ICT dump should not be fetched for every snapshot anymore.
    assert "get_ict_context()" not in source
