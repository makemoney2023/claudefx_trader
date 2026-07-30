"""Phase 3: live/replay parity for displacement, DXY, zone conversion."""

from types import SimpleNamespace

import pytest


class TestDisplacementParityGate:
    def test_market_without_displacement_rejects(self):
        from trading_bot.services.parity_gates import evaluate_displacement_parity

        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="distribution",
        )
        assert out.blocked is True
        assert out.gate_id == "no_displacement"

    def test_market_with_displacement_allows(self):
        from trading_bot.services.parity_gates import evaluate_displacement_parity

        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=True,
            amd_phase="distribution",
        )
        assert out.blocked is False
        assert out.action == "allow_market"

    def test_market_manipulation_converts(self):
        from trading_bot.services.parity_gates import evaluate_displacement_parity

        out = evaluate_displacement_parity(
            order_type="market",
            distribution_confirmed=False,
            amd_phase="manipulation",
        )
        assert out.blocked is False
        assert out.action == "convert_pending"

    def test_limit_unchanged(self):
        from trading_bot.services.parity_gates import evaluate_displacement_parity

        out = evaluate_displacement_parity(
            order_type="buy_limit",
            distribution_confirmed=False,
            amd_phase="distribution",
        )
        assert out.blocked is False
        assert out.action == "unchanged"


class TestDxyParityGate:
    def test_conflict_returns_half_size(self):
        from trading_bot.services.parity_gates import evaluate_dxy_parity

        out = evaluate_dxy_parity(
            symbol="EURUSD", direction="long", dxy_confirmation="short"
        )
        assert out.blocked is False
        assert out.size_multiplier == 0.5
        assert out.gate_id == "dxy_conflict"

    def test_aligned_no_haircut(self):
        from trading_bot.services.parity_gates import evaluate_dxy_parity

        out = evaluate_dxy_parity(
            symbol="EURUSD", direction="long", dxy_confirmation="long"
        )
        assert out.size_multiplier == 1.0

    def test_non_fx_ignored(self):
        from trading_bot.services.parity_gates import evaluate_dxy_parity

        out = evaluate_dxy_parity(
            symbol="XAUUSD", direction="long", dxy_confirmation="short"
        )
        assert out.size_multiplier == 1.0


class TestZoneConversionParity:
    def test_invalid_zone_market_converts_with_ote(self):
        from trading_bot.services.parity_gates import evaluate_zone_conversion

        pd = SimpleNamespace(swing_high=2100.0, swing_low=2000.0)
        out = evaluate_zone_conversion(
            zone_valid=False,
            zone_reason="long in premium",
            order_type="market",
            direction="long",
            current_entry=2090.0,
            current_price=2090.0,
            pd_analysis=pd,
        )
        assert out.blocked is False
        assert out.action == "convert_pending"
        assert out.new_order_type == "buy_limit"
        assert out.new_entry > 0

    def test_invalid_zone_no_ote_blocks(self):
        from trading_bot.services.parity_gates import evaluate_zone_conversion

        out = evaluate_zone_conversion(
            zone_valid=False,
            zone_reason="long in premium",
            order_type="market",
            direction="long",
            current_entry=2090.0,
            current_price=2090.0,
            pd_analysis=None,
        )
        assert out.blocked is True
        assert out.gate_id == "zone_conversion_failed"


class TestParityWiredIntoSharedChain:
    def test_post_claude_imports_parity_gates(self):
        import inspect
        from trading_bot.services import post_claude_gates

        src = inspect.getsource(post_claude_gates)
        assert "evaluate_displacement_parity" in src
        assert "evaluate_dxy_parity" in src

    def test_phased_and_oneshot_match_on_displacement_reject(self):
        from trading_bot.backtesting.replay import (
            compare_gate_fixture_batch,
            run_phased_live_gates,
        )
        from trading_bot.services.post_claude_gates import (
            PostClaudeGateInput,
            run_post_claude_gates,
        )
        from trading_bot.services.signal_normalizer import NormalizedSignal
        from trading_bot.services.entry_gates import ZoneGateSettings
        import pandas as pd

        sig = SimpleNamespace(
            direction="long",
            confidence=0.78,
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.1000,
            trade_type="intraday",
            order_type="market",
            amd_phase="distribution",
        )
        # Wide SL/TP so RR clears floors; displacement must be the blocker
        rows = []
        for i in range(30):
            p = 1.08 + i * 0.0001
            rows.append({"open": p, "high": p + 0.002, "low": p - 0.001, "close": p})
        inp = PostClaudeGateInput(
            symbol="EURUSD",
            trade_signal=sig,
            norm=NormalizedSignal(
                entry=1.0850, sl=1.0800, tp=1.1000, direction="long", rejected=False
            ),
            market_data={
                "d1_bias": "bullish",
                "h4_bias": "bullish",
                "m15_bias": "bullish",
                "spread": 0.0001,
            },
            analysis_results={
                "volume": {"relative_volume": 1.0},
                "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
                "order_blocks": SimpleNamespace(bullish_obs=[1], bearish_obs=[]),
                "displacement": {"distribution_confirmed": False},
                "amd_cycle": {"phase": "distribution"},
            },
            current_price=1.0850,
            zone_settings=ZoneGateSettings(gate_mode="disabled"),
            use_zone_gate=False,
            is_kill_zone=True,
            session_name="london",
            df=pd.DataFrame(rows),
            apply_secondary_modifiers=False,
        )
        # Ensure displacement parity sees confirmed=False via analysis_results
        batch = compare_gate_fixture_batch([("disp_reject", inp)])
        assert batch["parity_matches"] == batch["total"]
        assert batch["mismatches"] == []
