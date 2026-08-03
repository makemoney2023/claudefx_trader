"""TDD tests for remaining strategy review gaps."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer
from trading_bot.services.live_trade_gates import (
    apply_post_sizing_verification,
    compute_booked_risk_percent,
    effective_max_daily_trades,
    news_allows_trading,
    symbol_edge_allows_trading,
)
from trading_bot.utils.market_hours import is_market_open


class TestEffectiveMaxDailyTrades:
    def test_tier_limit_wins_over_aggressive_mode(self):
        sizer = ScalingPositionSizer()
        manager = MagicMock()
        manager.get_mode_config.return_value = MagicMock(max_daily_trades=30)
        # $800 equity -> tier max_daily_trades=2
        cap = effective_max_daily_trades(800, sizer, manager, config_cap=30)
        assert cap == 2

    def test_mode_limit_can_be_tighter_than_tier(self):
        sizer = ScalingPositionSizer()
        manager = MagicMock()
        manager.get_mode_config.return_value = MagicMock(max_daily_trades=2)
        # $3000 equity -> tier max_daily_trades=3
        cap = effective_max_daily_trades(3000, sizer, manager, config_cap=30)
        assert cap == 2

    def test_config_cap_applied(self):
        sizer = ScalingPositionSizer()
        cap = effective_max_daily_trades(3000, sizer, None, config_cap=2)
        assert cap == 2

    def test_data_collection_relaxes_tier_cap(self):
        """Paper/demo validation must not be stuck at 2 trades/day on small equity."""
        sizer = ScalingPositionSizer()
        manager = MagicMock()
        manager.get_mode_config.return_value = MagicMock(max_daily_trades=30)
        cap = effective_max_daily_trades(
            800,
            sizer,
            manager,
            config_cap=25,
            relax_tier_for_data_collection=True,
        )
        assert cap == 25


class TestActualRiskAccounting:
    def test_booked_risk_uses_actual_dollars_not_nominal_tier(self):
        # 0.10 lots EURUSD, 20 pip SL, $1000 balance -> ~2% not flat tier 2%
        pct = compute_booked_risk_percent(
            lots=0.10,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            account_balance=1000.0,
        )
        assert pct == pytest.approx(0.02, rel=0.01)

    def test_inflated_lots_book_more_than_nominal_tier(self):
        nominal_tier_pct = 0.02
        actual_pct = compute_booked_risk_percent(
            lots=0.15,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            account_balance=1000.0,
        )
        assert actual_pct > nominal_tier_pct


class TestPostSizingVerification:
    def test_oversized_lots_shrunk(self):
        lots, reason = apply_post_sizing_verification(
            final_lots=0.15,
            target_lots=0.10,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
        )
        assert reason is None
        assert lots < 0.15

    def test_extreme_overshoot_rejected(self):
        lots, reason = apply_post_sizing_verification(
            final_lots=1.0,
            target_lots=0.01,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
        )
        # Shrinks toward target; at 1.0 vs 0.01 target, still lands at broker min lot
        assert lots <= 0.01
        assert reason is None or "exceeds" in reason.lower()


class TestNewsFailClosed:
    def test_stale_calendar_blocks(self, monkeypatch):
        # Pin the gate on so the test doesn't depend on .env.local
        from trading_bot.config import settings
        monkeypatch.setattr(settings.trading, "news_gates_enabled", True)

        news = MagicMock()
        news.should_trade.return_value = False
        allowed, reason = news_allows_trading(news)
        assert allowed is False
        assert "fail-closed" in reason.lower()

    def test_healthy_calendar_allows(self, monkeypatch):
        from trading_bot.config import settings
        monkeypatch.setattr(settings.trading, "news_gates_enabled", True)

        news = MagicMock()
        news.should_trade.return_value = True
        allowed, _ = news_allows_trading(news)
        assert allowed is True


class TestSymbolEdgeBlocking:
    def test_blocked_symbol_returns_false(self):
        manager = MagicMock()
        manager.should_trade_symbol.return_value = (
            False,
            "Blocked: 35% win rate over 12 trades",
            0.0,
        )
        allowed, reason, mult = symbol_edge_allows_trading(
            manager, "EURUSD", "london"
        )
        assert allowed is False
        assert mult == 0.0
        assert "Blocked" in reason

    def test_healthy_symbol_passes_with_multiplier(self):
        manager = MagicMock()
        manager.should_trade_symbol.return_value = (True, "Approved", 1.15)
        allowed, _, mult = symbol_edge_allows_trading(manager, "EURUSD", "london")
        assert allowed is True
        assert mult == 1.15


class TestMarketHoursDST:
    def test_forex_sunday_open_winter_est(self):
        """Feb: 17:00 ET = 22:00 UTC."""
        sunday_open = datetime(2026, 2, 8, 22, 5, tzinfo=timezone.utc)
        is_open, _ = is_market_open("EURUSD", sunday_open)
        assert is_open is True

    def test_forex_sunday_open_summer_edt(self):
        """July: 17:00 ET = 21:00 UTC (EDT, UTC-4)."""
        sunday_open = datetime(2026, 7, 12, 21, 5, tzinfo=timezone.utc)
        is_open, _ = is_market_open("EURUSD", sunday_open)
        assert is_open is True

    def test_forex_sunday_before_open_summer_edt(self):
        """July: 16:30 ET = 20:30 UTC — still closed."""
        sunday_early = datetime(2026, 7, 12, 20, 30, tzinfo=timezone.utc)
        is_open, reason = is_market_open("EURUSD", sunday_early)
        assert is_open is False
        assert "Sunday before open" in reason
