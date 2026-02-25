"""
Correlation Service for tracking symbol correlations.

Prevents correlated losses by:
- Calculating rolling correlations
- Warning on highly correlated positions
- Limiting exposure per correlation group
"""

from typing import Dict, List, Tuple, Optional, Any
import numpy as np
from dataclasses import dataclass, field

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class OpenPosition:
    """Represents an open position for correlation tracking."""
    symbol: str
    volume: float
    direction: str  # 'long' or 'short'


class CorrelationService:
    """
    Service for tracking and managing symbol correlations.
    
    Rules:
    - >0.8 correlation: Block second trade
    - 0.6-0.8 correlation: Reduce size by 50%
    - <0.6 correlation: Normal trading
    """
    
    # Known correlations (positive = move together, negative = move opposite)
    # Negative correlations are critical: EURUSD long ≈ USDCHF short (same USD bet)
    DEFAULT_CORRELATIONS = {
        # Positive correlations (same direction = same exposure)
        ('EURUSD', 'GBPUSD'): 0.85,
        ('AUDUSD', 'NZDUSD'): 0.80,
        ('XAUUSD', 'XAGUSD'): 0.90,
        ('USDJPY', 'USDCHF'): 0.70,
        ('EURUSD', 'AUDUSD'): 0.65,
        ('GBPUSD', 'AUDUSD'): 0.60,
        # Inverse correlations (opposite direction = same exposure)
        ('EURUSD', 'USDCHF'): -0.90,
        ('GBPUSD', 'USDCHF'): -0.85,
        ('EURUSD', 'USDCAD'): -0.65,
        ('GBPUSD', 'USDCAD'): -0.60,
        ('AUDUSD', 'USDCAD'): -0.65,
        ('XAUUSD', 'USDJPY'): -0.40,  # Loose gold/USD inverse
    }
    
    def __init__(self, high_threshold: float = 0.8, medium_threshold: float = 0.6):
        """
        Initialize correlation service.
        
        Args:
            high_threshold: Correlation level to block trades (default 0.8)
            medium_threshold: Correlation level to reduce size (default 0.6)
        """
        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold
        
        # Correlation cache: (symbol_a, symbol_b) -> correlation
        self._correlations: Dict[Tuple[str, str], float] = {}
        
        # Load defaults
        for pair, corr in self.DEFAULT_CORRELATIONS.items():
            self._correlations[pair] = corr
            self._correlations[(pair[1], pair[0])] = corr  # Symmetric
        
        # Open positions
        self._open_positions: Dict[str, OpenPosition] = {}
        
        logger.info("Correlation service initialized")
    
    def calculate_correlation(
        self,
        prices_a: List[float],
        prices_b: List[float]
    ) -> float:
        """
        Calculate Pearson correlation between two price series.
        
        Args:
            prices_a: Price series for symbol A
            prices_b: Price series for symbol B
            
        Returns:
            Correlation coefficient (-1 to 1)
        """
        if len(prices_a) != len(prices_b) or len(prices_a) < 2:
            return 0.0
        
        arr_a = np.array(prices_a)
        arr_b = np.array(prices_b)
        
        # Calculate returns
        returns_a = np.diff(arr_a) / arr_a[:-1]
        returns_b = np.diff(arr_b) / arr_b[:-1]
        
        # Pearson correlation
        corr_matrix = np.corrcoef(returns_a, returns_b)
        return float(corr_matrix[0, 1])
    
    def calculate_matrix(
        self,
        price_data: Dict[str, List[float]]
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate correlation matrix for all symbols.
        
        Args:
            price_data: Dict of symbol -> price list
            
        Returns:
            Nested dict of correlations
        """
        symbols = list(price_data.keys())
        matrix = {}
        
        for sym_a in symbols:
            matrix[sym_a] = {}
            for sym_b in symbols:
                if sym_a == sym_b:
                    matrix[sym_a][sym_b] = 1.0
                else:
                    corr = self.calculate_correlation(
                        price_data[sym_a],
                        price_data[sym_b]
                    )
                    matrix[sym_a][sym_b] = corr
                    # Cache it
                    self.set_correlation(sym_a, sym_b, corr)
        
        return matrix
    
    def set_correlation(self, symbol_a: str, symbol_b: str, correlation: float):
        """Set correlation between two symbols."""
        self._correlations[(symbol_a, symbol_b)] = correlation
        self._correlations[(symbol_b, symbol_a)] = correlation  # Symmetric
    
    def get_correlation(self, symbol_a: str, symbol_b: str) -> float:
        """Get correlation between two symbols."""
        return self._correlations.get((symbol_a, symbol_b), 0.0)
    
    def set_open_position(self, symbol: str, volume: float = 0.01, direction: str = 'long'):
        """Record an open position."""
        self._open_positions[symbol] = OpenPosition(
            symbol=symbol,
            volume=volume,
            direction=direction
        )
    
    def remove_position(self, symbol: str):
        """Remove a closed position."""
        if symbol in self._open_positions:
            del self._open_positions[symbol]
    
    def get_correlation_warnings(self, symbols: List[str]) -> List[str]:
        """Get warnings for correlated symbols in a list."""
        warnings = []
        
        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i+1:]:
                corr = self.get_correlation(sym_a, sym_b)
                if corr >= self.high_threshold:
                    warnings.append(
                        f"High correlation ({corr:.2f}) between {sym_a} and {sym_b}"
                    )
                elif corr >= self.medium_threshold:
                    warnings.append(
                        f"Moderate correlation ({corr:.2f}) between {sym_a} and {sym_b}"
                    )
        
        return warnings
    
    def should_block_trade(self, symbol: str, direction: str = 'long') -> Tuple[bool, str]:
        """
        Check if a trade should be blocked due to correlation.
        Direction-aware: only blocks trades that INCREASE net exposure.
        
        Args:
            symbol: Symbol to trade
            direction: Proposed trade direction ('long' or 'short')
            
        Returns:
            Tuple of (should_block, reason)
        """
        for open_symbol, open_pos in self._open_positions.items():
            corr = self.get_correlation(symbol, open_symbol)
            
            if abs(corr) < self.high_threshold:
                continue
            
            # Determine if this trade increases or decreases net exposure
            # Positive correlation + same direction = INCREASES exposure (block)
            # Positive correlation + opposite direction = DECREASES exposure (hedge, allow)
            # Negative correlation + same direction = DECREASES exposure (hedge, allow)
            # Negative correlation + opposite direction = INCREASES exposure (block)
            same_direction = (direction == open_pos.direction)
            increases_exposure = (corr > 0 and same_direction) or (corr < 0 and not same_direction)
            
            if increases_exposure:
                return True, (
                    f"High correlation ({corr:.2f}) with open {open_pos.direction} {open_symbol} — "
                    f"adding {direction} {symbol} would increase exposure"
                )
        
        return False, ""
    
    def get_position_size_multiplier(self, symbol: str) -> float:
        """
        Get position size multiplier based on correlation with open positions.
        
        Returns:
            Multiplier (1.0 = full size, 0.5 = half size, 0 = blocked)
        """
        max_corr = 0.0
        
        for open_symbol in self._open_positions:
            corr = abs(self.get_correlation(symbol, open_symbol))
            max_corr = max(max_corr, corr)
        
        if max_corr >= self.high_threshold:
            return 0.0  # Blocked
        elif max_corr >= self.medium_threshold:
            return 0.5  # Reduced
        else:
            return 1.0  # Normal
    
    def get_correlation_groups(self) -> List[List[str]]:
        """
        Identify groups of highly correlated symbols.
        
        Returns:
            List of symbol groups
        """
        # Build adjacency list for high correlations
        adjacency: Dict[str, List[str]] = {}
        
        for (sym_a, sym_b), corr in self._correlations.items():
            if corr >= self.high_threshold and sym_a != sym_b:
                if sym_a not in adjacency:
                    adjacency[sym_a] = []
                adjacency[sym_a].append(sym_b)
        
        # Find connected components (groups)
        visited = set()
        groups = []
        
        def dfs(symbol: str, group: List[str]):
            if symbol in visited:
                return
            visited.add(symbol)
            group.append(symbol)
            for neighbor in adjacency.get(symbol, []):
                dfs(neighbor, group)
        
        for symbol in adjacency:
            if symbol not in visited:
                group = []
                dfs(symbol, group)
                if len(group) > 1:
                    groups.append(group)
        
        return groups
    
    def get_max_allowed_volume(
        self,
        symbol: str,
        account_balance: float,
        max_group_exposure: float = 0.10  # 10% max per group
    ) -> float:
        """
        Get maximum allowed volume considering correlation exposure.
        
        Args:
            symbol: Symbol to trade
            account_balance: Account balance
            max_group_exposure: Max exposure per correlation group (default 10%)
            
        Returns:
            Maximum allowed volume
        """
        # Find symbol's correlation group
        groups = self.get_correlation_groups()
        symbol_group = None
        
        for group in groups:
            if symbol in group:
                symbol_group = group
                break
        
        if not symbol_group:
            # No correlated symbols - allow normal exposure
            return account_balance * 0.05  # 5% max
        
        # Calculate current group exposure
        current_exposure = 0.0
        for sym in symbol_group:
            if sym in self._open_positions:
                pos = self._open_positions[sym]
                current_exposure += pos.volume * 1000  # Assume $1000 per 0.01 lot
        
        # Max allowed for this trade
        max_group = account_balance * max_group_exposure
        remaining = max_group - current_exposure
        
        return max(0, remaining / 1000)  # Convert back to lots
    
    def update_dynamic_correlations(self, symbol_data: Dict[str, 'pd.DataFrame']) -> None:
        """
        Compute 20-day rolling Pearson correlations from live close prices
        and merge with static defaults (using the HIGHER absolute value).

        Args:
            symbol_data: Dict mapping symbol -> DataFrame with 'close' column
        """
        import pandas as pd

        symbols = list(symbol_data.keys())
        if len(symbols) < 2:
            return

        # Build aligned close-price matrix
        closes: Dict[str, List[float]] = {}
        min_len = min(len(symbol_data[s]) for s in symbols)
        for s in symbols:
            closes[s] = symbol_data[s]['close'].values[-min_len:].astype(float).tolist()

        if min_len < 5:
            return

        updated = 0
        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i + 1:]:
                dynamic_corr = self.calculate_correlation(closes[sym_a], closes[sym_b])

                # Take the HIGHER absolute value between dynamic and static
                static_corr = self.DEFAULT_CORRELATIONS.get(
                    (sym_a, sym_b),
                    self.DEFAULT_CORRELATIONS.get((sym_b, sym_a), 0.0)
                )
                if abs(dynamic_corr) >= abs(static_corr):
                    self.set_correlation(sym_a, sym_b, dynamic_corr)
                    updated += 1

        if updated > 0:
            logger.info(f"Updated {updated} dynamic correlations from {len(symbols)} symbols")

    def get_portfolio_risk_score(self) -> float:
        """
        Combine all open position correlations into a single 0-1 risk number.

        0 = no correlated risk, 1 = maximum correlated risk.
        """
        if len(self._open_positions) < 2:
            return 0.0

        symbols = list(self._open_positions.keys())
        total_pairs = 0
        total_abs_corr = 0.0

        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i + 1:]:
                corr = abs(self.get_correlation(sym_a, sym_b))
                total_abs_corr += corr
                total_pairs += 1

        if total_pairs == 0:
            return 0.0

        return min(1.0, total_abs_corr / total_pairs)

    def get_status(self) -> Dict[str, Any]:
        """Get correlation service status."""
        open_symbols = list(self._open_positions.keys())
        warnings = self.get_correlation_warnings(open_symbols) if open_symbols else []
        
        return {
            'open_positions': len(self._open_positions),
            'symbols': open_symbols,
            'correlation_groups': self.get_correlation_groups(),
            'warnings': warnings,
            'portfolio_risk_score': self.get_portfolio_risk_score(),
        }
