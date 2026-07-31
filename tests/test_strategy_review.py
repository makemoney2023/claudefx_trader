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
    async def test_retry_after_max_tokens_uses_full_budget(self):
        """Retry must keep the full analysis budget — 8k was exhausted in prod (2026-07-31)."""
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
        assert retry_kwargs.get("max_tokens") == client.max_tokens
        assert retry_kwargs.get("max_tokens") == 16000
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


# ============================================================
# Phase 2 — pre-Claude mechanical viability filter
# ============================================================

class TestPreClaudeViability:
    """Skip the LLM call only when the gate stack already guarantees rejection."""

    def _check(self, **overrides):
        from trading_bot.utils.win_optimization import pre_claude_viability

        params = dict(
            d1_bias="unknown",
            h4_bias="unknown",
            m15_bias="unknown",
            amd_phase="unknown",
            relative_volume=1.0,
            in_kill_zone=False,
            silver_bullet_window=False,
        )
        params.update(overrides)
        return pre_claude_viability(**params)

    def test_both_directions_structurally_blocked_skips(self):
        """Today's live pattern: D1/H4 bearish blocks longs, M15 bullish blocks shorts."""
        result = self._check(d1_bias="bearish", h4_bias="bearish", m15_bias="bullish")
        assert result.proceed is False
        assert len(result.reasons) >= 2

    def test_kill_zone_overrides_skip(self):
        result = self._check(
            d1_bias="bearish", h4_bias="bearish", m15_bias="bullish",
            in_kill_zone=True,
        )
        assert result.proceed is True

    def test_silver_bullet_window_overrides_skip(self):
        result = self._check(
            d1_bias="bearish", h4_bias="bearish", m15_bias="bullish",
            silver_bullet_window=True,
        )
        assert result.proceed is True

    def test_dead_volume_alone_skips(self):
        result = self._check(
            d1_bias="bullish", h4_bias="bullish", m15_bias="bullish",
            relative_volume=0.2,
        )
        assert result.proceed is False
        assert any("volume" in r.lower() for r in result.reasons)

    def test_low_but_tradeable_volume_proceeds(self):
        result = self._check(
            d1_bias="bullish", h4_bias="bullish", m15_bias="bullish",
            relative_volume=0.6,
        )
        assert result.proceed is True

    def test_one_direction_open_proceeds(self):
        # Longs blocked by HTF, but M15 bearish leaves shorts fully open.
        result = self._check(d1_bias="bearish", h4_bias="bearish", m15_bias="bearish")
        assert result.proceed is True

    def test_amd_manipulation_reopens_ltf_opposed_direction(self):
        # M15 bullish would block shorts, but manipulation phase bypasses the
        # M15 gate — shorts viable (HTF only blocks with BOTH D1+H4 bullish).
        result = self._check(
            d1_bias="bearish", h4_bias="neutral", m15_bias="bullish",
            amd_phase="manipulation",
        )
        assert result.proceed is True

    def test_unknown_biases_proceed(self):
        assert self._check().proceed is True

    def test_pullback_cap_counts_as_blocked(self):
        # D1/H4 bullish + M15 bearish: long only via 0.55-capped pullback
        # (below the 0.60 floor -> dead end), short blocked by HTF.
        result = self._check(d1_bias="bullish", h4_bias="bullish", m15_bias="bearish")
        assert result.proceed is False

    @pytest.mark.asyncio
    async def test_runner_skips_chart_and_claude_when_unviable(self, monkeypatch):
        """Wiring: runner must bail before chart generation / Claude stage."""
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", False)
        monkeypatch.setattr(settings.trading, "allow_simulation_trades", False)

        df = pd.DataFrame({
            "open": [4100.0] * 30, "high": [4105.0] * 30,
            "low": [4095.0] * 30, "close": [4100.0] * 30,
            "volume": [100.0] * 30,
        })

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = set()
        bot.scaling_manager = None
        bot.mt5_client.is_simulation = False
        bot.kill_zone_checker = MagicMock()
        bot.kill_zone_checker.get_current_session.return_value = SimpleNamespace(
            is_tradeable=True, is_kill_zone=False, session_name="NY Afternoon",
            next_kill_zone=None, next_kill_zone_in_minutes=None,
        )
        bot.data_fetcher.get_ohlcv = AsyncMock(return_value=df)
        bot.claude_client.api_key = "test"
        bot._generate_chart_image = AsyncMock(return_value="chartb64")

        pipeline = bot._trade_pipeline
        pipeline.analysis.run_core_analysis.return_value = {
            "volume": {"relative_volume": 0.6, "volume_trend": "decreasing", "spike_bars": []},
            "amd_cycle": {"phase": "accumulation"},
        }

        # MTF: D1/H4 bearish (blocks longs), M15 bullish (blocks shorts).
        def _tf(bias):
            return SimpleNamespace(bias=SimpleNamespace(value=bias))

        mtf = SimpleNamespace(
            daily_analysis=_tf("bearish"),
            h4_analysis=_tf("bearish"),
            m15_analysis=_tf("bullish"),
        )
        expanded = SimpleNamespace(
            pd_analysis=None, mtf_result=mtf, dxy_confirmation=None,
            retail_contrarian=None, vix_risk_mode=None,
            currency_strength_recommendation=None, amd_state=None,
            displacement_analysis=None, breaker_blocks=[],
            silver_bullet_ready=False, ipda_analysis=None, nwog_target=None,
        )

        with patch(
            "trading_bot.services.expanded_analysis.run_expanded_analysis",
            AsyncMock(return_value=expanded),
        ), patch("trading_bot.api.routes.activity.add_activity"), patch(
            "trading_bot.services.analyze_and_trade_runner.bot_state", None
        ):
            await run_analyze_and_trade(bot, "XAUUSD")

        bot._generate_chart_image.assert_not_awaited()
        pipeline.claude.assert_not_called()


