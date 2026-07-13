"""
Regression tests for gaps found in the end-to-end strategy review.

Covers:
1. Reversal re-entry position sizing (was: wrong RiskManager API -> always 0.01 lots)
2. Trailing stop must not report an action when the MT5 modify fails
3. Silver Bullet windows must be evaluated in America/New_York, not system-local time
4. Precious-metals context must call the real NewsService geopolitical method
5. FVG detector must not use the still-forming candle (repainting zones)
"""

import pytest
import pandas as pd
import pytz
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

NY_TZ = pytz.timezone("America/New_York")


# ================================================================
# 1. Reversal re-entry sizing
# ================================================================

class TestReversalPositionSizing:
    """Reversal re-entries must size by risk, not silently fall back to 0.01."""

    @pytest.mark.asyncio
    async def test_reversal_sizing_uses_risk_manager(self):
        from trading_bot.main import TradingBot
        from trading_bot.execution.risk_manager import RiskManager

        bot = TradingBot.__new__(TradingBot)
        bot.risk_manager = RiskManager(risk_per_trade=0.01)
        bot.scaling_manager = None
        bot.mt5_client = MagicMock()
        bot.mt5_client.get_account_info = AsyncMock(
            return_value=SimpleNamespace(equity=10000.0, balance=10000.0)
        )

        # EURUSD: 50 pip SL, 1% of $10k = $100 risk -> 100 / (50 * $10) = 0.2 lots
        lots = await bot._reversal_position_size("EURUSD", 1.1000, 1.0950)
        assert lots == pytest.approx(0.2, abs=0.01)
        assert lots > 0.01, "reversal sizing must not fall back to minimum lot"

    @pytest.mark.asyncio
    async def test_reversal_sizing_falls_back_on_error(self):
        from trading_bot.main import TradingBot

        bot = TradingBot.__new__(TradingBot)
        bot.risk_manager = MagicMock()
        bot.risk_manager.calculate_position_size = MagicMock(
            side_effect=RuntimeError("boom")
        )
        bot.scaling_manager = None
        bot.mt5_client = MagicMock()
        bot.mt5_client.get_account_info = AsyncMock(return_value=None)

        lots = await bot._reversal_position_size("EURUSD", 1.1000, 1.0950)
        assert lots == 0.01

    @pytest.mark.asyncio
    async def test_reversal_sizing_applies_scaling_multiplier(self):
        from trading_bot.main import TradingBot
        from trading_bot.execution.risk_manager import RiskManager

        bot = TradingBot.__new__(TradingBot)
        bot.risk_manager = RiskManager(risk_per_trade=0.01)
        bot.scaling_manager = MagicMock()
        bot.scaling_manager.get_mode_config.return_value = SimpleNamespace(
            risk_multiplier=0.5
        )
        bot.mt5_client = MagicMock()
        bot.mt5_client.get_account_info = AsyncMock(
            return_value=SimpleNamespace(equity=10000.0, balance=10000.0)
        )

        lots = await bot._reversal_position_size("EURUSD", 1.1000, 1.0950)
        assert lots == pytest.approx(0.1, abs=0.01)


# ================================================================
# 2. Trailing stop failure reporting
# ================================================================

class TestTrailingStopFailure:
    @pytest.mark.asyncio
    async def test_failed_modify_returns_no_action(self):
        from trading_bot.execution.position_manager import (
            PositionManager,
            Position,
        )

        om = MagicMock()
        om.modify_order = AsyncMock(return_value=MagicMock(success=False))
        om._check_spread = AsyncMock(return_value=(True, 0.0001, 0.001))
        pm = PositionManager(order_manager=om)

        pos = Position(
            ticket=42,
            symbol="EURUSD",
            direction="long",
            volume=0.10,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1200,
            open_time=datetime.now(),
        )
        pos.current_price = 1.1150  # 3R in profit -> trailing wants to move SL

        original_sl = pos.stop_loss
        action = await pm._update_trailing_stop(pos)

        assert action is None, "failed SL modify must not be reported as an action"
        assert pos.stop_loss == original_sl, "SL must be reverted on failed modify"

    @pytest.mark.asyncio
    async def test_successful_modify_returns_action(self):
        from trading_bot.execution.position_manager import (
            PositionManager,
            Position,
        )

        om = MagicMock()
        om.modify_order = AsyncMock(return_value=MagicMock(success=True))
        om._check_spread = AsyncMock(return_value=(True, 0.0001, 0.001))
        pm = PositionManager(order_manager=om)

        pos = Position(
            ticket=43,
            symbol="EURUSD",
            direction="long",
            volume=0.10,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1200,
            open_time=datetime.now(),
        )
        pos.current_price = 1.1150

        action = await pm._update_trailing_stop(pos)
        assert action is not None
        assert action["action"] == "trailing_stop"
        assert pos.stop_loss > 1.0950


