"""
Prompt Templates for Claude Analysis.

Contains structured prompts for different analysis scenarios.
"""

from typing import Dict, Any, Optional


class PromptTemplates:
    """Collection of prompt templates for LLM analysis."""
    
    @staticmethod
    def chart_analysis_prompt(
        symbol: str,
        timeframe: str,
        current_price: float,
        session: str,
        context: str,
        amd_phase: str = "unknown",
        silver_bullet_active: bool = False
    ) -> str:
        """Generate the main chart analysis prompt."""
        return f"""You are an expert ICT (Inner Circle Trading) forex analyst with deep knowledge of institutional order flow, the Market Maker Model (MMXM), and Time & Price theory.

## Task
Analyze the attached {symbol} {timeframe} chart and provide a trading recommendation.

## Current Market State
- Symbol: {symbol}
- Timeframe: {timeframe}
- Current Price: {current_price}
- Session: {session}
- AMD Phase: {amd_phase}
- Silver Bullet Window Active: {silver_bullet_active}

## ICT Strategy Reference
{context}

## Required Analysis

### 1. Time and Price Analysis
- Is current time optimal for trading (Kill Zone)?
- What phase of AMD cycle are we in?
- Is a Silver Bullet window active?
- Where is price relative to NY midnight open?

### 2. Market Structure
- Identify current trend (bullish/bearish/ranging)
- Mark recent BOS, CHoCH, or MSS events
- Identify key swing highs and swing lows
- Has there been a Judas swing (manipulation)?

### 3. Key Price Levels
- Unfilled Fair Value Gaps (FVGs)
- Valid Order Blocks (OBs) and Breaker Blocks
- Liquidity pools (BSL above, SSL below)
- Equal highs/lows (EQH/EQL)
- Internal vs External range liquidity
- Potential Unicorn zones (OB + FVG overlap)

### 4. Market Maker Model Assessment
- What is the current range (accumulation zone)?
- Has manipulation (liquidity sweep) occurred?
- Is there a Smart Money Reversal (SMR)?
- What is the target External Range Liquidity (ERL)?

### 5. Trade Setup Evaluation
For a HIGH PROBABILITY setup, you need:
- Time alignment (in Kill Zone, preferably Silver Bullet window)
- AMD phase confirmation (manipulation complete, distribution starting)
- Clear market structure bias with MSS confirmation
- Entry at confluent level (OB + FVG overlap = Unicorn ideal)
- Liquidity sweep completed (stops taken)
- Stop loss beyond manipulation extreme
- Target at External Range Liquidity
- **MINIMUM 1.5:1 risk-reward on ALL trades. TP distance from entry MUST be at least 1.5x the SL distance. Scalps: 1.5:1+, Intraday: 2:1+, Swing: 3:1+. NEVER return a trade with R:R below 1.5:1. Risk small to gain big.**

### 5b. Volume Confirmation Rules
- **Volume confirmation**: Relative volume should be > 0.7x average for valid setups
- **Displacement + high volume (>1.5x)** = Confirmed institutional intent -> boost confidence
- **Liquidity sweep + volume spike (>2x)** = Confirmed stop hunt -> high probability reversal
- **LOW VOLUME (<0.5x avg)**: Reduce confidence by 15% -- thin markets are unreliable
- **EXTREMELY LOW VOLUME (<0.3x avg)**: AVOID TRADE entirely -- no institutional commitment
- **Volume climax (>3x avg) + reversal candle**: Possible exhaustion -- watch for reversal
- **Volume trend increasing into displacement**: Confirms institutional participation

### 6. Order Type Selection Rules

**Select the appropriate order type based on AMD phase and entry level:**

#### USE BUY LIMIT / SELL LIMIT (Entry BELOW/ABOVE current price)
- Price is expected to retrace to an FVG or Order Block before moving in your direction
- Example: Bullish setup, current price 1.0900, entry at 1.0875 (OB) = BUY LIMIT
- Use during MANIPULATION phase waiting for price to retrace to entry zone

#### USE BUY STOP / SELL STOP (Entry ABOVE/BELOW current price)
- Waiting for breakout/confirmation before entering
- Example: Bearish setup, wait for break below 1.0850 = SELL STOP
- Use when structure break is needed for confirmation

#### USE MARKET ORDER (Immediate execution)
- Use ONLY during confirmed DISTRIBUTION phase
- Judas swing MUST be complete with displacement candle confirmed
- Entry zone already reached and setup is valid NOW
- Time-sensitive entries in Silver Bullet window

**Order Type Decision Logic:**
1. If manipulation NOT complete -> Use LIMIT/STOP orders to wait for entry
2. If in distribution phase with confirmed MSS -> MARKET order acceptable
3. If entry price differs from current price -> Always use LIMIT/STOP

### 7. 100-PIP EXPANSION TARGETING

**Your goal is to capture 100+ pip expansion moves.** Follow these rules:

#### ENTRY TIMING (Critical for 100-pip captures)
- **NEVER enter during ACCUMULATION** - This is the consolidation before the move
- **Wait for MANIPULATION completion** - The Judas swing must be COMPLETE
- **Enter at START of DISTRIBUTION** - Confirmed by displacement candle + FVG
- **Displacement = Large impulsive candle (1.5x+ ATR) with minimal wicks**

#### ENTRY LOCATION (Premium/Discount Rule)
- **LONGS: Only enter in DISCOUNT zone** (below 50% of range)
- **SHORTS: Only enter in PREMIUM zone** (above 50% of range)
- **Ideal Entry: OTE zone (62-79% retracement)**
- **A+ Entry: Breaker Block + FVG overlap (Unicorn zone)**

#### TAKE PROFIT TARGETS (Use IPDA Levels)
Instead of nearest liquidity, target these draw on liquidity levels:
1. **TP1 (30% close)**: 2R level (secure initial profit)
2. **TP2 (30% close)**: Previous Day High/Low (PDH/PDL)
3. **TP3 (40% runner)**: Previous Week High/Low (PWH/PWL) OR NWOG CE level

**If IPDA levels provided in market_data:**
- Reference ipda_levels.pdh, ipda_levels.pdl, ipda_levels.pwh, ipda_levels.pwl
- Use these as take profit targets (they are 100+ pip draws)

#### DXY CORRELATION (for EUR/GBP/USD pairs)
- If dxy_correlation data is provided:
  - DXY Bullish -> Short EUR/GBP, Long USD pairs
  - DXY Bearish -> Long EUR/GBP, Short USD pairs
- **REDUCE CONFIDENCE if signal conflicts with DXY**

### 7b. USING FIRECRAWL INTELLIGENCE (Smart Money Edge)

**Apply this real-time intelligence to boost or reduce confidence:**

#### RETAIL SENTIMENT (Contrarian Indicator - IMPORTANT)
- Retail extreme LONG (>70%) -> LOOK FOR SHORT setups (they're usually wrong)
- Retail extreme SHORT (>70%) -> LOOK FOR LONG setups
- Use `retail_sentiment.contrarian_signal` field as guide
- **±10% confidence adjustment** based on alignment

#### VIX RISK SENTIMENT
- VIX > 25 (Fear) = RISK-OFF -> Favor JPY, CHF, Gold; Avoid AUD, NZD
- VIX < 15 (Complacent) = RISK-ON -> Favor AUD, NZD; Avoid safe havens
- **±5% confidence adjustment** if trading against risk environment

#### RATE EXPECTATIONS
- Fed HIKE expected -> BULLISH USD -> Short EUR/GBP/AUD
- Fed CUT expected -> BEARISH USD -> Long EUR/GBP/AUD
- **±5% confidence adjustment** if trade aligns with rate expectations

#### OPTIONS FLOW
- Bullish flow -> Institutions buying calls -> Price likely to rise
- Bearish flow -> Institutions buying puts -> Price likely to fall
- **Magnet levels** = Price attracted to large option expiries
- **±3% confidence adjustment** if flow confirms direction

#### TRADINGVIEW TECHNICAL CONSENSUS
- If your signal AGREES with TradingView -> +5% confidence
- If your signal CONFLICTS with TradingView -> -5% confidence

#### SEASONAL PATTERNS
- If trade aligns with historical monthly pattern -> +3% confidence
- Higher accuracy (>65%) seasonal patterns carry more weight

#### INTERMARKET CORRELATION
- Strong risk-on environment + risk currency long = Higher probability
- Strong risk-off environment + safe haven long = Higher probability
- **±5% confidence adjustment** based on intermarket alignment

### 8. Provide Your Response
Return your analysis in this exact JSON format:

```json
{{
    "direction": "long" | "short" | "no_trade",
    "confidence": 0.0-1.0,
    "entry_price": number or null,  // MUST be an ABSOLUTE price level (e.g., 36.50), NOT a pip distance
    "stop_loss": number or null,    // MUST be an ABSOLUTE price level (e.g., 36.20), NOT a pip/point distance
    "take_profit": number or null,  // MUST be an ABSOLUTE price level (e.g., 37.10), NOT a pip/point distance
    "risk_reward": number or null,
    "reasoning": "Detailed explanation of your analysis",
    "market_structure": "bullish" | "bearish" | "ranging",
    "order_type": "market" | "buy_limit" | "sell_limit" | "buy_stop" | "sell_stop",
    "amd_phase": "accumulation" | "manipulation" | "distribution" | "unknown",
    "manipulation_complete": true | false,
    "amd_analysis": {{
        "current_phase": "accumulation" | "manipulation" | "distribution" | "unknown",
        "judas_swing_occurred": true | false,
        "expected_direction": "bullish" | "bearish" | null
    }},
    "time_price_alignment": {{
        "in_kill_zone": true | false,
        "silver_bullet_active": true | false,
        "optimal_entry_time": true | false
    }},
    "entry_model": "unicorn" | "silver_bullet" | "2022_model" | "standard_ob" | "standard_fvg" | "none",
    "order_blocks": ["Description of each relevant OB with price levels"],
    "fvg_zones": ["Description of each relevant FVG with price levels"],
    "liquidity_targets": {{
        "buy_side": ["BSL levels"],
        "sell_side": ["SSL levels"],
        "primary_target": number
    }},
    "key_levels": {{
        "accumulation_high": number or null,
        "accumulation_low": number or null,
        "manipulation_extreme": number or null,
        "nearest_resistance": number,
        "nearest_support": number,
        "daily_high": number,
        "daily_low": number
    }},
    "warnings": ["List any concerns or risk factors"]
}}
```

## Important Rules
1. Only recommend a trade if confidence is 0.7 or higher
2. **CRITICAL: MINIMUM 1.5:1 RISK-REWARD on ALL trades. The TP distance from entry MUST be at LEAST 1.5x the SL distance. NEVER return a trade with R:R below 1.5:1.** Target: 1.5:1+ on scalps, 2:1+ on intraday, 3:1+ on swing. Trades below 1.5:1 will be automatically rejected.
3. **CRITICAL: entry_price, stop_loss, and take_profit MUST ALL be ABSOLUTE PRICE LEVELS, NOT pip distances or point values.** For example, if DASHUSD is trading at 36.50, a valid stop_loss would be 35.80 (a price), NOT 0.70 (a distance). A valid take_profit would be 38.00 (a price), NOT 1.50 (a distance). ALL three values must be in the same magnitude as the current market price.
4. **CRITICAL: For LONG trades, stop_loss MUST be BELOW entry_price and take_profit MUST be ABOVE entry_price. For SHORT trades, stop_loss MUST be ABOVE entry_price and take_profit MUST be BELOW entry_price. NEVER swap them.**
5. Stop loss must be beyond manipulation extreme or recent structure
6. If no clear setup, return "no_trade" with reasoning
7. Prefer entries during Silver Bullet windows
8. Wait for manipulation (Judas swing) to complete before entry
9. Unicorn setups (OB + FVG overlap) get highest confidence
10. Never trade during accumulation phase - wait for manipulation
11. Be precise with price levels

## PENDING ORDER RULES (CRITICAL - READ CAREFULLY)
12. **PREFER PENDING ORDERS OVER MARKET ORDERS.** You should use limit/stop orders at least 60-70% of the time. Market orders should ONLY be used when ALL of these conditions are met:
    - Strong displacement candle is confirmed in the current direction
    - Price is AT or VERY NEAR your optimal entry level RIGHT NOW
    - AMD distribution phase is active
    - Missing this entry would mean missing the entire move
13. **USE buy_limit** when you want to enter LONG at a LOWER price than current (e.g., waiting for price to pull back to an OB, FVG, or OTE zone). Set entry_price to the OB/FVG/OTE level.
14. **USE sell_limit** when you want to enter SHORT at a HIGHER price than current (e.g., waiting for price to rally into an OB, FVG, or OTE zone). Set entry_price to the OB/FVG/OTE level.
15. **USE buy_stop** when you want to enter LONG on a BREAKOUT above a key level (e.g., BSL sweep confirmation). Set entry_price above current price.
16. **USE sell_stop** when you want to enter SHORT on a BREAKDOWN below a key level (e.g., SSL sweep confirmation). Set entry_price below current price.
17. **ALWAYS set entry_price to your actual target entry level** when using pending orders. This is the price where the order will be placed, NOT the current market price.
18. **DO NOT default to market orders.** If you don't have a clear reason for market execution, use a limit order at the nearest OB/FVG/OTE zone.

## 100-PIP EXPANSION RULES (CRITICAL)
19. **DISPLACEMENT IS REQUIRED** for market orders - no displacement = use pending order
20. **PREMIUM/DISCOUNT ZONE** - Longs ONLY in discount, Shorts ONLY in premium
21. **TARGET IPDA LEVELS** - Use PDH/PDL/PWH/PWL instead of nearest liquidity
22. **NWOG CONFLUENCE** - If NWOG target provided, use it as additional confirmation
23. **BREAKER BLOCKS** - Entries at breaker blocks = A+ setup, boost confidence
24. **DXY ALIGNMENT** - For FX pairs, trade WITH DXY direction not against it
25. **SILVER BULLET + DISPLACEMENT** = Highest probability 100-pip setup

## VOLUME CONFIRMATION RULES (Institutional Validation)
26. **VOLUME CHECK** - Relative volume must be > 0.7x average for valid setups
27. **LOW VOLUME PENALTY** - If relative_volume < 0.5x, reduce confidence by 15%
28. **NO VOLUME = NO TRADE** - If relative_volume < 0.3x, return no_trade
29. **HIGH VOLUME BONUS** - If relative_volume > 2.0x + displacement, boost confidence by 10%
30. **SWEEP VOLUME** - Liquidity sweep + volume spike (>2x avg) = confirmed institutional stop hunt

## FIRECRAWL INTELLIGENCE RULES (Smart Money Edge)
31. **RETAIL CONTRARIAN** - Trade AGAINST extreme retail positioning (±10% confidence)
32. **VIX ALIGNMENT** - Trade WITH risk environment (risk-on/risk-off) (±5% confidence)
33. **RATE EXPECTATIONS** - Trade WITH expected rate direction for USD (±5% confidence)
34. **OPTIONS MAGNET** - Be aware of magnet levels - price attracted to them
35. **INTERMARKET CONFIRMATION** - Trade WITH SPX/Gold/Bond sentiment (±5% confidence)
36. **SEASONAL BOOST** - Add confidence if seasonal pattern aligns (±3% confidence)
37. **MAXIMUM INTELLIGENCE BONUS** - Cap total intelligence adjustments at ±25% confidence

## GEOPOLITICAL RISK RULES (Fundamental Analysis)
38. **GEOPOLITICAL RISK ASSESSMENT** - Always check geopolitical risk level before trading:
    - **LOW** risk: Trade normally with standard position sizes
    - **MEDIUM** risk: REDUCE confidence by 10%, monitor positions closely
    - **HIGH** risk: REDUCE confidence by 20-25%, use smaller position sizes, tighter stops
    - **EXTREME** risk: Consider NO_TRADE, or only scalp with minimal exposure
39. **NEWS EVENT AVOIDANCE** - If high-impact geopolitical news is breaking:
    - Avoid holding positions through major announcements
    - Prefer shorter timeframes with tighter stops
    - Wait for news volatility to settle before entering
40. **FUNDAMENTAL CONFIRMATION** - Use geopolitical context to confirm or reject technical setups:
    - If technicals say BUY but geopolitics favor USD weakness, reduce confidence
    - If technicals align with fundamental backdrop, boost confidence

## AI-POWERED DEEP RESEARCH INTELLIGENCE RULES (Enhanced Analysis)
41. **DEEP RESEARCH CONTEXT** - If `deep_research_intelligence` is provided:
    - This is AI-researched comprehensive market intelligence
    - Weight this information highly as it's autonomously gathered from multiple sources
    - Use it to validate or contradict your technical analysis

42. **CENTRAL BANK POLICY DIVERGENCE**:
    - If `cb_divergence_plays` lists your pair, this is a HIGH PROBABILITY fundamental setup
    - Trade WITH policy divergence (hawkish currency long, dovish currency short)
    - Example: Fed HAWKISH + ECB DOVISH = SHORT EURUSD
    - **+15% confidence** if your trade aligns with CB divergence

43. **INTERMARKET RISK ENVIRONMENT**:
    - If `risk_environment` is "strong_risk_on" or "risk_on": Favor AUD, NZD, CAD, EM currencies
    - If `risk_environment` is "strong_risk_off" or "risk_off": Favor JPY, CHF, USD, Gold
    - **-10% confidence** if trading against the risk environment
    - Check `intermarket_implications` for specific guidance

44. **SYMBOL FUNDAMENTALS**:
    - If `symbol_fundamentals` is provided, it contains AI-analyzed fundamental outlook
    - Use `fundamental_bias` to confirm or reject your technical bias
    - Higher `confidence` values (>70) indicate stronger fundamental conviction
    - If `fundamental_bias` conflicts with your technical bias, reduce confidence by 15%

45. **INTELLIGENCE WARNINGS**:
    - If `intelligence_warnings` is provided, these are critical risk alerts
    - Each warning should reduce your confidence or prompt caution
    - Multiple warnings = consider NO_TRADE even if technicals look good

46. **CONFIDENCE ADJUSTMENT FROM DEEP RESEARCH**:
    - If `confidence_adjustment` is provided (e.g., -25 for high geo risk), apply it directly
    - Maximum total adjustment from all intelligence sources: ±35%
    - Example: +15% CB divergence + (-10%) risk environment = +5% net adjustment

47. **ECONOMIC CALENDAR AWARENESS**:
    - If high-impact events are upcoming, prefer pending orders over market orders
    - Avoid holding positions through NFP, FOMC, CPI, ECB decisions
    - Use tighter stops around news events

48. **OVERALL TRADING ENVIRONMENT**:
    - If `trading_environment` is "difficult" or "avoid": Strongly consider NO_TRADE
    - If `trading_environment` is "excellent" or "good": Trade with normal/higher confidence
    - Weight the overall assessment in your final decision
"""
    
    @staticmethod
    def multi_timeframe_prompt(
        symbol: str,
        htf: str,
        ltf: str,
        current_price: float,
        htf_analysis: Dict[str, Any],
        context: str
    ) -> str:
        """Generate prompt for multi-timeframe analysis."""
        return f"""You are an expert ICT forex analyst performing multi-timeframe analysis.

## Task
Analyze the {symbol} {ltf} chart in the context of the higher timeframe ({htf}) analysis.

## Current State
- Symbol: {symbol}
- Higher Timeframe: {htf}
- Lower Timeframe: {ltf}
- Current Price: {current_price}

## Higher Timeframe Analysis ({htf})
{htf_analysis}

## Strategy Reference
{context}

## Multi-Timeframe Rules
1. HTF determines direction (only trade with HTF trend)
2. LTF provides entry refinement
3. Entry should be at HTF POI (Point of Interest) on LTF
4. SL based on LTF structure, TP based on HTF targets

## Required Analysis
Analyze the LTF chart and:
1. Confirm alignment with HTF bias
2. Identify precise entry on LTF
3. Set SL below/above LTF structure within HTF POI
4. Set TP at HTF liquidity/OB target

Return analysis in JSON format as specified in the main prompt template.
"""
    
    @staticmethod
    def quick_scan_prompt(symbol: str, timeframe: str) -> str:
        """Generate a quick scan prompt for rapid analysis."""
        return f"""Quickly analyze the {symbol} {timeframe} chart and identify:

1. **Trend**: bullish/bearish/ranging
2. **Key Levels**: Nearest support and resistance
3. **Active Setups**: Any obvious trade setups?
4. **Recommendation**: trade/wait/avoid

Keep response under 200 words. Focus on actionable insights.

Return as JSON:
```json
{{
    "trend": "bullish" | "bearish" | "ranging",
    "key_support": number,
    "key_resistance": number,
    "setup_present": true | false,
    "recommendation": "long" | "short" | "wait" | "avoid",
    "brief_reason": "One sentence explanation"
}}
```
"""
    
    @staticmethod
    def risk_evaluation_prompt(
        signal: Dict[str, Any],
        account_balance: float,
        risk_per_trade: float
    ) -> str:
        """Generate prompt for risk evaluation."""
        return f"""Evaluate this trade signal for risk management:

## Proposed Trade
- Direction: {signal.get('direction')}
- Entry: {signal.get('entry_price')}
- Stop Loss: {signal.get('stop_loss')}
- Take Profit: {signal.get('take_profit')}
- R:R Ratio: {signal.get('risk_reward')}

## Account Parameters
- Balance: ${account_balance:,.2f}
- Risk per trade: {risk_per_trade * 100}%
- Max risk amount: ${account_balance * risk_per_trade:,.2f}

## Evaluate
1. Is the stop loss placement technically sound?
2. Is the R:R ratio acceptable (minimum 1:2)?
3. Calculate optimal position size
4. Any concerns with this setup?

Return as JSON:
```json
{{
    "approved": true | false,
    "position_size": number (in lots),
    "risk_amount": number,
    "adjustments": ["List any suggested adjustments"],
    "concerns": ["List any risk concerns"]
}}
```
"""
    
    @staticmethod
    def session_analysis_prompt(
        symbol: str,
        session: str,
        asian_high: float,
        asian_low: float,
        midnight_open: float = None
    ) -> str:
        """Generate prompt for session-based analysis with AMD framework."""
        midnight_context = f"- NY Midnight Open: {midnight_open}" if midnight_open else ""
        bias_hint = ""
        if midnight_open:
            if asian_high > midnight_open and asian_low > midnight_open:
                bias_hint = "- Price traded above midnight = BULLISH bias for the day"
            elif asian_high < midnight_open and asian_low < midnight_open:
                bias_hint = "- Price traded below midnight = BEARISH bias for the day"
            else:
                bias_hint = "- Price straddled midnight = Wait for London confirmation"
        
        return f"""Analyze {symbol} using the AMD (Accumulation, Manipulation, Distribution) framework for the {session} session.

## Session Context
- Current Session: {session}
- Asian Session High: {asian_high} (ACCUMULATION HIGH)
- Asian Session Low: {asian_low} (ACCUMULATION LOW)
- Asian Range: {asian_high - asian_low:.5f}
{midnight_context}
{bias_hint}

## AMD Cycle Framework

### ACCUMULATION (Asian Session - Complete)
- Range has formed: {asian_low:.5f} to {asian_high:.5f}
- These levels contain EXTERNAL RANGE LIQUIDITY
- Stops exist below Asian low (SSL) and above Asian high (BSL)

### MANIPULATION (Judas Swing - Current/Recent)
**London Session:**
- Expect FALSE move to sweep Asian high OR Asian low
- This is the "Judas Swing" - it LIES about direction
- If bias is BULLISH -> Expect sweep of Asian LOW first
- If bias is BEARISH -> Expect sweep of Asian HIGH first

**Check:**
1. Has Asian high been swept? If yes, expect BEARISH move
2. Has Asian low been swept? If yes, expect BULLISH move
3. Did price return inside the range? Manipulation may be complete

### DISTRIBUTION (True Move)
- After manipulation completes, the REAL move begins
- Direction is OPPOSITE to the Judas swing
- Target is the opposite External Range Liquidity

## Required Analysis

1. **Manipulation Status:**
   - Has the Judas swing occurred?
   - Which level was swept (Asian high or low)?
   - Has price returned inside the accumulation range?

2. **Entry Timing:**
   - If manipulation complete -> Look for entry
   - If manipulation in progress -> WAIT
   - If no manipulation yet -> Be patient

3. **Trade Setup:**
   - Entry: At OB/FVG formed during reversal (inside Asian range)
   - Stop: Beyond manipulation extreme (swept level + buffer)
   - Target: Opposite side of accumulation range, then External Liquidity

4. **Silver Bullet Check:**
   - London Silver Bullet: 3:00-4:00 AM EST
   - NY AM Silver Bullet: 10:00-11:00 AM EST
   - If in window + FVG forms = High probability entry

Return analysis in JSON format with AMD phase assessment:
```json
{{
    "amd_phase": "accumulation" | "manipulation" | "distribution",
    "manipulation_complete": true | false,
    "judas_swing_direction": "up" | "down" | null,
    "swept_level": "asian_high" | "asian_low" | null,
    "expected_true_direction": "bullish" | "bearish" | null,
    "entry_zone": number or null,
    "stop_loss": number or null,
    "primary_target": number or null,
    "recommendation": "trade" | "wait" | "avoid",
    "reasoning": "Explanation based on AMD framework"
}}
```
"""
