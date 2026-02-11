"""
Candlestick utility functions for technical analysis.

Provides helper functions for candlestick pattern recognition,
swing point detection, and price action analysis.
"""

from typing import List, Tuple, Optional
import numpy as np
import pandas as pd


def calculate_body_percentage(open_price: float, high: float, low: float, close: float) -> float:
    """
    Calculate the body size as a percentage of the total candle range.
    
    Args:
        open_price: Candle open price
        high: Candle high price
        low: Candle low price
        close: Candle close price
        
    Returns:
        Body percentage (0.0 to 1.0)
    """
    candle_range = high - low
    if candle_range == 0:
        return 0.0
    
    body_size = abs(close - open_price)
    return body_size / candle_range


def is_bullish_candle(open_price: float, close: float) -> bool:
    """
    Check if a candle is bullish (close > open).
    
    Args:
        open_price: Candle open price
        close: Candle close price
        
    Returns:
        True if bullish, False otherwise
    """
    return close > open_price


def is_bearish_candle(open_price: float, close: float) -> bool:
    """
    Check if a candle is bearish (close < open).
    
    Args:
        open_price: Candle open price
        close: Candle close price
        
    Returns:
        True if bearish, False otherwise
    """
    return close < open_price


def get_candle_range(high: float, low: float) -> float:
    """
    Get the total range of a candle.
    
    Args:
        high: Candle high price
        low: Candle low price
        
    Returns:
        Candle range
    """
    return high - low


def get_body_high(open_price: float, close: float) -> float:
    """
    Get the top of the candle body.
    
    Args:
        open_price: Candle open price
        close: Candle close price
        
    Returns:
        Body high price
    """
    return max(open_price, close)


def get_body_low(open_price: float, close: float) -> float:
    """
    Get the bottom of the candle body.
    
    Args:
        open_price: Candle open price
        close: Candle close price
        
    Returns:
        Body low price
    """
    return min(open_price, close)


def find_swing_highs(
    df: pd.DataFrame,
    left_bars: int = 5,
    right_bars: int = 5,
    high_col: str = 'high'
) -> List[Tuple[int, float]]:
    """
    Find swing high points in price data.
    
    A swing high is a price point that is higher than the surrounding bars
    on both the left and right sides.
    
    Args:
        df: DataFrame with OHLCV data
        left_bars: Number of bars to check on the left
        right_bars: Number of bars to check on the right
        high_col: Column name for high prices
        
    Returns:
        List of tuples (index, price) for each swing high
    """
    swing_highs = []
    highs = df[high_col].values
    
    for i in range(left_bars, len(highs) - right_bars):
        is_swing_high = True
        current_high = highs[i]
        
        # Check left side
        for j in range(1, left_bars + 1):
            if highs[i - j] >= current_high:
                is_swing_high = False
                break
        
        # Check right side
        if is_swing_high:
            for j in range(1, right_bars + 1):
                if highs[i + j] >= current_high:
                    is_swing_high = False
                    break
        
        if is_swing_high:
            swing_highs.append((i, current_high))
    
    return swing_highs


def find_swing_lows(
    df: pd.DataFrame,
    left_bars: int = 5,
    right_bars: int = 5,
    low_col: str = 'low'
) -> List[Tuple[int, float]]:
    """
    Find swing low points in price data.
    
    A swing low is a price point that is lower than the surrounding bars
    on both the left and right sides.
    
    Args:
        df: DataFrame with OHLCV data
        left_bars: Number of bars to check on the left
        right_bars: Number of bars to check on the right
        low_col: Column name for low prices
        
    Returns:
        List of tuples (index, price) for each swing low
    """
    swing_lows = []
    lows = df[low_col].values
    
    for i in range(left_bars, len(lows) - right_bars):
        is_swing_low = True
        current_low = lows[i]
        
        # Check left side
        for j in range(1, left_bars + 1):
            if lows[i - j] <= current_low:
                is_swing_low = False
                break
        
        # Check right side
        if is_swing_low:
            for j in range(1, right_bars + 1):
                if lows[i + j] <= current_low:
                    is_swing_low = False
                    break
        
        if is_swing_low:
            swing_lows.append((i, current_low))
    
    return swing_lows


