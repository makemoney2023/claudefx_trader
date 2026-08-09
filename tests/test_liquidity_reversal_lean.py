"""Liquidity-reversal lean mode — sweep-fade unblocks for SB-style edge."""

import pytest

from trading_bot.analysis.kill_zones import claude_analysis_allowed
from trading_bot.config import TradingSettings, settings
from trading_bot.services.entry_gates import (
    ZoneGateSettings,
    evaluate_amd_distribution_gate,
    evaluate_confluence_gate,
    evaluate_direction_alignment_gate,
    evaluate_htf_alignment_gate,
    evaluate_ict_confirmation_gate,
    evaluate_m15_gate,
    evaluate_zone_gate,
)
from trading_bot.services.opportunity_scanner import pre_judge_zone_block_reason
from trading_bot.services.parity_gates import (
    evaluate_displacement_parity,
    evaluate_zone_conversion,
)
from trading_bot.services.setup_fingerprint import (
    SetupFingerprint,
    build_setup_fingerprint,
    classify_setup_family,
    is_lean_sweep_fade,
    is_liquidity_reversal_lean_active,
)
from trading_bot.services.trade_context import TradeContext


def _ssl_liquidity():
    return {
        "recent_sweeps": [
            {"type": "sell_side_liquidity", "reversal_detected": True}
        ]
    }


def _bsl_liquidity():
    return {
        "recent_sweeps": [
            {"type": "buy_side_liquidity", "reversal_detected": True}
        ]
    }


@pytest.fixture
def lean_on(monkeypatch):
    monkeypatch.setattr(settings.trading, "liquidity_reversal_lean_mode", "active")
    monkeypatch.setenv("TRADING_LIQUIDITY_REVERSAL_LEAN_MODE", "active")


@pytest.fixture
def lean_off(monkeypatch):
    monkeypatch.setattr(settings.trading, "liquidity_reversal_lean_mode", "off")
    monkeypatch.setenv("TRADING_LIQUIDITY_REVERSAL_LEAN_MODE", "off")


class TestLeanConfig:
    def test_field_default_is_off(self):
        assert (
            TradingSettings.model_fields["liquidity_reversal_lean_mode"].default
            == "off"
        )

    def test_active_helper(self, lean_on):
        assert is_liquidity_reversal_lean_active() is True

    def test_off_helper(self, lean_off):
        assert is_liquidity_reversal_lean_active() is False


class TestLeanSweepFadeHelper:
    def test_requires_lean_and_directional_sweep(self, lean_on):
        ar = {"liquidity": _ssl_liquidity()}
        assert is_lean_sweep_fade("long", ar) is True
        assert is_lean_sweep_fade("short", ar) is False

    def test_false_when_lean_off(self, lean_off):
        ar = {"liquidity": _ssl_liquidity()}
        assert is_lean_sweep_fade("long", ar) is False

    def test_live_enum_types_match(self, lean_on):
        """Regression: 'ssl' is NOT a substring of 'sell_side_liquidity'."""
        from trading_bot.services.setup_fingerprint import has_directional_sweep

        assert has_directional_sweep(
            "long", {"recent_sweeps": [{"type": "sell_side_liquidity"}]}
        )
        assert has_directional_sweep(
            "short", {"recent_sweeps": [{"type": "buy_side_liquidity"}]}
        )
        assert has_directional_sweep(
            "long", {"recent_sweeps": [{"type": "equal_lows"}]}
        )
        assert not has_directional_sweep(
            "long", {"recent_sweeps": [{"type": "buy_side_liquidity"}]}
        )

    def test_false_for_htf_displacement_continuation(self, lean_on):
        ar = {
            "liquidity": _ssl_liquidity(),
            "displacement": {"distribution_confirmed": True, "last_bullish": True},
            "fresh_displacement_direction": "bullish",
        }
        # HTF aligned long + displacement → continuation, not lean fade
        assert (
            is_lean_sweep_fade(
                "long",
                ar,
                d1_bias="bullish",
                h4_bias="bullish",
            )
            is False
        )


class TestClassifyUnderLean:
    def test_htf_aligned_sweep_without_disp_is_liquidity_reversal(self, lean_on):
        family = classify_setup_family(
            direction="long",
            order_type="market",
            d1_bias="bullish",
            h4_bias="bullish",
            sweep=True,
            mss=False,
            displacement=False,
        )
        assert family == "liquidity_reversal"

    def test_fingerprint_tags_sb_lean(self, lean_on):
        fp = build_setup_fingerprint(
            direction="long",
            order_type="market",
            d1_bias="bearish",
            h4_bias="bearish",
            analysis_results={"liquidity": _ssl_liquidity()},
        )
        assert fp.family == "liquidity_reversal"
        assert "sb_lean" in fp.tags


