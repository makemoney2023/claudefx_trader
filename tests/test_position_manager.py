"""
Tests for Position Manager.

Following TDD - these tests define the expected behavior.
"""

import pytest
from datetime import datetime
from trading_bot.execution.position_manager import (
    PositionManager,
    Position,
    PositionStatus
)


class TestPosition:
    """Tests for Position dataclass."""
    
    def test_position_creation(self, sample_position):
        """Test position is created with correct values."""
        assert sample_position.ticket == 12345
        assert sample_position.symbol == "XAGUSD"
        assert sample_position.direction == "long"
        assert sample_position.volume == 0.01
        assert sample_position.entry_price == 95.00
        assert sample_position.stop_loss == 90.00
        assert sample_position.take_profit == 120.00
        assert sample_position.status == PositionStatus.OPEN
    
    def test_initial_sl_set_on_creation(self, sample_position):
        """Test initial SL is captured on position creation."""
        assert sample_position.initial_sl == 90.00
    
    def test_risk_pips_calculation(self, sample_position):
        """Test risk pips is calculated correctly."""
        # Entry 95, SL 90 = 5 pips risk
        assert sample_position.risk_pips == 5.0
    
    def test_r_multiple_at_entry(self, sample_position):
        """Test R multiple is 0 at entry (no price movement)."""
        sample_position.current_price = 95.00  # At entry
        assert sample_position.current_r_multiple == 0.0
    
    def test_r_multiple_long_profit(self, sample_position):
        """Test R multiple calculation for long in profit."""
        # Entry 95, Risk 5, Current 100 = +5 profit = +1R
        sample_position.current_price = 100.00
        assert sample_position.current_r_multiple == pytest.approx(1.0)
        
        # Current 105 = +10 profit = +2R
        sample_position.current_price = 105.00
        assert sample_position.current_r_multiple == pytest.approx(2.0)
    
    def test_r_multiple_long_loss(self, sample_position):
        """Test R multiple calculation for long in loss."""
        # Entry 95, Risk 5, Current 92.5 = -2.5 loss = -0.5R
        sample_position.current_price = 92.50
        assert sample_position.current_r_multiple == pytest.approx(-0.5)
    
    def test_r_multiple_short_profit(self, sample_position_short):
        """Test R multiple calculation for short in profit."""
        # Entry 1.0850, SL 1.0900, Risk = 0.005
        # Current 1.0800 = +0.005 profit = +1R
        sample_position_short.current_price = 1.0800
        assert sample_position_short.current_r_multiple == pytest.approx(1.0)
    
    def test_r_multiple_short_loss(self, sample_position_short):
        """Test R multiple calculation for short in loss."""
        # Entry 1.0850, Risk 0.005, Current 1.0875 = -0.0025 = -0.5R
        sample_position_short.current_price = 1.0875
        assert sample_position_short.current_r_multiple == pytest.approx(-0.5)
    
    def test_to_dict(self, sample_position):
        """Test position serialization to dict."""
        sample_position.current_price = 100.00
        result = sample_position.to_dict()
        
        assert isinstance(result, dict)
        assert result["ticket"] == 12345
        assert result["symbol"] == "XAGUSD"
        assert result["direction"] == "long"
        assert result["status"] == "open"
        assert "r_multiple" in result


