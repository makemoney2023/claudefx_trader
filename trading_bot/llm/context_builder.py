"""
Context Builder for LLM Prompts.

Loads and formats strategy documentation to provide
context to Claude for chart analysis.
"""

from pathlib import Path
from typing import Optional, Dict, List
import os

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Docs Claude may fetch on demand (replay tool-lookup / slim index).
STRATEGY_DOC_ALLOWLIST: tuple = (
    "ict_strategy",
    "market_structure",
    "fair_value_gap",
    "order_blocks",
    "liquidity_concepts",
    "optimal_trade_entry",
    "kill_zones",
    "swing_validation",
    "precious_metals",
    "risk_management",
    "amd_cycle",
    "volume_concepts",
)

# Never ship these into analysis prompts (product/plan docs, not methodology).
STRATEGY_DOC_BLOCKLIST: tuple = (
    "website_documentation",
    "phase2_100k_plan",
)

_DOC_SUMMARIES: Dict[str, str] = {
    "ict_strategy": "Core ICT methodology overview",
    "market_structure": "BOS, CHoCH, MSS and swing structure",
    "fair_value_gap": "FVG formation and trading",
    "order_blocks": "Order blocks, breakers, mitigation",
    "liquidity_concepts": "BSL/SSL, EQH/EQL, sweeps",
    "optimal_trade_entry": "OTE, Fibonacci, premium/discount",
    "kill_zones": "Session times and kill-zone strategies",
    "swing_validation": "Swing count, wicks, sweep-and-reclaim",
    "precious_metals": "Gold/silver trading notes",
    "risk_management": "Position sizing and drawdown rules",
    "amd_cycle": "Power of 3 / Judas swing (AMD)",
    "volume_concepts": "Volume confirmation and institutional footprint",
}


