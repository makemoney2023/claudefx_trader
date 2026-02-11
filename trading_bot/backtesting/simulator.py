"""
Order execution simulator for backtesting.

Simulates:
- Market orders
- Stop loss and take profit execution
- Slippage and spread
- Position management
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
import uuid

from ..utils.logging import get_logger

logger = get_logger(__name__)


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class SimulatedOrder:
    """Simulated order."""
    order_id: str
    symbol: str
    direction: str  # 'long' or 'short'
    order_type: OrderType
    volume: float
    price: Optional[float] = None  # For limit/stop orders
    stop_loss: float = 0
    take_profit: float = 0
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "pending"
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None


@dataclass
class SimulatedPosition:
    """Simulated open position."""
    position_id: str
    symbol: str
    direction: str
    volume: float
    entry_price: float
    entry_time: datetime
    stop_loss: float
    take_profit: float
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    profit_loss: float = 0
    profit_loss_pips: float = 0
    r_multiple: float = 0
    status: PositionStatus = PositionStatus.OPEN
    
    # ICT context
    ict_concepts: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_reason": self.exit_reason,
            "profit_loss": self.profit_loss,
            "profit_loss_pips": self.profit_loss_pips,
            "r_multiple": self.r_multiple,
            "status": self.status.value,
            "ict_concepts": self.ict_concepts,
        }


class OrderSimulator:
    """
    Simulates order execution for backtesting.
    
    Features:
    - Realistic slippage simulation
    - Spread handling
    - SL/TP execution
    - Position tracking
    """
    
    def __init__(
        self,
        initial_balance: float = 10000.0,
        spread_pips: float = 1.0,
        slippage_pips: float = 0.5,
        commission_per_lot: float = 7.0
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
        self.commission_per_lot = commission_per_lot
        
        self.positions: List[SimulatedPosition] = []
        self.closed_positions: List[SimulatedPosition] = []
        self.orders: List[SimulatedOrder] = []
        
        logger.info(f"OrderSimulator initialized with balance: ${initial_balance}")
    
    def reset(self):
        """Reset simulator to initial state."""
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.positions = []
        self.closed_positions = []
        self.orders = []
    
    def place_market_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        current_price: float,
        stop_loss: float,
        take_profit: float,
        timestamp: datetime,
        ict_concepts: Optional[Dict[str, Any]] = None
    ) -> Optional[SimulatedPosition]:
        """
        Place a market order and immediately execute it.
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            volume: Position size in lots
            current_price: Current market price
            stop_loss: Stop loss price
            take_profit: Take profit price
            timestamp: Order timestamp
            ict_concepts: ICT concepts used for this trade
            
        Returns:
            Created position or None if failed
        """
        # Calculate fill price with spread and slippage
        pip_value = 0.01 if "JPY" in symbol or symbol == "XAUUSD" else 0.0001
        spread = self.spread_pips * pip_value
        slippage = self.slippage_pips * pip_value
        
        if direction == "long":
            fill_price = current_price + spread / 2 + slippage
        else:
            fill_price = current_price - spread / 2 - slippage
        
        # Calculate commission
        commission = volume * self.commission_per_lot
        self.balance -= commission
        
        # Create position
        position = SimulatedPosition(
            position_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction=direction,
            volume=volume,
            entry_price=fill_price,
            entry_time=timestamp,
            stop_loss=stop_loss,
            take_profit=take_profit,
            ict_concepts=ict_concepts or {}
        )
        
        self.positions.append(position)
        logger.debug(f"Opened {direction} position on {symbol} at {fill_price}")
        
        return position
    
    def update_positions(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        timestamp: datetime
    ) -> List[SimulatedPosition]:
        """
        Update positions with new price data and check for SL/TP hits.
        
        Args:
            symbol: Trading symbol
            high: Bar high price
            low: Bar low price
            close: Bar close price
            timestamp: Bar timestamp
            
        Returns:
            List of closed positions
        """
        closed = []
        pip_value = 0.01 if "JPY" in symbol or symbol == "XAUUSD" else 0.0001
        
        for position in list(self.positions):
            if position.symbol != symbol:
                continue
            
            exit_price = None
            exit_reason = ""
            
            if position.direction == "long":
                # Check stop loss
                if low <= position.stop_loss:
                    exit_price = position.stop_loss
                    exit_reason = "stop_loss"
                # Check take profit
                elif high >= position.take_profit:
                    exit_price = position.take_profit
                    exit_reason = "take_profit"
            else:  # short
                # Check stop loss
                if high >= position.stop_loss:
                    exit_price = position.stop_loss
                    exit_reason = "stop_loss"
                # Check take profit
                elif low <= position.take_profit:
                    exit_price = position.take_profit
                    exit_reason = "take_profit"
            
            if exit_price:
                position = self._close_position(
                    position, exit_price, timestamp, exit_reason, pip_value
                )
                closed.append(position)
        
        # Update equity
        self._update_equity(close, pip_value)
        
        return closed
    
    def close_all_positions(
        self,
        price: float,
        timestamp: datetime,
        reason: str = "manual"
    ) -> List[SimulatedPosition]:
        """Close all open positions."""
        closed = []
        
        for position in list(self.positions):
            pip_value = 0.01 if "JPY" in position.symbol or position.symbol == "XAUUSD" else 0.0001
            position = self._close_position(
                position, price, timestamp, reason, pip_value
            )
            closed.append(position)
        
        return closed
    
    def _close_position(
        self,
        position: SimulatedPosition,
        exit_price: float,
        exit_time: datetime,
        exit_reason: str,
        pip_value: float
    ) -> SimulatedPosition:
        """Close a position and calculate P/L."""
        position.exit_price = exit_price
        position.exit_time = exit_time
        position.exit_reason = exit_reason
        position.status = PositionStatus.CLOSED
        
        # Calculate profit/loss in pips
        if position.direction == "long":
            position.profit_loss_pips = (exit_price - position.entry_price) / pip_value
        else:
            position.profit_loss_pips = (position.entry_price - exit_price) / pip_value
        
        # Calculate profit/loss in currency (assuming standard lot = 100,000 units)
        position.profit_loss = position.profit_loss_pips * position.volume * pip_value * 100000
        
        # Calculate R multiple
        risk_pips = abs(position.entry_price - position.stop_loss) / pip_value
        if risk_pips > 0:
            position.r_multiple = position.profit_loss_pips / risk_pips
        
        # Update balance
        self.balance += position.profit_loss
        
        # Move to closed positions
        self.positions.remove(position)
        self.closed_positions.append(position)
        
        logger.debug(
            f"Closed position: {position.direction} {position.symbol}, "
            f"P/L: {position.profit_loss:.2f} ({position.r_multiple:.2f}R), "
            f"Reason: {exit_reason}"
        )
        
        return position
    
    def _update_equity(self, current_price: float, pip_value: float):
        """Update equity based on open positions."""
        unrealized_pnl = 0
        
        for position in self.positions:
            if position.direction == "long":
                pips = (current_price - position.entry_price) / pip_value
            else:
                pips = (position.entry_price - current_price) / pip_value
            
            unrealized_pnl += pips * position.volume * pip_value * 100000
        
        self.equity = self.balance + unrealized_pnl
    
    def get_account_state(self) -> Dict[str, Any]:
        """Get current account state."""
        return {
            "balance": self.balance,
            "equity": self.equity,
            "open_positions": len(self.positions),
            "closed_positions": len(self.closed_positions),
            "unrealized_pnl": self.equity - self.balance
        }
    
    def get_position_summary(self) -> Dict[str, Any]:
        """Get summary of all positions."""
        if not self.closed_positions:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0,
                "total_r": 0
            }
        
        wins = [p for p in self.closed_positions if p.profit_loss > 0]
        losses = [p for p in self.closed_positions if p.profit_loss <= 0]
        
        return {
            "total_trades": len(self.closed_positions),
            "wins": len(wins),
            "losses": len(losses),
            "total_pnl": sum(p.profit_loss for p in self.closed_positions),
            "total_r": sum(p.r_multiple for p in self.closed_positions)
        }