# ================================================================
# 3. Silver Bullet timezone handling
# ================================================================

class TestSilverBulletTimezone:
    def _detector(self):
        from trading_bot.analysis.silver_bullet import SilverBulletDetector
        return SilverBulletDetector()

    def test_ny_am_window_active_with_est_time(self):
        det = self._detector()
        t = NY_TZ.localize(datetime(2026, 7, 8, 10, 30))  # Wed 10:30 ET
        result = det.is_in_silver_bullet_window(t)
        assert result["active"] is True
        assert result["window"] == "ny_am"

    def test_ny_am_window_active_with_utc_time(self):
        """A UTC timestamp equivalent to 10:30 ET must also match (14:30 UTC in July)."""
        det = self._detector()
        t = datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc)
        result = det.is_in_silver_bullet_window(t)
        assert result["active"] is True
        assert result["window"] == "ny_am"

    def test_naive_time_treated_as_est(self):
        det = self._detector()
        result = det.is_in_silver_bullet_window(datetime(2026, 7, 8, 3, 30))
        assert result["active"] is True
        assert result["window"] == "london"

    def test_outside_all_windows(self):
        det = self._detector()
        t = NY_TZ.localize(datetime(2026, 7, 8, 12, 30))
        result = det.is_in_silver_bullet_window(t)
        assert result["active"] is False

    def test_time_remaining_computed_for_aware_input(self):
        det = self._detector()
        t = datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
        result = det.is_in_silver_bullet_window(t)
        assert result["time_remaining_minutes"] == pytest.approx(30, abs=1)


# ================================================================
# 4. Geopolitical risk method
# ================================================================

class TestGeopoliticalRiskWiring:
    def test_news_service_exposes_get_geopolitical_risk_level(self):
        from trading_bot.services.news_service import NewsService
        svc = NewsService()
        assert svc.get_geopolitical_risk_level() == "low"

    def test_main_does_not_call_nonexistent_method(self):
        """Pipeline must use get_geopolitical_risk_level(), not geopolitical_risk_level([])."""
        from tests.pipeline_source import pipeline_source

        source = pipeline_source()
        assert "geopolitical_risk_level([])" not in source
        assert "get_geopolitical_risk_level()" in source


# ================================================================
# 5. FVG forming-candle repaint
# ================================================================

def _fvg_df(rows):
    idx = pd.date_range(start="2026-01-01", periods=len(rows), freq="15min")
    return pd.DataFrame(rows, index=idx)


class TestFVGFormingCandle:
    def _detector(self):
        from trading_bot.analysis.fair_value_gap import FVGDetector
        return FVGDetector(min_gap_pips=3.0, min_body_percentage=0.3, pip_value=0.0001)

    def _base_rows(self):
        """Flat series with no gaps."""
        return [
            {"open": 1.1000, "high": 1.1010, "low": 1.0995, "close": 1.1005, "volume": 100}
            for _ in range(8)
        ]

    def test_fvg_completed_by_forming_candle_is_ignored(self):
        """If candle3 of the pattern is the last (still-forming) bar, no FVG yet."""
        rows = self._base_rows()
        # Pattern on the last three bars: candle1 high 1.1010 < candle3 low 1.1060
        rows.append({"open": 1.1005, "high": 1.1070, "low": 1.1000, "close": 1.1065, "volume": 300})
        rows.append({"open": 1.1065, "high": 1.1090, "low": 1.1060, "close": 1.1085, "volume": 200})
        df = _fvg_df(rows)

        result = self._detector().detect(df)
        assert len(result.bullish_fvgs) == 0, (
            "FVG whose third candle is the forming bar must not be reported (repaint risk)"
        )

    def test_fvg_confirmed_after_candle_closes(self):
        """Same pattern followed by one more (now-forming) bar becomes valid."""
        rows = self._base_rows()
        rows.append({"open": 1.1005, "high": 1.1070, "low": 1.1000, "close": 1.1065, "volume": 300})
        rows.append({"open": 1.1065, "high": 1.1090, "low": 1.1060, "close": 1.1085, "volume": 200})
        # New forming bar (low overlaps prior high so it creates no second gap);
        # the previous three-bar pattern is now fully closed
        rows.append({"open": 1.1085, "high": 1.1095, "low": 1.1065, "close": 1.1090, "volume": 150})
        df = _fvg_df(rows)

        result = self._detector().detect(df)
        assert len(result.bullish_fvgs) == 1
        fvg = result.bullish_fvgs[0]
        assert fvg.bottom == pytest.approx(1.1010)
        assert fvg.top == pytest.approx(1.1060)