class TestM15Lean:
    def test_opposing_m15_passes_with_lean_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.65,
            actual_rr=2.0,
            m15_bias="bearish",
            d1_bias="bearish",
            h4_bias="bearish",
            amd_phase="distribution",
            order_type="market",
            analysis_results={"liquidity": _ssl_liquidity()},
        )
        out = evaluate_m15_gate(ctx)
        assert out.blocked is False

    def test_opposing_m15_still_blocks_without_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.65,
            actual_rr=2.0,
            m15_bias="bearish",
            d1_bias="bullish",
            h4_bias="bullish",
            amd_phase="distribution",
            order_type="market",
            analysis_results={"liquidity": {"recent_sweeps": []}},
        )
        out = evaluate_m15_gate(ctx)
        assert out.blocked is True
        assert out.gate_id == "m15_structure"


class TestHtfLean:
    def test_dual_oppose_passes_with_lean_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="EURUSD",
            direction="long",
            confidence=0.65,
            actual_rr=2.0,
            d1_bias="bearish",
            h4_bias="bearish",
            m15_bias="bearish",
            analysis_results={"liquidity": _ssl_liquidity()},
        )
        out = evaluate_htf_alignment_gate(ctx)
        assert out.blocked is False
        assert out.gate_path == ["htf_lean_sweep_fade"]

    def test_dual_oppose_blocks_without_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="EURUSD",
            direction="long",
            confidence=0.65,
            actual_rr=2.0,
            d1_bias="bearish",
            h4_bias="bearish",
            analysis_results={"liquidity": {"recent_sweeps": []}},
        )
        out = evaluate_htf_alignment_gate(ctx)
        assert out.blocked is True
        assert out.gate_id == "htf_both_oppose"


class TestIctLean:
    def test_sweep_only_passes_active(self, lean_on):
        fp = SetupFingerprint(
            family="liquidity_reversal",
            tags=("sweep", "sb_lean"),
            key="liquidity_reversal|long|market|ny|x|sweep",
            has_sweep=True,
            has_mss=False,
            has_displacement=False,
            htf_aligned=False,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="market",
            mode="active",
            lean_sweep_fade=True,
        )
        assert out.blocked is False
        assert out.would_block is False

    def test_no_sweep_still_blocks(self, lean_on):
        fp = SetupFingerprint(
            family="liquidity_reversal",
            tags=("sb_lean",),
            key="liquidity_reversal|long|market|ny|x|none",
            has_sweep=False,
            has_mss=False,
            has_displacement=False,
            htf_aligned=False,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="market",
            mode="active",
            lean_sweep_fade=True,
        )
        assert out.blocked is True

    def test_lean_off_still_requires_full_stack(self, lean_off):
        fp = SetupFingerprint(
            family="liquidity_reversal",
            tags=("sweep",),
            key="liquidity_reversal|long|market|ny|x|sweep",
            has_sweep=True,
            has_mss=False,
            has_displacement=False,
            htf_aligned=False,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="market",
            mode="active",
            lean_sweep_fade=False,
        )
        assert out.blocked is True


class TestZoneLean:
    def test_wrong_zone_sweep_alone_allowed(self, lean_on):
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=2.0,
            retrace=0.36,
            zone_str="discount",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=True,
            has_displacement=False,
            lean_sweep_fade=True,
        )
        assert result.blocked is False
        assert result.decision == "allowed_wrong_zone_sweep_lean"

    def test_wrong_zone_no_sweep_still_blocked(self, lean_on):
        result = evaluate_zone_gate(
            direction="short",
            confidence=0.60,
            actual_rr=2.0,
            retrace=0.36,
            zone_str="discount",
            d1_bias="bearish",
            is_index=False,
            settings=ZoneGateSettings(),
            symbol="XAUUSD",
            has_sweep=False,
            has_displacement=False,
            lean_sweep_fade=False,
        )
        assert result.blocked is True


