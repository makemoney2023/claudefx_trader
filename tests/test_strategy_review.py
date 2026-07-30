"""
Strategy-review fixes (July 2026).

Covers, in phase order:
  1. Bug fixes: precious-metals DataFrame truthiness, truncation-retry budget,
     degenerate-reasoning guard.
  2. Pre-Claude mechanical viability filter.
  3. Truthful gates (caps-below-floor -> explicit rejects) + dead-code removal.
  4. Trade-judge scoping (A+ fast path, retry on UNAVAILABLE).
  5. Counterfactual journal.
  6. Direction-gate consolidation (with old-vs-new parity harness).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest


def _make_claude_client():
    """ClaudeClient with mocked internals (mirrors test_gap_fixes helpers)."""
    from trading_bot.llm.claude_client import ClaudeClient

    with patch.object(ClaudeClient, "__init__", lambda self, **kw: None):
        client = ClaudeClient.__new__(ClaudeClient)
    client.api_key = "test"
    client.model = "claude-opus-5"
    client.model_heavy = "claude-opus-5"
    client.model_light = "claude-opus-5"
    client.effort_heavy = "low"
    client.effort_judge = "medium"
    client.effort_light = "low"
    client.effort_review = "medium"
    client.max_tokens = 16000
    client.temperature = 0.3
    client.max_retries = 3
    client.async_client = AsyncMock()
    client._cache = MagicMock()
    client._cache.get = AsyncMock(return_value=None)
    client._cache.set = AsyncMock()
    client._check_rate_limit = AsyncMock()
    client._record_usage = MagicMock()
    client._build_analysis_prompt = MagicMock(return_value="analyze")
    client._build_system_messages = MagicMock(return_value=[])
    return client


def _submit_block(
    reasoning: str,
    direction: str = "no_trade",
    confidence: float = 0.3,
    entry=None,
    sl=None,
    tp=None,
):
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_trade_analysis"
    block.input = {
        "direction": direction,
        "confidence": confidence,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "reasoning": reasoning,
        "market_structure": "ranging",
        "trade_type": "intraday",
    }
    message = MagicMock()
    message.stop_reason = "tool_use"
    message.content = [block]
    return message


# ============================================================
# Phase 1a — precious-metals context: DataFrame truthiness fix
# ============================================================

class TestPreciousMetalsContext:
    def _make_bot(self, other_ohlcv):
        analyzer = MagicMock()
        analyzer.get_context_for_claude = MagicMock(return_value="PM CTX")
        return SimpleNamespace(
            PRECIOUS_METALS={"XAUUSD", "XAGUSD"},
            precious_metals_analyzer=analyzer,
            news_service=None,
            data_fetcher=SimpleNamespace(get_ohlcv=AsyncMock(return_value=other_ohlcv)),
        )

    @pytest.mark.asyncio
    async def test_dataframe_response_adds_context(self):
        """get_ohlcv returns a DataFrame; truthiness must not raise and context lands."""
        from trading_bot.services.claude_analysis_stage import add_precious_metals_context

        bot = self._make_bot(pd.DataFrame({"close": [50.10, 50.25]}))
        market_data = {"current_price": 4100.0}

        await add_precious_metals_context(bot, "XAUUSD", market_data)

        assert market_data.get("precious_metals_context") == "PM CTX"
        kwargs = bot.precious_metals_analyzer.get_context_for_claude.call_args.kwargs
        assert kwargs["gold_price"] == 4100.0
        assert kwargs["silver_price"] == pytest.approx(50.25)

    @pytest.mark.asyncio
    async def test_none_response_skips_without_error(self):
        from trading_bot.services.claude_analysis_stage import add_precious_metals_context

        bot = self._make_bot(None)
        market_data = {"current_price": 4100.0}
        await add_precious_metals_context(bot, "XAUUSD", market_data)
        assert "precious_metals_context" not in market_data

    @pytest.mark.asyncio
    async def test_empty_dataframe_skips_without_error(self):
        from trading_bot.services.claude_analysis_stage import add_precious_metals_context

        bot = self._make_bot(pd.DataFrame({"close": []}))
        market_data = {"current_price": 4100.0}
        await add_precious_metals_context(bot, "XAUUSD", market_data)
        assert "precious_metals_context" not in market_data

    @pytest.mark.asyncio
    async def test_non_metal_symbol_is_noop(self):
        from trading_bot.services.claude_analysis_stage import add_precious_metals_context

        bot = self._make_bot(pd.DataFrame({"close": [1.0]}))
        market_data = {"current_price": 1.10}
        await add_precious_metals_context(bot, "EURUSD", market_data)
        assert "precious_metals_context" not in market_data
        bot.data_fetcher.get_ohlcv.assert_not_awaited()


# ============================================================
# Phase 1b — truncation retry gets a workable budget (8k, not 4k)
# ============================================================

class TestTruncationRetryBudget:
    @pytest.mark.asyncio
    async def test_retry_after_max_tokens_uses_8000(self):
        client = _make_claude_client()

        truncated = MagicMock()
        truncated.stop_reason = "max_tokens"
        truncated.content = [MagicMock(type="thinking", thinking="...")]

        recovered = _submit_block("No setup after retry")

        client._async_messages_create = AsyncMock(side_effect=[truncated, recovered])

        result = await client.analyze_chart_async(
            chart_image_base64="abc",
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="ctx",
            use_cache=False,
        )

        assert client._async_messages_create.await_count == 2
        retry_kwargs = client._async_messages_create.await_args_list[1].kwargs
        assert retry_kwargs.get("max_tokens") == 8000
        assert retry_kwargs.get("thinking") == {"type": "disabled"}
        assert result.signal.reasoning == "No setup after retry"


# ============================================================
# Phase 1c — degenerate-reasoning guard
# ============================================================

class TestDegenerateReasoningGuard:
    def test_degenerate_literals_detected(self):
        from trading_bot.llm.claude_client import ClaudeClient

        degenerate = ["placeholder", "Placeholder.", "", "   ", "n/a", "N/A",
                      "none", "null", "todo", "tbd", "...", "-"]
        for text in degenerate:
            assert ClaudeClient._is_degenerate_reasoning(text), repr(text)

        legitimate = [
            "No valid setup - HTF conflict",
            "D1 and H4 are both CHoCH bearish while M15/M5 shifted bullish",
            "No setup",
        ]
        for text in legitimate:
            assert not ClaudeClient._is_degenerate_reasoning(text), repr(text)

    @pytest.mark.asyncio
    async def test_placeholder_reasoning_retries_once_then_accepts(self):
        client = _make_claude_client()
        client._async_messages_create = AsyncMock(side_effect=[
            _submit_block("placeholder"),
            _submit_block("Real analysis: HTF conflict blocks both directions"),
        ])

        result = await client.analyze_chart_async(
            chart_image_base64="abc",
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="ctx",
            use_cache=False,
        )

        assert client._async_messages_create.await_count == 2
        assert result.signal.reasoning.startswith("Real analysis")

    @pytest.mark.asyncio
    async def test_persistent_placeholder_discards_response(self):
        client = _make_claude_client()
        client._async_messages_create = AsyncMock(side_effect=[
            _submit_block("placeholder", direction="long", confidence=0.8,
                          entry=4100.0, sl=4090.0, tp=4120.0),
            _submit_block("placeholder", direction="long", confidence=0.8,
                          entry=4100.0, sl=4090.0, tp=4120.0),
        ])

        result = await client.analyze_chart_async(
            chart_image_base64="abc",
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="ctx",
            use_cache=False,
        )

        # Only one degenerate retry, then discard: never a tradeable signal.
        assert client._async_messages_create.await_count == 2
        assert result.signal.direction == "no_trade"
        assert "degenerate" in result.signal.reasoning.lower()
        client._cache.set.assert_not_called()