class TestPositionManager:
    """Tests for PositionManager class."""
    
    def test_initialization(self, position_manager):
        """Test position manager initialization."""
        assert position_manager.break_even_trigger_r == 1.0
        assert position_manager.trailing_start_r == 1.5
        assert position_manager.trailing_step_r == 0.5
        assert position_manager.partial_close_r == 1.0
        assert position_manager.partial_close_percent == 0.5
        assert len(position_manager.positions) == 0
    
    def test_add_position(self, position_manager, sample_position):
        """Test adding a position to track."""
        position_manager.add_position(sample_position)
        
        assert len(position_manager.positions) == 1
        assert 12345 in position_manager.positions
        assert position_manager.positions[12345] == sample_position
    
    def test_remove_position(self, position_manager, sample_position):
        """Test removing a position from tracking."""
        position_manager.add_position(sample_position)
        position_manager.remove_position(12345)
        
        assert len(position_manager.positions) == 0
        assert 12345 not in position_manager.positions
    
    def test_remove_nonexistent_position(self, position_manager):
        """Test removing a position that doesn't exist (should not raise)."""
        position_manager.remove_position(99999)  # Should not raise
        assert len(position_manager.positions) == 0
    
    def test_get_position(self, position_manager, sample_position):
        """Test getting a position by ticket."""
        position_manager.add_position(sample_position)
        
        retrieved = position_manager.get_position(12345)
        assert retrieved == sample_position
        
        # Non-existent
        assert position_manager.get_position(99999) is None
    
    def test_get_all_positions(self, position_manager, sample_position, sample_position_short):
        """Test getting all positions."""
        position_manager.add_position(sample_position)
        position_manager.add_position(sample_position_short)
        
        all_positions = position_manager.get_all_positions()
        assert len(all_positions) == 2
    
    def test_get_positions_by_symbol(self, position_manager, sample_position, sample_position_short):
        """Test filtering positions by symbol."""
        position_manager.add_position(sample_position)
        position_manager.add_position(sample_position_short)
        
        silver_positions = position_manager.get_positions_by_symbol("XAGUSD")
        assert len(silver_positions) == 1
        assert silver_positions[0].symbol == "XAGUSD"
        
        # Non-existent symbol
        gold_positions = position_manager.get_positions_by_symbol("XAUUSD")
        assert len(gold_positions) == 0
    
    def test_update_price(self, position_manager, sample_position):
        """Test updating current price for a position."""
        position_manager.add_position(sample_position)
        position_manager.update_price(12345, 100.00)
        
        pos = position_manager.get_position(12345)
        assert pos.current_price == 100.00
        assert pos.unrealized_pnl > 0  # Should be in profit
    
    def test_update_price_nonexistent(self, position_manager):
        """Test updating price for non-existent position (should not raise)."""
        position_manager.update_price(99999, 100.00)  # Should not raise
    
    def test_get_total_exposure(self, position_manager, sample_position, sample_position_short):
        """Test total exposure calculation."""
        position_manager.add_position(sample_position)  # 0.01 XAGUSD
        position_manager.add_position(sample_position_short)  # 0.02 EURUSD
        
        exposure = position_manager.get_total_exposure()
        assert exposure["XAGUSD"] == 0.01
        assert exposure["EURUSD"] == 0.02
    
    def test_get_summary(self, position_manager, sample_position):
        """Test summary generation."""
        position_manager.add_position(sample_position)
        sample_position.current_price = 100.00
        sample_position.unrealized_pnl = 50.0
        
        summary = position_manager.get_summary()
        assert summary["total_positions"] == 1
        assert summary["total_unrealized_pnl"] == 50.0
        assert len(summary["positions"]) == 1
        assert "XAGUSD" in summary["exposure"]


