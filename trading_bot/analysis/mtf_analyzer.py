"""
Multi-Timeframe (MTF) Analyzer.

Provides higher timeframe bias confirmation before lower timeframe entry.
Based on ICT methodology: Daily determines bias, H4 structure, H1 confirms, M15/M5 entry.

Key principle: Only trade in the direction of the higher timeframe bias.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import pandas as pd

from .market_structure import MarketStructureAnalyzer, StructureType, TrendDirection
from .fair_value_gap import FVGDetector, FVGType
from .order_blocks import OrderBlockDetector
from ..utils.logging import get_logger

logger = get_logger(__name__)


class TimeframeBias(Enum):
    """Bias direction from timeframe analysis."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass
class TimeframeAnalysis:
    """Analysis result for a single timeframe."""
    timeframe: str
    bias: TimeframeBias
    trend: str  # bullish, bearish, ranging
    structure: Optional[str] = None  # BOS, CHoCH, etc.
    key_levels: Dict[str, float] = field(default_factory=dict)
    fvg_count: int = 0
    ob_count: int = 0
    confidence: float = 0.5


@dataclass
class MTFAnalysisResult:
    """Result of multi-timeframe analysis."""
    overall_bias: TimeframeBias
    alignment: bool  # True if all timeframes agree
    daily_analysis: Optional[TimeframeAnalysis] = None
    h4_analysis: Optional[TimeframeAnalysis] = None
    h1_analysis: Optional[TimeframeAnalysis] = None
    m15_analysis: Optional[TimeframeAnalysis] = None
    m5_analysis: Optional[TimeframeAnalysis] = None
    m1_analysis: Optional[TimeframeAnalysis] = None
    
    # Entry conditions: 'preferred', 'counter_trend', or 'no_data'
    can_trade_long: str = 'no_data'
    can_trade_short: str = 'no_data'
    htf_key_levels: List[float] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_bias": self.overall_bias.value,
            "alignment": self.alignment,
            "can_trade_long": self.can_trade_long,
            "can_trade_short": self.can_trade_short,
            "daily": self.daily_analysis.__dict__ if self.daily_analysis else None,
            "h4": self.h4_analysis.__dict__ if self.h4_analysis else None,
            "h1": self.h1_analysis.__dict__ if self.h1_analysis else None,
            "m15": self.m15_analysis.__dict__ if self.m15_analysis else None,
            "m5": self.m5_analysis.__dict__ if self.m5_analysis else None,
            "m1": self.m1_analysis.__dict__ if self.m1_analysis else None,
            "htf_key_levels": self.htf_key_levels
        }


