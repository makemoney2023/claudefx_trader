"""TDD tests for remaining WIN optimization recommendations REC#6, #9, #10, #11."""

import pytest

from trading_bot.config import (
    ICT_KILL_ZONE_SESSIONS,
    format_startup_config_banner,
    get_config_risk_warnings,
    get_effective_allowed_sessions,
)
from trading_bot.execution.scaling_position_sizer import (
    compute_actual_risk_dollars,
    verify_post_sizing_risk,
)
from trading_bot.services.trade_learning_service import (
    MIN_PATTERN_SAMPLE_DRAFT,
    MIN_PATTERN_SAMPLE_PROMOTED,
    knowledge_write_allowed,
    validate_weekly_insights_schema,
)
from trading_bot.utils.win_optimization import validate_signal_coherence


# ---------------------------------------------------------------------------
# REC#6 — Post-sizing risk verification
# ---------------------------------------------------------------------------


class TestPostSizingRiskVerification:
    def test_compute_actual_risk_eurusd(self):
        # 0.10 lots, 20 pip SL on EURUSD ($10/pip/lot)
        risk = compute_actual_risk_dollars(
            lots=0.10,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
        )
        assert risk == pytest.approx(20.0)

    def test_within_tolerance_unchanged(self):
        lots, actual, reason = verify_post_sizing_risk(
            final_lots=0.10,
            target_lots=0.10,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
        )
        assert reason is None
        assert lots == pytest.approx(0.10)
        assert actual == pytest.approx(20.0)

    def test_overshoot_shrinks_lots(self):
        # Target 0.10 lots ($20 risk); 0.15 lots is 50% over — should shrink
        lots, actual, reason = verify_post_sizing_risk(
            final_lots=0.15,
            target_lots=0.10,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            tolerance=1.15,
        )
        assert reason is None
        assert lots < 0.15
        target_risk = compute_actual_risk_dollars(0.10, 1.1000, 1.0980, "EURUSD")
        assert actual <= target_risk * 1.15 + 0.01

    def test_extreme_overshoot_rejects_at_min_lot(self):
        # Tolerance below 1.0 makes max_allowed < risk achievable at broker min lot
        lots, actual, reason = verify_post_sizing_risk(
            final_lots=1.0,
            target_lots=0.01,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            tolerance=0.99,
        )
        assert lots == 0.0
        assert reason is not None
        assert "risk" in reason.lower()

    def test_zero_sl_distance_rejects(self):
        lots, actual, reason = verify_post_sizing_risk(
            final_lots=0.10,
            target_lots=0.10,
            entry_price=1.1000,
            stop_loss=1.1000,
            symbol="EURUSD",
        )
        assert lots == 0.0
        assert reason is not None


# ---------------------------------------------------------------------------
# REC#9 — Reject incoherent signals (no auto-fix)
# ---------------------------------------------------------------------------


class TestSignalCoherenceReject:
    def test_sl_equals_entry_rejected(self):
        ok, reason = validate_signal_coherence(
            entry=1.1000,
            sl=1.1000,
            tp=1.1100,
            direction="long",
        )
        assert ok is False
        assert "sl" in reason.lower() and "entry" in reason.lower()

    def test_direction_flip_long_levels_on_short_rejected(self):
        ok, reason = validate_signal_coherence(
            entry=1.1000,
            sl=1.0950,
            tp=1.1100,
            direction="short",
        )
        assert ok is False
        assert "direction" in reason.lower() or "coherent" in reason.lower()

    def test_swapped_sl_tp_rejected_not_fixed(self):
        # Long with SL above entry and TP below — would have been swapped before
        ok, reason = validate_signal_coherence(
            entry=1.1000,
            sl=1.1100,
            tp=1.0900,
            direction="long",
        )
        assert ok is False

    def test_coherent_long_passes(self):
        ok, reason = validate_signal_coherence(
            entry=1.1000,
            sl=1.0950,
            tp=1.1100,
            direction="long",
        )
        assert ok is True
        assert reason == ""

    def test_coherent_short_passes(self):
        ok, reason = validate_signal_coherence(
            entry=1.1000,
            sl=1.1100,
            tp=1.0900,
            direction="short",
        )
        assert ok is True


