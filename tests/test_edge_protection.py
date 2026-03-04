"""
Tests for the Edge Protection System.

Covers:
- ScalingManager edge health integration
- ScalingManager enhanced session thresholds
- Edge score computation
- Direction gate config
- Time-of-day config
"""

import pytest
from trading_bot.services.scaling_manager import ScalingManager, TradingMode


@pytest.fixture
def sm():
    """Create a ScalingManager instance."""
    return ScalingManager(starting_equity=5000, target_equity=50000)


# ===== Edge Health Integration =====

class TestEdgeHealthIntegration:

    def test_default_edge_score(self, sm):
        assert sm._edge_health_score == 100.0
        assert sm._blocked_symbols == set()

    def test_set_edge_health_updates_score(self, sm):
        sm.set_edge_health(55.0, {"BTCUSD": 60, "XRPUSD": 25})
        assert sm._edge_health_score == 55.0
        assert "XRPUSD" in sm._blocked_symbols
        assert "BTCUSD" not in sm._blocked_symbols

    def test_symbol_blocked_when_score_below_30(self, sm):
        sm.set_edge_health(70.0, {"BTCUSD": 29, "ETHUSD": 31})
        assert sm.is_symbol_edge_blocked("BTCUSD")
        assert not sm.is_symbol_edge_blocked("ETHUSD")
        assert not sm.is_symbol_edge_blocked("XAUUSD")

    def test_edge_risk_multiplier_healthy(self, sm):
        sm._edge_health_score = 80.0
        assert sm.get_edge_risk_multiplier() == 1.0

    def test_edge_risk_multiplier_warning(self, sm):
        sm._edge_health_score = 50.0
        assert sm.get_edge_risk_multiplier() == 0.75

    def test_edge_risk_multiplier_critical(self, sm):
        sm._edge_health_score = 30.0
        assert sm.get_edge_risk_multiplier() == 0.5

    def test_edge_forces_defensive_below_30(self, sm):
        sm.set_edge_health(25.0, {})
        mode = sm.determine_mode(5000)
        assert mode == TradingMode.DEFENSIVE

    def test_edge_forces_conservative_below_40(self, sm):
        sm.set_edge_health(35.0, {})
        mode = sm.determine_mode(5000)
        assert mode == TradingMode.CONSERVATIVE

    def test_edge_does_not_override_when_healthy(self, sm):
        sm.set_edge_health(70.0, {})
        mode = sm.determine_mode(5000)
        assert mode == TradingMode.NORMAL

    def test_blocked_symbols_refresh_on_update(self, sm):
        sm.set_edge_health(50.0, {"BTCUSD": 10})
        assert sm.is_symbol_edge_blocked("BTCUSD")
        sm.set_edge_health(60.0, {"BTCUSD": 50})
        assert not sm.is_symbol_edge_blocked("BTCUSD")


# ===== Enhanced Session Thresholds =====

