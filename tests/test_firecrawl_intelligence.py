"""
Tests for Firecrawl Intelligence Service - comprehensive market intelligence.

Tests cover:
- Core intelligence (DXY, VIX, COT, news)
- Retail sentiment (contrarian indicator)
- Currency strength
- TradingView technical
- Social sentiment
- Options flow
- Bond yields
- BTC dominance
- Economic surprise
- Seasonal patterns
- Intermarket analysis
- Complete analysis aggregation
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


class TestFirecrawlServiceInitialization:
    """Tests for service initialization."""
    
    def test_initialization_with_api_key(self):
        """Test service initializes correctly with API key."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(
            api_key="test_api_key",
            refresh_minutes=15,
            enabled=True
        )
        
        assert service.api_key == "test_api_key"
        assert service.refresh_minutes == 15
        assert service.enabled == True
    
    def test_initialization_without_api_key(self):
        """Test service handles missing API key."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(
            api_key="",
            refresh_minutes=15,
            enabled=True
        )
        
        # Should not be available without API key
        assert service.api_key == ""
    
    def test_initialization_disabled(self):
        """Test service can be disabled."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(
            api_key="test_key",
            refresh_minutes=15,
            enabled=False
        )
        
        assert service.enabled == False


class TestCachingMechanism:
    """Tests for the caching system."""
    
    def test_cache_is_valid_when_fresh(self):
        """Test cache validity check for fresh data."""
        from trading_bot.services.firecrawl_intelligence import (
            FirecrawlIntelligenceService, IntelligenceCache
        )
        
        service = FirecrawlIntelligenceService(
            api_key="test_key",
            refresh_minutes=15
        )
        
        # Add fresh cache entry
        service._cache["test_key"] = IntelligenceCache(
            data={"value": 123},
            timestamp=datetime.now(),
            ttl_minutes=15
        )
        
        assert service._is_cache_valid("test_key") == True
    
    def test_cache_is_expired(self):
        """Test cache validity check for expired data."""
        from trading_bot.services.firecrawl_intelligence import (
            FirecrawlIntelligenceService, IntelligenceCache
        )
        
        service = FirecrawlIntelligenceService(
            api_key="test_key",
            refresh_minutes=15
        )
        
        # Add expired cache entry
        service._cache["test_key"] = IntelligenceCache(
            data={"value": 123},
            timestamp=datetime.now() - timedelta(minutes=20),
            ttl_minutes=15
        )
        
        assert service._is_cache_valid("test_key") == False
    
    def test_update_cache(self):
        """Test cache update functionality."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(
            api_key="test_key",
            refresh_minutes=15
        )
        
        test_data = {"trend": "bullish"}
        service._update_cache("dxy", test_data)
        
        assert "dxy" in service._cache
        assert service._cache["dxy"].data == test_data
        assert service._is_cache_valid("dxy") == True


class TestDXYAnalysis:
    """Tests for DXY (Dollar Index) analysis."""
    
    @pytest.mark.asyncio
    async def test_get_dxy_returns_cached_data(self):
        """Test that cached DXY data is returned."""
        from trading_bot.services.firecrawl_intelligence import (
            FirecrawlIntelligenceService, IntelligenceCache
        )
        
        service = FirecrawlIntelligenceService(
            api_key="test_key",
            refresh_minutes=15
        )
        
        cached_data = {"trend": "bullish", "bias": "USD strength expected"}
        service._cache["dxy"] = IntelligenceCache(
            data=cached_data,
            timestamp=datetime.now(),
            ttl_minutes=15
        )
        
        result = await service.get_dxy_analysis()
        
        assert result == cached_data
    
    def test_parse_dxy_sentiment_bullish(self):
        """Test parsing bullish DXY sentiment."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        markdown = "DXY is bullish, strong, rising, uptrend expected, buy signals"
        result = service._parse_dxy_sentiment(markdown)
        
        assert result["trend"] == "bullish"
    
    def test_parse_dxy_sentiment_bearish(self):
        """Test parsing bearish DXY sentiment."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        markdown = "DXY is bearish, weak, falling, downtrend expected, sell signals"
        result = service._parse_dxy_sentiment(markdown)
        
        assert result["trend"] == "bearish"


class TestVIXSentiment:
    """Tests for VIX (Fear Index) analysis."""
    
    def test_parse_vix_extreme_fear(self):
        """Test parsing extreme fear VIX level."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        markdown = "VIX at 28.5 points"
        result = service._parse_vix_data(markdown)
        
        assert result["level"] == 28.5
        assert result["sentiment"] == "extreme_fear"
        assert result["risk_mode"] == "risk_off"
    
    def test_parse_vix_complacency(self):
        """Test parsing low VIX (complacency)."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        markdown = "VIX at 11.5 points"
        result = service._parse_vix_data(markdown)
        
        assert result["level"] == 11.5
        assert result["sentiment"] == "complacency"
        assert result["risk_mode"] == "risk_on"


class TestRetailSentiment:
    """Tests for retail sentiment (contrarian indicator)."""
    
    def test_parse_extreme_long_retail(self):
        """Test parsing extreme long retail positioning."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        # Regex expects: (\d+)%?\s*(?:are\s+)?long
        # So text must have "75% are long" or "75% long" pattern
        mock_results = {
            "data": [
                {"description": "EURUSD retail: 75% are long", "title": "Sentiment"}
            ]
        }
        
        result = service._parse_retail_sentiment(mock_results, "EURUSD")
        
        assert result["bias"] == "extreme_long"
        assert result["contrarian_signal"] == "short"
    
    def test_parse_extreme_short_retail(self):
        """Test parsing extreme short retail positioning."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        # Regex expects: (\d+)%?\s*(?:are\s+)?short
        mock_results = {
            "data": [
                {"description": "GBPUSD retail: 72% are short", "title": "Sentiment"}
            ]
        }
        
        result = service._parse_retail_sentiment(mock_results, "GBPUSD")
        
        assert result["bias"] == "extreme_short"
        assert result["contrarian_signal"] == "long"


class TestCurrencyStrength:
    """Tests for currency strength rankings."""
    
    def test_parse_currency_strength(self):
        """Test parsing currency strength data."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        mock_results = {
            "data": [
                {"description": "USD strongest currency this week", "title": "Strength"},
                {"description": "JPY weakest among majors", "title": "Weakness"}
            ]
        }
        
        result = service._parse_currency_strength(mock_results)
        
        assert result["strongest"] == "USD"
        assert result["weakest"] == "JPY"
        assert result["recommendation"] is not None


