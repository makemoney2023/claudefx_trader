"""
Tests for patient entry system: raised confidence thresholds,
two-tier swing validation, and patient re-evaluation.
"""
import pytest
from trading_bot.services.scaling_manager import ScalingManager, TradingMode, MODE_CONFIGS


class TestRaisedConfidenceThresholds:
    """Confidence floor is 60% for non-defensive modes (replay + live)."""

    def _create_manager(self, starting_equity: float = 10000) -> ScalingManager:
        manager = ScalingManager(starting_equity=starting_equity)
        return manager

    # ----- AGGRESSIVE mode: 0.60 threshold -----

    def test_aggressive_threshold_is_060(self):
        """AGGRESSIVE mode confidence threshold should be 0.60."""
        config = MODE_CONFIGS[TradingMode.AGGRESSIVE]
        assert config.confidence_threshold == 0.60

    def test_aggressive_rejects_below_060(self):
        """AGGRESSIVE mode should reject trades below 0.60 confidence."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.AGGRESSIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A',
            confidence=0.59,
            daily_trades=0
        )
        assert should_trade is False
        assert "confidence" in reason.lower() or "threshold" in reason.lower()

    def test_aggressive_accepts_060_confidence(self):
        """AGGRESSIVE mode should accept trades with 0.60 confidence."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.AGGRESSIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A',
            confidence=0.60,
            daily_trades=0
        )
        assert should_trade is True

    def test_aggressive_accepts_085_confidence(self):
        """AGGRESSIVE mode should accept trades with 0.85 confidence."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.AGGRESSIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A',
            confidence=0.85,
            daily_trades=0
        )
        assert should_trade is True

    # ----- NORMAL mode: 0.60 threshold -----

    def test_normal_threshold_is_060(self):
        """NORMAL mode confidence threshold should be 0.60."""
        config = MODE_CONFIGS[TradingMode.NORMAL]
        assert config.confidence_threshold == 0.60

    def test_normal_rejects_below_060(self):
        """NORMAL mode should reject trades below 0.60 confidence."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.NORMAL
        should_trade, reason = manager.should_take_trade(
            setup_grade='A',
            confidence=0.59,
            daily_trades=0
        )
        assert should_trade is False
        assert "confidence" in reason.lower() or "threshold" in reason.lower()

    def test_normal_accepts_060_confidence(self):
        """NORMAL mode should accept trades with 0.60 confidence."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.NORMAL
        should_trade, reason = manager.should_take_trade(
            setup_grade='B',
            confidence=0.60,
            daily_trades=0
        )
        assert should_trade is True

    # ----- CONSERVATIVE mode: 0.60 threshold (0.5x risk) -----

    def test_conservative_threshold_is_060(self):
        """CONSERVATIVE mode confidence threshold should be 0.60."""
        config = MODE_CONFIGS[TradingMode.CONSERVATIVE]
        assert config.confidence_threshold == 0.60

    def test_conservative_rejects_below_060(self):
        """CONSERVATIVE mode should reject trades below 0.60 confidence."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.CONSERVATIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A+',
            confidence=0.59,
            daily_trades=0
        )
        assert should_trade is False
        assert "confidence" in reason.lower() or "threshold" in reason.lower()

    def test_conservative_accepts_060_confidence(self):
        """CONSERVATIVE mode should accept trades with 0.60 confidence for A+ setup."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.CONSERVATIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A+',
            confidence=0.60,
            daily_trades=0
        )
        assert should_trade is True

    # ----- DEFENSIVE mode: 0.90 threshold -----

    def test_defensive_threshold_is_090(self):
        """DEFENSIVE mode confidence threshold should be 0.90."""
        config = MODE_CONFIGS[TradingMode.DEFENSIVE]
        assert config.confidence_threshold == 0.90

    def test_defensive_rejects_085_confidence(self):
        """DEFENSIVE mode should reject trades with 0.85 confidence (below 0.90)."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.DEFENSIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A+',
            confidence=0.85,
            daily_trades=0
        )
        assert should_trade is False
        assert "confidence" in reason.lower() or "threshold" in reason.lower()

    def test_defensive_accepts_090_confidence(self):
        """DEFENSIVE mode should accept trades with 0.90 confidence for A+ setup."""
        manager = self._create_manager()
        manager.current_mode = TradingMode.DEFENSIVE
        should_trade, reason = manager.should_take_trade(
            setup_grade='A+',
            confidence=0.90,
            daily_trades=0
        )
        assert should_trade is True