# ============================================================
# Phase 3a — sub-floor caps become explicit, honestly-labeled rejects
# ============================================================

def _gate_ctx(**kwargs):
    from trading_bot.services.trade_context import TradeContext

    base = dict(
        symbol="XAUUSD",
        direction="long",
        confidence=0.78,
        actual_rr=2.5,
        d1_bias="bullish",
        h4_bias="bullish",
        m15_bias="bullish",
        analysis_results={"volume": {"relative_volume": 1.0}},
    )
    base.update(kwargs)
    return TradeContext(**base)


class TestTruthfulGateRejects:
    """Caps that land below the 0.60 execution floor must reject at their own gate."""

    def test_m15_pullback_rejects_explicitly(self):
        from trading_bot.services.entry_gates import evaluate_m15_gate

        ctx = _gate_ctx(
            m15_bias="bearish", order_type="buy_limit",
            d1_bias="bullish", h4_bias="bullish",
        )
        outcome = evaluate_m15_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "m15_pullback_cap"

    def test_htf_counter_scalp_rejects_explicitly(self):
        from trading_bot.services.entry_gates import evaluate_htf_alignment_gate

        ctx = _gate_ctx(
            d1_bias="bearish", h4_bias="bearish", m15_bias="bullish",
            trade_type="scalp", actual_rr=2.5, confidence=0.70,
        )
        ctx.m15_opposes = False
        outcome = evaluate_htf_alignment_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "htf_oppose_cap"

    def test_htf_both_oppose_non_scalp_keeps_original_gate_id(self):
        from trading_bot.services.entry_gates import evaluate_htf_alignment_gate

        ctx = _gate_ctx(
            d1_bias="bearish", h4_bias="bearish", trade_type="intraday",
        )
        ctx.m15_opposes = True
        outcome = evaluate_htf_alignment_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "htf_both_oppose"

    def test_htf_single_oppose_cap_at_floor_survives(self):
        from trading_bot.services.entry_gates import evaluate_htf_alignment_gate

        # 0.60 cap == the execution floor -> still tradeable, keep capping.
        ctx = _gate_ctx(d1_bias="bearish", h4_bias="bullish", confidence=0.80)
        ctx.m15_opposes = False
        outcome = evaluate_htf_alignment_gate(ctx)
        assert outcome.blocked is False
        assert outcome.confidence_cap == 0.60

    def test_off_hours_rejects_even_with_great_rr(self):
        from trading_bot.services.entry_gates import evaluate_off_hours_gate

        ctx = _gate_ctx(off_hours_mode=True, actual_rr=5.0, confidence=0.90)
        outcome = evaluate_off_hours_gate(ctx)
        assert outcome.blocked is True
        assert outcome.gate_id == "off_hours_cap"

    def test_off_hours_off_passes_through(self):
        from trading_bot.services.entry_gates import evaluate_off_hours_gate

        ctx = _gate_ctx(off_hours_mode=False)
        outcome = evaluate_off_hours_gate(ctx)
        assert outcome.blocked is False
        assert outcome.confidence_cap is None