class TestEnhancedSessionThresholds:

    def test_block_below_40_wr(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 3, "losses": 8, "total_r": -5.0, "trades": 11,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 0.0

    def test_reduce_40_to_45_wr(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 4, "losses": 6, "total_r": -1.0, "trades": 10,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 0.4

    def test_reduce_45_to_50_wr(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 5, "losses": 6, "total_r": 0.5, "trades": 11,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 0.6

    def test_normal_50_to_55_wr(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 6, "losses": 6, "total_r": 1.0, "trades": 12,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 1.0

    def test_boost_55_plus_wr(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 7, "losses": 5, "total_r": 3.0, "trades": 12,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 1.15

    def test_high_boost_60_plus_with_sample(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 10, "losses": 5, "total_r": 8.0, "trades": 15,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 1.3

    def test_60_plus_without_enough_sample_gives_115(self, sm):
        sm.symbol_session_stats["BTCUSD_london"] = {
            "wins": 7, "losses": 4, "total_r": 5.0, "trades": 11,
            "symbol": "BTCUSD", "session": "london"
        }
        mult = sm.get_symbol_size_multiplier("BTCUSD", "london")
        assert mult == 1.15


# ===== Edge Score Computation =====

class TestEdgeScoreComputation:

    def test_compute_edge_score_perfect(self):
        from trading_bot.api.routes.performance import _compute_edge_score
        score = _compute_edge_score(0.60, 1.0, 30)
        assert score == 100.0

    def test_compute_edge_score_zero(self):
        from trading_bot.api.routes.performance import _compute_edge_score
        score = _compute_edge_score(0.0, 0.0, 0)
        assert score == 0.0

    def test_compute_edge_score_moderate(self):
        from trading_bot.api.routes.performance import _compute_edge_score
        score = _compute_edge_score(0.45, 0.5, 20)
        assert 50 < score < 70

    def test_status_from_score_healthy(self):
        from trading_bot.api.routes.performance import _status_from_score
        assert _status_from_score(65) == "healthy"

    def test_status_from_score_warning(self):
        from trading_bot.api.routes.performance import _status_from_score
        assert _status_from_score(45) == "warning"

    def test_status_from_score_critical(self):
        from trading_bot.api.routes.performance import _status_from_score
        assert _status_from_score(28) == "critical"

    def test_status_from_score_blocked(self):
        from trading_bot.api.routes.performance import _status_from_score
        assert _status_from_score(20) == "blocked"


# ===== Config Validation =====

class TestEdgeProtectionConfig:

    def test_weak_hours_default_btc(self):
        from trading_bot.config import settings
        weak = settings.trading.weak_hours_by_symbol.get("BTCUSD", [])
        assert 12 in weak
        assert 13 in weak

    def test_weak_hours_default_xrp(self):
        from trading_bot.config import settings
        weak = settings.trading.weak_hours_by_symbol.get("XRPUSD", [])
        assert 5 in weak
        assert 20 in weak

    def test_weak_hours_unknown_symbol_empty(self):
        from trading_bot.config import settings
        weak = settings.trading.weak_hours_by_symbol.get("UNKNOWN", [])
        assert weak == []

    def test_zone_gate_defaults(self):
        from trading_bot.config import settings
        assert settings.trading.zone_gate_mode == "active"
        assert settings.trading.zone_misaligned_min_confidence == 0.75
        assert settings.trading.zone_misaligned_min_rr == 3.0
        assert settings.trading.zone_equilibrium_min_confidence == 0.65
        assert settings.trading.zone_gate_disabled_symbols == []


# ===== Zone-Aware Gate Logic =====

class TestZoneGateLogic:
    """Tests for the ICT premium/discount zone-aware gate."""

    @staticmethod
    def _make_ranging_df():
        """Create a ranging OHLCV DataFrame with clear swing high/low."""
        import pandas as pd
        import numpy as np
        dates = pd.date_range("2025-01-01", periods=30, freq="D")
        np.random.seed(42)
        mid = 2050.0
        closes = mid + 50 * np.sin(np.linspace(0, 2 * np.pi, 30))
        data = {
            "open": closes - 2,
            "high": closes + 10,
            "low": closes - 10,
            "close": closes,
            "volume": [1000] * 30,
        }
        return pd.DataFrame(data, index=dates)

    def test_short_from_premium_allowed(self):
        """ICT aligned: shorting from premium should always be allowed."""
        from trading_bot.analysis.premium_discount import PremiumDiscountAnalyzer
        df = self._make_ranging_df()
        analyzer = PremiumDiscountAnalyzer(swing_lookback=20)
        swing_high = df['high'].tail(20).max()
        swing_low = df['low'].tail(20).min()
        # Place price in premium (above 61.8%)
        premium_price = swing_low + (swing_high - swing_low) * 0.80
        result = analyzer.analyze(df, current_price=premium_price)
        assert result.retracement_percent >= 0.5
        assert result.short_valid == True

    def test_long_from_discount_allowed(self):
        """ICT aligned: buying from discount should always be allowed."""
        from trading_bot.analysis.premium_discount import PremiumDiscountAnalyzer
        df = self._make_ranging_df()
        analyzer = PremiumDiscountAnalyzer(swing_lookback=20)
        swing_high = df['high'].tail(20).max()
        swing_low = df['low'].tail(20).min()
        # Place price in discount (below 38.2%)
        discount_price = swing_low + (swing_high - swing_low) * 0.20
        result = analyzer.analyze(df, current_price=discount_price)
        assert result.retracement_percent <= 0.5
        assert result.long_valid == True

    def test_long_from_premium_is_misaligned(self):
        """Buying in premium zone is zone-misaligned and should require high conf."""
        from trading_bot.analysis.premium_discount import PremiumDiscountAnalyzer
        df = self._make_ranging_df()
        analyzer = PremiumDiscountAnalyzer(swing_lookback=20)
        swing_high = df['high'].tail(20).max()
        swing_low = df['low'].tail(20).min()
        premium_price = swing_low + (swing_high - swing_low) * 0.85
        result = analyzer.analyze(df, current_price=premium_price)
        assert result.retracement_percent >= 0.618
        assert result.long_valid == False

    def test_short_from_discount_is_misaligned(self):
        """Shorting in discount zone is zone-misaligned."""
        from trading_bot.analysis.premium_discount import PremiumDiscountAnalyzer
        df = self._make_ranging_df()
        analyzer = PremiumDiscountAnalyzer(swing_lookback=20)
        swing_high = df['high'].tail(20).max()
        swing_low = df['low'].tail(20).min()
        discount_price = swing_low + (swing_high - swing_low) * 0.15
        result = analyzer.analyze(df, current_price=discount_price)
        assert result.retracement_percent <= 0.382
        assert result.short_valid == False

    def test_zone_gate_config_mode_options(self):
        """Verify zone gate mode accepts valid values."""
        from trading_bot.config import TradingSettings
        for mode in ('active', 'shadow', 'disabled'):
            ts = TradingSettings(zone_gate_mode=mode)
            assert ts.zone_gate_mode == mode

    def test_zone_gate_disabled_for_symbol(self):
        """Per-symbol disable should fall back to legacy gate."""
        from trading_bot.config import TradingSettings
        ts = TradingSettings(zone_gate_disabled_symbols=["XAUUSD"])
        assert "XAUUSD" in ts.zone_gate_disabled_symbols
        assert "BTCUSD" not in ts.zone_gate_disabled_symbols

    def test_equilibrium_zone_classification(self):
        """Equilibrium zone requires moderate confidence."""
        from trading_bot.analysis.premium_discount import PremiumDiscountAnalyzer, PriceZone
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2025-01-01", periods=30, freq="D")
        data = {
            "open": np.linspace(2000, 2100, 30),
            "high": np.linspace(2010, 2110, 30),
            "low": np.linspace(1990, 2090, 30),
            "close": np.linspace(2005, 2105, 30),
            "volume": [1000] * 30,
        }
        df = pd.DataFrame(data, index=dates)

        analyzer = PremiumDiscountAnalyzer(swing_lookback=20)
        # Price at midpoint = equilibrium
        swing_high = df['high'].tail(20).max()
        swing_low = df['low'].tail(20).min()
        midpoint = (swing_high + swing_low) / 2
        result = analyzer.analyze(df, current_price=midpoint)
        assert result.current_zone == PriceZone.EQUILIBRIUM

    def test_fallback_on_empty_analysis(self):
        """When swing range is flat, analyzer returns equilibrium with both directions valid."""
        from trading_bot.analysis.premium_discount import PremiumDiscountAnalyzer, PriceZone

        analyzer = PremiumDiscountAnalyzer(swing_lookback=20)
        empty = analyzer._empty_analysis(2100.0)
        assert empty.current_zone == PriceZone.EQUILIBRIUM
        assert empty.long_valid is True
        assert empty.short_valid is True
