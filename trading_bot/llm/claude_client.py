"""
Claude Opus 5 Client for Chart Analysis.

Integrates with Anthropic's Claude API to analyze forex charts
using vision capabilities and ICT strategy context.

Features:
- Async/await support for concurrent analysis
- Adaptive thinking with explicit effort (thinking is on by default on Opus 5;
  effort is the primary cost/latency control)
- Tool use for reliable structured JSON output (tool_choice=auto so it is
  compatible with adaptive thinking)
- Caching layer to avoid duplicate API calls
- Rate limit awareness with backoff
"""

import asyncio
import base64
import hashlib
import json
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import OrderedDict

import anthropic
from anthropic import AsyncAnthropic

from ..config import settings
from ..utils.logging import get_logger
from ..utils.win_optimization import validate_signal_coherence

logger = get_logger(__name__)


# Tool definition for structured trade signal output
TRADE_SIGNAL_TOOL = {
    "name": "submit_trade_analysis",
    "description": "Submit the trade analysis and recommendation based on ICT methodology.",
    # strict: the API guarantees tool inputs match this schema exactly (valid enums,
    # required fields present, no extra keys). Requires additionalProperties=false and
    # no numeric range constraints (those live in descriptions instead).
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["long", "short", "no_trade"],
                "description": "Trade direction recommendation"
            },
            "confidence": {
                "type": "number",
                "description": "Confidence level from 0.0 to 1.0 (values outside this range are invalid)"
            },
            "entry_price": {
                "type": ["number", "null"],
                "description": "Suggested entry price. REQUIRED number when direction is long or short; null only for no_trade."
            },
            "stop_loss": {
                "type": ["number", "null"],
                "description": "Stop loss price — REQUIRED number when direction is long or short; null only for no_trade. MUST be a different price from entry_price. For LONG: SL must be BELOW entry (place beyond the nearest structure low/OB). For SHORT: SL must be ABOVE entry (place beyond the nearest structure high/OB). NEVER set SL equal to entry."
            },
            "take_profit": {
                "type": ["number", "null"],
                "description": "Take profit price — REQUIRED number when direction is long or short; null only for no_trade. For LONG: TP must be ABOVE entry. For SHORT: TP must be BELOW entry."
            },
            "risk_reward": {
                "type": ["number", "null"],
                "description": "Risk/reward ratio"
            },
            "reasoning": {
                "type": "string",
                "description": "4-8 sentences naming the SPECIFIC confirmations you observed on the charts (e.g. 'M15 swept the 1.0845 low, displaced up, and left an FVG at 1.0862'). State the setup, the entry level and why, and the SL/TP basis. Do NOT restate the rules from the prompt or list generic ICT definitions — only what THIS chart shows."
            },
            "market_structure": {
                "type": "string",
                "enum": ["bullish", "bearish", "ranging"],
                "description": "Current market structure"
            },
            "trade_type": {
                "type": "string",
                "enum": ["scalp", "intraday", "swing"],
                "description": "REQUIRED: Classify this trade based on setup timeframe. 'scalp' = M5/M1 setup, target 5-20 pips, hold <30 min. 'intraday' = M15/H1 setup, target 20-80 pips, hold <8 hrs. 'swing' = H4/D1 setup, target 80+ pips, hold 1-5 days."
            },
            "order_type": {
                "type": "string",
                "enum": ["market", "buy_limit", "sell_limit", "buy_stop", "sell_stop"],
                "description": "Choose per the ORDER TYPE SELECTION section of the prompt: 'buy_limit'/'sell_limit' to enter INTO a level (retracement fill, correct zone only), 'buy_stop'/'sell_stop' to enter THROUGH a level (breakout), 'market' only when displacement is already confirmed and price is at your level now. Zone rule (buy discount / sell premium) is mandatory — see system message."
            },
            "amd_phase": {
                "type": "string",
                "enum": ["accumulation", "manipulation", "distribution", "unknown"],
                "description": "Current AMD (Accumulation, Manipulation, Distribution) cycle phase"
            },
            "manipulation_complete": {
                "type": "boolean",
                "description": "True if Judas swing/manipulation phase reversal is confirmed"
            },
            "order_blocks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Descriptions of relevant order blocks"
            },
            "fvg_zones": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Descriptions of relevant FVGs"
            },
            "liquidity_targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Liquidity targets identified"
            },
            "key_levels": {
                "type": "object",
                "properties": {
                    "resistance_1": {"type": ["number", "null"]},
                    "support_1": {"type": ["number", "null"]},
                    "daily_high": {"type": ["number", "null"]},
                    "daily_low": {"type": ["number", "null"]}
                },
                "additionalProperties": False,
                "description": "Key price levels"
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Risk factors or concerns"
            }
        },
        "required": [
            "direction",
            "confidence",
            "entry_price",
            "stop_loss",
            "take_profit",
            "reasoning",
            "market_structure",
            "trade_type",
        ],
        "additionalProperties": False
    }
}


@dataclass
class TradeSignal:
    """
    Represents a trade signal generated by Claude.
    """
    direction: str                  # 'long', 'short', or 'no_trade'
    confidence: float               # 0-1 confidence level
    entry_price: Optional[float]    # Suggested entry price
    stop_loss: Optional[float]      # Suggested stop loss
    take_profit: Optional[float]    # Suggested take profit
    risk_reward: Optional[float]    # Risk/reward ratio
    reasoning: str                  # Explanation of the analysis
    
    # ICT concepts identified
    market_structure: Optional[str] = None
    order_blocks: List[str] = field(default_factory=list)
    fvg_zones: List[str] = field(default_factory=list)
    liquidity_targets: List[str] = field(default_factory=list)
    
    # Trade classification
    trade_type: str = "intraday"    # scalp, intraday, swing
    
    # Pending order fields
    order_type: str = "market"      # market, buy_limit, sell_limit, buy_stop, sell_stop
    amd_phase: str = "unknown"      # accumulation, manipulation, distribution, unknown
    manipulation_complete: bool = False  # True if Judas swing reversal confirmed
    
    # Reversal re-entry flag (bypasses direction flip cooldown)
    reversal_reentry: bool = False
    
    @property
    def is_valid(self) -> bool:
        """Check if signal meets minimum requirements."""
        if self.direction == 'no_trade':
            return True
        return (
            self.entry_price is not None and
            self.stop_loss is not None and
            self.take_profit is not None and
            self.confidence >= 0.6
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "direction": self.direction,
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "reasoning": self.reasoning,
            "market_structure": self.market_structure,
            "trade_type": self.trade_type,
            "order_blocks": self.order_blocks,
            "fvg_zones": self.fvg_zones,
            "liquidity_targets": self.liquidity_targets,
            "order_type": self.order_type,
            "amd_phase": self.amd_phase,
            "manipulation_complete": self.manipulation_complete
        }


@dataclass
class AnalysisResult:
    """Complete analysis result from Claude."""
    signal: TradeSignal
    raw_response: str
    analysis_summary: str
    key_levels: Dict[str, float]
    warnings: List[str]
    cached: bool = False
    analysis_time: float = 0.0  # Time taken for analysis in seconds
    
    def to_dict(self) -> dict:
        return {
            "signal": self.signal.to_dict(),
            "analysis_summary": self.analysis_summary,
            "key_levels": self.key_levels,
            "warnings": self.warnings,
            "cached": self.cached,
            "analysis_time": self.analysis_time
        }


class AnalysisCache:
    """
    LRU cache for analysis results.
    
    Avoids duplicate API calls for same chart conditions.
    """
    
    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        """
        Initialize cache.
        
        Args:
            max_size: Maximum number of cached items
            ttl_seconds: Time-to-live for cache entries
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[AnalysisResult, float]] = OrderedDict()
        self._lock = asyncio.Lock()
    
    def _generate_key(
        self,
        symbol: str,
        timeframe: str,
        image_hash: str,
        market_data: Optional[Dict[str, Any]]
    ) -> str:
        """Generate cache key from analysis parameters."""
        key_parts = [
            symbol,
            timeframe,
            image_hash[:16],
            str(market_data.get('session', '')) if market_data else ''
        ]
        return hashlib.md5('|'.join(key_parts).encode()).hexdigest()
    
    async def get(
        self,
        symbol: str,
        timeframe: str,
        image_hash: str,
        market_data: Optional[Dict[str, Any]] = None
    ) -> Optional[AnalysisResult]:
        """Get cached analysis if available and not expired."""
        async with self._lock:
            key = self._generate_key(symbol, timeframe, image_hash, market_data)
            
            if key in self._cache:
                result, timestamp = self._cache[key]
                
                # Check TTL
                if time.time() - timestamp < self.ttl_seconds:
                    # Move to end (LRU)
                    self._cache.move_to_end(key)
                    result.cached = True
                    logger.debug(f"Cache hit for {symbol} {timeframe}")
                    return result
                else:
                    # Expired - remove
                    del self._cache[key]
            
            return None
    
    async def set(
        self,
        symbol: str,
        timeframe: str,
        image_hash: str,
        result: AnalysisResult,
        market_data: Optional[Dict[str, Any]] = None
    ):
        """Store analysis result in cache."""
        async with self._lock:
            key = self._generate_key(symbol, timeframe, image_hash, market_data)
            
            # Remove oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = (result, time.time())
            logger.debug(f"Cached analysis for {symbol} {timeframe}")
    
    async def clear(self):
        """Clear all cached entries."""
        async with self._lock:
            self._cache.clear()
    
    @property
    def size(self) -> int:
        """Current cache size."""
        return len(self._cache)


# =============================================================================
# STATIC ICT ANALYSIS RULESET
# -----------------------------------------------------------------------------
# These rules never change between calls, so they live in the (prompt-cached)
# system message instead of being rebuilt into every user prompt. Keeping them
# here as a single constant is both the cache anchor and the single source of
# truth for the methodology. Dynamic, per-symbol context is assembled separately
# in ClaudeClient._build_analysis_prompt().
# =============================================================================
ANALYSIS_RULES = """## Analysis Required

Analyze the chart image(s) and provide:

1. **Market Structure Analysis**
   - Current trend direction
   - Recent BOS (Break of Structure) or CHoCH (Change of Character)
   - Key swing highs and lows

2. **Key ICT Levels**
   - Fair Value Gaps (FVGs) - unfilled imbalances
   - Order Blocks - institutional entry zones
   - Liquidity pools - buy-side and sell-side

3. **Trade Recommendation**
   Use the submit_trade_analysis tool to provide your structured recommendation.

## EXAMPLE OF A GOOD SIGNAL (format + reasoning quality reference)
This is a generic example to show the expected shape — adapt every value to the actual chart,
never copy these numbers. A clean long: price is ~1.0850, D1/H4 bullish, M15 swept the 1.0838
session low and reclaimed it, an M5 displacement candle closed leaving an FVG at 1.0846-1.0849.
- direction: "long", trade_type: "intraday", confidence: 0.82
- order_type: "buy_limit", entry_price: 1.0847 (into the FVG, discount zone)
- stop_loss: 1.0833 (below the swept low + buffer — BELOW entry)
- take_profit: 1.0889 (prior day high / BSL — ABOVE entry), risk_reward: 3.0
- reasoning: names the sweep, the displacement, the FVG entry, and the SL/TP basis in 4-6 sentences.
All three prices are absolute levels in the same magnitude as current price; SL and TP are on the
correct sides of entry; R:R clears the 2:1 intraday minimum.

## ⚠️ CRITICAL WARNINGS - DANGEROUS PAIRS

**NEVER TRADE these pairs - ALWAYS return "no_trade" for:**
- Any pair ending in "BTC" (e.g., DASHBTC, ETHBTC, LTCBTC)
- Any pair ending in "BIT" (e.g., XRPBIT, EOSBIT, IOTABIT)

**Why:** These pairs are quoted in Bitcoin, not USD. Our position sizing assumes USD quote currency.
Trading these causes CATASTROPHIC losses due to incorrect position sizing.
If the current symbol ends in BTC or BIT, you MUST return direction="no_trade" with reasoning explaining it's a dangerous BTC-quoted pair.

## CORE MANDATE -- CONFIRMED SETUPS FIRST, ANTICIPATORY SECOND:
You operate in TWO modes:

MODE 1 (PRIMARY) -- REACTIVE: Signal a trade when price has ALREADY confirmed a setup.
Before recommending a MARKET order, cite at least TWO confirmations that have ALREADY
OCCURRED on the chart (not "developing" or "likely"):
1. A displacement candle has CLOSED (strong impulsive move with body > 70% of range)
2. A Break of Structure (BOS) or Change of Character (CHoCH) has PRINTED on M1/M5
3. A liquidity sweep has COMPLETED (wick swept a high/low and price reclaimed)
4. Price is AT or INSIDE a valid FVG/OB entry zone RIGHT NOW (not approaching it)
If you cannot cite two of these for a MARKET order, do NOT use a market order.

MODE 2 (SECONDARY) -- ANTICIPATORY: When D1+H4 agree on direction but M15 is pulling
back (opposing), you MAY place a PENDING LIMIT ORDER (buy_limit/sell_limit) at a key
level (OB, FVG, liquidity sweep zone) in the HTF direction. Requirements:
- Order type MUST be buy_limit or sell_limit (never market)
- Entry must be at a specific structural level (name it in reasoning)
- Confidence capped at 55-60%
- This is NOT predicting -- it is positioning at a key level where price is likely
  to react IF it arrives there

NEVER use a MARKET order based on prediction. Anticipation is only valid via pending
limit orders at defined structural levels.

## Important Rules -- PATIENCE IS THE EDGE:
- **Do NOT force a trade.** If the setup is not textbook (swing validation, confluence, clear levels), return no_trade. It is ALWAYS better to miss a move than to enter poorly and draw down.
- **Quality over quantity.** One perfect entry with circular price action confirmation is worth more than five mediocre entries. Wait for the setup to come to you.
- **Confidence must reflect CONFIRMED evidence, not conviction in a direction.** Use this scale:
  - 0.60-0.69: One confirmation present, setup developing but incomplete
  - 0.70-0.79: Two confirmations present, valid but not ideal
  - 0.80-0.89: Three+ confirmations, strong confluence, kill zone timing
  - 0.90-1.00: Full confluence: swing validation + displacement + sweep + FVG/OB + volume
  Do NOT park at exactly 0.75 every time. Your confidence MUST vary based on the actual evidence.
  This scale applies to EVERY signal — scalps, intraday, and swings, market and pending orders alike.

## CONFIDENCE ADJUSTMENT RULES (SINGLE SOURCE OF TRUTH for numeric confidence):
These are the ONLY numeric confidence adjustments you may apply. Every OTHER section
describes either (a) confluence factors that feed your BASE confidence scale above, or
(b) items to put in the `warnings` field. No other section may add or subtract a
specific percentage.

Your stated confidence is your SETUP QUALITY assessment. Start from the confidence
scale above (based on how many confirmations are present), then apply AT MOST -20%
total from these SETUP-QUALITY factors only:
- D1/H4/H1 misaligned: -10%
- Against TV consensus: -5%
- Total reduction from the above MUST NOT exceed -20%.

Do NOT adjust confidence for SESSION TIMING or VOLUME. The system applies its own
session and volume adjustments after you respond, so adjusting here would double-count.
If you see low volume or off-session timing, put it in the `warnings` field and leave
your confidence number unchanged. (Volume can still drive a no_trade decision per the
Volume Confirmation Rules below — that is a setup validity call, not a confidence tweak.)

