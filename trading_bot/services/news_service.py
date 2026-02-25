"""
News Service for economic calendar and geopolitical news.

Provides:
- Economic calendar fetching
- High-impact event detection
- Blackout period management
- Geopolitical news filtering
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import re

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class NewsEvent:
    """Represents an economic news event."""
    title: str
    datetime: datetime
    impact: str  # 'low', 'medium', 'high'
    currency: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None


class NewsService:
    """
    Service for economic calendar and geopolitical news.
    
    Features:
    - Fetch economic calendar events
    - Track high-impact events (NFP, FOMC, CPI, etc.)
    - Manage trading blackout periods around events
    - Filter geopolitical news for market-moving events
    """
    
    # Keywords for geopolitical filtering
    GEOPOLITICAL_KEYWORDS = [
        'war', 'military', 'conflict', 'sanctions', 'tariff', 'trade war',
        'invasion', 'attack', 'troops', 'nuclear', 'missile', 'bomb',
        'crisis', 'emergency', 'threat', 'escalation', 'tension',
        'coup', 'protest', 'riot', 'revolution'
    ]
    
    # High-impact event patterns
    NFP_PATTERNS = ['nonfarm', 'non-farm', 'nfp', 'payroll']
    FOMC_PATTERNS = ['fomc', 'fed rate', 'federal reserve', 'interest rate decision']
    CPI_PATTERNS = ['cpi', 'consumer price', 'inflation']
    ECB_PATTERNS = ['ecb', 'european central bank']
    BOE_PATTERNS = ['boe', 'bank of england']
    
    def __init__(
        self,
        blackout_minutes_before: int = 120,    # 2 hours before high-impact events
        blackout_minutes_after: int = 60,       # 1 hour after
        fomc_blackout_minutes_before: int = 180, # 3 hours before FOMC/NFP/CPI
        fomc_blackout_minutes_after: int = 120,  # 2 hours after
        api_key: Optional[str] = None
    ):
        """
        Initialize the news service.
        
        Args:
            blackout_minutes: Minutes before/after high-impact events to avoid trading
            fomc_blackout_minutes: Extended blackout for FOMC (more volatile)
            api_key: API key for news service (if required)
        """
        self.blackout_minutes_before = blackout_minutes_before
        self.blackout_minutes_after = blackout_minutes_after
        self.fomc_blackout_minutes_before = fomc_blackout_minutes_before
        self.fomc_blackout_minutes_after = fomc_blackout_minutes_after
        self.api_key = api_key
        
        # Cached events
        self._events: List[Dict[str, Any]] = []
        self._last_fetch: Optional[datetime] = None
        self._cache_duration = timedelta(minutes=15)
        
        # Geopolitical news
        self._geopolitical_news: List[str] = []
        
        logger.info(
            f"News service initialized (blackout: {blackout_minutes_before}min before / "
            f"{blackout_minutes_after}min after, FOMC: {fomc_blackout_minutes_before}/{fomc_blackout_minutes_after})"
        )
    
    def set_events(self, events: List[Dict[str, Any]]):
        """Set events directly (for testing or manual updates)."""
        self._events = events
        self._last_fetch = datetime.now()
    
    def add_geopolitical_news(self, headlines: List[str]):
        """Add geopolitical news headlines."""
        self._geopolitical_news.extend(headlines)
    
    async def fetch_economic_calendar(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch economic calendar events.
        
        Tries multiple sources:
        1. Firecrawl intelligence service (if available)
        2. Manual set_events (for testing)
        3. ForexFactory scraping fallback
        
        Args:
            days_ahead: Number of days to fetch events for
            
        Returns:
            List of event dictionaries
        """
        # Check cache
        if self._last_fetch and datetime.now() - self._last_fetch < self._cache_duration:
            return self._events
        
        try:
            # Try to fetch from Firecrawl intelligence service
            events = await self._fetch_from_firecrawl(days_ahead)
            
            if events:
                self._events = events
                self._last_fetch = datetime.now()
                logger.info(f"Fetched {len(events)} events from Firecrawl calendar")
                return self._events
            
            # Fallback: try scraping ForexFactory
            events = await self._fetch_from_forexfactory(days_ahead)
            
            if events:
                self._events = events
                self._last_fetch = datetime.now()
                logger.info(f"Fetched {len(events)} events from ForexFactory")
                return self._events
            
            # Last resort: return cached/set events
            self._last_fetch = datetime.now()
            return self._events
            
        except Exception as e:
            logger.error(f"Error fetching economic calendar: {e}")
            return self._events if self._events else []
    
    async def _fetch_from_firecrawl(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch economic calendar from Firecrawl intelligence service.
        
        The Firecrawl service may already have a calendar endpoint configured
        in the trading bot's intelligence layer.
        """
        try:
            from ..services.firecrawl_intelligence import FirecrawlIntelligenceService
            
            firecrawl = FirecrawlIntelligenceService()
            calendar_data = await firecrawl.get_economic_calendar()
            
            if not calendar_data:
                return []
            
            # Normalize to our event format
            events = []
            for item in calendar_data:
                event = {
                    'title': item.get('title', item.get('event', 'Unknown')),
                    'datetime': item.get('datetime', item.get('date', '')),
                    'impact': self._normalize_impact(item.get('impact', 'low')),
                    'currency': item.get('currency', item.get('country', 'USD')),
                    'forecast': item.get('forecast', ''),
                    'previous': item.get('previous', ''),
                    'actual': item.get('actual', ''),
                }
                events.append(event)
            
            return events
        except ImportError:
            logger.debug("Firecrawl intelligence service not available")
            return []
        except Exception as e:
            logger.warning(f"Error fetching from Firecrawl: {e}")
            return []
    
    async def _fetch_from_forexfactory(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Fallback: Scrape ForexFactory for economic calendar.
        Uses httpx or aiohttp if available.
        """
        try:
            import httpx
            
            url = "https://www.forexfactory.com/calendar"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                
                if response.status_code != 200:
                    return []
                
                # Basic parsing of ForexFactory data
                # (This is a simplified parser - production would use proper HTML parsing)
                return self._parse_forexfactory_html(response.text)
                
        except ImportError:
            logger.debug("httpx not available for ForexFactory scraping")
            return []
        except Exception as e:
            logger.warning(f"Error scraping ForexFactory: {e}")
            return []
    
    def _parse_forexfactory_html(self, html: str) -> List[Dict[str, Any]]:
        """Parse ForexFactory HTML into event dictionaries."""
        events = []
        try:
            # Look for high-impact events using simple patterns
            import re
            
            # ForexFactory uses CSS classes for impact levels
            # icon--ff-impact-red = high impact
            high_impact_pattern = re.compile(
                r'icon--ff-impact-red.*?calendar__event-title.*?<span>(.*?)</span>.*?'
                r'calendar__currency.*?>(.*?)<',
                re.DOTALL
            )
            
            for match in high_impact_pattern.finditer(html):
                events.append({
                    'title': match.group(1).strip(),
                    'datetime': datetime.now().isoformat(),  # Approximate
                    'impact': 'high',
                    'currency': match.group(2).strip(),
                })
            
        except Exception as e:
            logger.warning(f"Error parsing ForexFactory HTML: {e}")
        
        return events
    
    def _normalize_impact(self, impact_value) -> str:
        """Normalize impact values from different sources."""
        if isinstance(impact_value, int):
            if impact_value >= 3:
                return 'high'
            elif impact_value >= 2:
                return 'medium'
            return 'low'
        
        impact_str = str(impact_value).lower()
        if impact_str in ['high', 'red', '3']:
            return 'high'
        elif impact_str in ['medium', 'orange', 'yellow', '2']:
            return 'medium'
        return 'low'
    
    async def get_high_impact_events(self) -> List[Dict[str, Any]]:
        """Get only high-impact events."""
        events = await self.fetch_economic_calendar()
        
        return [
            event for event in events
            if event.get('impact', '').lower() in ['high', 'red'] or event.get('impact') == 3
        ]
    
    async def get_upcoming_events(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get events within the specified timeframe."""
        events = await self.fetch_economic_calendar()
        
        now = datetime.now()
        future = now + timedelta(hours=hours)
        
        upcoming = []
        for event in events:
            try:
                event_time = datetime.fromisoformat(event['datetime'])
                if now <= event_time <= future:
                    upcoming.append(event)
            except (KeyError, ValueError):
                continue
        
        return sorted(upcoming, key=lambda e: e['datetime'])
    
    def is_blackout_period(self) -> Tuple[bool, str]:
        """
        Check if currently in a blackout period around high-impact events.
        
        Uses asymmetric windows:
        - Standard high-impact: 2 hours before, 1 hour after
        - FOMC/NFP/CPI: 3 hours before, 2 hours after
        
        Returns:
            Tuple of (is_blackout, reason)
        """
        now = datetime.now()
        
        for event in self._events:
            # Skip low impact events
            impact = event.get('impact', '').lower()
            if impact in ['low', 'medium'] or (isinstance(impact, int) and impact < 3):
                continue
            
            try:
                event_time = datetime.fromisoformat(event['datetime'])
            except (KeyError, ValueError):
                continue
            
            # Determine blackout window (asymmetric before/after)
            is_critical = self.is_fomc_event(event) or self.is_nfp_event(event) or self.is_cpi_event(event)
            
            if is_critical:
                before_mins = self.fomc_blackout_minutes_before
                after_mins = self.fomc_blackout_minutes_after
            else:
                before_mins = self.blackout_minutes_before
                after_mins = self.blackout_minutes_after
            
            blackout_start = event_time - timedelta(minutes=before_mins)
            blackout_end = event_time + timedelta(minutes=after_mins)
            
            if blackout_start <= now <= blackout_end:
                title = event.get('title', 'Unknown event')
                event_type = "CRITICAL" if is_critical else "High-impact"
                if now < event_time:
                    reason = f"{event_type} blackout: {title} in {int((event_time - now).total_seconds() / 60)} minutes"
                else:
                    reason = f"{event_type} blackout: {title} occurred {int((now - event_time).total_seconds() / 60)} minutes ago"
                return True, reason
        
        return False, ""
    
    def should_trade(self) -> bool:
        """Check if trading should be allowed based on news."""
        is_blackout, _ = self.is_blackout_period()
        return not is_blackout
    
    def get_countdown_to_next_event(self) -> Optional[Dict[str, Any]]:
        """Get countdown to the next high-impact event."""
        now = datetime.now()
        next_event = None
        min_time = None
        
        for event in self._events:
            # Only consider high impact
            impact = event.get('impact', '').lower()
            if impact not in ['high', 'red'] and event.get('impact') != 3:
                continue
            
            try:
                event_time = datetime.fromisoformat(event['datetime'])
                if event_time > now:
                    time_until = event_time - now
                    if min_time is None or time_until < min_time:
                        min_time = time_until
                        next_event = event
            except (KeyError, ValueError):
                continue
        
        if next_event and min_time:
            total_minutes = int(min_time.total_seconds() / 60)
            hours = total_minutes // 60
            minutes = total_minutes % 60
            
            return {
                'event': next_event,
                'time_until': {
                    'hours': hours,
                    'minutes': minutes,
                    'total_minutes': total_minutes
                }
            }
        
        return None
    
    def get_events_for_currency(self, currency: str) -> List[Dict[str, Any]]:
        """Get events for a specific currency."""
        return [
            event for event in self._events
            if event.get('currency', '').upper() == currency.upper()
        ]
    
    def filter_geopolitical_news(self, headlines: List[str]) -> List[str]:
        """Filter headlines for geopolitical/market-moving news."""
        filtered = []
        
        for headline in headlines:
            headline_lower = headline.lower()
            if any(keyword in headline_lower for keyword in self.GEOPOLITICAL_KEYWORDS):
                filtered.append(headline)
        
        return filtered
    
    def get_geopolitical_risk_level(self) -> str:
        """
        Assess current geopolitical risk level.
        
        Returns:
            Risk level: 'low', 'medium', 'high', 'extreme'
        """
        filtered_news = self.filter_geopolitical_news(self._geopolitical_news)
        count = len(filtered_news)
        
        if count == 0:
            return 'low'
        elif count <= 2:
            return 'medium'
        elif count <= 5:
            return 'high'
        else:
            return 'extreme'
    
    def is_nfp_event(self, event: Dict[str, Any]) -> bool:
        """Check if event is Non-Farm Payrolls."""
        title = event.get('title', '').lower()
        return any(pattern in title for pattern in self.NFP_PATTERNS)
    
    def is_fomc_event(self, event: Dict[str, Any]) -> bool:
        """Check if event is FOMC-related."""
        title = event.get('title', '').lower()
        return any(pattern in title for pattern in self.FOMC_PATTERNS)
    
    def is_cpi_event(self, event: Dict[str, Any]) -> bool:
        """Check if event is CPI-related."""
        title = event.get('title', '').lower()
        return any(pattern in title for pattern in self.CPI_PATTERNS)
    
    # Per-symbol event impact scores (0-1).  Higher = more volatile reaction.
    SYMBOL_EVENT_IMPACT = {
        'XAUUSD': {'FOMC': 0.95, 'NFP': 0.90, 'CPI': 0.85, 'PPI': 0.60, 'GDP': 0.55, 'ECB': 0.40, 'BOE': 0.30},
        'XAGUSD': {'FOMC': 0.90, 'NFP': 0.85, 'CPI': 0.80, 'PPI': 0.55, 'GDP': 0.50, 'ECB': 0.35, 'BOE': 0.25},
        'EURUSD': {'FOMC': 0.85, 'NFP': 0.85, 'CPI': 0.80, 'ECB': 0.95, 'BOE': 0.35, 'GDP': 0.60, 'PPI': 0.50},
        'GBPUSD': {'FOMC': 0.80, 'NFP': 0.80, 'CPI': 0.75, 'BOE': 0.95, 'ECB': 0.35, 'GDP': 0.60, 'PPI': 0.50},
        'USDJPY': {'FOMC': 0.90, 'NFP': 0.85, 'CPI': 0.80, 'BOJ': 0.95, 'GDP': 0.60, 'PPI': 0.50},
        'USDCHF': {'FOMC': 0.80, 'NFP': 0.75, 'CPI': 0.70, 'SNB': 0.90, 'ECB': 0.50},
        'USDCAD': {'FOMC': 0.80, 'NFP': 0.80, 'CPI': 0.70, 'BOC': 0.90, 'GDP': 0.55},
        'AUDUSD': {'FOMC': 0.70, 'NFP': 0.65, 'RBA': 0.90, 'CPI': 0.65, 'GDP': 0.60},
        'NZDUSD': {'FOMC': 0.65, 'NFP': 0.60, 'RBNZ': 0.90, 'CPI': 0.60},
        'BTCUSD': {'FOMC': 0.70, 'NFP': 0.50, 'CPI': 0.55, 'SEC': 0.85},
        'ETHUSD': {'FOMC': 0.65, 'NFP': 0.45, 'CPI': 0.50, 'SEC': 0.80},
    }
    
    # Event keyword to canonical name mapping
    EVENT_ALIASES = {
        'fomc': 'FOMC', 'fed rate': 'FOMC', 'federal reserve': 'FOMC', 'interest rate decision': 'FOMC',
        'nonfarm': 'NFP', 'non-farm': 'NFP', 'nfp': 'NFP', 'payroll': 'NFP',
        'cpi': 'CPI', 'consumer price': 'CPI', 'inflation': 'CPI',
        'ecb': 'ECB', 'european central bank': 'ECB',
        'boe': 'BOE', 'bank of england': 'BOE',
        'boj': 'BOJ', 'bank of japan': 'BOJ',
        'rba': 'RBA', 'reserve bank of australia': 'RBA',
        'rbnz': 'RBNZ', 'reserve bank of new zealand': 'RBNZ',
        'snb': 'SNB', 'swiss national bank': 'SNB',
        'boc': 'BOC', 'bank of canada': 'BOC',
        'gdp': 'GDP', 'gross domestic': 'GDP',
        'ppi': 'PPI', 'producer price': 'PPI',
        'sec': 'SEC', 'securities': 'SEC',
    }
    
    def _classify_event(self, event: Dict[str, Any]) -> Optional[str]:
        """Map an event to a canonical event name."""
        title = event.get('title', '').lower()
        for keyword, canonical in self.EVENT_ALIASES.items():
            if keyword in title:
                return canonical
        return None
    
    def get_event_impact_for_symbol(self, symbol: str, event: Dict[str, Any]) -> float:
        """
        Return impact score (0-1) for a symbol/event pair.
        Higher means more historical volatility from this event for this symbol.
        """
        canonical = self._classify_event(event)
        if not canonical:
            return 0.3 if event.get('impact', '').lower() == 'high' else 0.1
        
        symbol_impacts = self.SYMBOL_EVENT_IMPACT.get(symbol, {})
        return symbol_impacts.get(canonical, 0.3)
    
    def should_reduce_size(self, symbol: str, lookahead_minutes: int = 120) -> Tuple[float, str]:
        """
        Return (size_multiplier, reason) based on upcoming events.

        Multiplier ranges:
        - 1.0 = no reduction
        - 0.5 = half size (high-impact event within lookahead)
        - 0.25 = quarter size (critical event imminent)
        
        Args:
            symbol: Trading symbol
            lookahead_minutes: How far ahead to look for events
        
        Returns:
            Tuple of (multiplier, reason_string)
        """
        now = datetime.now()
        worst_multiplier = 1.0
        worst_reason = ""
        
        for event in self._events:
            impact = event.get('impact', '').lower()
            if impact not in ['high', 'red'] and event.get('impact') != 3:
                continue
            
            try:
                event_time = datetime.fromisoformat(event['datetime'])
            except (KeyError, ValueError):
                continue
            
            minutes_until = (event_time - now).total_seconds() / 60
            if minutes_until < 0 or minutes_until > lookahead_minutes:
                continue
            
            impact_score = self.get_event_impact_for_symbol(symbol, event)
            
            if minutes_until <= 30 and impact_score >= 0.8:
                multiplier = 0.25
            elif minutes_until <= 60 and impact_score >= 0.7:
                multiplier = 0.5
            elif minutes_until <= 120 and impact_score >= 0.6:
                multiplier = 0.75
            else:
                multiplier = 0.85
            
            if multiplier < worst_multiplier:
                worst_multiplier = multiplier
                title = event.get('title', 'Unknown')
                worst_reason = (
                    f"{title} in {int(minutes_until)}min "
                    f"(impact {impact_score:.0%} on {symbol})"
                )
        
        return worst_multiplier, worst_reason
    
    def get_status(self) -> Dict[str, Any]:
        """Get current news service status."""
        is_blackout, reason = self.is_blackout_period()
        countdown = self.get_countdown_to_next_event()
        
        return {
            'is_blackout': is_blackout,
            'blackout_reason': reason,
            'next_event': countdown,
            'geopolitical_risk': self.get_geopolitical_risk_level(),
            'total_events_cached': len(self._events),
            'last_fetch': self._last_fetch.isoformat() if self._last_fetch else None
        }