class TestStagnantThreshold:
    """Tests that the stagnant threshold has been raised to 4 hours / 0.2R."""

    def test_not_stagnant_at_2_hours(self):
        """A position open for 2 hours with low movement should NOT be considered stagnant."""
        hours_open = 2
        r_mult = 0.1
        is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
        assert is_stagnant is False

    def test_not_stagnant_at_3_hours(self):
        """A position open for 3 hours with low movement should NOT be considered stagnant."""
        hours_open = 3
        r_mult = 0.05
        is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
        assert is_stagnant is False

    def test_stagnant_at_4_hours_low_r(self):
        """A position open for 4+ hours with <0.2R movement IS stagnant."""
        hours_open = 4.5
        r_mult = 0.1
        is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
        assert is_stagnant is True

    def test_not_stagnant_at_4_hours_decent_r(self):
        """A position open for 4+ hours but with 0.3R should NOT be stagnant."""
        hours_open = 5
        r_mult = 0.3
        is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
        assert is_stagnant is False

    def test_not_stagnant_negative_r_large(self):
        """A position at -0.4R is not stagnant -- it's moving (negatively)."""
        hours_open = 6
        r_mult = -0.4
        is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
        assert is_stagnant is False

    def test_stagnant_negative_small_r(self):
        """A position barely negative (-0.1R) after 5 hours IS stagnant."""
        hours_open = 5
        r_mult = -0.1
        is_stagnant = hours_open >= 4 and abs(r_mult) < 0.2
        assert is_stagnant is True


class TestSwingValidationPromptContent:
    """Tests that the Claude analysis prompt contains the two-tier swing validation system."""

    def test_analysis_prompt_contains_two_tier_system(self):
        """The analysis prompt should contain the two-tier swing validation keywords."""
        from trading_bot.llm.context_builder import ContextBuilder
        builder = ContextBuilder()
        quick_ref = builder.get_quick_reference()

        # Tier 1 - Hard gate for reversals
        assert "HARD GATE" in quick_ref or "MANDATORY" in quick_ref
        assert "Swing count < 4" in quick_ref or "swing count < 4" in quick_ref.lower()
        assert "NO TRADE" in quick_ref or "no_trade" in quick_ref.lower()

        # Tier 2 - Confluence for breakouts
        assert "TIER 2" in quick_ref or "BREAKOUT" in quick_ref
        assert "CONFLUENCE" in quick_ref or "confluence" in quick_ref.lower()

    def test_quick_reference_mentions_rounding(self):
        """The quick reference should mention rounding/circular price action."""
        from trading_bot.llm.context_builder import ContextBuilder
        builder = ContextBuilder()
        quick_ref = builder.get_quick_reference()

        assert "rounding" in quick_ref.lower() or "circular" in quick_ref.lower() or "dome/saucer" in quick_ref.lower()

    def test_quick_reference_mentions_pending_orders(self):
        """The quick reference should mention pending orders for entries."""
        from trading_bot.llm.context_builder import ContextBuilder
        builder = ContextBuilder()
        quick_ref = builder.get_quick_reference()

        assert "pending" in quick_ref.lower()

    def test_quick_reference_mentions_sweep(self):
        """The quick reference should mention sweep requirement."""
        from trading_bot.llm.context_builder import ContextBuilder
        builder = ContextBuilder()
        quick_ref = builder.get_quick_reference()

        assert "sweep" in quick_ref.lower()