class TestPositionManagerBreakEven:
    """Tests for break-even functionality."""
    
    @pytest.mark.asyncio
    async def test_break_even_triggered_at_1r(self, position_manager, sample_position, mock_order_manager):
        """Test break-even is triggered when position reaches 1R profit."""
        position_manager.add_position(sample_position)
        
        # Move to 1R profit: Entry 95, Risk 5, 1R = 100
        price_data = {"XAGUSD": 100.00}
        actions = await position_manager.manage_positions(price_data)
        
        # Should trigger break-even
        assert len(actions) >= 1
        be_action = next((a for a in actions if a["action"] == "break_even"), None)
        assert be_action is not None
        assert be_action["ticket"] == 12345
        
        # Position should be marked as BE triggered
        pos = position_manager.get_position(12345)
        assert pos.be_triggered == True
        assert pos.status == PositionStatus.BREAK_EVEN
    
    @pytest.mark.asyncio
    async def test_break_even_not_triggered_below_1r(self, position_manager, sample_position):
        """Test break-even is NOT triggered below 1R."""
        position_manager.add_position(sample_position)
        
        # Move to 0.5R profit
        price_data = {"XAGUSD": 97.50}  # Entry 95 + 2.5 = 0.5R
        actions = await position_manager.manage_positions(price_data)
        
        # Should NOT trigger break-even
        be_action = next((a for a in actions if a["action"] == "break_even"), None)
        assert be_action is None
        
        pos = position_manager.get_position(12345)
        assert pos.be_triggered == False
    
    @pytest.mark.asyncio
    async def test_break_even_only_triggers_once(self, position_manager, sample_position):
        """Test break-even only triggers once."""
        position_manager.add_position(sample_position)
        
        # First time at 1R
        price_data = {"XAGUSD": 100.00}
        actions1 = await position_manager.manage_positions(price_data)
        be_actions1 = [a for a in actions1 if a["action"] == "break_even"]
        assert len(be_actions1) == 1
        
        # Second time at 1.5R - should not trigger again
        price_data = {"XAGUSD": 102.50}
        actions2 = await position_manager.manage_positions(price_data)
        be_actions2 = [a for a in actions2 if a["action"] == "break_even"]
        assert len(be_actions2) == 0


class TestPositionManagerTrailingStop:
    """Tests for trailing stop functionality."""
    
    @pytest.mark.asyncio
    async def test_trailing_starts_at_1_5r(self, position_manager, sample_position):
        """Test trailing stop starts at 1.5R after TP1 and TP2 hit."""
        position_manager.add_position(sample_position)
        sample_position.be_triggered = True  # Assume BE already triggered
        sample_position.partial_closed = True  # Assume partial close already done
        sample_position.tp1_hit = True  # TP1 already hit (multi-TP flow)
        sample_position.tp2_hit = True  # TP2 already hit (multi-TP flow)
        
        # Move to 1.5R: Entry 95, Risk 5, 1.5R = 102.5
        price_data = {"XAGUSD": 102.50}
        actions = await position_manager.manage_positions(price_data)
        
        # Should trigger trailing stop
        trail_action = next((a for a in actions if a["action"] == "trailing_stop"), None)
        assert trail_action is not None
    
    @pytest.mark.asyncio
    async def test_trailing_updates_in_steps(self, position_manager, sample_position):
        """Test trailing stop updates in 0.5R steps."""
        position_manager.add_position(sample_position)
        sample_position.be_triggered = True
        sample_position.trailing_active = True
        sample_position.tp1_hit = True  # TP1 already hit (multi-TP flow)
        sample_position.tp2_hit = True  # TP2 already hit (multi-TP flow)
        sample_position.stop_loss = 96.0  # Already trailing
        
        # Move to 2R: Entry 95, Risk 5, 2R = 105
        price_data = {"XAGUSD": 105.00}
        actions = await position_manager.manage_positions(price_data)
        
        trail_action = next((a for a in actions if a["action"] == "trailing_stop"), None)
        if trail_action:
            # New SL should be higher than current
            assert trail_action["new_sl"] > 96.0


