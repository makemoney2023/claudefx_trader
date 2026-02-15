"""
Risk Management Module.

Handles position sizing, risk calculations, and trade validation
according to sound risk management principles.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Tuple
from enum import Enum

from ..config import settings, get_symbol_spec
from ..utils.logging import get_logger

logger = get_logger(__name__)


class RiskLevel(Enum):
    """Risk levels for different market conditions."""
    CONSERVATIVE = 0.5   # 50% of normal risk
    NORMAL = 1.0         # Standard risk
    AGGRESSIVE = 1.5     # 150% of normal risk (high confidence setups)


@dataclass
class PositionSize:
    """Calculated position size details."""
    lots: float                     # Position size in lots
    units: int                      # Position size in units
    risk_amount: float              # Dollar amount at risk
    risk_percentage: float          # Percentage of account at risk
    stop_loss_pips: float          # SL distance in pips
    pip_value: float               # Value per pip for this position
    
    def to_dict(self) -> dict:
        return {
            "lots": self.lots,
            "units": self.units,
            "risk_amount": self.risk_amount,
            "risk_percentage": self.risk_percentage,
            "stop_loss_pips": self.stop_loss_pips,
            "pip_value": self.pip_value
        }


@dataclass
class TradeValidation:
    """Result of trade validation."""
    is_valid: bool
    errors: list
    warnings: list
    adjusted_sl: Optional[float] = None
    adjusted_tp: Optional[float] = None
    
    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "adjusted_sl": self.adjusted_sl,
            "adjusted_tp": self.adjusted_tp
        }


class RiskManager:
    """
    Manages trading risk through position sizing and trade validation.
    
    Implements fixed-percentage risk model where each trade risks
    a consistent percentage of account equity.
    """
    
    # Legacy lot size constants - NOT USED in calculations.
    # Actual contract sizes now come from get_symbol_spec() / MT5 runtime specs.
    STANDARD_LOT = 100000  # Reference only
    MINI_LOT = 10000       # Reference only
    MICRO_LOT = 1000       # Reference only
    
    # Legacy PIP_VALUES kept for reference - actual values now come from get_symbol_spec()
    PIP_VALUES = {
        'EURUSD': 10.0,
        'GBPUSD': 10.0,
        'AUDUSD': 10.0,
        'NZDUSD': 10.0,
        'USDJPY': 9.0,
        'USDCHF': 10.0,
        'USDCAD': 7.5,
        'XAUUSD': 1.0,
        'XAGUSD': 5.0,
    }
    
    def __init__(
        self,
        risk_per_trade: Optional[float] = None,
        max_risk_per_trade: float = 0.02,
        max_daily_risk: float = 0.06,
        min_risk_reward: Optional[float] = None
    ):
        """
        Initialize the risk manager.
        
        Args:
            risk_per_trade: Default risk percentage per trade
            max_risk_per_trade: Maximum allowed risk per trade
            max_daily_risk: Maximum total daily risk
            min_risk_reward: Minimum required risk/reward ratio
        """
        self.risk_per_trade = risk_per_trade or settings.trading.risk_per_trade
        self.max_risk_per_trade = max_risk_per_trade
        self.max_daily_risk = max_daily_risk
        self.min_risk_reward = min_risk_reward or settings.trading.min_risk_reward
        
        self.daily_risk_used = 0.0
        
        logger.info(f"Risk manager initialized: {self.risk_per_trade * 100}% risk per trade")
    
    def calculate_position_size(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
        symbol: str,
        risk_level: RiskLevel = RiskLevel.NORMAL
    ) -> PositionSize:
        """
        Calculate the appropriate position size based on risk parameters.
        
        Args:
            account_balance: Current account balance
            entry_price: Entry price
            stop_loss: Stop loss price
            symbol: Trading symbol
            risk_level: Risk level adjustment
            
        Returns:
            PositionSize with calculated values
        """
        # Calculate risk amount
        adjusted_risk = self.risk_per_trade * risk_level.value
        risk_amount = account_balance * adjusted_risk
        
        # Calculate stop loss distance in pips
        sl_distance = abs(entry_price - stop_loss)
        pip_size = self._get_pip_size(symbol)
        sl_pips = sl_distance / pip_size
        
        # Get pip value per standard lot
        pip_value_per_lot = self._get_pip_value(symbol)
        
        # Calculate position size
        # Risk Amount = Position Size (lots) × SL Pips × Pip Value
        # Position Size = Risk Amount / (SL Pips × Pip Value)
        if sl_pips > 0 and pip_value_per_lot > 0:
            lots = risk_amount / (sl_pips * pip_value_per_lot)
        else:
            lots = 0.0
        
        # Normalize to broker-valid lot size (uses volume_min/max/step from MT5)
        from ..config import normalize_lots, settings
        lots = normalize_lots(symbol, lots)
        
        # Gap 25: Enforce maximum position size from settings (may be lower than broker max)
        max_position_size = getattr(settings.trading, 'max_position_size', 1.0)
        if lots > max_position_size:
            logger.warning(
                f"Position size {lots} lots exceeds config max {max_position_size} lots - capping"
            )
            lots = max_position_size
        
        # Calculate units using actual contract size
        spec = get_symbol_spec(symbol)
        units = int(lots * spec.contract_size)
        
        # Calculate actual pip value for this position
        actual_pip_value = lots * pip_value_per_lot
        
        return PositionSize(
            lots=lots,
            units=units,
            risk_amount=risk_amount,
            risk_percentage=adjusted_risk,
            stop_loss_pips=sl_pips,
            pip_value=actual_pip_value
        )
    
    def validate_trade(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        direction: str,
        symbol: str,
        account_balance: float,
        actual_risk_pct: Optional[float] = None,
        trade_type: Optional[str] = None
    ) -> TradeValidation:
        """
        Validate a trade setup against risk rules.
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            direction: 'long' or 'short'
            symbol: Trading symbol
            account_balance: Current account balance
            actual_risk_pct: The actual risk % being used (from scaling), overrides default
            trade_type: Trade type ('scalp', 'intraday', 'swing') for R:R thresholds
            
        Returns:
            TradeValidation result
        """
        errors = []
        warnings = []
        
        # Validate direction and SL/TP placement
        if direction == 'long':
            if stop_loss >= entry_price:
                errors.append("Stop loss must be below entry for long trades")
            if take_profit <= entry_price:
                errors.append("Take profit must be above entry for long trades")
        elif direction == 'short':
            if stop_loss <= entry_price:
                errors.append("Stop loss must be above entry for short trades")
            if take_profit >= entry_price:
                errors.append("Take profit must be below entry for short trades")
        else:
            errors.append(f"Invalid direction: {direction}")
        
        # Calculate risk/reward
        sl_distance = abs(entry_price - stop_loss)
        tp_distance = abs(take_profit - entry_price)
        
        if sl_distance > 0:
            risk_reward = tp_distance / sl_distance
        else:
            risk_reward = 0
            errors.append("Invalid stop loss distance")
        
        # Trade-type-aware minimum R:R thresholds
        rr_thresholds = {
            'scalp': 1.5,
            'intraday': 2.0,
            'swing': 3.0,
        }
        effective_min_rr = rr_thresholds.get((trade_type or '').lower(), self.min_risk_reward)
        
        # Check minimum R:R (use 0.01 tolerance for floating point comparison)
        if risk_reward < (effective_min_rr - 0.01):
            errors.append(f"Risk/reward {risk_reward:.2f} below minimum {effective_min_rr} ({trade_type or 'default'})")
        
        # Check daily risk limit using the actual risk % from scaling if provided
        position_size = self.calculate_position_size(
            account_balance, entry_price, stop_loss, symbol
        )
        
        # Use actual_risk_pct if provided (from scaling manager), otherwise use position_size default
        effective_risk_pct = actual_risk_pct if actual_risk_pct is not None else position_size.risk_percentage
        
        if self.daily_risk_used + effective_risk_pct > self.max_daily_risk:
            errors.append(f"Would exceed daily risk limit ({self.max_daily_risk * 100}%)")
            print(f"[RISK] {symbol}: daily_risk_used={self.daily_risk_used*100:.1f}% + this_trade={effective_risk_pct*100:.1f}% = {(self.daily_risk_used+effective_risk_pct)*100:.1f}% > max={self.max_daily_risk*100:.0f}%", flush=True)
        
        # Check if SL is too tight (less than spread + buffer)
        pip_size = self._get_pip_size(symbol)
        min_sl_pips = self._get_min_sl_pips(symbol)
        
        if (sl_distance / pip_size) < min_sl_pips:
            warnings.append(f"Stop loss may be too tight ({sl_distance / pip_size:.1f} pips)")
        
        # Check if trade size is reasonable
        if position_size.lots > 10:
            warnings.append(f"Large position size: {position_size.lots} lots")
        
        return TradeValidation(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
    
    def calculate_risk_reward(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float
    ) -> float:
        """Calculate risk/reward ratio."""
        sl_distance = abs(entry_price - stop_loss)
        tp_distance = abs(take_profit - entry_price)
        
        if sl_distance == 0:
            return 0.0
        
        return tp_distance / sl_distance
    
    def adjust_for_spread(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        direction: str,
        spread: float
    ) -> Tuple[float, float, float]:
        """
        Adjust prices to account for spread.
        
        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            direction: 'long' or 'short'
            spread: Current spread
            
        Returns:
            Adjusted (entry, sl, tp)
        """
        if direction == 'long':
            # Long: Enter at ask, SL/TP at bid
            adjusted_entry = entry_price + spread
            adjusted_sl = stop_loss  # SL hit at bid
            adjusted_tp = take_profit  # TP hit at bid
        else:
            # Short: Enter at bid, SL/TP at ask
            adjusted_entry = entry_price
            adjusted_sl = stop_loss + spread  # SL hit at ask
            adjusted_tp = take_profit + spread  # TP hit at ask
        
        return adjusted_entry, adjusted_sl, adjusted_tp
    
    def update_daily_risk(self, risk_used: float):
        """Update the daily risk counter. Accepts negative values to reclaim risk budget."""
        self.daily_risk_used += risk_used
        # Clamp to >= 0 (can go negative if reclaiming more than was tracked)
        if self.daily_risk_used < 0:
            self.daily_risk_used = 0.0
        logger.debug(f"Daily risk updated: {self.daily_risk_used * 100:.2f}%")
    
    def reset_daily_risk(self):
        """Reset daily risk counter (call at start of new day)."""
        self.daily_risk_used = 0.0
        logger.info("Daily risk counter reset")
    
    def get_remaining_daily_risk(self) -> float:
        """Get remaining daily risk allowance."""
        return max(0, self.max_daily_risk - self.daily_risk_used)
    
    def _get_pip_size(self, symbol: str) -> float:
        """Get pip size for a symbol using centralized symbol specs."""
        spec = get_symbol_spec(symbol)
        return spec.pip_size
    
    def _get_pip_value(self, symbol: str) -> float:
        """Get pip value per standard lot for a symbol using centralized symbol specs."""
        spec = get_symbol_spec(symbol)
        return spec.pip_value
    
    def _get_min_sl_pips(self, symbol: str) -> float:
        """Get minimum recommended SL in pips for a symbol using centralized symbol specs."""
        spec = get_symbol_spec(symbol)
        return spec.min_sl_pips
