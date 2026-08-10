# tests/test_claude_signal_trust.py
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from trading_bot.config import TradingSettings
from trading_bot.services.claude_signal_trust import (
    is_claude_signal_trust_active,
    should_apply_claude_signal_trust,
    should_ignore_judge_demote,
)
from trading_bot.utils.win_optimization import apply_demote_policy
from trading_bot.services.entry_gates import ZoneGateSettings
from trading_bot.services.gate_pipeline import (
    evaluate_entry_gates,
    evaluate_trade_permission_gates,
)
from trading_bot.services.parity_gates import (
    evaluate_displacement_parity,
    evaluate_zone_conversion,
)
from trading_bot.services.post_claude_gates import (
    PostClaudeGateInput,
    run_post_claude_gates,
)
from trading_bot.services.signal_normalizer import NormalizedSignal
from trading_bot.services.trade_context import TradeContext


def _trust_signal(**kwargs):
    base = dict(
        direction="long",
        confidence=0.82,
        entry_price=1.0850,
        stop_loss=1.0840,
        take_profit=1.0950,
        trade_type="intraday",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _trust_norm(**kwargs):
    base = dict(
        entry=1.0850,
        sl=1.0840,
        tp=1.0950,
        direction="long",
        rejected=False,
    )
    base.update(kwargs)
    return NormalizedSignal(**base)


def _trust_df_with_atr():
    rows = []
    for i in range(30):
        p = 1.08 + i * 0.0001
        rows.append(
            {"open": p, "high": p + 0.0010, "low": p - 0.0005, "close": p + 0.0002}
        )
    return pd.DataFrame(rows)


def _post_claude_input(**kwargs):
    """Builder mirrored from tests/test_post_claude_gates.py fixtures."""
    base = dict(
        symbol="EURUSD",
        trade_signal=_trust_signal(),
        norm=_trust_norm(),
        market_data={"d1_bias": "bullish"},
        analysis_results={
            "volume": {"relative_volume": 1.0},
            "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
            "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
        },
        pd_analysis=None,
        current_price=1.0850,
        zone_settings=ZoneGateSettings(gate_mode="disabled"),
        use_zone_gate=False,
        is_kill_zone=True,
        session_name="london",
        last_signal_direction={},
        direction_flipped=False,
        df=_trust_df_with_atr(),
    )
    base.update(kwargs)
    return PostClaudeGateInput(**base)


class TestClaudeSignalTrustConfig:
    def test_default_is_off(self):
        assert TradingSettings.model_fields["claude_signal_trust_mode"].default == "off"

    def test_active_helper(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        assert is_claude_signal_trust_active() is True
        assert should_apply_claude_signal_trust("long") is True
        assert should_apply_claude_signal_trust("NO_TRADE") is False
        assert should_apply_claude_signal_trust("") is False

    def test_off_helper(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "off")
        assert is_claude_signal_trust_active() is False
        assert should_apply_claude_signal_trust("long") is False


class TestEntryGatesTrustBypass:
    def test_buy_limit_no_displacement_passes_when_trusted(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        monkeypatch.setattr(settings.trading, "ict_confirmation_mode", "active")
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.62,
            actual_rr=2.04,
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bullish",
            order_type="buy_limit",
            regime_type="ranging",
            relative_volume=0.25,
            claude_signal_trust=True,
            analysis_results={"liquidity": {}},
        )
        out = evaluate_entry_gates(
            ctx,
            zone_settings=ZoneGateSettings(gate_mode="active"),
            use_zone_gate=False,
        )
        assert out.blocked is False
        assert any("claude_trust_bypass" in p for p in ctx.gate_path) or any(
            "claude_trust_bypass" in p for p in (out.gate_path or [])
        )


def _blocking_scaling_manager():
    mgr = MagicMock()
    mgr.should_take_trade.return_value = (False, "Low confidence for mode")
    mgr.get_mode_config.return_value = SimpleNamespace(confidence_threshold=0.70)
    return mgr


class TestPermissionGatesTrustBypass:
    def test_low_confidence_scaling_blocked_without_trust(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.50,
            actual_rr=2.0,
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bullish",
            claude_signal_trust=False,
        )
        out = evaluate_trade_permission_gates(
            ctx,
            scaling_manager=_blocking_scaling_manager(),
            daily_trades=0,
            gate_min_confidence=0.60,
        )
        assert out.blocked is True
        assert out.gate_id in ("scaling_manager", "min_confidence")

    def test_low_confidence_scaling_passes_when_trusted(self):
        ctx = TradeContext(
            symbol="XAUUSD",
            direction="long",
            confidence=0.50,
            actual_rr=2.0,
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bullish",
            claude_signal_trust=True,
        )
        out = evaluate_trade_permission_gates(
            ctx,
            scaling_manager=_blocking_scaling_manager(),
            daily_trades=0,
            gate_min_confidence=0.60,
        )
        assert out.blocked is False
        assert any("claude_trust_bypass" in p for p in ctx.gate_path) or any(
            "claude_trust_bypass" in p for p in (out.gate_path or [])
        )

    def test_correlation_passes_when_trusted(self):
        ctx = TradeContext(
            symbol="EURUSD",
            direction="long",
            confidence=0.80,
            actual_rr=2.5,
            d1_bias="bullish",
            h4_bias="bullish",
            m15_bias="bullish",
            claude_signal_trust=True,
        )
        out = evaluate_trade_permission_gates(
            ctx,
            scaling_manager=None,
            daily_trades=0,
            gate_min_confidence=0.60,
            correlation_check=lambda: (True, "EURUSD correlated with GBPUSD"),
        )
        assert out.blocked is False
        assert any("claude_trust_bypass" in p for p in ctx.gate_path) or any(
            "claude_trust_bypass" in p for p in (out.gate_path or [])
        )


class TestJudgeDemoteTrustIgnore:
    def test_should_ignore_judge_demote_helper(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        assert should_ignore_judge_demote(direction="long") is True
        assert should_ignore_judge_demote(direction="NO_TRADE") is False

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "off")
        assert should_ignore_judge_demote(direction="long") is False

    def test_demote_ignored_under_trust(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        ignore = should_ignore_judge_demote(direction="long")
        out = apply_demote_policy(
            direction="long",
            current_price=2000.0,
            original_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            order_type="market",
            suggested_entry=1995.0,
            lean_sweep_fade=False,
            ignore_demote=ignore,
        )
        assert ignore is True
        assert out["order_type"] == "market"
        assert out["action"] != "limit"
        assert out.get("reason") == "claude_trust_demote_ignored"

    def test_demote_keeps_limit_under_trust(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        ignore = should_ignore_judge_demote(direction="short")
        out = apply_demote_policy(
            direction="short",
            current_price=2000.0,
            original_entry=2005.0,
            stop_loss=2015.0,
            take_profit=1980.0,
            order_type="sell_limit",
            suggested_entry=2010.0,
            lean_sweep_fade=False,
            ignore_demote=ignore,
        )
        assert out["order_type"] == "sell_limit"
        assert out.get("reason") == "claude_trust_demote_ignored"

    def test_demote_still_demotes_when_trust_and_lean_off(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "off")
        ignore = should_ignore_judge_demote(direction="long")
        out = apply_demote_policy(
            direction="long",
            current_price=2000.0,
            original_entry=2005.0,
            stop_loss=1990.0,
            take_profit=2030.0,
            order_type="market",
            suggested_entry=1995.0,
            lean_sweep_fade=False,
            ignore_demote=ignore,
        )
        assert ignore is False
        assert out["action"] == "limit"
        assert out["order_type"] == "buy_limit"
        assert out.get("reason") != "claude_trust_demote_ignored"
        assert out.get("reason") != "lean_demote_ignored"


class TestParityGatesTrustKeepOrderType:
    """Zone conversion / displacement parity must keep Claude market under trust."""

    def test_zone_conversion_keeps_market_when_trusted(self):
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
            claude_signal_trust=True,
        )
        assert out.blocked is False
        assert out.action == "unchanged"
        assert out.new_order_type is None
        assert "claude_trust_bypass:zone_conversion" in out.gate_path

    def test_zone_conversion_still_blocks_without_trust(self):
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
            claude_signal_trust=False,
        )
        assert out.blocked is True
        assert out.gate_id == "zone_conversion_failed"

    def test_displacement_parity_allows_market_when_trusted(self):
        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=False,
            claude_signal_trust=True,
        )
        assert out.blocked is False
        assert out.action == "allow_market"
        assert "claude_trust_bypass:displacement_parity" in out.gate_path

    def test_displacement_parity_rejects_without_trust(self):
        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
            htf_aligned=False,
            has_displacement=False,
            lean_sweep_fade=False,
            claude_signal_trust=False,
        )
        assert out.blocked is True
        assert out.gate_id == "no_displacement"

    def test_post_claude_price_phase_keeps_market_under_trust(self, monkeypatch):
        """Market emit + trust: invalid zone / no displacement must not demote."""
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        inp = _post_claude_input(
            trade_signal=_trust_signal(order_type="market"),
            run_parity_gates=True,
            zone_valid=False,
            zone_reason="premium for long",
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[], bearish_fvgs=[]),
                "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
                "distribution_confirmed": False,
                "amd_phase": "distribution",
            },
            pd_analysis=None,
        )
        result = run_post_claude_gates(inp, stop_after="price")
        assert result.blocked is False
        assert (result.order_type or "").lower() == "market"
        assert "claude_trust_bypass:zone_conversion" in result.gate_path
        assert "claude_trust_bypass:displacement_parity" in result.gate_path

    def test_permission_bypass_tags_reach_top_level_gate_path(self, monkeypatch):
        """Soft-pass tags on ctx must appear on run_post_claude_gates.gate_path."""
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        inp = _post_claude_input(
            trade_signal=_trust_signal(confidence=0.50, order_type="buy_limit"),
            scaling_manager=_blocking_scaling_manager(),
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
            run_parity_gates=False,
        )
        result = run_post_claude_gates(inp, stop_after="permission")
        assert result.blocked is False
        assert any("claude_trust_bypass" in p for p in result.gate_path)