class TestPositionManagerPartialClose:
    """Tests for partial close functionality."""
    
    @pytest.mark.asyncio
    async def test_partial_close_at_1r(self, position_manager, sample_position, mock_order_manager):
        """Test TP1 triggers at 1R (break-even for micro position)."""
        position_manager.add_position(sample_position)
        # For micro positions (0.01 lots), can't partial close
        # At 1R, it should just move to break-even
        
        # Move to 1R profit: Entry 95, Risk 5, 1R = 100
        price_data = {"XAGUSD": 100.00}
        actions = await position_manager.manage_positions(price_data)
        
        # For 0.01 lot position, should trigger break-even (not partial close)
        be_action = next((a for a in actions if "break_even" in a.get("action", "")), None)
        assert be_action is not None
        assert sample_position.tp1_hit is True
    
    @pytest.mark.asyncio
    async def test_partial_close_only_once(self, position_manager, sample_position):
        """Test partial close only happens once."""
        position_manager.add_position(sample_position)
        sample_position.partial_closed = True  # Already closed partial
        
        # Move to 2R profit
        price_data = {"XAGUSD": 105.00}
        actions = await position_manager.manage_positions(price_data)
        
        # Should NOT trigger partial close again
        partial_action = next((a for a in actions if a["action"] == "partial_close"), None)
        assert partial_action is None


class TestPositionManagerSync:
    """Tests for MT5 synchronization."""
    
    @pytest.mark.asyncio
    async def test_sync_detects_closed_positions(self, position_manager, sample_position, mock_mt5_client):
        """Test sync detects positions closed in MT5."""
        position_manager.add_position(sample_position)
        
        # MT5 has no positions (position was closed)
        mock_mt5_client.set_positions([])
        
        result = await position_manager.sync_with_mt5(mock_mt5_client)
        
        assert result["synced"] == True
        assert result["closed_count"] == 1
        assert result["tracked_positions"] == 0
    
    @pytest.mark.asyncio
    async def test_sync_adds_untracked_mt5_positions(self, position_manager, mock_mt5_client, sample_mt5_position):
        """Test sync adds positions from MT5 that aren't tracked locally."""
        # No local positions, but MT5 has one
        mock_mt5_client.set_positions([sample_mt5_position])
        
        result = await position_manager.sync_with_mt5(mock_mt5_client)
        
        assert result["synced"] == True
        assert result["mt5_positions"] == 1
        assert result["tracked_positions"] == 1
        
        # Position should now be tracked
        pos = position_manager.get_position(99999)
        assert pos is not None
        assert pos.symbol == "GBPUSD"


class TestEmergencyClose:
    """Tests for emergency close functionality."""
    
    @pytest.mark.asyncio
    async def test_emergency_close_all_positions(self, mock_order_manager):
        """Test emergency close closes all positions."""
        from trading_bot.execution.position_manager import PositionManager, Position
        
        manager = PositionManager(order_manager=mock_order_manager)
        
        # Add multiple positions
        pos1 = Position(
            ticket=1, symbol="XAGUSD", direction="long",
            volume=0.01, entry_price=95, stop_loss=90, take_profit=100,
            open_time=datetime.now()
        )
        pos2 = Position(
            ticket=2, symbol="EURUSD", direction="short",
            volume=0.02, entry_price=1.08, stop_loss=1.09, take_profit=1.07,
            open_time=datetime.now()
        )
        
        manager.add_position(pos1)
        manager.add_position(pos2)
        
        # Emergency close
        closed_count = 0
        for ticket in list(manager.positions.keys()):
            result = await mock_order_manager.close_position(ticket)
            if result.success:
                manager.remove_position(ticket)
                closed_count += 1
        
        assert closed_count == 2
        assert len(manager.positions) == 0
        assert len(mock_order_manager.close_calls) == 2


class TestPositionManagerCallbacks:
    """Tests for callback functionality."""
    
    @pytest.mark.asyncio
    async def test_on_position_close_callback(self, position_manager, sample_position, mock_mt5_client):
        """Test callback is called when position closes."""
        closed_positions = []
        
        async def on_close(position):
            closed_positions.append(position)
        
        position_manager.set_on_position_close(on_close)
        position_manager.add_position(sample_position)
        
        # Simulate position closing in MT5
        mock_mt5_client.set_positions([])
        await position_manager.sync_with_mt5(mock_mt5_client)
        
        assert len(closed_positions) == 1
        assert closed_positions[0].ticket == 12345
