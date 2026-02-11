"""
Tests for Silver Analysis Module.

Following TDD - these tests define expected behavior for silver-specific analysis.
Based on the historic 1979 pattern and current market opportunity.
"""

import pytest
from datetime import datetime
from typing import List
import numpy as np


class TestSilverAnalyzer:
    """Tests for SilverAnalyzer class."""
    
    def test_initialization(self, silver_analyzer):
        """Test silver analyzer initialization."""
        assert silver_analyzer is not None
        assert silver_analyzer.key_levels is not None
    
    def test_key_levels_defined(self, silver_analyzer):
        """Test key levels are properly defined."""
        levels = silver_analyzer.key_levels
        
        # Based on video analysis - accessing dataclass attributes
        assert levels.recent_low == 95.00  # $95
        assert levels.recent_high == 121.00  # $121
        assert levels.target_1 == 150.00  # ~$150
        assert levels.target_2 == 160.00  # ~$160
        assert levels.euphoria == 200.00  # $200
        assert levels.invalidation == 90.00  # <$90
    
    def test_identify_entry_zone(self, silver_analyzer):
        """Test identification of entry zone ($95-105)."""
        # Price in entry zone
        assert silver_analyzer.is_in_entry_zone(95.50) == True
        assert silver_analyzer.is_in_entry_zone(100.00) == True
        assert silver_analyzer.is_in_entry_zone(104.99) == True
        
        # Price outside entry zone
        assert silver_analyzer.is_in_entry_zone(94.00) == False  # Below
        assert silver_analyzer.is_in_entry_zone(110.00) == False  # Above


class TestRSICalculation:
    """Tests for RSI calculation."""
    
    def test_rsi_calculation(self, silver_analyzer):
        """Test RSI is calculated correctly."""
        # Sample price data (14 periods minimum)
        prices = [95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108]
        
        rsi = silver_analyzer.calculate_rsi(prices, period=14)
        
        assert rsi is not None
        assert 0 <= rsi <= 100
    
    def test_rsi_overbought_detection(self, silver_analyzer):
        """Test detection of overbought RSI (>70)."""
        # Strongly uptrending prices
        prices = [100 + i * 2 for i in range(20)]  # 100, 102, 104, ...
        
        rsi = silver_analyzer.calculate_rsi(prices, period=14)
        is_overbought = silver_analyzer.is_rsi_overbought(prices)
        
        assert rsi > 70 or is_overbought == True
    
    def test_rsi_exit_signal(self, silver_analyzer):
        """Test RSI exit signal at 85 on weekly."""
        # Very overbought
        is_exit_signal = silver_analyzer.check_rsi_exit_signal(rsi_value=85)
        assert is_exit_signal == True
        
        # Not yet at exit level
        is_exit_signal = silver_analyzer.check_rsi_exit_signal(rsi_value=75)
        assert is_exit_signal == False


class TestPatternMatching1979:
    """Tests for 1979 pattern matching."""
    
    def test_pattern_comparison(self, silver_analyzer):
        """Test comparison with 1979 silver pattern."""
        # The 1979 pattern:
        # - December: 65% gain
        # - January: 35-40% additional gain
        # - Peak at $50, then crash
        
        current_data = {
            'january_2026_gain': 0.65,  # 65% gain
            'previous_month_close': 30.00,
            'current_price': 95.00
        }
        
        pattern_match = silver_analyzer.match_1979_pattern(current_data)
        
        assert pattern_match is not None
        assert 'similarity_score' in pattern_match
        assert 0 <= pattern_match['similarity_score'] <= 100
    
    def test_pattern_phase_detection(self, silver_analyzer):
        """Test detection of current phase in 1979 pattern."""
        # Phase 1: Initial surge (we're here based on video)
        # Phase 2: Continuation
        # Phase 3: Euphoria
        # Phase 4: Crash
        
        phase = silver_analyzer.detect_pattern_phase(
            monthly_gain=0.65,
            rsi_weekly=65,
            public_sentiment='mixed'
        )
        
        assert phase in ['accumulation', 'surge', 'continuation', 'euphoria', 'crash']
    
    def test_projected_price_targets(self, silver_analyzer):
        """Test price projection based on 1979 pattern."""
        projections = silver_analyzer.get_1979_projections(
            current_price=95.00,
            pattern_similarity=0.80
        )
        
        assert 'target_conservative' in projections
        assert 'target_aggressive' in projections
        assert 'peak_estimate' in projections
        
        # Verify projections are above current price
        assert projections['target_conservative'] > 95.00
        assert projections['target_aggressive'] > projections['target_conservative']
        assert projections['peak_estimate'] > projections['target_aggressive']


