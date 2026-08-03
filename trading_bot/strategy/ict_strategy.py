"""
ICT (Inner Circle Trading) Strategy Implementation.

Combines all analysis modules to identify high-probability
trade setups using ICT methodology.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import pandas as pd

from ..analysis.market_structure import MarketStructureAnalyzer, StructureAnalysis, TrendDirection
from ..analysis.fair_value_gap import FVGDetector, FVGAnalysis, FVGType
from ..analysis.order_blocks import OrderBlockDetector, OrderBlockAnalysis, OrderBlockType
from ..analysis.liquidity import LiquidityMapper, LiquidityAnalysis
from ..analysis.kill_zones import KillZoneChecker, TradingSession
from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TradeSetup:
    """
    Represents a potential trade setup.
    
    Contains all analysis findings and trade parameters
    for a potential entry.
    """
    symbol: str
    timeframe: str
    direction: str  # 'long' or 'short'
    
    # Entry zone
    entry_zone_high: float
    entry_zone_low: float
    optimal_entry: float
    
    # Risk management
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    
    # Analysis details
    market_structure: Optional[str] = None
    entry_reason: str = ""
    
    # Confluence factors
    fvg_present: bool = False
    order_block_present: bool = False
    liquidity_swept: bool = False
    in_kill_zone: bool = False
    
    # Confidence and R:R
    confidence: float = 0.0
    risk_reward: float = 0.0
    
    # Raw analysis
    structure_analysis: Optional[StructureAnalysis] = None
    fvg_analysis: Optional[FVGAnalysis] = None
    ob_analysis: Optional[OrderBlockAnalysis] = None
    liquidity_analysis: Optional[LiquidityAnalysis] = None
    
    def __post_init__(self):
        # Calculate R:R
        if self.stop_loss and self.optimal_entry and self.take_profit_1:
            risk = abs(self.optimal_entry - self.stop_loss)
            reward = abs(self.take_profit_1 - self.optimal_entry)
            self.risk_reward = reward / risk if risk > 0 else 0
        
        # Calculate confidence based on confluence
        confluence_count = sum([
            self.fvg_present,
            self.order_block_present,
            self.liquidity_swept,
            self.in_kill_zone
        ])
        self.confidence = min(0.5 + confluence_count * 0.15, 1.0)
    
    @property
    def is_valid(self) -> bool:
        """Check if setup meets minimum criteria."""
        return (
            self.risk_reward >= 1.0 and
            self.confidence >= 0.6 and
            self.stop_loss > 0 and
            self.optimal_entry > 0
        )
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entry_zone": {
                "high": self.entry_zone_high,
                "low": self.entry_zone_low,
                "optimal": self.optimal_entry
            },
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "market_structure": self.market_structure,
            "entry_reason": self.entry_reason,
            "confluence": {
                "fvg": self.fvg_present,
                "order_block": self.order_block_present,
                "liquidity_swept": self.liquidity_swept,
                "kill_zone": self.in_kill_zone
            },
            "confidence": self.confidence,
            "risk_reward": self.risk_reward,
            "is_valid": self.is_valid
        }


class ICTStrategy:
    """
    ICT Trading Strategy Implementation.
    
    Combines market structure, FVG, order block, and liquidity
    analysis to identify high-probability trade setups.
    
    Strategy Rules:
    1. Determine HTF (H4/D1) trend direction
    2. Wait for liquidity sweep or MSS on LTF (M15/H1)
    3. Look for entry at FVG or order block
    4. Set SL beyond structure, TP at opposing liquidity
    5. Minimum 1:2 risk-reward required
    """
    
    def __init__(
        self,
        structure_analyzer: Optional[MarketStructureAnalyzer] = None,
        fvg_detector: Optional[FVGDetector] = None,
        ob_detector: Optional[OrderBlockDetector] = None,
        liquidity_mapper: Optional[LiquidityMapper] = None,
        kill_zone_checker: Optional[KillZoneChecker] = None
    ):
        """
        Initialize the ICT strategy.
        
        Args:
            structure_analyzer: Market structure analyzer
            fvg_detector: FVG detector
            ob_detector: Order block detector
            liquidity_mapper: Liquidity mapper
            kill_zone_checker: Kill zone checker
        """
        self.structure_analyzer = structure_analyzer or MarketStructureAnalyzer()
        self.fvg_detector = fvg_detector or FVGDetector()
        self.ob_detector = ob_detector or OrderBlockDetector()
        self.liquidity_mapper = liquidity_mapper or LiquidityMapper()
        self.kill_zone_checker = kill_zone_checker or KillZoneChecker()
        
        logger.info("ICT Strategy initialized")
    
    def analyze(
        self,
        htf_data: pd.DataFrame,
        ltf_data: pd.DataFrame,
        symbol: str,
        htf_name: str = "H4",
        ltf_name: str = "M15",
        require_tradeable_session: bool = True,
    ) -> Optional[TradeSetup]:
        """
        Analyze market data and identify trade setups.
        
        Args:
            htf_data: Higher timeframe DataFrame
            ltf_data: Lower timeframe DataFrame
            symbol: Trading symbol
            htf_name: HTF timeframe name
            ltf_name: LTF timeframe name
            require_tradeable_session: When True (default), return None outside
                kill-zone/tradeable sessions. Opportunity scanner passes False
                so mechanical scoring can run off-hours.
            
        Returns:
            TradeSetup if valid setup found, None otherwise
        """
        logger.info(f"Analyzing {symbol} for ICT setup")
        
        # Update pip_value on detectors for this symbol
        from ..config import get_symbol_spec
        _sym_pip = get_symbol_spec(symbol).pip_size
        if hasattr(self.fvg_detector, 'pip_value'):
            self.fvg_detector.pip_value = _sym_pip
        if hasattr(self.liquidity_mapper, 'pip_value'):
            self.liquidity_mapper.pip_value = _sym_pip
        
        # Step 1: Check if we're in a valid session
        session_info = self.kill_zone_checker.get_current_session()
        if require_tradeable_session and not session_info.is_tradeable:
            logger.info(f"Not in tradeable session: {session_info.session_name}")
            return None
        
        # Step 2: Analyze HTF for trend direction
        htf_structure = self.structure_analyzer.analyze(htf_data)
        htf_trend = htf_structure.trend
        
        logger.info(f"HTF Trend: {htf_trend.value}")
        
        if htf_trend == TrendDirection.RANGING:
            logger.info("HTF is ranging - looking for mean-reversion setup at range extremes")
            
            # Step 3 (ranging): Analyze LTF for range boundary trades
            ltf_structure = self.structure_analyzer.analyze(ltf_data)
            ltf_fvg = self.fvg_detector.detect(ltf_data)
            ltf_ob = self.ob_detector.detect(ltf_data)
            ltf_liquidity = self.liquidity_mapper.analyze(ltf_data)
            
            ranging_setup = self._find_ranging_setup(
                symbol=symbol,
                timeframe=ltf_name,
                htf_structure=htf_structure,
                ltf_structure=ltf_structure,
                ltf_fvg=ltf_fvg,
                ltf_ob=ltf_ob,
                ltf_liquidity=ltf_liquidity,
                ltf_data=ltf_data,
                in_kill_zone=session_info.is_kill_zone
            )
            
            if ranging_setup and ranging_setup.is_valid:
                logger.info(f"Ranging setup found: {ranging_setup.direction} {symbol} (mean-reversion)")
                return ranging_setup
            
            logger.info("No valid ranging setup found")
            return None
        
        # Step 3: Analyze LTF for entry
        ltf_structure = self.structure_analyzer.analyze(ltf_data)
        ltf_fvg = self.fvg_detector.detect(ltf_data)
        ltf_ob = self.ob_detector.detect(ltf_data)
        ltf_liquidity = self.liquidity_mapper.analyze(ltf_data)
        
        # Step 4: Look for trade setup
        setup = self._find_setup(
            symbol=symbol,
            timeframe=ltf_name,
            htf_trend=htf_trend,
            ltf_structure=ltf_structure,
            ltf_fvg=ltf_fvg,
            ltf_ob=ltf_ob,
            ltf_liquidity=ltf_liquidity,
            ltf_data=ltf_data,
            in_kill_zone=session_info.is_kill_zone
        )
        
        if setup and setup.is_valid:
            logger.info(f"Valid setup found: {setup.direction} {symbol}")
            return setup
        
        logger.info("No valid setup found")
        return None
    
    def _find_setup(
        self,
        symbol: str,
        timeframe: str,
        htf_trend: TrendDirection,
        ltf_structure: StructureAnalysis,
        ltf_fvg: FVGAnalysis,
        ltf_ob: OrderBlockAnalysis,
        ltf_liquidity: LiquidityAnalysis,
        ltf_data: pd.DataFrame,
        in_kill_zone: bool
    ) -> Optional[TradeSetup]:
        """
        Find a trade setup based on analysis.
        
        ICT Setup Requirements:
        - HTF trend alignment
        - Recent liquidity sweep or structure shift
        - Entry at FVG or order block
        - Clear stop loss level
        - Defined profit target
        """
        current_price = ltf_data.iloc[-1]['close']
        
        # Determine trade direction from HTF
        direction = 'long' if htf_trend == TrendDirection.BULLISH else 'short'
        
        # Check for recent liquidity sweep
        liquidity_swept = self._check_liquidity_sweep(ltf_liquidity, direction)
        
        # Find entry zone (FVG or OB)
        entry_zone = self._find_entry_zone(
            direction, ltf_fvg, ltf_ob, current_price
        )
        
        if not entry_zone:
            return None
        
        # Find stop loss level (beyond structure)
        stop_loss = self._find_stop_loss(direction, ltf_structure, ltf_data, entry_zone)
        
        # Find take profit (opposing liquidity, with SL-based fallback for min R:R)
        take_profit = self._find_take_profit(direction, ltf_liquidity, current_price, stop_loss)
        
        if not all([stop_loss, take_profit]):
            return None
        
        # Create setup
        return TradeSetup(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            entry_zone_high=entry_zone['high'],
            entry_zone_low=entry_zone['low'],
            optimal_entry=entry_zone['optimal'],
            stop_loss=stop_loss,
            take_profit_1=take_profit,
            market_structure=htf_trend.value,
            entry_reason=entry_zone.get('reason', ''),
            fvg_present=entry_zone.get('fvg', False),
            order_block_present=entry_zone.get('ob', False),
            liquidity_swept=liquidity_swept,
            in_kill_zone=in_kill_zone,
            structure_analysis=ltf_structure,
            fvg_analysis=ltf_fvg,
            ob_analysis=ltf_ob,
            liquidity_analysis=ltf_liquidity
        )
    
    def _check_liquidity_sweep(
        self,
        liquidity: LiquidityAnalysis,
        direction: str
    ) -> bool:
        """Check if relevant liquidity has been swept recently."""
        recent_sweeps = liquidity.recent_sweeps[-3:] if liquidity.recent_sweeps else []
        
        for sweep in recent_sweeps:
            # For long: want to see SSL sweep (price dipped to take stops)
            if direction == 'long' and sweep.liquidity_pool.type.value in ['sell_side_liquidity', 'equal_lows']:
                if sweep.reversal_detected:
                    return True
            # For short: want to see BSL sweep (price spiked to take stops)
            elif direction == 'short' and sweep.liquidity_pool.type.value in ['buy_side_liquidity', 'equal_highs']:
                if sweep.reversal_detected:
                    return True
        
        return False
    
    def _find_entry_zone(
        self,
        direction: str,
        fvg: FVGAnalysis,
        ob: OrderBlockAnalysis,
        current_price: float
    ) -> Optional[Dict[str, Any]]:
        """Find the best entry zone from FVGs and OBs."""
        zones = []
        
        # Check for valid FVGs
        if direction == 'long':
            # Look for bullish FVGs below current price
            for fvg_zone in fvg.bullish_fvgs:
                if fvg_zone.is_valid and fvg_zone.top <= current_price:
                    zones.append({
                        'high': fvg_zone.top,
                        'low': fvg_zone.bottom,
                        'optimal': fvg_zone.midpoint,
                        'distance': current_price - fvg_zone.midpoint,
                        'fvg': True,
                        'ob': False,
                        'reason': 'Bullish FVG'
                    })
            
            # Look for bullish OBs below current price
            for ob_zone in ob.bullish_obs:
                if ob_zone.is_valid and ob_zone.top <= current_price:
                    zones.append({
                        'high': ob_zone.top,
                        'low': ob_zone.bottom,
                        'optimal': ob_zone.top,  # Enter at top of OB for longs
                        'distance': current_price - ob_zone.top,
                        'fvg': False,
                        'ob': True,
                        'reason': 'Bullish Order Block'
                    })
        else:
            # Look for bearish FVGs above current price
            for fvg_zone in fvg.bearish_fvgs:
                if fvg_zone.is_valid and fvg_zone.bottom >= current_price:
                    zones.append({
                        'high': fvg_zone.top,
                        'low': fvg_zone.bottom,
                        'optimal': fvg_zone.midpoint,
                        'distance': fvg_zone.midpoint - current_price,
                        'fvg': True,
                        'ob': False,
                        'reason': 'Bearish FVG'
                    })
            
            # Look for bearish OBs above current price
            for ob_zone in ob.bearish_obs:
                if ob_zone.is_valid and ob_zone.bottom >= current_price:
                    zones.append({
                        'high': ob_zone.top,
                        'low': ob_zone.bottom,
                        'optimal': ob_zone.bottom,  # Enter at bottom of OB for shorts
                        'distance': ob_zone.bottom - current_price,
                        'fvg': False,
                        'ob': True,
                        'reason': 'Bearish Order Block'
                    })
        
        if not zones:
            return None
        
        # Return nearest zone
        zones.sort(key=lambda x: x['distance'])
        return zones[0]
    
    def _find_stop_loss(
        self,
        direction: str,
        structure: StructureAnalysis,
        df: pd.DataFrame,
        entry_zone: Dict[str, Any]
    ) -> Optional[float]:
        """Find appropriate stop loss level."""
        from ..config import get_symbol_spec
        
        # Symbol-specific SL buffer (C4 fix): Use percentage of price for metals/crypto
        symbol = df.attrs.get('symbol', '') if hasattr(df, 'attrs') else ''
        spec = get_symbol_spec(symbol) if symbol else None
        
        if spec and spec.category == 'metal':
            buffer = float(df['close'].iloc[-1]) * 0.001  # 0.1% of price for metals
        elif spec and spec.category == 'crypto':
            buffer = float(df['close'].iloc[-1]) * 0.002  # 0.2% of price for crypto
        elif symbol and 'JPY' in symbol.upper():
            buffer = 0.05  # 5 pips for JPY pairs
        else:
            buffer = 0.0005  # 5 pips for standard forex
        
        # Minimum SL distance: at least 10 pips equivalent from entry
        min_sl_distance = buffer * 20  # At least 20x the buffer
        
        if direction == 'long':
            entry_price = entry_zone.get('low', float(df['close'].iloc[-1]))
            # SL below recent swing low or entry zone low
            if structure.swing_lows:
                recent_low = min(s.price for s in structure.swing_lows[-3:])
                sl = min(recent_low, entry_zone['low']) - buffer
            else:
                # Fallback: use entry zone low minus a meaningful distance (C5 fix)
                sl = entry_price - max(min_sl_distance, buffer * 10)
            return sl
        else:
            entry_price = entry_zone.get('high', float(df['close'].iloc[-1]))
            # SL above recent swing high or entry zone high
            if structure.swing_highs:
                recent_high = max(s.price for s in structure.swing_highs[-3:])
                sl = max(recent_high, entry_zone['high']) + buffer
            else:
                # Fallback: use entry zone high plus a meaningful distance (C5 fix)
                sl = entry_price + max(min_sl_distance, buffer * 10)
            return sl
    
    def _find_take_profit(
        self,
        direction: str,
        liquidity: LiquidityAnalysis,
        current_price: float,
        stop_loss: Optional[float] = None
    ) -> Optional[float]:
        """
        Find take profit target at opposing liquidity.
        
        Falls back to a minimum R:R based TP if no liquidity target is found.
        """
        from ..config import settings
        min_rr = settings.trading.min_risk_reward
        
        if direction == 'long':
            # TP at buy-side liquidity (above)
            if liquidity.nearest_bsl:
                return liquidity.nearest_bsl
            # Fallback: use minimum R:R from SL distance (C3 fix)
            if stop_loss and stop_loss < current_price:
                sl_distance = current_price - stop_loss
                return current_price + (sl_distance * min_rr)
            return current_price * 1.01  # Last resort: 1% target
        else:
            # TP at sell-side liquidity (below)
            if liquidity.nearest_ssl:
                return liquidity.nearest_ssl
            # Fallback: use minimum R:R from SL distance (C3 fix)
            if stop_loss and stop_loss > current_price:
                sl_distance = stop_loss - current_price
                return current_price - (sl_distance * min_rr)
            return current_price * 0.99  # Last resort: 1% target
    
    def _find_ranging_setup(
        self,
        symbol: str,
        timeframe: str,
        htf_structure: StructureAnalysis,
        ltf_structure: StructureAnalysis,
        ltf_fvg: FVGAnalysis,
        ltf_ob: OrderBlockAnalysis,
        ltf_liquidity: LiquidityAnalysis,
        ltf_data: pd.DataFrame,
        in_kill_zone: bool
    ) -> Optional[TradeSetup]:
        """
        Find a mean-reversion trade setup in ranging markets.
        
        Strategy:
        - Identify range boundaries from swing highs/lows
        - Look for price at range extreme (top 20% or bottom 20%)
        - Enter mean-reversion with tight SL beyond the range
        - Target middle of range (1:1 R:R approximately)
        """
        current_price = ltf_data.iloc[-1]['close']
        
        # Need swing points to define the range
        if not htf_structure.swing_highs or not htf_structure.swing_lows:
            return None
        
        # Define range boundaries
        range_high = max(s.price for s in htf_structure.swing_highs[-5:])
        range_low = min(s.price for s in htf_structure.swing_lows[-5:])
        range_size = range_high - range_low
        
        if range_size <= 0:
            return None
        
        # Calculate where price is in the range (0 = bottom, 1 = top)
        range_position = (current_price - range_low) / range_size
        
        # Only take trades at range extremes
        entry_zone = None
        direction = None
        
        if range_position <= 0.20:
            # Price at bottom of range - look for long (mean-reversion up)
            direction = 'long'
            entry_zone = self._find_entry_zone(direction, ltf_fvg, ltf_ob, current_price)
            if not entry_zone:
                # Create a synthetic entry zone at range low
                entry_zone = {
                    'high': range_low + range_size * 0.1,
                    'low': range_low,
                    'optimal': current_price,
                    'fvg': False,
                    'ob': False,
                    'reason': 'Range bottom mean-reversion'
                }
        
        elif range_position >= 0.80:
            # Price at top of range - look for short (mean-reversion down)
            direction = 'short'
            entry_zone = self._find_entry_zone(direction, ltf_fvg, ltf_ob, current_price)
            if not entry_zone:
                # Create a synthetic entry zone at range high
                entry_zone = {
                    'high': range_high,
                    'low': range_high - range_size * 0.1,
                    'optimal': current_price,
                    'fvg': False,
                    'ob': False,
                    'reason': 'Range top mean-reversion'
                }
        else:
            # Price in the middle of the range - no clear edge
            return None
        
        if not entry_zone or not direction:
            return None
        
        # Stop loss: beyond the range boundary with buffer
        from ..config import get_symbol_spec
        spec = get_symbol_spec(symbol) if symbol else None
        
        if spec and spec.category == 'metal':
            buffer = current_price * 0.001
        elif spec and spec.category == 'crypto':
            buffer = current_price * 0.002
        elif symbol and 'JPY' in symbol.upper():
            buffer = 0.05
        else:
            buffer = 0.0005
        
        if direction == 'long':
            stop_loss = range_low - buffer
            # Target: middle of range (approximately 1:1 R:R)
            sl_distance = current_price - stop_loss
            take_profit = current_price + sl_distance  # 1:1 R:R
        else:
            stop_loss = range_high + buffer
            sl_distance = stop_loss - current_price
            take_profit = current_price - sl_distance  # 1:1 R:R
        
        return TradeSetup(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            entry_zone_high=entry_zone['high'],
            entry_zone_low=entry_zone['low'],
            optimal_entry=entry_zone['optimal'],
            stop_loss=stop_loss,
            take_profit_1=take_profit,
            market_structure='ranging',
            entry_reason=f"Mean-reversion at range {'bottom' if direction == 'long' else 'top'}",
            fvg_present=entry_zone.get('fvg', False),
            order_block_present=entry_zone.get('ob', False),
            liquidity_swept=False,
            in_kill_zone=in_kill_zone,
            structure_analysis=ltf_structure,
            fvg_analysis=ltf_fvg,
            ob_analysis=ltf_ob,
            liquidity_analysis=ltf_liquidity
        )
    
    def get_analysis_summary(
        self,
        htf_data: pd.DataFrame,
        ltf_data: pd.DataFrame,
        symbol: str
    ) -> Dict[str, Any]:
        """
        Get a complete analysis summary without generating a setup.
        
        Useful for providing context to Claude for analysis.
        """
        htf_structure = self.structure_analyzer.analyze(htf_data)
        ltf_structure = self.structure_analyzer.analyze(ltf_data)
        ltf_fvg = self.fvg_detector.detect(ltf_data)
        ltf_ob = self.ob_detector.detect(ltf_data)
        ltf_liquidity = self.liquidity_mapper.analyze(ltf_data)
        session = self.kill_zone_checker.get_current_session()
        
        return {
            "symbol": symbol,
            "session": {
                "name": session.session_name,
                "is_kill_zone": session.is_kill_zone,
                "is_tradeable": session.is_tradeable
            },
            "market_structure": {
                "htf_trend": htf_structure.trend.value,
                "ltf_trend": ltf_structure.trend.value,
                "last_structure_break": ltf_structure.last_structure.type.value if ltf_structure.last_structure else None
            },
            "fvg_zones": ltf_fvg.to_dict(),
            "order_blocks": ltf_ob.to_dict(),
            "liquidity": ltf_liquidity.to_dict()
        }
