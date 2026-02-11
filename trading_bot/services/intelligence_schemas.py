"""
Pydantic Schemas for Firecrawl Deep Research Intelligence.

These schemas define structured data models for AI-powered market intelligence
gathered using Firecrawl's Agent (Deep Research) and Extract APIs.

Used by:
- FirecrawlIntelligenceService for structured extraction
- Claude trading prompts for fundamental analysis context
- Dashboard for displaying research results
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum


# ============================================================================
# Risk Level Enums
# ============================================================================

class RiskLevel(str, Enum):
    """Risk level classification."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class Sentiment(str, Enum):
    """Market sentiment classification."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class PolicyStance(str, Enum):
    """Central bank policy stance."""
    HAWKISH = "hawkish"
    DOVISH = "dovish"
    NEUTRAL = "neutral"
    UNCERTAIN = "uncertain"


class RateDirection(str, Enum):
    """Expected rate direction."""
    HIKE = "hike"
    CUT = "cut"
    HOLD = "hold"
    UNCERTAIN = "uncertain"


# ============================================================================
# Geopolitical Intelligence Schemas
# ============================================================================

class GeopoliticalEvent(BaseModel):
    """Individual geopolitical event affecting markets."""
    headline: str = Field(description="News headline summarizing the event")
    source: str = Field(default="Unknown", description="News source name (Reuters, Bloomberg, etc.)")
    impact_level: str = Field(default="medium", description="Impact level: low, medium, high, or extreme")
    affected_currencies: List[str] = Field(default_factory=list, description="Currency codes affected (USD, EUR, etc.)")
    summary: str = Field(default="", description="Brief summary of the event and its market implications")
    region: str = Field(default="Global", description="Geographic region affected")


class GeopoliticalAnalysis(BaseModel):
    """Comprehensive geopolitical risk assessment for trading."""
    risk_level: str = Field(default="low", description="Overall geopolitical risk: low, medium, high, extreme")
    events: List[GeopoliticalEvent] = Field(default_factory=list, description="Key geopolitical events currently affecting markets")
    trading_recommendation: str = Field(default="Normal trading conditions", description="How geopolitical factors should affect trading decisions")
    safe_haven_demand: str = Field(default="normal", description="Expected safe haven flows: low, normal, elevated, extreme")
    risk_currencies_warning: str = Field(default="", description="Warning about risk-sensitive currencies (AUD, NZD, etc.)")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Analysis timestamp")


# ============================================================================
# Central Bank Policy Schemas
# ============================================================================

class CentralBankStance(BaseModel):
    """Individual central bank policy stance."""
    bank: str = Field(description="Central bank name (Fed, ECB, BOE, BOJ, etc.)")
    stance: str = Field(default="neutral", description="Policy stance: hawkish, dovish, neutral")
    current_rate: Optional[float] = Field(default=None, description="Current interest rate")
    next_meeting: Optional[str] = Field(default=None, description="Date of next policy meeting")
    expected_action: str = Field(default="hold", description="Expected action: hike, cut, hold")
    probability: Optional[float] = Field(default=None, description="Probability of expected action (0-100)")
    key_statement: str = Field(default="", description="Key quote or statement from recent communications")
    currency_impact: str = Field(default="neutral", description="Expected impact on currency: bullish, bearish, neutral")


class CentralBankAnalysis(BaseModel):
    """Comprehensive central bank policy analysis."""
    fed: Optional[CentralBankStance] = Field(default=None, description="Federal Reserve stance")
    ecb: Optional[CentralBankStance] = Field(default=None, description="European Central Bank stance")
    boe: Optional[CentralBankStance] = Field(default=None, description="Bank of England stance")
    boj: Optional[CentralBankStance] = Field(default=None, description="Bank of Japan stance")
    rba: Optional[CentralBankStance] = Field(default=None, description="Reserve Bank of Australia stance")
    boc: Optional[CentralBankStance] = Field(default=None, description="Bank of Canada stance")
    divergence_plays: List[str] = Field(default_factory=list, description="Currency pairs benefiting from policy divergence")
    overall_bias: str = Field(default="neutral", description="Overall policy environment: tightening, easing, mixed")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Analysis timestamp")


# ============================================================================
# Economic Calendar Schemas
# ============================================================================

class EconomicCalendarEvent(BaseModel):
    """Individual economic calendar event."""
    datetime: str = Field(description="Event datetime in ISO format")
    currency: str = Field(description="Currency code affected (USD, EUR, etc.)")
    event: str = Field(description="Event name (NFP, CPI, GDP, etc.)")
    impact: str = Field(default="medium", description="Impact level: high, medium, low")
    forecast: Optional[str] = Field(default=None, description="Market forecast/expectation")
    previous: Optional[str] = Field(default=None, description="Previous reading")
    actual: Optional[str] = Field(default=None, description="Actual reading if released")


class EconomicCalendar(BaseModel):
    """Economic calendar with upcoming events."""
    events: List[EconomicCalendarEvent] = Field(default_factory=list, description="List of upcoming economic events")
    high_impact_count: int = Field(default=0, description="Number of high-impact events")
    next_major_event: Optional[EconomicCalendarEvent] = Field(default=None, description="Next high-impact event")
    blackout_periods: List[str] = Field(default_factory=list, description="Time periods to avoid trading")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Calendar fetch timestamp")


# ============================================================================
# Market Sentiment Schemas
# ============================================================================

class RetailSentiment(BaseModel):
    """Retail trader positioning (contrarian indicator)."""
    symbol: str = Field(description="Trading symbol")
    long_percent: float = Field(default=50.0, description="Percentage of retail traders long")
    short_percent: float = Field(default=50.0, description="Percentage of retail traders short")
    bias: str = Field(default="neutral", description="Retail bias: extreme_long, long, neutral, short, extreme_short")
    contrarian_signal: str = Field(default="neutral", description="Contrarian signal: long (when retail short), short (when retail long)")
    confidence: float = Field(default=50.0, description="Signal confidence 0-100")
    source: str = Field(default="", description="Data source")


class InstitutionalPositioning(BaseModel):
    """Institutional/COT positioning data."""
    currency: str = Field(description="Currency code")
    net_position: int = Field(default=0, description="Net speculative position")
    change_weekly: int = Field(default=0, description="Weekly change in net position")
    positioning: str = Field(default="neutral", description="Position: net_long, net_short, neutral")
    extreme_level: bool = Field(default=False, description="Whether positioning is at extreme levels")
    interpretation: str = Field(default="", description="Analysis of positioning implications")


class COTAnalysis(BaseModel):
    """Commitment of Traders comprehensive analysis."""
    currencies: List[InstitutionalPositioning] = Field(default_factory=list, description="COT data by currency")
    key_insights: List[str] = Field(default_factory=list, description="Key insights from COT data")
    reversal_signals: List[str] = Field(default_factory=list, description="Potential reversal signals from extreme positioning")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Analysis timestamp")


# ============================================================================
# Intermarket Analysis Schemas
# ============================================================================

class MarketTrend(BaseModel):
    """Individual market trend data."""
    market: str = Field(description="Market name (SPX, VIX, DXY, Gold, Oil, etc.)")
    trend: str = Field(default="neutral", description="Trend: bullish, bearish, neutral")
    current_value: Optional[float] = Field(default=None, description="Current value/price")
    change_percent: Optional[float] = Field(default=None, description="Percent change")
    key_level: Optional[float] = Field(default=None, description="Key technical level")


class IntermarketAnalysis(BaseModel):
    """Intermarket correlations and risk environment analysis."""
    spx: Optional[MarketTrend] = Field(default=None, description="S&P 500 trend")
    vix: Optional[MarketTrend] = Field(default=None, description="VIX volatility index")
    dxy: Optional[MarketTrend] = Field(default=None, description="Dollar Index trend")
    gold: Optional[MarketTrend] = Field(default=None, description="Gold trend")
    oil: Optional[MarketTrend] = Field(default=None, description="Oil trend")
    us10y: Optional[MarketTrend] = Field(default=None, description="US 10Y Treasury yield")
    risk_environment: str = Field(default="neutral", description="Risk environment: strong_risk_on, risk_on, neutral, risk_off, strong_risk_off")
    correlations_normal: bool = Field(default=True, description="Whether correlations are behaving normally")
    anomalies: List[str] = Field(default_factory=list, description="Any correlation anomalies detected")
    trading_implications: List[str] = Field(default_factory=list, description="Trading implications from intermarket analysis")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Analysis timestamp")


# ============================================================================
# Symbol-Specific Fundamental Analysis
# ============================================================================

class SymbolFundamentals(BaseModel):
    """Symbol-specific fundamental analysis."""
    symbol: str = Field(description="Trading symbol")
    base_currency: str = Field(default="", description="Base currency")
    quote_currency: str = Field(default="", description="Quote currency")
    fundamental_bias: str = Field(default="neutral", description="Fundamental bias: bullish, bearish, neutral")
    key_drivers: List[str] = Field(default_factory=list, description="Key fundamental drivers")
    upcoming_events: List[str] = Field(default_factory=list, description="Upcoming events affecting this pair")
    rate_differential: Optional[float] = Field(default=None, description="Interest rate differential")
    rate_differential_trend: str = Field(default="stable", description="Rate differential trend: widening, narrowing, stable")
    economic_strength_comparison: str = Field(default="", description="Relative economic strength comparison")
    trade_recommendation: str = Field(default="", description="Fundamental trade recommendation")
    confidence: float = Field(default=50.0, description="Confidence in analysis 0-100")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Analysis timestamp")


# ============================================================================
# Rate Expectations Schema
# ============================================================================

class RateExpectation(BaseModel):
    """Interest rate expectation for a specific central bank."""
    bank: str = Field(description="Central bank name")
    currency: str = Field(description="Currency affected")
    current_rate: float = Field(description="Current interest rate")
    next_meeting_date: Optional[str] = Field(default=None, description="Next meeting date")
    expected_rate: Optional[float] = Field(default=None, description="Expected rate after next meeting")
    hike_probability: float = Field(default=0.0, description="Probability of rate hike (0-100)")
    cut_probability: float = Field(default=0.0, description="Probability of rate cut (0-100)")
    hold_probability: float = Field(default=100.0, description="Probability of rate hold (0-100)")
    terminal_rate: Optional[float] = Field(default=None, description="Expected terminal rate")
    currency_impact: str = Field(default="neutral", description="Expected currency impact")


class RateExpectations(BaseModel):
    """Comprehensive interest rate expectations."""
    fed: Optional[RateExpectation] = Field(default=None, description="Federal Reserve rate expectations")
    ecb: Optional[RateExpectation] = Field(default=None, description="ECB rate expectations")
    boe: Optional[RateExpectation] = Field(default=None, description="BOE rate expectations")
    boj: Optional[RateExpectation] = Field(default=None, description="BOJ rate expectations")
    rate_differentials: dict = Field(default_factory=dict, description="Key rate differentials")
    carry_trade_outlook: str = Field(default="", description="Carry trade environment outlook")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Analysis timestamp")


# ============================================================================
# Comprehensive Market Intelligence (Master Schema)
# ============================================================================

class MarketIntelligence(BaseModel):
    """
    Master schema combining all intelligence sources.
    
    This is the comprehensive market intelligence object that gets
    passed to Claude for fundamental analysis context.
    """
    geopolitical: Optional[GeopoliticalAnalysis] = Field(default=None, description="Geopolitical risk assessment")
    central_banks: Optional[CentralBankAnalysis] = Field(default=None, description="Central bank policy analysis")
    economic_calendar: Optional[EconomicCalendar] = Field(default=None, description="Economic calendar")
    cot: Optional[COTAnalysis] = Field(default=None, description="COT institutional positioning")
    intermarket: Optional[IntermarketAnalysis] = Field(default=None, description="Intermarket analysis")
    rate_expectations: Optional[RateExpectations] = Field(default=None, description="Interest rate expectations")
    
    # Overall assessment
    overall_risk_level: str = Field(default="normal", description="Overall market risk: low, normal, elevated, high")
    trading_environment: str = Field(default="normal", description="Trading environment: excellent, good, normal, difficult, avoid")
    key_themes: List[str] = Field(default_factory=list, description="Key market themes")
    warnings: List[str] = Field(default_factory=list, description="Trading warnings and alerts")
    
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Intelligence timestamp")
    
    def to_claude_context(self) -> str:
        """Convert intelligence to formatted context for Claude's prompt."""
        sections = []
        
        # Geopolitical Section
        if self.geopolitical:
            geo = self.geopolitical
            sections.append(f"""### Geopolitical Risk Assessment
- Risk Level: **{geo.risk_level.upper()}**
- Safe Haven Demand: {geo.safe_haven_demand}
- Trading Recommendation: {geo.trading_recommendation}""")
            
            if geo.events:
                events_text = "\n".join([f"  - [{e.impact_level.upper()}] {e.headline}" for e in geo.events[:3]])
                sections.append(f"- Key Events:\n{events_text}")
            
            if geo.risk_level in ["high", "extreme"]:
                sections.append(f"⚠️ **HIGH GEOPOLITICAL RISK - REDUCE POSITION SIZES**")
        
        # Central Bank Section
        if self.central_banks:
            cb = self.central_banks
            cb_lines = []
            if cb.fed:
                cb_lines.append(f"- Fed: {cb.fed.stance.upper()} (Expected: {cb.fed.expected_action})")
            if cb.ecb:
                cb_lines.append(f"- ECB: {cb.ecb.stance.upper()} (Expected: {cb.ecb.expected_action})")
            if cb.boe:
                cb_lines.append(f"- BOE: {cb.boe.stance.upper()} (Expected: {cb.boe.expected_action})")
            
            if cb_lines:
                sections.append(f"""### Central Bank Policy
{chr(10).join(cb_lines)}
- Overall Bias: {cb.overall_bias}""")
                
                if cb.divergence_plays:
                    sections.append(f"- Divergence Plays: {', '.join(cb.divergence_plays)}")
        
        # Intermarket Section
        if self.intermarket:
            im = self.intermarket
            sections.append(f"""### Intermarket Analysis
- Risk Environment: **{im.risk_environment.upper().replace('_', ' ')}**""")
            
            if im.spx:
                sections.append(f"- S&P 500: {im.spx.trend}")
            if im.vix:
                sections.append(f"- VIX: {im.vix.current_value or 'N/A'} ({im.vix.trend})")
            if im.dxy:
                sections.append(f"- DXY: {im.dxy.trend}")
            
            if im.trading_implications:
                sections.append(f"- Implications: {'; '.join(im.trading_implications[:2])}")
        
        # Economic Calendar Section
        if self.economic_calendar and self.economic_calendar.next_major_event:
            evt = self.economic_calendar.next_major_event
            sections.append(f"""### Economic Calendar
- Next Major Event: {evt.event} ({evt.currency}) - {evt.datetime}
- High Impact Events Today: {self.economic_calendar.high_impact_count}""")
        
        # Overall Assessment
        sections.append(f"""### Overall Assessment
- Risk Level: {self.overall_risk_level.upper()}
- Environment: {self.trading_environment.upper()}""")
        
        if self.warnings:
            sections.append(f"⚠️ Warnings: {'; '.join(self.warnings)}")
        
        if self.key_themes:
            sections.append(f"- Key Themes: {', '.join(self.key_themes)}")
        
        return "\n\n".join(sections)