# ---------------------------------------------------------------------------
# REC#10 — Gate learning-loop writes
# ---------------------------------------------------------------------------


class TestLearningLoopGates:
    def test_valid_insights_schema(self):
        ok, err = validate_weekly_insights_schema({
            "performance_grade": "B",
            "summary": "Solid week",
            "patterns_identified": ["pat1"],
            "recurring_mistakes": ["mistake1"],
            "winning_patterns": ["win1"],
            "recommendations": ["rec1"],
            "symbol_insights": {"EURUSD": "Avoid London open"},
            "session_insights": {"london": "72% WR"},
            "focus_area": "Patience",
            "best_setup": "FVG",
        })
        assert ok is True
        assert err == ""

    def test_invalid_insights_missing_grade(self):
        ok, err = validate_weekly_insights_schema({"summary": "no grade"})
        assert ok is False
        assert "performance_grade" in err

    def test_invalid_insights_bad_symbol_insights_type(self):
        ok, err = validate_weekly_insights_schema({
            "performance_grade": "C",
            "summary": "ok",
            "symbol_insights": ["not", "a", "dict"],
        })
        assert ok is False

    def test_promoted_requires_ten_samples(self):
        assert knowledge_write_allowed(9, draft=False) is False
        assert knowledge_write_allowed(10, draft=False) is True

    def test_draft_requires_five_samples(self):
        assert knowledge_write_allowed(4, draft=True) is False
        assert knowledge_write_allowed(5, draft=True) is True

    def test_constants(self):
        assert MIN_PATTERN_SAMPLE_PROMOTED >= 10
        assert MIN_PATTERN_SAMPLE_DRAFT >= 5


# ---------------------------------------------------------------------------
# REC#11 — Config alignment warnings
# ---------------------------------------------------------------------------


class TestConfigAlignment:
    def test_kill_zone_sessions_defined(self):
        assert "london" in ICT_KILL_ZONE_SESSIONS
        assert "new_york" in ICT_KILL_ZONE_SESSIONS

    def test_warnings_for_aggressive_settings(self, monkeypatch):
        from trading_bot import config as cfg

        monkeypatch.setattr(
            cfg.settings.trading,
            "allowed_sessions",
            ["all"],
        )
        monkeypatch.setattr(cfg.settings.trading, "risk_per_trade", 0.02)
        monkeypatch.setattr(cfg.settings.trading, "max_daily_trades", 15)

        warnings = get_config_risk_warnings()
        assert any("all" in w.lower() or "session" in w.lower() for w in warnings)
        assert any("risk" in w.lower() for w in warnings)
        assert any("daily" in w.lower() or "trade" in w.lower() for w in warnings)

    def test_startup_banner_includes_warnings(self, monkeypatch):
        from trading_bot import config as cfg

        monkeypatch.setattr(
            cfg.settings.trading,
            "allowed_sessions",
            ["all"],
        )
        monkeypatch.setattr(cfg.settings.trading, "risk_per_trade", 0.02)
        monkeypatch.setattr(cfg.settings.trading, "max_daily_trades", 15)

        banner = format_startup_config_banner()
        assert "WARNING" in banner or "⚠" in banner
        assert "kill" in banner.lower() or "london" in banner.lower()

    def test_strict_ict_sessions_filters(self, monkeypatch):
        from trading_bot import config as cfg

        monkeypatch.setattr(
            cfg.settings.trading,
            "allowed_sessions",
            ["all"],
        )
        monkeypatch.setattr(cfg.settings, "strict_ict_sessions", True)

        effective = get_effective_allowed_sessions()
        assert effective == ICT_KILL_ZONE_SESSIONS
        assert "all" not in [s.lower() for s in effective]
