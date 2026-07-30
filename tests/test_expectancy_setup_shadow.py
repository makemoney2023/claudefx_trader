"""Phase 4: setup fingerprint + shadow ICT confirmation gate."""

from types import SimpleNamespace

import pytest


class TestSetupFingerprint:
    def test_continuation_fingerprint(self):
        from trading_bot.services.setup_fingerprint import build_setup_fingerprint

        fp = build_setup_fingerprint(
            direction="long",
            order_type="market",
            session="london",
            regime="trending",
            d1_bias="bullish",
            h4_bias="bullish",
            analysis_results={
                "displacement": {"distribution_confirmed": True},
                "market_structure": SimpleNamespace(
                    structure_breaks=[
                        SimpleNamespace(
                            type=SimpleNamespace(value="bos_bullish"),
                            is_bullish=True,
                            is_bearish=False,
                        )
                    ]
                ),
                "liquidity": SimpleNamespace(recent_sweeps=[]),
                "fvg": SimpleNamespace(bullish_fvgs=[1], bearish_fvgs=[]),
                "order_blocks": SimpleNamespace(bullish_obs=[], bearish_obs=[]),
            },
        )
        assert fp.family == "continuation"
        assert "disp" in fp.tags
        assert "mss" in fp.tags or "bos" in fp.tags
        assert fp.key.startswith("continuation|")
        assert len(fp.key) <= 120

    def test_liquidity_reversal_requires_sweep(self):
        from trading_bot.services.setup_fingerprint import build_setup_fingerprint

        fp = build_setup_fingerprint(
            direction="long",
            order_type="market",
            session="new_york",
            regime="volatile",
            d1_bias="bearish",
            h4_bias="bearish",
            analysis_results={
                "displacement": {"distribution_confirmed": True},
                "market_structure": SimpleNamespace(
                    structure_breaks=[
                        SimpleNamespace(
                            type=SimpleNamespace(value="choch_bullish"),
                            is_bullish=True,
                            is_bearish=False,
                        )
                    ]
                ),
                "liquidity": {
                    "recent_sweeps": [
                        {"type": "ssl", "reversal_detected": True},
                    ]
                },
                "fvg": {},
                "order_blocks": {},
            },
        )
        assert fp.family == "liquidity_reversal"
        assert "sweep" in fp.tags


class TestDirectionalSweepCredit:
    def test_nearby_liquidity_alone_no_longer_counts(self):
        from trading_bot.services.gate_pipeline import count_confluence
        from trading_bot.services.trade_context import TradeContext

        ctx = TradeContext(
            symbol="EURUSD",
            direction="long",
            confidence=0.7,
            analysis_results={
                "liquidity": SimpleNamespace(
                    nearest_ssl=1.095,
                    recent_sweeps=[],
                )
            },
        )
        count, factors = count_confluence(ctx)
        assert "SSL Liquidity" not in factors
        assert "Directional Sweep" not in factors

    def test_direction_aligned_sweep_counts(self):
        from trading_bot.services.gate_pipeline import count_confluence
        from trading_bot.services.trade_context import TradeContext

        ctx = TradeContext(
            symbol="EURUSD",
            direction="long",
            confidence=0.7,
            analysis_results={
                "liquidity": {
                    "recent_sweeps": [
                        {"type": "ssl", "reversal_detected": True},
                    ]
                }
            },
        )
        count, factors = count_confluence(ctx)
        assert "Directional Sweep" in factors


class TestIctConfirmationShadow:
    def test_reversal_missing_sweep_would_block_shadow(self):
        from trading_bot.services.entry_gates import evaluate_ict_confirmation_gate
        from trading_bot.services.setup_fingerprint import SetupFingerprint

        fp = SetupFingerprint(
            family="liquidity_reversal",
            tags=("sweep_missing",),
            key="liquidity_reversal|long|market|ny|volatile",
            has_sweep=False,
            has_mss=True,
            has_displacement=True,
            htf_aligned=False,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="market",
            mode="shadow",
        )
        assert out.blocked is False  # shadow never hard-blocks
        assert out.shadow_only is True
        assert out.gate_id == "ict_confirmation"
        assert "sweep" in out.reason.lower() or "missing" in out.reason.lower()

    def test_passive_limit_skips_post_entry_confirm(self):
        from trading_bot.services.entry_gates import evaluate_ict_confirmation_gate
        from trading_bot.services.setup_fingerprint import SetupFingerprint

        fp = SetupFingerprint(
            family="passive_retracement",
            tags=("htf", "zone"),
            key="passive_retracement|long|buy_limit|london|trending",
            has_sweep=False,
            has_mss=False,
            has_displacement=True,
            htf_aligned=True,
            zone_valid=True,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="buy_limit",
            mode="shadow",
        )
        assert out.would_block is False
        assert out.decision == "passive_limit_ok"

    def test_active_mode_blocks_reversal_without_confirm(self):
        from trading_bot.services.entry_gates import evaluate_ict_confirmation_gate
        from trading_bot.services.setup_fingerprint import SetupFingerprint

        fp = SetupFingerprint(
            family="liquidity_reversal",
            tags=(),
            key="liquidity_reversal|short|market|london|ranging",
            has_sweep=False,
            has_mss=False,
            has_displacement=True,
            htf_aligned=False,
        )
        out = evaluate_ict_confirmation_gate(
            fingerprint=fp,
            order_type="market",
            mode="active",
        )
        assert out.blocked is True
        assert out.gate_id == "ict_confirmation"