- **MINIMUM 1.5:1 R:R on ALL trades (every trade_type, including scalps)** — see the R:R GUIDANCE in the trade-type section for the per-type targets.
- Session timing is handled by the system — focus on setup quality.
- Identify specific price levels for entry, SL, and TP using M1/M5 precision
- **CRITICAL: SL must NEVER equal entry price.** For LONG trades, SL must be placed BELOW entry (beyond the nearest swing low or OB). For SHORT trades, SL must be placed ABOVE entry (beyond the nearest swing high or OB). A zero-distance SL is invalid and will be rejected.
- If genuinely no setup exists (ranging, no structure, no POI nearby), recommend "no_trade" with your reasoning
- RESPECT news blackouts - recommend no_trade, or add the blackout to the `warnings` field (do not apply an ad-hoc confidence reduction — see CONFIDENCE ADJUSTMENT RULES)
- Consider recent performance and current streak when setting confidence
- **ONLY trade USD-quoted pairs** (ending in USD, USDT, or standard forex like EURUSD)
- **Do NOT flip direction without cause.** If market structure has not changed since the last analysis, do not switch from long to short or vice versa. Unchanged chart = unchanged signal or no_trade.

## Volume Confirmation Rules (Institutional Validation — single volume ladder):
Volume affects SETUP VALIDITY and CONFLUENCE, never a numeric confidence tweak (see
CONFIDENCE ADJUSTMENT RULES). Apply this one ladder:
- EXTREMELY LOW VOLUME (<0.3x avg): return no_trade — no institutional commitment.
- LOW / BELOW-AVERAGE VOLUME (0.3x-0.7x avg): the setup can still be valid, but add a
  warning noting the thin participation.
- NORMAL VOLUME (0.7x-2.0x avg): no volume concern.
- HIGH VOLUME (>2.0x avg) WITH displacement: strong institutional commitment — counts as an
  extra confirmation feeding your base confidence scale.
- Liquidity sweep + volume spike (>2x avg) = confirmed stop hunt, a strong reversal confluence.
- Volume climax (>3x avg) + reversal candle: possible exhaustion — note in warnings.

**Precious Metals Notes (XAUUSD/XAGUSD):**
- Gold/Silver have wider typical stops than forex (30-50 pips for gold)
- Consider the gold/silver ratio when analyzing either metal
- Both metals are safe havens - strong during geopolitical uncertainty
- Silver is more volatile (~2x gold moves) - adjust position size accordingly
- Watch USD correlation (typically inverse)

Reasoning in the submit_trade_analysis tool must be 4-8 sentences naming specific chart
confirmations only — no preamble and no generic ICT definitions.
"""


# Conciseness reminder placed LAST in the analysis system message stack (after
# strategy_context). Opus 5 docs: length is steered by prompting, not effort, and
# a short reminder near the end of a long system prompt is most effective.
ANALYSIS_TONE_PREFERENCE = """<tone_preference>
Keep outputs reasonably concise. Prefer calling submit_trade_analysis over prose.
Do not restate the ruleset, pad with filler, or narrate a plan before the tool call.
Call submit_trade_analysis as soon as you have a decision — do not burn the output
budget on long preambles or restated methodology. Always include entry, stop_loss,
and take_profit in the tool call when direction is long or short.
</tone_preference>"""


# Approximate Claude Opus 5 pricing in USD per million tokens, used only for the
# estimated_cost_usd column in usage telemetry (cache write = 1.25x input for the
# 5-minute ephemeral tier; cache read = 0.1x input). Same $/MTok as Opus 4.8.
# Update if Anthropic reprices. Alias kept for older imports/tests during transition.
OPUS_5_PRICING = {
    "input": 5.00,
    "output": 25.00,
    "cache_read": 0.50,
    "cache_write": 6.25,
}
OPUS_48_PRICING = OPUS_5_PRICING  # backwards-compatible alias


# JSON schema for the trade judge's structured output (Opus 5 structured outputs).
# Passed via output_config.format so the verdict is guaranteed-parseable JSON rather
# than relying on regex extraction. Structured outputs require additionalProperties=false
# and disallow unsupported constraints (min/maxLength, numeric bounds, etc.).
JUDGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["APPROVE", "DEMOTE", "REJECT"]},
        "reason": {"type": "string"},
        "suggested_entry": {"type": ["number", "null"]},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reason", "suggested_entry", "risk_flags"],
    "additionalProperties": False,
}


# Static trade-judge rubric. Lives in a prompt-cached system block so the judge —
# which runs on EVERY signal at medium effort — only bills these tokens once per
# cache window. Per-trade facts (proposed trade, risk metrics, learnings) stay in
# the user message built by judge_trade().
JUDGE_RUBRIC = """You are a TRADE JUDGE — a risk-focused second opinion before a trade is executed with real money.

A trade analyst proposes a trade in the user message. Your job is to check it against learned patterns and risk math. You are NOT re-analyzing the market — the analysis is already done. You are validating whether this trade should proceed NOW at market price, or whether it should be DEMOTED to a pending limit order at a better entry price. Judge every trade on its own merit.

## SCOPE
Deliver exactly the verdict JSON asked for. Do not widen into market re-analysis,
unsolicited strategy advice, or extra checklist items beyond Step 1/2. List concerns
in risk_flags (do not under-report); Step 2 decides what blocks the trade. At most
8 risk_flags.

## R:R TARGETS (per trade type)
- SCALP: target 1.5:1 | INTRADAY: target 2:1 | SWING: target 3:1
- ABSOLUTE FLOOR for ALL trade types: 1.5:1
- SCALP trades have tighter SL/TP and shorter hold times — this is NORMAL, do not penalize.
- SWING trades should have wider SL/TP and require full HTF alignment.
- Only a R:R below the 1.5:1 floor is a rejectable defect; between 1.5:1 and the trade-type
  target is a warning, not a reject.

## Step 1 — Find every concern (coverage, do NOT filter here)
List EVERY concern you find in `risk_flags`, including low-confidence or minor ones. Do not
suppress a flag because it feels minor — the verdict logic in Step 2 decides what actually blocks
the trade. Prefix each flag with a severity: "critical:", "warning:", or "note:". Check at minimum:
- Does the trade match any KNOWN LOSING PATTERN from the past learnings in the user message?
- Is the SL on the correct side of entry? Is the TP on the correct side of entry?
- Is the R:R at or above the 1.5:1 ABSOLUTE FLOOR? Is it at or above the trade-type target from
  the R:R TARGETS table? (Below 1.5:1 is a "critical:" flag; between 1.5:1 and the target is a
  "warning:" flag.)
- Is the risk per trade appropriate for an account of this size?
- Is the current session appropriate for this symbol and setup?

## Step 2 — Decide the verdict from concrete criteria only
Base the verdict ONLY on the enumerated criteria below, not on overall "feel". A pile of
"warning:"/"note:" flags does NOT justify a REJECT on its own.

REJECT only if AT LEAST ONE of these is true:
(a) SL or TP is on the WRONG side of entry (long: SL must be < entry < TP; short: TP < entry < SL).
(b) The daily trade limit is already reached (Trades Today at or above the max shown in Risk Metrics).
(c) The trade matches a NAMED losing pattern from the learning context.
(d) R:R is below the 1.5:1 ABSOLUTE FLOOR (after any SL adjustment). A trade that is above
    1.5:1 but below its trade-type target does NOT reject on this criterion — record it as a
    "warning:" flag and let (a)-(c) decide.

DEMOTE (convert to a tighter pending limit order) only if entry price has a CONCRETE, specific
problem — e.g. entry is chasing into premium/discount against the zone rule, or sits past the
ideal OB/FVG level — and none of the REJECT criteria fire.
- For LONG: suggested_entry must be BELOW current entry (buy cheaper).
- For SHORT: suggested_entry must be ABOVE current entry (sell higher).
- Default improvement 0.1%-0.3% from the proposed entry. NEVER suggest a WORSE entry than proposed.

APPROVE in all other cases. Confidence alone neither approves nor rejects.

## Hard exceptions
- NEVER REJECT or DEMOTE solely on position size % when the position is AT BROKER MINIMUM LOT SIZE.
  The trader cannot go smaller; judge purely on technical merit. Record it as a "note:" flag only.

Keep `reason` to ONE sentence. No preamble, no markdown, no restating the rubric.

Your entire response must be a single JSON object (no surrounding prose or markdown) with
exactly this shape:
- "verdict": "APPROVE" or "DEMOTE" or "REJECT"
- "reason": one sentence explanation
- "suggested_entry": float price, or null if APPROVE
- "risk_flags": array of strings, each prefixed "critical:", "warning:", or "note:"

