"""Lean outside-KZ cycle gate + sweep freshness + ICT/fingerprint parity."""

import pytest

from trading_bot.config import settings
from trading_bot.services.entry_gates import evaluate_ict_confirmation_gate
from trading_bot.services.setup_fingerprint import (
    LEAN_SWEEP_MAX_AGE_BARS,
    SetupFingerprint,
    build_setup_fingerprint,
    has_directional_sweep,
    is_lean_sweep_fade,
    lean_continues_outside_kill_zone,
)


@pytest.fixture
def lean_on(monkeypatch):
    monkeypatch.setattr(settings.trading, "liquidity_reversal_lean_mode", "active")
    monkeypatch.setenv("TRADING_LIQUIDITY_REVERSAL_LEAN_MODE", "active")


@pytest.fixture
def lean_off(monkeypatch):
    monkeypatch.setattr(settings.trading, "liquidity_reversal_lean_mode", "off")
    monkeypatch.setenv("TRADING_LIQUIDITY_REVERSAL_LEAN_MODE", "off")


class TestLeanOutsideKillZone:
    def test_lean_continues_outside_kz_when_active(self, lean_on):
        assert lean_continues_outside_kill_zone() is True

    def test_lean_does_not_continue_when_off(self, lean_off):
        assert lean_continues_outside_kill_zone() is False

    def test_resolve_cycle_symbols_keeps_metals_when_lean(self, lean_on):
        from trading_bot.analysis.kill_zones import resolve_outside_kz_cycle_symbols

        symbols, off_hours = resolve_outside_kz_cycle_symbols(
            ["XAUUSD", "XAGUSD", "BTCUSD"],
            crypto_symbols=frozenset({"BTCUSD"}),
            is_tradeable=False,
            disp_override_symbols=[],
            lean_active=True,
        )
        assert symbols == ["XAUUSD", "XAGUSD", "BTCUSD"]
        assert off_hours is False

    def test_resolve_cycle_symbols_crypto_only_when_lean_off(self, lean_off):
        from trading_bot.analysis.kill_zones import resolve_outside_kz_cycle_symbols

        symbols, off_hours = resolve_outside_kz_cycle_symbols(
            ["XAUUSD", "XAGUSD", "BTCUSD"],
            crypto_symbols=frozenset({"BTCUSD"}),
            is_tradeable=False,
            disp_override_symbols=[],
            lean_active=False,
        )
        assert symbols == ["BTCUSD"]
        assert off_hours is True

    def test_resolve_empty_when_no_crypto_lean_off(self, lean_off):
        from trading_bot.analysis.kill_zones import resolve_outside_kz_cycle_symbols

        symbols, off_hours = resolve_outside_kz_cycle_symbols(
            ["XAUUSD"],
            crypto_symbols=frozenset({"BTCUSD"}),
            is_tradeable=False,
            disp_override_symbols=[],
            lean_active=False,
        )
        assert symbols == []
        assert off_hours is True


