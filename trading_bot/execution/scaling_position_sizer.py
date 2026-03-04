"""
Scaling Position Sizer for $1K to $100K Goal.

Implements dynamic position sizing that scales with equity growth.
Claude can recommend adjustments based on setup quality and confidence.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum

from ..config import get_symbol_spec
from ..utils.logging import get_logger
from ..api.routes.activity import add_activity

logger = get_logger(__name__)


class SetupGrade(Enum):
    """Trade setup quality grades."""
    A_PLUS = "A+"  # Perfect setup - all confluences align
    A = "A"        # Strong setup - most confluences
    B = "B"        # Average setup - some confluences
    C = "C"        # Weak setup - minimal confluences


@dataclass
class ScalingTier:
    """Defines a scaling tier based on equity range."""
    equity_min: float
    equity_max: float
    base_lots: float
    max_lots: float
    risk_percent: float  # Risk per trade as decimal
    max_daily_trades: int
    max_exposure_percent: float  # Max total exposure


# Scaling tiers for $400+ -> $100K journey
# Conservative risk management to survive drawdowns while compounding
# At 1-2% risk with 55% win rate and 2:1 R:R, expected monthly growth ~3-5%
SCALING_TIERS: List[ScalingTier] = [
    ScalingTier(0, 500, 0.01, 0.01, 0.02, 2, 0.06),          # $0-$500: Micro account, 2% risk, min lots only, 2 trades/day
    ScalingTier(500, 1000, 0.01, 0.02, 0.02, 2, 0.06),       # $500-$1K: Small account, 2% risk, 2 trades/day
    ScalingTier(1000, 2500, 0.01, 0.03, 0.02, 3, 0.06),      # $1K-$2.5K: 2% risk, 6% max exposure
    ScalingTier(2500, 5000, 0.02, 0.05, 0.02, 3, 0.06),      # $2.5K-$5K: 2% risk, 6% max exposure
    ScalingTier(5000, 10000, 0.03, 0.08, 0.02, 3, 0.08),     # $5K-$10K: 2% risk, 8% max exposure
    ScalingTier(10000, 25000, 0.05, 0.15, 0.015, 4, 0.08),   # $10K-$25K: 1.5% risk, 8% max exposure
    ScalingTier(25000, 50000, 0.10, 0.30, 0.015, 4, 0.08),   # $25K-$50K: 1.5% risk, 8% max exposure
    ScalingTier(50000, 100000, 0.20, 0.60, 0.01, 5, 0.08),   # $50K-$100K: 1% risk, 8% max exposure
    ScalingTier(100000, float('inf'), 0.50, 1.50, 0.01, 5, 0.06),  # $100K+: 1% risk, 6% max exposure
]


@dataclass
class PositionSizeResult:
    """Result of position size calculation."""
    lots: float
    risk_amount: float
    risk_percent: float
    tier_name: str
    base_lots: float
    adjustments: List[str]
    claude_recommended: bool = False
    claude_adjustment: float = 1.0


class ScalingPositionSizer:
    """
    Dynamic position sizer that scales with equity growth.
    
    Features:
    - Equity-based tier system
    - Setup quality adjustments
    - Claude confidence integration
    - Correlation exposure limits
    - Win/loss streak adjustments
    """
    
    def __init__(
        self,
        tiers: Optional[List[ScalingTier]] = None,
        min_lot_size: float = 0.01,
        lot_step: float = 0.01
    ):
        """
        Initialize the scaling position sizer.
        
        Args:
            tiers: Custom scaling tiers (uses defaults if None)
            min_lot_size: Minimum lot size (broker minimum)
            lot_step: Lot size increment (broker step)
        """
        self.tiers = tiers or SCALING_TIERS
        self.min_lot_size = min_lot_size
        self.lot_step = lot_step
        
        # Tier demotion/promotion tracking (T3-3)
        self._current_tier_index: int = 0
        self._highest_tier_index: int = 0
        self._promotion_lockout: bool = False
        self._consecutive_winners: int = 0
        self._lockout_win_threshold: int = 5  # Need 5 consecutive wins to re-promote
        
        logger.info("Scaling position sizer initialized")
    
    def get_tier(self, equity: float) -> ScalingTier:
        """Get the scaling tier for current equity."""
        for tier in self.tiers:
            if tier.equity_min <= equity < tier.equity_max:
                return tier
        
        # If equity is above all tiers, use the last (largest) tier
        if equity >= self.tiers[-1].equity_min:
            return self.tiers[-1]
        
        # If equity is below all tiers, use the first (smallest/safest) tier
        return self.tiers[0]
    
    def get_tier_name(self, equity: float) -> str:
        """Get human-readable tier name."""
        tier = self.get_tier(equity)
        return f"${tier.equity_min:,.0f}-${tier.equity_max:,.0f}"
    
    def get_tier_index(self, equity: float) -> int:
        """Get the index of the tier for current equity."""
        for i, tier in enumerate(self.tiers):
            if tier.equity_min <= equity < tier.equity_max:
                return i
        return len(self.tiers) - 1
    
    def check_tier_transition(self, equity: float) -> Dict[str, Any]:
        """
        Check for tier transitions and handle demotion/promotion.
        
        If equity drops below a tier threshold after being promoted,
        immediately demote position sizing and lock out re-promotion
        until the trader proves consistency with consecutive winners.
        
        Returns:
            Dict with transition info
        """
        new_index = self.get_tier_index(equity)
        result = {
            'tier_changed': False,
            'direction': None,
            'new_tier': self.get_tier_name(equity),
            'lockout_active': self._promotion_lockout,
            'consecutive_winners': self._consecutive_winners
        }
        
        # Check for tier demotion
        if new_index < self._current_tier_index:
            logger.warning(
                f"TIER DEMOTION: Dropped from tier {self._current_tier_index} to {new_index} "
                f"(equity: ${equity:,.0f})"
            )
            self._current_tier_index = new_index
            self._promotion_lockout = True
            self._consecutive_winners = 0
            result['tier_changed'] = True
            result['direction'] = 'demotion'
            result['lockout_active'] = True
            add_activity("tier_change", f"Scaling tier demoted to {result['new_tier']}", details={"direction": "demotion", "new_tier": result['new_tier'], "equity": equity})
        
        # Check for tier promotion
        elif new_index > self._current_tier_index:
            if self._promotion_lockout:
                if self._consecutive_winners >= self._lockout_win_threshold:
                    # Lockout cleared - allow promotion
                    logger.info(
                        f"TIER PROMOTION UNLOCKED: {self._consecutive_winners} consecutive wins. "
                        f"Promoting from tier {self._current_tier_index} to {new_index}"
                    )
                    self._current_tier_index = new_index
                    self._highest_tier_index = max(self._highest_tier_index, new_index)
                    self._promotion_lockout = False
                    self._consecutive_winners = 0
                    result['tier_changed'] = True
                    result['direction'] = 'promotion'
                    result['lockout_active'] = False
                    add_activity("tier_change", f"Scaling tier promoted to {result['new_tier']} (lockout cleared)", details={"direction": "promotion", "new_tier": result['new_tier'], "equity": equity, "consecutive_wins": self._lockout_win_threshold})
                else:
                    # Still in lockout - use demoted tier
                    logger.info(
                        f"Tier promotion BLOCKED by lockout: need {self._lockout_win_threshold - self._consecutive_winners} "
                        f"more consecutive wins"
                    )
                    result['lockout_active'] = True
            else:
                # Normal promotion
                logger.info(
                    f"TIER PROMOTION: Moving from tier {self._current_tier_index} to {new_index} "
                    f"(equity: ${equity:,.0f})"
                )
                self._current_tier_index = new_index
                self._highest_tier_index = max(self._highest_tier_index, new_index)
                result['tier_changed'] = True
                result['direction'] = 'promotion'
                add_activity("tier_change", f"Scaling tier promoted to {result['new_tier']}", details={"direction": "promotion", "new_tier": result['new_tier'], "equity": equity})
        
        return result
    
    def record_trade_result(self, is_winner: bool):
        """
        Record a trade result for tier lockout tracking.
        
        Args:
            is_winner: True if the trade was profitable
        """
        if is_winner:
            self._consecutive_winners += 1
            if self._promotion_lockout and self._consecutive_winners >= self._lockout_win_threshold:
                logger.info(
                    f"Lockout condition met: {self._consecutive_winners} consecutive winners "
                    f"(threshold: {self._lockout_win_threshold})"
                )
        else:
            self._consecutive_winners = 0
            if not self._promotion_lockout and self._current_tier_index > 0:
                # After a loss, don't immediately demote but reset win counter
                logger.debug("Loss recorded - consecutive winner count reset")
    
    def get_effective_tier(self, equity: float) -> ScalingTier:
        """
        Get the effective tier considering lockout.
        
        During lockout, uses the demoted tier even if equity qualifies for higher.
        """
        self.check_tier_transition(equity)
        
        if self._promotion_lockout:
            # Use the locked-down tier index
            effective_index = self._current_tier_index
            return self.tiers[min(effective_index, len(self.tiers) - 1)]
        
        return self.get_tier(equity)
    
    def calculate_risk_based_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        symbol: str
    ) -> float:
        """
        Calculate position size based on risk amount.
        
        Args:
            equity: Current account equity
            entry_price: Trade entry price
            stop_loss: Stop loss price
            symbol: Trading symbol
            
        Returns:
            Position size in lots
        """
        symbol = symbol.upper()
        
        # CRITICAL: Block dangerous BTC/BIT-quoted pairs
        if symbol.endswith('BTC') or symbol.endswith('BIT'):
            logger.error(f"BLOCKED: {symbol} is a BTC/BIT-quoted pair - refusing to calculate position size!")
            return 0.0
        
        tier = self.get_tier(equity)
        risk_amount = equity * tier.risk_percent
        
        # Use centralized symbol specs for accurate pip size and pip value
        spec = get_symbol_spec(symbol)
        pip_size = spec.pip_size
        risk_pips = abs(entry_price - stop_loss) / pip_size
        
        if risk_pips == 0:
            logger.warning(f"SL distance is 0 (entry={entry_price}, sl={stop_loss}) — cannot size position")
            return 0.0  # Reject: undefined risk
        
        pip_value_per_lot = spec.pip_value
        
        # Position size = Risk Amount / (Risk in Pips * Pip Value per Lot)
        lots = risk_amount / (risk_pips * pip_value_per_lot)
        
        return lots
    
    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        symbol: str,
        confidence: float = 0.7,
        setup_grade: SetupGrade = SetupGrade.B,
        win_streak: int = 0,
        loss_streak: int = 0,
        current_exposure_lots: float = 0.0,
        correlation_multiplier: float = 1.0,
        claude_recommendation: Optional[float] = None,
        confluence_count: int = 0,
    ) -> PositionSizeResult:
        """
        Calculate position size with all adjustments.
        
        Args:
            equity: Current account equity
            entry_price: Trade entry price
            stop_loss: Stop loss price
            symbol: Trading symbol
            confidence: Claude's confidence (0-1)
            setup_grade: Quality of the setup
            win_streak: Current winning streak
            loss_streak: Current losing streak
            current_exposure_lots: Total lots currently open
            correlation_multiplier: From correlation service (0.5 or 1.0)
            claude_recommendation: Optional direct Claude lot recommendation
            
        Returns:
            PositionSizeResult with final lot size and details
        """
        tier = self.get_tier(equity)
        adjustments = []
        
        # Start with risk-based calculation
        base_lots = self.calculate_risk_based_size(equity, entry_price, stop_loss, symbol)
        
        # Clamp to tier limits
        lots = max(tier.base_lots, min(base_lots, tier.max_lots))
        adjustments.append(f"Base: {lots:.2f} lots (tier: {self.get_tier_name(equity)})")
        
        # Adjustment 1: Setup Grade
        grade_multipliers = {
            SetupGrade.A_PLUS: 1.25,
            SetupGrade.A: 1.0,
            SetupGrade.B: 0.75,
            SetupGrade.C: 0.5
        }
        grade_mult = grade_multipliers.get(setup_grade, 0.75)
        if grade_mult != 1.0:
            lots *= grade_mult
            adjustments.append(f"Setup grade {setup_grade.value}: {grade_mult}x")
        
        # Adjustment 2: Confidence (tiered)
        if confidence < 0.68:
            conf_mult = 0.5
        elif confidence < 0.75:
            conf_mult = 0.75
        elif confidence < 0.85:
            conf_mult = 1.0
        elif confidence < 0.90:
            conf_mult = 1.25
        else:
            conf_mult = 1.5
        if conf_mult != 1.0:
            lots *= conf_mult
            adjustments.append(f"Confidence ({confidence:.0%}): {conf_mult}x")
        
        # Adjustment 2b: Confluence count (more factors = higher conviction)
        _confluence = confluence_count
        if _confluence >= 4:
            _cf_mult = 1.15
        elif _confluence == 3:
            _cf_mult = 1.0
        elif _confluence == 2:
            _cf_mult = 0.85
        elif _confluence == 1:
            _cf_mult = 0.7
        else:
            _cf_mult = 1.0
        if _cf_mult != 1.0:
            lots *= _cf_mult
            adjustments.append(f"Confluence ({_confluence}): {_cf_mult}x")
        
        # Adjustment 3: Win/Loss Streak
        if win_streak >= 3:
            # Don't get overconfident - slight reduction
            streak_mult = 0.9
            lots *= streak_mult
            adjustments.append(f"Win streak ({win_streak}): {streak_mult}x (caution)")
        elif loss_streak >= 2:
            # Reduce size on losing streak
            streak_mult = 0.7
            lots *= streak_mult
            adjustments.append(f"Loss streak ({loss_streak}): {streak_mult}x")
        
        # Adjustment 4: Correlation
        if correlation_multiplier < 1.0:
            lots *= correlation_multiplier
            adjustments.append(f"Correlation: {correlation_multiplier}x")
        
        # Adjustment 5: Exposure Limit
        # NOTE: The detailed margin-based exposure check is done by ClaudeTradeManager.precheck_trade()
        # which uses live MT5 margin data. Here we only apply a simple sanity cap:
        # total lots across all positions for this symbol shouldn't exceed tier max_lots * 3.
        # This prevents runaway sizing without the broken notional-value calculation
        # that was clamping everything to 0.01 on leveraged forex pairs.
        max_symbol_lots = tier.max_lots * 3
        if current_exposure_lots > 0 and lots + current_exposure_lots > max_symbol_lots:
            old_lots = lots
            lots = max(0, max_symbol_lots - current_exposure_lots)
            adjustments.append(f"Exposure limit: {old_lots:.2f} -> {lots:.2f}")
        
        # Adjustment 6: Claude Override (if provided, clamped to 0.5x-1.5x of calculated size)
        claude_adjusted = False
        claude_adj = 1.0
        if claude_recommendation is not None and lots > 0:
            # Claude can adjust up to 1.5x or down to 0.5x of the calculated size
            claude_adj = max(0.5, min(1.5, claude_recommendation / lots))
            old_lots = lots
            lots = lots * claude_adj  # Apply clamped adjustment instead of raw override
            claude_adjusted = True
            adjustments.append(f"Claude adjustment: {old_lots:.2f} -> {lots:.2f} lots ({claude_adj:.2f}x)")
        
        # Final normalization to broker-valid lot size (uses volume_min/max/step from MT5)
        from ..config import normalize_lots
        lots = normalize_lots(symbol, lots)
        
        # Calculate actual risk
        risk_amount = equity * tier.risk_percent * (lots / tier.base_lots if tier.base_lots > 0 else 1)
        
        return PositionSizeResult(
            lots=lots,
            risk_amount=risk_amount,
            risk_percent=tier.risk_percent,
            tier_name=self.get_tier_name(equity),
            base_lots=tier.base_lots,
            adjustments=adjustments,
            claude_recommended=claude_adjusted,
            claude_adjustment=claude_adj
        )
    
    def get_tier_info(self, equity: float) -> Dict[str, Any]:
        """Get detailed information about current tier."""
        tier = self.get_tier(equity)
        
        # Calculate progress to next tier
        if tier.equity_max == float('inf'):
            progress = 100.0
            next_tier = None
        else:
            progress = ((equity - tier.equity_min) / (tier.equity_max - tier.equity_min)) * 100
            next_tier = self.get_tier(tier.equity_max + 1)
        
        return {
            "current_tier": self.get_tier_name(equity),
            "equity_range": {
                "min": tier.equity_min,
                "max": tier.equity_max if tier.equity_max != float('inf') else None
            },
            "progress_percent": progress,
            "base_lots": tier.base_lots,
            "max_lots": tier.max_lots,
            "risk_percent": tier.risk_percent * 100,
            "max_daily_trades": tier.max_daily_trades,
            "max_exposure_percent": tier.max_exposure_percent * 100,
            "promotion_lockout": self._promotion_lockout,
            "consecutive_winners": self._consecutive_winners,
            "lockout_threshold": self._lockout_win_threshold,
            "next_tier": {
                "name": f"${next_tier.equity_min:,.0f}-${next_tier.equity_max:,.0f}" if next_tier else "MAX",
                "equity_needed": tier.equity_max - equity if tier.equity_max != float('inf') else 0,
                "base_lots": next_tier.base_lots if next_tier else None
            } if next_tier else None
        }
    
    def simulate_growth(
        self,
        starting_equity: float,
        target_equity: float,
        avg_r_per_trade: float = 1.5,
        win_rate: float = 0.55,
        trades_per_month: int = 40
    ) -> List[Dict[str, Any]]:
        """
        Simulate equity growth with scaling position sizes.
        
        Returns monthly projections.
        """
        equity = starting_equity
        projections = []
        month = 0
        
        while equity < target_equity and month < 60:  # Max 5 years
            month += 1
            tier = self.get_tier(equity)
            
            # Expected return per trade
            win_return = avg_r_per_trade * tier.risk_percent
            loss_return = -tier.risk_percent
            
            # Expected monthly return
            wins = trades_per_month * win_rate
            losses = trades_per_month * (1 - win_rate)
            
            monthly_return = (wins * win_return) + (losses * loss_return)
            
            equity *= (1 + monthly_return)
            
            projections.append({
                "month": month,
                "equity": round(equity, 2),
                "tier": self.get_tier_name(equity),
                "monthly_return_pct": round(monthly_return * 100, 1),
                "lots_per_trade": tier.base_lots
            })
        
        return projections
