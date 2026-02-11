"""
Volume Analysis Module.

Computes volume-based metrics from OHLCV DataFrames to help confirm
institutional commitment behind price moves.

Key metrics:
- Relative volume (current vs 20-bar average)
- Volume trend (increasing / decreasing / flat)
- Spike detection (bars > 2x average)
- Climax detection (bars > 3x average on reversal candles)
"""

from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VolumeAnalysis:
    """Result of volume analysis on a DataFrame."""
    current_volume: float = 0.0
    avg_volume_20: float = 0.0
    relative_volume: float = 0.0  # current / avg  (>1.5 = high, <0.5 = low)
    volume_trend: str = "flat"     # "increasing" / "decreasing" / "flat"
    spike_bars: List[int] = field(default_factory=list)  # indices where volume > 2x avg
    climax_detected: bool = False  # volume > 3x avg on a reversal candle
    volume_sma_10: float = 0.0    # 10-bar SMA of volume (latest value)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "current_volume": float(self.current_volume),
            "avg_volume_20": float(self.avg_volume_20),
            "relative_volume": round(float(self.relative_volume), 2),
            "volume_trend": self.volume_trend,
            "spike_count": len(self.spike_bars),
            "spike_bars": [int(i) for i in self.spike_bars[-10:]],  # last 10
            "climax_detected": self.climax_detected,
            "is_high_volume": self.relative_volume >= 1.5,
            "is_low_volume": self.relative_volume < 0.5,
        }


class VolumeAnalyzer:
    """
    Analyzes the volume column of an OHLCV DataFrame.

    All methods are pure computation -- no side effects, no external
    dependencies beyond pandas/numpy.
    """

    def __init__(
        self,
        avg_period: int = 20,
        trend_period: int = 10,
        spike_threshold: float = 2.0,
        climax_threshold: float = 3.0,
    ):
        """
        Args:
            avg_period: Period for average volume calculation.
            trend_period: Period for volume trend slope calculation.
            spike_threshold: Multiplier over average to flag as spike.
            climax_threshold: Multiplier over average to flag as climax.
        """
        self.avg_period = avg_period
        self.trend_period = trend_period
        self.spike_threshold = spike_threshold
        self.climax_threshold = climax_threshold

    def analyze(self, df: pd.DataFrame) -> VolumeAnalysis:
        """
        Run full volume analysis on *df*.

        Args:
            df: DataFrame with at least ``open``, ``high``, ``low``,
                ``close``, and ``volume`` columns.

        Returns:
            VolumeAnalysis dataclass with all computed metrics.
        """
        result = VolumeAnalysis()

        if df is None or df.empty or "volume" not in df.columns:
            logger.debug("No volume data available for analysis")
            return result

        vol = df["volume"].astype(float)

        if len(vol) == 0:
            return result

        # --- basic metrics ---------------------------------------------------
        result.current_volume = float(vol.iloc[-1])
        result.avg_volume_20 = float(vol.rolling(self.avg_period).mean().iloc[-1]) if len(vol) >= self.avg_period else float(vol.mean())

        if result.avg_volume_20 > 0:
            result.relative_volume = result.current_volume / result.avg_volume_20
        else:
            result.relative_volume = 0.0

        # --- 10-bar SMA of volume (latest) -----------------------------------
        if len(vol) >= self.trend_period:
            result.volume_sma_10 = float(vol.rolling(self.trend_period).mean().iloc[-1])
        else:
            result.volume_sma_10 = float(vol.mean())

        # --- volume trend (slope of SMA over last *trend_period* bars) --------
        result.volume_trend = self._compute_trend(vol)

        # --- spike detection --------------------------------------------------
        result.spike_bars = self._detect_spikes(vol)

        # --- climax detection -------------------------------------------------
        result.climax_detected = self._detect_climax(df, vol)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_trend(self, vol: pd.Series) -> str:
        """Compute volume trend from the SMA slope over *trend_period* bars."""
        if len(vol) < self.trend_period + 1:
            return "flat"

        sma = vol.rolling(self.trend_period).mean()
        recent_sma = sma.dropna().tail(self.trend_period)

        if len(recent_sma) < 2:
            return "flat"

        # Use linear regression slope
        x = np.arange(len(recent_sma), dtype=float)
        y = recent_sma.values.astype(float)

        if np.std(y) == 0:
            return "flat"

        slope = np.polyfit(x, y, 1)[0]
        avg_val = np.mean(y)

        # Normalise slope relative to average value
        if avg_val > 0:
            normalised = slope / avg_val
        else:
            normalised = 0.0

        if normalised > 0.02:
            return "increasing"
        elif normalised < -0.02:
            return "decreasing"
        return "flat"

    def _detect_spikes(self, vol: pd.Series) -> List[int]:
        """Return integer positions where volume exceeds *spike_threshold* x rolling average."""
        if len(vol) < self.avg_period:
            return []

        rolling_avg = vol.rolling(self.avg_period).mean()
        threshold = rolling_avg * self.spike_threshold

        # Only look at bars where we have a valid rolling average
        mask = (vol > threshold) & rolling_avg.notna()
        spike_positions = list(mask[mask].index)

        # Convert to integer positions if index is not already integer
        if spike_positions and not isinstance(spike_positions[0], (int, np.integer)):
            spike_positions = [vol.index.get_loc(idx) for idx in spike_positions]

        return spike_positions

    def _detect_climax(self, df: pd.DataFrame, vol: pd.Series) -> bool:
        """
        Detect a volume climax on the most recent bar.

        A climax is when volume > *climax_threshold* x average AND the candle
        shows a potential reversal (upper or lower wick is large relative to body).
        """
        if len(vol) < self.avg_period or len(df) < 2:
            return False

        avg = vol.rolling(self.avg_period).mean().iloc[-1]
        if pd.isna(avg) or avg <= 0:
            return False

        latest_vol = float(vol.iloc[-1])
        if latest_vol < avg * self.climax_threshold:
            return False

        # Check for reversal candle characteristics
        latest = df.iloc[-1]
        body = abs(float(latest["close"]) - float(latest["open"]))
        full_range = float(latest["high"]) - float(latest["low"])

        if full_range == 0:
            return False

        # Large wick relative to body indicates reversal / rejection
        wick_ratio = 1.0 - (body / full_range)
        return wick_ratio >= 0.5  # wick is >= 50% of total range

    # ------------------------------------------------------------------
    # Utility: volume at specific indices
    # ------------------------------------------------------------------

    def volume_at_indices(self, df: pd.DataFrame, indices: List[int]) -> List[float]:
        """
        Return the volume values at the given integer positions.

        Useful for checking volume at specific candle locations
        (e.g. OB candles, sweep candles, displacement candles).
        """
        if df is None or df.empty or "volume" not in df.columns:
            return [0.0] * len(indices)

        result = []
        for idx in indices:
            if 0 <= idx < len(df):
                result.append(float(df["volume"].iloc[idx]))
            else:
                result.append(0.0)
        return result
