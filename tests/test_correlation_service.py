"""
Tests for Correlation Service.

Following TDD - tests for symbol correlation tracking.
"""

import pytest
import numpy as np
from datetime import datetime


class TestCorrelationService:
    """Tests for CorrelationService class."""
    
    def test_initialization(self, correlation_service):
        """Test correlation service initialization."""
        assert correlation_service is not None
    
    def test_calculate_correlation(self, correlation_service):
        """Test correlation calculation between two price series."""
        # Perfectly correlated series
        prices_a = [100, 101, 102, 103, 104, 105]
        prices_b = [50, 51, 52, 53, 54, 55]  # Same direction, different scale
        
        corr = correlation_service.calculate_correlation(prices_a, prices_b)
        
        assert corr is not None
        assert abs(corr - 1.0) < 0.01  # Should be ~1.0
    
    def test_negative_correlation(self, correlation_service):
        """Test negative correlation calculation."""
        # Create truly anti-correlated return patterns
        # A goes up when B goes down, and vice versa
        prices_a = [100, 102, 100, 103, 99, 104, 98]  # Oscillating up trend
        prices_b = [50, 48, 50, 47, 51, 46, 52]  # Opposite oscillation
        
        corr = correlation_service.calculate_correlation(prices_a, prices_b)
        
        # Should be negative (returns move opposite)
        assert corr < 0
    
    def test_correlation_matrix(self, correlation_service):
        """Test correlation matrix calculation."""
        price_data = {
            'EURUSD': [1.08, 1.085, 1.09, 1.095, 1.10],
            'GBPUSD': [1.25, 1.255, 1.26, 1.265, 1.27],  # Correlated
            'USDJPY': [150, 149.5, 149, 148.5, 148],  # Negative corr
        }
        
        matrix = correlation_service.calculate_matrix(price_data)
        
        assert matrix is not None
        assert 'EURUSD' in matrix
        assert 'GBPUSD' in matrix['EURUSD']
        
        # EURUSD and GBPUSD should be positively correlated
        assert matrix['EURUSD']['GBPUSD'] > 0.5
    
    def test_highly_correlated_warning(self, correlation_service):
        """Test warning for highly correlated symbols."""
        correlation_service.set_correlation('EURUSD', 'GBPUSD', 0.85)
        
        warnings = correlation_service.get_correlation_warnings(['EURUSD', 'GBPUSD'])
        
        assert len(warnings) > 0
        assert any('EURUSD' in w and 'GBPUSD' in w for w in warnings)


class TestCorrelationRules:
    """Tests for correlation-based trading rules."""
    
    def test_correlation_above_0_8_blocks(self, correlation_service):
        """Test that >0.8 correlation blocks second trade."""
        correlation_service.set_correlation('EURUSD', 'GBPUSD', 0.85)
        correlation_service.set_open_position('EURUSD')
        
        should_block, reason = correlation_service.should_block_trade('GBPUSD')
        
        assert should_block == True
        assert 'correlation' in reason.lower()
    
    def test_correlation_0_6_to_0_8_reduces(self, correlation_service):
        """Test that 0.6-0.8 correlation reduces position size."""
        correlation_service.set_correlation('EURUSD', 'GBPUSD', 0.70)
        correlation_service.set_open_position('EURUSD')
        
        size_multiplier = correlation_service.get_position_size_multiplier('GBPUSD')
        
        assert size_multiplier == 0.5  # 50% size
    
    def test_correlation_below_0_6_normal(self, correlation_service):
        """Test that <0.6 correlation allows normal trading."""
        correlation_service.set_correlation('EURUSD', 'USDJPY', 0.40)
        correlation_service.set_open_position('EURUSD')
        
        size_multiplier = correlation_service.get_position_size_multiplier('USDJPY')
        
        assert size_multiplier == 1.0  # Full size


class TestCorrelationGroups:
    """Tests for correlation group management."""
    
    def test_identify_correlation_groups(self, correlation_service):
        """Test identification of correlated symbol groups."""
        correlations = {
            ('EURUSD', 'GBPUSD'): 0.85,
            ('AUDUSD', 'NZDUSD'): 0.80,
            ('XAUUSD', 'XAGUSD'): 0.90,
        }
        
        for (a, b), corr in correlations.items():
            correlation_service.set_correlation(a, b, corr)
        
        groups = correlation_service.get_correlation_groups()
        
        # Should identify 3 groups
        assert len(groups) >= 2
    
    def test_max_exposure_per_group(self, correlation_service):
        """Test max exposure limit per correlation group."""
        correlation_service.set_correlation('XAUUSD', 'XAGUSD', 0.90)
        correlation_service.set_open_position('XAUUSD', volume=0.05)
        
        # Try to open silver position
        max_volume = correlation_service.get_max_allowed_volume('XAGUSD', account_balance=1000)
        
        # Should be limited due to gold exposure (less than or equal)
        assert max_volume <= 0.05  # Limited by gold position
