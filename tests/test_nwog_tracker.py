"""
Tests for NWOG (New Week Opening Gap) Tracker.

Updated to match current NWOG API:
- NWOG(friday_close=..., sunday_open=..., week_start=...) constructor
- tracker.add_weekend_gap() instead of calculate_and_store_nwog/add_gap
- nwog.ce_level property instead of get_consequent_encroachment()
- tracker.get_nwog_target() instead of get_nearest_nwog_target()
- nwog.filled attribute instead of is_gap_filled()
- tracker.update_fill_status() to check gap fills
- tracker.get_all_levels() for target levels
- high/low/direction are computed properties
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock


class TestNWOGTracker:
    """Tests for New Week Opening Gap tracking."""
    
    def test_initialization(self):
        """Test NWOG tracker initializes correctly."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(max_gaps=4)
        
        assert tracker.max_gaps == 4
        assert len(tracker.gaps) == 0
    
    def test_add_weekend_gap(self):
        """Test adding a weekend gap via add_weekend_gap()."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(max_gaps=4, min_gap_pips=1.0)
        
        result = tracker.add_weekend_gap(
            friday_close=1.0810,
            sunday_open=1.0850,
            week_start=datetime(2026, 2, 2)
        )
        
        assert result is not None
        assert len(tracker.gaps) == 1
        assert result.friday_close == 1.0810
        assert result.sunday_open == 1.0850
    
    def test_calculate_from_data(self):
        """Test NWOG calculation from candle data."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(max_gaps=4, min_gap_pips=1.0)
        
        friday_data = {"close": 1.0810}
        sunday_data = {"open": 1.0850}
        
        result = tracker.calculate_from_data(friday_data, sunday_data)
        
        assert result is not None
        assert len(tracker.gaps) == 1
    
    def test_consequent_encroachment_level(self):
        """Test consequent encroachment calculation (midpoint of gap)."""
        from trading_bot.analysis.nwog import NWOG
        
        # Bullish gap: sunday_open > friday_close
        gap = NWOG(
            friday_close=1.0810,
            sunday_open=1.0850,
            week_start=datetime(2026, 2, 2)
        )
        
        # CE should be midpoint: (1.0810 + 1.0850) / 2 = 1.0830
        assert gap.ce_level == pytest.approx(1.0830, rel=1e-5)
        # high/low are computed properties
        assert gap.high == 1.0850
        assert gap.low == 1.0810
    
    def test_get_nwog_target_long(self):
        """Test finding nearest NWOG target for long trade."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(min_gap_pips=1.0)
        
        # Add bullish gaps above current price
        tracker.add_weekend_gap(
            friday_close=1.0850,
            sunday_open=1.0900,
            week_start=datetime(2026, 1, 26)
        )
        tracker.add_weekend_gap(
            friday_close=1.0950,
            sunday_open=1.1000,
            week_start=datetime(2026, 1, 19)
        )
        
        current_price = 1.0800
        target = tracker.get_nwog_target(current_price, "long")
        
        # Should return the CE level of the nearest unfilled gap above price
        assert target is not None
        assert target > current_price
    
    def test_get_nwog_target_short(self):
        """Test finding nearest NWOG target for short trade."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(min_gap_pips=1.0)
        
        # Add bearish gaps below current price
        tracker.add_weekend_gap(
            friday_close=1.0750,
            sunday_open=1.0700,
            week_start=datetime(2026, 1, 26)
        )
        tracker.add_weekend_gap(
            friday_close=1.0650,
            sunday_open=1.0600,
            week_start=datetime(2026, 1, 19)
        )
        
        current_price = 1.0800
        target = tracker.get_nwog_target(current_price, "short")
        
        # Should return the CE level of the nearest unfilled gap below price
        assert target is not None
        assert target < current_price
    
    def test_max_gaps_limit(self):
        """Test that tracker respects max gaps limit."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(max_gaps=2, min_gap_pips=1.0)
        
        # Add more gaps than limit
        for i in range(5):
            tracker.add_weekend_gap(
                friday_close=1.0800 + i * 0.01,
                sunday_open=1.0850 + i * 0.01,
                week_start=datetime(2026, 1, i + 1)
            )
        
        # Should only keep max_gaps number of gaps
        assert len(tracker.gaps) <= 2
    
    def test_gap_fill_detection(self):
        """Test checking if a gap has been filled."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(min_gap_pips=1.0)
        
        # Bullish gap (sunday_open > friday_close)
        gap = tracker.add_weekend_gap(
            friday_close=1.0800,
            sunday_open=1.0850,
            week_start=datetime(2026, 1, 26)
        )
        assert gap is not None
        
        # Price above gap - not filled
        tracker.update_fill_status(1.0860)
        assert gap.filled == False
        
        # Price inside gap - not filled yet for bullish gap
        tracker.update_fill_status(1.0825)
        assert gap.filled == False
        
        # Price at or below friday_close for bullish gap = filled
        tracker.update_fill_status(1.0790)
        assert gap.filled == True