# ============================================================
# Phase 3b — dead code removal + DEFENSIVE becomes an explicit halt
# ============================================================

class TestDeadCodeRemoval:
    def test_soft_kill_zone_path_removed_from_cycle(self):
        import inspect
        from trading_bot.main import TradingBot

        src = inspect.getsource(TradingBot._trading_cycle)
        assert "soft-block active" not in src
        assert "crypto_kill_zone_only" not in src
        # Hard skip must survive
        assert "claude_analysis_allowed" in src

    def test_runner_has_single_btc_block(self):
        import inspect
        import trading_bot.services.analyze_and_trade_runner as runner_mod

        src = inspect.getsource(runner_mod)
        assert "FINAL BLOCK" not in src
        assert src.count("BTC/BIT pair") >= 1  # analysis-time block survives

    @pytest.mark.asyncio
    async def test_btc_pair_still_blocked_at_analysis_time(self, monkeypatch):
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", False)

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = ["ETHBTC"]
        bot.scaling_manager = None
        bot.kill_zone_checker = None
        bot.data_fetcher.get_ohlcv = AsyncMock()

        with patch("trading_bot.api.routes.activity.add_activity"), patch(
            "trading_bot.services.analyze_and_trade_runner.bot_state", None
        ):
            await run_analyze_and_trade(bot, "ETHBTC")

        bot.data_fetcher.get_ohlcv.assert_not_awaited()

    def test_weak_hours_default_is_empty(self):
        from trading_bot.config import TradingSettings

        assert TradingSettings().weak_hours_by_symbol == {}

    def test_tod_gate_mechanism_survives(self):
        from trading_bot.services.entry_gates import evaluate_tod_gate

        blocked, reason = evaluate_tod_gate(
            utc_hour=12, weak_hours=(12, 13), confidence=0.55
        )
        assert blocked is True


# ============================================================
# Phase 4 — judge scoping: A+ fast path + retry on UNAVAILABLE
# ============================================================

class TestJudgeFastPath:
    def _qualifies(self, **overrides):
        from trading_bot.services.trade_judge import qualifies_for_judge_fast_path

        params = dict(
            confidence=0.86, risk_reward=2.6, htf_aligned=True, warnings=[],
        )
        params.update(overrides)
        return qualifies_for_judge_fast_path(**params)

    def test_a_plus_setup_qualifies(self):
        assert self._qualifies() is True

    def test_low_confidence_disqualifies(self):
        assert self._qualifies(confidence=0.84) is False

    def test_low_rr_disqualifies(self):
        assert self._qualifies(risk_reward=2.4) is False

    def test_htf_misalignment_disqualifies(self):
        assert self._qualifies(htf_aligned=False) is False

    def test_warnings_disqualify(self):
        assert self._qualifies(warnings=["D1/H4 conflict"]) is False

    def test_runner_wires_fast_path_before_judge(self):
        import inspect
        import trading_bot.services.analyze_and_trade_runner as runner_mod

        src = inspect.getsource(runner_mod)
        assert "judge_skipped_a_plus" in src
        assert src.index("judge_skipped_a_plus") < src.index("_run_trade_judge(")