class TestBondYields:
    """Tests for bond yield spread analysis."""
    
    def test_parse_yield_spread(self):
        """Test parsing bond yield spread."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        mock_results = [
            {"markdown": "US 10Y yield at 4.25%"},
            {"markdown": "German 10Y yield at 2.35%"}
        ]
        
        result = service._parse_yield_spread(mock_results)
        
        assert result["us_10y"] == 4.25
        assert result["de_10y"] == 2.35
        assert result["spread"] == pytest.approx(1.9, 0.1)


class TestBTCDominance:
    """Tests for Bitcoin dominance analysis."""
    
    def test_parse_btc_dominance_rising(self):
        """Test parsing rising BTC dominance."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        # Regex: r'(\d+\.?\d*)%?\s*(?:dominance|dom\.?|btc\.d)'
        # Needs number THEN "dominance" or "btc.d"
        markdown = "Current level: 52.3% dominance, rising trend"
        result = service._parse_btc_dominance(markdown)
        
        assert result["dominance"] == 52.3
        assert result["trend"] == "rising"
        assert result["altcoin_sentiment"] == "bearish"
    
    def test_parse_btc_dominance_falling(self):
        """Test parsing falling BTC dominance."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        # Match pattern: number then btc.d
        markdown = "48.5 btc.d falling from highs"
        result = service._parse_btc_dominance(markdown)
        
        assert result["dominance"] == 48.5
        assert result["trend"] == "falling"
        assert result["altcoin_sentiment"] == "bullish"


class TestSeasonalPatterns:
    """Tests for seasonal pattern analysis."""
    
    def test_parse_seasonal_bullish(self):
        """Test parsing bullish seasonal pattern."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        mock_results = {
            "data": [
                {"description": "EURUSD typically rises in February with 68% accuracy", "title": ""}
            ]
        }
        
        result = service._parse_seasonal_pattern(mock_results, "EURUSD", "February")
        
        assert result["current_month_bias"] == "bullish"
        assert result["historical_accuracy"] == 68


class TestIntermarketAnalysis:
    """Tests for intermarket correlations."""
    
    def test_determine_risk_on_environment(self):
        """Test determining risk-on environment."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        data = {
            "spx_trend": "bullish",
            "vix_sentiment": "complacency",
            "gold_trend": "bearish"
        }
        
        result = service._determine_risk_environment(data)
        
        assert "risk_on" in result
    
    def test_determine_risk_off_environment(self):
        """Test determining risk-off environment."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        data = {
            "spx_trend": "bearish",
            "vix_sentiment": "fear",
            "gold_trend": "bullish"
        }
        
        result = service._determine_risk_environment(data)
        
        assert "risk_off" in result


