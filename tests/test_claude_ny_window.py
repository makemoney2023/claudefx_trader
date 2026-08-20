"""Claude analysis window: 30 minutes before NY open through NY kill-zone end."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.analysis.kill_zones import (
    NY_KILL_ZONE_END,
    NY_KILL_ZONE_START,
    claude_analysis_allowed,
    is_in_claude_ny_window,
    minutes_until_claude_ny_window,
)
from trading_bot.config import (
    TradingSettings,
    claude_analysis_window_is_ny_open,
    opportunity_scanner_should_run,
)


def _et(year, month, day, hour, minute):
    from trading_bot.analysis.kill_zones import KillZoneChecker

    tz = KillZoneChecker().timezone
    return tz.localize(datetime(year, month, day, hour, minute))


class TestNyWindowBounds:
    def test_default_window_is_0630_to_1000(self):
        assert NY_KILL_ZONE_START.hour == 7
        assert NY_KILL_ZONE_END.hour == 10
        assert is_in_claude_ny_window(_et(2026, 8, 20, 6, 29), lead_minutes=30) is False
        assert is_in_claude_ny_window(_et(2026, 8, 20, 6, 30), lead_minutes=30) is True
        assert is_in_claude_ny_window(_et(2026, 8, 20, 7, 0), lead_minutes=30) is True
        assert is_in_claude_ny_window(_et(2026, 8, 20, 9, 59), lead_minutes=30) is True
        assert is_in_claude_ny_window(_et(2026, 8, 20, 10, 0), lead_minutes=30) is True
        assert is_in_claude_ny_window(_et(2026, 8, 20, 10, 1), lead_minutes=30) is False

    def test_london_kz_is_outside_ny_window(self):
        assert is_in_claude_ny_window(_et(2026, 8, 20, 3, 30), lead_minutes=30) is False

    def test_london_close_is_outside_ny_window(self):
        assert is_in_claude_ny_window(_et(2026, 8, 20, 11, 0), lead_minutes=30) is False

    def test_lead_minutes_is_configurable(self):
        assert is_in_claude_ny_window(_et(2026, 8, 20, 6, 15), lead_minutes=30) is False
        assert is_in_claude_ny_window(_et(2026, 8, 20, 6, 15), lead_minutes=45) is True

    def test_eta_before_window(self):
        eta = minutes_until_claude_ny_window(_et(2026, 8, 20, 6, 0), lead_minutes=30)
        assert eta == 30

    def test_eta_zero_inside_window(self):
        assert minutes_until_claude_ny_window(_et(2026, 8, 20, 8, 0), lead_minutes=30) == 0


class TestClaudeAnalysisAllowedNyWindow:
    def test_ny_open_ignores_tradeable_and_requires_window(self):
        assert (
            claude_analysis_allowed(
                True,
                analysis_window="ny_open",
                in_ny_window=False,
            )
            is False
        )
        assert (
            claude_analysis_allowed(
                False,
                analysis_window="ny_open",
                in_ny_window=True,
            )
            is True
        )

    def test_ny_open_blocks_lean_and_displacement_outside_window(self):
        assert (
            claude_analysis_allowed(
                False,
                analysis_window="ny_open",
                in_ny_window=False,
                lean_active=True,
                displacement_override=True,
                claude_kill_zone_only=False,
            )
            is False
        )

    def test_all_kill_zones_keeps_legacy_lean_override(self):
        assert (
            claude_analysis_allowed(
                False,
                analysis_window="all_kill_zones",
                in_ny_window=False,
                lean_active=True,
            )
            is True
        )


class TestConfigHelpers:
    def test_window_aliases(self):
        assert claude_analysis_window_is_ny_open("ny_open") is True
        assert claude_analysis_window_is_ny_open("NY") is True
        assert claude_analysis_window_is_ny_open("all_kill_zones") is False

    def test_settings_default_is_ny_open(self):
        assert TradingSettings().claude_analysis_window == "ny_open"
        assert TradingSettings().claude_ny_lead_minutes == 30

    def test_scanner_auto_runs_when_ny_window_on(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "opportunity_scanner_enabled", False)
        monkeypatch.setattr(settings.trading, "claude_analysis_window", "ny_open")
        assert opportunity_scanner_should_run() is True

    def test_scanner_stays_opt_in_for_all_kill_zones(self, monkeypatch):
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "opportunity_scanner_enabled", False)
        monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")
        assert opportunity_scanner_should_run() is False


class TestRunnerNyWindow:
    @pytest.mark.asyncio
    async def test_pre_open_lead_reaches_ohlcv(self, monkeypatch):
        from trading_bot.config import settings
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade

        monkeypatch.setattr(settings.trading, "claude_analysis_window", "ny_open")
        monkeypatch.setattr(settings.trading, "claude_ny_lead_minutes", 30)
        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", True)
        monkeypatch.setattr(settings.trading, "allow_simulation_trades", False)

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = set()
        bot.scaling_manager = None
        bot.mt5_client.is_simulation = False
        bot.kill_zone_checker = MagicMock()
        bot.kill_zone_checker.get_current_session.return_value = SimpleNamespace(
            is_tradeable=False,
            session_name="London Continuation",
            next_kill_zone="New York Kill Zone",
            next_kill_zone_in_minutes=25,
        )
        bot.data_fetcher.get_ohlcv = AsyncMock(return_value=None)

        with patch(
            "trading_bot.analysis.kill_zones.is_in_claude_ny_window", return_value=True
        ), patch("trading_bot.api.routes.activity.add_activity"), patch(
            "trading_bot.services.analyze_and_trade_runner.bot_state", None
        ):
            await run_analyze_and_trade(bot, "EURUSD")

        bot.data_fetcher.get_ohlcv.assert_awaited()

    @pytest.mark.asyncio
    async def test_london_kz_skips_when_ny_window_on(self, monkeypatch):
        from trading_bot.config import settings
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade

        monkeypatch.setattr(settings.trading, "claude_analysis_window", "ny_open")
        monkeypatch.setattr(settings.trading, "claude_ny_lead_minutes", 30)
        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", True)

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = set()
        bot.scaling_manager = None
        bot.kill_zone_checker = MagicMock()
        bot.kill_zone_checker.get_current_session.return_value = SimpleNamespace(
            is_tradeable=True,
            session_name="London Kill Zone",
            next_kill_zone="New York Kill Zone",
            next_kill_zone_in_minutes=180,
        )
        bot.data_fetcher.get_ohlcv = AsyncMock()
        bot._fetch_m5_displacement_analysis = AsyncMock()

        with patch(
            "trading_bot.analysis.kill_zones.is_in_claude_ny_window", return_value=False
        ), patch("trading_bot.api.routes.activity.add_activity"), patch(
            "trading_bot.services.analyze_and_trade_runner.bot_state", None
        ):
            await run_analyze_and_trade(bot, "XAUUSD")

        bot.data_fetcher.get_ohlcv.assert_not_awaited()
        bot._fetch_m5_displacement_analysis.assert_not_awaited()


class TestCycleWiring:
    def test_trading_cycle_mentions_ny_window(self):
        import inspect
        from trading_bot.main import TradingBot

        src = inspect.getsource(TradingBot._trading_cycle)
        assert "is_in_claude_ny_window" in src
        assert "claude_analysis_window" in src or "ny_open" in src