class ContextBuilder:
    """
    Builds context strings from strategy documentation files.
    
    Loads .md files from the docs directory and combines them
    into a comprehensive context for LLM prompts.
    """
    
    def __init__(self, docs_dir: Optional[str] = None):
        """
        Initialize the context builder.
        
        Args:
            docs_dir: Path to strategy documentation directory
        """
        self.docs_dir = Path(docs_dir or settings.docs_dir)
        self._cache: Dict[str, str] = {}
        self._load_documents()
    
    def _load_documents(self):
        """Load all documentation files into cache."""
        if not self.docs_dir.exists():
            logger.warning(f"Docs directory not found: {self.docs_dir}")
            return
        
        for doc_file in self.docs_dir.glob("*.md"):
            # Skip macOS AppleDouble junk (._*.md) which is not valid UTF-8 text.
            if doc_file.name.startswith("._"):
                continue
            try:
                content = doc_file.read_text(encoding='utf-8')
                self._cache[doc_file.stem] = content
                logger.debug(f"Loaded document: {doc_file.name}")
            except Exception as e:
                logger.error(f"Error loading {doc_file}: {e}")
        
        logger.info(f"Loaded {len(self._cache)} strategy documents")
    
    def get_document(self, name: str) -> Optional[str]:
        """
        Get a specific document by name.
        
        Args:
            name: Document name (without .md extension)
            
        Returns:
            Document content or None
        """
        return self._cache.get(name)

    def get_strategy_doc_index(self) -> str:
        """Short allowlisted catalog for slim/replay system prompts (not full markdown)."""
        lines = ["## Available strategy documents",
                 "Call lookup_strategy_doc to read a document (max 2 lookups per analysis)."]
        for name in STRATEGY_DOC_ALLOWLIST:
            if name not in self._cache or name in STRATEGY_DOC_BLOCKLIST:
                continue
            summary = _DOC_SUMMARIES.get(name, name.replace("_", " "))
            lines.append(f"- {name}: {summary}")
        return "\n".join(lines)

    def lookup_strategy_doc(
        self,
        doc_name: Optional[str] = None,
        query: Optional[str] = None,
        max_chars: int = 12000,
    ) -> Dict[str, object]:
        """
        Return one allowlisted strategy doc by name or query.

        Blocklisted names are always rejected. Content is truncated to max_chars.
        """
        resolved: Optional[str] = None
        if doc_name:
            key = str(doc_name).strip().lower().replace(".md", "").replace(" ", "_")
            if key in STRATEGY_DOC_BLOCKLIST:
                return {"error": f"Document '{key}' is not available for lookup"}
            if key in STRATEGY_DOC_ALLOWLIST and key in self._cache:
                resolved = key
            else:
                return {"error": f"Unknown or disallowed document '{doc_name}'"}
        elif query:
            q = str(query).strip().lower()
            if not q:
                return {"error": "Empty query"}
            # Prefer exact stem match, then substring on allowlisted names only.
            for name in STRATEGY_DOC_ALLOWLIST:
                if name in STRATEGY_DOC_BLOCKLIST or name not in self._cache:
                    continue
                if name == q or q in name or q in _DOC_SUMMARIES.get(name, "").lower():
                    resolved = name
                    break
            if resolved is None:
                return {"error": f"No allowlisted document matched query '{query}'"}
        else:
            return {"error": "Provide doc_name or query"}

        content = self._cache[resolved]
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        return {"doc_name": resolved, "content": content, "truncated": truncated}
    
    def get_ict_context(self) -> str:
        """
        Get the complete ICT strategy context.
        
        Combines all relevant ICT documentation into a single
        context string for the LLM.
        """
        sections = []
        
        # Priority order for documents (all strategy docs, ordered by importance)
        priority_docs = [
            'ict_strategy',        # Core ICT methodology overview
            'time_and_price',      # Time meets price theory
            'amd_cycle',           # Power of 3 / Judas swing
            'power_of_three',      # AMD cycle detailed
            'market_structure',    # BOS, CHoCH, MSS
            'fair_value_gap',      # FVG formation and trading
            'order_blocks',        # OB, breaker blocks, mitigation
            'liquidity_concepts',  # BSL/SSL, EQH/EQL, sweeps
            'optimal_trade_entry', # OTE zone, Fibonacci, premium/discount
            'volume_concepts',     # Volume confirmation & institutional footprint
            'kill_zones',          # Session times and kill zone strategies
            'silver_bullet',       # Silver Bullet model
            'market_maker',        # Market maker concepts and tactics
            'market_maker_model',  # MMXM detailed model
            'execution_models',    # Unicorn, 2022, turtle soup entry techniques
            'risk_management',     # Position sizing, drawdown management
            'precious_metals',     # Gold/Silver trading guide
            'swing_validation',    # 4-6 swing rule, prominent wicks, sweep-and-reclaim
            'trading_learnings',   # Auto-generated learning insights
        ]
        
        for doc_name in priority_docs:
            if doc_name in STRATEGY_DOC_BLOCKLIST:
                continue
            if doc_name in self._cache:
                sections.append(f"### {doc_name.replace('_', ' ').title()}\n\n{self._cache[doc_name]}")
        
        # Add any remaining documents except blocklisted product/plan docs
        for doc_name, content in self._cache.items():
            if doc_name in priority_docs or doc_name in STRATEGY_DOC_BLOCKLIST:
                continue
            sections.append(f"### {doc_name.replace('_', ' ').title()}\n\n{content}")
        
        return "\n\n---\n\n".join(sections)
    
    def get_trading_rules_context(self) -> str:
        """Get trading rules and risk management context."""
        rules_docs = ['risk_management', 'market_maker']
        
        sections = []
        for doc_name in rules_docs:
            if doc_name in self._cache:
                sections.append(self._cache[doc_name])
        
        return "\n\n".join(sections)
    
    def get_precious_metals_context(self) -> str:
        """
        Get precious metals specific context for gold/silver trading.
        
        Returns the precious_metals documentation if available.
        """
        if 'precious_metals' in self._cache:
            return self._cache['precious_metals']
        return ""
    
    def get_quick_reference(self) -> str:
        """
        Get a condensed quick reference for the LLM.
        
        This is a shorter version for when full context isn't needed.
        """
        return """## ICT Quick Reference

### Time and Price Theory
- **Key Principle**: Best trades occur when price reaches a key level during a key time
- **NY Midnight (00:00 EST)**: Daily equilibrium - above = bullish bias, below = bearish bias
- **Macro Times**: XX:50-XX:10 windows for algorithmic entries

### AMD Cycle (Power of 3)
- **Accumulation**: Asian session (7PM-2AM EST) - range forms, mark high/low
- **Manipulation (Judas Swing)**: London/NY open - FALSE move opposite to true direction
- **Distribution**: True directional move after manipulation completes
- **KEY**: Wait for manipulation, then trade the distribution

### Market Structure
- **BOS (Break of Structure)**: Continuation - price breaks swing in trend direction
- **CHoCH (Change of Character)**: Reversal - price breaks swing against trend
- **MSS (Market Structure Shift)**: Strong reversal with displacement

### Fair Value Gaps (FVG)
- **Bullish FVG**: Candle 1 high < Candle 3 low (gap = support)
- **Bearish FVG**: Candle 1 low > Candle 3 high (gap = resistance)
- **Entry**: 50% of FVG is optimal entry point

### Order Blocks
- **Bullish OB**: Last bearish candle before strong up-move (demand)
- **Bearish OB**: Last bullish candle before strong down-move (supply)
- **Breaker Block**: Failed OB that now acts as reversal zone

### Liquidity
- **BSL (Buy-Side Liquidity)**: Stops above swing highs
- **SSL (Sell-Side Liquidity)**: Stops below swing lows
- **EQH/EQL**: Equal highs/lows = strong liquidity targets
- **IRL vs ERL**: Internal (inside range) vs External (range extremes) liquidity

### Kill Zones (EST)
- **Asian**: 7:00 PM - 12:00 AM (accumulation - don't trade)
- **London**: 2:00 AM - 5:00 AM (manipulation then real move)
- **New York**: 7:00 AM - 10:00 AM (highest volume, continuation)
- **London Close**: 10:00 AM - 12:00 PM (reversals common)

### Silver Bullet Windows (EST)
- **London**: 3:00 AM - 4:00 AM
- **NY AM**: 10:00 AM - 11:00 AM
- **NY PM**: 2:00 PM - 3:00 PM
- **Entry**: FVG formed from displacement during window

### Market Maker Model (MMXM)
1. Mark consolidation range (external liquidity at range high/low)
2. Wait for manipulation to sweep one side
3. Identify Smart Money Reversal (MSS + OB/FVG)
4. Enter at SMR zone, stop beyond manipulation extreme
5. Target opposite external liquidity

### Unicorn Model (Highest Probability)
- Breaker Block + FVG overlap = Unicorn zone
- Enter when price retraces to overlap zone
- Double confluence = highest probability setup

### Entry Hierarchy (Best to Good)
1. Unicorn Model (OB + FVG overlap)
2. Silver Bullet (Time + FVG)
3. 2022 Model (Liquidity sweep + OB/FVG)
4. Standard OB/FVG entry

### Entry Criteria Checklist
1. ✓ HTF bias determined (Daily/4H trend)
2. ✓ In kill zone (London or NY session)
3. ✓ AMD phase identified (wait for manipulation complete)
4. ✓ Liquidity swept (stops taken)
5. ✓ MSS confirmed (structure shift)
6. ✓ Entry at OB/FVG/Unicorn zone
7. ✓ Stop beyond manipulation extreme
8. ✓ Minimum 1.5:1 risk-reward (1.5:1 scalp, 2:1 intraday, 3:1 swing)
9. ✓ No major news within 30 minutes

### Swing Exhaustion Validation -- TWO-TIER ENTRY SYSTEM

**TIER 1 -- REVERSAL ENTRIES (buy_limit/sell_limit into POI) -- HARD GATE:**
- **4-6 Swing Rule (MANDATORY)**: Count swings on M5/M1 into the POI. Swing count < 4 = NO TRADE. Period.
- **Rounding / Circular Price Action (MANDATORY)**: After 4+ swings, MUST see dome/saucer pattern, shrinking candles, or consolidation. No rounding = NO TRADE.
- **The Sweep (MANDATORY)**: Price MUST sweep a high/low before reversal. No sweep = place PENDING ORDER at anticipated sweep level.
- **Prominent Wick**: Visually obvious wicks within previous range = liquidity magnets. Place pending orders at prominent wick levels.
- **First Hour**: First hour of NY open is manipulation. Wait for second swing.
- All three confirmed (4+ swings + rounding + sweep) = HIGH PROBABILITY. Set confidence >= 0.80.

**TIER 2 -- BREAKOUT / DISTRIBUTION ENTRIES -- STRONG CONFLUENCE:**
- Silver Bullet displacement, AMD distribution, Unicorn/Breaker setups
- 4+ swings of accumulation before breakout = HIGHEST confidence (0.85+)
- Displacement confirmed but low swing count = cap confidence at 0.75
- Still prefer pending orders (buy_stop/sell_stop) over market orders

**Trailing via 21 EMA**: Once in profit, trail stops behind swing lows/highs around the 21 EMA.
"""
    
    def build_analysis_context(
        self,
        include_full_docs: bool = False,
        specific_docs: Optional[List[str]] = None
    ) -> str:
        """
        Build context for chart analysis.
        
        Args:
            include_full_docs: Include full documentation
            specific_docs: List of specific docs to include
            
        Returns:
            Context string for LLM prompt
        """
        if specific_docs:
            sections = []
            for doc_name in specific_docs:
                if doc_name in self._cache:
                    sections.append(self._cache[doc_name])
            return "\n\n".join(sections)
        
        if include_full_docs:
            return self.get_ict_context()
        
        return self.get_quick_reference()
    
    def reload_documents(self):
        """Reload all documents from disk."""
        self._cache.clear()
        self._load_documents()
    
    @property
    def available_documents(self) -> List[str]:
        """Get list of available document names."""
        return list(self._cache.keys())