class TestNWOGAsLiquidityMagnet:
    """Tests for NWOG as liquidity magnet for targets."""
    
    def test_nwog_provides_target_levels(self):
        """Test NWOG gaps provide target levels for trades."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(min_gap_pips=1.0)
        
        tracker.add_weekend_gap(
            friday_close=1.0850,
            sunday_open=1.0900,
            week_start=datetime(2026, 2, 2)
        )
        
        levels = tracker.get_all_levels()
        
        # Should include highs, lows, ce_levels
        assert len(levels["highs"]) > 0
        assert len(levels["lows"]) > 0
        assert len(levels["ce_levels"]) > 0
    
    def test_prioritize_unfilled_gaps(self):
        """Test unfilled gaps are prioritized as targets."""
        from trading_bot.analysis.nwog import NWOGTracker
        
        tracker = NWOGTracker(min_gap_pips=1.0)
        
        # Add gap and mark it as filled
        gap1 = tracker.add_weekend_gap(
            friday_close=1.0800,
            sunday_open=1.0850,
            week_start=datetime(2026, 1, 19)
        )
        gap1.filled = True
        
        # Add unfilled gap
        gap2 = tracker.add_weekend_gap(
            friday_close=1.0900,
            sunday_open=1.0950,
            week_start=datetime(2026, 1, 26)
        )
        
        levels = tracker.get_all_levels()
        
        # unfilled_ce should only have the unfilled gap's CE
        assert len(levels["unfilled_ce"]) == 1
        assert levels["unfilled_ce"][0] == gap2.ce_level


class TestNWOGDataclass:
    """Tests for NWOG dataclass."""
    
    def test_nwog_creation(self):
        """Test NWOG dataclass creation with proper constructor."""
        from trading_bot.analysis.nwog import NWOG, NWOGType
        
        gap = NWOG(
            friday_close=1.0800,
            sunday_open=1.0850,
            week_start=datetime(2026, 2, 2)
        )
        
        # Computed properties
        assert gap.high == 1.0850  # max(friday_close, sunday_open)
        assert gap.low == 1.0800   # min(friday_close, sunday_open)
        assert gap.gap_type == NWOGType.BULLISH  # sunday_open > friday_close
        assert gap.filled == False  # Default
    
    def test_nwog_bearish_direction(self):
        """Test NWOG with bearish gap (sunday_open < friday_close)."""
        from trading_bot.analysis.nwog import NWOG, NWOGType
        
        gap = NWOG(
            friday_close=1.0850,
            sunday_open=1.0800,
            week_start=datetime(2026, 2, 2)
        )
        
        assert gap.gap_type == NWOGType.BEARISH
        assert gap.high == 1.0850
        assert gap.low == 1.0800
    
    def test_nwog_gap_size_calculation(self):
        """Test gap size calculation."""
        from trading_bot.analysis.nwog import NWOG
        
        gap = NWOG(
            friday_close=1.0800,
            sunday_open=1.0850,
            week_start=datetime(2026, 2, 2)
        )
        
        # Gap size: abs(1.0850 - 1.0800) = 0.0050
        assert gap.gap_size == pytest.approx(0.0050, rel=1e-5)
    
    def test_nwog_to_dict(self):
        """Test NWOG serialization."""
        from trading_bot.analysis.nwog import NWOG
        
        gap = NWOG(
            friday_close=1.0800,
            sunday_open=1.0850,
            week_start=datetime(2026, 2, 2)
        )
        
        data = gap.to_dict()
        
        assert data["friday_close"] == 1.0800
        assert data["sunday_open"] == 1.0850
        assert data["high"] == 1.0850
        assert data["low"] == 1.0800
        assert data["gap_type"] == "bullish"
        assert "week_start" in data
        assert "ce_level" in data
    
    def test_nwog_price_in_gap(self):
        """Test checking if price is inside the gap zone."""
        from trading_bot.analysis.nwog import NWOG
        
        gap = NWOG(
            friday_close=1.0800,
            sunday_open=1.0850,
            week_start=datetime(2026, 2, 2)
        )
        
        assert gap.is_price_in_gap(1.0825) == True
        assert gap.is_price_in_gap(1.0750) == False
        assert gap.is_price_in_gap(1.0900) == False


# Fixtures
@pytest.fixture
def tracker():
    """Create NWOG tracker fixture."""
    from trading_bot.analysis.nwog import NWOGTracker
    return NWOGTracker(max_gaps=4, min_gap_pips=1.0)


@pytest.fixture
def sample_gaps():
    """Create sample NWOG gaps."""
    from trading_bot.analysis.nwog import NWOG
    
    return [
        NWOG(
            friday_close=1.0850,
            sunday_open=1.0900,
            week_start=datetime(2026, 2, 2)
        ),
        NWOG(
            friday_close=1.0750,
            sunday_open=1.0700,
            week_start=datetime(2026, 1, 26)
        )
    ]
