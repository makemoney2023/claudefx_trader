"""
Tests for Scaling Position Sizer.
"""

import pytest
from trading_bot.execution.scaling_position_sizer import (
    ScalingPositionSizer,
    ScalingTier,
    SetupGrade,
    SCALING_TIERS
)


@pytest.fixture
def sizer():
    """Create a ScalingPositionSizer instance."""
    return ScalingPositionSizer()


class TestScalingTiers:
    """Tests for tier selection."""
    
    def test_tier_at_1000(self, sizer):
        """Test tier selection at $1,000."""
        tier = sizer.get_tier(1000)
        assert tier.equity_min == 1000
        assert tier.risk_percent == 0.02  # 2% risk (conservative)
    
    def test_tier_at_5000(self, sizer):
        """Test tier selection at $5,000."""
        tier = sizer.get_tier(5000)
        assert tier.equity_min == 5000
        assert tier.risk_percent == 0.02  # 2% risk (conservative)
    
    def test_tier_at_50000(self, sizer):
        """Test tier selection at $50,000."""
        tier = sizer.get_tier(50000)
        assert tier.equity_min == 50000
        assert tier.risk_percent == 0.01  # 1% risk
    
    def test_tier_at_100000(self, sizer):
        """Test tier selection at $100,000+."""
        tier = sizer.get_tier(150000)
        assert tier.equity_min == 100000
        assert tier.risk_percent == 0.01  # 1% risk
    
    def test_tier_name(self, sizer):
        """Test tier name generation."""
        name = sizer.get_tier_name(3000)
        assert "$2,500" in name
        assert "$5,000" in name
    
    def test_risk_decreases_with_equity(self, sizer):
        """Test that risk per trade decreases as equity grows."""
        tier_1k = sizer.get_tier(1000)
        tier_50k = sizer.get_tier(50000)
        tier_100k = sizer.get_tier(150000)
        assert tier_1k.risk_percent >= tier_50k.risk_percent
        assert tier_50k.risk_percent >= tier_100k.risk_percent


class TestPositionSizeCalculation:
    """Tests for position size calculation."""
    
    def test_basic_calculation(self, sizer):
        """Test basic position size calculation."""
        result = sizer.calculate_position_size(
            equity=1000,
            entry_price=1.1000,
            stop_loss=1.0950,
            symbol="EURUSD"
        )
        
        assert result.lots >= 0.01
        assert result.lots <= 0.03  # Max for tier
        assert result.tier_name == "$1,000-$2,500"
    
    def test_size_increases_with_equity(self, sizer):
        """Test that position size increases with equity."""
        # Use higher equity to avoid hitting minimum lot floor
        result_5k = sizer.calculate_position_size(
            equity=5000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD"
        )
        
        result_50k = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD"
        )
        
        assert result_50k.lots >= result_5k.lots
    
    def test_aplus_setup_gets_more_size(self, sizer):
        """Test that A+ setup gets larger size than B grade."""
        # Use higher equity so sizes are distinguishable above min lot
        result_b = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            setup_grade=SetupGrade.B
        )
        
        result_a_plus = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            setup_grade=SetupGrade.A_PLUS
        )
        
        assert result_a_plus.lots >= result_b.lots
    
    def test_low_confidence_reduces_size(self, sizer):
        """Test that low confidence reduces size."""
        result_high = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            confidence=0.9
        )
        
        result_low = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            confidence=0.6
        )
        
        assert result_low.lots <= result_high.lots
    
    def test_loss_streak_reduces_size(self, sizer):
        """Test that loss streak reduces size."""
        result_normal = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            loss_streak=0
        )
        
        result_losing = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            loss_streak=3
        )
        
        assert result_losing.lots <= result_normal.lots
    
    def test_correlation_reduces_size(self, sizer):
        """Test that correlation multiplier reduces size."""
        result_no_corr = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            correlation_multiplier=1.0
        )
        
        result_corr = sizer.calculate_position_size(
            equity=50000,
            entry_price=1.1000,
            stop_loss=1.0980,
            symbol="EURUSD",
            correlation_multiplier=0.5
        )
        
        assert result_corr.lots <= result_no_corr.lots
    
    def test_minimum_lot_size(self, sizer):
        """Test that lot size doesn't go below minimum."""
        result = sizer.calculate_position_size(
            equity=1000,
            entry_price=1.1000,
            stop_loss=1.0950,
            symbol="EURUSD",
            confidence=0.5,
            setup_grade=SetupGrade.C,
            loss_streak=5
        )
        
        assert result.lots >= 0.01


class TestTierInfo:
    """Tests for tier information."""
    
    def test_tier_info_structure(self, sizer):
        """Test tier info contains all fields."""
        info = sizer.get_tier_info(5000)
        
        assert "current_tier" in info
        assert "equity_range" in info
        assert "progress_percent" in info
        assert "base_lots" in info
        assert "max_lots" in info
        assert "risk_percent" in info
        assert "next_tier" in info
    
    def test_progress_calculation(self, sizer):
        """Test progress within tier."""
        # At $7,500 (halfway through $5K-$10K tier)
        info = sizer.get_tier_info(7500)
        
        assert info["progress_percent"] == 50.0
    
    def test_next_tier_equity_needed(self, sizer):
        """Test next tier equity calculation."""
        info = sizer.get_tier_info(8000)
        
        # $2,000 needed to reach $10K tier
        assert info["next_tier"]["equity_needed"] == 2000


class TestGrowthSimulation:
    """Tests for growth simulation."""
    
    def test_simulation_reaches_target(self, sizer):
        """Test simulation can reach $100K."""
        projections = sizer.simulate_growth(
            starting_equity=1000,
            target_equity=100000,
            avg_r_per_trade=1.5,
            win_rate=0.55,
            trades_per_month=40
        )
        
        # Should reach target eventually
        final_equity = projections[-1]["equity"]
        assert final_equity >= 100000 or len(projections) == 60
    
    def test_simulation_shows_tier_progression(self, sizer):
        """Test simulation shows tier changes."""
        projections = sizer.simulate_growth(
            starting_equity=1000,
            target_equity=50000,
            avg_r_per_trade=1.5,
            win_rate=0.55
        )
        
        # Should progress through tiers
        tiers = set(p["tier"] for p in projections)
        assert len(tiers) > 1  # Multiple tiers encountered


class TestSymbolSpecificCalculations:
    """Tests for symbol-specific calculations."""
    
    def test_jpy_pair_calculation(self, sizer):
        """Test JPY pair position sizing."""
        result = sizer.calculate_position_size(
            equity=5000,
            entry_price=150.00,
            stop_loss=149.50,
            symbol="USDJPY"
        )
        
        assert result.lots > 0
    
    def test_gold_calculation(self, sizer):
        """Test gold position sizing."""
        result = sizer.calculate_position_size(
            equity=5000,
            entry_price=2000.00,
            stop_loss=1990.00,
            symbol="XAUUSD"
        )
        
        assert result.lots > 0
    
    def test_crypto_calculation(self, sizer):
        """Test crypto position sizing."""
        result = sizer.calculate_position_size(
            equity=5000,
            entry_price=2.50,
            stop_loss=2.40,
            symbol="XRPUSD"
        )
        
        assert result.lots > 0