class TestCompleteAnalysis:
    """Tests for complete analysis aggregation."""
    
    @pytest.mark.asyncio
    async def test_complete_analysis_computes_bias(self):
        """Test that complete analysis computes overall bias."""
        from trading_bot.services.firecrawl_intelligence import (
            FirecrawlIntelligenceService, IntelligenceCache
        )
        
        service = FirecrawlIntelligenceService(api_key="", refresh_minutes=15)
        
        # Mock all the individual methods
        service.get_dxy_analysis = AsyncMock(return_value={"trend": "bearish"})
        service.get_vix_sentiment = AsyncMock(return_value={"risk_mode": "risk_on", "level": 15})
        service.get_retail_sentiment = AsyncMock(return_value={"contrarian_signal": "long", "bias": "short"})
        service.get_currency_strength = AsyncMock(return_value={"strongest": "EUR", "weakest": "USD"})
        service.get_tradingview_technical = AsyncMock(return_value={"signal": "buy", "consensus": "buy"})
        service.get_rate_expectations = AsyncMock(return_value={"fed": {"next_move": "cut"}})
        service.get_commodity_correlation = AsyncMock(return_value={"trend": "neutral"})
        service.get_twitter_forex_sentiment = AsyncMock(return_value={"contrarian_signal": "neutral"})
        service.get_options_flow = AsyncMock(return_value={"flow": "bullish"})
        service.get_bond_yield_spread = AsyncMock(return_value={"eurusd_bias": "bullish", "spread": 1.0})
        service.get_economic_surprise_index = AsyncMock(return_value={"eurusd_bias": "neutral"})
        service.get_seasonal_pattern = AsyncMock(return_value={"current_month_bias": "bullish", "historical_accuracy": 70})
        service.get_intermarket_analysis = AsyncMock(return_value={"risk_environment": "risk_on"})
        service.get_btc_dominance = AsyncMock(return_value={"dominance": 50, "trend": "stable"})
        
        result = await service.get_complete_analysis("EURUSD")
        
        assert "overall_bias" in result
        assert "bullish_signals" in result
        assert "bearish_signals" in result
        assert result["symbol"] == "EURUSD"
        # With these mocked values, should be bullish
        assert result["bullish_signals"] > result["bearish_signals"]