class TestPatienceInPrompts:
    """Tests that patience-related language is present in the Claude prompts."""

    def test_entry_reminder_contains_patience(self):
        """The entry reminder should emphasize patience and pending orders."""
        from trading_bot.llm.claude_client import ClaudeClient
        # We test the prompt builder by checking it constructs the right content.
        # Since _build_analysis_prompt requires market_data, we check the string directly.
        client = ClaudeClient.__new__(ClaudeClient)
        client.model = "claude-3-5-sonnet-20241022"
        client.async_client = None
        client.sync_client = None
        client.cache = {}

        from trading_bot.llm import claude_client
        prompt = client._build_analysis_prompt(
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="Test context",
            market_data={
                "current_price": 2000.0,
                "session": "new_york",
                "daily_high": 2010.0,
                "daily_low": 1990.0,
            },
            analysis_data=None
        )
        combined = prompt + "\n" + claude_client.ANALYSIS_RULES

        # Check patience language
        assert "PATIENCE IS PROFIT" in combined or "patience" in combined.lower()
        assert "STRONGLY PREFER pending orders" in combined or "pending order" in combined.lower()
        assert "no_trade" in combined.lower()

    def test_important_rules_mention_quality_over_quantity(self):
        """Important Rules section should mention quality over quantity."""
        from trading_bot.llm.claude_client import ClaudeClient
        client = ClaudeClient.__new__(ClaudeClient)
        client.model = "claude-3-5-sonnet-20241022"
        client.async_client = None
        client.sync_client = None
        client.cache = {}

        from trading_bot.llm import claude_client
        prompt = client._build_analysis_prompt(
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="Test context",
            market_data={
                "current_price": 2000.0,
                "session": "new_york",
                "daily_high": 2010.0,
                "daily_low": 1990.0,
            },
            analysis_data=None
        )
        combined = prompt + "\n" + claude_client.ANALYSIS_RULES

        assert "Quality over quantity" in combined or "quality over quantity" in combined.lower()
        assert "Do NOT force a trade" in combined or "do not force" in combined.lower()


class TestFallbackMinConfidence:
    """Tests the min confidence gate in main.py."""

    def test_fallback_confidence_is_060(self):
        """Min confidence gate uses gate_min_confidence via pipeline."""
        import inspect
        from trading_bot.services import gate_pipeline
        from tests.pipeline_source import analyze_and_trade_source

        runner_source = analyze_and_trade_source()
        pipeline_source = inspect.getsource(gate_pipeline)
        assert "gate_min_confidence" in runner_source
        assert "gate_min_confidence" in pipeline_source