class TestJudgeUnavailableRetry:
    def _client(self, side_effect):
        client = MagicMock()
        client.api_key = "test"
        client.async_client = object()
        client.judge_trade = AsyncMock(side_effect=side_effect)
        return client

    @pytest.mark.asyncio
    async def test_transient_error_retries_then_approves(self):
        from trading_bot.services.trade_judge import run_trade_judge, JudgeVerdict

        client = self._client([
            RuntimeError("api hiccup"),
            {"verdict": "APPROVE", "reason": "ok", "risk_flags": []},
        ])
        outcome = await run_trade_judge(client, {}, {})
        assert outcome.verdict == JudgeVerdict.APPROVE
        assert client.judge_trade.await_count == 2

    @pytest.mark.asyncio
    async def test_persistent_failure_stays_fail_closed(self):
        from trading_bot.services.trade_judge import run_trade_judge, JudgeVerdict

        client = self._client([RuntimeError("down"), RuntimeError("still down")])
        outcome = await run_trade_judge(client, {}, {})
        assert outcome.verdict == JudgeVerdict.UNAVAILABLE
        assert outcome.blocks_execution() is True
        assert client.judge_trade.await_count == 2

    @pytest.mark.asyncio
    async def test_definitive_verdict_not_retried(self):
        from trading_bot.services.trade_judge import run_trade_judge, JudgeVerdict

        client = self._client([
            {"verdict": "REJECT", "reason": "bad idea", "risk_flags": []},
        ])
        outcome = await run_trade_judge(client, {}, {})
        assert outcome.verdict == JudgeVerdict.REJECT
        assert client.judge_trade.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_client_does_not_retry(self):
        from trading_bot.services.trade_judge import run_trade_judge, JudgeVerdict

        outcome = await run_trade_judge(None, {}, {})
        assert outcome.verdict == JudgeVerdict.UNAVAILABLE


# ============================================================
# Phase 5 — counterfactual journal
# ============================================================

