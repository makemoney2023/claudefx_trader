"""
Regression tests for log-cycle fixes:
- shadow_would_block recognized by gate_funnel
- IPDA multi-TP never returns near-entry / wrong-side / negative levels
- DOGE-style micro-SL sizing never surfaces 100-lot intermediates
- Claude adjustment log uses enough precision for sub-0.01 lots
"""

from unittest.mock import MagicMock

import pytest

from trading_bot.analysis.ipda import IPDATracker, IPDALevel, IPDAAnalysis
from trading_bot.execution.risk_manager import RiskManager
from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer, SetupGrade
from trading_bot.services.gate_funnel import TERMINAL_OUTCOMES, GateFunnel


class TestShadowWouldBlockOutcome:
    def test_shadow_would_block_is_terminal_outcome(self):
        assert "shadow_would_block" in TERMINAL_OUTCOMES

    @pytest.mark.asyncio
    async def test_record_decision_accepts_shadow_would_block(self, monkeypatch):
        """GateFunnel must persist shadow_would_block instead of warning+dropping."""
        captured = {}

        class _FakeSession:
            def add(self, row):
                captured["outcome_type"] = row.outcome_type
                captured["symbol"] = row.symbol

            async def commit(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        def _maker():
            return _FakeSession()

        funnel = GateFunnel(session_maker=_maker)
        did = await funnel.record_decision(
            "shadow_would_block",
            "XAUUSD",
            gate_id="ict_confirmation",
            direction="short",
            entry=4037.0,
            sl=4045.5,
            tp=4019.5,
            confidence=0.60,
            reason="ICT confirmation shadow would-block",
            details={"shadow": True},
        )
        assert did is not None
        assert captured["outcome_type"] == "shadow_would_block"
        assert captured["symbol"] == "XAUUSD"


class TestIpdaMultiTpSanity:
    def _tracker_with_near_entry_target(self, direction: str, entry: float, near: float):
        tracker = IPDATracker(pip_value=0.01)  # BTC-style pip (reproduces $1 "100-pip")
        level = IPDALevel(
            level_type="PDH" if direction == "long" else "PDL",
            price=near,
            period_start=__import__("datetime").datetime(2026, 8, 1),
            period_end=__import__("datetime").datetime(2026, 8, 2),
        )
        if direction == "long":
            tracker._analysis.pdh = level
        else:
            tracker._analysis.pdl = level
        return tracker

    def test_btc_long_tp_ladder_stays_beyond_entry(self):
        """BTC long: 100-pip with pip=0.01 is +$1 — must not become TP2 near entry."""
        entry, sl = 63965.0, 63590.0
        tracker = self._tracker_with_near_entry_target("long", entry, entry + 1.0)
        levels = tracker.get_take_profit_levels("long", entry, sl)
        risk = entry - sl

        assert levels["tp1"] == pytest.approx(entry + 2 * risk)
        assert levels["tp2"] is not None
        assert levels["tp2"] > levels["tp1"] - 1e-9
        assert (levels["tp2"] - entry) / risk >= 2.0 - 1e-9
        # Must not be the bogus +$1 / near-entry level
        assert levels["tp2"] != pytest.approx(entry + 1.0)

    def test_doge_short_rejects_negative_100pip_projection(self):
        """DOGE short with pip=0.01 projects 100-pip to negative — must fall back to R ladder."""
        entry, sl = 0.07035, 0.07075
        tracker = IPDATracker(pip_value=0.01)
        levels = tracker.get_take_profit_levels("short", entry, sl)
        risk = abs(entry - sl)

        assert levels["tp1"] == pytest.approx(entry - 2 * risk)
        assert levels["tp2"] is not None
        assert levels["tp2"] > 0
        assert levels["tp2"] < entry
        assert (entry - levels["tp2"]) / risk >= 2.0 - 1e-9
        assert levels["tp2"] != pytest.approx(-0.92965)


class TestDogePositionSizeCap:
    def test_doge_micro_sl_never_returns_broker_max_lots(self, caplog):
        """Risk manager must cap before normalize so we never warn on 100.0 lots."""
        import logging

        rm = RiskManager(risk_per_trade=0.02, max_risk_per_trade=0.02, max_daily_risk=0.06)
        with caplog.at_level(logging.WARNING):
            result = rm.calculate_position_size(
                account_balance=986.63,
                entry_price=0.07035,
                stop_loss=0.07075,
                symbol="DOGEUSD",
            )
        assert result.lots <= 1.0
        assert result.lots > 0
        oversize_msgs = [
            r.message for r in caplog.records if "exceeds config max" in r.message
        ]
        for msg in oversize_msgs:
            assert "100.0" not in msg, msg


class TestClaudeAdjustmentLogging:
    def test_claude_adjustment_preserves_sub_cent_lot_precision(self):
        sizer = ScalingPositionSizer()
        # Tiny SL on gold-like math isn't needed — force tiny pre-claude lots via
        # grade/confidence on a small tier, with Claude recommending min lot.
        result = sizer.calculate_position_size(
            equity=986.63,
            entry_price=4037.0,
            stop_loss=4045.5,
            symbol="XAUUSD",
            confidence=0.60,
            setup_grade=SetupGrade.C,
            loss_streak=2,
            confluence_count=2,
            claude_recommendation=0.01,
        )
        claude_lines = [a for a in result.adjustments if a.startswith("Claude adjustment:")]
        assert claude_lines, result.adjustments
        # Must not collapse both sides to 0.00
        assert "0.00 -> 0.00" not in claude_lines[0]