class TestMarketContextBuilder:
    """Tests for building Claude context string."""
    
    def test_context_includes_all_cached_data(self):
        """Test that context includes all available cached data."""
        from trading_bot.services.firecrawl_intelligence import (
            FirecrawlIntelligenceService, IntelligenceCache
        )
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        # Ensure is_available returns True even without Firecrawl SDK installed
        if service.client is None:
            from unittest.mock import MagicMock
            service.client = MagicMock()
        
        # Add various cached data
        now = datetime.now()
        service._cache["dxy"] = IntelligenceCache(
            data={"trend": "bullish", "bias": "USD strength"},
            timestamp=now, ttl_minutes=15
        )
        service._cache["vix"] = IntelligenceCache(
            data={"level": 18, "sentiment": "neutral", "risk_mode": "neutral"},
            timestamp=now, ttl_minutes=15
        )
        service._cache["retail_eurusd"] = IntelligenceCache(
            data={"bias": "extreme_long", "contrarian_signal": "short"},
            timestamp=now, ttl_minutes=15
        )
        
        context = service.get_market_context_for_claude("EURUSD")
        
        # Should include DXY section
        assert "DXY" in context or "Dollar" in context
    
    def test_context_empty_when_no_data(self):
        """Test context is empty when no data available."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        # No cached data
        context = service.get_market_context_for_claude("EURUSD")
        
        # Should return empty string
        assert context == ""


class TestRefreshMethods:
    """Tests for data refresh methods."""
    
    @pytest.mark.asyncio
    async def test_refresh_all_calls_all_methods(self):
        """Test that refresh_all calls all intelligence methods."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        # Must use a valid api_key AND mock client so is_available returns True
        service = FirecrawlIntelligenceService(api_key="test_api_key", refresh_minutes=15)
        # Ensure client is not None so is_available is True
        service.client = MagicMock()
        
        # Mock all methods
        service.get_dxy_analysis = AsyncMock(return_value={})
        service.get_vix_sentiment = AsyncMock(return_value={})
        service.get_breaking_news = AsyncMock(return_value=[])
        service.get_geopolitical_news = AsyncMock(return_value=[])
        service.get_intermarket_analysis = AsyncMock(return_value={})
        service.get_retail_sentiment = AsyncMock(return_value={})
        service.get_tradingview_technical = AsyncMock(return_value={})
        service.get_twitter_forex_sentiment = AsyncMock(return_value={})
        service.get_options_flow = AsyncMock(return_value={})
        service.get_seasonal_pattern = AsyncMock(return_value={})
        service.get_cot_positioning = AsyncMock(return_value={})
        service.get_central_bank_sentiment = AsyncMock(return_value={})
        service.get_rate_expectations = AsyncMock(return_value={})
        service.get_bond_yield_spread = AsyncMock(return_value={})
        service.get_economic_surprise_index = AsyncMock(return_value={})
        service.get_commodity_correlation = AsyncMock(return_value={})
        service.get_economic_calendar_today = AsyncMock(return_value=[])
        service.get_currency_strength = AsyncMock(return_value={})
        service.get_btc_dominance = AsyncMock(return_value={})
        
        await service.refresh_all(["EURUSD"])
        
        # Verify key methods were called
        service.get_dxy_analysis.assert_called()
        service.get_vix_sentiment.assert_called()
    
    @pytest.mark.asyncio
    async def test_refresh_quick_only_time_sensitive(self):
        """Test quick refresh only calls time-sensitive methods."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        # Must use a valid api_key AND mock client so is_available returns True
        service = FirecrawlIntelligenceService(api_key="test_api_key", refresh_minutes=15)
        service.client = MagicMock()
        
        service.get_dxy_analysis = AsyncMock(return_value={})
        service.get_vix_sentiment = AsyncMock(return_value={})
        service.get_geopolitical_news = AsyncMock(return_value=[])
        service.get_retail_sentiment = AsyncMock(return_value={})
        service.get_twitter_forex_sentiment = AsyncMock(return_value={})
        
        await service.refresh_quick("EURUSD")
        
        # Quick refresh should call these
        service.get_dxy_analysis.assert_called()
        service.get_vix_sentiment.assert_called()
        service.get_retail_sentiment.assert_called()


class TestErrorHandling:
    """Tests for error handling."""
    
    @pytest.mark.asyncio
    async def test_graceful_failure_on_api_error(self):
        """Test service handles API errors gracefully."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
        
        # Should not raise, should return fallback data
        result = await service.get_dxy_analysis()
        
        # Should return dict (possibly with unknown/error values)
        assert isinstance(result, dict)
    
    def test_status_returns_service_info(self):
        """Test get_status returns service information."""
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
        
        service = FirecrawlIntelligenceService(
            api_key="test_key",
            refresh_minutes=15,
            enabled=True
        )
        
        status = service.get_status()
        
        assert "enabled" in status
        assert "available" in status
        assert "refresh_minutes" in status
        assert "cached_keys" in status


# Fixtures
@pytest.fixture
def mock_firecrawl_service():
    """Create a mocked Firecrawl service."""
    from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService
    
    service = FirecrawlIntelligenceService(
        api_key="test_key",
        refresh_minutes=15
    )
    
    # Mock the client
    service.client = MagicMock()
    
    return service


@pytest.fixture
def populated_cache_service():
    """Create a service with pre-populated cache."""
    from trading_bot.services.firecrawl_intelligence import (
        FirecrawlIntelligenceService, IntelligenceCache
    )
    
    service = FirecrawlIntelligenceService(
        api_key="test_key",
        refresh_minutes=15
    )
    
    now = datetime.now()
    
    service._cache = {
        "dxy": IntelligenceCache(
            data={"trend": "bullish", "bias": "USD strength"},
            timestamp=now, ttl_minutes=15
        ),
        "vix": IntelligenceCache(
            data={"level": 18, "sentiment": "neutral", "risk_mode": "neutral"},
            timestamp=now, ttl_minutes=15
        ),
        "retail_eurusd": IntelligenceCache(
            data={"bias": "long", "contrarian_signal": "short"},
            timestamp=now, ttl_minutes=15
        ),
        "currency_strength": IntelligenceCache(
            data={"strongest": "USD", "weakest": "JPY"},
            timestamp=now, ttl_minutes=15
        ),
    }
    
    return service


