"""
Volume Profile analysis.

Computes the vertical volume distribution across price levels,
identifying Point of Control (POC), Value Area High (VAH), and
Value Area Low (VAL) from OHLCV data.
"""

from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

from ..utils.logging import get_logger

logger = get_logger(__name__)


def compute_volume_profile(
    df: pd.DataFrame,
    num_bins: int = 50,
    value_area_pct: float = 0.70,
    high_col: str = 'high',
    low_col: str = 'low',
    close_col: str = 'close',
    volume_col: str = 'volume',
) -> Optional[Dict[str, Any]]:
    """
    Compute the volume profile from OHLCV data.

    Each bar's volume is distributed equally across price bins
    that the bar's high-low range overlaps.

    Args:
        df: DataFrame with OHLCV data
        num_bins: Number of price bins
        value_area_pct: Percentage of total volume for Value Area (default 70%)

    Returns:
        Dict with price_levels, volumes, poc, vah, val (or None on failure)
    """
    if df is None or len(df) < 5:
        return None

    try:
        highs = df[high_col].values.astype(float)
        lows = df[low_col].values.astype(float)

        has_volume = volume_col in df.columns and df[volume_col].sum() > 0
        if has_volume:
            volumes = df[volume_col].values.astype(float)
        else:
            volumes = np.ones(len(df))

        price_min = float(np.nanmin(lows))
        price_max = float(np.nanmax(highs))

        if price_max <= price_min:
            return None

        bin_edges = np.linspace(price_min, price_max, num_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        bin_volumes = np.zeros(num_bins)

        for i in range(len(df)):
            bar_low = lows[i]
            bar_high = highs[i]
            bar_vol = volumes[i]

            first_bin = np.searchsorted(bin_edges, bar_low, side='right') - 1
            last_bin = np.searchsorted(bin_edges, bar_high, side='left')

            first_bin = max(0, first_bin)
            last_bin = min(num_bins, last_bin)

            n_bins_touched = last_bin - first_bin
            if n_bins_touched <= 0:
                n_bins_touched = 1
                first_bin = max(0, min(first_bin, num_bins - 1))
                last_bin = first_bin + 1

            vol_per_bin = bar_vol / n_bins_touched
            bin_volumes[first_bin:last_bin] += vol_per_bin

        poc_idx = int(np.argmax(bin_volumes))
        poc = float(bin_centers[poc_idx])

        # Value Area: expand from POC until value_area_pct of total volume
        total_vol = bin_volumes.sum()
        if total_vol == 0:
            return None

        target_vol = total_vol * value_area_pct
        va_low_idx = poc_idx
        va_high_idx = poc_idx
        accumulated = bin_volumes[poc_idx]

        while accumulated < target_vol and (va_low_idx > 0 or va_high_idx < num_bins - 1):
            expand_low = bin_volumes[va_low_idx - 1] if va_low_idx > 0 else 0
            expand_high = bin_volumes[va_high_idx + 1] if va_high_idx < num_bins - 1 else 0

            if expand_low >= expand_high and va_low_idx > 0:
                va_low_idx -= 1
                accumulated += bin_volumes[va_low_idx]
            elif va_high_idx < num_bins - 1:
                va_high_idx += 1
                accumulated += bin_volumes[va_high_idx]
            elif va_low_idx > 0:
                va_low_idx -= 1
                accumulated += bin_volumes[va_low_idx]
            else:
                break

        vah = float(bin_centers[va_high_idx])
        val = float(bin_centers[va_low_idx])

        return {
            'price_levels': bin_centers.tolist(),
            'volumes': bin_volumes.tolist(),
            'poc': poc,
            'vah': vah,
            'val': val,
            'total_volume': float(total_vol),
        }

    except Exception as e:
        logger.warning(f"Volume profile computation failed: {e}")
        return None