class TestSweepFreshness:
    def test_max_age_constant_is_positive(self):
        assert LEAN_SWEEP_MAX_AGE_BARS >= 3

    def test_stale_sweep_rejected_for_lean(self, lean_on):
        liq = {
            "recent_sweeps": [
                {
                    "type": "sell_side_liquidity",
                    "reversal_detected": True,
                    "sweep_index": 10,
                },
                {
                    "type": "buy_side_liquidity",
                    "reversal_detected": True,
                    "sweep_index": 80,
                },
            ]
        }
        # Long wants SSL at index 10; reference bar 100 → age 90 > max
        assert (
            has_directional_sweep(
                "long",
                liq,
                max_age_bars=LEAN_SWEEP_MAX_AGE_BARS,
                reference_bar_index=100,
            )
            is False
        )
        assert (
            is_lean_sweep_fade(
                "long",
                {"liquidity": liq},
                reference_bar_index=100,
            )
            is False
        )

    def test_fresh_sweep_accepted_for_lean(self, lean_on):
        liq = {
            "recent_sweeps": [
                {
                    "type": "sell_side_liquidity",
                    "reversal_detected": True,
                    "sweep_index": 95,
                }
            ]
        }
        assert (
            has_directional_sweep(
                "long",
                liq,
                max_age_bars=LEAN_SWEEP_MAX_AGE_BARS,
                reference_bar_index=100,
            )
            is True
        )
        assert (
            is_lean_sweep_fade(
                "long",
                {"liquidity": liq},
                reference_bar_index=100,
            )
            is True
        )

    def test_without_age_limit_stale_still_matches(self, lean_on):
        liq = {
            "recent_sweeps": [
                {
                    "type": "sell_side_liquidity",
                    "reversal_detected": True,
                    "sweep_index": 10,
                }
            ]
        }
        assert has_directional_sweep("long", liq) is True

    def test_raw_bar_count_on_ar_ages_sweep(self, lean_on):
        ar = {
            "_raw_bar_count": 100,
            "liquidity": {
                "recent_sweeps": [
                    {
                        "type": "sell_side_liquidity",
                        "reversal_detected": True,
                        "sweep_index": 10,
                    }
                ]
            },
        }
        assert is_lean_sweep_fade("long", ar) is False

    def test_fingerprint_does_not_tag_stale_sweep_sb_lean(self, lean_on):
        ar = {
            "_raw_bar_count": 100,
            "liquidity": {
                "recent_sweeps": [
                    {
                        "type": "sell_side_liquidity",
                        "reversal_detected": True,
                        "sweep_index": 10,
                    }
                ]
            },
        }
        fp = build_setup_fingerprint(
            direction="long",
            order_type="market",
            d1_bias="bearish",
            h4_bias="bearish",
            analysis_results=ar,
        )
        assert fp.has_sweep is False
        assert "sb_lean" not in fp.tags

    def test_fingerprint_tags_fresh_sweep_sb_lean(self, lean_on):
        ar = {
            "_raw_bar_count": 100,
            "liquidity": {
                "recent_sweeps": [
                    {
                        "type": "sell_side_liquidity",
                        "reversal_detected": True,
                        "sweep_index": 95,
                    }
                ]
            },
        }
        fp = build_setup_fingerprint(
            direction="long",
            order_type="market",
            d1_bias="bearish",
            h4_bias="bearish",
            analysis_results=ar,
        )
        assert fp.has_sweep is True
        assert fp.family == "liquidity_reversal"
        assert "sb_lean" in fp.tags


class TestIctLeanFadeParity:
    def test_flag_alone_does_not_skip_mss_disp(self, lean_on):
        """ICT sweep-only requires lean_sweep_fade, not bare lean flag."""
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

    def test_lean_sweep_fade_allows_sweep_only(self, lean_on):
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

    def test_htf_disp_on_ar_blocks_lean_in_prepare_order(self, lean_on):
        """Stamped d1/h4 + displacement → not lean fade → auto_convert may demote."""
        from trading_bot.execution.trade_execution import auto_convert_to_pending

        ar = {
            "d1_bias": "bullish",
            "h4_bias": "bullish",
            "_raw_bar_count": 100,
            "liquidity": {
                "recent_sweeps": [
                    {
                        "type": "sell_side_liquidity",
                        "reversal_detected": True,
                        "sweep_index": 95,
                    }
                ]
            },
            "displacement": {
                "distribution_confirmed": True,
                "last_bullish": True,
            },
            "fresh_displacement_direction": "bullish",
        }
        assert (
            is_lean_sweep_fade(
                "long", ar, d1_bias="bullish", h4_bias="bullish"
            )
            is False
        )
        # Without lean fade, deep pullback market still converts to limit
        ot = auto_convert_to_pending(
            "market", "long", 1990.0, 2000.0, lean_sweep_fade=False
        )
        assert ot == "buy_limit"