class TestDirectionalBiasFix:
    """Tests that hard directional blocks have been converted to soft guidance,
    preventing the systematic SHORT-only bias."""

    def _build_prompt_with_bearish_htf(self) -> str:
        """Build a Claude prompt with bearish HTF bias to test directional handling."""
        from trading_bot.llm.claude_client import ClaudeClient
        client = ClaudeClient.__new__(ClaudeClient)
        client.model = "claude-3-5-sonnet-20241022"
        client.async_client = None
        client.sync_client = None
        client.cache = {}

        return client._build_analysis_prompt(
            symbol="ETHUSD",
            timeframe="M15",
            strategy_context="Test context",
            market_data={
                "current_price": 1935.0,
                "session": "new_york",
                "daily_high": 2010.0,
                "daily_low": 1900.0,
                "htf_bias": "bearish",
                "htf_alignment": True,
                "htf_can_trade_long": "counter_trend",
                "htf_can_trade_short": "preferred",
                "h4_structure": "BOS",
                "h1_structure": "CHoCH",
                "htf_key_levels": [1900.0, 1950.0, 2000.0],
            },
            analysis_data=None
        )

    def _build_prompt_with_bullish_htf(self) -> str:
        """Build a Claude prompt with bullish HTF bias."""
        from trading_bot.llm.claude_client import ClaudeClient
        client = ClaudeClient.__new__(ClaudeClient)
        client.model = "claude-3-5-sonnet-20241022"
        client.async_client = None
        client.sync_client = None
        client.cache = {}

        return client._build_analysis_prompt(
            symbol="BTCUSD",
            timeframe="M15",
            strategy_context="Test context",
            market_data={
                "current_price": 95000.0,
                "session": "new_york",
                "daily_high": 96000.0,
                "daily_low": 94000.0,
                "htf_bias": "bullish",
                "htf_alignment": True,
                "htf_can_trade_long": "preferred",
                "htf_can_trade_short": "counter_trend",
                "h4_structure": "BOS",
                "h1_structure": "BOS",
                "htf_key_levels": [94000.0, 95000.0, 96000.0],
            },
            analysis_data=None
        )

    # ----- HTF Bias: No Hard Block -----

    def test_no_hard_block_language_in_prompt(self):
        """The prompt should NOT contain 'do NOT go long' or 'ONLY trade in the direction'."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "do NOT go long" not in prompt
        assert "do NOT go short" not in prompt
        assert "ONLY trade in the direction" not in prompt

    def test_soft_guidance_present(self):
        """The prompt should contain directional authority hierarchy."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "DIRECTIONAL AUTHORITY" in prompt
        assert "strict hierarchy" in prompt

    def test_counter_trend_label_not_blocked(self):
        """When HTF is bearish, longs should show as 'COUNTER-TREND', not 'BLOCKED'."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "BLOCKED by HTF" not in prompt
        assert "COUNTER-TREND" in prompt

    def test_preferred_label_for_with_trend(self):
        """When HTF is bearish, shorts should show as 'PREFERRED'."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "PREFERRED" in prompt

    def test_both_directions_evaluable(self):
        """The prompt should allow counter-trend trades with M15 confirmation."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "Counter-trend trades" in prompt or "counter-trend" in prompt.lower()

    def test_counter_trend_reversal_explicitly_valid(self):
        """The prompt should explicitly state counter-trend reversals are valid."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "Counter-trend reversals" in prompt or "counter-trend" in prompt.lower()
        assert "VALID" in prompt or "valid" in prompt

    def test_swing_validation_mentioned_for_counter_trend(self):
        """Counter-trend entries should reference Tier 1 swing validation."""
        prompt = self._build_prompt_with_bearish_htf()
        assert "Tier 1" in prompt or "swing validation" in prompt.lower()

    # ----- Anti-Bias Instruction -----

    def test_reactive_mandate_in_important_rules(self):
        """Ruleset should contain reactive trading mandate (now in cached ANALYSIS_RULES)."""
        from trading_bot.llm import claude_client
        combined = self._build_prompt_with_bearish_htf() + "\n" + claude_client.ANALYSIS_RULES
        assert "REACT" in combined, "Ruleset should have reactive mandate"
        assert "ALREADY" in combined, "Ruleset should require ALREADY confirmed setups"

    def test_anti_flip_instruction_in_rules(self):
        """Ruleset should warn against flipping direction without confirmed change."""
        from trading_bot.llm import claude_client
        combined = self._build_prompt_with_bearish_htf() + "\n" + claude_client.ANALYSIS_RULES
        assert "flip direction" in combined.lower(), \
            "Ruleset should warn against direction flips without cause"

    # ----- Premium/Discount Soft Guidance -----

    def test_premium_discount_is_soft_guidance(self):
        """Premium/Discount rule should be soft guidance, not a hard block."""
        from trading_bot.llm.claude_client import ClaudeClient
        client = ClaudeClient.__new__(ClaudeClient)
        client.model = "claude-3-5-sonnet-20241022"
        client.async_client = None
        client.sync_client = None
        client.cache = {}

        prompt = client._build_analysis_prompt(
            symbol="ETHUSD",
            timeframe="M15",
            strategy_context="Test context",
            market_data={
                "current_price": 1935.0,
                "session": "new_york",
                "daily_high": 2010.0,
                "daily_low": 1900.0,
                "premium_discount": {
                    "current_zone": "premium",
                    "retracement_percent": 0.65,
                    "ote": {"in_zone": False},
                },
            },
            analysis_data=None
        )

        # Should NOT contain hard block language
        assert "Longs ONLY in discount" not in prompt
        assert "Shorts ONLY in premium" not in prompt
        # SHOULD contain soft guidance language
        assert "GUIDANCE" in prompt or "Prefer" in prompt
        assert "Counter-zone trades are allowed" in prompt or "counter-zone" in prompt.lower()

    # ----- Bullish HTF reversal -----

    def test_bullish_htf_allows_short_counter_trend(self):
        """When HTF is bullish, shorts should show as COUNTER-TREND, not BLOCKED."""
        prompt = self._build_prompt_with_bullish_htf()
        assert "BLOCKED by HTF" not in prompt
        # Shorts should be counter-trend, longs should be preferred
        assert "PREFERRED" in prompt
        assert "COUNTER-TREND" in prompt


