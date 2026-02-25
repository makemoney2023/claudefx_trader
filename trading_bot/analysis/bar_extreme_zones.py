"""
Bar Extreme Supply/Demand Zone Detector.

Identifies supply and demand zones from the extremes of bar ranges:
- Supply zone: the range (high → low) of the bar with the highest high
- Demand zone: the range (low → high) of the bar with the lowest low

If price reaches the demand zone first → look for longs.
If price reaches the supply zone first → look for shorts.

Works on any timeframe: M5 for scalps, H1 for intraday, D1 for swings.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any

import pandas as pd
import numpy as np

from . import exclude_forming_candle
from ..utils.logging import get_logger

logger = get_logger(__name__)


TIMEFRAME_LOOKBACK = {
    "M1": 30,
    "M5": 30,
    "M15": 40,
    "H1": 50,
    "H4": 40,
    "D1": 40,
}


@dataclass
class BarExtremeZone:
    """A supply or demand zone derived from a bar extreme."""
    zone_type: str          # "supply" or "demand"
    top: float              # upper boundary of the zone
    bottom: float           # lower boundary of the zone
    bar_index: int          # index within the DataFrame
    bar_time: Optional[pd.Timestamp] = None
    timeframe: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_type": self.zone_type,
            "top": round(self.top, 6),
            "bottom": round(self.bottom, 6),
            "bar_index": self.bar_index,
            "bar_time": self.bar_time.isoformat() if self.bar_time else None,
            "timeframe": self.timeframe,
        }


@dataclass
class BarExtremeAnalysis:
    """Result of bar extreme zone detection."""
    supply_zone: Optional[BarExtremeZone] = None
    demand_zone: Optional[BarExtremeZone] = None
    bias: str = "neutral"           # "long", "short", "neutral"
    bias_reason: str = ""
    current_price: float = 0.0
    timeframe: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "supply_zone": self.supply_zone.to_dict() if self.supply_zone else None,
            "demand_zone": self.demand_zone.to_dict() if self.demand_zone else None,
            "bias": self.bias,
            "bias_reason": self.bias_reason,
            "current_price": round(self.current_price, 6),
            "timeframe": self.timeframe,
        }


class BarExtremeZoneDetector:
    """
    Detects supply/demand zones from the most extreme bars in a window.

    Supply zone = range (high, low) of the bar with the highest high.
    Demand zone = range (high, low) of the bar with the lowest low.

    Directional bias is determined by which zone price reaches first
    after both extremes have formed.
    """

    def __init__(self, lookback: Optional[int] = None):
        self._lookback = lookback

    def detect(
        self,
        df: pd.DataFrame,
        current_price: Optional[float] = None,
        timeframe: str = "",
    ) -> BarExtremeAnalysis:
        """
        Detect bar extreme supply/demand zones.

        Args:
            df: OHLCV DataFrame (must have 'open', 'high', 'low', 'close')
            current_price: live price for bias determination
            timeframe: label (M5, H1, D1, etc.) used for lookback default

        Returns:
            BarExtremeAnalysis with supply zone, demand zone, and bias
        """
        df = exclude_forming_candle(df)
        if df is None or len(df) < 10:
            return BarExtremeAnalysis(timeframe=timeframe)

        lookback = self._lookback or TIMEFRAME_LOOKBACK.get(timeframe.upper(), 50)
        window = df.tail(lookback)

        if current_price is None:
            current_price = float(window["close"].iloc[-1])

        # Highest bar → supply zone
        highest_idx = int(window["high"].idxmax()) if hasattr(window["high"].idxmax(), '__int__') else window["high"].idxmax()
        highest_bar = window.loc[highest_idx]
        supply = BarExtremeZone(
            zone_type="supply",
            top=float(highest_bar["high"]),
            bottom=float(highest_bar["low"]),
            bar_index=int(window.index.get_loc(highest_idx)) if highest_idx in window.index else 0,
            bar_time=highest_idx if isinstance(highest_idx, pd.Timestamp) else None,
            timeframe=timeframe,
        )

        # Lowest bar → demand zone
        lowest_idx = int(window["low"].idxmin()) if hasattr(window["low"].idxmin(), '__int__') else window["low"].idxmin()
        lowest_bar = window.loc[lowest_idx]
        demand = BarExtremeZone(
            zone_type="demand",
            top=float(lowest_bar["high"]),
            bottom=float(lowest_bar["low"]),
            bar_index=int(window.index.get_loc(lowest_idx)) if lowest_idx in window.index else 0,
            bar_time=lowest_idx if isinstance(lowest_idx, pd.Timestamp) else None,
            timeframe=timeframe,
        )

        # Determine bias: which zone did price reach first after both extremes formed?
        bias, reason = self._determine_bias(window, supply, demand, current_price)

        return BarExtremeAnalysis(
            supply_zone=supply,
            demand_zone=demand,
            bias=bias,
            bias_reason=reason,
            current_price=current_price,
            timeframe=timeframe,
        )

    @staticmethod
    def _determine_bias(
        window: pd.DataFrame,
        supply: BarExtremeZone,
        demand: BarExtremeZone,
        current_price: float,
    ) -> tuple:
        """
        Determine directional bias by checking which zone price tested first
        after both extreme bars have formed.
        """
        supply_loc = supply.bar_index
        demand_loc = demand.bar_index
        start_after = max(supply_loc, demand_loc) + 1

        if start_after >= len(window):
            # Both extremes are recent — use proximity
            dist_supply = abs(current_price - supply.bottom)
            dist_demand = abs(current_price - demand.top)
            if dist_demand < dist_supply:
                return "long", "price closer to demand zone"
            elif dist_supply < dist_demand:
                return "short", "price closer to supply zone"
            return "neutral", "price equidistant from zones"

        # Scan bars after both extremes formed
        post = window.iloc[start_after:]
        for i in range(len(post)):
            bar_high = float(post["high"].iloc[i])
            bar_low = float(post["low"].iloc[i])
            touched_demand = bar_low <= demand.top
            touched_supply = bar_high >= supply.bottom
            if touched_demand and not touched_supply:
                return "long", "price hit demand zone first"
            if touched_supply and not touched_demand:
                return "short", "price hit supply zone first"
            if touched_demand and touched_supply:
                break

        # Fall back to current price proximity
        dist_supply = abs(current_price - supply.bottom)
        dist_demand = abs(current_price - demand.top)
        if dist_demand < dist_supply:
            return "long", "price closer to demand zone"
        elif dist_supply < dist_demand:
            return "short", "price closer to supply zone"
        return "neutral", "price equidistant from zones"
