"""
Tests for Risk Manager.
"""

import pytest
from trading_bot.execution.risk_manager import (
    RiskManager,
    PositionSize,
    TradeValidation,
    RiskLevel
)


@pytest.fixture
def risk_manager():
    """Create a RiskManager instance."""
    return RiskManager(
        risk_per_trade=0.01,
        max_risk_per_trade=0.02,
        max_daily_risk=0.06,
        min_risk_reward=2.0
    )


class TestRiskManager:
    """Tests for RiskManager class."""
    
    def test_initialization(self, risk_manager):
        """Test risk manager initialization."""
        assert risk_manager.risk_per_trade == 0.01
        assert risk_manager.max_risk_per_trade == 0.02
        assert risk_manager.max_daily_risk == 0.06
        assert risk_manager.min_risk_reward == 2.0
    
    def test_calculate_position_size(self, risk_manager):
        """Test position size calculation."""
        result = risk_manager.calculate_position_size(
            account_balance=10000,
            entry_price=1.0850,
            stop_loss=1.0800,
            symbol='EURUSD'
        )
        
        assert isinstance(result, PositionSize)
        assert result.lots > 0
        assert result.risk_amount == 100  # 1% of 10000
        assert result.risk_percentage == 0.01
    
    def test_position_size_with_risk_levels(self, risk_manager):
        """Test position sizing with different risk levels."""
        normal_size = risk_manager.calculate_position_size(
            account_balance=10000,
            entry_price=1.0850,
            stop_loss=1.0800,
            symbol='EURUSD',
            risk_level=RiskLevel.NORMAL
        )
        
        conservative_size = risk_manager.calculate_position_size(
            account_balance=10000,
            entry_price=1.0850,
            stop_loss=1.0800,
            symbol='EURUSD',
            risk_level=RiskLevel.CONSERVATIVE
        )
        
        aggressive_size = risk_manager.calculate_position_size(
            account_balance=10000,
            entry_price=1.0850,
            stop_loss=1.0800,
            symbol='EURUSD',
            risk_level=RiskLevel.AGGRESSIVE
        )
        
        # Conservative should be smaller than normal
        assert conservative_size.lots < normal_size.lots
        # Aggressive should be larger than normal
        assert aggressive_size.lots > normal_size.lots
    
    def test_validate_trade_long_valid(self, risk_manager):
        """Test trade validation for valid long trade."""
        result = risk_manager.validate_trade(
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            direction='long',
            symbol='EURUSD',
            account_balance=10000
        )
        
        assert isinstance(result, TradeValidation)
        assert result.is_valid == True
        assert len(result.errors) == 0
    
    def test_validate_trade_short_valid(self, risk_manager):
        """Test trade validation for valid short trade."""
        result = risk_manager.validate_trade(
            entry_price=1.0850,
            stop_loss=1.0900,
            take_profit=1.0750,
            direction='short',
            symbol='EURUSD',
            account_balance=10000
        )
        
        assert result.is_valid == True
    
    def test_validate_trade_invalid_sl_long(self, risk_manager):
        """Test that invalid SL placement is caught for long."""
        result = risk_manager.validate_trade(
            entry_price=1.0850,
            stop_loss=1.0900,  # SL above entry for long = invalid
            take_profit=1.0950,
            direction='long',
            symbol='EURUSD',
            account_balance=10000
        )
        
        assert result.is_valid == False
        assert len(result.errors) > 0
    
    def test_validate_trade_invalid_rr(self, risk_manager):
        """Test that insufficient R:R is caught."""
        result = risk_manager.validate_trade(
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0870,  # Only 0.4 R:R
            direction='long',
            symbol='EURUSD',
            account_balance=10000
        )
        
        assert result.is_valid == False
        assert any('risk/reward' in error.lower() for error in result.errors)
    
    def test_calculate_risk_reward(self, risk_manager):
        """Test risk/reward calculation."""
        rr = risk_manager.calculate_risk_reward(
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950
        )
        
        # Risk = 50 pips, Reward = 100 pips = 2.0 R:R
        assert rr == pytest.approx(2.0)
    
    def test_daily_risk_tracking(self, risk_manager):
        """Test daily risk tracking."""
        # Initial should be 0
        assert risk_manager.daily_risk_used == 0.0
        
        # Update risk
        risk_manager.update_daily_risk(0.01)
        assert risk_manager.daily_risk_used == 0.01
        
        # Update again
        risk_manager.update_daily_risk(0.01)
        assert risk_manager.daily_risk_used == 0.02
        
        # Reset
        risk_manager.reset_daily_risk()
        assert risk_manager.daily_risk_used == 0.0
    
    def test_remaining_daily_risk(self, risk_manager):
        """Test remaining daily risk calculation."""
        risk_manager.update_daily_risk(0.02)
        remaining = risk_manager.get_remaining_daily_risk()
        
        # Max daily = 0.06, used = 0.02, remaining = 0.04
        assert remaining == pytest.approx(0.04)
    
    def test_position_size_minimum(self, risk_manager):
        """Test that position size doesn't go below minimum."""
        result = risk_manager.calculate_position_size(
            account_balance=100,  # Small account
            entry_price=1.0850,
            stop_loss=1.0800,
            symbol='EURUSD'
        )
        
        # Should be at least 0.01 lot
        assert result.lots >= 0.01


class TestPositionSize:
    """Tests for PositionSize dataclass."""
    
    def test_to_dict(self):
        """Test PositionSize to_dict method."""
        ps = PositionSize(
            lots=0.5,
            units=50000,
            risk_amount=100,
            risk_percentage=0.01,
            stop_loss_pips=20,
            pip_value=5.0
        )
        
        result = ps.to_dict()
        
        assert isinstance(result, dict)
        assert result['lots'] == 0.5
        assert result['units'] == 50000
        assert result['risk_amount'] == 100


class TestTradeValidation:
    """Tests for TradeValidation dataclass."""
    
    def test_to_dict(self):
        """Test TradeValidation to_dict method."""
        tv = TradeValidation(
            is_valid=True,
            errors=[],
            warnings=['SL may be tight']
        )
        
        result = tv.to_dict()
        
        assert isinstance(result, dict)
        assert result['is_valid'] == True
        assert result['warnings'] == ['SL may be tight']


class TestRiskLevel:
    """Tests for RiskLevel enum."""
    
    def test_risk_level_values(self):
        """Test RiskLevel multipliers."""
        assert RiskLevel.CONSERVATIVE.value == 0.5
        assert RiskLevel.NORMAL.value == 1.0
        assert RiskLevel.AGGRESSIVE.value == 1.5
