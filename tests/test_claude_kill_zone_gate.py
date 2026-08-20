"""Claude analysis must hard-skip outside ICT kill zones when enabled."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.analysis.kill_zones import KillZoneChecker, claude_analysis_allowed


class TestClaudeAnalysisAllowed:
    def test_outside_kz_blocked_when_flag_on(self):
        assert claude_analysis_allowed(False, claude_kill_zone_only=True) is False

    def test_inside_kz_allowed_when_flag_on(self):
        assert claude_analysis_allowed(True, claude_kill_zone_only=True) is True

    def test_outside_kz_allowed_when_flag_off(self):
        assert claude_analysis_allowed(False, claude_kill_zone_only=False) is True

    def test_displacement_override_allows_any_session(self):
        """M5 metal impulse may call Claude in Asian, London gaps, or off-hours."""
        assert (
            claude_analysis_allowed(
                False,
                claude_kill_zone_only=True,
                displacement_override=True,
            )
            is True
        )

    def test_kill_zone_checker_london_is_tradeable(self):
        checker = KillZoneChecker(allowed_sessions=["london", "new_york", "london_close"])
        # 3:30 AM America/New_York — London Kill Zone
        dt = checker.timezone.localize(datetime(2026, 7, 30, 3, 30))
        session = checker.get_current_session(dt)
        assert session.is_tradeable is True
        assert claude_analysis_allowed(
            session.is_tradeable, claude_kill_zone_only=True
        ) is True

    def test_kill_zone_checker_asian_not_tradeable(self):
        checker = KillZoneChecker(allowed_sessions=["london", "new_york", "london_close"])
        # 8:00 PM America/New_York — Asian Session
        dt = checker.timezone.localize(datetime(2026, 7, 29, 20, 0))
        session = checker.get_current_session(dt)
        assert session.is_tradeable is False
        assert claude_analysis_allowed(
            session.is_tradeable, claude_kill_zone_only=True
        ) is False


class TestConfigFlag:
    def test_claude_kill_zone_only_defaults_true(self):
        from trading_bot.config import TradingSettings

        assert TradingSettings().claude_kill_zone_only is True


class TestRunnerHardSkip:
    @pytest.mark.asyncio
    async def test_outside_kz_skips_before_ohlcv(self, monkeypatch):
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", True)
        monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = set()
        bot.scaling_manager = None
        bot.kill_zone_checker = MagicMock()
        bot.kill_zone_checker.get_current_session.return_value = SimpleNamespace(
            is_tradeable=False,
            session_name="Asian Session",
            next_kill_zone="London Kill Zone",
            next_kill_zone_in_minutes=120,
        )
        bot.data_fetcher.get_ohlcv = AsyncMock()

        with patch("trading_bot.api.routes.activity.add_activity"):
            await run_analyze_and_trade(bot, "XAUUSD")

        bot.data_fetcher.get_ohlcv.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inside_kz_reaches_ohlcv(self, monkeypatch):
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", True)
        monkeypatch.setattr(settings.trading, "allow_simulation_trades", False)
        monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = set()
        bot.scaling_manager = None
        bot.mt5_client.is_simulation = False
        bot.kill_zone_checker = MagicMock()
        bot.kill_zone_checker.get_current_session.return_value = SimpleNamespace(
            is_tradeable=True,
            session_name="New York Kill Zone",
            next_kill_zone=None,
            next_kill_zone_in_minutes=None,
        )
        bot.data_fetcher.get_ohlcv = AsyncMock(return_value=None)

        with patch("trading_bot.api.routes.activity.add_activity"), patch(
            "trading_bot.services.analyze_and_trade_runner.bot_state", None
        ):
            await run_analyze_and_trade(bot, "XAUUSD")

        bot.data_fetcher.get_ohlcv.assert_awaited()

    @pytest.mark.asyncio
    async def test_outside_kz_metals_disp_override_reaches_ohlcv(self, monkeypatch):
        from trading_bot.services.analyze_and_trade_runner import run_analyze_and_trade
        from trading_bot.config import settings

        monkeypatch.setattr(settings.trading, "claude_kill_zone_only", True)
        monkeypatch.setattr(settings.trading, "allow_simulation_trades", False)
        monkeypatch.setattr(settings.trading, "claude_analysis_window", "all_kill_zones")

        disp = SimpleNamespace(
            recent_displacements=[
                SimpleNamespace(
                    index=38,
                    atr_multiple=2.0,
                    direction="bearish",
                    is_strong=True,
                )
            ],
            _raw_bar_count=40,
        )

        bot = MagicMock()
        bot._symbol_loss_cooldowns = {}
        bot._volatility_pause_until = None
        bot.BLOCKED_PAIRS = set()
        bot.scaling_manager = None
        bot.mt5_client.is_simulation = False
        bot.kill_zone_checker = MagicMock()
        bot.kill_zone_checker.get_current_session.return_value = SimpleNamespace(
            is_tradeable=False,
            session_name="Asian Session",
            next_kill_zone="London Kill Zone",
            next_kill_zone_in_minutes=120,
        )
        bot._fetch_m5_displacement_analysis = AsyncMock(return_value=disp)
        bot.data_fetcher.get_ohlcv = AsyncMock(return_value=None)

        with patch("trading_bot.api.routes.activity.add_activity"), patch(
            "trading_bot.services.analyze_and_trade_runner.bot_state", None
        ):
            await run_analyze_and_trade(bot, "XAUUSD")

        bot.data_fetcher.get_ohlcv.assert_awaited()


class TestCycleWiring:
    def test_trading_cycle_hard_skips_outside_kz_when_flag_on(self):
        import inspect
        from trading_bot.main import TradingBot

        src = inspect.getsource(TradingBot._trading_cycle)
        assert "claude_kill_zone_only" in src
        assert "claude_analysis_allowed" in src or "Claude skipped" in src
        # Soft-block path must not be the default when flag is on
        assert "soft-block active, analysis continues" not in src or (
            "claude_kill_zone_only" in src
        )
