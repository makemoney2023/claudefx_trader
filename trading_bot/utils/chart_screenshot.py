"""
Chart screenshot generator for Claude vision analysis.

Creates candlestick chart images from OHLCV data that can be
sent to Claude Opus 4.5 for visual analysis.
"""

import io
import base64
from typing import Optional, Dict, Any, List
from datetime import datetime

import warnings
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-GUI backend - prevents tkinter threading crashes
import mplfinance as mpf
import matplotlib.pyplot as plt
from PIL import Image

# Suppress noisy mplfinance warnings when plotting flat/single-tick data
warnings.filterwarnings("ignore", message=".*Attempting to set identical.*", category=UserWarning)

from .logging import get_logger

logger = get_logger(__name__)


class ChartScreenshot:
    """
    Generates chart screenshots for LLM vision analysis.
    
    Creates professional candlestick charts with optional overlays
    for order blocks, FVGs, and liquidity levels.
    """
    
    def __init__(
        self,
        style: str = "charles",
        figsize: tuple = (16, 10),
        dpi: int = 100
    ):
        """
        Initialize the chart screenshot generator.
        
        Args:
            style: mplfinance style name
            figsize: Figure size (width, height)
            dpi: Image resolution
        """
        self.style = style
        self.figsize = figsize
        self.dpi = dpi
        
        # Custom style for cleaner charts
        self.custom_style = mpf.make_mpf_style(
            base_mpf_style=style,
            gridstyle='',
            y_on_right=True,
            rc={
                'font.size': 10,
                'axes.labelsize': 10,
                'axes.titlesize': 12,
            }
        )
    
    def generate(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        order_blocks: Optional[List[Dict]] = None,
        fvg_zones: Optional[List[Dict]] = None,
        liquidity_levels: Optional[List[Dict]] = None,
        swing_points: Optional[List[Dict]] = None,
        title: Optional[str] = None
    ) -> bytes:
        """
        Generate a chart screenshot as PNG bytes.
        
        Args:
            df: DataFrame with OHLCV data (must have datetime index)
            symbol: Trading symbol
            timeframe: Chart timeframe
            order_blocks: List of order block zones to highlight
            fvg_zones: List of FVG zones to highlight
            liquidity_levels: List of liquidity levels to mark
            swing_points: List of swing high/low points
            title: Optional custom title
            
        Returns:
            PNG image as bytes
        """
        try:
            # Validate minimum bar count
            min_bars = {
                'M1': 30, 'M5': 20, 'M15': 15, 'H1': 10, 'H4': 8, 'D1': 5
            }
            expected_min = min_bars.get(timeframe.upper(), 10)
            actual_bars = len(df)
            if actual_bars < expected_min:
                logger.warning(
                    f"[CHART-QUALITY] {symbol} {timeframe}: Only {actual_bars} bars "
                    f"(expected >= {expected_min}). Chart may be unreliable for analysis."
                )
            
            # Check for gaps in data (more than 3x expected interval between bars)
            if actual_bars >= 2 and isinstance(df.index, pd.DatetimeIndex):
                deltas = df.index.to_series().diff().dropna()
                if len(deltas) > 0:
                    median_delta = deltas.median()
                    large_gaps = deltas[deltas > median_delta * 3]
                    if len(large_gaps) > 0:
                        logger.warning(
                            f"[CHART-QUALITY] {symbol} {timeframe}: {len(large_gaps)} data gap(s) "
                            f"detected (median interval: {median_delta}, largest gap: {deltas.max()})"
                        )
            
            # Ensure DataFrame has proper index
            df_plot = self._prepare_dataframe(df)
            
            # Build additional plots
            addplots = []
            
            # Add swing point markers
            if swing_points:
                addplots.extend(self._create_swing_markers(df_plot, swing_points))
            
            # Create horizontal lines for key levels
            hlines = self._create_hlines(order_blocks, fvg_zones, liquidity_levels)
            
            # Generate title
            chart_title = title or f"{symbol} - {timeframe} Chart"
            
            # Create the chart - build kwargs dynamically to avoid passing None
            plot_kwargs = {
                'type': 'candle',
                'style': self.custom_style,
                'title': chart_title,
                'ylabel': 'Price',
                'volume': True if 'volume' in df_plot.columns else False,
                'figsize': self.figsize,
                'returnfig': True,
                'tight_layout': True,
            }
            
            # Only add addplot if there are actually plots to add
            if addplots:
                plot_kwargs['addplot'] = addplots
            
            # Only add hlines if there are actually lines to add
            if hlines:
                plot_kwargs['hlines'] = hlines
            
            fig, axes = mpf.plot(df_plot, **plot_kwargs)
            
            # Add zone rectangles
            if order_blocks or fvg_zones:
                self._add_zone_rectangles(axes[0], df_plot, order_blocks, fvg_zones)
            
            # Add annotations
            self._add_annotations(axes[0], df_plot, liquidity_levels)
            
            # Convert to bytes
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight')
            buf.seek(0)
            image_bytes = buf.getvalue()
            
            plt.close(fig)
            
            return image_bytes
            
        except Exception as e:
            logger.error(f"Error generating chart screenshot: {e}")
            raise
    
    def generate_base64(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        **kwargs
    ) -> str:
        """
        Generate a chart screenshot as base64 string.
        
        Args:
            df: DataFrame with OHLCV data
            symbol: Trading symbol
            timeframe: Chart timeframe
            **kwargs: Additional arguments passed to generate()
            
        Returns:
            Base64 encoded PNG image
        """
        image_bytes = self.generate(df, symbol, timeframe, **kwargs)
        return base64.b64encode(image_bytes).decode('utf-8')
    
    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepare DataFrame for mplfinance plotting."""
        df_plot = df.copy()
        
        # Ensure column names are lowercase
        df_plot.columns = df_plot.columns.str.lower()
        
        # Ensure datetime index
        if not isinstance(df_plot.index, pd.DatetimeIndex):
            if 'time' in df_plot.columns:
                df_plot['time'] = pd.to_datetime(df_plot['time'])
                df_plot.set_index('time', inplace=True)
            elif 'datetime' in df_plot.columns:
                df_plot['datetime'] = pd.to_datetime(df_plot['datetime'])
                df_plot.set_index('datetime', inplace=True)
            elif 'date' in df_plot.columns:
                df_plot['date'] = pd.to_datetime(df_plot['date'])
                df_plot.set_index('date', inplace=True)
        
        return df_plot
    
    def _create_swing_markers(
        self,
        df: pd.DataFrame,
        swing_points: List[Dict]
    ) -> List[mpf.make_addplot]:
        """Create scatter plot markers for swing highs/lows."""
        addplots = []
        
        # Initialize marker arrays
        swing_highs = [np.nan] * len(df)
        swing_lows = [np.nan] * len(df)
        
        for point in swing_points:
            idx = point.get('index')
            price = point.get('price')
            point_type = point.get('type', 'high')
            
            if idx is not None and 0 <= idx < len(df):
                if point_type == 'high':
                    swing_highs[idx] = price
                else:
                    swing_lows[idx] = price
        
        # Add swing high markers (triangles pointing down above candle)
        if any(not np.isnan(x) for x in swing_highs):
            addplots.append(mpf.make_addplot(
                swing_highs,
                type='scatter',
                markersize=100,
                marker='v',
                color='red',
            ))
        
        # Add swing low markers (triangles pointing up below candle)
        if any(not np.isnan(x) for x in swing_lows):
            addplots.append(mpf.make_addplot(
                swing_lows,
                type='scatter',
                markersize=100,
                marker='^',
                color='green',
            ))
        
        return addplots
    
    def _create_hlines(
        self,
        order_blocks: Optional[List[Dict]],
        fvg_zones: Optional[List[Dict]],
        liquidity_levels: Optional[List[Dict]]
    ) -> Optional[Dict]:
        """Create horizontal lines configuration."""
        hlines_dict = {'hlines': [], 'colors': [], 'linestyle': [], 'linewidths': []}
        
        # Add liquidity levels
        if liquidity_levels:
            for level in liquidity_levels:
                hlines_dict['hlines'].append(level.get('price', 0))
                hlines_dict['colors'].append(level.get('color', 'purple'))
                hlines_dict['linestyle'].append('--')
                hlines_dict['linewidths'].append(1.5)
        
        if not hlines_dict['hlines']:
            return None
        
        return hlines_dict
    
    def _add_zone_rectangles(
        self,
        ax,
        df: pd.DataFrame,
        order_blocks: Optional[List[Dict]],
        fvg_zones: Optional[List[Dict]]
    ):
        """Add rectangular zones for order blocks and FVGs."""
        import matplotlib.patches as patches
        
        xlim = ax.get_xlim()
        
        # Add order block zones
        if order_blocks:
            for ob in order_blocks:
                top = ob.get('top', 0)
                bottom = ob.get('bottom', 0)
                ob_type = ob.get('type', 'bullish')
                
                color = 'green' if ob_type == 'bullish' else 'red'
                alpha = 0.2
                
                rect = patches.Rectangle(
                    (xlim[0], bottom),
                    xlim[1] - xlim[0],
                    top - bottom,
                    linewidth=1,
                    edgecolor=color,
                    facecolor=color,
                    alpha=alpha
                )
                ax.add_patch(rect)
        
        # Add FVG zones
        if fvg_zones:
            for fvg in fvg_zones:
                top = fvg.get('top', 0)
                bottom = fvg.get('bottom', 0)
                fvg_type = fvg.get('type', 'bullish')
                
                color = 'blue' if fvg_type == 'bullish' else 'orange'
                alpha = 0.15
                
                rect = patches.Rectangle(
                    (xlim[0], bottom),
                    xlim[1] - xlim[0],
                    top - bottom,
                    linewidth=1,
                    edgecolor=color,
                    facecolor=color,
                    alpha=alpha,
                    linestyle='--'
                )
                ax.add_patch(rect)
    
    def _add_annotations(
        self,
        ax,
        df: pd.DataFrame,
        liquidity_levels: Optional[List[Dict]]
    ):
        """Add text annotations for key levels."""
        if liquidity_levels:
            for level in liquidity_levels:
                price = level.get('price', 0)
                label = level.get('label', '')
                
                if label:
                    ax.annotate(
                        label,
                        xy=(ax.get_xlim()[1], price),
                        xytext=(5, 0),
                        textcoords='offset points',
                        fontsize=8,
                        color='purple',
                        va='center'
                    )


def create_simple_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str
) -> str:
    """
    Quick utility to create a simple chart as base64.
    
    Args:
        df: DataFrame with OHLCV data
        symbol: Trading symbol
        timeframe: Chart timeframe
        
    Returns:
        Base64 encoded PNG image
    """
    generator = ChartScreenshot()
    return generator.generate_base64(df, symbol, timeframe)
