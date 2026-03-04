"""
Tests for newly-added add_activity() calls.

Covers:
- ScalingManager edge health activities (score change, symbol blocked)
- ScalingPositionSizer tier change activities
"""

import pytest
from unittest.mock import patch, MagicMock

from trading_bot.services.scaling_manager import ScalingManager, TradingMode


@pytest.fixture
def sm():
    return ScalingManager(starting_equity=5000, target_equity=50000)


class TestEdgeHealthActivities:

    def test_edge_health_significant_change_fires_activity(self, sm):
        """A score change > 10 points should emit an edge_health activity."""
        with patch("trading_bot.services.scaling_manager.add_activity") as mock_add:
            sm._edge_health_score = 80.0
            sm.set_edge_health(65.0, {})
            edge_calls = [c for c in mock_add.call_args_list if c[0][0] == "edge_health"]
            assert len(edge_calls) >= 1

    def test_edge_health_small_change_no_activity(self, sm):
        """A score change <= 10 points should NOT emit an edge_health activity."""
        with patch("trading_bot.services.scaling_manager.add_activity") as mock_add:
            sm._edge_health_score = 80.0
            sm.set_edge_health(75.0, {})
            edge_calls = [c for c in mock_add.call_args_list if c[0][0] == "edge_health"]
            assert len(edge_calls) == 0

    def test_newly_blocked_symbol_fires_activity(self, sm):
        """A symbol dropping below 30 should emit an edge_blocked activity."""
        with patch("trading_bot.services.scaling_manager.add_activity") as mock_add:
            sm._blocked_symbols = set()
            sm.set_edge_health(70.0, {"BTCUSD": 25, "EURUSD": 60})
            blocked_calls = [c for c in mock_add.call_args_list if c[0][0] == "edge_blocked"]
            assert len(blocked_calls) >= 1
            assert "BTCUSD" in str(blocked_calls[0])

    def test_already_blocked_symbol_no_duplicate_activity(self, sm):
        """A symbol already blocked should not fire another edge_blocked activity."""
        with patch("trading_bot.services.scaling_manager.add_activity") as mock_add:
            sm._blocked_symbols = {"BTCUSD"}
            sm._edge_health_score = 70.0
            sm.set_edge_health(70.0, {"BTCUSD": 20})
            blocked_calls = [c for c in mock_add.call_args_list if c[0][0] == "edge_blocked"]
            assert len(blocked_calls) == 0


class TestTierChangeActivities:

    def test_tier_demotion_fires_activity(self):
        """A tier demotion should emit a tier_change activity."""
        from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer

        sizer = ScalingPositionSizer()
        sizer._current_tier_index = 2
        sizer._highest_tier_index = 2

        with patch("trading_bot.execution.scaling_position_sizer.add_activity") as mock_add:
            result = sizer.check_tier_transition(500.0)
            assert result["tier_changed"] is True, "Tier transition should have occurred"
            assert result["direction"] == "demotion"
            demotion_calls = [c for c in mock_add.call_args_list if c[0][0] == "tier_change"]
            assert len(demotion_calls) >= 1

    def test_tier_promotion_fires_activity(self):
        """A tier promotion should emit a tier_change activity."""
        from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer

        sizer = ScalingPositionSizer()
        sizer._current_tier_index = 0
        sizer._highest_tier_index = 0
        sizer._promotion_lockout = False

        with patch("trading_bot.execution.scaling_position_sizer.add_activity") as mock_add:
            result = sizer.check_tier_transition(3000.0)
            assert result["tier_changed"] is True, "Tier transition should have occurred"
            assert result["direction"] == "promotion"
            promo_calls = [c for c in mock_add.call_args_list if c[0][0] == "tier_change"]
            assert len(promo_calls) >= 1

    def test_lockout_cleared_promotion_fires_activity(self):
        """A promotion after lockout is cleared should emit a tier_change activity with lockout info."""
        from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer

        sizer = ScalingPositionSizer()
        sizer._current_tier_index = 1
        sizer._highest_tier_index = 1
        sizer._promotion_lockout = True
        sizer._consecutive_winners = 5

        with patch("trading_bot.execution.scaling_position_sizer.add_activity") as mock_add:
            result = sizer.check_tier_transition(3000.0)
            assert result["tier_changed"] is True, "Tier transition should have occurred after lockout cleared"
            assert result["direction"] == "promotion"
            assert result["lockout_active"] is False
            promo_calls = [c for c in mock_add.call_args_list if c[0][0] == "tier_change"]
            assert len(promo_calls) >= 1
            assert "lockout cleared" in str(promo_calls[0])

    def test_blocked_promotion_no_activity(self):
        """A promotion blocked by lockout should NOT emit a tier_change activity."""
        from trading_bot.execution.scaling_position_sizer import ScalingPositionSizer

        sizer = ScalingPositionSizer()
        sizer._current_tier_index = 1
        sizer._highest_tier_index = 1
        sizer._promotion_lockout = True
        sizer._consecutive_winners = 2

        with patch("trading_bot.execution.scaling_position_sizer.add_activity") as mock_add:
            result = sizer.check_tier_transition(3000.0)
            assert result["tier_changed"] is False, "Tier should not change while lockout is active"
            tier_calls = [c for c in mock_add.call_args_list if c[0][0] == "tier_change"]
            assert len(tier_calls) == 0
