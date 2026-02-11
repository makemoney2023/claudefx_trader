"""
Firecrawl Intelligence Service for real-time market data.

Scrapes and aggregates market intelligence from various sources
to enhance Claude's trading analysis with live context.

Data sources:
- DXY (Dollar Index) trend analysis
- COT (Commitment of Traders) institutional positioning
- Central bank statement sentiment
- Breaking news detection
- Retail sentiment (contrarian indicator)
- Currency strength meter
- VIX/Risk sentiment
- Economic calendar events
- TradingView technical consensus
- Interest rate expectations
- Commodity correlations
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import json
import re

from ..utils.logging import get_logger

# Import Pydantic schemas for structured deep research
from .intelligence_schemas import (
    GeopoliticalAnalysis,
    GeopoliticalEvent,
    CentralBankAnalysis,
    CentralBankStance,
    IntermarketAnalysis,
    MarketTrend,
    SymbolFundamentals,
    EconomicCalendar,
    EconomicCalendarEvent,
    COTAnalysis,
    InstitutionalPositioning,
    RateExpectations,
    RateExpectation,
    MarketIntelligence,
)

logger = get_logger(__name__)

# Try to import firecrawl (v2 SDK uses Firecrawl class, not FirecrawlApp)
try:
    from firecrawl import Firecrawl, AsyncFirecrawl
    FIRECRAWL_AVAILABLE = True
except ImportError:
    try:
        # Fallback for older SDK versions
        from firecrawl import FirecrawlApp as Firecrawl
        AsyncFirecrawl = None
        FIRECRAWL_AVAILABLE = True
    except ImportError:
        FIRECRAWL_AVAILABLE = False
        Firecrawl = None
        AsyncFirecrawl = None
        logger.warning("Firecrawl SDK not installed: pip install firecrawl-py")


@dataclass
class IntelligenceCache:
    """Cached intelligence data with TTL."""
    data: Dict[str, Any]
    timestamp: datetime
    ttl_minutes: int = 15
    
    def is_expired(self) -> bool:
        return datetime.now() - self.timestamp > timedelta(minutes=self.ttl_minutes)


@dataclass
class CurrencyStrength:
    """Currency strength data."""
    currency: str
    strength: float  # 0-100
    rank: int  # 1-8 (strongest to weakest)
    trend: str  # 'strengthening', 'weakening', 'stable'


@dataclass
class RetailSentiment:
    """Retail positioning (contrarian indicator)."""
    symbol: str
    long_percent: float
    short_percent: float
    bias: str  # 'extreme_long', 'long', 'neutral', 'short', 'extreme_short'
    contrarian_signal: str  # The opposite of retail


class FirecrawlIntelligenceService:
    """
    Real-time market intelligence via Firecrawl web scraping.
    
    Provides:
    - DXY (Dollar Index) trend analysis
    - COT (Commitment of Traders) institutional positioning
    - Central bank statement sentiment
    - Breaking news detection
    - Retail sentiment (CONTRARIAN - trade against crowd)
    - Currency strength rankings
    - VIX / Risk sentiment
    - Economic calendar live events
    - TradingView technical consensus
    - Interest rate expectations
    - Commodity correlations (Oil->CAD, Gold->AUD)
    """
    
    # Data source URLs
    SOURCES = {
        "dxy": "https://www.tradingview.com/symbols/TVC-DXY/",
        "cot_overview": "https://www.myfxbook.com/commitments-of-traders",
        "fed_news": "https://www.federalreserve.gov/newsevents.htm",
        "forex_factory": "https://www.forexfactory.com/calendar",
        # Sentiment sources
        "retail_sentiment": "https://www.myfxbook.com/community/outlook",
        "currency_strength": "https://www.livecharts.co.uk/currency-strength.php",
        "vix": "https://www.tradingview.com/symbols/CBOE-VIX/",
        "tradingview_eurusd": "https://www.tradingview.com/symbols/FX-EURUSD/technicals/",
        "tradingview_gbpusd": "https://www.tradingview.com/symbols/FX-GBPUSD/technicals/",
        "rate_expectations": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
        "oil": "https://www.tradingview.com/symbols/TVC-USOIL/",
        "gold": "https://www.tradingview.com/symbols/TVC-GOLD/",
        # NEW: Advanced Intelligence Sources
        "spx": "https://www.tradingview.com/symbols/SP-SPX/",
        "us10y": "https://www.tradingview.com/symbols/TVC-US10Y/",
        "de10y": "https://www.tradingview.com/symbols/TVC-DE10Y/",
        "btc_dominance": "https://www.tradingview.com/symbols/CRYPTOCAP-BTC.D/",
        "forex_seasonality": "https://www.seasonalcharts.com/future_currencies.html",
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        refresh_minutes: int = 15,
        enabled: bool = True
    ):
        """
        Initialize the Firecrawl Intelligence Service.
        
        Args:
            api_key: Firecrawl API key
            refresh_minutes: Cache refresh interval in minutes
            enabled: Whether the service is enabled
        """
        self.api_key = api_key
        self.refresh_minutes = refresh_minutes
        self.enabled = enabled
        self._cache: Dict[str, IntelligenceCache] = {}
        
        if FIRECRAWL_AVAILABLE and self.api_key and self.enabled:
            # Use new Firecrawl class (v2 SDK)
            self.client = Firecrawl(api_key=self.api_key)
            # Initialize async client if available (for non-blocking operations)
            self.async_client = AsyncFirecrawl(api_key=self.api_key) if AsyncFirecrawl else None
            logger.info("FirecrawlIntelligenceService initialized (v2 SDK)")
        else:
            self.client = None
            self.async_client = None
            if not FIRECRAWL_AVAILABLE:
                logger.warning("Firecrawl SDK not available")
            elif not self.api_key:
                logger.warning("No Firecrawl API key configured")
            elif not self.enabled:
                logger.info("FirecrawlIntelligenceService disabled")
    
    @property
    def is_available(self) -> bool:
        """Check if the service is available and configured."""
        return self.client is not None and self.enabled
    
    async def get_dxy_analysis(self) -> Dict[str, Any]:
        """
        Get DXY trend analysis.
        
        Returns:
            Dict with DXY trend and bias information
        """
        cache_key = "dxy"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"trend": "unknown", "note": "Firecrawl not configured"}
        
        try:
            result = self.client.scrape(
                self.SOURCES["dxy"],
                formats=["markdown"]
            )
            
            # Parse the result for trend indicators
            markdown = result.markdown if hasattr(result, 'markdown') else (result.get("markdown", "") if isinstance(result, dict) else "")
            analysis = self._parse_dxy_sentiment(markdown)
            self._update_cache(cache_key, analysis)
            return analysis
            
        except Exception as e:
            logger.error(f"Error fetching DXY analysis: {e}")
            return {"trend": "unknown", "error": str(e)}
    
    async def get_cot_positioning(self, currency: str = "EUR") -> Dict[str, Any]:
        """
        Get COT data for institutional positioning.
        
        Args:
            currency: Currency to get positioning for (EUR, GBP, JPY, etc.)
            
        Returns:
            Dict with positioning information
        """
        cache_key = f"cot_{currency.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"positioning": "unknown"}
        
        try:
            # Search for recent COT analysis
            results = self.client.search(
                f"{currency} COT commitment of traders positioning forex 2026",
                limit=3
            )
            
            positioning = self._parse_cot_data(results, currency)
            self._update_cache(cache_key, positioning)
            return positioning
            
        except Exception as e:
            logger.error(f"Error fetching COT data for {currency}: {e}")
            return {"positioning": "unknown", "error": str(e)}
    
    async def get_breaking_news(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Search for breaking news affecting symbols.
        
        Args:
            symbols: List of trading symbols
            
        Returns:
            List of news items
        """
        cache_key = "breaking_news"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return []
        
        try:
            # Build search query
            symbol_terms = " OR ".join(symbols[:3])  # Limit to 3 symbols
            query = f"forex {symbol_terms} breaking news market moving"
            
            results = self.client.search(query, limit=5)
            
            news_items = self._parse_news_results(results)
            self._update_cache(cache_key, news_items)
            return news_items
            
        except Exception as e:
            logger.error(f"Error fetching breaking news: {e}")
            return []
    
    async def get_geopolitical_news(self) -> Dict[str, Any]:
        """
        Fetch geopolitical news from major sources that could affect markets.
        
        Uses Firecrawl's news sources and time-based filtering for fresh content.
        
        Returns:
            Dict with risk_level, headlines, and analysis
        """
        cache_key = "geopolitical_news"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"risk_level": "unknown", "headlines": [], "error": "Firecrawl not available"}
        
        try:
            # Use async client if available, otherwise wrap sync in thread
            if self.async_client:
                results = await self.async_client.search(
                    query="geopolitical news war sanctions tariffs trade tensions military conflict crisis",
                    limit=10,
                    tbs="qdr:d"  # Past 24 hours for fresh news
                )
            else:
                results = await asyncio.to_thread(
                    lambda: self.client.search(
                        query="geopolitical news war sanctions tariffs trade tensions military conflict crisis",
                        limit=10,
                        tbs="qdr:d"  # Past 24 hours for fresh news
                    )
                )
            
            headlines = []
            normalized = self._normalize_search_results(results)
            
            for item in normalized:
                title = item.get('title', '')
                description = item.get('description', '')
                url = item.get('url', '')
                
                if title:
                    headlines.append({
                        'title': title,
                        'description': description[:200] if description else '',
                        'url': url,
                        'source': self._extract_source_from_url(url)
                    })
            
            # Determine risk level based on keywords
            risk_keywords = {
                'extreme': ['nuclear', 'invasion', 'war declared', 'massive attack'],
                'high': ['war', 'military strike', 'sanctions imposed', 'conflict escalates', 'troops deployed'],
                'medium': ['tensions', 'trade war', 'tariffs', 'crisis', 'threat', 'protest'],
                'low': ['talks', 'negotiations', 'summit', 'diplomacy']
            }
            
            all_text = ' '.join([h.get('title', '') + ' ' + h.get('description', '') for h in headlines]).lower()
            
            risk_level = 'low'
            for level in ['extreme', 'high', 'medium']:
                if any(kw in all_text for kw in risk_keywords.get(level, [])):
                    risk_level = level
                    break
            
            result = {
                'risk_level': risk_level,
                'headlines': headlines[:10],  # Limit to 10
                'total_found': len(headlines),
                'last_updated': datetime.now().isoformat()
            }
            
            self._update_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error fetching geopolitical news: {e}")
            return {"risk_level": "unknown", "headlines": [], "error": str(e)}
    
    def _extract_source_from_url(self, url: str) -> str:
        """Extract news source from URL."""
        if not url:
            return "Unknown"
        
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            # Remove common prefixes
            domain = domain.replace('www.', '').replace('news.', '')
            # Get just the main domain name
            parts = domain.split('.')
            if len(parts) >= 2:
                return parts[-2].capitalize()
            return domain.capitalize()
        except:
            return "Unknown"
    
    # =========================================================================
    # DEEP RESEARCH (AGENT) METHODS - AI-Powered Market Analysis
    # =========================================================================
    
    async def research_geopolitical_risk(self) -> Optional[GeopoliticalAnalysis]:
        """
        Use Firecrawl Agent for deep geopolitical research.
        
        The Agent autonomously searches, navigates, and extracts data
        to provide comprehensive geopolitical risk assessment.
        
        Returns:
            GeopoliticalAnalysis schema with structured risk data
        """
        cache_key = "deep_geopolitical"
        if self._is_cache_valid(cache_key, ttl_override=30):  # 30 min TTL for deep research
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return GeopoliticalAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            logger.warning("Firecrawl not available for geopolitical research")
            return None
        
        try:
            logger.info("🔍 Starting Deep Research: Geopolitical Risk Analysis (via search)...")
            
            # Use search instead of agent to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query="geopolitical risk forex war sanctions trade tensions military conflict 2026",
                    limit=5
                )
            )
            
            normalized = self._normalize_search_results(results)
            
            # Parse search results into events
            events = []
            risk_keywords_extreme = ['nuclear', 'invasion', 'war declared', 'massive attack']
            risk_keywords_high = ['war', 'military strike', 'sanctions imposed', 'conflict escalates', 'troops deployed']
            risk_keywords_medium = ['tensions', 'trade war', 'tariffs', 'crisis', 'threat']
            
            all_text = ""
            for item in normalized[:10]:
                title = item.get('title', '')
                desc = item.get('description', '')[:200]
                url = item.get('url', '')
                all_text += f" {title} {desc}"
                
                if title:
                    events.append(GeopoliticalEvent(
                        headline=title,
                        source=self._extract_source_from_url(url),
                        impact_level='high' if any(kw in title.lower() for kw in risk_keywords_high) else 'medium',
                        affected_currencies=[],
                        summary=desc,
                        region='Global'
                    ))
            
            # Determine risk level from all text
            all_lower = all_text.lower()
            if any(kw in all_lower for kw in risk_keywords_extreme):
                risk_level = 'extreme'
            elif any(kw in all_lower for kw in risk_keywords_high):
                risk_level = 'high'
            elif any(kw in all_lower for kw in risk_keywords_medium):
                risk_level = 'medium'
            else:
                risk_level = 'low'
            
            safe_haven = 'elevated' if risk_level in ('high', 'extreme') else 'normal'
            
            analysis = GeopoliticalAnalysis(
                risk_level=risk_level,
                events=events[:10],
                trading_recommendation=f"Risk level {risk_level} — {'reduce position sizes, favor safe havens' if risk_level in ('high', 'extreme') else 'normal trading conditions'}",
                safe_haven_demand=safe_haven,
                risk_currencies_warning='Avoid AUD, NZD, CAD during elevated geopolitical risk' if risk_level in ('high', 'extreme') else ''
            )
            
            self._update_cache(cache_key, analysis.model_dump(), ttl_override=30)
            logger.info(f"✅ Deep Geopolitical Research complete - Risk Level: {analysis.risk_level.upper()}")
            return analysis
            
        except Exception as e:
            logger.error(f"Error in geopolitical deep research: {e}")
            return None
    
    async def research_central_bank_policy(self) -> Optional[CentralBankAnalysis]:
        """
        Use Firecrawl Agent for deep central bank policy research.
        
        Analyzes Fed, ECB, BOE, BOJ policy statements and rate expectations.
        
        Returns:
            CentralBankAnalysis schema with structured policy data
        """
        cache_key = "deep_central_bank"
        if self._is_cache_valid(cache_key, ttl_override=30):
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return CentralBankAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            return None
        
        try:
            logger.info("🔍 Starting Deep Research: Central Bank Policy Analysis (via search)...")
            
            # Use search instead of agent to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query="Federal Reserve ECB BOE BOJ monetary policy rate decision interest rate 2026",
                    limit=5
                )
            )
            
            normalized = self._normalize_search_results(results)
            all_text = ' '.join([
                f"{item.get('title', '')} {item.get('description', '')}"
                for item in normalized
            ]).lower()
            
            # Detect stances from search text
            def detect_stance(text: str, bank_keywords: list) -> str:
                bank_text = ' '.join([s for s in text.split('.') if any(kw in s.lower() for kw in bank_keywords)])
                if any(w in bank_text for w in ['hawkish', 'hike', 'tighten', 'raising']):
                    return 'hawkish'
                elif any(w in bank_text for w in ['dovish', 'cut', 'easing', 'lower']):
                    return 'dovish'
                return 'neutral'
            
            fed_stance = detect_stance(all_text, ['fed', 'federal reserve', 'powell', 'fomc'])
            ecb_stance = detect_stance(all_text, ['ecb', 'european central bank', 'lagarde'])
            boe_stance = detect_stance(all_text, ['boe', 'bank of england', 'bailey'])
            boj_stance = detect_stance(all_text, ['boj', 'bank of japan', 'ueda'])
            
            # Build summary from search headlines
            headlines = [item.get('title', '') for item in normalized if item.get('title')]
            summary = '; '.join(headlines[:3]) if headlines else 'No recent policy updates found'
            
            analysis = CentralBankAnalysis(
                fed=CentralBankStance(
                    bank='Federal Reserve', stance=fed_stance, current_rate=None,
                    expected_action='hold', key_statement=summary[:200],
                    currency_impact='bullish USD' if fed_stance == 'hawkish' else ('bearish USD' if fed_stance == 'dovish' else 'neutral')
                ),
                ecb=CentralBankStance(
                    bank='ECB', stance=ecb_stance, current_rate=None,
                    expected_action='hold', key_statement='',
                    currency_impact='bullish EUR' if ecb_stance == 'hawkish' else ('bearish EUR' if ecb_stance == 'dovish' else 'neutral')
                ),
                boe=CentralBankStance(
                    bank='Bank of England', stance=boe_stance, current_rate=None,
                    expected_action='hold', key_statement='',
                    currency_impact='neutral'
                ),
                boj=CentralBankStance(
                    bank='Bank of Japan', stance=boj_stance, current_rate=None,
                    expected_action='hold', key_statement='',
                    currency_impact='neutral'
                ),
                divergence_plays=[],
                overall_bias='mixed'
            )
            
            self._update_cache(cache_key, analysis.model_dump(), ttl_override=30)
            logger.info(f"✅ Deep Central Bank Research complete - Overall Bias: {analysis.overall_bias}")
            return analysis
            
        except Exception as e:
            logger.error(f"Error in central bank deep research: {e}")
            return None
    
    async def research_intermarket_correlations(self) -> Optional[IntermarketAnalysis]:
        """
        Use Firecrawl Agent for deep intermarket correlation analysis.
        
        Analyzes SPX, VIX, DXY, Gold, Oil, and bond yields for risk sentiment.
        
        Returns:
            IntermarketAnalysis schema with correlation data
        """
        cache_key = "deep_intermarket"
        if self._is_cache_valid(cache_key, ttl_override=30):
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return IntermarketAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            return None
        
        try:
            logger.info("🔍 Starting Deep Research: Intermarket Correlations (via search)...")
            
            # Use search instead of agent to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query="SPX VIX DXY gold oil intermarket correlation risk sentiment forex 2026",
                    limit=5
                )
            )
            
            normalized = self._normalize_search_results(results)
            all_text = ' '.join([
                f"{item.get('title', '')} {item.get('description', '')}"
                for item in normalized
            ]).lower()
            
            # Detect risk environment from search text
            if any(w in all_text for w in ['sell-off', 'crash', 'panic', 'fear', 'plunge']):
                risk_env = 'strong_risk_off'
            elif any(w in all_text for w in ['risk off', 'safe haven', 'decline', 'bearish']):
                risk_env = 'risk_off'
            elif any(w in all_text for w in ['rally', 'surge', 'record high', 'bullish', 'risk on']):
                risk_env = 'risk_on'
            else:
                risk_env = 'neutral'
            
            implications = []
            if risk_env in ('risk_off', 'strong_risk_off'):
                implications = ['Favor JPY, CHF, USD safe havens', 'Reduce AUD, NZD, CAD exposure']
            elif risk_env == 'risk_on':
                implications = ['Favor AUD, NZD, CAD risk currencies', 'Reduce JPY, CHF positions']
            else:
                implications = ['Mixed signals — trade individual setups']
            
            analysis = IntermarketAnalysis(
                spx=MarketTrend(market='S&P 500', trend='neutral', current_value=None, change_percent=None),
                vix=MarketTrend(market='VIX', trend='neutral', current_value=None, change_percent=None),
                dxy=MarketTrend(market='DXY', trend='neutral', current_value=None, change_percent=None),
                gold=MarketTrend(market='Gold', trend='neutral', current_value=None, change_percent=None),
                oil=MarketTrend(market='Oil', trend='neutral', current_value=None, change_percent=None),
                risk_environment=risk_env,
                correlations_normal=True,
                anomalies=[],
                trading_implications=implications
            )
            
            self._update_cache(cache_key, analysis.model_dump(), ttl_override=30)
            logger.info(f"✅ Deep Intermarket Research complete - Risk Environment: {analysis.risk_environment}")
            return analysis
            
        except Exception as e:
            logger.error(f"Error in intermarket deep research: {e}")
            return None
    
    async def research_symbol_fundamentals(self, symbol: str) -> Optional[SymbolFundamentals]:
        """
        Use Firecrawl Agent for deep symbol-specific fundamental analysis.
        
        Args:
            symbol: Trading symbol (e.g., EURUSD, GBPUSD)
            
        Returns:
            SymbolFundamentals schema with fundamental data
        """
        cache_key = f"deep_fundamentals_{symbol.lower()}"
        if self._is_cache_valid(cache_key, ttl_override=30):
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return SymbolFundamentals(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            return None
        
        try:
            logger.info(f"🔍 Starting Deep Research: {symbol} Fundamentals (via search)...")
            
            # Determine base and quote currencies
            base = symbol[:3].upper()
            quote = symbol[3:6].upper() if len(symbol) >= 6 else "USD"
            
            # Use search instead of agent to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query=f"{symbol} fundamental analysis outlook forecast {base} {quote} 2026",
                    limit=5
                )
            )
            
            normalized = self._normalize_search_results(results)
            all_text = ' '.join([
                f"{item.get('title', '')} {item.get('description', '')}"
                for item in normalized
            ]).lower()
            
            # Extract key drivers from headlines
            key_drivers = [item.get('title', '')[:100] for item in normalized if item.get('title')][:5]
            
            # Detect bias from search text
            bullish_words = ['bullish', 'rally', 'surge', 'strong', 'upside', 'buy']
            bearish_words = ['bearish', 'decline', 'weak', 'downside', 'sell', 'drop']
            bull_count = sum(1 for w in bullish_words if w in all_text)
            bear_count = sum(1 for w in bearish_words if w in all_text)
            
            if bull_count > bear_count + 1:
                bias = 'bullish'
            elif bear_count > bull_count + 1:
                bias = 'bearish'
            else:
                bias = 'neutral'
            
            analysis = SymbolFundamentals(
                symbol=symbol,
                base_currency=base,
                quote_currency=quote,
                fundamental_bias=bias,
                key_drivers=key_drivers,
                upcoming_events=[],
                rate_differential=None,
                rate_differential_trend='stable',
                economic_strength_comparison='',
                trade_recommendation=f"Fundamental outlook: {bias} for {symbol}",
                confidence=50.0
            )
            
            self._update_cache(cache_key, analysis.model_dump(), ttl_override=30)
            logger.info(f"✅ Deep {symbol} Fundamentals complete - Bias: {analysis.fundamental_bias}")
            return analysis
            
        except Exception as e:
            logger.error(f"Error in {symbol} fundamental deep research: {e}")
            return None
    
    # =========================================================================
    # DEEP RESEARCH CACHE GETTERS
    # =========================================================================
    
    def get_cached_geopolitical(self) -> Optional[GeopoliticalAnalysis]:
        """Get cached geopolitical analysis (non-blocking)."""
        cached = self._cache.get("deep_geopolitical")
        if cached and not cached.is_expired() and cached.data:
            return GeopoliticalAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    def get_cached_central_bank(self) -> Optional[CentralBankAnalysis]:
        """Get cached central bank analysis (non-blocking)."""
        cached = self._cache.get("deep_central_bank")
        if cached and not cached.is_expired() and cached.data:
            return CentralBankAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    def get_cached_intermarket(self) -> Optional[IntermarketAnalysis]:
        """Get cached intermarket analysis (non-blocking)."""
        cached = self._cache.get("deep_intermarket")
        if cached and not cached.is_expired() and cached.data:
            return IntermarketAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    def get_cached_symbol_fundamentals(self, symbol: str) -> Optional[SymbolFundamentals]:
        """Get cached symbol fundamentals (non-blocking)."""
        cached = self._cache.get(f"deep_fundamentals_{symbol.lower()}")
        if cached and not cached.is_expired() and cached.data:
            return SymbolFundamentals(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    def get_comprehensive_intelligence(self, symbol: str = "EURUSD") -> MarketIntelligence:
        """
        Get comprehensive market intelligence from all deep research sources.
        
        This combines all cached deep research into a single MarketIntelligence object
        that can be used for Claude's context or dashboard display.
        
        Args:
            symbol: Symbol for symbol-specific data
            
        Returns:
            MarketIntelligence with all available data
        """
        geopolitical = self.get_cached_geopolitical()
        central_banks = self.get_cached_central_bank()
        intermarket = self.get_cached_intermarket()
        
        # Determine overall risk level
        overall_risk = "normal"
        warnings = []
        key_themes = []
        
        if geopolitical:
            if geopolitical.risk_level in ["high", "extreme"]:
                overall_risk = "elevated" if geopolitical.risk_level == "high" else "high"
                warnings.append(f"Geopolitical risk: {geopolitical.risk_level.upper()}")
            key_themes.extend([e.headline[:50] for e in geopolitical.events[:2]])
        
        if intermarket:
            if intermarket.risk_environment in ["strong_risk_off", "risk_off"]:
                if overall_risk == "normal":
                    overall_risk = "elevated"
                warnings.append(f"Risk-off environment detected")
                key_themes.append(f"Risk sentiment: {intermarket.risk_environment}")
            if not intermarket.correlations_normal:
                warnings.append("Correlation anomalies detected")
        
        if central_banks and central_banks.divergence_plays:
            key_themes.append(f"CB divergence: {', '.join(central_banks.divergence_plays[:2])}")
        
        # Determine trading environment
        trading_environment = "normal"
        if overall_risk == "high" or len(warnings) >= 3:
            trading_environment = "difficult"
        elif overall_risk == "elevated":
            trading_environment = "caution"
        elif overall_risk == "low" and not warnings:
            trading_environment = "good"
        
        return MarketIntelligence(
            geopolitical=geopolitical,
            central_banks=central_banks,
            intermarket=intermarket,
            overall_risk_level=overall_risk,
            trading_environment=trading_environment,
            key_themes=key_themes[:5],
            warnings=warnings
        )
    
    def _is_cache_valid(self, key: str, ttl_override: Optional[int] = None) -> bool:
        """Check if cache entry is valid."""
        if key not in self._cache:
            return False
        
        cache_entry = self._cache[key]
        
        # Use custom TTL if provided
        if ttl_override:
            return datetime.now() - cache_entry.timestamp < timedelta(minutes=ttl_override)
        
        return not cache_entry.is_expired()
    
    def _update_cache(self, key: str, data: Any, ttl_override: Optional[int] = None):
        """Update cache with new data."""
        ttl = ttl_override if ttl_override else self.refresh_minutes
        self._cache[key] = IntelligenceCache(
            data=data,
            timestamp=datetime.now(),
            ttl_minutes=ttl
        )
    
    # =========================================================================
    # EXTRACT METHODS - Structured Data from Known Sources
    # =========================================================================
    
    async def extract_economic_calendar(self) -> Optional[EconomicCalendar]:
        """
        Use Firecrawl Extract for structured economic calendar data.
        
        Extracts high and medium impact events from economic calendar sources.
        
        Returns:
            EconomicCalendar schema with structured event data
        """
        cache_key = "extract_calendar"
        if self._is_cache_valid(cache_key, ttl_override=15):  # 15 min TTL
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return EconomicCalendar(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            return None
        
        try:
            logger.info("📅 Fetching Economic Calendar (via search)...")
            
            # Use search instead of extract to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query="forex high impact economic events this week FOMC NFP CPI GDP central bank decision",
                    limit=5
                )
            )
            
            normalized = self._normalize_search_results(results)
            
            # Parse events from search results
            events = []
            high_impact_keywords = ['fomc', 'nfp', 'non-farm', 'cpi', 'gdp', 'rate decision', 'inflation', 'employment']
            
            for item in normalized:
                title = item.get('title', '')
                desc = item.get('description', '')[:200]
                
                if title:
                    impact = 'high' if any(kw in title.lower() for kw in high_impact_keywords) else 'medium'
                    events.append(EconomicCalendarEvent(
                        datetime='',
                        currency='',
                        event=title[:100],
                        impact=impact,
                        forecast=None,
                        previous=None
                    ))
            
            high_impact = [e for e in events if e.impact.lower() == 'high']
            next_major = high_impact[0] if high_impact else None
            
            calendar = EconomicCalendar(
                events=events,
                high_impact_count=len(high_impact),
                next_major_event=next_major,
                blackout_periods=[]
            )
            
            self._update_cache(cache_key, calendar.model_dump(), ttl_override=15)
            logger.info(f"✅ Economic Calendar extracted - {len(events)} events, {len(high_impact)} high-impact")
            return calendar
            
        except Exception as e:
            logger.error(f"Error extracting economic calendar: {e}")
            return None
    
    async def extract_cot_positioning(self) -> Optional[COTAnalysis]:
        """
        Use Firecrawl Extract for COT (Commitment of Traders) data.
        
        Returns:
            COTAnalysis schema with institutional positioning
        """
        cache_key = "extract_cot"
        if self._is_cache_valid(cache_key, ttl_override=60):  # 60 min TTL (COT updates weekly)
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return COTAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            return None
        
        try:
            logger.info("📊 Fetching COT Positioning Data (via search)...")
            
            # Use search instead of extract to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query="COT commitment of traders latest forex speculative positioning EUR GBP JPY 2026",
                    limit=3
                )
            )
            
            normalized = self._normalize_search_results(results)
            all_text = ' '.join([
                f"{item.get('title', '')} {item.get('description', '')}"
                for item in normalized
            ]).lower()
            
            # Parse key insights from headlines
            key_insights = [item.get('title', '')[:100] for item in normalized if item.get('title')][:5]
            
            # Build basic positioning from search text for major currencies
            currencies = []
            for currency in ['EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD']:
                if currency.lower() in all_text:
                    # Detect positioning from text context
                    if any(w in all_text for w in [f'{currency.lower()} long', f'{currency.lower()} bullish', f'buy {currency.lower()}']):
                        pos = 'long'
                    elif any(w in all_text for w in [f'{currency.lower()} short', f'{currency.lower()} bearish', f'sell {currency.lower()}']):
                        pos = 'short'
                    else:
                        pos = 'neutral'
                    
                    currencies.append(InstitutionalPositioning(
                        currency=currency,
                        net_position=0,
                        change_weekly=0,
                        positioning=pos,
                        extreme_level=False,
                        interpretation=f"Positioning derived from search results"
                    ))
            
            cot = COTAnalysis(
                currencies=currencies,
                key_insights=key_insights,
                reversal_signals=[]
            )
            
            self._update_cache(cache_key, cot.model_dump(), ttl_override=60)
            logger.info(f"✅ COT Data extracted - {len(currencies)} currencies")
            return cot
            
        except Exception as e:
            logger.error(f"Error extracting COT data: {e}")
            return None
    
    async def extract_rate_expectations(self) -> Optional[RateExpectations]:
        """
        Use Firecrawl Extract for interest rate expectations.
        
        Returns:
            RateExpectations schema with rate forecasts
        """
        cache_key = "extract_rates"
        if self._is_cache_valid(cache_key, ttl_override=30):
            cached = self._cache.get(cache_key)
            if cached and cached.data:
                return RateExpectations(**cached.data) if isinstance(cached.data, dict) else cached.data
        
        if not self.is_available:
            return None
        
        try:
            logger.info("💰 Fetching Interest Rate Expectations (via search)...")
            
            # Use search instead of extract to save credits
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    query="Fed funds rate probability expectations CME FedWatch rate cut hike 2026",
                    limit=3
                )
            )
            
            normalized = self._normalize_search_results(results)
            all_text = ' '.join([
                f"{item.get('title', '')} {item.get('description', '')}"
                for item in normalized
            ]).lower()
            
            # Detect Fed stance from search text
            if any(w in all_text for w in ['rate cut', 'cutting', 'dovish pivot', 'easing']):
                fed_impact = 'bearish USD — rate cuts expected'
                cut_prob = 60.0
                hold_prob = 30.0
                hike_prob = 10.0
            elif any(w in all_text for w in ['rate hike', 'raising', 'hawkish', 'tightening']):
                fed_impact = 'bullish USD — rate hikes expected'
                cut_prob = 10.0
                hold_prob = 30.0
                hike_prob = 60.0
            else:
                fed_impact = 'neutral — rates on hold'
                cut_prob = 20.0
                hold_prob = 60.0
                hike_prob = 20.0
            
            rates = RateExpectations(
                fed=RateExpectation(
                    bank='Federal Reserve', currency='USD', current_rate=0.0,
                    next_meeting_date=None, expected_rate=None,
                    hike_probability=hike_prob, cut_probability=cut_prob,
                    hold_probability=hold_prob, terminal_rate=None,
                    currency_impact=fed_impact
                ),
                ecb=RateExpectation(
                    bank='ECB', currency='EUR', current_rate=0.0,
                    next_meeting_date=None, expected_rate=None,
                    hike_probability=20.0, cut_probability=20.0,
                    hold_probability=60.0, terminal_rate=None,
                    currency_impact='neutral'
                ),
                rate_differentials={},
                carry_trade_outlook='Based on search results; check CME FedWatch for precise probabilities'
            )
            
            self._update_cache(cache_key, rates.model_dump(), ttl_override=30)
            logger.info("✅ Rate Expectations extracted")
            return rates
            
        except Exception as e:
            logger.error(f"Error extracting rate expectations: {e}")
            return None
    
    def get_cached_economic_calendar(self) -> Optional[EconomicCalendar]:
        """Get cached economic calendar (non-blocking)."""
        cached = self._cache.get("extract_calendar")
        if cached and not cached.is_expired() and cached.data:
            return EconomicCalendar(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    def get_cached_cot(self) -> Optional[COTAnalysis]:
        """Get cached COT analysis (non-blocking)."""
        cached = self._cache.get("extract_cot")
        if cached and not cached.is_expired() and cached.data:
            return COTAnalysis(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    def get_cached_rate_expectations(self) -> Optional[RateExpectations]:
        """Get cached rate expectations (non-blocking)."""
        cached = self._cache.get("extract_rates")
        if cached and not cached.is_expired() and cached.data:
            return RateExpectations(**cached.data) if isinstance(cached.data, dict) else cached.data
        return None
    
    async def get_central_bank_sentiment(self) -> Dict[str, Any]:
        """
        Monitor central bank communications tone.
        
        Returns:
            Dict with Fed and ECB sentiment
        """
        cache_key = "cb_sentiment"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"fed": "unknown", "ecb": "unknown"}
        
        try:
            # Search for recent Fed/ECB statements
            results = self.client.search(
                "Federal Reserve ECB monetary policy statement rate decision 2026",
                limit=3
            )
            
            sentiment = self._parse_cb_sentiment(results)
            self._update_cache(cache_key, sentiment)
            return sentiment
            
        except Exception as e:
            logger.error(f"Error fetching CB sentiment: {e}")
            return {"fed": "unknown", "ecb": "unknown"}
    
    # =========================================================================
    # NEW: ENHANCED INTELLIGENCE METHODS
    # =========================================================================
    
    async def get_retail_sentiment(self, symbol: str = "EURUSD") -> Dict[str, Any]:
        """
        Get retail trader positioning (USE AS CONTRARIAN INDICATOR).
        
        When retail is extremely long, consider shorting.
        When retail is extremely short, consider going long.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with retail positioning and contrarian signal
        """
        cache_key = f"retail_{symbol.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"bias": "unknown", "contrarian_signal": "unknown"}
        
        try:
            # Search for retail sentiment
            results = self.client.search(
                f"{symbol} retail sentiment positioning trader outlook 2026",
                limit=3
            )
            
            sentiment = self._parse_retail_sentiment(results, symbol)
            self._update_cache(cache_key, sentiment)
            return sentiment
            
        except Exception as e:
            logger.error(f"Error fetching retail sentiment for {symbol}: {e}")
            return {"bias": "unknown", "contrarian_signal": "unknown", "error": str(e)}
    
    async def get_currency_strength(self) -> Dict[str, Any]:
        """
        Get currency strength rankings.
        
        Helps identify strong vs weak currency pairs for better setups.
        Trade: Strong currency vs Weak currency
        
        Returns:
            Dict with currency rankings
        """
        cache_key = "currency_strength"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"rankings": [], "strongest": None, "weakest": None}
        
        try:
            # Search for currency strength
            results = self.client.search(
                "forex currency strength meter live rankings USD EUR GBP JPY 2026",
                limit=3
            )
            
            strength = self._parse_currency_strength(results)
            self._update_cache(cache_key, strength)
            return strength
            
        except Exception as e:
            logger.error(f"Error fetching currency strength: {e}")
            return {"rankings": [], "error": str(e)}
    
    async def get_vix_sentiment(self) -> Dict[str, Any]:
        """
        Get VIX (Fear Index) reading for risk sentiment.
        
        VIX > 20: Risk-off (favor safe havens: JPY, CHF, Gold)
        VIX < 15: Risk-on (favor risk currencies: AUD, NZD)
        
        Returns:
            Dict with VIX reading and risk sentiment
        """
        cache_key = "vix"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"level": None, "sentiment": "unknown"}
        
        try:
            result = self.client.scrape(
                self.SOURCES["vix"],
                formats=["markdown"]
            )
            
            markdown = result.markdown if hasattr(result, 'markdown') else (result.get("markdown", "") if isinstance(result, dict) else "")
            vix_data = self._parse_vix_data(markdown)
            self._update_cache(cache_key, vix_data)
            return vix_data
            
        except Exception as e:
            logger.error(f"Error fetching VIX data: {e}")
            return {"level": None, "sentiment": "unknown", "error": str(e)}
    
    async def get_tradingview_technical(self, symbol: str = "EURUSD") -> Dict[str, Any]:
        """
        Get TradingView technical analysis consensus.
        
        Aggregates technical indicators into buy/sell/neutral signal.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with technical consensus
        """
        cache_key = f"tv_tech_{symbol.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"consensus": "unknown", "signal": "neutral"}
        
        try:
            # Search for TradingView technical analysis
            results = self.client.search(
                f"{symbol} TradingView technical analysis buy sell signal summary 2026",
                limit=3
            )
            
            tech_data = self._parse_tv_technical(results, symbol)
            self._update_cache(cache_key, tech_data)
            return tech_data
            
        except Exception as e:
            logger.error(f"Error fetching TV technical for {symbol}: {e}")
            return {"consensus": "unknown", "signal": "neutral", "error": str(e)}
    
    async def get_rate_expectations(self) -> Dict[str, Any]:
        """
        Get interest rate expectations from Fed funds futures.
        
        Rate hike probability -> USD bullish
        Rate cut probability -> USD bearish
        
        Returns:
            Dict with rate expectations
        """
        cache_key = "rate_expectations"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"fed": {"next_move": "unknown"}}
        
        try:
            results = self.client.search(
                "Fed funds futures rate expectations probability hike cut 2026",
                limit=3
            )
            
            rate_data = self._parse_rate_expectations(results)
            self._update_cache(cache_key, rate_data)
            return rate_data
            
        except Exception as e:
            logger.error(f"Error fetching rate expectations: {e}")
            return {"fed": {"next_move": "unknown"}, "error": str(e)}
    
    async def get_commodity_correlation(self, commodity: str = "oil") -> Dict[str, Any]:
        """
        Get commodity trend for correlated currency pairs.
        
        Oil bullish -> CAD bullish
        Gold bullish -> AUD bullish (also risk-off signal)
        
        Args:
            commodity: 'oil' or 'gold'
            
        Returns:
            Dict with commodity trend and currency implication
        """
        cache_key = f"commodity_{commodity.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"trend": "unknown", "currency_implication": {}}
        
        try:
            url = self.SOURCES.get(commodity.lower())
            if not url:
                return {"trend": "unknown", "error": "Unknown commodity"}
            
            result = self.client.scrape(url, formats=["markdown"])
            
            markdown = result.markdown if hasattr(result, 'markdown') else (result.get("markdown", "") if isinstance(result, dict) else "")
            commodity_data = self._parse_commodity_trend(markdown, commodity)
            self._update_cache(cache_key, commodity_data)
            return commodity_data
            
        except Exception as e:
            logger.error(f"Error fetching {commodity} data: {e}")
            return {"trend": "unknown", "error": str(e)}
    
    async def get_economic_calendar_today(self) -> List[Dict[str, Any]]:
        """
        Get today's high-impact economic events.
        
        Returns:
            List of economic events with impact and times
        """
        return await self.get_economic_calendar(days=1)
    
    async def get_economic_calendar(self, days: int = 90) -> List[Dict[str, Any]]:
        """
        Get high-impact economic events for the specified number of days.
        
        Args:
            days: Number of days to fetch (default 90 = 3 months)
        
        Returns:
            List of economic events with impact and times
        """
        cache_key = f"econ_calendar_{days}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return []
        
        events = []
        
        try:
            # Use search for fast results - avoid slow scraping
            time_range = "this week" if days <= 7 else f"next {days // 30} months" if days >= 30 else f"next {days} days"
            
            # Run search in thread pool to avoid blocking
            results = await asyncio.to_thread(
                lambda: self.client.search(
                    f"forex factory economic calendar {time_range} high impact news events USD EUR GBP JPY AUD CAD FOMC ECB Fed NFP CPI GDP PMI",
                    limit=15
                )
            )
            
            events = self._parse_economic_calendar(results)
            
            # Cache even if empty to prevent repeated slow calls
            self._update_cache(cache_key, events)
            return events
            
        except Exception as e:
            logger.error(f"Error fetching economic calendar: {e}")
            # Return empty list but cache it to avoid hammering on errors
            self._update_cache(cache_key, [])
            return []
    
    
    # =========================================================================
    # NEW: ADVANCED INTELLIGENCE SOURCES
    # =========================================================================
    
    async def get_twitter_forex_sentiment(self, symbol: str = "EURUSD") -> Dict[str, Any]:
        """
        Get forex sentiment from Twitter/X social media.
        
        Tracks trending forex discussions and sentiment.
        Can be used as contrarian or momentum indicator.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with social sentiment data
        """
        cache_key = f"twitter_{symbol.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"sentiment": "unknown", "volume": "unknown"}
        
        try:
            # Search for forex-related tweets/discussions
            base_currency = symbol[:3]
            quote_currency = symbol[3:] if len(symbol) >= 6 else "USD"
            
            results = self.client.search(
                f"{symbol} OR #{base_currency} forex trading sentiment analysis 2026",
                limit=5
            )
            
            sentiment_data = self._parse_social_sentiment(results, symbol)
            self._update_cache(cache_key, sentiment_data)
            return sentiment_data
            
        except Exception as e:
            logger.error(f"Error fetching Twitter sentiment for {symbol}: {e}")
            return {"sentiment": "unknown", "error": str(e)}
    
    async def get_options_flow(self, symbol: str = "EURUSD") -> Dict[str, Any]:
        """
        Get FX options expiry and flow data.
        
        Large option expiries act as magnets for price.
        Heavy call buying = bullish flow.
        Heavy put buying = bearish flow.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with options flow and expiry data
        """
        cache_key = f"options_{symbol.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"flow": "unknown", "expiries": []}
        
        try:
            results = self.client.search(
                f"{symbol} forex options expiry large strikes barriers 2026",
                limit=3
            )
            
            options_data = self._parse_options_flow(results, symbol)
            self._update_cache(cache_key, options_data)
            return options_data
            
        except Exception as e:
            logger.error(f"Error fetching options flow for {symbol}: {e}")
            return {"flow": "unknown", "error": str(e)}
    
    async def get_bond_yield_spread(self) -> Dict[str, Any]:
        """
        Get bond yield spreads for currency flow analysis.
        
        Higher US yields vs German yields = USD bullish vs EUR.
        Yield differentials drive carry trades.
        
        Returns:
            Dict with yield spread data and currency implications
        """
        cache_key = "bond_yields"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"spread": None, "implication": "unknown"}
        
        try:
            # Fetch US 10Y and DE 10Y yields
            # Use lambda to pass keyword args properly
            results = await asyncio.gather(
                asyncio.to_thread(
                    lambda: self.client.scrape(self.SOURCES["us10y"], formats=["markdown"])
                ),
                asyncio.to_thread(
                    lambda: self.client.scrape(self.SOURCES["de10y"], formats=["markdown"])
                ),
                return_exceptions=True
            )
            
            yield_data = self._parse_yield_spread(results)
            self._update_cache(cache_key, yield_data)
            return yield_data
            
        except Exception as e:
            logger.error(f"Error fetching bond yields: {e}")
            return {"spread": None, "implication": "unknown", "error": str(e)}
    
    async def get_btc_dominance(self) -> Dict[str, Any]:
        """
        Get Bitcoin dominance for crypto pair analysis.
        
        BTC.D rising = Money flowing to BTC from alts (altcoin bearish)
        BTC.D falling = Money flowing to alts from BTC (altcoin bullish)
        
        Returns:
            Dict with BTC dominance and crypto sentiment
        """
        cache_key = "btc_dominance"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"dominance": None, "trend": "unknown", "altcoin_sentiment": "unknown"}
        
        try:
            result = self.client.scrape(
                self.SOURCES["btc_dominance"],
                formats=["markdown"]
            )
            
            markdown = result.markdown if hasattr(result, 'markdown') else (result.get("markdown", "") if isinstance(result, dict) else "")
            btc_data = self._parse_btc_dominance(markdown)
            self._update_cache(cache_key, btc_data)
            return btc_data
            
        except Exception as e:
            logger.error(f"Error fetching BTC dominance: {e}")
            return {"dominance": None, "trend": "unknown", "error": str(e)}
    
    async def get_economic_surprise_index(self) -> Dict[str, Any]:
        """
        Get economic surprise index (actual vs expectations).
        
        Positive surprises = Currency bullish
        Negative surprises = Currency bearish
        
        Returns:
            Dict with surprise indices by region
        """
        cache_key = "econ_surprise"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"us": "unknown", "eu": "unknown", "uk": "unknown"}
        
        try:
            results = self.client.search(
                "Citi economic surprise index USD EUR GBP latest 2026",
                limit=3
            )
            
            surprise_data = self._parse_economic_surprise(results)
            self._update_cache(cache_key, surprise_data)
            return surprise_data
            
        except Exception as e:
            logger.error(f"Error fetching economic surprise: {e}")
            return {"us": "unknown", "error": str(e)}
    
    async def get_seasonal_pattern(self, symbol: str = "EURUSD") -> Dict[str, Any]:
        """
        Get historical seasonal patterns for the symbol.
        
        Many currencies show seasonal tendencies.
        E.g., USD often strengthens in Q4.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with seasonal pattern data
        """
        cache_key = f"seasonal_{symbol.lower()}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"current_month_bias": "unknown", "historical_accuracy": 0}
        
        try:
            current_month = datetime.now().strftime("%B")
            
            results = self.client.search(
                f"{symbol} forex seasonality {current_month} historical pattern tendency",
                limit=3
            )
            
            seasonal_data = self._parse_seasonal_pattern(results, symbol, current_month)
            self._update_cache(cache_key, seasonal_data)
            return seasonal_data
            
        except Exception as e:
            logger.error(f"Error fetching seasonal pattern for {symbol}: {e}")
            return {"current_month_bias": "unknown", "error": str(e)}
    
    async def get_intermarket_analysis(self) -> Dict[str, Any]:
        """
        Get intermarket correlations (SPX, VIX, DXY, Bonds, Gold).
        
        These relationships help confirm or warn against trades:
        - SPX up + VIX down = Risk-on (AUD/NZD bullish)
        - SPX down + VIX up = Risk-off (JPY/CHF bullish)
        - DXY + Gold inverse = Normal
        - DXY + Gold both up = Uncertainty
        
        Returns:
            Dict with intermarket analysis
        """
        cache_key = "intermarket"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key].data
        
        if not self.is_available:
            return {"risk_environment": "unknown", "correlations_normal": True}
        
        try:
            # Fetch SPX data
            spx_result = self.client.scrape(
                self.SOURCES["spx"],
                formats=["markdown"]
            )
            
            spx_markdown = spx_result.markdown if hasattr(spx_result, 'markdown') else (spx_result.get("markdown", "") if isinstance(spx_result, dict) else "")
            intermarket_data = self._parse_intermarket_analysis(spx_markdown)
            
            # Combine with cached VIX and DXY data
            vix = self._cache.get("vix")
            dxy = self._cache.get("dxy")
            gold = self._cache.get("commodity_gold")
            
            if vix and not vix.is_expired() and vix.data:
                intermarket_data["vix_sentiment"] = vix.data.get("sentiment", "unknown")
            if dxy and not dxy.is_expired() and dxy.data:
                intermarket_data["dxy_trend"] = dxy.data.get("trend", "unknown")
            if gold and not gold.is_expired() and gold.data:
                intermarket_data["gold_trend"] = gold.data.get("trend", "unknown")
            
            # Determine risk environment
            intermarket_data["risk_environment"] = self._determine_risk_environment(intermarket_data)
            
            self._update_cache(cache_key, intermarket_data)
            return intermarket_data
            
        except Exception as e:
            logger.error(f"Error fetching intermarket analysis: {e}")
            return {"risk_environment": "unknown", "error": str(e)}
    
    async def get_complete_analysis(self, symbol: str = "EURUSD") -> Dict[str, Any]:
        """
        Get comprehensive analysis combining ALL intelligence sources.
        
        This is the MASTER method for complete market context.
        Includes: DXY, VIX, retail sentiment, currency strength, TradingView,
        rates, commodities, social sentiment, options flow, bond yields,
        BTC dominance (for crypto), economic surprise, seasonality, and intermarket.
        
        Args:
            symbol: Primary trading symbol
            
        Returns:
            Comprehensive analysis dict
        """
        is_crypto = symbol in ['BTCUSD', 'ETHUSD', 'XRPUSD', 'ADAUSD', 'SOLUSD']
        
        # Core intelligence
        core_results = await asyncio.gather(
            self.get_dxy_analysis(),
            self.get_vix_sentiment(),
            self.get_retail_sentiment(symbol),
            self.get_currency_strength(),
            self.get_tradingview_technical(symbol),
            self.get_rate_expectations(),
            return_exceptions=True
        )
        
        dxy, vix, retail, strength, tv_tech, rates = core_results
        
        # Commodity correlations
        commodity_results = await asyncio.gather(
            self.get_commodity_correlation("oil"),
            self.get_commodity_correlation("gold"),
            return_exceptions=True
        )
        oil, gold = commodity_results
        
        # Advanced intelligence
        advanced_results = await asyncio.gather(
            self.get_twitter_forex_sentiment(symbol),
            self.get_options_flow(symbol),
            self.get_bond_yield_spread(),
            self.get_economic_surprise_index(),
            self.get_seasonal_pattern(symbol),
            self.get_intermarket_analysis(),
            return_exceptions=True
        )
        twitter, options, yields, surprise, seasonal, intermarket = advanced_results
        
        # Crypto-specific
        btc_dom = None
        if is_crypto:
            btc_dom = await self.get_btc_dominance()
        
        # Compute overall bias from all sources
        bullish_signals = 0
        bearish_signals = 0
        
        # DXY impact on symbol (inverse for EUR/GBP)
        if isinstance(dxy, dict):
            is_usd_counter = symbol.startswith("EUR") or symbol.startswith("GBP") or symbol.startswith("AUD")
            if is_usd_counter:
                if dxy.get("trend") == "bearish":
                    bullish_signals += 1
                elif dxy.get("trend") == "bullish":
                    bearish_signals += 1
            else:
                if dxy.get("trend") == "bullish":
                    bullish_signals += 1
                elif dxy.get("trend") == "bearish":
                    bearish_signals += 1
        
        # Retail contrarian (IMPORTANT - trade against retail)
        if isinstance(retail, dict):
            if retail.get("contrarian_signal") == "long":
                bullish_signals += 2  # Double weight
            elif retail.get("contrarian_signal") == "short":
                bearish_signals += 2
        
        # Social sentiment contrarian
        if isinstance(twitter, dict):
            if twitter.get("contrarian_signal") == "long":
                bullish_signals += 1
            elif twitter.get("contrarian_signal") == "short":
                bearish_signals += 1
        
        # TradingView technical
        if isinstance(tv_tech, dict):
            if tv_tech.get("signal") == "buy":
                bullish_signals += 1
            elif tv_tech.get("signal") == "sell":
                bearish_signals += 1
        
        # Options flow
        if isinstance(options, dict):
            if options.get("flow") == "bullish":
                bullish_signals += 1
            elif options.get("flow") == "bearish":
                bearish_signals += 1
        
        # Bond yields (for EURUSD)
        if isinstance(yields, dict) and "EUR" in symbol:
            if yields.get("eurusd_bias") == "bullish":
                bullish_signals += 1
            elif yields.get("eurusd_bias") == "bearish":
                bearish_signals += 1
        
        # Economic surprise
        if isinstance(surprise, dict) and "EUR" in symbol:
            if surprise.get("eurusd_bias") == "bullish":
                bullish_signals += 1
            elif surprise.get("eurusd_bias") == "bearish":
                bearish_signals += 1
        
        # Seasonal pattern
        if isinstance(seasonal, dict):
            if seasonal.get("current_month_bias") == "bullish" and seasonal.get("historical_accuracy", 0) > 60:
                bullish_signals += 1
            elif seasonal.get("current_month_bias") == "bearish" and seasonal.get("historical_accuracy", 0) > 60:
                bearish_signals += 1
        
        # Intermarket (risk environment)
        if isinstance(intermarket, dict):
            risk_env = intermarket.get("risk_environment", "")
            if "risk_on" in risk_env and symbol in ['AUDUSD', 'NZDUSD']:
                bullish_signals += 1
            elif "risk_off" in risk_env and symbol in ['USDJPY', 'USDCHF']:
                bearish_signals += 1
        
        # BTC dominance for crypto
        if is_crypto and isinstance(btc_dom, dict):
            if symbol == 'BTCUSD':
                if btc_dom.get("trend") == "rising":
                    bullish_signals += 1
                elif btc_dom.get("trend") == "falling":
                    bearish_signals += 1
            else:  # Altcoins
                if btc_dom.get("altcoin_sentiment") == "bullish":
                    bullish_signals += 1
                elif btc_dom.get("altcoin_sentiment") == "bearish":
                    bearish_signals += 1
        
        # Determine overall bias
        total_signals = bullish_signals + bearish_signals
        if total_signals > 0:
            bias_strength = abs(bullish_signals - bearish_signals) / total_signals
        else:
            bias_strength = 0
        
        if bullish_signals > bearish_signals + 2:
            overall_bias = "strong_bullish"
        elif bullish_signals > bearish_signals:
            overall_bias = "bullish"
        elif bearish_signals > bullish_signals + 2:
            overall_bias = "strong_bearish"
        elif bearish_signals > bullish_signals:
            overall_bias = "bearish"
        else:
            overall_bias = "neutral"
        
        return {
            "symbol": symbol,
            "overall_bias": overall_bias,
            "bias_strength": bias_strength,
            "bullish_signals": bullish_signals,
            "bearish_signals": bearish_signals,
            "total_signals": total_signals,
            # Core Intelligence
            "dxy": dxy if isinstance(dxy, dict) else {"error": str(dxy)},
            "vix": vix if isinstance(vix, dict) else {"error": str(vix)},
            "retail_sentiment": retail if isinstance(retail, dict) else {"error": str(retail)},
            "currency_strength": strength if isinstance(strength, dict) else {"error": str(strength)},
            "tradingview_technical": tv_tech if isinstance(tv_tech, dict) else {"error": str(tv_tech)},
            "rate_expectations": rates if isinstance(rates, dict) else {"error": str(rates)},
            # Commodities
            "oil_correlation": oil if isinstance(oil, dict) else {"error": str(oil)},
            "gold_correlation": gold if isinstance(gold, dict) else {"error": str(gold)},
            # Advanced Intelligence
            "social_sentiment": twitter if isinstance(twitter, dict) else {"error": str(twitter)},
            "options_flow": options if isinstance(options, dict) else {"error": str(options)},
            "bond_yields": yields if isinstance(yields, dict) else {"error": str(yields)},
            "economic_surprise": surprise if isinstance(surprise, dict) else {"error": str(surprise)},
            "seasonal_pattern": seasonal if isinstance(seasonal, dict) else {"error": str(seasonal)},
            "intermarket": intermarket if isinstance(intermarket, dict) else {"error": str(intermarket)},
            # Crypto-specific
            "btc_dominance": btc_dom if isinstance(btc_dom, dict) else None,
            # Metadata
            "timestamp": datetime.now().isoformat(),
            "is_crypto": is_crypto
        }
    
    def get_market_context_for_claude(self, symbol: str = "EURUSD") -> str:
        """
        Compile all intelligence into context string for Claude's prompt.
        
        This is the main method called by main.py to add to market_data.
        
        Args:
            symbol: Trading symbol for symbol-specific context
            
        Returns:
            Context string for Claude
        """
        if not self.is_available:
            return ""
        
        context_parts = []
        
        # === DXY (Dollar Index) ===
        dxy = self._cache.get("dxy")
        if dxy and not dxy.is_expired() and dxy.data:
            context_parts.append(f"### Dollar Index (DXY)")
            context_parts.append(f"- Trend: {dxy.data.get('trend', 'unknown').upper()}")
            if dxy.data.get('bias'):
                context_parts.append(f"- USD Bias: {dxy.data.get('bias')}")
        
        # === VIX (Risk Sentiment) ===
        vix = self._cache.get("vix")
        if vix and not vix.is_expired() and vix.data:
            context_parts.append(f"\n### Risk Sentiment (VIX)")
            context_parts.append(f"- Level: {vix.data.get('level', 'N/A')}")
            context_parts.append(f"- Sentiment: {vix.data.get('sentiment', 'unknown').upper()}")
            context_parts.append(f"- Mode: {vix.data.get('risk_mode', 'unknown').upper()}")
            if vix.data.get('note'):
                context_parts.append(f"- ⚠️ {vix.data.get('note')}")
        
        # === Retail Sentiment (CONTRARIAN) ===
        retail_key = f"retail_{symbol.lower()}"
        retail = self._cache.get(retail_key)
        if retail and not retail.is_expired() and retail.data:
            context_parts.append(f"\n### 🔄 Retail Sentiment (TRADE AGAINST)")
            context_parts.append(f"- Retail Bias: {retail.data.get('bias', 'unknown').upper()}")
            context_parts.append(f"- CONTRARIAN Signal: **{retail.data.get('contrarian_signal', 'neutral').upper()}**")
            if retail.data.get('note'):
                context_parts.append(f"- {retail.data.get('note')}")
        
        # === Currency Strength ===
        strength = self._cache.get("currency_strength")
        if strength and not strength.is_expired() and strength.data:
            context_parts.append(f"\n### Currency Strength")
            if strength.data.get('strongest'):
                context_parts.append(f"- Strongest: {strength.data.get('strongest')}")
            if strength.data.get('weakest'):
                context_parts.append(f"- Weakest: {strength.data.get('weakest')}")
            if strength.data.get('recommendation'):
                context_parts.append(f"- 💡 {strength.data.get('recommendation')}")
        
        # === TradingView Technical ===
        tv_key = f"tv_tech_{symbol.lower()}"
        tv_tech = self._cache.get(tv_key)
        if tv_tech and not tv_tech.is_expired() and tv_tech.data:
            context_parts.append(f"\n### TradingView Technical ({symbol})")
            context_parts.append(f"- Consensus: {tv_tech.data.get('consensus', 'unknown').upper()}")
            context_parts.append(f"- Signal: {tv_tech.data.get('signal', 'neutral').upper()}")
        
        # === Rate Expectations ===
        rates = self._cache.get("rate_expectations")
        if rates and not rates.is_expired() and rates.data:
            fed_data = rates.data.get('fed', {})
            if fed_data.get('next_move') != 'unknown':
                context_parts.append(f"\n### Interest Rate Expectations")
                context_parts.append(f"- Fed Next Move: {fed_data.get('next_move', 'unknown').upper()}")
                if fed_data.get('usd_impact'):
                    context_parts.append(f"- USD Impact: {fed_data.get('usd_impact', 'neutral').upper()}")
        
        # === COT (Institutional Positioning) ===
        cot_parts = []
        for key, cache in self._cache.items():
            if key.startswith("cot_") and not cache.is_expired():
                currency = key.replace("cot_", "").upper()
                pos = cache.data.get('positioning', 'unknown')
                sentiment = cache.data.get('sentiment', '')
                cot_parts.append(f"- {currency}: {pos} ({sentiment})")
        
        if cot_parts:
            context_parts.append(f"\n### Institutional Positioning (COT)")
            context_parts.extend(cot_parts)
        
        # === Central Bank Sentiment ===
        cb = self._cache.get("cb_sentiment")
        if cb and not cb.is_expired() and cb.data:
            fed_tone = cb.data.get("fed", "unknown")
            ecb_tone = cb.data.get("ecb", "unknown")
            if fed_tone != "unknown" or ecb_tone != "unknown":
                context_parts.append(f"\n### Central Bank Tone")
                if fed_tone != "unknown":
                    context_parts.append(f"- Fed: {fed_tone.upper()}")
                if ecb_tone != "unknown":
                    context_parts.append(f"- ECB: {ecb_tone.upper()}")
        
        # === Commodity Correlations ===
        oil = self._cache.get("commodity_oil")
        gold = self._cache.get("commodity_gold")
        if (oil and not oil.is_expired()) or (gold and not gold.is_expired()):
            context_parts.append(f"\n### Commodity Correlations")
            if oil and not oil.is_expired() and oil.data:
                context_parts.append(f"- Oil Trend: {oil.data.get('trend', 'unknown').upper()}")
                impl = oil.data.get('currency_implication', {})
                if impl.get('pair_recommendation'):
                    context_parts.append(f"  -> {impl.get('pair_recommendation')}")
            if gold and not gold.is_expired() and gold.data:
                context_parts.append(f"- Gold Trend: {gold.data.get('trend', 'unknown').upper()}")
                impl = gold.data.get('currency_implication', {})
                if impl.get('pair_recommendation'):
                    context_parts.append(f"  -> {impl.get('pair_recommendation')}")
        
        # === Breaking News ===
        news = self._cache.get("breaking_news")
        if news and not news.is_expired() and news.data:
            context_parts.append("\n### Breaking News")
            for item in news.data[:3]:
                title = item.get('title', 'Unknown')[:80]
                context_parts.append(f"- {title}")
        
        # === Geopolitical Risk (CRITICAL for Fundamental Analysis) ===
        geo = self._cache.get("geopolitical_news")
        if geo and not geo.is_expired() and geo.data:
            risk_level = geo.data.get('risk_level', 'low')
            headlines = geo.data.get('headlines', [])
            if risk_level != 'low' or headlines:
                context_parts.append(f"\n### ⚠️ Geopolitical Risk: {risk_level.upper()}")
                if risk_level in ['high', 'extreme']:
                    context_parts.append(f"- 🚨 HIGH RISK - Consider REDUCED position sizes")
                    context_parts.append(f"- 🚨 Avoid holding through major news events")
                elif risk_level == 'medium':
                    context_parts.append(f"- ⚡ Moderate risk - Monitor positions closely")
                for h in headlines[:3]:
                    title = h.get('title', '')[:60]
                    source = h.get('source', 'Unknown')
                    if title:
                        context_parts.append(f"- [{source}] {title}")
        
        # === Economic Calendar ===
        econ = self._cache.get("econ_calendar")
        if econ and not econ.is_expired() and econ.data:
            context_parts.append("\n### ⚠️ High-Impact Events Today")
            for event in econ.data[:3]:
                context_parts.append(f"- {event.get('event', 'Unknown')}: {event.get('note', '')}")
        
        # === NEW: Social Sentiment ===
        twitter_key = f"twitter_{symbol.lower()}"
        twitter = self._cache.get(twitter_key)
        if twitter and not twitter.is_expired() and twitter.data:
            context_parts.append(f"\n### 🐦 Social Sentiment ({symbol})")
            context_parts.append(f"- Sentiment: {twitter.data.get('sentiment', 'unknown').upper()}")
            context_parts.append(f"- Volume: {twitter.data.get('volume', 'unknown')}")
            if twitter.data.get('note'):
                context_parts.append(f"- {twitter.data.get('note')}")
        
        # === NEW: Options Flow ===
        options_key = f"options_{symbol.lower()}"
        options = self._cache.get(options_key)
        if options and not options.is_expired() and options.data and options.data.get('flow') != 'neutral':
            context_parts.append(f"\n### 📊 Options Flow ({symbol})")
            context_parts.append(f"- Flow: {options.data.get('flow', 'unknown').upper()}")
            magnets = options.data.get('magnet_levels', [])
            if magnets:
                context_parts.append(f"- Magnet Levels: {', '.join(str(m) for m in magnets[:3])}")
        
        # === NEW: Bond Yield Spread ===
        yields = self._cache.get("bond_yields")
        if yields and not yields.is_expired() and yields.data and yields.data.get('spread') is not None:
            context_parts.append(f"\n### 📈 Bond Yield Spread")
            context_parts.append(f"- US 10Y: {yields.data.get('us_10y', 'N/A')}%")
            context_parts.append(f"- DE 10Y: {yields.data.get('de_10y', 'N/A')}%")
            context_parts.append(f"- Spread: {yields.data.get('spread', 0):.2f}%")
            context_parts.append(f"- EUR/USD Bias: {yields.data.get('eurusd_bias', 'neutral').upper()}")
        
        # === NEW: Economic Surprise ===
        surprise = self._cache.get("econ_surprise")
        if surprise and not surprise.is_expired() and surprise.data:
            us_surprise = surprise.data.get('us', 'unknown')
            eu_surprise = surprise.data.get('eu', 'unknown')
            if us_surprise != 'neutral' or eu_surprise != 'neutral':
                context_parts.append(f"\n### 📰 Economic Surprises")
                context_parts.append(f"- US Data: {us_surprise.upper()}")
                context_parts.append(f"- EU Data: {eu_surprise.upper()}")
        
        # === NEW: Seasonal Pattern ===
        seasonal_key = f"seasonal_{symbol.lower()}"
        seasonal = self._cache.get(seasonal_key)
        if seasonal and not seasonal.is_expired() and seasonal.data and seasonal.data.get('current_month_bias') != 'unknown':
            context_parts.append(f"\n### 📅 Seasonal Pattern ({symbol})")
            context_parts.append(f"- {seasonal.data.get('current_month', 'N/A')} Bias: {seasonal.data.get('current_month_bias', 'unknown').upper()}")
            if seasonal.data.get('historical_accuracy'):
                context_parts.append(f"- Historical Accuracy: {seasonal.data.get('historical_accuracy')}%")
            if seasonal.data.get('note'):
                context_parts.append(f"- 📝 {seasonal.data.get('note')}")
        
        # === NEW: Intermarket Analysis ===
        intermarket = self._cache.get("intermarket")
        if intermarket and not intermarket.is_expired() and intermarket.data:
            risk_env = intermarket.data.get('risk_environment', 'unknown')
            context_parts.append(f"\n### 🌐 Intermarket Analysis")
            context_parts.append(f"- SPX Trend: {intermarket.data.get('spx_trend', 'unknown').upper()}")
            context_parts.append(f"- Risk Environment: **{risk_env.upper().replace('_', ' ')}**")
            
            # Risk environment trading implications
            if 'risk_on' in risk_env:
                context_parts.append(f"- 💡 Favor: AUD, NZD, Risk assets")
            elif 'risk_off' in risk_env:
                context_parts.append(f"- 💡 Favor: JPY, CHF, Gold, Safe havens")
        
        # === NEW: BTC Dominance (for crypto) ===
        btc_dom = self._cache.get("btc_dominance")
        is_crypto_symbol = 'BTC' in symbol.upper() or 'ETH' in symbol.upper()
        if btc_dom and not btc_dom.is_expired() and is_crypto_symbol and btc_dom.data:
            context_parts.append(f"\n### ₿ BTC Dominance")
            context_parts.append(f"- Dominance: {btc_dom.data.get('dominance', 'N/A')}%")
            context_parts.append(f"- Trend: {btc_dom.data.get('trend', 'unknown').upper()}")
            context_parts.append(f"- Altcoin Sentiment: {btc_dom.data.get('altcoin_sentiment', 'unknown').upper()}")
            if btc_dom.data.get('note'):
                context_parts.append(f"- {btc_dom.data.get('note')}")
        
        if context_parts:
            return "## 📊 Real-Time Market Intelligence (Firecrawl)\n" + "\n".join(context_parts)
        return ""
    
    async def refresh_all(self, symbols: Optional[List[str]] = None):
        """
        Refresh ALL intelligence data (comprehensive).
        
        This performs a full refresh of all intelligence sources.
        Call every 15-30 minutes during trading hours.
        
        Args:
            symbols: List of symbols to get news for
        """
        if not self.is_available:
            logger.info("Firecrawl not available - skipping refresh")
            return
        
        symbols = symbols or ["EURUSD", "GBPUSD", "XAUUSD"]
        is_crypto = any(s in ['BTCUSD', 'ETHUSD', 'ADAUSD', 'XRPUSD', 'SOLUSD', 'DOGEUSD'] for s in symbols)
        
        # Prioritize key symbols for symbol-specific data
        # First add priority trading symbols (majors, metals, top crypto)
        priority_order = ['EURUSD', 'GBPUSD', 'XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD', 
                         'USDJPY', 'AUDUSD', 'USDCAD', 'XRPUSD', 'ADAUSD', 'SOLUSD',
                         'NZDUSD', 'USDCHF', 'DOGEUSD', 'LTCUSD']
        
        priority_symbols = [s for s in priority_order if s in symbols]
        
        # Add remaining symbols (limit to 30 total to balance coverage vs rate limits)
        for s in symbols:
            if s not in priority_symbols and len(priority_symbols) < 30:
                # Skip BTC-quoted pairs (less liquid, less retail data)
                if not s.endswith('BTC') and not s.endswith('BIT'):
                    priority_symbols.append(s)
        
        try:
            logger.info(f"🔄 Refreshing ALL Firecrawl intelligence for {len(priority_symbols)} symbols...")
            
            # TIER 1: Core market sentiment (highest priority) - global, not symbol-specific
            logger.debug("Refreshing Tier 1: Core sentiment...")
            await asyncio.gather(
                self.get_dxy_analysis(),
                self.get_vix_sentiment(),
                self.get_breaking_news(symbols[:10]),  # Limit news to 10 symbols
                self.get_geopolitical_news(),  # Geopolitical risk assessment
                self.get_intermarket_analysis(),
                return_exceptions=True
            )
            
            # TIER 2: Symbol-specific intelligence - FOR ALL PRIORITY SYMBOLS
            logger.info(f"Refreshing Tier 2: Symbol-specific for {len(priority_symbols)} symbols...")
            
            # Process in batches of 15 (Firecrawl supports up to 50 concurrent calls)
            batch_size = 15
            for i in range(0, len(priority_symbols), batch_size):
                batch = priority_symbols[i:i+batch_size]
                logger.debug(f"Processing symbol batch: {batch}")
                
                tasks = []
                for sym in batch:
                    tasks.extend([
                        self.get_retail_sentiment(sym),
                        self.get_tradingview_technical(sym),
                        self.get_twitter_forex_sentiment(sym),
                        self.get_options_flow(sym),
                        self.get_seasonal_pattern(sym),
                    ])
                
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Small delay between batches to avoid rate limits
                if i + batch_size < len(priority_symbols):
                    await asyncio.sleep(1)
            
            # TIER 3: Institutional and fundamental
            logger.debug("Refreshing Tier 3: Institutional/Fundamental...")
            await asyncio.gather(
                self.get_cot_positioning("EUR"),
                self.get_cot_positioning("GBP"),
                self.get_central_bank_sentiment(),
                self.get_rate_expectations(),
                self.get_bond_yield_spread(),
                self.get_economic_surprise_index(),
                return_exceptions=True
            )
            
            # TIER 4: Commodity correlations
            logger.debug("Refreshing Tier 4: Commodities...")
            await asyncio.gather(
                self.get_commodity_correlation("oil"),
                self.get_commodity_correlation("gold"),
                self.get_economic_calendar_today(),
                return_exceptions=True
            )
            
            # TIER 5: Currency strength
            logger.debug("Refreshing Tier 5: Currency strength...")
            await self.get_currency_strength()
            
            # TIER 6: Crypto-specific (if needed)
            if is_crypto:
                logger.debug("Refreshing Tier 6: Crypto...")
                await self.get_btc_dominance()
            
            logger.info("✅ Firecrawl intelligence FULLY refreshed (all tiers)")
            
        except Exception as e:
            logger.error(f"Error refreshing intelligence: {e}")
    
    async def refresh_quick(self, symbols: Optional[List[str]] = None):
        """
        Quick refresh for time-sensitive data only.
        
        Call this more frequently (every 3-5 min).
        Focuses on fast-moving sentiment indicators.
        
        Args:
            symbols: List of symbols or single symbol string
        """
        if not self.is_available:
            return
        
        # Handle both list and single symbol input
        if symbols is None:
            symbols = ["EURUSD"]
        elif isinstance(symbols, str):
            symbols = [symbols]
        
        primary_symbol = symbols[0] if symbols else "EURUSD"
        
        try:
            await asyncio.gather(
                self.get_dxy_analysis(),
                self.get_vix_sentiment(),
                self.get_geopolitical_news(),  # Include geopolitical for dashboard
                self.get_retail_sentiment(primary_symbol),
                self.get_twitter_forex_sentiment(primary_symbol),
                return_exceptions=True
            )
            logger.debug("Quick intelligence refresh complete")
        except Exception as e:
            logger.error(f"Error in quick refresh: {e}")
    
    async def refresh_intermarket(self):
        """
        Refresh intermarket correlations only.
        
        Call when market conditions change rapidly.
        """
        if not self.is_available:
            return
        
        try:
            await asyncio.gather(
                self.get_vix_sentiment(),
                self.get_intermarket_analysis(),
                self.get_commodity_correlation("gold"),
                return_exceptions=True
            )
            logger.debug("Intermarket refresh complete")
        except Exception as e:
            logger.error(f"Error in intermarket refresh: {e}")
    
    # =========================================================================
    # Helper methods
    # =========================================================================
    
    def _parse_dxy_sentiment(self, markdown: str) -> Dict[str, Any]:
        """Parse DXY page for trend indicators."""
        if not markdown:
            return {"trend": "unknown"}
        
        text_lower = markdown.lower()
        
        bullish_keywords = ['bullish', 'strong', 'rising', 'uptrend', 'buy', 'higher']
        bearish_keywords = ['bearish', 'weak', 'falling', 'downtrend', 'sell', 'lower']
        
        bullish_count = sum(1 for k in bullish_keywords if k in text_lower)
        bearish_count = sum(1 for k in bearish_keywords if k in text_lower)
        
        if bullish_count > bearish_count + 2:
            return {"trend": "bullish", "bias": "USD strength expected"}
        elif bearish_count > bullish_count + 2:
            return {"trend": "bearish", "bias": "USD weakness expected"}
        else:
            return {"trend": "neutral", "bias": "Mixed USD signals"}
    
    def _normalize_search_results(self, results) -> List[Dict[str, Any]]:
        """
        Normalize Firecrawl search results to a consistent list format.
        Handles v2 SDK SearchData object with web/news/images attributes.
        
        Returns:
            List of normalized result dicts with title, description, url, source
        """
        if results is None:
            return []
        
        data_items = []
        
        # Handle dict with 'data' key (older format)
        if isinstance(results, dict):
            if "data" in results:
                return results["data"] if isinstance(results["data"], list) else []
            if "web" in results:
                for item in results.get("web", []):
                    data_items.append({
                        "title": item.get('title', ''),
                        "description": item.get('description', ''),
                        "url": item.get('url', ''),
                        "source": "web"
                    })
            if "news" in results:
                for item in results.get("news", []):
                    data_items.append({
                        "title": item.get('title', ''),
                        "description": item.get('description', item.get('snippet', '')),
                        "url": item.get('url', ''),
                        "source": "news",
                        "date": item.get('date', '')
                    })
            return data_items
        
        # v2 SDK format - SearchData object has web, news, images attributes
        if hasattr(results, 'web') and results.web:
            for item in results.web:
                data_items.append({
                    "title": getattr(item, 'title', ''),
                    "description": getattr(item, 'description', ''),
                    "url": getattr(item, 'url', ''),
                    "source": "web",
                    "position": getattr(item, 'position', 0)
                })
        
        if hasattr(results, 'news') and results.news:
            for item in results.news:
                data_items.append({
                    "title": getattr(item, 'title', ''),
                    "description": getattr(item, 'snippet', getattr(item, 'description', '')),
                    "url": getattr(item, 'url', ''),
                    "source": "news",
                    "date": getattr(item, 'date', '')
                })
        
        if hasattr(results, 'images') and results.images:
            for item in results.images:
                data_items.append({
                    "title": getattr(item, 'title', ''),
                    "url": getattr(item, 'url', ''),
                    "image_url": getattr(item, 'imageUrl', ''),
                    "source": "image"
                })
        
        # Fallback: if it's a list directly
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    data_items.append(item)
                else:
                    data_items.append({
                        "title": getattr(item, 'title', ''),
                        "description": getattr(item, 'description', ''),
                        "url": getattr(item, 'url', ''),
                    })
        
        return data_items
    
    def _parse_cot_data(self, results, currency: str) -> Dict[str, Any]:
        """Parse COT search results."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"positioning": "unknown", "sentiment": "unknown"}
        
        # Analyze search result snippets
        for result in normalized:
            desc = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            if "net long" in desc or "bullish positioning" in desc or "buying" in desc:
                return {"positioning": "net_long", "sentiment": "bullish"}
            if "net short" in desc or "bearish positioning" in desc or "selling" in desc:
                return {"positioning": "net_short", "sentiment": "bearish"}
        
        return {"positioning": "neutral", "sentiment": "mixed"}
    
    def _parse_news_results(self, results) -> List[Dict[str, Any]]:
        """Parse news search results."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return []
        
        news_items = []
        for result in normalized[:5]:
            news_items.append({
                "title": result.get("title", "Unknown"),
                "url": result.get("url", ""),
                "source": result.get("source", "Unknown"),
                "timestamp": datetime.now().isoformat()
            })
        return news_items
    
    def _parse_cb_sentiment(self, results: Dict) -> Dict[str, Any]:
        """Parse central bank sentiment from results."""
        sentiment = {"fed": "unknown", "ecb": "unknown"}
        
        normalized = self._normalize_search_results(results)
        if not normalized:
            return sentiment
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            if "fed" in text or "federal reserve" in text or "powell" in text:
                if "hawkish" in text or "rate hike" in text or "tighten" in text:
                    sentiment["fed"] = "hawkish"
                elif "dovish" in text or "rate cut" in text or "ease" in text:
                    sentiment["fed"] = "dovish"
                else:
                    sentiment["fed"] = "neutral"
            
            if "ecb" in text or "european central bank" in text or "lagarde" in text:
                if "hawkish" in text or "rate hike" in text or "tighten" in text:
                    sentiment["ecb"] = "hawkish"
                elif "dovish" in text or "rate cut" in text or "ease" in text:
                    sentiment["ecb"] = "dovish"
                else:
                    sentiment["ecb"] = "neutral"
        
        return sentiment
    
    def _parse_retail_sentiment(self, results, symbol: str) -> Dict[str, Any]:
        """Parse retail sentiment - USE AS CONTRARIAN INDICATOR."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"bias": "unknown", "contrarian_signal": "unknown"}
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Look for percentage patterns like "70% long" or "65% short"
            long_match = re.search(r'(\d+)%?\s*(?:are\s+)?long', text)
            short_match = re.search(r'(\d+)%?\s*(?:are\s+)?short', text)
            
            if long_match:
                long_pct = int(long_match.group(1))
                if long_pct >= 70:
                    return {
                        "bias": "extreme_long",
                        "long_percent": long_pct,
                        "contrarian_signal": "short",
                        "note": "⚠️ CONTRARIAN: Retail extremely long - consider SHORT"
                    }
                elif long_pct >= 55:
                    return {
                        "bias": "long",
                        "long_percent": long_pct,
                        "contrarian_signal": "short",
                        "note": "Retail biased long - slight SHORT bias"
                    }
            
            if short_match:
                short_pct = int(short_match.group(1))
                if short_pct >= 70:
                    return {
                        "bias": "extreme_short",
                        "short_percent": short_pct,
                        "contrarian_signal": "long",
                        "note": "⚠️ CONTRARIAN: Retail extremely short - consider LONG"
                    }
                elif short_pct >= 55:
                    return {
                        "bias": "short",
                        "short_percent": short_pct,
                        "contrarian_signal": "long",
                        "note": "Retail biased short - slight LONG bias"
                    }
        
        return {"bias": "neutral", "contrarian_signal": "neutral"}
    
    def _parse_currency_strength(self, results: Dict) -> Dict[str, Any]:
        """Parse currency strength rankings."""
        currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
        rankings = []
        
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"rankings": [], "strongest": None, "weakest": None}
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Look for strongest/weakest mentions
            for currency in currencies:
                if f"{currency.lower()} strongest" in text or f"strongest {currency.lower()}" in text:
                    rankings.append({"currency": currency, "rank": 1, "status": "strongest"})
                elif f"{currency.lower()} weakest" in text or f"weakest {currency.lower()}" in text:
                    rankings.append({"currency": currency, "rank": 8, "status": "weakest"})
        
        strongest = next((r["currency"] for r in rankings if r.get("status") == "strongest"), None)
        weakest = next((r["currency"] for r in rankings if r.get("status") == "weakest"), None)
        
        # Generate trading recommendation
        recommendation = None
        if strongest and weakest:
            # Best trade is strong vs weak
            recommendation = f"Consider {strongest}/{weakest} long or {weakest}/{strongest} short"
        
        return {
            "rankings": rankings,
            "strongest": strongest,
            "weakest": weakest,
            "recommendation": recommendation
        }
    
    def _parse_vix_data(self, markdown: str) -> Dict[str, Any]:
        """Parse VIX data for risk sentiment."""
        if not markdown:
            return {"level": None, "sentiment": "unknown"}
        
        # Try to find VIX level
        vix_match = re.search(r'(\d+\.?\d*)\s*(?:points?)?', markdown)
        vix_level = float(vix_match.group(1)) if vix_match else None
        
        text_lower = markdown.lower()
        
        # Determine sentiment
        if vix_level:
            if vix_level >= 25:
                sentiment = "extreme_fear"
                note = "RISK-OFF: Favor JPY, CHF, Gold"
            elif vix_level >= 20:
                sentiment = "fear"
                note = "Caution: Elevated volatility"
            elif vix_level <= 12:
                sentiment = "complacency"
                note = "RISK-ON: Favor AUD, NZD"
            else:
                sentiment = "neutral"
                note = "Normal market conditions"
        else:
            # Parse from text
            if "spike" in text_lower or "surge" in text_lower or "fear" in text_lower:
                sentiment = "fear"
                note = "RISK-OFF signals detected"
            elif "low" in text_lower or "calm" in text_lower:
                sentiment = "complacency"
                note = "RISK-ON signals detected"
            else:
                sentiment = "neutral"
                note = ""
        
        return {
            "level": vix_level,
            "sentiment": sentiment,
            "note": note,
            "risk_mode": "risk_off" if sentiment in ["fear", "extreme_fear"] else "risk_on"
        }
    
    def _parse_tv_technical(self, results, symbol: str) -> Dict[str, Any]:
        """Parse TradingView technical consensus."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"consensus": "unknown", "signal": "neutral"}
        
        buy_count = 0
        sell_count = 0
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Count buy/sell signals
            buy_count += text.count("buy") + text.count("bullish") + text.count("long")
            sell_count += text.count("sell") + text.count("bearish") + text.count("short")
            
            # Look for explicit consensus
            if "strong buy" in text:
                return {"consensus": "strong_buy", "signal": "buy", "strength": "strong"}
            elif "strong sell" in text:
                return {"consensus": "strong_sell", "signal": "sell", "strength": "strong"}
        
        # Determine signal from counts
        if buy_count > sell_count + 2:
            return {"consensus": "buy", "signal": "buy", "strength": "moderate"}
        elif sell_count > buy_count + 2:
            return {"consensus": "sell", "signal": "sell", "strength": "moderate"}
        else:
            return {"consensus": "neutral", "signal": "neutral", "strength": "weak"}
    
    def _parse_rate_expectations(self, results) -> Dict[str, Any]:
        """Parse interest rate expectations."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"fed": {"next_move": "unknown"}}
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Look for rate hike/cut probability
            if "rate hike" in text or "raise" in text:
                if any(p in text for p in ["likely", "expected", "probable", "certainty"]):
                    return {
                        "fed": {
                            "next_move": "hike",
                            "probability": "high",
                            "usd_impact": "bullish"
                        }
                    }
            
            if "rate cut" in text or "lower" in text:
                if any(p in text for p in ["likely", "expected", "probable", "certainty"]):
                    return {
                        "fed": {
                            "next_move": "cut",
                            "probability": "high",
                            "usd_impact": "bearish"
                        }
                    }
            
            if "hold" in text or "unchanged" in text:
                return {
                    "fed": {
                        "next_move": "hold",
                        "probability": "high",
                        "usd_impact": "neutral"
                    }
                }
        
        return {"fed": {"next_move": "uncertain"}}
    
    def _parse_commodity_trend(self, markdown: str, commodity: str) -> Dict[str, Any]:
        """Parse commodity trend and currency implications."""
        if not markdown:
            return {"trend": "unknown", "currency_implication": {}}
        
        text_lower = markdown.lower()
        
        bullish_keywords = ['bullish', 'rising', 'uptrend', 'rally', 'higher', 'surge']
        bearish_keywords = ['bearish', 'falling', 'downtrend', 'decline', 'lower', 'drop']
        
        bullish_count = sum(1 for k in bullish_keywords if k in text_lower)
        bearish_count = sum(1 for k in bearish_keywords if k in text_lower)
        
        if bullish_count > bearish_count:
            trend = "bullish"
        elif bearish_count > bullish_count:
            trend = "bearish"
        else:
            trend = "neutral"
        
        # Currency implications
        implications = {}
        if commodity.lower() == "oil":
            implications = {
                "CAD": "bullish" if trend == "bullish" else "bearish",
                "pair_recommendation": "USDCAD short" if trend == "bullish" else "USDCAD long",
                "reason": "Oil prices correlate positively with CAD (Canada is oil exporter)"
            }
        elif commodity.lower() == "gold":
            implications = {
                "AUD": "bullish" if trend == "bullish" else "bearish",
                "safe_haven": "Gold rising = risk-off environment",
                "pair_recommendation": "AUDUSD long" if trend == "bullish" else "Consider risk-off pairs",
                "reason": "Gold correlates with AUD (Australia is gold exporter) and is a safe haven"
            }
        
        return {
            "commodity": commodity,
            "trend": trend,
            "currency_implication": implications
        }
    
    def _parse_economic_calendar(self, results) -> List[Dict[str, Any]]:
        """Parse economic calendar events."""
        events = []
        
        normalized = self._normalize_search_results(results)
        if not normalized:
            return events
        
        for result in normalized:
            text = result.get("description", "") + " " + result.get("title", "")
            
            # Look for high-impact events
            high_impact_keywords = ["NFP", "FOMC", "CPI", "GDP", "interest rate", "employment", "inflation"]
            
            for keyword in high_impact_keywords:
                if keyword.lower() in text.lower():
                    events.append({
                        "event": keyword,
                        "impact": "high",
                        "source": result.get("source", ""),
                        "note": f"High-impact {keyword} event detected"
                    })
                    break
        
        return events
    
    # =========================================================================
    # NEW: Parsing helpers for advanced intelligence
    # =========================================================================
    
    def _parse_social_sentiment(self, results, symbol: str) -> Dict[str, Any]:
        """Parse social media sentiment from search results."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"sentiment": "unknown", "volume": "low"}
        
        bullish_count = 0
        bearish_count = 0
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Count sentiment indicators
            bullish_words = ["bullish", "buy", "long", "moon", "pump", "breakout", "rally"]
            bearish_words = ["bearish", "sell", "short", "dump", "crash", "breakdown", "collapse"]
            
            bullish_count += sum(1 for w in bullish_words if w in text)
            bearish_count += sum(1 for w in bearish_words if w in text)
        
        total = bullish_count + bearish_count
        volume = "high" if total > 10 else "medium" if total > 5 else "low"
        
        if bullish_count > bearish_count * 1.5:
            sentiment = "bullish"
            note = "⚠️ Social sentiment extremely bullish - potential contrarian short"
        elif bearish_count > bullish_count * 1.5:
            sentiment = "bearish"
            note = "⚠️ Social sentiment extremely bearish - potential contrarian long"
        elif bullish_count > bearish_count:
            sentiment = "slightly_bullish"
            note = "Social sentiment leaning bullish"
        elif bearish_count > bullish_count:
            sentiment = "slightly_bearish"
            note = "Social sentiment leaning bearish"
        else:
            sentiment = "neutral"
            note = "Mixed social sentiment"
        
        return {
            "sentiment": sentiment,
            "volume": volume,
            "bullish_mentions": bullish_count,
            "bearish_mentions": bearish_count,
            "note": note,
            "contrarian_signal": "short" if sentiment == "bullish" else "long" if sentiment == "bearish" else "neutral"
        }
    
    def _parse_options_flow(self, results, symbol: str) -> Dict[str, Any]:
        """Parse FX options flow and expiry data."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"flow": "neutral", "expiries": [], "note": "No options data found"}
        
        expiries = []
        flow = "neutral"
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Look for option expiry levels
            price_matches = re.findall(r'(\d+\.\d{4,5})\s*(?:expir|strike|barrier|level)', text)
            for price in price_matches[:3]:  # Max 3 expiries
                expiries.append({
                    "level": float(price),
                    "note": "Potential magnet level"
                })
            
            # Determine flow direction
            if "call buying" in text or "bullish flow" in text or "upside options" in text:
                flow = "bullish"
            elif "put buying" in text or "bearish flow" in text or "downside options" in text:
                flow = "bearish"
        
        return {
            "flow": flow,
            "expiries": expiries,
            "magnet_levels": [e["level"] for e in expiries],
            "note": f"Options flow: {flow.upper()}" if flow != "neutral" else "No clear options flow"
        }
    
    def _parse_yield_spread(self, results: List) -> Dict[str, Any]:
        """Parse bond yield spread data."""
        us_yield = None
        de_yield = None
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                continue
            
            markdown = result.get("markdown", "") if isinstance(result, dict) else ""
            
            # Try to extract yield value
            yield_match = re.search(r'(\d+\.?\d*)\s*%', markdown)
            if yield_match:
                yield_val = float(yield_match.group(1))
                if i == 0:
                    us_yield = yield_val
                else:
                    de_yield = yield_val
        
        # Calculate spread
        spread = None
        implication = "unknown"
        
        if us_yield is not None and de_yield is not None:
            spread = us_yield - de_yield
            
            if spread > 1.5:
                implication = "Strong USD vs EUR (wide yield advantage)"
            elif spread > 0.5:
                implication = "Moderate USD strength vs EUR"
            elif spread < -0.5:
                implication = "EUR strength vs USD (negative spread)"
            else:
                implication = "Neutral yield spread"
        
        return {
            "us_10y": us_yield,
            "de_10y": de_yield,
            "spread": spread,
            "implication": implication,
            "eurusd_bias": "bearish" if spread and spread > 1.0 else "bullish" if spread and spread < 0 else "neutral"
        }
    
    def _parse_btc_dominance(self, markdown: str) -> Dict[str, Any]:
        """Parse Bitcoin dominance for crypto analysis."""
        if not markdown:
            return {"dominance": None, "trend": "unknown", "altcoin_sentiment": "unknown"}
        
        text_lower = markdown.lower()
        
        # Try to find dominance percentage
        dom_match = re.search(r'(\d+\.?\d*)%?\s*(?:dominance|dom\.?|btc\.d)', text_lower)
        dominance = float(dom_match.group(1)) if dom_match else None
        
        # Determine trend
        if "rising" in text_lower or "increasing" in text_lower or "gaining" in text_lower:
            trend = "rising"
            altcoin_sentiment = "bearish"  # Money flowing to BTC from alts
        elif "falling" in text_lower or "decreasing" in text_lower or "losing" in text_lower:
            trend = "falling"
            altcoin_sentiment = "bullish"  # Money flowing to alts from BTC
        else:
            trend = "stable"
            altcoin_sentiment = "neutral"
        
        # Additional context based on dominance level
        note = ""
        if dominance:
            if dominance > 55:
                note = "BTC dominance high - risk-off for alts"
            elif dominance < 40:
                note = "BTC dominance low - alt season potential"
            else:
                note = "BTC dominance in normal range"
        
        return {
            "dominance": dominance,
            "trend": trend,
            "altcoin_sentiment": altcoin_sentiment,
            "note": note,
            "eth_implication": "bullish" if altcoin_sentiment == "bullish" else "bearish"
        }
    
    def _parse_economic_surprise(self, results) -> Dict[str, Any]:
        """Parse economic surprise index data."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"us": "unknown", "eu": "unknown", "uk": "unknown"}
        
        surprises = {"us": "neutral", "eu": "neutral", "uk": "neutral"}
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Check for US surprises
            if "us" in text or "usd" in text or "american" in text:
                if "positive" in text or "beat" in text or "better than" in text:
                    surprises["us"] = "positive"
                elif "negative" in text or "miss" in text or "worse than" in text:
                    surprises["us"] = "negative"
            
            # Check for EU surprises
            if "eu" in text or "eur" in text or "eurozone" in text:
                if "positive" in text or "beat" in text:
                    surprises["eu"] = "positive"
                elif "negative" in text or "miss" in text:
                    surprises["eu"] = "negative"
            
            # Check for UK surprises
            if "uk" in text or "gbp" in text or "british" in text:
                if "positive" in text or "beat" in text:
                    surprises["uk"] = "positive"
                elif "negative" in text or "miss" in text:
                    surprises["uk"] = "negative"
        
        # Generate implications
        implications = {}
        if surprises["us"] == "positive":
            implications["usd"] = "bullish"
        elif surprises["us"] == "negative":
            implications["usd"] = "bearish"
        
        return {
            **surprises,
            "implications": implications,
            "eurusd_bias": "bearish" if surprises["us"] == "positive" and surprises["eu"] != "positive" else 
                          "bullish" if surprises["eu"] == "positive" and surprises["us"] != "positive" else "neutral"
        }
    
    def _parse_seasonal_pattern(self, results, symbol: str, current_month: str) -> Dict[str, Any]:
        """Parse historical seasonal patterns."""
        normalized = self._normalize_search_results(results)
        if not normalized:
            return {"current_month_bias": "unknown", "historical_accuracy": 0}
        
        bias = "neutral"
        accuracy = 0
        
        for result in normalized:
            text = (result.get("description", "") + " " + result.get("title", "")).lower()
            
            # Check for seasonal bias
            if current_month.lower() in text:
                if "bullish" in text or "rises" in text or "gains" in text:
                    bias = "bullish"
                elif "bearish" in text or "falls" in text or "declines" in text:
                    bias = "bearish"
                
                # Try to find accuracy percentage
                acc_match = re.search(r'(\d+)%?\s*(?:accuracy|win rate|probability)', text)
                if acc_match:
                    accuracy = int(acc_match.group(1))
        
        # Known seasonal patterns
        seasonal_notes = {
            "january": "USD often weak in January (portfolio rebalancing)",
            "march": "Quarter-end rebalancing flows",
            "april": "Tax season impacts USD",
            "august": "Low liquidity, choppy markets",
            "december": "Holiday low liquidity, position squaring"
        }
        
        return {
            "current_month": current_month,
            "current_month_bias": bias,
            "historical_accuracy": accuracy,
            "note": seasonal_notes.get(current_month.lower(), ""),
            "confidence": "high" if accuracy > 70 else "moderate" if accuracy > 50 else "low"
        }
    
    def _parse_intermarket_analysis(self, markdown: str) -> Dict[str, Any]:
        """Parse SPX/intermarket data."""
        if not markdown:
            return {"spx_trend": "unknown", "risk_environment": "unknown"}
        
        text_lower = markdown.lower()
        
        # Determine SPX trend
        bullish_keywords = ['bullish', 'rising', 'rally', 'new high', 'breakout']
        bearish_keywords = ['bearish', 'falling', 'selloff', 'crash', 'breakdown']
        
        bullish_count = sum(1 for k in bullish_keywords if k in text_lower)
        bearish_count = sum(1 for k in bearish_keywords if k in text_lower)
        
        if bullish_count > bearish_count:
            spx_trend = "bullish"
        elif bearish_count > bullish_count:
            spx_trend = "bearish"
        else:
            spx_trend = "neutral"
        
        return {
            "spx_trend": spx_trend,
            "equity_sentiment": "risk_on" if spx_trend == "bullish" else "risk_off" if spx_trend == "bearish" else "neutral"
        }
    
    def _determine_risk_environment(self, data: Dict) -> str:
        """
        Determine overall risk environment from intermarket data.
        
        Risk-on: SPX up, VIX down, DXY down, Gold down
        Risk-off: SPX down, VIX up, DXY up or down (flight to safety), Gold up
        """
        risk_on_signals = 0
        risk_off_signals = 0
        
        # SPX trend
        if data.get("spx_trend") == "bullish":
            risk_on_signals += 2
        elif data.get("spx_trend") == "bearish":
            risk_off_signals += 2
        
        # VIX sentiment
        vix_sentiment = data.get("vix_sentiment", "")
        if vix_sentiment in ["complacency", "low"]:
            risk_on_signals += 2
        elif vix_sentiment in ["fear", "extreme_fear"]:
            risk_off_signals += 2
        
        # Gold trend
        if data.get("gold_trend") == "bullish":
            risk_off_signals += 1  # Gold up = risk-off
        elif data.get("gold_trend") == "bearish":
            risk_on_signals += 1
        
        # Determine environment
        if risk_on_signals > risk_off_signals + 2:
            return "strong_risk_on"
        elif risk_on_signals > risk_off_signals:
            return "risk_on"
        elif risk_off_signals > risk_on_signals + 2:
            return "strong_risk_off"
        elif risk_off_signals > risk_on_signals:
            return "risk_off"
        else:
            return "neutral"
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status."""
        return {
            "enabled": self.enabled,
            "available": self.is_available,
            "firecrawl_sdk": FIRECRAWL_AVAILABLE,
            "api_key_configured": bool(self.api_key),
            "refresh_minutes": self.refresh_minutes,
            "cached_keys": list(self._cache.keys()),
            "cache_status": {
                k: {
                    "expired": v.is_expired(),
                    "age_minutes": (datetime.now() - v.timestamp).total_seconds() / 60
                }
                for k, v in self._cache.items()
            }
        }