class TestVolumeAnalysis:
    """Tests for volume analysis."""
    
    def test_volume_accumulation(self, silver_analyzer):
        """Test detection of accumulation via volume."""
        # High volume on up days, low volume on down days = accumulation
        volume_data = [
            {'close': 95, 'volume': 100000, 'direction': 'up'},
            {'close': 96, 'volume': 120000, 'direction': 'up'},
            {'close': 95.5, 'volume': 50000, 'direction': 'down'},
            {'close': 97, 'volume': 130000, 'direction': 'up'},
        ]
        
        is_accumulation = silver_analyzer.detect_accumulation(volume_data)
        assert is_accumulation == True
    
    def test_volume_distribution(self, silver_analyzer):
        """Test detection of distribution via volume."""
        # High volume on down days = distribution (warning)
        volume_data = [
            {'close': 100, 'volume': 50000, 'direction': 'up'},
            {'close': 99, 'volume': 150000, 'direction': 'down'},
            {'close': 98, 'volume': 180000, 'direction': 'down'},
            {'close': 99, 'volume': 40000, 'direction': 'up'},
        ]
        
        is_distribution = silver_analyzer.detect_distribution(volume_data)
        assert is_distribution == True


class TestPaperPhysicalDisconnect:
    """Tests for paper vs physical price tracking."""
    
    def test_disconnect_calculation(self, silver_analyzer):
        """Test calculation of paper vs physical disconnect."""
        paper_price = 95.00
        physical_premium = 15.00  # 15% premium at dealers
        
        disconnect = silver_analyzer.calculate_disconnect(paper_price, physical_premium)
        
        assert disconnect is not None
        assert 'premium_percent' in disconnect
        assert disconnect['premium_percent'] == pytest.approx(15.0, rel=0.1)
    
    def test_disconnect_warning(self, silver_analyzer):
        """Test warning when disconnect is significant."""
        # If physical is 20%+ above paper, that's a warning sign
        is_warning = silver_analyzer.is_significant_disconnect(premium_percent=20.0)
        assert is_warning == True
        
        # Normal premium
        is_warning = silver_analyzer.is_significant_disconnect(premium_percent=5.0)
        assert is_warning == False


class TestSilverTradingRules:
    """Tests for silver-specific trading rules."""
    
    def test_entry_validation(self, silver_analyzer):
        """Test entry validation based on silver rules."""
        # Valid entry: in zone, RSI not overbought
        signal = {
            'price': 98.00,
            'direction': 'long',
            'rsi': 55,
            'pattern_phase': 'surge'
        }
        
        is_valid, reason = silver_analyzer.validate_silver_entry(signal)
        assert is_valid == True
    
    def test_entry_rejected_above_zone(self, silver_analyzer):
        """Test entry rejected when price above entry zone."""
        signal = {
            'price': 125.00,  # Above entry zone
            'direction': 'long',
            'rsi': 55,
            'pattern_phase': 'surge'
        }
        
        is_valid, reason = silver_analyzer.validate_silver_entry(signal)
        assert is_valid == False
        assert 'entry zone' in reason.lower()
    
    def test_exit_signal_euphoria(self, silver_analyzer):
        """Test exit signal during euphoria phase."""
        market_state = {
            'rsi_weekly': 88,
            'public_sentiment': 'extremely_bullish',
            'pattern_phase': 'euphoria',
            'price': 180.00
        }
        
        should_exit = silver_analyzer.check_exit_conditions(market_state)
        assert should_exit == True
    
    def test_stop_loss_placement(self, silver_analyzer):
        """Test stop loss below $90 invalidation level."""
        entry_price = 98.00
        
        stop_loss = silver_analyzer.calculate_silver_stop_loss(entry_price)
        
        # Stop should be below $90 (invalidation)
        assert stop_loss < 90.00
    
    def test_target_calculation(self, silver_analyzer):
        """Test target calculation based on pattern."""
        entry_price = 98.00
        
        targets = silver_analyzer.calculate_silver_targets(entry_price)
        
        assert 'tp1' in targets  # First target ~$121 (recent high)
        assert 'tp2' in targets  # Second target ~$150
        assert 'tp3' in targets  # Third target ~$160-200
        
        assert targets['tp1'] >= 120
        assert targets['tp2'] >= 145


class TestSilverAnalysisSummary:
    """Tests for comprehensive silver analysis."""
    
    def test_generate_full_analysis(self, silver_analyzer):
        """Test generation of full silver analysis."""
        market_data = {
            'current_price': 98.00,
            'prices': [95 + i * 0.5 for i in range(20)],  # Sample price history
            'volume': [100000 + i * 1000 for i in range(20)],
            'physical_premium': 12.0
        }
        
        analysis = silver_analyzer.analyze(market_data)
        
        assert analysis is not None
        assert 'recommendation' in analysis
        assert 'entry_zone_status' in analysis
        assert 'pattern_match' in analysis
        assert 'risk_assessment' in analysis
        assert 'targets' in analysis
    
    def test_risk_assessment(self, silver_analyzer):
        """Test risk assessment for silver trade."""
        market_state = {
            'price': 98.00,
            'rsi': 65,
            'pattern_phase': 'surge',
            'geopolitical_risk': 'medium'
        }
        
        risk = silver_analyzer.assess_risk(market_state)
        
        assert 'level' in risk
        assert risk['level'] in ['low', 'medium', 'high', 'extreme']
        assert 'factors' in risk