class TestCounterfactualJournal:
    def _journal(self, tmp_path):
        from trading_bot.services.counterfactual_journal import CounterfactualJournal

        return CounterfactualJournal(path=str(tmp_path / "counterfactuals.jsonl"))

    def _df(self, start, bars):
        """bars: list of (high, low) tuples, 5-minute spacing."""
        idx = pd.date_range(start=start, periods=len(bars), freq="5min")
        return pd.DataFrame(
            {
                "open": [(h + l) / 2 for h, l in bars],
                "high": [h for h, l in bars],
                "low": [l for h, l in bars],
                "close": [(h + l) / 2 for h, l in bars],
            },
            index=idx,
        )

    def test_record_and_reload(self, tmp_path):
        journal = self._journal(tmp_path)
        rec = journal.record(
            symbol="XAUUSD", gate_id="htf_both_oppose", outcome_type="mechanical_reject",
            direction="long", confidence=0.7, entry=4100.0, sl=4090.0, tp=4130.0,
            reason="test",
        )
        assert rec["gate_id"] == "htf_both_oppose"
        records = journal.load_records()
        assert len(records) == 1
        assert records[0]["symbol"] == "XAUUSD"
        assert records[0]["outcome"] is None

    @pytest.mark.asyncio
    async def test_tp_first_scores_missed_r(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        journal = self._journal(tmp_path)
        blocked_at = datetime.now(timezone.utc) - timedelta(hours=6)
        journal.record(
            symbol="XAUUSD", gate_id="htf_both_oppose", outcome_type="mechanical_reject",
            direction="long", entry=4100.0, sl=4090.0, tp=4130.0,
            timestamp=blocked_at,
        )
        # Bars after the block: price runs straight to TP without touching SL.
        df = self._df(blocked_at + timedelta(minutes=5), [
            (4105, 4098), (4115, 4104), (4131, 4114),
        ])
        fetch = AsyncMock(return_value=df)

        scored = await journal.score_pending(fetch)
        assert scored == 1
        rec = journal.load_records()[0]
        assert rec["outcome"] == "tp_first"
        assert rec["outcome_r"] == pytest.approx(3.0)  # 30 gained / 10 risked
        assert rec["finalized"] is True

    @pytest.mark.asyncio
    async def test_sl_first_scores_saved_r(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        journal = self._journal(tmp_path)
        blocked_at = datetime.now(timezone.utc) - timedelta(hours=6)
        journal.record(
            symbol="XAUUSD", gate_id="m15_structure", outcome_type="mechanical_reject",
            direction="long", entry=4100.0, sl=4090.0, tp=4130.0,
            timestamp=blocked_at,
        )
        df = self._df(blocked_at + timedelta(minutes=5), [
            (4102, 4095), (4098, 4089),  # SL hit on bar 2
        ])
        scored = await journal.score_pending(AsyncMock(return_value=df))
        assert scored == 1
        rec = journal.load_records()[0]
        assert rec["outcome"] == "sl_first"
        assert rec["outcome_r"] == pytest.approx(-1.0)

    @pytest.mark.asyncio
    async def test_same_bar_both_counts_as_sl_first(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        journal = self._journal(tmp_path)
        blocked_at = datetime.now(timezone.utc) - timedelta(hours=6)
        journal.record(
            symbol="XAUUSD", gate_id="zone_gate", outcome_type="mechanical_reject",
            direction="long", entry=4100.0, sl=4090.0, tp=4130.0,
            timestamp=blocked_at,
        )
        df = self._df(blocked_at + timedelta(minutes=5), [(4131, 4089)])
        await journal.score_pending(AsyncMock(return_value=df))
        assert journal.load_records()[0]["outcome"] == "sl_first"

    @pytest.mark.asyncio
    async def test_neither_finalizes_after_24h(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        journal = self._journal(tmp_path)
        blocked_at = datetime.now(timezone.utc) - timedelta(hours=30)
        journal.record(
            symbol="XAUUSD", gate_id="tod_gate", outcome_type="mechanical_reject",
            direction="short", entry=4100.0, sl=4110.0, tp=4070.0,
            timestamp=blocked_at,
        )
        df = self._df(blocked_at + timedelta(minutes=5), [
            (4103, 4097), (4104, 4096),
        ])
        await journal.score_pending(AsyncMock(return_value=df))
        rec = journal.load_records()[0]
        assert rec["outcome"] == "neither"
        assert rec["outcome_r"] == 0.0
        assert rec["finalized"] is True

    @pytest.mark.asyncio
    async def test_too_fresh_records_not_scored(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        journal = self._journal(tmp_path)
        journal.record(
            symbol="XAUUSD", gate_id="htf_both_oppose", outcome_type="mechanical_reject",
            direction="long", entry=4100.0, sl=4090.0, tp=4130.0,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        fetch = AsyncMock()
        scored = await journal.score_pending(fetch)
        assert scored == 0
        fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_priceless_records_never_scored(self, tmp_path):
        from datetime import datetime, timedelta, timezone

        journal = self._journal(tmp_path)
        journal.record(
            symbol="XAUUSD", gate_id="pre_claude_viability", outcome_type="pre_claude_skip",
            market_price=4100.0,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        fetch = AsyncMock()
        scored = await journal.score_pending(fetch)
        assert scored == 0
        fetch.assert_not_awaited()

    def test_per_gate_summary(self, tmp_path):
        import json

        journal = self._journal(tmp_path)
        records = [
            {"gate_id": "htf_both_oppose", "outcome": "tp_first", "outcome_r": 2.5},
            {"gate_id": "htf_both_oppose", "outcome": "sl_first", "outcome_r": -1.0},
            {"gate_id": "htf_both_oppose", "outcome": None, "outcome_r": None},
            {"gate_id": "zone_gate", "outcome": "sl_first", "outcome_r": -1.0},
        ]
        with open(journal.path, "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

        summary = journal.summary()
        htf = summary["gates"]["htf_both_oppose"]
        assert htf["count"] == 3
        assert htf["tp_first"] == 1
        assert htf["sl_first"] == 1
        assert htf["pending"] == 1
        assert htf["missed_r"] == pytest.approx(2.5)
        assert htf["saved_r"] == pytest.approx(1.0)
        assert summary["gates"]["zone_gate"]["net_saved_r"] == pytest.approx(1.0)


class TestCounterfactualWiring:
    @pytest.mark.asyncio
    async def test_terminal_decisions_feed_journal(self, tmp_path):
        """Every terminal decision through the bot hook lands in the journal."""
        from trading_bot.main import TradingBot
        from trading_bot.services.counterfactual_journal import CounterfactualJournal

        bot = MagicMock()
        bot.counterfactual_journal = CounterfactualJournal(
            path=str(tmp_path / "cf.jsonl")
        )
        bot._last_confidence_components = None
        bot.gate_funnel = MagicMock()
        bot.gate_funnel.record_decision = AsyncMock(return_value="id1")

        await TradingBot._record_terminal_decision(
            bot, "mechanical_reject", "XAUUSD",
            gate_id="htf_both_oppose", direction="long",
            entry=4100.0, sl=4090.0, tp=4130.0, confidence=0.7,
            reason="test",
        )

        records = bot.counterfactual_journal.load_records()
        assert len(records) == 1
        assert records[0]["gate_id"] == "htf_both_oppose"
        assert records[0]["outcome_type"] == "mechanical_reject"

    def test_pre_claude_skip_records_counterfactual(self):
        import inspect
        import trading_bot.services.analyze_and_trade_runner as runner_mod

        src = inspect.getsource(runner_mod)
        assert "counterfactual" in src.lower()

    def test_api_endpoint_exists(self):
        from trading_bot.api.routes.analysis import router

        paths = [r.path for r in router.routes]
        assert "/counterfactuals" in paths


class TestDefensiveHalt:
    def test_cycle_halts_explicitly_in_defensive_mode(self):
        import inspect
        from trading_bot.main import TradingBot

        src = inspect.getsource(TradingBot._trading_cycle)
        assert "trading_halted" in src
        assert "TradingMode.DEFENSIVE" in src

    def test_defensive_scaling_backstop_unchanged(self):
        """Mid-cycle DEFENSIVE overrides still reject non-A+ setups."""
        from trading_bot.services.scaling_gates import evaluate_scaling_gate
        from trading_bot.services.scaling_manager import ScalingManager, TradingMode

        mgr = ScalingManager()
        mgr.current_mode = TradingMode.DEFENSIVE
        outcome = evaluate_scaling_gate(
            setup_grade="B", confidence=0.70, daily_trades=0, scaling_manager=mgr,
        )
        assert outcome.blocked is True


# =============================================================================
# Phase 6: Direction-gate consolidation
# =============================================================================

def _dir_ctx(
    *,
    direction="long",
    d1="",
    h4="",
    m15="",
    confidence=0.65,
    rr=3.5,
    trade_type="intraday",
    is_index=False,
    retrace=None,
    zone="equilibrium",
):
    """Minimal TradeContext for direction-gate tests."""
    from trading_bot.services.trade_context import TradeContext

    pd_analysis = None
    if retrace is not None:
        pd_analysis = SimpleNamespace(
            retracement_percent=retrace,
            current_zone=SimpleNamespace(value=zone),
        )
    return TradeContext(
        symbol="XAUUSD",
        direction=direction,
        confidence=confidence,
        actual_rr=rr,
        d1_bias=d1,
        h4_bias=h4,
        m15_bias=m15,
        trade_type=trade_type,
        is_index=is_index,
        pd_analysis=pd_analysis,
        trade_signal=SimpleNamespace(confidence=confidence),
    )


class TestDirectionAlignmentGate:
    """One gate owns the direction-vs-D1 policy."""

    def test_counter_d1_nonscalp_low_rr_blocks(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = _dir_ctx(direction="long", d1="bearish", confidence=0.65, rr=2.5)
        out = evaluate_direction_alignment_gate(ctx)
        assert out.blocked is True
        assert out.gate_id == "direction_alignment"

    def test_counter_d1_nonscalp_quality_passes(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = _dir_ctx(direction="long", d1="bearish", confidence=0.65, rr=3.5)
        out = evaluate_direction_alignment_gate(ctx)
        assert out.blocked is False

    def test_counter_d1_scalp_rr_floor(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = _dir_ctx(
            direction="short", d1="bullish", trade_type="scalp",
            confidence=0.68, rr=2.0,
        )
        out = evaluate_direction_alignment_gate(ctx, scalp_rr_floor=2.5)
        assert out.blocked is True
        assert out.gate_id == "direction_alignment"

    def test_counter_d1_scalp_caps_confidence(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = _dir_ctx(
            direction="short", d1="bullish", trade_type="scalp",
            confidence=0.85, rr=3.0,
        )
        out = evaluate_direction_alignment_gate(ctx, scalp_rr_floor=2.5)
        assert out.blocked is False
        assert out.confidence_cap == 0.70

    def test_aligned_passes(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = _dir_ctx(direction="long", d1="bullish", confidence=0.55, rr=1.5)
        out = evaluate_direction_alignment_gate(ctx)
        assert out.blocked is False

    def test_neutral_d1_passes(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        ctx = _dir_ctx(direction="long", d1="", confidence=0.55, rr=1.5)
        out = evaluate_direction_alignment_gate(ctx)
        assert out.blocked is False

    def test_index_without_d1_support_needs_quality(self):
        from trading_bot.services.entry_gates import evaluate_direction_alignment_gate

        weak = _dir_ctx(
            direction="long", d1="", is_index=True, retrace=0.3,
            confidence=0.55, rr=2.5,
        )
        assert evaluate_direction_alignment_gate(weak).blocked is True

        strong = _dir_ctx(
            direction="long", d1="", is_index=True, retrace=0.3,
            confidence=0.65, rr=2.5,
        )
        assert evaluate_direction_alignment_gate(strong).blocked is False


class TestZoneGatePureLocation:
    """Zone gate keeps location logic only; D1 policy moved out."""

    def test_counter_d1_from_discount_not_blocked_by_zone_gate(self):
        from trading_bot.services.entry_gates import (
            ZoneGateSettings, evaluate_zone_gate,
        )

        res = evaluate_zone_gate(
            direction="long",
            confidence=0.55,
            actual_rr=1.5,
            retrace=0.3,
            zone_str="discount",
            d1_bias="bearish",  # ignored: direction policy lives elsewhere
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
        )
        assert res.blocked is False
        assert res.decision == "allowed_zone_aligned"

    def test_misaligned_location_still_blocks(self):
        from trading_bot.services.entry_gates import (
            ZoneGateSettings, evaluate_zone_gate,
        )

        res = evaluate_zone_gate(
            direction="long",
            confidence=0.55,
            actual_rr=1.5,
            retrace=0.8,
            zone_str="premium",
            d1_bias="",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
        )
        assert res.blocked is True
        assert res.decision == "blocked_misaligned"


class TestDirectionGatePipelineWiring:
    def test_zone_inactive_counter_d1_blocks_via_direction_gate(self):
        from trading_bot.services.entry_gates import ZoneGateSettings
        from trading_bot.services.gate_pipeline import evaluate_zone_and_regime_gates

        ctx = _dir_ctx(direction="long", d1="bearish", confidence=0.65, rr=2.0)
        out = evaluate_zone_and_regime_gates(
            ctx,
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
        )
        assert out.blocked is True
        assert out.gate_id == "direction_alignment"

    def test_legacy_d1_gate_gone_from_pipeline(self):
        import inspect
        from trading_bot.services import gate_pipeline

        src = inspect.getsource(gate_pipeline)
        assert "evaluate_legacy_d1_gate" not in src

    def test_post_claude_counter_trend_block_removed(self):
        import inspect
        from trading_bot.services import post_claude_gates

        src = inspect.getsource(post_claude_gates)
        assert "counter_trend_scalp_rr" not in src


class TestDirectionGateParity:
    """Old five-place direction stack vs new consolidated stack.

    Contract: the new stack is never MORE permissive than the old one.
    Where it is stricter, the divergence must fall in the documented
    category: counter-D1 non-scalp trades now uniformly need 3:1 RR
    (the legacy-D1 standard) even when the zone gate is active.
    """

    @staticmethod
    def _old_blocked(direction, d1, trade_type, conf, rr, retrace, is_index):
        """Replica of the pre-consolidation composite (zone gate active)."""
        opposes = (
            (d1 == "bullish" and direction == "short")
            or (d1 == "bearish" and direction == "long")
        )
        is_counter_scalp = trade_type == "scalp" and opposes
        if is_counter_scalp:
            # post-Claude scalp check; zone gate was skipped for these
            return rr < 2.5

        in_correct_zone = (
            (direction == "short" and retrace >= 0.5)
            or (direction == "long" and retrace <= 0.5)
        )
        counter_trend = opposes
        index_counter = False
        if is_index and in_correct_zone:
            index_counter = (
                (direction == "short" and d1 != "bearish")
                or (direction == "long" and d1 != "bullish")
            )
        if in_correct_zone and (counter_trend or index_counter) and is_index:
            zone_aligned, zone_misaligned = False, True
        elif in_correct_zone and counter_trend:
            zone_aligned, zone_misaligned = False, False
        else:
            zone_aligned = in_correct_zone
            zone_misaligned = (
                (direction == "long" and retrace >= 0.618)
                or (direction == "short" and retrace <= 0.382)
            )
        if zone_misaligned:
            return conf < 0.60 or rr < 2.0
        if not zone_aligned:
            return conf < 0.60
        return False

    @staticmethod
    def _new_blocked(direction, d1, trade_type, conf, rr, retrace, is_index):
        from trading_bot.services.entry_gates import (
            ZoneGateSettings,
            evaluate_direction_alignment_gate,
            evaluate_zone_gate,
        )

        ctx = _dir_ctx(
            direction=direction, d1=d1, trade_type=trade_type,
            confidence=conf, rr=rr, is_index=is_index, retrace=retrace,
        )
        out = evaluate_direction_alignment_gate(ctx, scalp_rr_floor=2.5)
        if out.blocked:
            return True
        opposes = (
            (d1 == "bullish" and direction == "short")
            or (d1 == "bearish" and direction == "long")
        )
        if trade_type == "scalp" and opposes:
            return False  # zone gate still skipped for counter scalps
        res = evaluate_zone_gate(
            direction=direction,
            confidence=conf,
            actual_rr=rr,
            retrace=retrace,
            zone_str="grid",
            d1_bias=d1,
            is_index=is_index,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
        )
        return res.blocked

    def test_new_stack_never_more_permissive(self):
        import itertools

        grid = itertools.product(
            ("long", "short"),
            ("bullish", "bearish", ""),
            ("scalp", "intraday"),
            (0.55, 0.65, 0.80),
            (1.5, 2.2, 2.7, 3.5),
            (0.2, 0.5, 0.7, 0.8),
            (False, True),
        )
        divergences = []
        for direction, d1, ttype, conf, rr, retrace, is_index in grid:
            old = self._old_blocked(direction, d1, ttype, conf, rr, retrace, is_index)
            new = self._new_blocked(direction, d1, ttype, conf, rr, retrace, is_index)
            if old and not new:
                pytest.fail(
                    f"New stack MORE permissive: {direction} d1={d1} {ttype} "
                    f"conf={conf} rr={rr} retrace={retrace} index={is_index}"
                )
            if new and not old:
                divergences.append((direction, d1, ttype, conf, rr, retrace, is_index))

        for direction, d1, ttype, conf, rr, retrace, is_index in divergences:
            opposes = (
                (d1 == "bullish" and direction == "short")
                or (d1 == "bearish" and direction == "long")
            )
            assert opposes and ttype != "scalp" and rr < 3.0, (
                f"Undocumented divergence: {direction} d1={d1} {ttype} "
                f"conf={conf} rr={rr} retrace={retrace} index={is_index}"
            )
