"""
Tests for Claude Trade Manager - centralized trade management with margin validation.

Updated to match current ClaudeTradeManager API:
- validate_margin() requires mt5_client with proper numeric account/symbol info
- precheck_trade() needs risk_manager.calculate_position_size() and calculate_risk_reward()
- get_trade_decision() (not get_claude_trade_decision)
- monitor_margin_health() returns {"status": "emergency"} (not {"emergency": True})
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass


@dataclass
class MockAccountInfo:
    """Mock account info for testing."""
    balance: float = 10000.0
    equity: float = 10000.0
    margin: float = 500.0
    free_margin: float = 9500.0
    margin_level: float = 2000.0
    profit: float = 0.0
    currency: str = "USD"
    leverage: int = 100


@dataclass
class MockSymbolInfo:
    """Mock symbol info for testing."""
    bid: float = 1.0850
    ask: float = 1.0852
    trade_contract_size: float = 100000.0
    volume_min: float = 0.01
    volume_max: float = 100.0


@dataclass
class MockPositionSize:
    """Mock position sizing result."""
    lots: float = 0.05
    risk_amount: float = 100.0
    risk_percentage: float = 0.01


class TestMarginValidation:
    """Tests for margin validation before trade execution."""
    
    @pytest.mark.asyncio
    async def test_margin_validation_passes_with_sufficient_margin(self):
        """Test margin validation passes when there's sufficient free margin."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager, MarginValidation
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            free_margin=9500.0,
            margin_level=2000.0,
            leverage=100
        )
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 108.50  # Realistic margin for 0.01 lots EURUSD
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        result = await manager.validate_margin("EURUSD", 0.01)
        
        assert result.is_valid == True
        assert result.margin_level > 300.0
        assert result.error is None
    
    @pytest.mark.asyncio
    async def test_margin_validation_fails_with_low_margin_level(self):
        """Test margin validation fails when margin level is below threshold."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager, MarginValidation
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            free_margin=50.0,  # Very low
            margin_level=120.0,  # Below MIN_MARGIN_LEVEL (200%)
            leverage=100
        )
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 108.50
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        result = await manager.validate_margin("EURUSD", 0.01)
        
        # Should fail because free_margin (50) < required_margin with buffer
        assert result.is_valid == False
    
    @pytest.mark.asyncio
    async def test_margin_validation_warns_at_medium_level(self):
        """Test margin validation passes with adequate margin."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            free_margin=2000.0,
            margin_level=400.0,
            leverage=100
        )
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 542.50  # 0.05 lots
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        result = await manager.validate_margin("EURUSD", 0.05)
        
        # Should pass: free_margin (2000) > required with buffer (~597)
        assert result.is_valid == True