class TestZoneConversionLean:
    def test_keeps_market_on_lean_sweep(self, lean_on):
        out = evaluate_zone_conversion(
            zone_valid=False,
            zone_reason="wrong zone",
            order_type="market",
            direction="long",
            current_entry=2000.0,
            current_price=2000.0,
            pd_analysis=None,
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=True,
        )
        assert out.blocked is False
        assert out.action == "unchanged"
        assert "zone_lean_market" in out.gate_path

    def test_still_fails_without_lean(self, lean_off):
        out = evaluate_zone_conversion(
            zone_valid=False,
            zone_reason="wrong zone",
            order_type="market",
            direction="long",
            current_entry=2000.0,
            current_price=2000.0,
            pd_analysis=None,
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=False,
        )
        assert out.blocked is True


class TestDisplacementParityLean:
    def test_allows_market_on_lean_sweep(self, lean_on):
        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=True,
        )
        assert out.blocked is False
        assert out.action == "allow_market"
        assert "liquidity_reversal_lean_ok" in out.gate_path

    def test_rejects_without_lean(self, lean_off):
        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=False,
        )
        assert out.blocked is True
        assert out.gate_id == "no_displacement"


class TestPreJudgeLean:
    def test_extreme_allowed_with_lean_sweep(self, lean_on):
        reason = pre_judge_zone_block_reason(
            order_type="market",
            direction="long",
            retrace_pct=0.70,
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=True,
        )
        assert reason is None

    def test_extreme_blocked_without_lean(self, lean_off):
        reason = pre_judge_zone_block_reason(
            order_type="market",
            direction="long",
            retrace_pct=0.70,
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=False,
        )
        assert reason is not None


class TestKillZoneLean:
    def test_outside_kz_allowed_when_lean_active(self, lean_on):
        assert (
            claude_analysis_allowed(
                False,
                claude_kill_zone_only=True,
                lean_active=True,
            )
            is True
        )

    def test_outside_kz_blocked_when_lean_off(self, lean_off):
        assert (
            claude_analysis_allowed(
                False,
                claude_kill_zone_only=True,
                lean_active=False,
            )
            is False
        )


class TestPromptLean:
    def test_lean_fade_marker_present_when_active(self, lean_on):
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)
        prompt = ClaudeClient._build_analysis_prompt(
            client,
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="",
            market_data={
                "current_price": 2000,
                "d1_bias": "bearish",
                "h4_bias": "bearish",
                "m15_bias": "bearish",
            },
            analysis_data=None,
        )
        assert "LEAN FADE" in prompt
        assert "LEAN FADE OVERRIDE" in prompt

    def test_system_identity_includes_lean_when_active(self, lean_on):
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)
        msgs = ClaudeClient._build_system_messages(
            client, strategy_context="## docs", strategy_mode="full"
        )
        text = " ".join(
            (b.get("text") if isinstance(b, dict) else str(b)) for b in msgs
        )
        assert "LEAN FADE EXCEPTION" in text

    def test_lean_fade_marker_absent_when_off(self, lean_off):
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)
        prompt = ClaudeClient._build_analysis_prompt(
            client,
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="",
            market_data={"current_price": 2000},
            analysis_data=None,
        )
        assert "LEAN FADE" not in prompt
        msgs = ClaudeClient._build_system_messages(
            client, strategy_context="## docs", strategy_mode="full"
        )
        text = " ".join(
            (b.get("text") if isinstance(b, dict) else str(b)) for b in msgs
        )
        assert "LEAN FADE EXCEPTION" not in text


class TestContinuationUnchanged:
    def test_continuation_still_needs_displacement(self, lean_on):
        fp = SetupFingerprint(
            family="continuation",
            tags=("htf",),
            key="continuation|long|market|ny|x|htf",
            has_sweep=False,
            has_mss=False,
            has_displacement=False,
            htf_aligned=True,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp, order_type="market", mode="active"
        )
        assert out.blocked is True


class TestDirectionAlignmentLean:
    def test_counter_d1_low_quality_passes_with_lean_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.55,
            actual_rr=1.5,
            d1_bias="bearish",
            h4_bias="neutral",
            trade_type="intraday",
            analysis_results={"liquidity": _ssl_liquidity()},
        )
        out = evaluate_direction_alignment_gate(ctx)
        assert out.blocked is False
        assert out.gate_path == ["direction_lean_sweep_fade"]

    def test_counter_d1_still_blocks_without_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.55,
            actual_rr=1.5,
            d1_bias="bearish",
            analysis_results={"liquidity": {"recent_sweeps": []}},
        )
        out = evaluate_direction_alignment_gate(ctx)
        assert out.blocked is True