class MTFAnalyzer:
    """
    Multi-Timeframe Analyzer for ICT trading.
    
    Analysis flow:
    1. Daily (D1) - Determine overall bias
    2. 4-Hour (H4) - Confirm structure, identify OB/FVG
    3. 1-Hour (H1) - Confirm bias and structure
    4. 15-Minute (M15) - Entry timeframe
    5. 5-Minute (M5) - Precision entry/exit timing
    6. 1-Minute (M1) - Micro-structure, sniper entries
    
    Rule: Only trade in direction of HTF bias with alignment.
    """
    
    TIMEFRAMES = ['D1', 'H4', 'H1', 'M15', 'M5', 'M1']
    
    def __init__(self, mt5_client=None):
        """
        Initialize MTF Analyzer.
        
        Args:
            mt5_client: MT5 client for fetching data
        """
        self.mt5_client = mt5_client
        
        # Initialize sub-analyzers
        self.structure_analyzer = MarketStructureAnalyzer()
        self.fvg_detector = FVGDetector()
        self.ob_detector = OrderBlockDetector()
        
        logger.info("MTFAnalyzer initialized")
    
    def set_mt5_client(self, client):
        """Set the MT5 client."""
        self.mt5_client = client
    
    async def analyze(
        self,
        symbol: str,
        d1_data: Optional[pd.DataFrame] = None,
        h4_data: Optional[pd.DataFrame] = None,
        h1_data: Optional[pd.DataFrame] = None,
        m15_data: Optional[pd.DataFrame] = None,
        m5_data: Optional[pd.DataFrame] = None,
        m1_data: Optional[pd.DataFrame] = None
    ) -> MTFAnalysisResult:
        """
        Perform multi-timeframe analysis.
        
        Args:
            symbol: Trading symbol
            d1_data: Daily OHLCV data (optional - will fetch if mt5_client available)
            h4_data: H4 OHLCV data
            h1_data: H1 OHLCV data
            m15_data: M15 OHLCV data
            m5_data: M5 OHLCV data (precision entry/exit)
            m1_data: M1 OHLCV data (micro-structure, sniper entries)
            
        Returns:
            MTFAnalysisResult with bias and alignment info
        """
        # Fetch data if not provided and mt5_client available
        if self.mt5_client:
            if d1_data is None:
                d1_data = await self._fetch_data(symbol, 'D1', 50)
            if h4_data is None:
                h4_data = await self._fetch_data(symbol, 'H4', 100)
            if h1_data is None:
                h1_data = await self._fetch_data(symbol, 'H1', 100)
            if m15_data is None:
                m15_data = await self._fetch_data(symbol, 'M15', 100)
            if m5_data is None:
                m5_data = await self._fetch_data(symbol, 'M5', 100)
            if m1_data is None:
                m1_data = await self._fetch_data(symbol, 'M1', 60)
        
        # Analyze each timeframe
        daily_analysis = self._analyze_timeframe(d1_data, 'D1') if d1_data is not None else None
        h4_analysis = self._analyze_timeframe(h4_data, 'H4') if h4_data is not None else None
        h1_analysis = self._analyze_timeframe(h1_data, 'H1') if h1_data is not None else None
        m15_analysis = self._analyze_timeframe(m15_data, 'M15') if m15_data is not None else None
        m5_analysis = self._analyze_timeframe(m5_data, 'M5') if m5_data is not None else None
        m1_analysis = self._analyze_timeframe(m1_data, 'M1') if m1_data is not None else None
        
        # Determine overall bias
        overall_bias = self._determine_overall_bias(
            daily_analysis, h4_analysis, h1_analysis
        )
        
        # Check alignment
        alignment = self._check_alignment(
            daily_analysis, h4_analysis, h1_analysis
        )
        
        # Determine tradeable directions: preferred / counter_trend / no_data
        if overall_bias == TimeframeBias.UNKNOWN:
            can_trade_long = 'no_data'
            can_trade_short = 'no_data'
        elif overall_bias == TimeframeBias.BULLISH:
            can_trade_long = 'preferred'
            can_trade_short = 'counter_trend'
        elif overall_bias == TimeframeBias.BEARISH:
            can_trade_long = 'counter_trend'
            can_trade_short = 'preferred'
        else:  # NEUTRAL
            can_trade_long = 'counter_trend'
            can_trade_short = 'counter_trend'
        
        # Collect HTF key levels
        htf_levels = self._collect_htf_levels(
            daily_analysis, h4_analysis, h1_analysis
        )
        
        return MTFAnalysisResult(
            overall_bias=overall_bias,
            alignment=alignment,
            daily_analysis=daily_analysis,
            h4_analysis=h4_analysis,
            h1_analysis=h1_analysis,
            m15_analysis=m15_analysis,
            m5_analysis=m5_analysis,
            m1_analysis=m1_analysis,
            can_trade_long=can_trade_long,
            can_trade_short=can_trade_short,
            htf_key_levels=htf_levels
        )
    
    async def get_htf_bias(self, symbol: str) -> Dict[str, Any]:
        """
        Quick method to get HTF bias without full analysis.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with daily and H4 bias info
        """
        result = await self.analyze(symbol)
        
        return {
            'daily_trend': result.daily_analysis.bias.value if result.daily_analysis else 'unknown',
            'h4_trend': result.h4_analysis.bias.value if result.h4_analysis else 'unknown',
            'alignment': result.alignment,
            'overall_bias': result.overall_bias.value,
            'can_trade_long': result.can_trade_long,
            'can_trade_short': result.can_trade_short
        }
    
    def should_trade_direction(
        self,
        signal_direction: str,
        mtf_result: MTFAnalysisResult
    ) -> bool:
        """
        Check if a trade direction aligns with HTF bias.
        
        Args:
            signal_direction: 'long' or 'short'
            mtf_result: MTFAnalysisResult from analyze()
            
        Returns:
            True if direction is preferred or counter_trend (both are tradeable).
            Only returns False if no_data.
        """
        if signal_direction == 'long':
            return mtf_result.can_trade_long in ('preferred', 'counter_trend')
        elif signal_direction == 'short':
            return mtf_result.can_trade_short in ('preferred', 'counter_trend')
        return False
    
    def _analyze_timeframe(
        self,
        df: pd.DataFrame,
        timeframe: str
    ) -> TimeframeAnalysis:
        """Analyze a single timeframe."""
        if df is None or df.empty:
            return TimeframeAnalysis(
                timeframe=timeframe,
                bias=TimeframeBias.UNKNOWN,
                trend='unknown'
            )
        
        try:
            # Get market structure
            structure = self.structure_analyzer.analyze(df)
            
            # Determine bias from structure
            if structure and hasattr(structure, 'trend'):
                if structure.trend == TrendDirection.BULLISH:
                    bias = TimeframeBias.BULLISH
                    trend = 'bullish'
                elif structure.trend == TrendDirection.BEARISH:
                    bias = TimeframeBias.BEARISH
                    trend = 'bearish'
                else:
                    bias = TimeframeBias.NEUTRAL
                    trend = 'ranging'
            else:
                # Fallback: use simple price analysis
                bias, trend = self._simple_trend_analysis(df)
            
            # Count FVGs and OBs
            fvgs = self.fvg_detector.detect(df)
            obs = self.ob_detector.detect(df)
            
            # Get key levels
            key_levels = self._extract_key_levels(df, structure)
            
            # Calculate confidence
            confidence = self._calculate_confidence(structure, fvgs, obs)
            
            return TimeframeAnalysis(
                timeframe=timeframe,
                bias=bias,
                trend=trend,
                structure=structure.last_structure.type.value if structure and structure.last_structure else None,
                key_levels=key_levels,
                fvg_count=fvgs.total_fvgs if fvgs else 0,
                ob_count=obs.total_obs if obs else 0,
                confidence=confidence
            )
            
        except Exception as e:
            logger.error(f"Error analyzing {timeframe}: {e}")
            return TimeframeAnalysis(
                timeframe=timeframe,
                bias=TimeframeBias.UNKNOWN,
                trend='unknown'
            )
    
    def _simple_trend_analysis(self, df: pd.DataFrame) -> tuple:
        """Simple trend analysis based on price action."""
        if len(df) < 20:
            return TimeframeBias.UNKNOWN, 'unknown'
        
        # Use 20-period moving average
        close = df['close']
        ma20 = close.rolling(20).mean()
        
        current_price = close.iloc[-1]
        current_ma = ma20.iloc[-1]
        
        # Also check if price is making higher highs/lows
        recent_highs = df['high'].tail(10)
        recent_lows = df['low'].tail(10)
        
        hh = recent_highs.iloc[-1] > recent_highs.iloc[0]  # Higher high
        hl = recent_lows.iloc[-1] > recent_lows.iloc[0]   # Higher low
        lh = recent_highs.iloc[-1] < recent_highs.iloc[0]  # Lower high
        ll = recent_lows.iloc[-1] < recent_lows.iloc[0]   # Lower low
        
        if current_price > current_ma and (hh and hl):
            return TimeframeBias.BULLISH, 'bullish'
        elif current_price < current_ma and (lh and ll):
            return TimeframeBias.BEARISH, 'bearish'
        else:
            return TimeframeBias.NEUTRAL, 'ranging'
    
    def _determine_overall_bias(
        self,
        daily: Optional[TimeframeAnalysis],
        h4: Optional[TimeframeAnalysis],
        h1: Optional[TimeframeAnalysis]
    ) -> TimeframeBias:
        """Determine overall bias from timeframe analyses."""
        # Priority: Daily > H4 > H1
        if daily and daily.bias != TimeframeBias.UNKNOWN:
            # Daily determines primary bias
            return daily.bias
        elif h4 and h4.bias != TimeframeBias.UNKNOWN:
            return h4.bias
        elif h1 and h1.bias != TimeframeBias.UNKNOWN:
            return h1.bias
        
        return TimeframeBias.UNKNOWN
    
    def _check_alignment(
        self,
        daily: Optional[TimeframeAnalysis],
        h4: Optional[TimeframeAnalysis],
        h1: Optional[TimeframeAnalysis]
    ) -> bool:
        """Check if timeframes are aligned."""
        biases = []
        
        if daily and daily.bias not in [TimeframeBias.UNKNOWN, TimeframeBias.NEUTRAL]:
            biases.append(daily.bias)
        if h4 and h4.bias not in [TimeframeBias.UNKNOWN, TimeframeBias.NEUTRAL]:
            biases.append(h4.bias)
        if h1 and h1.bias not in [TimeframeBias.UNKNOWN, TimeframeBias.NEUTRAL]:
            biases.append(h1.bias)
        
        if len(biases) < 2:
            return False  # Not enough data
        
        # Check if all biases are the same
        return len(set(biases)) == 1
    
    def _collect_htf_levels(
        self,
        daily: Optional[TimeframeAnalysis],
        h4: Optional[TimeframeAnalysis],
        h1: Optional[TimeframeAnalysis]
    ) -> List[float]:
        """Collect key levels from higher timeframes."""
        levels = []
        
        for analysis in [daily, h4, h1]:
            if analysis and analysis.key_levels:
                for level in analysis.key_levels.values():
                    if level is not None:
                        levels.append(level)
        
        return sorted(set(levels))
    
    def _extract_key_levels(
        self,
        df: pd.DataFrame,
        structure
    ) -> Dict[str, float]:
        """Extract key price levels from analysis."""
        levels = {}
        
        if df is not None and not df.empty:
            levels['current_high'] = float(df['high'].iloc[-1])
            levels['current_low'] = float(df['low'].iloc[-1])
            levels['swing_high'] = float(df['high'].tail(20).max())
            levels['swing_low'] = float(df['low'].tail(20).min())
        
        return levels
    
    def _calculate_confidence(
        self,
        structure,
        fvgs,
        obs
    ) -> float:
        """Calculate confidence in the analysis."""
        confidence = 0.5
        
        # Boost confidence for clear structure
        if structure and hasattr(structure, 'trend'):
            if structure.trend in [TrendDirection.BULLISH, TrendDirection.BEARISH]:
                confidence += 0.2
        
        # Boost for FVGs present
        fvg_count = fvgs.total_fvgs if hasattr(fvgs, 'total_fvgs') else (len(fvgs) if fvgs else 0)
        if fvg_count > 0:
            confidence += min(0.15, fvg_count * 0.05)
        
        # Boost for OBs present
        ob_count = obs.total_obs if hasattr(obs, 'total_obs') else (len(obs) if obs else 0)
        if ob_count > 0:
            confidence += min(0.15, ob_count * 0.05)
        
        return min(1.0, confidence)
    
    async def _fetch_data(
        self,
        symbol: str,
        timeframe: str,
        count: int
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data from MT5 and convert to DataFrame."""
        if not self.mt5_client:
            return None
        
        try:
            raw_data = await self.mt5_client.get_ohlcv_data(symbol, timeframe, count)
            if raw_data is None:
                return None
            # Convert list of dicts to DataFrame if needed
            if isinstance(raw_data, list):
                if not raw_data:
                    return None
                df = pd.DataFrame(raw_data)
                # Ensure standard column names
                col_map = {'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume', 'Time': 'time'}
                df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)
                return df
            # Already a DataFrame
            return raw_data
        except Exception as e:
            logger.error(f"Error fetching {timeframe} data for {symbol}: {e}")
            return None
    
    def get_context_for_claude(self, result: MTFAnalysisResult) -> str:
        """
        Build MTF context string for Claude's analysis.
        
        Args:
            result: MTFAnalysisResult from analyze()
            
        Returns:
            Context string for Claude
        """
        lines = ["## Multi-Timeframe Analysis"]
        
        lines.append(f"Overall Bias: **{result.overall_bias.value.upper()}**")
        lines.append(f"Timeframe Alignment: {'YES' if result.alignment else 'NO'}")
        
        if result.daily_analysis:
            lines.append(f"- Daily: {result.daily_analysis.trend} (conf: {result.daily_analysis.confidence:.0%})")
        if result.h4_analysis:
            lines.append(f"- H4: {result.h4_analysis.trend} (conf: {result.h4_analysis.confidence:.0%})")
        if result.h1_analysis:
            lines.append(f"- H1: {result.h1_analysis.trend} (conf: {result.h1_analysis.confidence:.0%})")
        if result.m15_analysis:
            lines.append(f"- M15: {result.m15_analysis.trend} (conf: {result.m15_analysis.confidence:.0%})")
        if result.m5_analysis:
            lines.append(f"- M5: {result.m5_analysis.trend} (conf: {result.m5_analysis.confidence:.0%})")
        if result.m1_analysis:
            lines.append(f"- M1: {result.m1_analysis.trend} (conf: {result.m1_analysis.confidence:.0%})")
        
        lines.append("")
        lines.append(f"Can Trade Long: {result.can_trade_long.upper()}")
        lines.append(f"Can Trade Short: {result.can_trade_short.upper()}")
        
        if result.htf_key_levels:
            lines.append(f"HTF Key Levels: {', '.join(f'{l:.5f}' for l in result.htf_key_levels[:5])}")
        
        return "\n".join(lines)