class TestSafetyGatesRemainHardUnderTrust:
    def test_min_rr_still_enforced_in_price_phase(self, monkeypatch):
        """actual_rr below hard floor must block even when trust is active."""
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        # Wide SL + tiny TP → actual_rr << min (and below hard floor 1.5)
        inp = _post_claude_input(
            trade_signal=_trust_signal(
                entry_price=1.0850,
                stop_loss=1.0800,
                take_profit=1.0855,
            ),
            norm=_trust_norm(entry=1.0850, sl=1.0800, tp=1.0855),
        )
        result = run_post_claude_gates(inp, stop_after="price")
        assert result.blocked is True
        assert result.gate_id == "rr_hard_floor"
        assert result.actual_rr < result.min_rr

    def test_trust_does_not_skip_flip_guard(self, monkeypatch):
        """Flip guard must still hard-block under trust (fixture from post_claude tests)."""
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        last = {
            "EURUSD": (
                "long",
                datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        }
        inp = _post_claude_input(
            norm=_trust_norm(direction="short"),
            trade_signal=_trust_signal(direction="short", confidence=0.75),
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[], bearish_fvgs=[1]),
                "order_blocks": SimpleNamespace(bullish_obs=[], bearish_obs=[1]),
            },
            last_signal_direction=last,
        )
        result = run_post_claude_gates(inp, stop_after="complete")
        assert result.blocked is True
        assert result.gate_id == "direction_flip"
        assert "claude_signal_trust" in result.gate_path


