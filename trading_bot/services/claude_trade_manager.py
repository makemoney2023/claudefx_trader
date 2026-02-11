"""
Claude Trade Manager - Centralized AI Trade Management Service.

Consolidates all Claude-driven trade decisions:
- Pre-trade validation (margin, exposure, risk)
- Position sizing recommendations  
- Active position management
- Post-trade learning
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum

from ..utils.logging import get_logger

logger = get_logger(__name__)


class TradeDecision(Enum):
    """Claude's trade decision types."""
    EXECUTE = "execute"
    PENDING = "pending"
    REJECT = "reject"
    REDUCE_SIZE = "reduce_size"
    WAIT = "wait"


@dataclass
class MarginValidation:
    """Result of margin validation."""
    is_valid: bool
    free_margin: float
    required_margin: float
    margin_level: float
    can_trade: bool
    max_lots_available: float
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "free_margin": self.free_margin,
            "required_margin": self.required_margin,
            "margin_level": self.margin_level,
            "can_trade": self.can_trade,
            "max_lots_available": self.max_lots_available,
            "error": self.error
        }


@dataclass
class TradePrecheck:
    """Pre-trade validation result."""
    can_execute: bool
    margin_check: MarginValidation
    exposure_check: Dict[str, Any]
    risk_check: Dict[str, Any]
    recommended_lots: float
    warnings: List[str]
    blockers: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "can_execute": self.can_execute,
            "margin_check": self.margin_check.to_dict(),
            "exposure_check": self.exposure_check,
            "risk_check": self.risk_check,
            "recommended_lots": self.recommended_lots,
            "warnings": self.warnings,
            "blockers": self.blockers
        }