<tone_preference>
Keep outputs concise. Prefer the JSON verdict over narration.
</tone_preference>
"""


# JSON schemas for light-task structured outputs (Opus 5 output_config.format).
# Guarantees parseable JSON instead of regex extraction with silent degraded
# fallbacks. NOTE: generate_weekly_insights is intentionally NOT schema-constrained
# because its symbol_insights/session_insights are free-form dicts, which the strict
# grammar (additionalProperties must be false) cannot express.
POSITION_SIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended_lots": {"type": "number"},
        "reasoning": {"type": "string"},
        "risk_assessment": {"type": "string", "enum": ["low", "medium", "high"]},
        "size_adjustment": {"type": "string"},
    },
    "required": ["recommended_lots", "reasoning", "risk_assessment"],
    "additionalProperties": False,
}

TRADE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["win", "loss", "breakeven"]},
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "analysis": {"type": "string"},
        "what_went_right": {"type": "array", "items": {"type": "string"}},
        "what_went_wrong": {"type": "array", "items": {"type": "string"}},
        "learnings": {"type": "array", "items": {"type": "string"}},
        "would_take_again": {"type": "boolean"},
        "improvement_suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["outcome", "grade", "analysis", "what_went_right", "what_went_wrong",
                 "learnings", "would_take_again", "improvement_suggestions"],
    "additionalProperties": False,
}

WEEKLY_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "performance_grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "summary": {"type": "string"},
        "patterns_identified": {"type": "array", "items": {"type": "string"}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "focus_for_next_week": {"type": "string"},
        "risk_adjustment": {"type": "string", "enum": ["increase", "maintain", "decrease"]},
    },
    "required": ["performance_grade", "summary", "patterns_identified", "strengths",
                 "weaknesses", "recommendations", "focus_for_next_week", "risk_adjustment"],
    "additionalProperties": False,
}

SCALING_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended_mode": {"type": "string", "enum": ["aggressive", "normal", "conservative", "defensive"]},
        "reasoning": {"type": "string"},
        "risk_multiplier": {"type": "number"},
        "setup_filter": {"type": "string", "enum": ["all", "A_and_B", "A_only"]},
        "confidence_threshold": {"type": "number"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recommended_mode", "reasoning", "risk_multiplier", "setup_filter",
                 "confidence_threshold", "warnings"],
    "additionalProperties": False,
}


class ClaudeClient:
    """
    Async client for Claude Opus 5 chart analysis.
    
    Uses Anthropic's API with vision capabilities to analyze
    forex chart screenshots and generate trade signals based
    on ICT methodology.
    
    Features:
    - Full async/await support
    - Concurrent multi-symbol analysis
    - Response caching
    - Rate limit handling
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 64000,
        temperature: float = 0.3,
        max_retries: int = 3,
        cache_ttl: int = 300,
        cache_size: int = 100
    ):
        """
        Initialize the Claude client.
        
        Args:
            api_key: Anthropic API key (uses settings if not provided)
            model: Model to use (uses settings if not provided)
            max_tokens: Maximum output tokens for heavy analysis calls. On Opus 5
                this caps thinking + response combined. 32k still truncated some
                medium-effort XAUUSD analyses before the tool call; 64k is the
                production floor.
            temperature: Retained for backwards compatibility only. NOT sent to
                Opus 5 (non-default sampling params return a 400 error); behavior
                is steered via prompting and the effort parameter instead.
            max_retries: Maximum retry attempts for API calls
            cache_ttl: Cache time-to-live in seconds
            cache_size: Maximum cache entries
        """
        self.api_key = api_key or settings.claude.api_key
        # Legacy attribute: no runtime call uses self.model anymore (kept only so
        # external readers of client.model don't break). All calls use model_heavy/
        # model_light below, both pinned to Opus 5.
        self.model = model or settings.claude.model
        self.model_heavy = "claude-opus-5"  # Best model for chart analysis + trade judge
        self.model_light = "claude-opus-5"  # Opus 5 everywhere — light tasks too (re-evals, reviews)
        # Effort is the primary cost/latency knob on Opus 5 (thinking stays on).
        # Keep effort constant within a cached conversation/prompt family.
        self.effort_heavy = "medium"   # chart analysis — medium on Opus 5 (cost/latency); raise to high/xhigh only after evals
        self.effort_judge = "medium"   # trade judge: rubric is narrow; medium is enough on Opus 5
        self.effort_light = "low"      # sizing, scaling, re-evals, and other narrow helpers
        self.effort_review = "medium"  # trade reviews + weekly insights (need more depth)
        self.max_tokens = max_tokens
        self.temperature = temperature  # kept for compat; NOT sent to Opus 5 (any task)
        self.max_retries = max_retries
        
        # Initialize cache
        self._cache = AnalysisCache(max_size=cache_size, ttl_seconds=cache_ttl)
        
        # Rate limiting
        self._request_timestamps: List[float] = []
        self._rate_limit_window = 60  # seconds
        self._rate_limit_max = 50  # requests per window
        self._rate_lock = asyncio.Lock()
        
        # Initialize clients
        self.sync_client: Optional[anthropic.Anthropic] = None
        self.async_client: Optional[AsyncAnthropic] = None
        
        if not self.api_key:
            logger.warning("No Anthropic API key configured")
        else:
            # Initialize both sync and async clients
            # 600s client timeout: Opus 5 analysis with vision + thinking can run
            # several minutes; streaming requests need headroom beyond the SDK's
            # non-stream 10-minute estimate gate.
            self.sync_client = anthropic.Anthropic(
                api_key=self.api_key,
                max_retries=max_retries,
                timeout=600.0,
            )
            self.async_client = AsyncAnthropic(
                api_key=self.api_key,
                max_retries=max_retries,
                timeout=600.0,
            )
            logger.info(f"Claude client initialized — analysis: {self.model_heavy}, light tasks: {self.model_light}")
    
    async def _check_rate_limit(self):
        """Check and enforce rate limiting. Sleeps OUTSIDE the lock to avoid convoy starvation."""
        while True:
            async with self._rate_lock:
                now = time.time()
                
                # Remove old timestamps
                self._request_timestamps = [
                    ts for ts in self._request_timestamps
                    if now - ts < self._rate_limit_window
                ]
                
                # If under the limit, record this request and proceed
                if len(self._request_timestamps) < self._rate_limit_max:
                    self._request_timestamps.append(time.time())
                    return  # OK to proceed
                
                # Calculate wait time
                oldest = self._request_timestamps[0]
                wait_time = self._rate_limit_window - (now - oldest) + 1
            
            # Sleep OUTSIDE the lock so other coroutines aren't starved
            if wait_time > 0:
                logger.warning(f"Rate limit reached, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
            # Loop back and re-check under the lock
    
    async def analyze_chart_async(
        self,
        chart_image_base64: str,
        symbol: str,
        timeframe: str,
        strategy_context: str,
        market_data: Optional[Dict[str, Any]] = None,
        analysis_data: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        additional_charts: Optional[List[Dict[str, str]]] = None
    ) -> AnalysisResult:
        """
        Analyze a chart image asynchronously.
        
        Args:
            chart_image_base64: Base64 encoded chart image (primary timeframe)
            symbol: Trading symbol (e.g., 'EURUSD')
            timeframe: Chart timeframe (e.g., 'H1')
            strategy_context: ICT strategy documentation for context
            market_data: Optional current market data
            analysis_data: Optional pre-computed analysis (FVGs, OBs, etc.)
            use_cache: Whether to use cached results
            additional_charts: Optional list of additional chart images with
                keys 'base64' (image data) and 'timeframe' (e.g., 'M5', 'M1')
            
        Returns:
            AnalysisResult with trade signal and analysis
        """
        if not self.async_client:
            logger.error("Claude async client not initialized - missing API key")
            return self._create_no_trade_result("Claude client not available")
        
        start_time = time.time()
        
        # Generate image hash for caching
        image_hash = hashlib.md5(chart_image_base64.encode()).hexdigest()
        
        # Check cache
        if use_cache:
            cached = await self._cache.get(symbol, timeframe, image_hash, market_data)
            if cached:
                return cached
        
        # Check rate limit
        await self._check_rate_limit()
        
        # Build the analysis prompt (outside retry loop)
        prompt = self._build_analysis_prompt(
            symbol, timeframe, strategy_context, market_data, analysis_data
        )
        
        # Exponential backoff retry for transient errors
        max_attempts = 3
        base_delay = 5  # seconds
        
        for attempt in range(1, max_attempts + 1):
            try:
                # Build content blocks: primary chart + optional LTF charts + prompt
                content_blocks = [
                    {
                        "type": "text",
                        "text": f"**Chart 1: {symbol} {timeframe} (Execution Timeframe)**"
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": chart_image_base64
                        }
                    }
                ]
                
                # Add M5 and M1 charts if provided (for swing counting and precision entries)
                if additional_charts:
                    for i, chart_info in enumerate(additional_charts):
                        tf_label = chart_info.get('timeframe', f'LTF-{i+1}')
                        content_blocks.append({
                            "type": "text",
                            "text": f"**Chart {i+2}: {symbol} {tf_label} (Lower Timeframe -- USE FOR SWING COUNTING & PRECISE ENTRIES)**"
                        })
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": chart_info['base64']
                            }
                        })
                
                # Add the analysis prompt last
                content_blocks.append({
                    "type": "text",
                    "text": prompt
                })
                
                # Create message with images and tool use (Opus 5 for best analysis quality).
                # Strategy docs go in system message with cache_control for Anthropic prompt caching.
                # Opus 5: no temperature (400 error), adaptive thinking + explicit effort, and
                # tool_choice=auto (forced tool use is incompatible with thinking). The system
                # message instructs the model to always finish by calling submit_trade_analysis.
                # Large max_tokens requires streaming via _async_messages_create.
                message = await self._async_messages_create(
                    model=self.model_heavy,
                    max_tokens=self.max_tokens,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort_heavy},
                    system=self._build_system_messages(strategy_context),
                    tools=[TRADE_SIGNAL_TOOL],
                    tool_choice={"type": "auto"},
                    messages=[
                        {
                            "role": "user",
                            "content": content_blocks
                        }
                    ]
                )
                
                self._record_usage("analysis", message)

                stop_reason = getattr(message, "stop_reason", None)
                if stop_reason == "max_tokens":
                    logger.warning(
                        f"[ANALYSIS] {symbol} hit max_tokens={self.max_tokens} "
                        f"(stop_reason=max_tokens). Thinking likely consumed the "
                        f"budget before submit_trade_analysis — raise max_tokens "
                        f"or lower effort_heavy if this persists."
                    )
                
                # Parse response
                result = self._parse_tool_response(message)
                result.analysis_time = time.time() - start_time
                
                # Cache result
                if use_cache:
                    await self._cache.set(symbol, timeframe, image_hash, result, market_data)
                
                return result
                
            except anthropic.RateLimitError as e:
                delay = base_delay * (2 ** (attempt - 1))  # 5s, 10s, 20s
                logger.warning(f"Rate limit hit (attempt {attempt}/{max_attempts}): {e} - waiting {delay}s")
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                    continue
                return self._create_no_trade_result(f"Rate limit exceeded after {max_attempts} attempts")
                
            except anthropic.APIConnectionError as e:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(f"Connection error (attempt {attempt}/{max_attempts}): {e} - waiting {delay}s")
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                    continue
                return self._create_no_trade_result(f"Connection error after {max_attempts} attempts: {str(e)}")
                
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    # Server error - retry
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"Server error {e.status_code} (attempt {attempt}/{max_attempts}) - waiting {delay}s")
                    if attempt < max_attempts:
                        await asyncio.sleep(delay)
                        continue
                # Client error (4xx) or final attempt - don't retry
                logger.error(f"API status error {e.status_code}: {e}")
                return self._create_no_trade_result(f"API error: {str(e)}")
                
            except Exception as e:
                logger.error(f"Error analyzing chart (attempt {attempt}/{max_attempts}): {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(base_delay)
                    continue
                return self._create_no_trade_result(f"Analysis error: {str(e)}")
        
        return self._create_no_trade_result("All retry attempts exhausted")
    
    async def analyze_multiple_symbols(
        self,
        analyses: List[Dict[str, Any]],
        max_concurrent: int = 3
    ) -> Dict[str, AnalysisResult]:
        """
        Analyze multiple symbols concurrently.
        
        Args:
            analyses: List of dicts with keys:
                - chart_image_base64
                - symbol
                - timeframe
                - strategy_context
                - market_data (optional)
                - analysis_data (optional)
            max_concurrent: Maximum concurrent API calls
            
        Returns:
            Dict mapping symbol to AnalysisResult
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def analyze_with_semaphore(params: Dict[str, Any]) -> Tuple[str, AnalysisResult]:
            async with semaphore:
                result = await self.analyze_chart_async(
                    chart_image_base64=params['chart_image_base64'],
                    symbol=params['symbol'],
                    timeframe=params['timeframe'],
                    strategy_context=params['strategy_context'],
                    market_data=params.get('market_data'),
                    analysis_data=params.get('analysis_data')
                )
                return params['symbol'], result
        
        # Run all analyses concurrently (with semaphore limiting)
        tasks = [analyze_with_semaphore(params) for params in analyses]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Compile results
        output = {}
        for item in results:
            if isinstance(item, Exception):
                logger.error(f"Analysis failed: {item}")
            else:
                symbol, result = item
                output[symbol] = result
        
        return output
    
    # NOTE: the synchronous analyze_chart() wrapper was removed — it had no callers
    # and silently duplicated the Opus 5 request shape (every prompt/param change
    # had to be made twice). Use analyze_chart_async().
    
    def _build_system_messages(self, strategy_context: str) -> list:
        """Build system messages with prompt caching for strategy docs.

        Order matters for Opus 5: put the short conciseness reminder AFTER the
        long cached blocks so it sits near the end of the system stack.
        """
        return [
            {
                "type": "text",
                "text": "You are an expert ICT (Inner Circle Trading) forex analyst. "
                        "Analyze charts using the ICT methodology described below. "
                        "OUTPUT REQUIREMENT — ALWAYS: You MUST finish every analysis by calling the "
                        "submit_trade_analysis tool exactly once. Do NOT reply with plain text instead "
                        "of the tool call. If there is no valid setup, still call the tool with "
                        "direction='no_trade' and your reasoning. "
                        "CRITICAL ZONE RULE — NEVER VIOLATE: "
                        "buy_limit ONLY in discount zone (below equilibrium). "
                        "sell_limit ONLY in premium zone (above equilibrium). "
                        "Buying expensive (premium) or selling cheap (discount) is retail behavior. "
                        "For breakouts, use buy_stop/sell_stop instead. "
                        "CRITICAL STRUCTURE RULES — NEVER VIOLATE: "
                        "1) NEVER go long when D1 AND H4 are both bearish. NEVER go short when D1 AND H4 are both bullish. "
                        "Higher timeframes set the direction — lower timeframes only refine the entry. "
                        "2) During Distribution phase, the move is done. Avoid new entries unless displacement confirms a fresh cycle. "
                        "3) M15 direction rules are detailed in the DIRECTIONAL AUTHORITY section of the analysis prompt.",
            },
            {
                # Static ICT methodology — cached so it is not re-billed every call.
                "type": "text",
                "text": ANALYSIS_RULES,
                "cache_control": {"type": "ephemeral"}
            },
            {
                "type": "text",
                "text": strategy_context,
                "cache_control": {"type": "ephemeral"}
            },
            {
                # Last: Opus 5 verbosity control (effort does not shorten visible text).
                "type": "text",
                "text": ANALYSIS_TONE_PREFERENCE,
            },
        ]
    
    def _build_analysis_prompt(
        self,
        symbol: str,
        timeframe: str,
        strategy_context: str,
        market_data: Optional[Dict[str, Any]],
        analysis_data: Optional[Dict[str, Any]]
    ) -> str:
        """Build the analysis prompt for Claude (symbol-specific, no strategy docs)."""
        prompt = f"""Analyze the attached {symbol} {timeframe} chart using the ICT strategy context provided in the system message.

## Current Market Information
- Symbol: {symbol}
- Timeframe: {timeframe}
"""
        
        if market_data:
            prompt += f"""- Current Price: {market_data.get('current_price', 'N/A')}
- Session: {market_data.get('session', 'N/A')}
- Daily Range: {market_data.get('daily_high', 'N/A')} - {market_data.get('daily_low', 'N/A')}
"""
            # Cycle-to-cycle memory: show Claude what it said last time
            last_signal = market_data.get('last_signal')
            if last_signal:
                last_dir = last_signal.get('direction', 'unknown').upper()
                last_conf = last_signal.get('confidence', 0)
                last_reason = last_signal.get('reasoning', '')
                last_ts = last_signal.get('timestamp', '')
                prompt += f"""
## YOUR LAST SIGNAL FOR THIS SYMBOL
- Direction: {last_dir}
- Confidence: {last_conf:.0%}
- Time: {last_ts}
- Reasoning: {last_reason}

**DIRECTION FLIP RULE**: If you are now recommending the OPPOSITE direction from your
last signal above, you MUST cite a SPECIFIC confirmed change on the chart that justifies
the flip: a new BOS/CHoCH, a completed liquidity sweep, a displacement candle, or a
structure break that was NOT present during your last analysis. If nothing has materially
changed on the chart, either maintain your previous direction or return no_trade.
Flipping direction without citing a confirmed change is NOT allowed.
"""
            prompt += """
**ENTRY STRATEGY REMINDER**: Pick the order type using the single ORDER TYPE SELECTION
section below (limit = INTO a level, stop = THROUGH a level, market = displacement already
confirmed per the CORE MANDATE gate). Always set entry_price to the EXACT level you want.
"""
            # Enhanced context from integrated services
            if market_data.get('account_equity'):
                prompt += f"""
## Account State
- Equity: ${market_data.get('account_equity', 0):,.2f}
- Scaling Tier: {market_data.get('scaling_tier', 'Unknown')}
- Trading Mode: {market_data.get('trading_mode', 'normal')}
- Goal Progress: {market_data.get('goal_progress', 0):.1f}% toward $100K
"""
            
            if market_data.get('recent_performance'):
                perf = market_data['recent_performance']
                prompt += f"""
## Recent Performance (Last 20 Trades)
- Win Rate: {perf.get('win_rate', 50):.0f}%
- Average R: {perf.get('avg_r', 0):.2f}
- Current Streak: {perf.get('current_streak', 'None')}
"""
            
            if market_data.get('session_performance'):
                sess = market_data['session_performance']
                prompt += f"""
## Session Performance ({market_data.get('session', 'Unknown')})
- Session Win Rate: {sess.get('win_rate', 50):.0f}%
- Session Avg R: {sess.get('avg_r', 0):.2f}
- Trades in Session: {sess.get('total_trades', 0)}
"""
            
            if market_data.get('news_status'):
                news = market_data['news_status']
                if news.get('is_blackout'):
                    prompt += f"""
## ⚠️ NEWS BLACKOUT ACTIVE
- Reason: {news.get('reason', 'High-impact event upcoming')}
- Recommendation: AVOID new positions or use minimum size
"""
                elif news.get('next_event'):
                    prompt += f"""
## Upcoming News Event
- Event: {news['next_event'].get('title', 'Unknown')}
- Time Until: {news['next_event'].get('minutes_until', 0)} minutes
- Impact: {news['next_event'].get('impact', 'Unknown')}
"""
            
            if market_data.get('correlation_exposure'):
                corr = market_data['correlation_exposure']
                if corr.get('warning'):
                    prompt += f"""
## ⚠️ CORRELATION WARNING
- {corr.get('warning')}
- Recommendation: Reduce position size or skip trade
"""
            
            # Precious metals context for XAUUSD and XAGUSD
            if market_data.get('precious_metals_context'):
                prompt += market_data['precious_metals_context']
            elif symbol in ['XAUUSD', 'XAGUSD']:
                # Provide basic precious metals info if full context not available
                prompt += f"""
## Precious Metals Trading Notes
- {"Gold" if symbol == "XAUUSD" else "Silver"} is a safe-haven asset
- Correlation with USD is typically negative
- High volatility during geopolitical events
- {"Gold is less volatile, preferred in risk-off" if symbol == "XAUUSD" else "Silver has higher beta, more volatile than gold"}
"""
            
            # ATR volatility context for SL placement
            if market_data.get('atr_14'):
                prompt += f"""
## Volatility (ATR)
- M15 ATR(14): {market_data['atr_14']}
- Minimum SL Distance (1.5x ATR): {market_data.get('atr_min_sl', 'N/A')}
- **Your SL must be at least {market_data.get('atr_min_sl', 'N/A')} from entry. SLs tighter than 1.5x ATR will be automatically widened. If widening pushes R:R below 1.5, the trade will be blocked.**
"""
            
            # Market regime context
            if market_data.get('regime'):
                regime = market_data['regime']
                prompt += f"""
## Market Regime
- Regime: {regime.get('regime', 'unknown')}
- Trend Strength (ADX): {regime.get('adx', 'N/A')}
- Volatility Ratio: {regime.get('volatility_ratio', 'N/A')}x avg
- Phase: {regime.get('phase', 'unknown')}
- **Strategy Guidance**: {regime.get('guidance', 'Use standard approach')}
"""
            
            # Volume profile levels
            if market_data.get('volume_profile_levels'):
                vpl = market_data['volume_profile_levels']
                prompt += f"""
## Volume Profile Levels
- POC (Point of Control): {vpl.get('poc', 'N/A')}
- VAH (Value Area High): {vpl.get('vah', 'N/A')}
- VAL (Value Area Low): {vpl.get('val', 'N/A')}
- Current price vs POC: {"Above POC" if market_data.get('current_price', 0) > (vpl.get('poc', 0) or 0) else "Below POC"}
"""
            
            # Volume analysis context
            if market_data.get('volume_profile'):
                vp = market_data['volume_profile']
                rel_vol = vp.get('relative_volume', 0)
                prompt += f"""
## Volume Analysis
- Relative Volume: {rel_vol:.1f}x average (20-bar)
- Volume Trend: {vp.get('volume_trend', 'unknown')}
- Recent Volume Spikes: {vp.get('spike_count', 0)}
- Climax Detected: {"YES" if vp.get('climax_detected') else "NO"}
"""
                if rel_vol < 0.3:
                    prompt += """- **EXTREMELY LOW VOLUME (<0.3x avg) - return no_trade per the Volume Confirmation Rules (no institutional participation)**
"""
                elif rel_vol < 0.7:
                    prompt += """- **LOW / BELOW-AVERAGE VOLUME (0.3x-0.7x avg) - setup may still be valid; add a warning noting thin participation**
"""
                elif rel_vol > 2.0:
                    prompt += """- **HIGH VOLUME (>2.0x avg) with displacement = strong institutional commitment; counts as an extra confirmation feeding your base confidence**
"""
            
            # Learning context from past trade reviews
            if market_data.get('learning_context'):
                prompt += f"""
{market_data['learning_context']}
"""
            
            # MFE/MAE excursion data for SL/TP optimization
            if market_data.get('excursion_data'):
                exc = market_data['excursion_data']
                prompt += f"""
## Historical SL/TP Guidance (MFE/MAE from {exc.get('sample_size', 0)} trades)
- Optimal SL (90th pctl MAE of winners): {exc.get('optimal_sl', 'N/A')}
- Optimal TP (median MFE of winners): {exc.get('optimal_tp', 'N/A')}
- Average winner runs: {exc.get('median_mfe', 'N/A')} (median MFE)
- Average winner dips: {exc.get('median_mae', 'N/A')} before reaching TP
- **Calibrate your SL/TP against these historical distributions. SL tighter than optimal_sl risks noise stop-outs. TP beyond p90_mfe is statistically unlikely.**
"""
            
            # Setup playbook from historical performance
            if market_data.get('setup_playbook'):
                prompt += f"""
{market_data['setup_playbook']}
**Use the playbook above: favor PREFERRED setups; for AVOID setups, treat it as negative confluence (fewer confirmations toward your base scale) or return no_trade — do not apply an ad-hoc confidence percentage.**
"""
            
            # Bar Extreme Supply/Demand Zones (multi-timeframe)
            _be_tfs = [k for k in market_data if k.startswith('bar_extreme_')]
            if _be_tfs:
                prompt += "\n## Bar Extreme Supply/Demand Zones\n"
                for _be_key in sorted(_be_tfs):
                    _be = market_data[_be_key]
                    _tf_label = _be.get('timeframe', _be_key.replace('bar_extreme_', '').upper())
                    sz = _be.get('supply_zone')
                    dz = _be.get('demand_zone')
                    prompt += f"\n### {_tf_label}\n"
                    if sz:
                        prompt += f"- Supply Zone: {sz['top']} – {sz['bottom']} (range of highest bar)\n"
                    if dz:
                        prompt += f"- Demand Zone: {dz['bottom']} – {dz['top']} (range of lowest bar)\n"
                    prompt += f"- Bias: **{_be.get('bias', 'neutral').upper()}** ({_be.get('bias_reason', '')})\n"
                prompt += """
**BAR EXTREME ZONE RULES:**
- If price hits a demand zone first and respects it (bounce), look for LONGS. Enter on the retest, SL below the demand zone, TP at the supply zone.
- If price hits a supply zone first and rejects, look for SHORTS. Enter on the retest, SL above the supply zone, TP at the demand zone.
- Use the execution timeframe (M15/M5) zone for precise entry. Use higher TF (H1/D1) zones for directional bias and TP targets.
- Bar extreme zones are additional confluence — stack with FVG, OB, and liquidity for high-confidence entries.
"""

            # =============================================
            # FULL MULTI-TIMEFRAME CONTEXT (D1 → H4 → H1 → M15 → M5 → M1)
            # =============================================
            
            if market_data.get('htf_bias'):
                htf_bias = market_data.get('htf_bias', 'unknown').upper()
                long_status = market_data.get('htf_can_trade_long', 'no_data')
                short_status = market_data.get('htf_can_trade_short', 'no_data')
                
                def _format_direction_status(status, direction):
                    if status == 'preferred':
                        return f"✅ PREFERRED (with-trend)"
                    elif status == 'counter_trend':
                        return f"⚠️ COUNTER-TREND (requires M15 confirmation + Tier 1 swing validation)"
                    else:
                        return f"⚠️ Insufficient data"
                
                prompt += f"""
## Multi-Timeframe Analysis (Top-Down: D1 → H4 → H1 → M15 → M5 → M1)

### Daily (D1) — Directional Narrative
- D1 Bias: {str(market_data.get('d1_bias', 'N/A')).upper()}
- D1 Structure: {market_data.get('d1_structure', 'N/A')}
- D1 Trend: {market_data.get('d1_trend', 'N/A')}

### 4-Hour (H4) — Intermediate Trend
- H4 Bias: {str(market_data.get('h4_bias', 'N/A')).upper()}
- H4 Structure: {market_data.get('h4_structure', 'N/A')}
- H4 Trend: {market_data.get('h4_trend', 'N/A')}

### 1-Hour (H1) — Swing Context
- H1 Bias: {str(market_data.get('h1_bias', 'N/A')).upper()}
- H1 Structure: {market_data.get('h1_structure', 'N/A')}
- H1 Trend: {market_data.get('h1_trend', 'N/A')}

### Overall HTF Summary
- Combined HTF Bias: {htf_bias}
- Timeframe Alignment: {"✅ YES — D1/H4/H1 agree" if market_data.get('htf_alignment') else "❌ NO — CONFLICTING TIMEFRAMES across D1/H4/H1"}
- Trade Long: {_format_direction_status(long_status, 'long')}
- Trade Short: {_format_direction_status(short_status, 'short')}
- Key HTF Levels: {market_data.get('htf_key_levels', [])}

🚨 DIRECTIONAL AUTHORITY (strict hierarchy — follow in order):
1. M15 IS YOUR EXECUTION GATE. If M15 structure is bearish, you may ONLY go short
   (or no_trade). If M15 structure is bullish, you may ONLY go long (or no_trade).
   Exception: manipulation phase (Judas swing) where M15 temporarily opposes the move.
2. D1+H4 SET THE PREFERRED DIRECTION. Trade with D1/H4 when M15 confirms.
   If M15 opposes D1/H4, it means the pullback is NOT done — WAIT for M15 to confirm,
   OR place a PENDING LIMIT ORDER (buy_limit / sell_limit) at a key level (OB, FVG,
   liquidity sweep zone) in the D1/H4 direction. This anticipates the pullback completing.
3. If D1 AND H4 BOTH oppose your direction, do NOT take the trade regardless of M15.
4. Counter-trend trades (against D1) are valid ONLY when M15 structure has already
   shifted to confirm the reversal (CHoCH or BOS in your direction). Never anticipate
   a reversal before M15 confirms it.
5. If D1/H4/H1 are NOT aligned, note this as a warning factor (see the CONFIDENCE ADJUSTMENT RULES in the system message).
"""
            
            # =============================================
            # EXECUTION & ENTRY TIMEFRAMES (M15 → M5 → M1)
            # =============================================
            
            has_ltf = any(market_data.get(k) for k in ['m15_bias', 'm5_bias', 'm1_bias'])
            if has_ltf:
                prompt += f"""
### 15-Minute (M15) — Execution Timeframe
- M15 Bias: {str(market_data.get('m15_bias', 'N/A')).upper()}
- M15 Structure: {market_data.get('m15_structure', 'N/A')}
- M15 Trend: {market_data.get('m15_trend', 'N/A')}

### 5-Minute (M5) — Entry Precision
- M5 Bias: {str(market_data.get('m5_bias', 'N/A')).upper()}
- M5 Structure: {market_data.get('m5_structure', 'N/A')}
- M5 Trend: {market_data.get('m5_trend', 'N/A')}

### 1-Minute (M1) — Sniper Entry
- M1 Bias: {str(market_data.get('m1_bias', 'N/A')).upper()}
- M1 Structure: {market_data.get('m1_structure', 'N/A')}
- M1 Trend: {market_data.get('m1_trend', 'N/A')}

See DIRECTIONAL AUTHORITY above for M15 execution rules. Use M5/M1 for precision entries.

## Trade Type Classification (REQUIRED — you MUST set trade_type for every signal)

### SCALP (trade_type = "scalp")
- Setup identified on M5/M1 charts
- Target: 5-20 pips (metals: 50-200 pips due to pip value), hold time <30 min
- Valid when: M5/M1 shows a clean ICT setup (OB, FVG, sweep, displacement) even if
  D1/H4 are RANGING or UNCLEAR. Scalps do NOT require full HTF alignment.
  NOTE: If D1 AND H4 are both actively OPPOSING your scalp direction (both bearish
  for a long scalp), the HTF gate still applies (see DIRECTIONAL AUTHORITY rule 3).
  Ranging/unclear HTFs are fine for scalps; actively opposing HTFs are not.
- Requirements: Must have at least 2 confluences on M5/M1 (e.g., OB + FVG, sweep + displacement)
- SL: Tight — just beyond the M1 structure level (typically 5-15 pips)
- R:R: Target 1.5:1 (lower threshold than intraday since high win-rate setups)
- Confidence: Can be 60%+ with clean M5/M1 structure, no need for 75%+

### INTRADAY (trade_type = "intraday")
- Setup identified on M15/H1 charts, entry refined on M5/M1
- Target: 20-80 pips (metals: 200-800 pips), hold time <8 hours
- Valid when: H1 structure supports the trade direction, M15 shows entry setup
- Requirements: H1 bias + M15 setup + M5/M1 entry confirmation
- SL: Beyond the M15 structure level
- R:R: Target 2:1

### SWING (trade_type = "swing")
- Setup identified on H4/D1 charts, entry on M15/H1
- Target: 80+ pips (metals: 800+ pips), hold time 1-5 days
- Valid when: D1 and H4 agree on direction with clear structure
- Requirements: Full D1/H4/H1 alignment + key level confluence
- SL: Beyond the H1/H4 structure level
- R:R: Target 3:1

⚠️ **R:R GUIDANCE**: Aim for at least the target R:R for each trade type (1.5:1 scalp, 2:1 intraday, 3:1 swing).
If your R:R is below the target, you need exceptional confluence and high win probability to justify it
(e.g., confirmed liquidity sweep into a strong OB with displacement on multiple timeframes).
If you cannot find a structural TP target that provides reasonable reward, either:
1. TIGHTEN YOUR SL — use M5/M1 structure instead of M15 to reduce risk distance
2. Return no_trade — the setup doesn't have enough room to run
NEVER submit a trade where TP distance < 1.5x SL distance. Trades with R:R below 1.5:1 will be automatically rejected.

⚠️ **CRYPTO R:R GUIDANCE** (aim higher due to significant dollar risk per pip):
- Crypto SCALP: Aim for 2.0:1 R:R
- Crypto INTRADAY: Aim for 2.5:1 R:R
- Crypto SWING: Aim for 3.5:1 R:R
For BTC/ETH especially: prefer TIGHT SLs using M1/M5 structure to keep risk small,
then target meaningful structural levels (IPDA, PDH/PDL, PWH/PWL) for TP.
Lower R:R is acceptable only with A+ confluence (multiple confirmed ICT factors).

⚠️ SCALP OPPORTUNITY RULE: When D1/H4 are ranging/unclear but M5/M1 shows
a textbook ICT setup (clean sweep of liquidity + displacement + FVG/OB retest),
you SHOULD signal a SCALP trade. Do NOT default to no_trade just because
the higher timeframes lack a clear trend. Scalps are valid reactive trades
on lower timeframe structure.

⚠️ COUNTER-TREND SCALP WARNING: If your SCALP direction is AGAINST the D1 bias
(e.g., D1 is BEARISH but you want to SCALP LONG on M5/M1), it must clear a HIGHER bar
than a with-trend scalp. Only take it when ALL of these are genuinely true:
1. Your HONEST confidence is already 0.70+ on the base scale (3+ real confirmations).
   Do NOT inflate the number to reach 0.70 — if the evidence only supports 0.60-0.65,
   the correct action is no_trade. (The system additionally caps counter-trend scalp
   confidence for risk management; that cap is not a reason to pad your input.)
2. At least 2.0:1 R:R — no marginal R:R allowed against the trend.
3. 3+ confluences on M5/M1 (not just 2).
Counter-trend scalps are valid ONLY when M15 has already shifted to confirm the reversal.

You MUST use M5/M1 to:
1. COUNT SWINGS into the POI (4-6 swing rule -- mandatory for reversals)
2. IDENTIFY ROUNDING / CIRCULAR PRICE ACTION (dome/saucer patterns on M5/M1)
3. SPOT THE SWEEP of liquidity that triggers entry
4. FIND THE EXACT PRICE LEVEL for your pending order (OB, FVG, or sweep-and-reclaim zone on M1)
5. CONFIRM micro-structure shifts (MSS/CHoCH on M1) before entering
6. DETECT displacement and FVGs for breakout entry timing

Use M5/M1 context to refine your entry level and assess momentum exhaustion.
M5/M1 alignment is a CONFLUENCE factor, not a numeric tweak (see CONFIDENCE ADJUSTMENT RULES):
if M5/M1 structure aligns with your thesis, count it as a confirmation toward your base scale.
If M5/M1 structure is AGAINST your thesis (e.g., bearish M1 for a long), treat it as a missing
confirmation and add a warning — if it directly invalidates the entry, return no_trade.

## MANDATORY ANALYSIS WORKFLOW (follow this order)

You MUST evaluate trade opportunities in this exact sequence:

**PASS 1 — SWING CHECK (D1 + H4):**
Do D1 and H4 agree on a clear directional bias with structure (BOS/CHoCH)?
If YES → look for a SWING setup. Entry on H1/M15.

**PASS 2 — INTRADAY CHECK (H1 + M15):**
Does H1 show a clear trend/structure that M15 confirms?
If YES → look for an INTRADAY setup. Entry refined on M5/M1.

**PASS 3 — SCALP CHECK (M5 + M1) — DO NOT SKIP THIS:**
Even if Pass 1 and Pass 2 found nothing (HTF ranging, no clear trend, misaligned),
you MUST still examine the M5 and M1 charts for independent ICT setups:
- Is there a liquidity sweep on M5/M1?
- Is there displacement + FVG on M5/M1?
- Is there an order block being tested on M1?
- Is there a CHoCH/BOS on M5 with clean structure?
If M5/M1 shows 2+ confluences → signal a SCALP trade with trade_type="scalp".

**PASS 4 — NO TRADE (only after all 3 passes fail):**
Only return no_trade if NONE of the above passes found a valid setup.

⚠️ You MUST NOT skip Pass 3. If you are about to return no_trade, first
re-examine the M5/M1 data and charts one more time for scalp opportunities.
The most common mistake is ignoring clean M5/M1 setups because D1/H4 are unclear.

## ORDER TYPE SELECTION (single source of truth for order_type)

Do NOT default to buy_limit/sell_limit for every trade. Match the order type to the setup:

- **buy_limit / sell_limit** (INTO a level): price is APPROACHING a key level (OB, FVG,
  liquidity pool, prominent wick) and you expect a reaction. Entry is AWAY from current price.
  ZONE RULE (mandatory): buy_limit ONLY in discount, sell_limit ONLY in premium.
- **buy_stop / sell_stop** (THROUGH a level): price is consolidating NEAR a breakout level and
  you expect a break through it. buy_stop = buy ABOVE current price; sell_stop = sell BELOW.
  Ideal for consolidation at a session high/low with displacement building, price coiling at an
  equal-highs/lows pool, or a pending M15/H1 CHoCH.
- **market** (displacement already confirmed): use ONLY when the CORE MANDATE market-order gate
  is satisfied — a displacement candle has CLOSED and a second confirmation (BOS/CHoCH printed,
  or completed sweep) is present AND price is AT/INSIDE your zone right now (not approaching).
  Do not wait for a pullback that may never come, but do not use market on prediction either.

If you find yourself always choosing buy_limit or sell_limit, you are likely missing breakout
setups. Decide: is the best entry INTO a level (limit), THROUGH a level (stop), or a confirmed
displacement you must take NOW (market)?
"""
            
            # =============================================
            # SWING EXHAUSTION VALIDATION
            # =============================================
            
            prompt += """
## Swing Exhaustion Validation -- TWO-TIER ENTRY SYSTEM

### TIER 1: REVERSAL ENTRIES -- HARD GATE (buy_limit / sell_limit into a POI)
When entering AGAINST the current move (buying into demand, selling into supply),
ALL of the following conditions are MANDATORY. If ANY fail, return no_trade:

1. **Count the swings (REQUIRED)**: On M5/M1, count the structural swings leading
   into the POI. You MUST observe at least 4-6 swings.
   - Swing count < 4 = return no_trade. The move is still impulsive. NO EXCEPTIONS.

2. **Rounding / circular price action (REQUIRED)**: After 4+ swings, there MUST be
   visible consolidation: a dome/saucer pattern, shrinking candle bodies, or a rounded
   shape forming. This circular price action confirms momentum is exhausting.
   - No rounding after 4+ swings = return no_trade. The move is not done yet.

3. **Require a sweep (REQUIRED)**: Price MUST sweep a nearby high/low (liquidity pool)
   before entering the reversal. No sweep = the sweep will target YOUR stop.
   - No sweep yet = place a PENDING ORDER at the anticipated sweep level, NOT a market order.

4. **Prominent wick check**: Prominent wicks (visible at any zoom level) within the
   previous range act as liquidity magnets. Place pending orders at prominent wick
   levels for sweep-and-reclaim entries.

5. **First hour caution**: First hour of NY open is manipulation. WAIT for the second
   swing to confirm direction. Return no_trade or use a pending order.

REVERSAL HARD RULES:
- Swing count < 4 = NO TRADE. Period. Do not override.
- No rounding/circular price action = NO TRADE. The move is not exhausted.
- No sweep = PENDING ORDER at anticipated sweep level, NOT a market order.
- All three confirmed (4+ swings + rounding + sweep) = HIGH PROBABILITY reversal. Set confidence >= 0.80.

### TIER 2: BREAKOUT / DISTRIBUTION ENTRIES -- STRONG CONFLUENCE
When entering WITH confirmed momentum (Silver Bullet displacement, AMD distribution,
Unicorn/Breaker setups, buy_stop/sell_stop breakouts):

- If the breakout follows 4+ swings of accumulation + a sweep = HIGHEST confidence (0.85+).
- If displacement is confirmed but swing count is low, trade can proceed but cap confidence at 0.75.
- Use buy_stop/sell_stop at breakout level if price hasn't broken yet; use market if displacement is already underway.
- Rounding pattern before the breakout = an extra confirmation feeding your base confidence scale.
"""
            
            # Fibonacci / OTE Context
            if market_data.get('fibonacci_zone'):
                prompt += f"""
## Fibonacci / OTE Analysis
- Price Zone: {market_data.get('fibonacci_zone', 'unknown').upper()}
- In OTE (61.8%-79%): {"✅ YES - OPTIMAL ENTRY ZONE" if market_data.get('in_ote') else "❌ NO"}
- Optimal Entry Price: {market_data.get('optimal_entry', 'N/A')}
"""
                fib_levels = market_data.get('fib_levels', {})
                if fib_levels and fib_levels.get('levels'):
                    prompt += f"""- Key Fib Levels: 50%={fib_levels['levels'].get('50%', 'N/A')}, 61.8%={fib_levels['levels'].get('61.8%', 'N/A')}, 70.5%={fib_levels['levels'].get('70.5%', 'N/A')}, 79%={fib_levels['levels'].get('79%', 'N/A')}
"""
                prompt += """- ⚠️ RULE: Prefer entries within OTE zone for higher probability setups
"""
            
            # =============================================
            # MECHANICAL ICT BASELINE (rule-based advisory)
            # =============================================
            
            if market_data.get('mechanical_ict_setup'):
                mech = market_data['mechanical_ict_setup']
                mech_zone = mech.get('entry_zone') or {}
                prompt += f"""
## Mechanical ICT Baseline (rule-based scan — advisory only)
A deterministic ICT ruleset (H4 trend + liquidity sweep + FVG/OB entry zone) independently found this setup:
- Direction: {str(mech.get('direction', 'N/A')).upper()}
- Confidence: {mech.get('confidence', 0):.0%} | R:R: {mech.get('risk_reward', 0):.2f}
- Entry zone: {mech_zone.get('low', 'N/A')} - {mech_zone.get('high', 'N/A')} (optimal: {mech_zone.get('optimal', 'N/A')})
- SL: {mech.get('stop_loss', 'N/A')} | TP1: {mech.get('take_profit_1', 'N/A')}
- Basis: {mech.get('entry_reason', 'N/A')} | HTF structure: {mech.get('market_structure', 'N/A')}

Treat this as one additional confluence input, NOT an instruction:
- If your read AGREES with the mechanical baseline, that is meaningful confluence.
- If you DISAGREE, explicitly state in your reasoning why the mechanical read is wrong
  (e.g. stale zone, sweep not confirmed, structure shifted since).
"""
            
            # =============================================
            # 100-PIP EXPANSION CONTEXT
            # =============================================
            
            # AMD Cycle Analysis
            if market_data.get('amd_cycle'):
                amd = market_data['amd_cycle']
                prompt += f"""
## AMD Cycle Status (Power of Three)
- Current Phase: {amd.get('phase', 'unknown').upper()}
- Expected Direction: {amd.get('expected_direction', 'N/A')}
- Manipulation Extreme: {amd.get('manipulation_extreme', 'N/A')}
- AMD Confidence: {amd.get('confidence', 0):.0%}
"""
            
            # Displacement Analysis
            if market_data.get('displacement'):
                disp = market_data['displacement']
                prompt += f"""
## Displacement Status
- Distribution Confirmed: {"✅ YES" if disp.get('distribution_confirmed') else "❌ NO - Wait for displacement"}
- Direction: {disp.get('distribution_direction', 'N/A')}
- Note: Market orders should ONLY be placed if displacement confirmed
"""
            
            # Premium/Discount Zone
            if market_data.get('premium_discount'):
                pd = market_data['premium_discount']
                prompt += f"""
## Premium/Discount Zone Analysis
- Current Zone: {pd.get('current_zone', 'unknown').upper()}
- Retracement: {pd.get('retracement_percent', 0):.0%}
- In OTE (Optimal Trade Entry): {"✅ YES" if pd.get('ote', {}).get('in_zone') else "❌ NO"}
- ⚠️ GUIDANCE: Prefer longs in discount zones (<50%) and shorts in premium zones (>50%).
  Counter-zone trades are allowed with strong reversal confluence (Tier 1 swing validation).
  Example: A long in premium is valid if price swept a key high with 4+ swings of exhaustion.
"""
            
            # Breaker Blocks (A+ Setup)
            if market_data.get('breaker_blocks'):
                bb = market_data['breaker_blocks']
                prompt += f"""
## ⭐ BREAKER BLOCKS DETECTED (A+ ENTRY ZONES)
- Total: {bb.get('count', 0)} breaker blocks found
- Bullish Breakers: {len(bb.get('bullish', []))}
- Bearish Breakers: {len(bb.get('bearish', []))}
- Note: Entry at breaker block = HIGH probability setup
"""
            
            # IPDA Levels (100-pip Targets)
            if market_data.get('ipda_levels'):
                ipda = market_data['ipda_levels']
                prompt += f"""
## 🎯 IPDA LEVELS (100-PIP DRAW ON LIQUIDITY TARGETS)
- Previous Day High (PDH): {ipda.get('pdh', {}).get('price') if ipda.get('pdh') else 'N/A'}
- Previous Day Low (PDL): {ipda.get('pdl', {}).get('price') if ipda.get('pdl') else 'N/A'}
- Previous Week High (PWH): {ipda.get('pwh', {}).get('price') if ipda.get('pwh') else 'N/A'}
- Previous Week Low (PWL): {ipda.get('pwl', {}).get('price') if ipda.get('pwl') else 'N/A'}
- ⚠️ USE THESE AS TAKE PROFIT TARGETS instead of nearest liquidity!
- For LONGS: Target PDH, then PWH
- For SHORTS: Target PDL, then PWL
"""
            
            # NWOG Target
            if market_data.get('nwog_target'):
                nwog = market_data['nwog_target']
                prompt += f"""
## 🎯 NWOG TARGET (New Week Opening Gap)
- CE Level: {nwog.get('ce_level', 'N/A')}
- Gap Size: {nwog.get('gap_size_pips', 0):.0f} pips
- Use as additional confluence for take profit
"""
            
            # Silver Bullet Setup
            if market_data.get('silver_bullet_setup'):
                sb = market_data['silver_bullet_setup']
                prompt += f"""
## 🔫 SILVER BULLET STATUS
- Window Active: {"✅ YES - HIGH PROBABILITY WINDOW" if sb.get('window_active') else "❌ NO"}
- Window: {sb.get('window', 'N/A')}
- Displacement Confirmed: {"✅ YES - TRADE NOW" if sb.get('displacement_confirmed') else "⏳ Waiting..."}
- Time Remaining: {sb.get('time_remaining', 0):.0f} minutes
"""
            
            # DXY Correlation (Critical for FX pairs)
            if market_data.get('dxy_correlation'):
                dxy = market_data['dxy_correlation']
                prompt += f"""
## 💵 DXY CORRELATION ANALYSIS
- DXY Trend: {dxy.get('dxy_trend', 'unknown').upper()}
- Confirmed Direction for {symbol}: {dxy.get('confirmed_direction', 'N/A').upper() if dxy.get('confirmed_direction') else 'N/A'}
- ⚠️ If your signal CONFLICTS with DXY direction, treat it as a missing confirmation (weaker confluence toward your base scale) and add a warning — do not apply an ad-hoc confidence percentage (see CONFIDENCE ADJUSTMENT RULES).
- DXY Bullish = Short EUR/GBP, Long USD pairs
- DXY Bearish = Long EUR/GBP, Short USD pairs
"""
            
            # Firecrawl Intelligence Summary
            if market_data.get('firecrawl_intelligence'):
                prompt += f"""
## 📊 REAL-TIME MARKET INTELLIGENCE (Firecrawl)
{market_data['firecrawl_intelligence']}
"""
            
            # Rate Expectations (CRITICAL for currency bias)
            if market_data.get('rate_expectations'):
                rates = market_data['rate_expectations']
                fed = rates.get('fed', {})
                prompt += f"""
## 💰 RATE EXPECTATIONS
- Fed Next Move: {fed.get('next_move', 'unknown').upper()}
- USD Impact: {fed.get('usd_impact', 'unknown').upper()}
- ⚠️ Rate hike expected = BULLISH USD, Rate cut expected = BEARISH USD
"""
            
            # Retail Sentiment (CONTRARIAN INDICATOR)
            if market_data.get('retail_sentiment'):
                rs = market_data['retail_sentiment']
                prompt += f"""
## 👥 RETAIL SENTIMENT (Trade AGAINST the crowd)
- Retail Bias: {rs.get('bias', 'unknown').upper()}
- Contrarian Signal: {rs.get('contrarian_signal', 'neutral').upper()}
- If retail is extremely long -> LOOK FOR SHORT opportunities
- If retail is extremely short -> LOOK FOR LONG opportunities
"""
            
            # VIX Risk Mode
            if market_data.get('vix_sentiment'):
                vix = market_data['vix_sentiment']
                prompt += f"""
## 📉 VIX RISK SENTIMENT
- VIX Level: {vix.get('level', 'N/A')}
- Risk Mode: {vix.get('risk_mode', 'neutral').upper()}
- ⚠️ RISK-OFF (VIX > 20): Favor safe havens (JPY, CHF, Gold), avoid high-beta
- ⚠️ RISK-ON (VIX < 15): Favor risk currencies (AUD, NZD), equities
"""
            
            # TradingView Technical
            if market_data.get('tradingview_technical'):
                tv = market_data['tradingview_technical']
                prompt += f"""
## 📊 TRADINGVIEW TECHNICAL CONSENSUS
- Signal: {tv.get('signal', 'neutral').upper()}
- Consensus: {tv.get('consensus', 'neutral').upper()}
- ⚠️ If your analysis CONFLICTS with TV consensus, note this as a warning factor
"""
            
            # Options Flow
            if market_data.get('options_flow'):
                opts = market_data['options_flow']
                prompt += f"""
## 🎯 OPTIONS FLOW
- Flow Bias: {opts.get('flow', 'neutral').upper()}
- Magnet Levels: {', '.join(str(l) for l in opts.get('magnet_levels', []))}
- ⚠️ Price tends to be attracted to magnet levels (large option expiries)
- ⚠️ Options flow bullish = Institutional buying calls/selling puts
"""
            
            # Economic Calendar
            if market_data.get('economic_calendar'):
                events = market_data['economic_calendar']
                high_impact = [e for e in events if e.get('impact') == 'high']
                if high_impact:
                    prompt += f"""
## 📅 HIGH IMPACT EVENTS TODAY
- Count: {len(high_impact)} high impact events
- ⚠️ AVOID trading 30 minutes before high impact releases
- ⚠️ Consider reducing position size on event days
"""
            
            # Intermarket Analysis
            if market_data.get('intermarket'):
                im = market_data['intermarket']
                prompt += f"""
## 🌐 INTERMARKET ANALYSIS
- Risk Environment: {im.get('risk_environment', 'unknown').upper()}
- SPX Trend: {im.get('spx_trend', 'unknown').upper()}
- ⚠️ Trade WITH the risk environment (risk-on = long risk currencies)
"""
            
            # Seasonal Pattern
            if market_data.get('seasonal_pattern'):
                sp = market_data['seasonal_pattern']
                prompt += f"""
## 📆 SEASONAL PATTERN
- {sp.get('current_month', 'N/A')} Bias: {sp.get('current_month_bias', 'unknown').upper()}
- Historical Accuracy: {sp.get('historical_accuracy', 0)}%
- ⚠️ Treat seasonal alignment as minor supporting confluence only; if it conflicts, note it in `warnings`. Do not apply an ad-hoc confidence percentage (see CONFIDENCE ADJUSTMENT RULES).
"""
        
        if analysis_data:
            prompt += f"""
## Pre-computed Analysis
- D1 Bias: {analysis_data.get('d1_bias', 'N/A')}
- H1 Bias: {analysis_data.get('h1_bias', 'N/A')}
- Market Structure (M15): {analysis_data.get('market_structure', {})}
- Fair Value Gaps: {analysis_data.get('fvg', {})}
- Order Blocks: {analysis_data.get('order_blocks', [])}
- Liquidity Levels: {analysis_data.get('liquidity', {})}
- Volume Metrics: {analysis_data.get('volume', {})}
"""
            if analysis_data.get('h1_premium_discount'):
                prompt += f"- H1 Premium/Discount: {analysis_data['h1_premium_discount']}\n"
        
        # The static methodology (Analysis Required, CORE MANDATE, confidence scale,
        # CONFIDENCE ADJUSTMENT RULES, Volume Confirmation Rules, dangerous-pair
        # warnings, precious-metals notes and the worked example) lives in the
        # prompt-cached ANALYSIS_RULES system block — see _build_system_messages().
        # Only the pointer below is repeated per call so the model knows where the
        # authoritative rules are.
        prompt += """
## Apply the ruleset
Now produce your recommendation by STRICTLY following the ICT ANALYSIS RULESET in the
system message: the CORE MANDATE (confirmed setups first), the confidence scale and the
CONFIDENCE ADJUSTMENT RULES (the single source of truth for the confidence number), the
Volume Confirmation Rules, the dangerous-pair warnings, and the worked example format.
Finish by calling the submit_trade_analysis tool exactly once.
"""
        
        return prompt
    
    def _validate_trade_signal(self, tool_input: dict) -> dict:
        """
        Validate and sanitize Claude's trade signal response.
        Ensures all fields have correct types and are within valid ranges.
        """
        if not isinstance(tool_input, dict):
            logger.warning(f"tool_input is not a dict (got {type(tool_input)}), returning no_trade")
            return {'direction': 'no_trade', 'confidence': 0, 'reasoning': 'Invalid response format'}

        # Validate direction
        valid_directions = {'long', 'short', 'no_trade'}
        direction = str(tool_input.get('direction', 'no_trade')).lower()
        if direction not in valid_directions:
            logger.warning(f"Invalid direction '{direction}', defaulting to no_trade")
            direction = 'no_trade'
        tool_input['direction'] = direction

        # Validate confidence (clamp to 0-1)
        try:
            confidence = float(tool_input.get('confidence', 0))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0
        tool_input['confidence'] = confidence

        # Validate numeric price fields
        for field in ['entry_price', 'stop_loss', 'take_profit', 'risk_reward']:
            val = tool_input.get(field)
            if val is not None:
                try:
                    val = float(val)
                    if field in ('entry_price', 'stop_loss', 'take_profit') and val <= 0:
                        logger.warning(f"Non-positive {field}: {val}, setting to None")
                        val = None
                    elif field == 'risk_reward' and val < 0:
                        logger.warning(f"Negative {field}: {val}, setting to None")
                        val = None
                except (TypeError, ValueError):
                    val = None
            tool_input[field] = val

        entry = tool_input.get('entry_price')
        sl = tool_input.get('stop_loss')
        tp = tool_input.get('take_profit')
        # Policy: long/short always require entry + SL + TP. Incomplete directional
        # signals become no_trade so the normalizer never sees "Missing SL or TP".
        if direction in ('long', 'short') and not (entry and sl and tp):
            logger.warning(
                f"Rejecting {direction}: missing entry/SL/TP "
                f"(entry={entry}, sl={sl}, tp={tp}) — prices are always required"
            )
            tool_input['direction'] = 'no_trade'
            tool_input['confidence'] = 0.0
            tool_input['reasoning'] = (
                "Signal rejected: long/short requires entry_price, stop_loss, "
                "and take_profit"
            )
            return tool_input

        if direction in ('long', 'short') and entry and sl and tp:
            coherent, reason = validate_signal_coherence(entry, sl, tp, direction)
            if not coherent:
                logger.warning(f"Signal coherence rejected before auto-repair: {reason}")
                tool_input['direction'] = 'no_trade'
                tool_input['confidence'] = 0.0
                tool_input['reasoning'] = f"Signal rejected: {reason}"
                return tool_input

        return self._apply_sl_tp_repairs(tool_input)

    def _validate_tool_input(self, tool_input: dict) -> dict:
        """Alias for readiness tests and external callers."""
        return self._validate_trade_signal(tool_input)

    def _apply_sl_tp_repairs(self, tool_input: dict) -> dict:
        direction = tool_input.get('direction', 'no_trade')
        if direction == 'no_trade':
            return tool_input
        # Claude sometimes swaps SL and TP values, or returns
        # pip distances instead of absolute price levels.
        # ============================================
        entry = tool_input.get('entry_price')
        sl = tool_input.get('stop_loss')
        tp = tool_input.get('take_profit')
        
        if direction in ('long', 'short') and entry and sl and tp and entry > 0:
            # Check if SL or TP look like pip distances rather than price levels
            # (e.g., TP=0.09 when price is ~36.0 is clearly not an absolute price)
            for field_name, field_val in [('stop_loss', sl), ('take_profit', tp)]:
                if field_val > 0 and entry > 0:
                    ratio = field_val / entry
                    if ratio < 0.01:  # Value is less than 1% of entry — likely a pip distance, not a price
                        logger.warning(
                            f"SL/TP SANITY: {field_name}={field_val:.6f} looks like a pip distance, "
                            f"not a price level (entry={entry:.6f}, ratio={ratio:.4f}). "
                            f"Converting: entry {'+'  if (direction == 'long') == (field_name == 'take_profit') else '-'} {field_val}"
                        )
                        if field_name == 'stop_loss':
                            sl = entry - field_val if direction == 'long' else entry + field_val
                            tool_input['stop_loss'] = sl
                        else:
                            tp = entry + field_val if direction == 'long' else entry - field_val
                            tool_input['take_profit'] = tp
            
            # Re-read sl/tp after potential pip-distance conversion
            sl = tool_input.get('stop_loss')
            tp = tool_input.get('take_profit')
            
            # ── SL == ENTRY FIX ──────────────────────────────────────
            # Claude sometimes outputs SL at the exact same price as entry.
            # This must be handled BEFORE the swap logic, otherwise the swap
            # logic misidentifies it as "SL on wrong side" and swaps SL↔TP,
            # which makes BOTH wrong.
            sl_at_entry = abs(sl - entry) < entry * 0.0001  # Within 0.01%
            if sl_at_entry:
                logger.warning(
                    f"SL=ENTRY DETECTED for {direction}: SL={sl} == Entry={entry}. "
                    f"Leaving SL as-is for main.py A5 auto-fix to correct using key levels."
                )
                # Do NOT swap — main.py's A5 block will derive a proper SL
                # from key levels (support_1 / resistance_1) or a % fallback.
            else:
                # ── DIRECTION COHERENCE CHECK ──────────────────────────
                # Before swap logic: if SL and TP are BOTH oriented for
                # the opposite direction, the direction label is wrong.
                # Flip the direction instead of swapping SL/TP.
                levels_say_long = (sl < entry and tp > entry)
                levels_say_short = (sl > entry and tp < entry)
                
                if direction == 'short' and levels_say_long:
                    logger.warning(
                        f"DIRECTION FLIP: Levels say LONG (SL={sl} < Entry={entry} < TP={tp}) "
                        f"but direction was SHORT. Flipping to LONG."
                    )
                    direction = 'long'
                    tool_input['direction'] = 'long'
                elif direction == 'long' and levels_say_short:
                    logger.warning(
                        f"DIRECTION FLIP: Levels say SHORT (TP={tp} < Entry={entry} < SL={sl}) "
                        f"but direction was LONG. Flipping to SHORT."
                    )
                    direction = 'short'
                    tool_input['direction'] = 'short'
                
                # Detect swapped SL/TP: for longs SL should be below entry and TP above;
                # for shorts SL should be above entry and TP below.
                sl_wrong_side = (direction == 'long' and sl >= entry) or (direction == 'short' and sl <= entry)
                tp_wrong_side = (direction == 'long' and tp <= entry) or (direction == 'short' and tp >= entry)
                
                if sl_wrong_side and tp_wrong_side:
                    # Both are on the wrong side — swap them
                    logger.warning(
                        f"SL/TP SWAP DETECTED for {direction}: SL={sl}, TP={tp}, Entry={entry}. "
                        f"Both on wrong side — swapping SL<->TP"
                    )
                    tool_input['stop_loss'], tool_input['take_profit'] = tp, sl
                elif sl_wrong_side and not tp_wrong_side:
                    logger.warning(
                        f"SL WRONG SIDE for {direction}: SL={sl}, TP={tp}, Entry={entry}. "
                        f"Swapping SL<->TP"
                    )
                    tool_input['stop_loss'], tool_input['take_profit'] = tp, sl
                elif tp_wrong_side and not sl_wrong_side:
                    logger.warning(
                        f"TP WRONG SIDE for {direction}: SL={sl}, TP={tp}, Entry={entry}. "
                        f"Swapping SL<->TP"
                    )
                    tool_input['stop_loss'], tool_input['take_profit'] = tp, sl
                
                # ── POST-SWAP VALIDATION ──────────────────────────────
                # Re-check after swap: if the result is WORSE, reject the signal.
                new_sl = tool_input.get('stop_loss')
                new_tp = tool_input.get('take_profit')
                new_sl_wrong = (direction == 'long' and new_sl >= entry) or (direction == 'short' and new_sl <= entry)
                new_tp_wrong = (direction == 'long' and new_tp <= entry) or (direction == 'short' and new_tp >= entry)
                if new_sl_wrong or new_tp_wrong:
                    logger.warning(
                        f"POST-SWAP STILL INVALID for {direction}: SL={new_sl}, TP={new_tp}, "
                        f"Entry={entry}. SL_wrong={new_sl_wrong}, TP_wrong={new_tp_wrong}. "
                        f"Setting direction to no_trade."
                    )
                    tool_input['direction'] = 'no_trade'
                    tool_input['confidence'] = 0.0
                    tool_input['reasoning'] = (
                        f"Signal rejected: SL/TP levels invalid after correction attempts. "
                        f"Original: SL={sl}, TP={tp}, Entry={entry}, Dir={direction}."
                    )
            
            # Final R:R check: warn if SL distance > TP distance (bad R:R)
            # Don't swap here — main.py's R:R enforcement will auto-extend TP to meet min R:R
            final_sl = tool_input.get('stop_loss')
            final_tp = tool_input.get('take_profit')
            if final_sl and final_tp:
                sl_dist = abs(entry - final_sl)
                tp_dist = abs(final_tp - entry)
                if sl_dist > 0 and tp_dist > 0 and sl_dist > tp_dist:
                    logger.warning(
                        f"SL/TP R:R INVERTED after validation: SL_dist={sl_dist:.6f} > TP_dist={tp_dist:.6f}. "
                        f"R:R enforcement in main.py will auto-extend TP to meet minimum ratio."
                    )
        
        # Validate trade_type
        valid_trade_types = {'scalp', 'intraday', 'swing'}
        trade_type = str(tool_input.get('trade_type', 'intraday')).lower()
        if trade_type not in valid_trade_types:
            trade_type = 'intraday'
        tool_input['trade_type'] = trade_type
        
        # Validate order_type
        valid_order_types = {'market', 'buy_limit', 'sell_limit', 'buy_stop', 'sell_stop'}
        order_type = str(tool_input.get('order_type', 'market')).lower()
        if order_type not in valid_order_types:
            order_type = 'market'
        tool_input['order_type'] = order_type
        
        # Validate market_structure
        valid_structures = {'bullish', 'bearish', 'ranging'}
        ms = str(tool_input.get('market_structure', 'ranging')).lower()
        if ms not in valid_structures:
            ms = 'ranging'
        tool_input['market_structure'] = ms
        
        # Validate amd_phase
        valid_phases = {'accumulation', 'manipulation', 'distribution', 'unknown'}
        phase = str(tool_input.get('amd_phase', 'unknown')).lower()
        if phase not in valid_phases:
            phase = 'unknown'
        tool_input['amd_phase'] = phase
        
        # Ensure reasoning is a string
        tool_input['reasoning'] = str(tool_input.get('reasoning', ''))
        
        # Validate manipulation_complete as boolean (string "false" is truthy in Python)
        mc = tool_input.get('manipulation_complete', False)
        if isinstance(mc, str):
            mc = mc.lower() in ('true', '1', 'yes')
        tool_input['manipulation_complete'] = bool(mc)
        
        return tool_input
    
    @staticmethod
    def _extract_text(message) -> str:
        """
        Concatenate all text blocks from a message response.

        With Opus 5 adaptive thinking, the response may lead with a thinking
        block, so content[0] is not guaranteed to be text. This skips thinking
        (and any non-text) blocks and returns only the assistant's text output.
        """
        if not message or not hasattr(message, 'content') or not message.content:
            return ""
        parts = []
        for block in message.content:
            # Skip thinking blocks; accept any block exposing string text.
            if getattr(block, 'type', None) in ('thinking', 'redacted_thinking'):
                continue
            text = getattr(block, 'text', None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    # Anthropic's Python SDK rejects non-streaming create() when max_tokens is
    # large enough that the request could exceed ~10 minutes (empirically >20k).
    _STREAM_MAX_TOKENS_THRESHOLD = 20000

    async def _async_messages_create(self, **kwargs):
        """
        Create a message, streaming when required by the SDK for large max_tokens.

        Streaming still returns the assembled final Message, so callers (usage
        telemetry, tool parsing) stay unchanged. Prefer create() below the
        threshold to keep mocks/tests simple for light/judge calls.
        """
        max_tokens = int(kwargs.get("max_tokens") or 0)
        if max_tokens > self._STREAM_MAX_TOKENS_THRESHOLD:
            async with self.async_client.messages.stream(**kwargs) as stream:
                return await stream.get_final_message()
        return await self.async_client.messages.create(**kwargs)

    def _record_usage(self, task: str, message) -> None:
        """
        Record token usage + estimated cost for a completed API call.

        Logs a summary line and fire-and-forgets a row into the api_usage table.
        Never raises: telemetry must not break trading, and mocked responses in
        tests (whose usage fields are not ints) are silently skipped.
        """
        try:
            usage = getattr(message, "usage", None)
            if usage is None:
                return
            def _tok(attr: str) -> Optional[int]:
                value = getattr(usage, attr, 0) or 0
                return value if isinstance(value, int) else None
            input_tokens = _tok("input_tokens")
            output_tokens = _tok("output_tokens")
            cache_read = _tok("cache_read_input_tokens")
            cache_creation = _tok("cache_creation_input_tokens")
            if input_tokens is None or output_tokens is None:
                return  # mocked/malformed usage — skip
            cache_read = cache_read or 0
            cache_creation = cache_creation or 0

            cost = (
                input_tokens * OPUS_5_PRICING["input"]
                + output_tokens * OPUS_5_PRICING["output"]
                + cache_read * OPUS_5_PRICING["cache_read"]
                + cache_creation * OPUS_5_PRICING["cache_write"]
            ) / 1_000_000

            model_name = getattr(message, "model", "") or self.model_heavy
            logger.info(
                f"[USAGE] {task}: in={input_tokens} out={output_tokens} "
                f"cache_read={cache_read} cache_write={cache_creation} "
                f"est=${cost:.4f} ({model_name})"
            )

            async def _persist():
                try:
                    from ..api.database import ApiUsageModel, async_session_maker
                    async with async_session_maker() as session:
                        session.add(ApiUsageModel(
                            task=task,
                            model=str(model_name),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_read_tokens=cache_read,
                            cache_creation_tokens=cache_creation,
                            estimated_cost_usd=cost,
                        ))
                        await session.commit()
                except Exception as db_err:
                    logger.debug(f"[USAGE] DB write skipped: {db_err}")

            try:
                asyncio.get_running_loop()
                asyncio.create_task(_persist())
            except RuntimeError:
                pass  # no running loop (sync context) — log line is still emitted
        except Exception as e:
            logger.debug(f"[USAGE] recording skipped: {e}")

    async def _light_json_call(
        self,
        task: str,
        prompt: str,
        schema: Optional[dict],
        max_tokens: int,
        effort: Optional[str] = None,
    ) -> Tuple[Optional[dict], str]:
        """
        Run a light-task Opus 5 call that must return a JSON object.

        Single source of truth for the light-task request shape: no temperature
        (Opus 5 rejects it), adaptive thinking + effort, structured output
        when a schema is provided (with one retry without the format constraint if
        the API rejects it), usage telemetry, and thinking-block-safe parsing.

        ``effort`` defaults to ``self.effort_light`` (low). Pass ``self.effort_review``
        for reviews/weekly insights that need more depth.

        Returns (parsed_dict_or_None, raw_response_text). Exceptions from the API
        (other than a schema-format rejection) propagate to the caller, which owns
        its own defaults.
        """
        effort_level = effort or self.effort_light
        output_config: Dict[str, Any] = {"effort": effort_level}
        if schema:
            output_config = {
                "effort": effort_level,
                "format": {"type": "json_schema", "schema": schema},
            }
        try:
            message = await self.async_client.messages.create(
                model=self.model_light,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config=output_config,
                messages=[{"role": "user", "content": prompt}]
            )
        except anthropic.BadRequestError as e:
            if not schema:
                raise
            logger.warning(f"[{task}] Structured output rejected ({e}); retrying without format")
            message = await self.async_client.messages.create(
                model=self.model_light,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": effort_level},
                messages=[{"role": "user", "content": prompt}]
            )

        self._record_usage(task, message)
        response_text = self._extract_text(message)

        # Structured output emits a bare JSON object; the fallback path may fence it.
        try:
            return json.loads(response_text.strip()), response_text
        except (json.JSONDecodeError, ValueError):
            pass
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1)), response_text
            except (json.JSONDecodeError, ValueError):
                pass
        return None, response_text

    def _parse_tool_response(self, message) -> AnalysisResult:
        """Parse the tool use response from Claude."""
        tool_input = None
        raw_text = ""
        
        # Safety check: ensure message.content exists and is iterable
        if not message or not hasattr(message, 'content') or not message.content:
            logger.warning("Empty or invalid Claude response")
            return self._empty_result("Empty response from Claude")
        
        for content in message.content:
            if content.type == "tool_use" and content.name == "submit_trade_analysis":
                tool_input = content.input
            elif content.type == "text":
                raw_text += content.text
        
        if tool_input:
            # Validate and sanitize the response
            tool_input = self._validate_trade_signal(tool_input)
            
            signal = TradeSignal(
                direction=tool_input.get('direction', 'no_trade'),
                confidence=float(tool_input.get('confidence', 0)),
                entry_price=tool_input.get('entry_price'),
                stop_loss=tool_input.get('stop_loss'),
                take_profit=tool_input.get('take_profit'),
                risk_reward=tool_input.get('risk_reward'),
                reasoning=tool_input.get('reasoning', ''),
                market_structure=tool_input.get('market_structure'),
                trade_type=tool_input.get('trade_type', 'intraday'),
                order_blocks=tool_input.get('order_blocks', []),
                fvg_zones=tool_input.get('fvg_zones', []),
                liquidity_targets=tool_input.get('liquidity_targets', []),
                order_type=tool_input.get('order_type', 'market'),
                amd_phase=tool_input.get('amd_phase', 'unknown'),
                manipulation_complete=tool_input.get('manipulation_complete', False)
            )
            
            return AnalysisResult(
                signal=signal,
                raw_response=json.dumps(tool_input, indent=2),
                analysis_summary=tool_input.get('reasoning', ''),
                key_levels=tool_input.get('key_levels', {}),
                warnings=tool_input.get('warnings', [])
            )
        
        logger.warning("Tool use response not found, falling back to text parsing")
        return self._parse_response(raw_text)
    
    def _empty_result(self, reason: str = "No signal") -> AnalysisResult:
        """Return an empty no-trade result."""
        return AnalysisResult(
            signal=TradeSignal(
                direction='no_trade',
                confidence=0.0,
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                risk_reward=None,
                reasoning=reason
            ),
            raw_response="",
            analysis_summary=reason,
            key_levels={},
            warnings=[reason]
        )
    
    def _parse_response(self, response_text: str) -> AnalysisResult:
        """Parse Claude's response into structured result."""
        import re
        
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                
                signal = TradeSignal(
                    direction=data.get('direction', 'no_trade'),
                    confidence=float(data.get('confidence', 0)),
                    entry_price=data.get('entry_price'),
                    stop_loss=data.get('stop_loss'),
                    take_profit=data.get('take_profit'),
                    risk_reward=data.get('risk_reward'),
                    reasoning=data.get('reasoning', ''),
                    market_structure=data.get('market_structure'),
                    trade_type=data.get('trade_type', 'intraday'),
                    order_blocks=data.get('order_blocks', []),
                    fvg_zones=data.get('fvg_zones', []),
                    liquidity_targets=data.get('liquidity_targets', []),
                    order_type=data.get('order_type', 'market'),
                    amd_phase=data.get('amd_phase', 'unknown'),
                    manipulation_complete=data.get('manipulation_complete', False)
                )
                
                return AnalysisResult(
                    signal=signal,
                    raw_response=response_text,
                    analysis_summary=data.get('reasoning', ''),
                    key_levels=data.get('key_levels', {}),
                    warnings=data.get('warnings', [])
                )
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON from response: {e}")
        
        return self._parse_text_response(response_text)
    
    def _parse_text_response(self, response_text: str) -> AnalysisResult:
        """Fallback parsing for non-JSON responses."""
        direction = 'no_trade'
        response_lower = response_text.lower()
        
        if 'recommend long' in response_lower or 'bullish setup' in response_lower:
            direction = 'long'
        elif 'recommend short' in response_lower or 'bearish setup' in response_lower:
            direction = 'short'
        
        signal = TradeSignal(
            direction=direction,
            confidence=0.5,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            reasoning=response_text,
            order_type='market',
            amd_phase='unknown',
            manipulation_complete=False
        )
        
        return AnalysisResult(
            signal=signal,
            raw_response=response_text,
            analysis_summary=response_text,
            key_levels={},
            warnings=["Could not parse structured response - manual review recommended"]
        )
    
    def _create_no_trade_result(self, reason: str) -> AnalysisResult:
        """Create a no-trade result with error message."""
        signal = TradeSignal(
            direction='no_trade',
            confidence=0,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            reasoning=reason
        )
        
        return AnalysisResult(
            signal=signal,
            raw_response="",
            analysis_summary=reason,
            key_levels={},
            warnings=[reason]
        )
    
    async def test_connection_async(self) -> bool:
        """Test the API connection asynchronously."""
        if not self.async_client:
            return False
        
        try:
            # Opus 5 has thinking on by default; a tiny max_tokens can truncate
            # before any visible text. Keep budget modest but safe.
            message = await self.async_client.messages.create(
                model=self.model_light,
                max_tokens=256,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": "Reply with the single word: ok"}]
            )
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Test the API connection synchronously."""
        if not self.sync_client:
            return False
        
        try:
            message = self.sync_client.messages.create(
                model=self.model_light,
                max_tokens=256,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": "Reply with the single word: ok"}]
            )
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    async def clear_cache(self):
        """Clear the analysis cache."""
        await self._cache.clear()
        logger.info("Analysis cache cleared")
    
    @property
    def cache_size(self) -> int:
        """Get current cache size."""
        return self._cache.size
    
    # =========================================================================
    # PHASE 2: Extended Claude Capabilities
    # =========================================================================
    
    async def recommend_position_size(
        self,
        equity: float,
        setup_grade: str,
        confidence: float,
        symbol: str,
        win_streak: int = 0,
        loss_streak: int = 0,
        base_lots: float = 0.01,
        max_lots: float = 0.10
    ) -> Dict[str, Any]:
        """
        Get Claude's recommendation for position size.
        
        Args:
            equity: Current account equity
            setup_grade: Setup quality (A+, A, B, C)
            confidence: Trade confidence (0-1)
            symbol: Trading symbol
            win_streak: Current winning streak
            loss_streak: Current losing streak
            base_lots: Base lot size for tier
            max_lots: Maximum lot size for tier
            
        Returns:
            Dict with recommended_lots, reasoning, risk_assessment
        """
        if not self.async_client:
            return {"recommended_lots": base_lots, "reasoning": "Claude unavailable", "risk_assessment": "unknown"}
        
        prompt = f"""You are a risk management expert. Recommend a position size for this trade.

## Account State
- Current Equity: ${equity:,.2f}
- Base Lot Size (tier default): {base_lots} lots
- Maximum Lot Size (tier limit): {max_lots} lots

## Trade Setup
- Symbol: {symbol}
- Setup Grade: {setup_grade}
- Confidence: {confidence:.0%}
- Win Streak: {win_streak}
- Loss Streak: {loss_streak}

## Guidelines
- A+ setups can go up to max lots
- B setups should use base lots or less
- C setups should use minimum (0.01 lots)
- After 3+ losses, reduce size significantly
- After 3+ wins, be cautious (don't get overconfident)

## SCOPE
Deliver only the size recommendation JSON. Keep reasoning to 1-2 short sentences.
No market re-analysis and no filler.

Respond with a single JSON object (no surrounding prose) of this shape:
{{
    "recommended_lots": <float>,
    "reasoning": "<brief explanation>",
    "risk_assessment": "low|medium|high",
    "size_adjustment": "<0.5x|0.75x|1.0x|1.25x|1.5x>"
}}"""
        
        try:
            result, response_text = await self._light_json_call(
                "position_size", prompt, POSITION_SIZE_SCHEMA, max_tokens=2000
            )
            if result is not None:
                return result
            return {"recommended_lots": base_lots, "reasoning": response_text, "risk_assessment": "medium"}
            
        except Exception as e:
            logger.error(f"Error getting position size recommendation: {e}")
            return {"recommended_lots": base_lots, "reasoning": str(e), "risk_assessment": "unknown"}
    
    async def review_closed_trade(
        self,
        trade_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Have Claude review a closed trade and extract learnings.
        
        Args:
            trade_data: Dict containing trade details:
                - symbol, direction, entry_price, exit_price
                - stop_loss, take_profit, profit_loss, pips
                - duration, setup_type, market_conditions
                
        Returns:
            Dict with analysis, learnings, grade, would_take_again
        """
        if not self.async_client:
            return {"analysis": "Claude unavailable", "learnings": [], "grade": "N/A"}
        
        prompt = f"""You are a trading coach reviewing a completed trade. Analyze what went right or wrong.

## Trade Details
- Symbol: {trade_data.get('symbol', 'Unknown')}
- Direction: {trade_data.get('direction', 'Unknown')}
- Entry: {trade_data.get('entry_price', 0)}
- Exit: {trade_data.get('exit_price', 0)}
- Stop Loss: {trade_data.get('stop_loss', 0)}
- Take Profit: {trade_data.get('take_profit', 0)}
- P/L: ${trade_data.get('profit_loss', 0):.2f} ({trade_data.get('pips', 0):.1f} pips)
- R-Multiple: {trade_data.get('r_multiple', 0):.2f}R
- Duration: {trade_data.get('duration', 'Unknown')}
- Setup Type: {trade_data.get('setup_type', 'ICT')}
- Entry Reason: {trade_data.get('entry_reason', 'N/A')}
- Original Confidence: {f"{trade_data['original_confidence']:.0%}" if trade_data.get('original_confidence') else 'N/A'}
- Analysis Timeframe: {trade_data.get('timeframe', 'N/A')}

## Review Questions
1. Was the entry timing good?
2. Was the stop loss placement appropriate?
3. Was the take profit realistic?
4. Was the original confidence level justified by the outcome?
5. Was the timeframe appropriate for this setup?
6. What could have been done better?
7. Should this type of setup be taken again?

## SCOPE
Answer only those questions in the JSON below. Keep analysis to a short paragraph
(cover the substance, no filler, no redundant summary). Match document length to need.

Respond with a single JSON object (no surrounding prose) of this shape:
{{
    "outcome": "win|loss|breakeven",
    "grade": "A|B|C|D|F",
    "analysis": "<detailed analysis>",
    "what_went_right": ["<point1>", "<point2>"],
    "what_went_wrong": ["<point1>", "<point2>"],
    "learnings": ["<learning1>", "<learning2>"],
    "would_take_again": true|false,
    "improvement_suggestions": ["<suggestion1>"]
}}"""
        
        try:
            result, response_text = await self._light_json_call(
                "trade_review", prompt, TRADE_REVIEW_SCHEMA, max_tokens=3000,
                effort=self.effort_review,
            )
            if result is not None:
                return result
            return {"analysis": response_text, "learnings": [], "grade": "N/A"}
            
        except Exception as e:
            logger.error(f"Error reviewing trade: {e}")
            return {"analysis": str(e), "learnings": [], "grade": "N/A"}
    
    async def judge_trade(
        self,
        signal: Dict[str, Any],
        risk_metrics: Dict[str, Any],
        learning_context: str,
    ) -> Dict[str, Any]:
        """
        Pre-execution trade judge: validate a trade signal against learned
        patterns and risk math before committing capital.
        
        Returns a verdict: APPROVE (proceed as-is), DEMOTE (convert to
        pending limit order at a tighter entry price), or REJECT (skip entirely).
        
        Args:
            signal: Trade signal dict (symbol, direction, confidence, entry, SL, TP, reasoning)
            risk_metrics: Risk context (R:R, position size %, balance, daily P&L, drawdown, etc.)
            learning_context: Formatted string from TradeLearningService.build_context_for_claude()
            
        Returns:
            Dict with verdict, reason, suggested_entry, risk_flags
        """
        default_fail = {
            "verdict": "UNAVAILABLE",
            "reason": "Judge unavailable",
            "suggested_entry": None,
            "risk_flags": ["judge_unavailable"],
        }

        if not self.async_client:
            logger.warning("[JUDGE] Claude client unavailable — fail-closed")
            return default_fail
        
        direction = signal.get('direction', 'unknown')
        confidence = signal.get('confidence', 0)
        symbol = signal.get('symbol', 'Unknown')
        entry_price = signal.get('entry_price', 0)
        stop_loss = signal.get('stop_loss', 0)
        take_profit = signal.get('take_profit', 0)
        order_type = signal.get('order_type', 'market')
        trade_type = signal.get('trade_type', 'intraday')
        reasoning = str(signal.get('reasoning', ''))
        
        # R:R expectations per trade type (the full table also lives in JUDGE_RUBRIC)
        _rr_expectations = {'scalp': '1.5:1', 'intraday': '2:1', 'swing': '3:1'}
        expected_rr = _rr_expectations.get(trade_type, '2:1')
        
        # Only per-trade facts here — the judging rubric itself is the (prompt-cached)
        # JUDGE_RUBRIC system block. See _build_judge_system_messages().
        prompt = f"""Apply the TRADE JUDGE rubric from the system message to the following trade.

## Proposed Trade
- Symbol: {symbol}
- Direction: {direction.upper()}
- Trade Type: {trade_type.upper()} (target R:R {expected_rr}; absolute floor 1.5:1)
- Confidence: {confidence:.0%}
- Entry Price: {entry_price}
- Stop Loss: {stop_loss}
- Take Profit: {take_profit}
- Order Type: {order_type}
- Reasoning: {reasoning}

## Risk Metrics
- Account Balance: ${risk_metrics.get('account_balance', 0):.2f}
- Daily P&L: ${risk_metrics.get('daily_pnl', 0):+.2f}
- Current Drawdown: {risk_metrics.get('drawdown_pct', 0):.1%}
- Risk-Reward Ratio: {risk_metrics.get('risk_reward', 0):.2f}:1 (SL dist: ${risk_metrics.get('sl_distance', 0):.2f}, TP dist: ${risk_metrics.get('tp_distance', 0):.2f})
- Position Size: {risk_metrics.get('position_size_pct', 0):.1%} of equity{' **[AT BROKER MINIMUM LOT SIZE — cannot go smaller]**' if risk_metrics.get('at_broker_minimum_lots') else ''}
- Trades Today: {risk_metrics.get('trades_today', 0)} / {risk_metrics.get('max_daily_trades', 5)}
- Session: {risk_metrics.get('session', 'unknown')}
- Asset Category: {risk_metrics.get('symbol_category', 'unknown')}

## Past Learning Context
{learning_context if learning_context else "No historical learnings available yet."}

Now run Step 1 and Step 2 from the rubric and respond with the single JSON object."""
        
        try:
            # Opus 5: no temperature, adaptive thinking + effort. No tools here,
            # so thinking is fully compatible. max_tokens covers thinking + JSON verdict.
            # Structured outputs (output_config.format) force schema-valid JSON so we do
            # not have to rely on regex extraction. If the API rejects the format (e.g.
            # incompatible with adaptive thinking on this model), we retry once without it
            # and fall back to the regex/plain-JSON parsing below.
            base_output_config = {
                "effort": self.effort_judge,
                "format": {"type": "json_schema", "schema": JUDGE_OUTPUT_SCHEMA},
            }
            # Static rubric goes in a cached system block (billed once per cache window).
            judge_system = [{
                "type": "text",
                "text": JUDGE_RUBRIC,
                "cache_control": {"type": "ephemeral"},
            }]
            try:
                message = await self.async_client.messages.create(
                    model=self.model_heavy,
                    max_tokens=4000,
                    thinking={"type": "adaptive"},
                    output_config=base_output_config,
                    system=judge_system,
                    messages=[{"role": "user", "content": prompt}]
                )
            except anthropic.BadRequestError as e:
                logger.warning(
                    f"[JUDGE] Structured output rejected ({e}); retrying without format constraint"
                )
                message = await self.async_client.messages.create(
                    model=self.model_heavy,
                    max_tokens=4000,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort_judge},
                    system=judge_system,
                    messages=[{"role": "user", "content": prompt}]
                )
            
            self._record_usage("judge", message)
            
            # Adaptive thinking may emit a thinking block before the text block,
            # so extract the text content explicitly rather than assuming content[0].
            response_text = self._extract_text(message)
            
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(1))
            else:
                # Try parsing entire response as JSON
                result = json.loads(response_text.strip())
            
            # Validate the response structure
            verdict = result.get('verdict', 'UNAVAILABLE').upper()
            if verdict not in ('APPROVE', 'DEMOTE', 'REJECT'):
                logger.warning(f"[JUDGE] Invalid verdict '{verdict}', failing closed")
                return default_fail
            
            result['verdict'] = verdict
            result.setdefault('reason', '')
            result.setdefault('suggested_entry', None)
            result.setdefault('risk_flags', [])
            
            # Safety: ensure suggested_entry doesn't worsen the trade
            if verdict == 'DEMOTE' and result['suggested_entry'] is not None:
                suggested = result['suggested_entry']
                if direction == 'long' and suggested > entry_price:
                    logger.warning(f"[JUDGE] Suggested long entry {suggested} > current {entry_price}, clamping")
                    result['suggested_entry'] = entry_price
                elif direction == 'short' and suggested < entry_price:
                    logger.warning(f"[JUDGE] Suggested short entry {suggested} < current {entry_price}, clamping")
                    result['suggested_entry'] = entry_price
            
            logger.info(f"[JUDGE] {symbol} {direction}: {verdict} — {result.get('reason', '')}")
            return result
            
        except json.JSONDecodeError as e:
            logger.warning(f"[JUDGE] Failed to parse response: {e} — fail-closed")
            return default_fail
        except Exception as e:
            logger.error(f"[JUDGE] Error: {e} — fail-closed")
            return default_fail
    
    async def generate_weekly_review(
        self,
        trades: List[Dict[str, Any]],
        equity_start: float,
        equity_end: float,
        session_stats: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate a weekly performance review.
        
        Args:
            trades: List of trade dicts from the week
            equity_start: Starting equity
            equity_end: Ending equity
            session_stats: Optional session performance data
            
        Returns:
            Comprehensive weekly review
        """
        if not self.async_client:
            return {"summary": "Claude unavailable", "recommendations": []}
        
        # Compile trade summary
        wins = len([t for t in trades if t.get('profit_loss', 0) > 0])
        losses = len([t for t in trades if t.get('profit_loss', 0) < 0])
        total_pnl = sum(t.get('profit_loss', 0) for t in trades)
        total_r = sum(t.get('r_multiple', 0) for t in trades)
        
        prompt = f"""You are a trading mentor conducting a weekly review.

## Weekly Performance
- Starting Equity: ${equity_start:,.2f}
- Ending Equity: ${equity_end:,.2f}
- Return: {((equity_end - equity_start) / equity_start * 100):.1f}%
- Total Trades: {len(trades)}
- Wins: {wins} | Losses: {losses}
- Win Rate: {(wins / len(trades) * 100) if trades else 0:.1f}%
- Total P/L: ${total_pnl:,.2f}
- Total R: {total_r:.1f}R
- Average R: {(total_r / len(trades)) if trades else 0:.2f}R

## Trade Breakdown
"""
        for i, trade in enumerate(trades[:25], 1):  # Limit to 25 trades
            prompt += f"{i}. {trade.get('symbol', '?')} {trade.get('direction', '?')}: {trade.get('profit_loss', 0):+.2f} ({trade.get('r_multiple', 0):.1f}R)\n"
        
        if session_stats:
            prompt += f"""
## Session Performance
- Best Session: {session_stats.get('best_session', 'N/A')}
- Worst Session: {session_stats.get('worst_session', 'N/A')}
"""
        
        prompt += """
## Review Tasks
1. Identify patterns in wins and losses
2. Note any recurring mistakes
3. Highlight what's working well
4. Provide specific recommendations for next week
5. Rate overall performance

## SCOPE
Deliver only the weekly review JSON. Cover the substance; do not pad with filler
sections, redundant summaries, or boilerplate. Summary: 2-4 sentences max.

Respond with a single JSON object (no surrounding prose) of this shape:
{
    "performance_grade": "A|B|C|D|F",
    "summary": "<paragraph summary>",
    "patterns_identified": ["<pattern1>", "<pattern2>"],
    "strengths": ["<strength1>"],
    "weaknesses": ["<weakness1>"],
    "recommendations": ["<rec1>", "<rec2>", "<rec3>"],
    "focus_for_next_week": "<specific focus area>",
    "risk_adjustment": "increase|maintain|decrease"
}"""
        
        try:
            result, response_text = await self._light_json_call(
                "weekly_review", prompt, WEEKLY_REVIEW_SCHEMA, max_tokens=4000,
                effort=self.effort_review,
            )
            if result is not None:
                return result
            return {"summary": response_text, "recommendations": []}
            
        except Exception as e:
            logger.error(f"Error generating weekly review: {e}")
            return {"summary": str(e), "recommendations": []}
    
    async def generate_weekly_insights(
        self,
        learnings_data: str
    ) -> Dict[str, Any]:
        """
        Have Claude analyze weekly learnings and generate consolidated insights.
        
        This is used by the TradeLearningService during weekly consolidation
        to identify patterns and generate actionable improvements.
        
        Args:
            learnings_data: JSON string of all trade reviews from the week
            
        Returns:
            Dict with performance_grade, patterns, insights, recommendations
        """
        if not self.async_client:
            return {
                "performance_grade": "N/A",
                "summary": "Claude unavailable",
                "patterns_identified": [],
                "recurring_mistakes": [],
                "winning_patterns": [],
                "recommendations": [],
                "symbol_insights": {},
                "session_insights": {},
                "focus_area": "",
                "best_setup": ""
            }
        
        prompt = f"""You are a trading coach analyzing a week's worth of trade data, including trade reviews AND trade judge performance.

Your task is to identify patterns, consolidate learnings, evaluate judge accuracy, and generate actionable insights that will improve future trading decisions.

## Weekly Data

{learnings_data}

## Analysis Required

Analyze ALL the data above (trade reviews AND judge analysis) and identify:

1. **Performance Grade (A-F)**: Overall quality of trading decisions this week
2. **Recurring Patterns**: Both positive and negative patterns that appear multiple times
3. **Winning Patterns**: What specifically worked well in profitable trades
4. **Recurring Mistakes**: Common errors to avoid
5. **Symbol-Specific Insights**: Any symbol-specific patterns or learnings
6. **Session-Specific Insights**: Performance patterns by trading session
7. **Judge Accuracy Assessment**: Is the judge approving the right trades and rejecting the right ones? Are there false rejections (rejected signals that would have won)? Should the judge be more or less restrictive?
8. **Confluence Analysis**: Which confluence factor combinations lead to the best outcomes?
9. **Top 3 Actionable Recommendations**: Specific improvements for next week
10. **Focus Area**: The ONE thing to focus on improving next week
11. **Best Setup**: The setup type that performed best this week

Pay special attention to:
- APPROVED trades that lost: Was the judge too lenient?
- REJECTED signals that would have won: Was the judge too strict?
- DEMOTED trades: Did the limit entry improve the outcome?
- Confluence count vs win rate: Is there a minimum confluence for profitability?

Be specific and actionable. These insights will be used to improve future trade analysis.

## SCOPE
Deliver only the insights JSON below. Cover the substance; do not pad with filler
sections, redundant summaries, or boilerplate. Summary: 2-3 sentences.

Respond with JSON:
```json
{{
    "performance_grade": "A|B|C|D|F",
    "summary": "<2-3 sentence overall assessment including judge performance>",
    "patterns_identified": ["<pattern1>", "<pattern2>", "<pattern3>"],
    "recurring_mistakes": ["<mistake1>", "<mistake2>", "<mistake3>"],
    "winning_patterns": ["<pattern1>", "<pattern2>", "<pattern3>"],
    "judge_assessment": "<assessment of judge accuracy — too strict, too lenient, or well-calibrated>",
    "judge_recommendations": ["<specific judge tuning rec1>", "<rec2>"],
    "confluence_insights": "<what confluence counts/factors correlate with wins>",
    "recommendations": ["<specific actionable rec1>", "<rec2>", "<rec3>"],
    "symbol_insights": {{
        "EURUSD": "<insight if applicable>",
        "GBPUSD": "<insight if applicable>"
    }},
    "session_insights": {{
        "london": "<insight if applicable>",
        "new_york": "<insight if applicable>",
        "asian": "<insight if applicable>"
    }},
    "focus_area": "<ONE specific thing to focus on next week>",
    "best_setup": "<the setup type that performed best>"
}}
```"""
        
        try:
            # No schema here: symbol_insights/session_insights are free-form dicts,
            # which strict structured-output grammar cannot express. The helper still
            # centralizes the request shape and parses fenced or bare JSON.
            result, response_text = await self._light_json_call(
                "weekly_insights", prompt, None, max_tokens=5000,
                effort=self.effort_review,
            )
            if result is not None:
                logger.info(f"Generated weekly insights: Grade {result.get('performance_grade', 'N/A')}")
                return result
            
            # Fallback if JSON not found
            return {
                "performance_grade": "C",
                "summary": response_text,
                "patterns_identified": [],
                "recurring_mistakes": [],
                "winning_patterns": [],
                "recommendations": [],
                "symbol_insights": {},
                "session_insights": {},
                "focus_area": "Review trade entries",
                "best_setup": "Unknown"
            }
            
        except Exception as e:
            logger.error(f"Error generating weekly insights: {e}")
            return {
                "performance_grade": "N/A",
                "summary": str(e),
                "patterns_identified": [],
                "recurring_mistakes": [],
                "winning_patterns": [],
                "recommendations": [],
                "symbol_insights": {},
                "session_insights": {},
                "focus_area": "",
                "best_setup": ""
            }
    
    async def assess_scaling_decision(
        self,
        current_equity: float,
        current_tier: str,
        recent_performance: Dict[str, Any],
        goal_progress: float
    ) -> Dict[str, Any]:
        """
        Get Claude's assessment on scaling/risk adjustments.
        
        Args:
            current_equity: Current account equity
            current_tier: Current scaling tier name
            recent_performance: Recent win rate, avg R, etc.
            goal_progress: Progress toward $100K goal (0-100)
            
        Returns:
            Scaling recommendation
        """
        if not self.async_client:
            return {"mode": "normal", "reasoning": "Claude unavailable"}
        
        prompt = f"""You are a risk management advisor assessing whether to adjust trading approach.

## Current State
- Equity: ${current_equity:,.2f}
- Scaling Tier: {current_tier}
- Goal Progress: {goal_progress:.1f}% toward $100K

## Recent Performance (Last 20 trades)
- Win Rate: {recent_performance.get('win_rate', 50):.0f}%
- Average R: {recent_performance.get('avg_r', 1.0):.2f}
- Max Drawdown: {recent_performance.get('max_drawdown', 5):.1f}%
- Streak: {recent_performance.get('current_streak', 'None')}

## Decision Options
1. AGGRESSIVE - Increase position sizes, take more setups
2. NORMAL - Standard position sizes, standard selectivity
3. CONSERVATIVE - Reduce sizes, be more selective
4. DEFENSIVE - Minimum sizes, only A+ setups

Consider:
- Are we on track for the goal?
- Is recent performance justifying current risk?
- Any concerning patterns?

## SCOPE
Deliver only the scaling JSON. Keep reasoning to 1-2 short sentences. No filler.

Respond with a single JSON object (no surrounding prose) of this shape:
{{
    "recommended_mode": "aggressive|normal|conservative|defensive",
    "reasoning": "<explanation>",
    "risk_multiplier": <0.5 to 1.5>,
    "setup_filter": "all|A_and_B|A_only",
    "confidence_threshold": <0.6 to 0.9>,
    "warnings": ["<warning if any>"]
}}"""
        
        try:
            result, response_text = await self._light_json_call(
                "scaling_decision", prompt, SCALING_DECISION_SCHEMA, max_tokens=2500
            )
            if result is not None:
                return result
            return {"mode": "normal", "reasoning": response_text}
            
        except Exception as e:
            logger.error(f"Error assessing scaling: {e}")
            return {"mode": "normal", "reasoning": str(e)}