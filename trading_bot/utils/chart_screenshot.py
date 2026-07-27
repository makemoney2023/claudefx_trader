"""
Chart screenshot generator for Claude vision analysis.

Creates candlestick chart images from OHLCV data that can be
sent to Claude Opus 5 for visual analysis.
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


    def generate_composite(
        self,
        charts: List[Dict[str, Any]],
        symbol: str,
        trade_markers: Optional[List[Dict]] = None,
        volume_profile: Optional[Dict] = None,
        reactive_levels: Optional[List[Dict]] = None,
        bar_extreme_zones: Optional[List[Dict]] = None,
    ) -> bytes:
        """
        Generate a multi-timeframe composite chart as a single image.

        Args:
            charts: List of dicts with keys 'timeframe', 'df', and optional 'overlays'.
                    Expected order: HTF first (e.g. [D1, H4, H1, M15, M5]).
                    Up to 6 panels (2x2 grid for <=4, 3x2 grid for 5-6).
            symbol: Trading symbol
            trade_markers: Trade history markers for M15 panel
            volume_profile: Volume profile data for M15 panel
            reactive_levels: Historical reactive levels for M15 panel
            bar_extreme_zones: Supply/demand zones from bar extremes.
                Each dict: {"top", "bottom", "type": "supply"|"demand", "tf"}

        Returns:
            PNG image as bytes
        """
        import matplotlib.gridspec as gridspec

        n = min(len(charts), 6)
        if n == 0:
            raise ValueError("At least one chart dict is required")

        if n <= 4:
            grid_rows, grid_cols = 2, 2
            fig_height = 14
        else:
            grid_rows, grid_cols = 3, 2
            fig_height = 21

        # 96 dpi keeps the composite near ~1080p (1920x1344 / 1920x2016) to control
        # Opus 5 high-res image token cost while preserving readable ICT detail.
        fig = plt.figure(figsize=(20, fig_height), dpi=96)
        gs = gridspec.GridSpec(grid_rows, grid_cols, hspace=0.30, wspace=0.20)

        panel_positions = [
            (r, c) for r in range(grid_rows) for c in range(grid_cols)
        ]

        for idx in range(n):
            row, col = panel_positions[idx]
            chart_info = charts[idx]
            tf_label = chart_info.get('timeframe', f'TF{idx}')
            df_raw = chart_info['df']
            overlays = chart_info.get('overlays', {})

            ax = fig.add_subplot(gs[row, col])
            df_plot = self._prepare_dataframe(df_raw)

            is_m15 = tf_label.upper() == 'M15'

            self._draw_candlestick_panel(ax, df_plot, tf_label, symbol, is_m15, overlays)

            if is_m15 and trade_markers:
                self._draw_trade_markers(ax, df_plot, trade_markers)

            if is_m15 and volume_profile:
                self._draw_volume_profile(ax, volume_profile)

            if is_m15 and reactive_levels:
                self._draw_reactive_levels(ax, reactive_levels)

            if bar_extreme_zones:
                panel_zones = [z for z in bar_extreme_zones if z.get('tf', '').upper() == tf_label.upper()]
                self._draw_bar_extreme_zones(ax, panel_zones)

        for idx in range(n, 4):
            row, col = panel_positions[idx]
            ax = fig.add_subplot(gs[row, col])
            ax.set_visible(False)

        fig.suptitle(f"{symbol} Multi-Timeframe Composite", fontsize=14, fontweight='bold')

        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        image_bytes = buf.getvalue()
        plt.close(fig)
        return image_bytes

    def generate_composite_base64(self, charts: List[Dict[str, Any]], symbol: str, **kwargs) -> str:
        """Generate composite chart as base64 string."""
        image_bytes = self.generate_composite(charts, symbol, **kwargs)
        return base64.b64encode(image_bytes).decode('utf-8')

    def _draw_candlestick_panel(
        self, ax, df: pd.DataFrame, timeframe: str, symbol: str,
        apply_overlays: bool = False, overlays: Optional[Dict] = None
    ):
        """Draw a candlestick sub-panel on the given axes."""
        import matplotlib.dates as mdates

        if not isinstance(df.index, pd.DatetimeIndex):
            ax.text(0.5, 0.5, f"{timeframe}\nNo data", transform=ax.transAxes, ha='center', va='center')
            return

        dates = mdates.date2num(df.index.to_pydatetime())
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values

        for i in range(len(df)):
            color = '#26a69a' if closes[i] >= opens[i] else '#ef5350'
            ax.plot([dates[i], dates[i]], [lows[i], highs[i]], color=color, linewidth=0.6)
            body_bottom = min(opens[i], closes[i])
            body_height = abs(closes[i] - opens[i])
            if body_height < (highs[i] - lows[i]) * 0.01:
                body_height = (highs[i] - lows[i]) * 0.01
            ax.bar(dates[i], body_height, bottom=body_bottom, width=0.6 / max(len(df) / 50, 1),
                   color=color, edgecolor=color, linewidth=0.3)

        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
        ax.tick_params(axis='x', rotation=30, labelsize=7)
        ax.tick_params(axis='y', labelsize=8)
        ax.set_title(f"{timeframe}", fontsize=11, fontweight='bold', pad=4)
        ax.grid(True, alpha=0.15)
        ax.yaxis.set_label_position('right')
        ax.yaxis.tick_right()

        if apply_overlays and overlays:
            import matplotlib.patches as patches
            for ob in overlays.get('order_blocks', []):
                top, bottom = ob.get('top', 0), ob.get('bottom', 0)
                color = '#26a69a' if ob.get('type') == 'bullish' else '#ef5350'
                xlim = ax.get_xlim()
                rect = patches.Rectangle(
                    (xlim[0], bottom), xlim[1] - xlim[0], top - bottom,
                    alpha=0.15, facecolor=color, edgecolor=color, linewidth=0.5
                )
                ax.add_patch(rect)
            for fvg in overlays.get('fvg_zones', []):
                top, bottom = fvg.get('top', 0), fvg.get('bottom', 0)
                color = '#2196F3' if fvg.get('type') == 'bullish' else '#FF9800'
                xlim = ax.get_xlim()
                rect = patches.Rectangle(
                    (xlim[0], bottom), xlim[1] - xlim[0], top - bottom,
                    alpha=0.10, facecolor=color, edgecolor=color, linewidth=0.5, linestyle='--'
                )
                ax.add_patch(rect)
            for liq in overlays.get('liquidity_levels', []):
                ax.axhline(y=liq.get('price', 0), color='purple', linestyle='--',
                           linewidth=0.8, alpha=0.6)

    def _draw_trade_markers(self, ax, df: pd.DataFrame, markers: List[Dict]):
        """Draw trade entry markers with P/L labels on the chart."""
        import matplotlib.dates as mdates

        for m in markers:
            try:
                ts = pd.Timestamp(m['time'])
                if ts < df.index[0] or ts > df.index[-1]:
                    continue
                x_pos = mdates.date2num(ts.to_pydatetime())
                price = m['price']
                is_win = m.get('outcome', 'loss') == 'win'
                direction = m.get('direction', 'long')

                marker_char = '^' if direction == 'long' else 'v'
                color = '#26a69a' if is_win else '#ef5350'

                ax.scatter(x_pos, price, marker=marker_char, color=color, s=80, zorder=5, edgecolors='black', linewidths=0.5)

                label = m.get('label', '')
                if label:
                    offset_y = 8 if direction == 'long' else -8
                    ax.annotate(label, xy=(x_pos, price), xytext=(0, offset_y),
                                textcoords='offset points', fontsize=6, color=color,
                                ha='center', va='bottom' if direction == 'long' else 'top',
                                fontweight='bold')
            except Exception:
                continue

    def _draw_volume_profile(self, ax, vp: Dict):
        """Draw vertical volume profile on the right side of the chart."""
        price_levels = vp.get('price_levels', [])
        volumes = vp.get('volumes', [])
        poc = vp.get('poc')
        vah = vp.get('vah')
        val = vp.get('val')

        if not price_levels or not volumes:
            return

        max_vol = max(volumes) if volumes else 1
        xlim = ax.get_xlim()
        chart_width = xlim[1] - xlim[0]
        bar_max_width = chart_width * 0.12

        for price, vol in zip(price_levels, volumes):
            width = (vol / max_vol) * bar_max_width
            bin_height = (max(price_levels) - min(price_levels)) / len(price_levels) if len(price_levels) > 1 else 1
            ax.barh(price, width, height=bin_height * 0.9, left=xlim[1] - bar_max_width,
                    color='#5C6BC0', alpha=0.25, edgecolor='none')

        if poc is not None:
            ax.axhline(y=poc, color='#FF6F00', linestyle='-', linewidth=1.0, alpha=0.8)
            ax.annotate('POC', xy=(xlim[1], poc), fontsize=6, color='#FF6F00',
                        va='center', ha='right', fontweight='bold')
        if vah is not None:
            ax.axhline(y=vah, color='#1565C0', linestyle='--', linewidth=0.7, alpha=0.6)
            ax.annotate('VAH', xy=(xlim[1], vah), fontsize=6, color='#1565C0', va='center', ha='right')
        if val is not None:
            ax.axhline(y=val, color='#1565C0', linestyle='--', linewidth=0.7, alpha=0.6)
            ax.annotate('VAL', xy=(xlim[1], val), fontsize=6, color='#1565C0', va='center', ha='right')

    def _draw_bar_extreme_zones(self, ax, zones: List[Dict]):
        """Draw supply/demand zones from bar extremes as shaded bands."""
        for z in zones:
            top = z.get('top', 0)
            bottom = z.get('bottom', 0)
            z_type = z.get('type', '')
            if z_type == 'supply':
                color = '#ef5350'
                label = 'S'
            elif z_type == 'demand':
                color = '#26a69a'
                label = 'D'
            else:
                continue
            ax.axhspan(bottom, top, color=color, alpha=0.12, linewidth=0)
            ax.axhline(y=top, color=color, linestyle=':', linewidth=0.6, alpha=0.5)
            ax.axhline(y=bottom, color=color, linestyle=':', linewidth=0.6, alpha=0.5)
            mid = (top + bottom) / 2
            xlim = ax.get_xlim()
            ax.annotate(
                f'{label}-Zone', xy=(xlim[0] + (xlim[1] - xlim[0]) * 0.02, mid),
                fontsize=6, color=color, va='center', ha='left', alpha=0.7,
                fontweight='bold',
            )

    def _draw_reactive_levels(self, ax, levels: List[Dict]):
        """Draw historical reactive price levels as semi-transparent bands."""
        for level in levels:
            price = level.get('price', 0)
            count = level.get('reaction_count', 1)
            win_rate = level.get('win_rate', 0.5)

            alpha = min(0.05 + count * 0.04, 0.35)
            if win_rate > 0.6:
                color = '#26a69a'
            elif win_rate < 0.4:
                color = '#ef5350'
            else:
                color = '#9E9E9E'

            band = price * 0.001
            ax.axhspan(price - band, price + band, color=color, alpha=alpha)
            ax.annotate(f'{win_rate:.0%} ({count})', xy=(ax.get_xlim()[1], price),
                        fontsize=5, color=color, va='center', ha='right', alpha=0.7)


def create_simple_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    **kwargs
) -> str:
    """
    Quick utility to create a simple chart as base64.
    
    Args:
        df: DataFrame with OHLCV data
        symbol: Trading symbol
        timeframe: Chart timeframe
        **kwargs: Overlay data (order_blocks, fvg_zones, liquidity_levels, swing_points)
        
    Returns:
        Base64 encoded PNG image
    """
    # Default 96 dpi keeps the 16x10 figure near ~1080p (1536x960). Opus 5 has
    # high-resolution vision that would ingest larger images at up to ~3x the token
    # cost, and this detail level is sufficient for ICT chart reading.
    dpi = kwargs.pop('dpi', 96)
    generator = ChartScreenshot(dpi=dpi)
    return generator.generate_base64(df, symbol, timeframe, **kwargs)


def create_composite_chart(
    charts: List[Dict[str, Any]],
    symbol: str,
    **kwargs
) -> str:
    """
    Create a multi-timeframe composite chart as base64.

    Args:
        charts: List of dicts with 'timeframe', 'df', and optional 'overlays'
        symbol: Trading symbol
        **kwargs: trade_markers, volume_profile, reactive_levels

    Returns:
        Base64 encoded PNG image
    """
    generator = ChartScreenshot()
    return generator.generate_composite_base64(charts, symbol, **kwargs)
