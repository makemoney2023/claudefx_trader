"""
Analysis modules for ICT trading strategies.

Provides technical analysis components including:
- Market structure detection (BOS, CHoCH, MSS)
- Fair Value Gap identification
- Order block detection
- Liquidity pool mapping
- Kill zone/session timing
- Fibonacci/OTE analysis
- Power of 3 / AMD detection
- Silver Bullet setups
- Market Maker Model (MMXM)
"""

from .market_structure import MarketStructureAnalyzer, MarketStructure, StructureType
from .fair_value_gap import FVGDetector, FairValueGap, FVGType
from .order_blocks import OrderBlockDetector, OrderBlock, OrderBlockType
from .liquidity import LiquidityMapper, LiquidityPool, LiquidityType
from .kill_zones import KillZoneChecker, TradingSession
from .fibonacci import FibonacciAnalyzer, FibonacciLevels, OTEAnalysis, PriceZone
from .power_of_three import PowerOfThreeAnalyzer, AMDAnalysis, MarketPhase, JudasSwing
from .silver_bullet import SilverBulletDetector, SilverBulletSetup, detect_silver_bullets
from .amd_cycle import AMDCycleAnalyzer, AMDCycleState, AMDPhase, analyze_amd_cycle
from .nwog import NWOGTracker, NWOG, NWOGType, detect_nwog_from_history
from .mtf_analyzer import MTFAnalyzer, MTFAnalysisResult, TimeframeBias
from .silver_analysis import SilverAnalyzer, SilverKeyLevels
from .crypto_analysis import CryptoAnalyzer
from .precious_metals_analysis import PreciousMetalsAnalyzer, GoldKeyLevels, GoldSilverRatio
from .displacement import DisplacementDetector, DisplacementCandle, DisplacementAnalysis, detect_displacement
from .volume_analysis import VolumeAnalyzer, VolumeAnalysis
from .ipda import IPDATracker, IPDALevel, IPDAAnalysis, get_ipda_targets
from .premium_discount import PremiumDiscountAnalyzer, PremiumDiscountAnalysis, PriceZone as PDZone, validate_entry_zone

__all__ = [
    # Market Structure
    "MarketStructureAnalyzer",
    "MarketStructure",
    "StructureType",
    # Fair Value Gaps
    "FVGDetector",
    "FairValueGap",
    "FVGType",
    # Order Blocks
    "OrderBlockDetector",
    "OrderBlock",
    "OrderBlockType",
    # Liquidity
    "LiquidityMapper",
    "LiquidityPool",
    "LiquidityType",
    # Sessions
    "KillZoneChecker",
    "TradingSession",
    # Fibonacci/OTE
    "FibonacciAnalyzer",
    "FibonacciLevels",
    "OTEAnalysis",
    "PriceZone",
    # Power of 3 / AMD
    "PowerOfThreeAnalyzer",
    "AMDAnalysis",
    "MarketPhase",
    "JudasSwing",
    # Silver Bullet
    "SilverBulletDetector",
    "SilverBulletSetup",
    "detect_silver_bullets",
    # AMD Cycle (Enhanced)
    "AMDCycleAnalyzer",
    "AMDCycleState",
    "AMDPhase",
    "analyze_amd_cycle",
    # NWOG (New Week Opening Gap)
    "NWOGTracker",
    "NWOG",
    "NWOGType",
    "detect_nwog_from_history",
    # MTF Analysis
    "MTFAnalyzer",
    "MTFAnalysisResult",
    "TimeframeBias",
    # Silver Analysis
    "SilverAnalyzer",
    "SilverKeyLevels",
    # Crypto Analysis
    "CryptoAnalyzer",
    # Precious Metals
    "PreciousMetalsAnalyzer",
    "GoldKeyLevels",
    "GoldSilverRatio",
    # Displacement Detection (100-pip expansion confirmation)
    "DisplacementDetector",
    "DisplacementCandle",
    "DisplacementAnalysis",
    "detect_displacement",
    # IPDA Levels (Draw on Liquidity Targets)
    "IPDATracker",
    "IPDALevel",
    "IPDAAnalysis",
    "get_ipda_targets",
    # Premium/Discount Zones
    "PremiumDiscountAnalyzer",
    "PremiumDiscountAnalysis",
    "PDZone",
    "validate_entry_zone",
    # Volume Analysis
    "VolumeAnalyzer",
    "VolumeAnalysis",
]