class TestPrepareOrderAndPlaybookTrust:
    """Late-path strategy gates that still killed Claude emits after entry soft-pass."""

    def test_validate_limit_zone_allows_extreme_under_trust(self, monkeypatch):
        from trading_bot.config import settings
        from trading_bot.execution.trade_execution import validate_limit_zone

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        out = validate_limit_zone(
            "buy_limit", 0.82, lean_sweep_fade=False, claude_signal_trust=True
        )
        assert out.blocked is False
        assert "claude_trust_bypass:limit_zone" in out.gate_path

    def test_validate_limit_zone_still_blocks_without_trust(self):
        from trading_bot.execution.trade_execution import validate_limit_zone

        out = validate_limit_zone(
            "buy_limit", 0.82, lean_sweep_fade=False, claude_signal_trust=False
        )
        assert out.blocked is True
        assert out.gate_id == "zone_block"

    def test_auto_convert_keeps_market_under_trust(self, monkeypatch):
        from trading_bot.config import settings
        from trading_bot.execution.trade_execution import auto_convert_to_pending

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        ot = auto_convert_to_pending(
            "market",
            "long",
            1.0800,
            1.0850,
            lean_sweep_fade=False,
            claude_signal_trust=True,
        )
        assert ot == "market"

    def test_playbook_soft_pass_helper(self, monkeypatch):
        from trading_bot.config import settings
        from trading_bot.services.claude_signal_trust import (
            should_soft_pass_playbook,
        )

        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "active")
        assert should_soft_pass_playbook(direction="long") is True
        monkeypatch.setattr(settings.trading, "claude_signal_trust_mode", "off")
        assert should_soft_pass_playbook(direction="long") is False