def find_equal_highs(
    swing_highs: List[Tuple[int, float]],
    tolerance_pips: float = 5.0,
    pip_value: float = 0.0001
) -> List[List[Tuple[int, float]]]:
    """
    Find clusters of equal highs (potential liquidity pools).
    
    Args:
        swing_highs: List of swing highs from find_swing_highs
        tolerance_pips: Tolerance in pips for considering highs "equal"
        pip_value: Value of one pip for the symbol
        
    Returns:
        List of clusters, where each cluster is a list of equal highs
    """
    if not swing_highs:
        return []
    
    tolerance = tolerance_pips * pip_value
    clusters = []
    used = set()
    
    for i, (idx1, price1) in enumerate(swing_highs):
        if i in used:
            continue
        
        cluster = [(idx1, price1)]
        used.add(i)
        
        for j, (idx2, price2) in enumerate(swing_highs[i + 1:], start=i + 1):
            if j in used:
                continue
            
            if abs(price1 - price2) <= tolerance:
                cluster.append((idx2, price2))
                used.add(j)
        
        if len(cluster) >= 2:
            clusters.append(cluster)
    
    return clusters


def find_equal_lows(
    swing_lows: List[Tuple[int, float]],
    tolerance_pips: float = 5.0,
    pip_value: float = 0.0001
) -> List[List[Tuple[int, float]]]:
    """
    Find clusters of equal lows (potential liquidity pools).
    
    Args:
        swing_lows: List of swing lows from find_swing_lows
        tolerance_pips: Tolerance in pips for considering lows "equal"
        pip_value: Value of one pip for the symbol
        
    Returns:
        List of clusters, where each cluster is a list of equal lows
    """
    if not swing_lows:
        return []
    
    tolerance = tolerance_pips * pip_value
    clusters = []
    used = set()
    
    for i, (idx1, price1) in enumerate(swing_lows):
        if i in used:
            continue
        
        cluster = [(idx1, price1)]
        used.add(i)
        
        for j, (idx2, price2) in enumerate(swing_lows[i + 1:], start=i + 1):
            if j in used:
                continue
            
            if abs(price1 - price2) <= tolerance:
                cluster.append((idx2, price2))
                used.add(j)
        
        if len(cluster) >= 2:
            clusters.append(cluster)
    
    return clusters


def is_impulsive_move(
    df: pd.DataFrame,
    start_idx: int,
    end_idx: int,
    min_body_percentage: float = 0.6,
    min_consecutive: int = 2
) -> bool:
    """
    Check if a price move is impulsive (strong directional move).
    
    An impulsive move is characterized by consecutive candles with
    large bodies moving in the same direction.
    
    Args:
        df: DataFrame with OHLCV data
        start_idx: Start index of the potential impulse
        end_idx: End index of the potential impulse
        min_body_percentage: Minimum body percentage for impulsive candles
        min_consecutive: Minimum consecutive impulsive candles
        
    Returns:
        True if move is impulsive, False otherwise
    """
    if end_idx - start_idx < min_consecutive:
        return False
    
    consecutive_bullish = 0
    consecutive_bearish = 0
    max_consecutive = 0
    
    for i in range(start_idx, end_idx + 1):
        row = df.iloc[i]
        body_pct = calculate_body_percentage(
            row['open'], row['high'], row['low'], row['close']
        )
        
        if body_pct >= min_body_percentage:
            if is_bullish_candle(row['open'], row['close']):
                consecutive_bullish += 1
                consecutive_bearish = 0
            else:
                consecutive_bearish += 1
                consecutive_bullish = 0
            
            max_consecutive = max(max_consecutive, consecutive_bullish, consecutive_bearish)
        else:
            consecutive_bullish = 0
            consecutive_bearish = 0
    
    return max_consecutive >= min_consecutive


def calculate_atr(
    df: pd.DataFrame,
    period: int = 14,
    high_col: str = 'high',
    low_col: str = 'low',
    close_col: str = 'close'
) -> pd.Series:
    """
    Calculate Average True Range (ATR).
    
    Args:
        df: DataFrame with OHLCV data
        period: ATR period
        high_col: Column name for high prices
        low_col: Column name for low prices
        close_col: Column name for close prices
        
    Returns:
        Series with ATR values
    """
    high = df[high_col]
    low = df[low_col]
    close = df[close_col]
    
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    
    return atr