class TestFirecrawlCostOptimization:
    """
    Tests to verify Firecrawl cost optimization:
    - No agent() calls remain in the service
    - No extract() calls remain in the service  
    - search() is the only Firecrawl API method used (besides scrape() for page fetches)
    """
    
    def _get_source_code(self) -> str:
        """Read the firecrawl_intelligence.py source for static analysis."""
        import os
        source_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "trading_bot", "services", "firecrawl_intelligence.py"
        )
        with open(source_path, "r", encoding="utf-8") as f:
            return f.read()
    
    def test_no_agent_calls_remain(self):
        """Verify no self.client.agent() calls exist in the service."""
        source = self._get_source_code()
        import re
        agent_calls = re.findall(r'self\.client\.agent\s*\(', source)
        assert len(agent_calls) == 0, (
            f"Found {len(agent_calls)} agent() call(s) in firecrawl_intelligence.py. "
            f"All agent() calls should be replaced with search() to save credits."
        )
    
    def test_no_extract_calls_remain(self):
        """Verify no self.client.extract() calls exist in the service."""
        source = self._get_source_code()
        import re
        extract_calls = re.findall(r'self\.client\.extract\s*\(', source)
        assert len(extract_calls) == 0, (
            f"Found {len(extract_calls)} extract() call(s) in firecrawl_intelligence.py. "
            f"All extract() calls should be replaced with search() to save credits."
        )
    
    def test_search_calls_exist(self):
        """Verify search() calls are present in the service."""
        source = self._get_source_code()
        import re
        search_calls = re.findall(r'self\.client\.search\s*\(', source)
        assert len(search_calls) >= 10, (
            f"Expected at least 10 search() calls (original + replacements), "
            f"found {len(search_calls)}."
        )
    
    def test_only_search_and_scrape_api_methods(self):
        """Verify only search() and scrape() are used as Firecrawl API calls."""
        source = self._get_source_code()
        import re
        all_client_calls = re.findall(r'self\.client\.(\w+)\s*\(', source)
        allowed_methods = {'search', 'scrape'}
        disallowed = [m for m in all_client_calls if m not in allowed_methods]
        assert len(disallowed) == 0, (
            f"Found disallowed Firecrawl API methods: {set(disallowed)}. "
            f"Only search() and scrape() should be used."
        )
    
    def test_firecrawl_package_importable(self):
        """Service degrades gracefully when firecrawl-py is not installed."""
        import importlib.util
        from trading_bot.services.firecrawl_intelligence import FirecrawlIntelligenceService

        if importlib.util.find_spec("firecrawl") is None:
            service = FirecrawlIntelligenceService(api_key="test_key", refresh_minutes=15)
            assert service.client is None
            assert service.get_status()["available"] is False
            return

        from firecrawl import FirecrawlApp
        assert FirecrawlApp is not None
    
    def test_research_geopolitical_uses_search(self):
        """Verify research_geopolitical_risk uses search instead of agent."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def research_geopolitical_risk\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "research_geopolitical_risk method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "research_geopolitical_risk should use search()"
        assert 'self.client.agent(' not in method_body, "research_geopolitical_risk should NOT use agent()"
    
    def test_research_central_bank_uses_search(self):
        """Verify research_central_bank_policy uses search instead of agent."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def research_central_bank_policy\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "research_central_bank_policy method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "research_central_bank_policy should use search()"
        assert 'self.client.agent(' not in method_body, "research_central_bank_policy should NOT use agent()"
    
    def test_research_intermarket_uses_search(self):
        """Verify research_intermarket_correlations uses search instead of agent."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def research_intermarket_correlations\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "research_intermarket_correlations method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "research_intermarket_correlations should use search()"
        assert 'self.client.agent(' not in method_body, "research_intermarket_correlations should NOT use agent()"
    
    def test_research_symbol_fundamentals_uses_search(self):
        """Verify research_symbol_fundamentals uses search instead of agent."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def research_symbol_fundamentals\(self.*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "research_symbol_fundamentals method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "research_symbol_fundamentals should use search()"
        assert 'self.client.agent(' not in method_body, "research_symbol_fundamentals should NOT use agent()"
    
    def test_extract_economic_calendar_uses_search(self):
        """Verify extract_economic_calendar uses search instead of extract."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def extract_economic_calendar\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "extract_economic_calendar method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "extract_economic_calendar should use search()"
        assert 'self.client.extract(' not in method_body, "extract_economic_calendar should NOT use extract()"
    
    def test_extract_cot_positioning_uses_search(self):
        """Verify extract_cot_positioning uses search instead of extract."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def extract_cot_positioning\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "extract_cot_positioning method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "extract_cot_positioning should use search()"
        assert 'self.client.extract(' not in method_body, "extract_cot_positioning should NOT use extract()"
    
    def test_extract_rate_expectations_uses_search(self):
        """Verify extract_rate_expectations uses search instead of extract."""
        source = self._get_source_code()
        import re
        method_match = re.search(
            r'async def extract_rate_expectations\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert method_match, "extract_rate_expectations method not found"
        method_body = method_match.group()
        assert 'self.client.search(' in method_body, "extract_rate_expectations should use search()"
        assert 'self.client.extract(' not in method_body, "extract_rate_expectations should NOT use extract()"