class TestDemoteAndLimitZoneLean:
    def test_demote_keeps_market_on_lean_sweep(self, lean_on):
        from trading_bot.utils.win_optimization import apply_demote_policy

        out = apply_demote_policy(
            direction="long",
            current_price=2000.0,
            original_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            order_type="market",
            suggested_entry=1995.0,
            lean_sweep_fade=True,
        )
        assert out["order_type"] == "market"
        assert out["action"] != "limit"
        assert out.get("reason") == "lean_demote_ignored"

    def test_demote_still_limits_without_lean(self, lean_off):
        from trading_bot.utils.win_optimization import apply_demote_policy

        out = apply_demote_policy(
            direction="long",
            current_price=2000.0,
            original_entry=2005.0,
            stop_loss=1990.0,
            take_profit=2030.0,
            order_type="market",
            suggested_entry=1995.0,
            lean_sweep_fade=False,
        )
        assert out["action"] == "limit"
        assert out["order_type"] == "buy_limit"

    def test_auto_convert_skips_for_lean(self, lean_on):
        from trading_bot.execution.trade_execution import auto_convert_to_pending

        ot = auto_convert_to_pending(
            "market", "long", 1990.0, 2000.0, lean_sweep_fade=True
        )
        assert ot == "market"

    def test_validate_limit_zone_allows_lean_extreme(self, lean_on):
        from trading_bot.execution.trade_execution import validate_limit_zone

        out = validate_limit_zone(
            "buy_limit", 0.75, lean_sweep_fade=True
        )
        assert out.blocked is False


class TestIctLimitLean:
    def test_sell_limit_with_sweep_is_liquidity_reversal(self, lean_on):
        family = classify_setup_family(
            direction="short",
            order_type="sell_limit",
            d1_bias="bullish",
            h4_bias="neutral",
            sweep=True,
            mss=False,
            displacement=False,
        )
        assert family == "liquidity_reversal"

    def test_limit_ict_passes_sweep_only(self, lean_on):
        fp = build_setup_fingerprint(
            direction="short",
            order_type="sell_limit",
            d1_bias="bullish",
            h4_bias="neutral",
            analysis_results={"liquidity": _bsl_liquidity()},
        )
        assert fp.family == "liquidity_reversal"
        assert "sb_lean" in fp.tags
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="sell_limit",
            mode="active",
            lean_sweep_fade=True,
        )
        assert out.blocked is False


class TestPreJudgeLimitLean:
    def test_extreme_buy_limit_allowed_with_lean(self, lean_on):
        reason = pre_judge_zone_block_reason(
            order_type="buy_limit",
            direction="long",
            retrace_pct=0.75,
            lean_sweep_fade=True,
        )
        assert reason is None


class TestAmdConfluenceLean:
    def test_amd_distribution_rr_passes_on_lean(self, lean_on):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.65,
            actual_rr=1.5,
            amd_phase="distribution",
            analysis_results={
                "liquidity": _ssl_liquidity(),
                "amd_cycle": {"phase": "distribution"},
            },
        )
        out = evaluate_amd_distribution_gate(ctx)
        assert out.blocked is False

    def test_confluence_satisfied_on_lean_sweep(self, lean_on):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.55,
            analysis_results={"liquidity": _ssl_liquidity()},
        )
        out = evaluate_confluence_gate(
            ctx, confluence_count=1, min_confluence=2, confidence_override=0.60
        )
        assert out.blocked is False


class TestPromptToolLeanResiduals:
    def test_core_mandate_lean_block_in_system(self, lean_on):
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)
        msgs = ClaudeClient._build_system_messages(
            client, strategy_context="## docs", strategy_mode="full"
        )
        text = " ".join(
            (b.get("text") if isinstance(b, dict) else str(b)) for b in msgs
        )
        assert "CORE MANDATE LEAN" in text

    def test_tool_description_allows_lean_market(self, lean_on):
        from trading_bot.llm.claude_client import lean_aware_trade_signal_tools

        tools = lean_aware_trade_signal_tools(replay=False)
        desc = tools[0]["input_schema"]["properties"]["order_type"]["description"]
        assert "LEAN FADE" in desc
        assert "WITHOUT a closed" in desc

    def test_tier1_lean_swing_exemption_in_prompt(self, lean_on):
        from trading_bot.llm.claude_client import ClaudeClient

        client = ClaudeClient.__new__(ClaudeClient)
        prompt = ClaudeClient._build_analysis_prompt(
            client,
            symbol="XAUUSD",
            timeframe="M15",
            strategy_context="",
            market_data={"current_price": 2000, "m15_bias": "bearish"},
            analysis_data=None,
        )
        assert "LEAN SWING EXEMPTION" in prompt
