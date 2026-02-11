"""
Tests for News Service.

Following TDD - these tests define the expected behavior for news/event handling.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock


class TestNewsService:
    """Tests for NewsService class."""
    
    def test_initialization(self, news_service):
        """Test news service initialization."""
        assert news_service is not None
        assert news_service.blackout_minutes_before == 120
        assert news_service.blackout_minutes_after == 60
    
    @pytest.mark.asyncio
    async def test_fetch_economic_calendar(self, news_service):
        """Test fetching economic calendar events."""
        events = await news_service.fetch_economic_calendar()
        
        assert isinstance(events, list)
        # Events should have required fields
        if events:
            event = events[0]
            assert 'title' in event
            assert 'datetime' in event
            assert 'impact' in event
            assert 'currency' in event
    
    @pytest.mark.asyncio
    async def test_get_high_impact_events(self, news_service):
        """Test filtering for high impact events only."""
        events = await news_service.get_high_impact_events()
        
        assert isinstance(events, list)
        for event in events:
            assert event['impact'] in ['high', 'red', 3]  # Different APIs use different values
    
    @pytest.mark.asyncio
    async def test_get_upcoming_events(self, news_service):
        """Test getting upcoming events within timeframe."""
        # Get events for next 24 hours
        events = await news_service.get_upcoming_events(hours=24)
        
        assert isinstance(events, list)
        now = datetime.now()
        future = now + timedelta(hours=24)
        
        for event in events:
            event_time = datetime.fromisoformat(event['datetime'])
            assert now <= event_time <= future
    
    def test_is_blackout_period_during_event(self, news_service):
        """Test blackout period detection during event."""
        # Create an event happening now
        event_time = datetime.now()
        events = [{
            'title': 'NFP Release',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        is_blackout, reason = news_service.is_blackout_period()
        assert is_blackout == True
        assert 'NFP' in reason or 'event' in reason.lower()
    
    def test_is_blackout_period_before_event(self, news_service):
        """Test blackout 30 minutes BEFORE high impact event."""
        # Event in 15 minutes
        event_time = datetime.now() + timedelta(minutes=15)
        events = [{
            'title': 'FOMC Decision',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        is_blackout, reason = news_service.is_blackout_period()
        assert is_blackout == True
        assert 'FOMC' in reason or 'before' in reason.lower()
    
    def test_is_blackout_period_after_event(self, news_service):
        """Test blackout 30 minutes AFTER high impact event."""
        # Event was 15 minutes ago
        event_time = datetime.now() - timedelta(minutes=15)
        events = [{
            'title': 'CPI Release',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        is_blackout, reason = news_service.is_blackout_period()
        assert is_blackout == True
    
    def test_is_not_blackout_when_no_events(self, news_service):
        """Test no blackout when no events nearby."""
        # No events or events far away
        event_time = datetime.now() + timedelta(hours=5)
        events = [{
            'title': 'Future Event',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        is_blackout, reason = news_service.is_blackout_period()
        assert is_blackout == False
    
    def test_low_impact_events_no_blackout(self, news_service):
        """Test that low impact events don't trigger blackout."""
        event_time = datetime.now()
        events = [{
            'title': 'Minor Report',
            'datetime': event_time.isoformat(),
            'impact': 'low',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        is_blackout, reason = news_service.is_blackout_period()
        assert is_blackout == False
    
    def test_get_countdown_to_next_event(self, news_service):
        """Test countdown timer to next high impact event."""
        event_time = datetime.now() + timedelta(hours=2, minutes=30)
        events = [{
            'title': 'ECB Meeting',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'EUR'
        }]
        
        news_service.set_events(events)
        
        countdown = news_service.get_countdown_to_next_event()
        
        assert countdown is not None
        assert 'event' in countdown
        assert 'time_until' in countdown
        assert 'minutes' in countdown['time_until'] or 'hours' in countdown['time_until']


class TestNewsIntegration:
    """Tests for news service integration with trading bot."""
    
    @pytest.mark.asyncio
    async def test_bot_skips_during_blackout(self, news_service):
        """Test that trading is skipped during blackout periods."""
        # Create high impact event now
        event_time = datetime.now()
        events = [{
            'title': 'NFP',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        # should_trade returns False during blackout
        should_trade = news_service.should_trade()
        assert should_trade == False
    
    @pytest.mark.asyncio
    async def test_bot_trades_outside_blackout(self, news_service):
        """Test that trading is allowed outside blackout periods."""
        # Create event far in future
        event_time = datetime.now() + timedelta(hours=5)
        events = [{
            'title': 'Future Event',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        should_trade = news_service.should_trade()
        assert should_trade == True
    
    def test_get_events_for_currency(self, news_service):
        """Test filtering events by currency."""
        events = [
            {'title': 'NFP', 'datetime': datetime.now().isoformat(), 'impact': 'high', 'currency': 'USD'},
            {'title': 'ECB', 'datetime': datetime.now().isoformat(), 'impact': 'high', 'currency': 'EUR'},
            {'title': 'BOE', 'datetime': datetime.now().isoformat(), 'impact': 'high', 'currency': 'GBP'},
        ]
        
        news_service.set_events(events)
        
        usd_events = news_service.get_events_for_currency('USD')
        assert len(usd_events) == 1
        assert usd_events[0]['currency'] == 'USD'


class TestGeopoliticalNews:
    """Tests for geopolitical news handling."""
    
    def test_detect_war_news(self, news_service):
        """Test detection of war-related news."""
        headlines = [
            "Trade negotiations continue between US and China",
            "Military conflict escalates in Eastern Europe",
            "Central bank maintains interest rates"
        ]
        
        war_related = news_service.filter_geopolitical_news(headlines)
        
        assert len(war_related) >= 1
        assert any('military' in h.lower() or 'conflict' in h.lower() for h in war_related)
    
    def test_detect_sanctions_news(self, news_service):
        """Test detection of sanctions-related news."""
        headlines = [
            "New economic sanctions imposed on major oil producer",
            "Stock market reaches new highs",
            "Weather forecast for trading week"
        ]
        
        sanctions_related = news_service.filter_geopolitical_news(headlines)
        
        assert len(sanctions_related) >= 1
        assert any('sanctions' in h.lower() for h in sanctions_related)
    
    def test_geopolitical_risk_level(self, news_service):
        """Test geopolitical risk level assessment."""
        # Set multiple geopolitical news items
        news_service.add_geopolitical_news([
            "Tensions rise in Middle East oil region",
            "Major currency bloc faces instability",
            "Trade war escalates between major economies"
        ])
        
        risk_level = news_service.get_geopolitical_risk_level()
        
        # With 3 geopolitical items, risk should be elevated
        assert risk_level in ['low', 'medium', 'high', 'extreme']
        assert risk_level != 'low'  # Should be elevated due to multiple items


class TestNewsEvents:
    """Tests for specific high-impact news events."""
    
    def test_nfp_recognition(self, news_service):
        """Test recognition of Non-Farm Payrolls."""
        event = {
            'title': 'Nonfarm Payrolls',
            'impact': 'high',
            'currency': 'USD'
        }
        
        is_nfp = news_service.is_nfp_event(event)
        assert is_nfp == True
    
    def test_fomc_recognition(self, news_service):
        """Test recognition of FOMC events."""
        event = {
            'title': 'Fed Interest Rate Decision',
            'impact': 'high',
            'currency': 'USD'
        }
        
        is_fomc = news_service.is_fomc_event(event)
        assert is_fomc == True
    
    def test_cpi_recognition(self, news_service):
        """Test recognition of CPI events."""
        event = {
            'title': 'Consumer Price Index m/m',
            'impact': 'high',
            'currency': 'USD'
        }
        
        is_cpi = news_service.is_cpi_event(event)
        assert is_cpi == True
    
    def test_extended_blackout_for_fomc(self, news_service):
        """Test extended blackout period for FOMC events."""
        # FOMC should have longer blackout (60 min instead of 30)
        event_time = datetime.now() + timedelta(minutes=45)
        events = [{
            'title': 'FOMC Rate Decision',
            'datetime': event_time.isoformat(),
            'impact': 'high',
            'currency': 'USD'
        }]
        
        news_service.set_events(events)
        
        # With 45 minutes to FOMC, should still be in blackout (extended to 60)
        is_blackout, reason = news_service.is_blackout_period()
        # This test expects extended blackout for FOMC
        # If not implemented, this will fail and guide implementation
        assert is_blackout == True