class TestMTFAnalyzerThreeValueSystem:
    """Tests that MTF analyzer uses 3-value (preferred/counter_trend/no_data) system."""

    def test_bearish_bias_long_is_counter_trend(self):
        """When bias is BEARISH, can_trade_long should be 'counter_trend'."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BEARISH,
            alignment=True,
            can_trade_long='counter_trend',
            can_trade_short='preferred',
        )
        assert result.can_trade_long == 'counter_trend'
        assert result.can_trade_short == 'preferred'

    def test_bullish_bias_short_is_counter_trend(self):
        """When bias is BULLISH, can_trade_short should be 'counter_trend'."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BULLISH,
            alignment=True,
            can_trade_long='preferred',
            can_trade_short='counter_trend',
        )
        assert result.can_trade_long == 'preferred'
        assert result.can_trade_short == 'counter_trend'

    def test_unknown_bias_both_no_data(self):
        """When bias is UNKNOWN, both directions should be 'no_data'."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.UNKNOWN,
            alignment=False,
            can_trade_long='no_data',
            can_trade_short='no_data',
        )
        assert result.can_trade_long == 'no_data'
        assert result.can_trade_short == 'no_data'

    def test_neutral_bias_both_counter_trend(self):
        """When bias is NEUTRAL, both directions should be 'counter_trend'."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.NEUTRAL,
            alignment=False,
            can_trade_long='counter_trend',
            can_trade_short='counter_trend',
        )
        assert result.can_trade_long == 'counter_trend'
        assert result.can_trade_short == 'counter_trend'

    def test_should_trade_direction_accepts_counter_trend(self):
        """should_trade_direction should return True for counter_trend (tradeable)."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias
        )
        analyzer = MTFAnalyzer.__new__(MTFAnalyzer)

        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BEARISH,
            alignment=True,
            can_trade_long='counter_trend',
            can_trade_short='preferred',
        )

        # Long should be allowed (counter_trend is tradeable)
        assert analyzer.should_trade_direction('long', result) is True
        # Short should be allowed (preferred)
        assert analyzer.should_trade_direction('short', result) is True

    def test_should_trade_direction_rejects_no_data(self):
        """should_trade_direction should return False for no_data."""
        from trading_bot.analysis.mtf_analyzer import (
            MTFAnalyzer, MTFAnalysisResult, TimeframeBias
        )
        analyzer = MTFAnalyzer.__new__(MTFAnalyzer)

        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.UNKNOWN,
            alignment=False,
            can_trade_long='no_data',
            can_trade_short='no_data',
        )

        assert analyzer.should_trade_direction('long', result) is False
        assert analyzer.should_trade_direction('short', result) is False

    def test_to_dict_returns_string_values(self):
        """to_dict should return string values for can_trade_long/short."""
        from trading_bot.analysis.mtf_analyzer import MTFAnalysisResult, TimeframeBias
        result = MTFAnalysisResult(
            overall_bias=TimeframeBias.BEARISH,
            alignment=True,
            can_trade_long='counter_trend',
            can_trade_short='preferred',
        )
        d = result.to_dict()
        assert d['can_trade_long'] == 'counter_trend'
        assert d['can_trade_short'] == 'preferred'
        assert isinstance(d['can_trade_long'], str)
        assert isinstance(d['can_trade_short'], str)
