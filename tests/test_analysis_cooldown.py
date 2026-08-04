"""TDD: symbol/KZ-aware analysis cooldown + M5 displacement early wakeup."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from trading_bot.services.analysis_cooldown import (
    DISPLACEMENT_WAKEUP_MIN_ELAPSED,
    resolve_analysis_cooldown_seconds,
    should_run_analysis,
    has_recent_m5_displacement,
)


class TestResolveAnalysisCooldown:
    def test_default_forex_stays_at_base(self):
        assert resolve_analysis_cooldown_seconds("EURUSD", is_kill_zone=True) == 270
        assert resolve_analysis_cooldown_seconds("EURUSD", is_kill_zone=False) == 270

    def test_gold_in_kill_zone_is_90s(self):
        assert resolve_analysis_cooldown_seconds("XAUUSD", is_kill_zone=True) == 90

    def test_gold_outside_kill_zone_is_180s(self):
        assert resolve_analysis_cooldown_seconds("XAUUSD", is_kill_zone=False) == 180

    def test_silver_matches_gold_policy(self):
        assert resolve_analysis_cooldown_seconds("XAGUSD", is_kill_zone=True) == 90
        assert resolve_analysis_cooldown_seconds("XAGUSD", is_kill_zone=False) == 180

    def test_custom_base_honored_for_non_metals(self):
        assert resolve_analysis_cooldown_seconds(
            "GBPUSD", is_kill_zone=True, base_seconds=300
        ) == 300


class TestShouldRunAnalysis:
    def test_first_run_always_runs(self):
        now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
        run, reason = should_run_analysis(
            last_run=None,
            now=now,
            cooldown_seconds=270,
            displacement_wakeup=False,
        )
        assert run is True
        assert reason == "ready"

    def test_blocks_inside_cooldown(self):
        now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=60)
        run, reason = should_run_analysis(
            last_run=last,
            now=now,
            cooldown_seconds=270,
            displacement_wakeup=False,
        )
        assert run is False
        assert reason == "cooldown"

    def test_allows_after_cooldown(self):
        now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=271)
        run, reason = should_run_analysis(
            last_run=last,
            now=now,
            cooldown_seconds=270,
            displacement_wakeup=False,
        )
        assert run is True
        assert reason == "ready"

    def test_displacement_wakeup_after_min_elapsed(self):
        now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=DISPLACEMENT_WAKEUP_MIN_ELAPSED)
        run, reason = should_run_analysis(
            last_run=last,
            now=now,
            cooldown_seconds=270,
            displacement_wakeup=True,
        )
        assert run is True
        assert reason == "m5_displacement_wakeup"

    def test_displacement_wakeup_respects_min_elapsed_floor(self):
        now = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=DISPLACEMENT_WAKEUP_MIN_ELAPSED - 1)
        run, reason = should_run_analysis(
            last_run=last,
            now=now,
            cooldown_seconds=270,
            displacement_wakeup=True,
        )
        assert run is False
        assert reason == "cooldown"


class TestHasRecentM5Displacement:
    def test_true_when_recent_strong_displacement(self):
        analysis = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(index=18, atr_multiple=1.8, strength=0.8),
            ]
        )
        assert has_recent_m5_displacement(analysis, last_bar_index=20, max_age_bars=3)

    def test_false_when_stale(self):
        analysis = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(index=10, atr_multiple=2.0, strength=0.9),
            ]
        )
        assert (
            has_recent_m5_displacement(analysis, last_bar_index=20, max_age_bars=3)
            is False
        )

    def test_false_when_none(self):
        assert has_recent_m5_displacement(None, last_bar_index=20) is False


class TestMetalLossCooldownMinutes:
    def test_gold_loss_cooldown_is_15(self):
        from trading_bot.services.analysis_cooldown import resolve_loss_cooldown_minutes

        assert resolve_loss_cooldown_minutes("XAUUSD") == 15
        assert resolve_loss_cooldown_minutes("XAGUSD") == 15

    def test_crypto_stays_15_forex_30(self):
        from trading_bot.services.analysis_cooldown import resolve_loss_cooldown_minutes

        assert resolve_loss_cooldown_minutes("BTCUSD") == 15
        assert resolve_loss_cooldown_minutes("EURUSD") == 30