class ClaudeTradeManager:
    """
    Centralized service for Claude-managed trading decisions.
    
    Responsibilities:
    1. Pre-trade validation (margin, exposure, correlation)
    2. Position sizing with Claude recommendations
    3. Order type determination (market vs pending)
    4. Active position monitoring and adjustment
    5. Post-trade analysis and learning
    """
    
    # Minimum margin level to allow new trades (200% = 2:1)
    MIN_MARGIN_LEVEL = 200.0
    
    # Emergency margin level - close positions if below
    EMERGENCY_MARGIN_LEVEL = 100.0
    
    # Minimum free margin buffer (10% for small accounts)
    MARGIN_BUFFER = 0.10
    
    def __init__(
        self,
        mt5_client,
        risk_manager,
        position_manager,
        claude_client=None,
        max_concurrent_positions: int = 5,
        max_exposure_percent: float = 0.30  # 30% max exposure (adjusted for micro accounts)
    ):
        """
        Initialize the Claude Trade Manager.
        
        Args:
            mt5_client: MT5 client for account/symbol info
            risk_manager: RiskManager for position sizing
            position_manager: PositionManager for tracking positions
            claude_client: Optional ClaudeClient for AI recommendations
            max_concurrent_positions: Maximum number of open positions
            max_exposure_percent: Maximum total exposure as percent of equity
        """
        self.mt5 = mt5_client
        self.risk_manager = risk_manager
        self.position_manager = position_manager
        self.claude = claude_client
        self.max_concurrent_positions = max_concurrent_positions
        self.max_exposure_percent = max_exposure_percent
        
        logger.info(
            f"ClaudeTradeManager initialized: "
            f"max_positions={max_concurrent_positions}, "
            f"max_exposure={max_exposure_percent*100:.0f}%"
        )
    
    async def validate_margin(
        self,
        symbol: str,
        volume: float,
        order_type: str = "buy"
    ) -> MarginValidation:
        """
        Validate margin availability before trade.
        
        CRITICAL: This should be called BEFORE any trade execution!
        
        Args:
            symbol: Trading symbol
            volume: Position size in lots
            order_type: 'buy' or 'sell'
            
        Returns:
            MarginValidation with detailed margin analysis
        """
        try:
            account = await self.mt5.get_account_info()
            
            # Get symbol info for margin calculation
            symbol_info = await self.mt5.get_symbol_info(symbol)
            
            # BEST: Use MT5's native margin calculation (handles all broker rules)
            required_margin = await self.mt5.calc_margin(symbol, volume, order_type)
            
            if required_margin is None:
                # FALLBACK: Manual calculation using ACTUAL contract size from symbol info
                leverage = account.leverage if hasattr(account, 'leverage') and account.leverage else 100
                
                # Use real contract size from symbol info, NOT hardcoded 100000
                contract_size = symbol_info.trade_contract_size if symbol_info else 100000
                
                # Get current price
                if hasattr(symbol_info, 'bid') and hasattr(symbol_info, 'ask'):
                    current_price = (symbol_info.bid + symbol_info.ask) / 2
                else:
                    current_price = symbol_info.bid if hasattr(symbol_info, 'bid') else 1.0
                
                # Calculate required margin with correct contract size
                required_margin = (volume * contract_size * current_price) / leverage
                logger.debug(
                    f"Fallback margin calc: {volume} lots * {contract_size} contract * "
                    f"${current_price:.2f} / {leverage} leverage = ${required_margin:.2f}"
                )
            
            # Apply buffer
            required_with_buffer = required_margin * (1 + self.MARGIN_BUFFER)
            
            # Check if we have enough free margin
            has_sufficient_margin = account.free_margin >= required_with_buffer
            
            # Check margin level threshold
            margin_level_ok = account.margin_level > self.MIN_MARGIN_LEVEL if account.margin_level else True
            
            # Can trade if both conditions met
            can_trade = has_sufficient_margin and margin_level_ok
            
            # Calculate max lots we can afford with current free margin
            if required_margin > 0 and volume > 0:
                margin_per_lot = required_margin / volume
                max_lots = (account.free_margin * (1 - self.MARGIN_BUFFER)) / margin_per_lot
                max_lots = max(0, round(max_lots, 2))
            else:
                max_lots = 0
            
            # Build error message if needed
            error = None
            if not has_sufficient_margin:
                error = f"Insufficient margin: need ${required_with_buffer:.2f}, have ${account.free_margin:.2f}"
            elif not margin_level_ok:
                error = f"Margin level too low: {account.margin_level:.0f}% (min: {self.MIN_MARGIN_LEVEL}%)"
            
            return MarginValidation(
                is_valid=has_sufficient_margin,
                free_margin=account.free_margin,
                required_margin=required_margin,
                margin_level=account.margin_level if account.margin_level else 0,
                can_trade=can_trade,
                max_lots_available=max_lots,
                error=error
            )
            
        except Exception as e:
            logger.error(f"Margin validation error: {e}")
            return MarginValidation(
                is_valid=False,
                free_margin=0,
                required_margin=0,
                margin_level=0,
                can_trade=False,
                max_lots_available=0,
                error=str(e)
            )
    
    async def precheck_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        confidence: float = 0.7,
        setup_grade: str = "B",
        order_type: str = "market"
    ) -> TradePrecheck:
        """
        Comprehensive pre-trade validation.
        
        Checks:
        1. Margin availability
        2. Current exposure vs limits
        3. Position count limits (relaxed for high-confidence and pending orders)
        4. Risk rules compliance
        
        Args:
            symbol: Trading symbol
            direction: 'long' or 'short'
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            confidence: Signal confidence (0-1)
            setup_grade: Setup quality grade
            order_type: Order type (market, buy_limit, sell_limit, buy_stop, sell_stop)
            
        Returns:
            TradePrecheck with validation results
        """
        warnings = []
        blockers = []
        
        try:
            # Get account info
            account = await self.mt5.get_account_info()
            
            # 1. Position count check — use live MT5 positions for accuracy
            # Internal tracker can be stale if SL/TP closed positions since last sync
            try:
                live_positions = await self.mt5.get_positions()
                current_positions = len(live_positions) if live_positions else 0
            except Exception:
                current_positions = len(self.position_manager.positions) if self.position_manager else 0
            
            is_pending = order_type in ('buy_limit', 'sell_limit', 'buy_stop', 'sell_stop')
            is_high_confidence = confidence >= 0.80
            
            if current_positions >= self.max_concurrent_positions:
                if is_pending:
                    # Pending orders don't occupy a position slot until filled,
                    # so allow them even at max positions (MT5 will enforce on fill)
                    warnings.append(
                        f"At position limit ({current_positions}/{self.max_concurrent_positions}) "
                        f"- pending order allowed (slot used on fill)"
                    )
                elif is_high_confidence and current_positions <= self.max_concurrent_positions + 1:
                    # Allow 1 extra position for very high-confidence signals (A+/A setups)
                    warnings.append(
                        f"Position limit override: {confidence:.0%} confidence signal "
                        f"({current_positions}/{self.max_concurrent_positions} positions, +1 allowed)"
                    )
                else:
                    blockers.append(
                        f"Max positions reached ({current_positions}/{self.max_concurrent_positions})"
                    )
            elif current_positions >= self.max_concurrent_positions - 1:
                warnings.append(
                    f"Approaching position limit ({current_positions}/{self.max_concurrent_positions})"
                )
            
            # 2. Calculate initial position size using risk manager
            initial_size = self.risk_manager.calculate_position_size(
                account_balance=account.balance,
                entry_price=entry_price,
                stop_loss=stop_loss,
                symbol=symbol
            )
            
            # 3. Margin validation
            order_type = "buy" if direction == "long" else "sell"
            margin_check = await self.validate_margin(symbol, initial_size.lots, order_type)
            
            if not margin_check.can_trade:
                # Instead of blocking, check if we can trade a smaller size
                if margin_check.max_lots_available >= 0.01:
                    warnings.append(
                        f"Margin reduced: wanted {initial_size.lots} lots, "
                        f"max affordable is {margin_check.max_lots_available} lots "
                        f"(free margin: ${margin_check.free_margin:.2f})"
                    )
                    # Re-validate with the smaller size to confirm it works
                    recheck = await self.validate_margin(symbol, margin_check.max_lots_available, order_type)
                    if not recheck.can_trade:
                        blockers.append(f"Margin check failed: {margin_check.error}")
                    else:
                        margin_check = recheck  # Use the valid smaller-size check
                else:
                    blockers.append(f"Margin check failed: {margin_check.error}")
            elif margin_check.margin_level and margin_check.margin_level < 500:
                warnings.append(f"Low margin level: {margin_check.margin_level:.0f}%")
            
            # 4. Exposure check — use LIVE MT5 margin data, not stale internal tracker
            # This ensures closed positions (SL/TP hit) immediately free up exposure
            current_margin_used = account.margin if hasattr(account, 'margin') else 0
            max_margin_allowed = account.equity * self.max_exposure_percent
            margin_available_for_new = max(0, max_margin_allowed - current_margin_used)
            
            # Also get live position count from MT5 for accurate count
            try:
                mt5_live_positions = await self.mt5.get_positions()
                live_position_count = len(mt5_live_positions) if mt5_live_positions else 0
            except Exception:
                live_position_count = current_positions  # fallback to internal tracker
            
            # Calculate how many lots we can afford with remaining margin budget
            if margin_check.required_margin > 0 and initial_size.lots > 0:
                margin_per_lot = margin_check.required_margin / initial_size.lots
                max_new_lots = margin_available_for_new / margin_per_lot if margin_per_lot > 0 else 0
            else:
                max_new_lots = initial_size.lots  # fallback — don't block
            
            utilization_pct = (current_margin_used / max_margin_allowed * 100) if max_margin_allowed > 0 else 0
            
            exposure_check = {
                "current_margin_used": round(current_margin_used, 2),
                "max_margin_allowed": round(max_margin_allowed, 2),
                "margin_available": round(margin_available_for_new, 2),
                "current_lots": round(max_new_lots, 2),  # kept for compatibility
                "max_lots": round(max_new_lots, 2),
                "available_lots": round(max_new_lots, 2),
                "utilization_percent": round(utilization_pct, 1),
                "live_positions": live_position_count
            }
            
            logger.info(
                f"Exposure check: margin used ${current_margin_used:.2f} / "
                f"max ${max_margin_allowed:.2f} ({utilization_pct:.0f}%), "
                f"live positions: {live_position_count}, "
                f"can open: {max_new_lots:.2f} lots of {symbol}"
            )
            
            if margin_available_for_new <= 0:
                blockers.append(
                    f"Max exposure reached: margin used ${current_margin_used:.2f} "
                    f">= limit ${max_margin_allowed:.2f} ({utilization_pct:.0f}%)"
                )
            elif utilization_pct > 80:
                warnings.append(
                    f"High exposure utilization: {utilization_pct:.0f}% "
                    f"(${current_margin_used:.2f} / ${max_margin_allowed:.2f})"
                )
            
            # 5. Adjust position size based on all constraints
            recommended_lots = min(
                initial_size.lots,
                margin_check.max_lots_available,
                exposure_check["available_lots"]
            )
            recommended_lots = max(0.01, round(recommended_lots, 2))
            
            # If recommended lots is 0 or very small, block
            if recommended_lots < 0.01:
                blockers.append("Position size too small after constraints")
                recommended_lots = 0
            
            # 6. Risk check
            rr_ratio = self.risk_manager.calculate_risk_reward(entry_price, stop_loss, take_profit)
            risk_check = {
                "risk_amount": initial_size.risk_amount,
                "risk_percent": initial_size.risk_percentage * 100,
                "rr_ratio": rr_ratio
            }
            
            if rr_ratio < 1.5:
                warnings.append(f"Low R:R ratio: {rr_ratio:.2f}")
            
            # 7. Confidence check
            if confidence < 0.6:
                warnings.append(f"Low confidence: {confidence:.0%}")
            
            can_execute = len(blockers) == 0 and recommended_lots >= 0.01
            
            return TradePrecheck(
                can_execute=can_execute,
                margin_check=margin_check,
                exposure_check=exposure_check,
                risk_check=risk_check,
                recommended_lots=recommended_lots,
                warnings=warnings,
                blockers=blockers
            )
            
        except Exception as e:
            logger.error(f"Trade precheck error: {e}")
            return TradePrecheck(
                can_execute=False,
                margin_check=MarginValidation(
                    is_valid=False, free_margin=0, required_margin=0,
                    margin_level=0, can_trade=False, max_lots_available=0,
                    error=str(e)
                ),
                exposure_check={"error": str(e)},
                risk_check={"error": str(e)},
                recommended_lots=0,
                warnings=[],
                blockers=[f"Precheck error: {str(e)}"]
            )
    
    async def get_trade_decision(
        self,
        precheck: TradePrecheck,
        trade_signal: Any,
        market_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get final trade decision based on precheck results.
        
        Args:
            precheck: TradePrecheck result
            trade_signal: The trade signal from Claude
            market_context: Optional additional market context
            
        Returns:
            Decision dict with action and reasoning
        """
        if not precheck.can_execute:
            return {
                "decision": TradeDecision.REJECT.value,
                "reason": "; ".join(precheck.blockers),
                "recommended_lots": 0
            }
        
        # If precheck passed but has warnings, note them
        if precheck.warnings:
            return {
                "decision": TradeDecision.EXECUTE.value,
                "reason": "Pre-check passed with warnings",
                "recommended_lots": precheck.recommended_lots,
                "warnings": precheck.warnings
            }
        
        return {
            "decision": TradeDecision.EXECUTE.value,
            "reason": "All pre-checks passed",
            "recommended_lots": precheck.recommended_lots
        }
    
    async def monitor_margin_health(self) -> Dict[str, Any]:
        """
        Monitor account margin health - call periodically.
        
        Returns emergency actions if margin critically low.
        """
        try:
            account = await self.mt5.get_account_info()
            
            result = {
                "margin_level": account.margin_level if account.margin_level else 0,
                "free_margin": account.free_margin,
                "equity": account.equity,
                "balance": account.balance,
                "status": "healthy",
                "action": None
            }
            
            if account.margin_level and account.margin_level < self.EMERGENCY_MARGIN_LEVEL:
                result["status"] = "emergency"
                result["action"] = "close_largest_loser"
                logger.critical(
                    f"EMERGENCY: Margin level {account.margin_level:.0f}% - "
                    f"positions at risk!"
                )
            elif account.margin_level and account.margin_level < self.MIN_MARGIN_LEVEL:
                result["status"] = "warning"
                result["action"] = "no_new_trades"
                logger.warning(
                    f"WARNING: Margin level {account.margin_level:.0f}% - "
                    f"no new trades allowed"
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Error monitoring margin health: {e}")
            return {
                "status": "error",
                "error": str(e),
                "action": "no_new_trades"
            }
    
    def determine_order_type(
        self,
        direction: str,
        entry_price: float,
        current_price: float,
        amd_phase: str = "unknown"
    ) -> str:
        """
        Determine the appropriate order type based on price and AMD phase.
        
        Args:
            direction: 'long' or 'short'
            entry_price: Desired entry price
            current_price: Current market price
            amd_phase: Current AMD cycle phase
            
        Returns:
            Order type string: 'market', 'buy_limit', 'sell_limit', 'buy_stop', 'sell_stop'
        """
        # If in distribution phase or entry at current price, use market order
        if amd_phase == "distribution" or abs(entry_price - current_price) < 0.0001:
            return "market"
        
        if direction == "long":
            if entry_price < current_price:
                return "buy_limit"  # Entry below current = limit order
            else:
                return "buy_stop"   # Entry above current = stop order
        else:  # short
            if entry_price > current_price:
                return "sell_limit"  # Entry above current = limit order
            else:
                return "sell_stop"   # Entry below current = stop order
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of the trade manager."""
        return {
            "max_concurrent_positions": self.max_concurrent_positions,
            "max_exposure_percent": self.max_exposure_percent,
            "min_margin_level": self.MIN_MARGIN_LEVEL,
            "emergency_margin_level": self.EMERGENCY_MARGIN_LEVEL,
            "margin_buffer": self.MARGIN_BUFFER,
            "active_positions": len(self.position_manager.positions) if self.position_manager else 0
        }