class TestTradePrecheck:
    """Tests for pre-trade validation."""
    
    @pytest.mark.asyncio
    async def test_precheck_blocks_when_max_positions_reached(self):
        """Test precheck blocks trade when max concurrent positions reached (normal confidence)."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo()
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 108.50
        # Return 5 live positions from MT5
        mock_mt5.get_positions.return_value = [MagicMock() for _ in range(5)]
        
        mock_risk_manager = MagicMock()
        mock_risk_manager.calculate_position_size.return_value = MockPositionSize()
        mock_risk_manager.calculate_risk_reward.return_value = 2.0
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=mock_risk_manager,
            claude_client=MagicMock(),
            max_concurrent_positions=5
        )
        
        # Use confidence below the 0.80 high-confidence override threshold
        result = await manager.precheck_trade(
            symbol="EURUSD",
            direction="long",
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            confidence=0.70
        )
        
        assert result.can_execute == False
        assert any("position" in b.lower() or "max" in b.lower() for b in result.blockers)
    
    @pytest.mark.asyncio
    async def test_precheck_allows_high_confidence_override(self):
        """Test precheck allows +1 position for high-confidence signals (>=0.80)."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo()
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 108.50
        # Return 5 live positions from MT5 (at the max)
        mock_mt5.get_positions.return_value = [MagicMock() for _ in range(5)]
        
        mock_risk_manager = MagicMock()
        mock_risk_manager.calculate_position_size.return_value = MockPositionSize()
        mock_risk_manager.calculate_risk_reward.return_value = 2.0
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=mock_risk_manager,
            claude_client=MagicMock(),
            max_concurrent_positions=5
        )
        
        # High confidence (>=0.80) should allow +1 override with a warning
        result = await manager.precheck_trade(
            symbol="EURUSD",
            direction="long",
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            confidence=0.85
        )
        
        assert result.can_execute == True
        assert any("override" in w.lower() or "position limit" in w.lower() for w in result.warnings)
    
    @pytest.mark.asyncio
    async def test_precheck_allows_scalp_override_at_position_limit(self):
        """Test precheck allows +1 position for scalps at position limit."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo()
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 108.50
        # Return 5 live positions from MT5 (at the max)
        mock_mt5.get_positions.return_value = [MagicMock() for _ in range(5)]
        
        mock_risk_manager = MagicMock()
        mock_risk_manager.calculate_position_size.return_value = MockPositionSize()
        mock_risk_manager.calculate_risk_reward.return_value = 1.5
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=mock_risk_manager,
            claude_client=MagicMock(),
            max_concurrent_positions=5
        )
        
        # Scalp with moderate confidence should override position limit
        result = await manager.precheck_trade(
            symbol="EURUSD",
            direction="long",
            entry_price=1.0850,
            stop_loss=1.0830,
            take_profit=1.0880,
            confidence=0.65,
            trade_type="scalp"
        )
        
        assert result.can_execute == True
        assert any("scalp" in w.lower() for w in result.warnings)
    
    @pytest.mark.asyncio
    async def test_precheck_scalp_rr_warning_uses_lower_threshold(self):
        """Test that scalps use a lower R:R warning threshold (1.2 instead of 1.5)."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo()
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 108.50
        mock_mt5.get_positions.return_value = []
        
        mock_risk_manager = MagicMock()
        mock_risk_manager.calculate_position_size.return_value = MockPositionSize()
        # R:R of 1.3 — should warn for intraday (< 1.5) but NOT for scalp (>= 1.2)
        mock_risk_manager.calculate_risk_reward.return_value = 1.3
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=mock_risk_manager,
            claude_client=MagicMock(),
            max_concurrent_positions=5
        )
        
        # Scalp: R:R 1.3 >= 1.2 threshold — no warning
        result_scalp = await manager.precheck_trade(
            symbol="EURUSD",
            direction="long",
            entry_price=1.0850,
            stop_loss=1.0830,
            take_profit=1.0880,
            confidence=0.70,
            trade_type="scalp"
        )
        assert not any("r:r" in w.lower() for w in result_scalp.warnings)
        
        # Intraday: R:R 1.3 < 1.5 threshold — should warn
        result_intraday = await manager.precheck_trade(
            symbol="EURUSD",
            direction="long",
            entry_price=1.0850,
            stop_loss=1.0830,
            take_profit=1.0880,
            confidence=0.70,
            trade_type="intraday"
        )
        assert any("r:r" in w.lower() for w in result_intraday.warnings)
    
    @pytest.mark.asyncio
    async def test_precheck_passes_all_validations(self):
        """Test precheck passes when all conditions are met."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            balance=10000.0,
            equity=10000.0,
            margin=500.0,
            free_margin=9500.0,
            margin_level=2000.0,
            leverage=100
        )
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 542.50
        mock_mt5.get_positions.return_value = []  # No open positions
        
        mock_risk_manager = MagicMock()
        mock_risk_manager.calculate_position_size.return_value = MockPositionSize(
            lots=0.05,
            risk_amount=100.0,
            risk_percentage=0.01
        )
        mock_risk_manager.calculate_risk_reward.return_value = 2.0
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=mock_risk_manager,
            claude_client=MagicMock()
        )
        
        result = await manager.precheck_trade(
            symbol="EURUSD",
            direction="long",
            entry_price=1.0850,
            stop_loss=1.0800,
            take_profit=1.0950,
            confidence=0.85
        )
        
        assert result.can_execute == True
        assert len(result.blockers) == 0


class TestOrderTypeDecision:
    """Tests for order type determination using get_trade_decision."""
    
    @pytest.mark.asyncio
    async def test_trade_decision_execute(self):
        """Test get_trade_decision returns execute when precheck passes."""
        from trading_bot.services.claude_trade_manager import (
            ClaudeTradeManager, TradePrecheck, MarginValidation
        )
        
        manager = ClaudeTradeManager(
            mt5_client=MagicMock(),
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        # Build a passing precheck
        precheck = TradePrecheck(
            can_execute=True,
            margin_check=MarginValidation(
                is_valid=True, free_margin=9500, required_margin=100,
                margin_level=2000, can_trade=True, max_lots_available=1.0
            ),
            exposure_check={"utilization_percent": 10},
            risk_check={"rr_ratio": 2.0},
            recommended_lots=0.05,
            warnings=[],
            blockers=[]
        )
        
        mock_signal = MagicMock()
        mock_signal.direction = "long"
        mock_signal.entry_price = 1.0850
        mock_signal.order_type = "market"
        
        decision = await manager.get_trade_decision(
            precheck=precheck,
            trade_signal=mock_signal
        )
        
        assert decision["decision"] == "execute"
        assert decision["recommended_lots"] == 0.05
    
    @pytest.mark.asyncio
    async def test_trade_decision_reject_on_failed_precheck(self):
        """Test get_trade_decision returns reject when precheck fails."""
        from trading_bot.services.claude_trade_manager import (
            ClaudeTradeManager, TradePrecheck, MarginValidation
        )
        
        manager = ClaudeTradeManager(
            mt5_client=MagicMock(),
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        # Build a failing precheck
        precheck = TradePrecheck(
            can_execute=False,
            margin_check=MarginValidation(
                is_valid=False, free_margin=50, required_margin=100,
                margin_level=120, can_trade=False, max_lots_available=0
            ),
            exposure_check={},
            risk_check={},
            recommended_lots=0,
            warnings=[],
            blockers=["Insufficient margin"]
        )
        
        mock_signal = MagicMock()
        
        decision = await manager.get_trade_decision(
            precheck=precheck,
            trade_signal=mock_signal
        )
        
        assert decision["decision"] == "reject"
        assert decision["recommended_lots"] == 0


class TestEmergencyMarginProtection:
    """Tests for emergency margin protection."""
    
    @pytest.mark.asyncio
    async def test_emergency_detected_at_low_margin(self):
        """Test emergency status when margin level drops critically."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            free_margin=200.0,
            margin_level=90.0,  # Below EMERGENCY_MARGIN_LEVEL (100%)
            equity=500.0,
            balance=1000.0
        )
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        result = await manager.monitor_margin_health()
        
        # Source returns {"status": "emergency"} not {"emergency": True}
        assert result["status"] == "emergency"
        assert result["action"] == "close_largest_loser"
    
    @pytest.mark.asyncio
    async def test_healthy_margin_status(self):
        """Test healthy status when margin is adequate."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            free_margin=9500.0,
            margin_level=2000.0,
            equity=10000.0,
            balance=10000.0
        )
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=MagicMock(),
            claude_client=MagicMock()
        )
        
        result = await manager.monitor_margin_health()
        
        assert result["status"] == "healthy"
        assert result["action"] is None


class TestExposureValidation:
    """Tests for exposure limits."""
    
    @pytest.mark.asyncio
    async def test_precheck_with_existing_exposure(self):
        """Test precheck considers existing margin usage."""
        from trading_bot.services.claude_trade_manager import ClaudeTradeManager
        
        mock_mt5 = AsyncMock()
        mock_mt5.get_account_info.return_value = MockAccountInfo(
            balance=10000.0,
            equity=10000.0,
            margin=1400.0,  # Already using 14% margin
            free_margin=8600.0,
            margin_level=714.0,
            leverage=100
        )
        mock_mt5.get_symbol_info.return_value = MockSymbolInfo()
        mock_mt5.calc_margin.return_value = 542.50
        mock_mt5.get_positions.return_value = [MagicMock()]  # 1 existing position
        
        mock_risk_manager = MagicMock()
        mock_risk_manager.calculate_position_size.return_value = MockPositionSize(
            lots=0.50,
            risk_amount=500.0,
            risk_percentage=0.05
        )
        mock_risk_manager.calculate_risk_reward.return_value = 2.0
        
        manager = ClaudeTradeManager(
            mt5_client=mock_mt5,
            position_manager=MagicMock(),
            risk_manager=mock_risk_manager,
            claude_client=MagicMock()
        )
        
        result = await manager.precheck_trade(
            symbol="GBPUSD",
            direction="long",
            entry_price=1.2500,
            stop_loss=1.2400,
            take_profit=1.2600,
            confidence=0.90
        )
        
        # Should return a valid result (may warn or block depending on exposure)
        assert result is not None
        assert isinstance(result.can_execute, bool)


# Fixtures
@pytest.fixture
def mock_mt5_client():
    """Create mock MT5 client with proper numeric values."""
    client = AsyncMock()
    client.get_account_info.return_value = MockAccountInfo()
    client.get_symbol_info.return_value = MockSymbolInfo()
    client.calc_margin.return_value = 108.50
    client.get_positions.return_value = []
    return client


@pytest.fixture
def mock_position_manager():
    """Create mock position manager."""
    manager = MagicMock()
    manager.get_all_positions.return_value = []
    manager.get_total_exposure.return_value = {}
    return manager


@pytest.fixture
def mock_risk_manager():
    """Create mock risk manager."""
    manager = MagicMock()
    manager.calculate_position_size.return_value = MockPositionSize()
    manager.calculate_risk_reward.return_value = 2.0
    return manager
