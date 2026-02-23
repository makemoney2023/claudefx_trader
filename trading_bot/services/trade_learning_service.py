"""
Trade Learning Service.

Manages the learning system that stores Claude's trade reviews,
consolidates insights weekly, and provides context for future analysis.

Features:
- Store trade reviews (losses and big wins >2R)
- Retrieve learnings by symbol/session
- Build dynamic context for Claude prompts
- Weekly consolidation with Claude-generated insights
- Update permanent documentation
- Telegram notifications
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.database import (
    async_session_maker,
    TradeLearningModel,
    KnowledgeBaseModel,
    WeeklyReviewModel,
    AnalysisLogModel,
    TradeModel,
)
from ..utils.logging import get_logger

if TYPE_CHECKING:
    from ..llm.claude_client import ClaudeClient

logger = get_logger(__name__)

# Knowledge retention period
KNOWLEDGE_RETENTION_DAYS = 90

# Confidence thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.7
MIN_SAMPLE_SIZE_FOR_DOCS = 10


class TradeLearningService:
    """
    Service for managing Claude's trade learning system.
    
    Handles storage, retrieval, and consolidation of trade learnings
    to continuously improve trading performance.
    """
    
    @staticmethod
    def _sanitize_r(val: float) -> float:
        """Cap unreasonable R-multiples at +/-10R."""
        if val is None:
            return 0.0
        return val if abs(val) <= 10 else 0.0
    
    def __init__(self):
        """Initialize the trade learning service."""
        self._docs_path = Path(__file__).parent.parent / "docs" / "trading_learnings.md"
        logger.info("Trade learning service initialized")
    
    # =========================================================================
    # STORAGE METHODS
    # =========================================================================
    
    async def store_trade_review(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        profit_loss: float,
        r_multiple: float,
        review: Dict[str, Any],
        session: str = "",
        setup_type: str = "ICT",
        entry_reason: Optional[str] = None,
        original_confidence: Optional[float] = None,
        timeframe: Optional[str] = None,
        # Judge & confluence context (for correlating decisions to outcomes)
        judge_verdict: Optional[str] = None,
        judge_reason: Optional[str] = None,
        confluence_factors: Optional[List[str]] = None,
        confluence_count: Optional[int] = None,
    ) -> Optional[TradeLearningModel]:
        """
        Store Claude's review of a trade with full judge/analysis context.
        
        Args:
            trade_id: The trade's unique identifier
            symbol: Trading symbol
            direction: Trade direction (long/short)
            profit_loss: Profit/loss amount
            r_multiple: R-multiple achieved
            review: Claude's review dict containing grade, analysis, learnings, etc.
            session: Trading session (asian, london, new_york)
            setup_type: Type of setup (FVG, OB, etc.)
            entry_reason: Original entry reasoning from Claude at trade open
            original_confidence: Claude's original confidence score at trade open
            timeframe: Timeframe used for the trade
            judge_verdict: APPROVE or DEMOTE
            judge_reason: Judge's reasoning for the verdict
            confluence_factors: List of confluence factor names
            confluence_count: Number of confluence factors
            
        Returns:
            Created TradeLearningModel or None on failure
        """
        try:
            async with async_session_maker() as db_session:
                learning = TradeLearningModel(
                    trade_id=trade_id,
                    symbol=symbol,
                    direction=direction,
                    session=session,
                    setup_type=setup_type,
                    entry_reason=entry_reason,
                    original_confidence=original_confidence,
                    timeframe=timeframe,
                    profit_loss=profit_loss,
                    r_multiple=r_multiple,
                    outcome=review.get('outcome', 'loss' if profit_loss < 0 else 'win'),
                    grade=review.get('grade', 'C'),
                    analysis=review.get('analysis', ''),
                    what_went_right=review.get('what_went_right', []),
                    what_went_wrong=review.get('what_went_wrong', []),
                    learnings=review.get('learnings', []),
                    improvement_suggestions=review.get('improvement_suggestions', []),
                    would_take_again=review.get('would_take_again', True),
                    judge_verdict=judge_verdict,
                    judge_reason=judge_reason,
                    confluence_factors=confluence_factors,
                    confluence_count=confluence_count,
                )
                
                db_session.add(learning)
                await db_session.commit()
                await db_session.refresh(learning)
                
                logger.info(f"Stored trade learning for {trade_id} ({symbol}): Grade {learning.grade}, judge={judge_verdict}, confluence={confluence_count}")
                return learning
                
        except Exception as e:
            logger.error(f"Failed to store trade review: {e}")
            return None
    
    # =========================================================================
    # RETRIEVAL METHODS
    # =========================================================================
    
    async def get_learnings_for_symbol(
        self,
        symbol: str,
        limit: int = 10,
        days_back: int = KNOWLEDGE_RETENTION_DAYS
    ) -> List[Dict[str, Any]]:
        """
        Get recent learnings for a specific symbol.
        
        Args:
            symbol: Trading symbol to filter by
            limit: Maximum number of learnings to return
            days_back: How far back to look (default 90 days)
            
        Returns:
            List of learning dictionaries
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(TradeLearningModel)
                    .where(
                        and_(
                            TradeLearningModel.symbol == symbol,
                            TradeLearningModel.timestamp >= cutoff
                        )
                    )
                    .order_by(desc(TradeLearningModel.timestamp))
                    .limit(limit)
                )
                
                learnings = result.scalars().all()
                
                return [
                    {
                        'trade_id': l.trade_id,
                        'timestamp': l.timestamp.isoformat(),
                        'direction': l.direction,
                        'session': l.session,
                        'outcome': l.outcome,
                        'grade': l.grade,
                        'r_multiple': self._sanitize_r(l.r_multiple),
                        'analysis': l.analysis,
                        'learnings': l.learnings or [],
                        'would_take_again': l.would_take_again
                    }
                    for l in learnings
                ]
                
        except Exception as e:
            logger.error(f"Failed to get learnings for {symbol}: {e}")
            return []
    
    async def get_recent_mistakes(self, limit: int = 5) -> List[str]:
        """
        Get the most recent mistakes from losing trades.
        
        Args:
            limit: Maximum number of mistakes to return
            
        Returns:
            List of mistake descriptions
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=KNOWLEDGE_RETENTION_DAYS)
            
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(TradeLearningModel)
                    .where(
                        and_(
                            TradeLearningModel.outcome == 'loss',
                            TradeLearningModel.timestamp >= cutoff
                        )
                    )
                    .order_by(desc(TradeLearningModel.timestamp))
                    .limit(limit * 2)  # Get extra to extract mistakes
                )
                
                learnings = result.scalars().all()
                
                mistakes = []
                for learning in learnings:
                    if learning.what_went_wrong:
                        for mistake in learning.what_went_wrong:
                            if isinstance(mistake, str):
                                mistakes.append(f"[{learning.symbol}] {mistake}")
                            if len(mistakes) >= limit:
                                break
                    if len(mistakes) >= limit:
                        break
                
                return mistakes[:limit]
                
        except Exception as e:
            logger.error(f"Failed to get recent mistakes: {e}")
            return []
    
    async def get_winning_patterns(self, limit: int = 5) -> List[str]:
        """
        Get patterns from winning trades (>2R).
        
        Args:
            limit: Maximum number of patterns to return
            
        Returns:
            List of winning pattern descriptions
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=KNOWLEDGE_RETENTION_DAYS)
            
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(TradeLearningModel)
                    .where(
                        and_(
                            TradeLearningModel.outcome == 'win',
                            TradeLearningModel.r_multiple >= 2.0,
                            TradeLearningModel.timestamp >= cutoff
                        )
                    )
                    .order_by(desc(TradeLearningModel.r_multiple))
                    .limit(limit * 2)
                )
                
                learnings = result.scalars().all()
                
                patterns = []
                for learning in learnings:
                    if learning.what_went_right:
                        for pattern in learning.what_went_right:
                            if isinstance(pattern, str):
                                patterns.append(f"[{learning.symbol}] {pattern} ({self._sanitize_r(learning.r_multiple):.1f}R)")
                            if len(patterns) >= limit:
                                break
                    if len(patterns) >= limit:
                        break
                
                return patterns[:limit]
                
        except Exception as e:
            logger.error(f"Failed to get winning patterns: {e}")
            return []
    
    async def get_knowledge_base(
        self,
        category: Optional[str] = None,
        include_expired: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get knowledge base entries.
        
        Args:
            category: Filter by category (optional)
            include_expired: Include expired entries
            
        Returns:
            List of knowledge entries
        """
        try:
            async with async_session_maker() as db_session:
                query = select(KnowledgeBaseModel)
                
                if not include_expired:
                    query = query.where(KnowledgeBaseModel.expires_at > datetime.utcnow())
                
                if category:
                    query = query.where(KnowledgeBaseModel.category == category)
                
                query = query.order_by(desc(KnowledgeBaseModel.confidence))
                
                result = await db_session.execute(query)
                entries = result.scalars().all()
                
                return [
                    {
                        'category': e.category,
                        'key': e.key,
                        'insight': e.insight,
                        'confidence': e.confidence,
                        'sample_size': e.sample_size,
                        'win_rate': e.win_rate,
                        'avg_r': e.avg_r,
                        'expires_at': e.expires_at.isoformat()
                    }
                    for e in entries
                ]
                
        except Exception as e:
            logger.error(f"Failed to get knowledge base: {e}")
            return []
    
    # =========================================================================
    # JUDGE ACCURACY ANALYSIS
    # =========================================================================
    
    async def get_judge_accuracy_stats(self, days_back: int = 30) -> Dict[str, Any]:
        """
        Analyze judge accuracy by correlating verdicts with trade outcomes.
        
        Returns stats on:
        - How many APPROVE trades won vs lost
        - How many DEMOTE trades won vs lost
        - How many REJECT signals would have won (false rejections)
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            
            async with async_session_maker() as db_session:
                # --- EXECUTED TRADES (from trades table) ---
                result = await db_session.execute(
                    select(TradeModel).where(
                        and_(
                            TradeModel.timestamp >= cutoff,
                            TradeModel.exit_price.isnot(None),
                            TradeModel.judge_verdict.isnot(None),
                        )
                    )
                )
                closed_trades = result.scalars().all()
                
                approve_wins = 0
                approve_losses = 0
                demote_wins = 0
                demote_losses = 0
                
                for t in closed_trades:
                    is_win = (t.profit_loss or 0) > 0
                    if t.judge_verdict == 'APPROVE':
                        if is_win:
                            approve_wins += 1
                        else:
                            approve_losses += 1
                    elif t.judge_verdict == 'DEMOTE':
                        if is_win:
                            demote_wins += 1
                        else:
                            demote_losses += 1
                
                # --- REJECTED SIGNALS (from analysis_logs) ---
                result = await db_session.execute(
                    select(AnalysisLogModel).where(
                        and_(
                            AnalysisLogModel.timestamp >= cutoff,
                            AnalysisLogModel.judge_verdict == 'REJECT',
                        )
                    )
                )
                rejected_signals = result.scalars().all()
                
                reject_would_win = len([s for s in rejected_signals if getattr(s, 'outcome_result', None) == 'would_have_won'])
                reject_would_lose = len([s for s in rejected_signals if getattr(s, 'outcome_result', None) == 'would_have_lost'])
                reject_unknown = len(rejected_signals) - reject_would_win - reject_would_lose
                
                # --- ALL SIGNALS (total by verdict) ---
                result = await db_session.execute(
                    select(AnalysisLogModel).where(
                        and_(
                            AnalysisLogModel.timestamp >= cutoff,
                            AnalysisLogModel.judge_verdict.isnot(None),
                        )
                    )
                )
                all_signals = result.scalars().all()
                
                total_approve = len([s for s in all_signals if s.judge_verdict == 'APPROVE'])
                total_demote = len([s for s in all_signals if s.judge_verdict == 'DEMOTE'])
                total_reject = len([s for s in all_signals if s.judge_verdict == 'REJECT'])
                
                # --- CONFLUENCE ANALYSIS ---
                # Which confluence counts lead to wins vs losses?
                confluence_stats = {}
                for t in closed_trades:
                    cc = getattr(t, 'confluence_count', None)
                    if cc is not None:
                        if cc not in confluence_stats:
                            confluence_stats[cc] = {'wins': 0, 'losses': 0}
                        if (t.profit_loss or 0) > 0:
                            confluence_stats[cc]['wins'] += 1
                        else:
                            confluence_stats[cc]['losses'] += 1
                
                return {
                    'period_days': days_back,
                    'total_signals': len(all_signals),
                    'total_approve': total_approve,
                    'total_demote': total_demote,
                    'total_reject': total_reject,
                    'approve_wins': approve_wins,
                    'approve_losses': approve_losses,
                    'approve_win_rate': approve_wins / max(1, approve_wins + approve_losses),
                    'demote_wins': demote_wins,
                    'demote_losses': demote_losses,
                    'demote_win_rate': demote_wins / max(1, demote_wins + demote_losses),
                    'reject_would_have_won': reject_would_win,
                    'reject_would_have_lost': reject_would_lose,
                    'reject_unknown': reject_unknown,
                    'false_rejection_rate': reject_would_win / max(1, reject_would_win + reject_would_lose) if (reject_would_win + reject_would_lose) > 0 else None,
                    'confluence_stats': confluence_stats,
                }
                
        except Exception as e:
            logger.error(f"Failed to get judge accuracy stats: {e}")
            return {}
    
    async def check_rejected_signal_outcomes(self, lookback_hours: int = 8) -> int:
        """
        Check rejected signals to see if they would have won or lost.
        
        Looks at rejected signals from the past N hours that haven't been
        checked yet, and determines if price hit the TP or SL.
        
        Returns: number of signals updated
        """
        try:
            cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
            
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(AnalysisLogModel).where(
                        and_(
                            AnalysisLogModel.judge_verdict == 'REJECT',
                            AnalysisLogModel.outcome_result.is_(None),
                            AnalysisLogModel.timestamp >= cutoff,
                            AnalysisLogModel.entry_price.isnot(None),
                            AnalysisLogModel.stop_loss.isnot(None),
                            AnalysisLogModel.take_profit.isnot(None),
                        )
                    )
                )
                signals = result.scalars().all()
                
                if not signals:
                    return 0
                
                updated = 0
                
                # We need MT5 price data to check outcomes
                try:
                    import MetaTrader5 as mt5
                    
                    for signal in signals:
                        try:
                            rates = mt5.copy_rates_from(
                                signal.symbol, mt5.TIMEFRAME_M5,
                                signal.timestamp, 100
                            )
                            
                            if rates is None or len(rates) == 0:
                                continue
                            
                            sl = signal.stop_loss
                            tp = signal.take_profit
                            direction = signal.signal_direction
                            
                            hit_tp = False
                            hit_sl = False
                            
                            for rate in rates:
                                high = rate[2]  # high
                                low = rate[3]   # low
                                
                                if direction == 'long':
                                    if low <= sl:
                                        hit_sl = True
                                        break
                                    if high >= tp:
                                        hit_tp = True
                                        break
                                else:  # short
                                    if high >= sl:
                                        hit_sl = True
                                        break
                                    if low <= tp:
                                        hit_tp = True
                                        break
                            
                            if hit_tp:
                                signal.outcome_result = 'would_have_won'
                                signal.outcome_price = tp
                                updated += 1
                            elif hit_sl:
                                signal.outcome_result = 'would_have_lost'
                                signal.outcome_price = sl
                                updated += 1
                            # else: still in play, leave as None
                            
                        except Exception as e:
                            logger.debug(f"Could not check outcome for signal {signal.id}: {e}")
                            continue
                    
                    await db_session.commit()
                    
                except Exception:
                    logger.debug("MT5 not available for rejected signal outcome tracking")
                
                if updated > 0:
                    logger.info(f"Updated {updated} rejected signal outcomes")
                return updated
                
        except Exception as e:
            logger.error(f"Failed to check rejected signal outcomes: {e}")
            return 0
    
    # =========================================================================
    # CONTEXT BUILDING
    # =========================================================================
    
    async def build_context_for_claude(
        self,
        symbol: str,
        session: str = ""
    ) -> str:
        """
        Build dynamic learning context for Claude's analysis prompt.
        
        Includes trade learnings, judge accuracy stats, and confluence patterns.
        
        Args:
            symbol: Trading symbol being analyzed
            session: Current trading session
            
        Returns:
            Formatted markdown string with learning context
        """
        try:
            # Get symbol-specific learnings
            symbol_learnings = await self.get_learnings_for_symbol(symbol, limit=5)
            
            # Get recent mistakes
            recent_mistakes = await self.get_recent_mistakes(limit=5)
            
            # Get winning patterns
            winning_patterns = await self.get_winning_patterns(limit=5)
            
            # Get relevant knowledge base entries
            knowledge = await self.get_knowledge_base()
            symbol_knowledge = [k for k in knowledge if symbol.lower() in k['key'].lower()]
            
            # Get judge accuracy stats (30-day rolling)
            judge_stats = await self.get_judge_accuracy_stats(days_back=30)
            
            # Build context string
            context_parts = []
            
            context_parts.append("## Learning Context (Past 90 Days)")
            
            # Symbol-specific section
            if symbol_learnings:
                losses = [l for l in symbol_learnings if l['outcome'] == 'loss']
                wins = [l for l in symbol_learnings if l['outcome'] == 'win']
                
                context_parts.append(f"\n### Symbol: {symbol}")
                context_parts.append(f"- Recent trades reviewed: {len(symbol_learnings)}")
                context_parts.append(f"- Losses: {len(losses)} | Wins: {len(wins)}")
                
                if losses:
                    context_parts.append(f"- Last loss grade: {losses[0]['grade']}")
                    if losses[0]['learnings']:
                        context_parts.append(f"- Key learning: {losses[0]['learnings'][0] if losses[0]['learnings'] else 'N/A'}")
            
            # Symbol knowledge
            if symbol_knowledge:
                context_parts.append(f"\n### {symbol} Insights (High Confidence)")
                for k in symbol_knowledge[:3]:
                    context_parts.append(f"- {k['insight']} (confidence: {k['confidence']:.0%})")
            
            # Judge accuracy section
            if judge_stats and judge_stats.get('total_signals', 0) > 0:
                context_parts.append("\n### Trade Judge Performance (Last 30 Days)")
                context_parts.append(f"- Signals analyzed: {judge_stats.get('total_signals', 0)} (Approved: {judge_stats.get('total_approve', 0)}, Demoted: {judge_stats.get('total_demote', 0)}, Rejected: {judge_stats.get('total_reject', 0)})")
                
                if judge_stats.get('approve_wins', 0) + judge_stats.get('approve_losses', 0) > 0:
                    context_parts.append(f"- Approved trades: {judge_stats['approve_wins']}W / {judge_stats['approve_losses']}L ({judge_stats['approve_win_rate']:.0%} win rate)")
                
                if judge_stats.get('demote_wins', 0) + judge_stats.get('demote_losses', 0) > 0:
                    context_parts.append(f"- Demoted trades: {judge_stats['demote_wins']}W / {judge_stats['demote_losses']}L ({judge_stats['demote_win_rate']:.0%} win rate)")
                
                if judge_stats.get('reject_would_have_won', 0) > 0:
                    context_parts.append(f"- **False rejections (missed winners): {judge_stats['reject_would_have_won']}** — consider being less restrictive")
                
                # Confluence insights
                confluence = judge_stats.get('confluence_stats', {})
                if confluence:
                    best_cc = max(confluence.items(), key=lambda x: x[1]['wins'] / max(1, x[1]['wins'] + x[1]['losses']), default=None)
                    if best_cc:
                        cc_val, cc_stats = best_cc
                        cc_wr = cc_stats['wins'] / max(1, cc_stats['wins'] + cc_stats['losses'])
                        context_parts.append(f"- Best confluence count: {cc_val} factors ({cc_wr:.0%} win rate)")
            
            # Recent mistakes
            if recent_mistakes:
                context_parts.append("\n### Recent Mistakes to Avoid")
                for i, mistake in enumerate(recent_mistakes, 1):
                    context_parts.append(f"{i}. {mistake}")
            
            # Winning patterns
            if winning_patterns:
                context_parts.append("\n### Winning Patterns (What Works)")
                for i, pattern in enumerate(winning_patterns, 1):
                    context_parts.append(f"{i}. {pattern}")
            
            # Symbol + direction pattern stats from DB
            try:
                pattern_lines = await self._get_symbol_pattern_stats(symbol)
                if pattern_lines:
                    context_parts.append("\n### Trade Patterns (Historical)")
                    context_parts.extend(pattern_lines)
            except Exception:
                pass
            
            # Final warning
            context_parts.append("\n**IMPORTANT:** Consider these learnings before making your recommendation.")
            context_parts.append("If this setup matches a known mistake pattern, reduce confidence or recommend no_trade.")
            
            return "\n".join(context_parts) if len(context_parts) > 1 else ""
            
        except Exception as e:
            logger.error(f"Failed to build Claude context: {e}")
            return ""
    
    async def _get_symbol_pattern_stats(self, symbol: str) -> List[str]:
        """Query DB for symbol+direction win/loss stats to give Claude explicit pattern feedback."""
        lines = []
        try:
            from sqlalchemy import select, func, case
            async with async_session_maker() as session:
                cutoff = datetime.utcnow() - timedelta(days=90)
                stmt = (
                    select(
                        TradeModel.direction,
                        func.count().label('total'),
                        func.sum(case((TradeModel.profit_loss > 0, 1), else_=0)).label('wins'),
                        func.sum(case((TradeModel.profit_loss <= 0, 1), else_=0)).label('losses'),
                        func.avg(TradeModel.r_multiple).label('avg_r'),
                    )
                    .where(TradeModel.symbol == symbol)
                    .where(TradeModel.timestamp >= cutoff)
                    .where(TradeModel.exit_price.isnot(None))
                    .group_by(TradeModel.direction)
                )
                result = await session.execute(stmt)
                rows = result.all()
                for row in rows:
                    direction = row.direction or 'unknown'
                    total = row.total or 0
                    wins = int(row.wins or 0)
                    losses = int(row.losses or 0)
                    avg_r = float(row.avg_r or 0)
                    wr = wins / total * 100 if total > 0 else 0
                    lines.append(
                        f"[PATTERN] {symbol} {direction.upper()}: {wins}W/{losses}L "
                        f"({wr:.0f}% win rate), avg {avg_r:.1f}R over last {total} trades"
                    )
                    if wr < 40 and total >= 3:
                        lines.append(f"  WARNING: {direction.upper()} trades on {symbol} are losing money. Consider avoiding or reducing size.")
                    elif wr >= 70 and total >= 3:
                        lines.append(f"  STRONG: {direction.upper()} trades on {symbol} are profitable. Lean toward this direction.")
        except Exception as e:
            logger.debug(f"Could not get symbol pattern stats: {e}")
        return lines
    
    # =========================================================================
    # WEEKLY CONSOLIDATION
    # =========================================================================
    
    async def consolidate_weekly(
        self,
        claude_client: "ClaudeClient"
    ) -> Optional[WeeklyReviewModel]:
        """
        Perform weekly consolidation of learnings with Claude-generated insights.
        
        Now includes judge accuracy analysis and rejected signal outcomes
        for comprehensive learning.
        
        Args:
            claude_client: ClaudeClient instance for generating insights
            
        Returns:
            Created WeeklyReviewModel or None on failure
        """
        try:
            # Check rejected signal outcomes before consolidating (non-critical)
            try:
                await self.check_rejected_signal_outcomes(lookback_hours=168)  # 7 days
            except Exception as e:
                logger.debug(f"Rejected signal outcome check skipped: {e}")
            
            # Get this week's learnings
            week_start = datetime.utcnow() - timedelta(days=7)
            week_end = datetime.utcnow()
            
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(TradeLearningModel)
                    .where(TradeLearningModel.timestamp >= week_start)
                    .order_by(TradeLearningModel.timestamp)
                )
                
                learnings = result.scalars().all()
            
            # Get judge accuracy stats for the week
            judge_stats = await self.get_judge_accuracy_stats(days_back=7)
            
            # Get rejected signals with outcomes
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(AnalysisLogModel).where(
                        and_(
                            AnalysisLogModel.timestamp >= week_start,
                            AnalysisLogModel.judge_verdict == 'REJECT',
                            AnalysisLogModel.outcome_result.isnot(None),
                        )
                    )
                )
                rejected_with_outcomes = result.scalars().all()
            
            if not learnings and not judge_stats.get('total_signals'):
                logger.info("No learnings or signals to consolidate this week")
                return None
            
            # Compile learnings data
            learnings_data = []
            for l in learnings:
                learnings_data.append({
                    'symbol': l.symbol,
                    'direction': l.direction,
                    'session': l.session,
                    'setup_type': l.setup_type or 'ICT',
                    'entry_reason': l.entry_reason or 'N/A',
                    'original_confidence': l.original_confidence if l.original_confidence is not None else 'N/A',
                    'timeframe': l.timeframe or 'N/A',
                    'outcome': l.outcome,
                    'grade': l.grade,
                    'r_multiple': self._sanitize_r(l.r_multiple),
                    'what_went_right': l.what_went_right or [],
                    'what_went_wrong': l.what_went_wrong or [],
                    'learnings': l.learnings or [],
                    # NEW: judge & confluence context
                    'judge_verdict': getattr(l, 'judge_verdict', None),
                    'judge_reason': getattr(l, 'judge_reason', None),
                    'confluence_factors': getattr(l, 'confluence_factors', None),
                    'confluence_count': getattr(l, 'confluence_count', None),
                })
            
            # Compile judge analysis section
            judge_analysis = {
                'stats': judge_stats,
                'false_rejections': [
                    {
                        'symbol': s.symbol,
                        'direction': s.signal_direction,
                        'confidence': s.confidence,
                        'entry': s.entry_price,
                        'tp': s.take_profit,
                        'sl': s.stop_loss,
                        'judge_reason': getattr(s, 'judge_reason', 'N/A'),
                        'outcome': getattr(s, 'outcome_result', 'unknown'),
                    }
                    for s in rejected_with_outcomes
                    if getattr(s, 'outcome_result', None) == 'would_have_won'
                ],
                'correct_rejections': [
                    {
                        'symbol': s.symbol,
                        'direction': s.signal_direction,
                        'confidence': s.confidence,
                        'judge_reason': getattr(s, 'judge_reason', 'N/A'),
                    }
                    for s in rejected_with_outcomes
                    if getattr(s, 'outcome_result', None) == 'would_have_lost'
                ],
            }
            
            # Combine everything into one data package for Claude
            # Sanitize data to ensure JSON-safe types (handles mock objects in tests)
            def _sanitize_for_json(obj):
                if isinstance(obj, dict):
                    return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [_sanitize_for_json(i) for i in obj]
                elif isinstance(obj, (str, int, float, bool)) or obj is None:
                    return obj
                else:
                    return str(obj)
            
            consolidation_data = json.dumps(_sanitize_for_json({
                'trade_reviews': learnings_data,
                'judge_analysis': judge_analysis,
            }), indent=2)
            
            # Have Claude generate insights (now with judge data)
            insights = await claude_client.generate_weekly_insights(consolidation_data)
            
            # Calculate statistics
            wins = len([l for l in learnings if l.outcome == 'win'])
            losses = len([l for l in learnings if l.outcome == 'loss'])
            total_pnl = sum(l.profit_loss for l in learnings)
            total_r = sum(self._sanitize_r(l.r_multiple) for l in learnings)
            
            # Create weekly review
            async with async_session_maker() as db_session:
                review = WeeklyReviewModel(
                    week_start=week_start,
                    week_end=week_end,
                    performance_grade=insights.get('performance_grade', 'C'),
                    summary=insights.get('summary', ''),
                    total_trades=len(learnings),
                    wins=wins,
                    losses=losses,
                    total_pnl=total_pnl,
                    total_r=total_r,
                    patterns_identified=insights.get('patterns_identified', []),
                    recurring_mistakes=insights.get('recurring_mistakes', []),
                    winning_patterns=insights.get('winning_patterns', []),
                    recommendations=insights.get('recommendations', []),
                    symbol_insights=insights.get('symbol_insights', {}),
                    session_insights=insights.get('session_insights', {}),
                    focus_area=insights.get('focus_area', ''),
                    best_setup=insights.get('best_setup', '')
                )
                
                db_session.add(review)
                await db_session.commit()
                await db_session.refresh(review)
            
            logger.info(f"Created weekly review: Grade {review.performance_grade}, {len(learnings)} trades")
            
            # Update knowledge base from insights
            await self._update_knowledge_from_insights(insights, learnings)
            
            # Update documentation
            await self.update_learnings_documentation()
            
            # Send notification
            await self.send_weekly_notification(review)
            
            return review
            
        except Exception as e:
            logger.error(f"Failed to consolidate weekly: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def _update_knowledge_from_insights(
        self,
        insights: Dict[str, Any],
        learnings: List[TradeLearningModel]
    ):
        """Update knowledge base from weekly insights."""
        try:
            expires_at = datetime.utcnow() + timedelta(days=KNOWLEDGE_RETENTION_DAYS)
            
            async with async_session_maker() as db_session:
                # Update symbol insights
                symbol_insights = insights.get('symbol_insights', {})
                for symbol, insight in symbol_insights.items():
                    if isinstance(insight, str) and insight:
                        key = f"{symbol}_weekly_insight"
                        
                        # Check if exists
                        result = await db_session.execute(
                            select(KnowledgeBaseModel).where(KnowledgeBaseModel.key == key)
                        )
                        existing = result.scalar_one_or_none()
                        
                        symbol_learnings = [l for l in learnings if l.symbol == symbol]
                        sample_size = len(symbol_learnings)
                        wins = len([l for l in symbol_learnings if l.outcome == 'win'])
                        win_rate = wins / sample_size if sample_size > 0 else 0
                        avg_r = sum(self._sanitize_r(l.r_multiple) for l in symbol_learnings) / sample_size if sample_size > 0 else 0
                        
                        if existing:
                            existing.insight = insight
                            existing.sample_size += sample_size
                            existing.win_rate = win_rate
                            existing.avg_r = avg_r
                            existing.expires_at = expires_at
                            existing.confidence = min(0.9, existing.sample_size / 50)
                        else:
                            knowledge = KnowledgeBaseModel(
                                category="symbol_pattern",
                                key=key,
                                insight=insight,
                                confidence=min(0.9, sample_size / 50),
                                sample_size=sample_size,
                                win_rate=win_rate,
                                avg_r=avg_r,
                                expires_at=expires_at
                            )
                            db_session.add(knowledge)
                
                # Update recurring mistakes
                mistakes = insights.get('recurring_mistakes', [])
                for i, mistake in enumerate(mistakes[:5]):
                    if isinstance(mistake, str) and mistake:
                        key = f"mistake_{i}_{datetime.utcnow().strftime('%Y%m%d')}"
                        
                        knowledge = KnowledgeBaseModel(
                            category="mistake",
                            key=key,
                            insight=mistake,
                            confidence=0.7,
                            sample_size=len(learnings),
                            expires_at=expires_at
                        )
                        db_session.add(knowledge)
                
                await db_session.commit()
                logger.info("Updated knowledge base from weekly insights")
                
        except Exception as e:
            logger.error(f"Failed to update knowledge base: {e}")
    
    # =========================================================================
    # DOCUMENTATION UPDATE
    # =========================================================================
    
    async def update_learnings_documentation(self):
        """
        Write consolidated high-confidence insights to trading_learnings.md.
        
        This creates permanent documentation that Claude loads as part of
        its strategy context.
        """
        try:
            # Get high-confidence knowledge
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(KnowledgeBaseModel)
                    .where(
                        and_(
                            KnowledgeBaseModel.confidence >= HIGH_CONFIDENCE_THRESHOLD,
                            KnowledgeBaseModel.sample_size >= MIN_SAMPLE_SIZE_FOR_DOCS,
                            KnowledgeBaseModel.expires_at > datetime.utcnow()
                        )
                    )
                    .order_by(desc(KnowledgeBaseModel.confidence))
                )
                
                high_confidence = result.scalars().all()
            
            # Get latest weekly review
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(WeeklyReviewModel)
                    .order_by(desc(WeeklyReviewModel.created_at))
                    .limit(1)
                )
                latest_review = result.scalar_one_or_none()
            
            # Generate markdown content
            content = self._generate_learnings_markdown(high_confidence, latest_review)
            
            # Write to docs folder
            self._docs_path.write_text(content, encoding='utf-8')
            
            logger.info(f"Updated trading_learnings.md with {len(high_confidence)} high-confidence insights")
            
        except Exception as e:
            logger.error(f"Failed to update learnings documentation: {e}")
    
    def _generate_learnings_markdown(
        self,
        knowledge: List[KnowledgeBaseModel],
        latest_review: Optional[WeeklyReviewModel]
    ) -> str:
        """Generate markdown content for trading_learnings.md."""
        lines = [
            "# Trading Learnings (Auto-Generated)",
            f"Last Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
            "",
            "> This file is automatically generated by the learning system.",
            "> Do not edit manually - changes will be overwritten.",
            "",
        ]
        
        # Permanent rules (high confidence)
        high_conf = [k for k in knowledge if k.confidence >= 0.8]
        if high_conf:
            lines.append("## Permanent Rules (High Confidence)")
            lines.append("")
            for k in high_conf:
                lines.append(f"- **[{k.category}]** {k.insight}")
                lines.append(f"  - Confidence: {k.confidence:.0%} | Sample: {k.sample_size} trades | Win Rate: {k.win_rate:.0%}")
            lines.append("")
        
        # Symbol-specific insights
        symbol_knowledge = [k for k in knowledge if k.category == "symbol_pattern"]
        if symbol_knowledge:
            lines.append("## Symbol-Specific Insights")
            lines.append("")
            for k in symbol_knowledge:
                symbol = k.key.split('_')[0].upper()
                lines.append(f"### {symbol}")
                lines.append(f"- {k.insight}")
                lines.append(f"- Win Rate: {k.win_rate:.0%} | Avg R: {k.avg_r:.2f}")
                lines.append("")
        
        # Mistakes to avoid
        mistakes = [k for k in knowledge if k.category == "mistake"]
        if mistakes:
            lines.append("## Patterns to Avoid")
            lines.append("")
            for i, k in enumerate(mistakes[:5], 1):
                lines.append(f"{i}. {k.insight}")
            lines.append("")
        
        # Latest weekly summary
        if latest_review:
            lines.append("## Latest Weekly Review")
            lines.append("")
            lines.append(f"**Week:** {latest_review.week_start.strftime('%Y-%m-%d')} to {latest_review.week_end.strftime('%Y-%m-%d')}")
            lines.append(f"**Grade:** {latest_review.performance_grade}")
            lines.append("")
            
            if latest_review.summary:
                lines.append(f"**Summary:** {latest_review.summary}")
                lines.append("")
            
            if latest_review.focus_area:
                lines.append(f"**Focus Area:** {latest_review.focus_area}")
                lines.append("")
            
            if latest_review.best_setup:
                lines.append(f"**Best Setup:** {latest_review.best_setup}")
                lines.append("")
        
        return "\n".join(lines)
    
    # =========================================================================
    # NOTIFICATIONS
    # =========================================================================
    
    async def send_weekly_notification(self, review: WeeklyReviewModel):
        """
        Send Telegram notification with weekly learning report.
        
        Args:
            review: The weekly review to notify about
        """
        try:
            from ..utils.notifications import notify, NotificationType
            
            # Build top insights
            top_insights = []
            if review.winning_patterns:
                for pattern in review.winning_patterns[:3]:
                    if isinstance(pattern, str):
                        top_insights.append(pattern)
            
            # Build mistakes
            mistakes = []
            if review.recurring_mistakes:
                for mistake in review.recurring_mistakes[:3]:
                    if isinstance(mistake, str):
                        mistakes.append(mistake)
            
            message = f"""📊 Weekly Learning Report

Grade: {review.performance_grade}
Trades Reviewed: {review.total_trades}
Wins: {review.wins} | Losses: {review.losses}
Total P/L: ${review.total_pnl:,.2f}
Total R: {review.total_r:.1f}R

🎯 Top Insights:
{chr(10).join(f"• {i}" for i in top_insights) if top_insights else "• None identified"}

⚠️ Patterns to Avoid:
{chr(10).join(f"• {m}" for m in mistakes) if mistakes else "• None identified"}

📈 Best Setup: {review.best_setup or 'N/A'}
🎯 Focus Area: {review.focus_area or 'N/A'}
"""
            
            await notify(NotificationType.INFO, message)
            logger.info("Sent weekly learning notification")
            
        except Exception as e:
            logger.warning(f"Failed to send weekly notification: {e}")
    
    # =========================================================================
    # MAINTENANCE
    # =========================================================================
    
    async def prune_expired_knowledge(self) -> int:
        """
        Remove expired knowledge base entries.
        
        Returns:
            Number of entries removed
        """
        try:
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(KnowledgeBaseModel)
                    .where(KnowledgeBaseModel.expires_at <= datetime.utcnow())
                )
                
                expired = result.scalars().all()
                count = len(expired)
                
                for entry in expired:
                    await db_session.delete(entry)
                
                await db_session.commit()
                
                if count > 0:
                    logger.info(f"Pruned {count} expired knowledge entries")
                
                return count
                
        except Exception as e:
            logger.error(f"Failed to prune expired knowledge: {e}")
            return 0
    
    async def get_latest_weekly_report(self) -> Optional[Dict[str, Any]]:
        """Get the latest weekly review report."""
        try:
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(WeeklyReviewModel)
                    .order_by(desc(WeeklyReviewModel.created_at))
                    .limit(1)
                )
                
                review = result.scalar_one_or_none()
                
                if not review:
                    return None
                
                return {
                    'week_start': review.week_start.isoformat(),
                    'week_end': review.week_end.isoformat(),
                    'performance_grade': review.performance_grade,
                    'summary': review.summary,
                    'total_trades': review.total_trades,
                    'wins': review.wins,
                    'losses': review.losses,
                    'total_pnl': review.total_pnl,
                    'total_r': review.total_r,
                    'patterns_identified': review.patterns_identified,
                    'recurring_mistakes': review.recurring_mistakes,
                    'winning_patterns': review.winning_patterns,
                    'recommendations': review.recommendations,
                    'symbol_insights': review.symbol_insights,
                    'session_insights': review.session_insights,
                    'focus_area': review.focus_area,
                    'best_setup': review.best_setup,
                    'created_at': review.created_at.isoformat()
                }
                
        except Exception as e:
            logger.error(f"Failed to get latest weekly report: {e}")
            return None
    
    async def get_all_learnings(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get all trade learnings with pagination."""
        try:
            async with async_session_maker() as db_session:
                result = await db_session.execute(
                    select(TradeLearningModel)
                    .order_by(desc(TradeLearningModel.timestamp))
                    .limit(limit)
                    .offset(offset)
                )
                
                learnings = result.scalars().all()
                
                return [
                    {
                        'id': l.id,
                        'trade_id': l.trade_id,
                        'timestamp': l.timestamp.isoformat(),
                        'symbol': l.symbol,
                        'direction': l.direction,
                        'session': l.session,
                        'setup_type': l.setup_type,
                        'profit_loss': l.profit_loss,
                        'r_multiple': l.r_multiple,
                        'outcome': l.outcome,
                        'grade': l.grade,
                        'analysis': l.analysis,
                        'what_went_right': l.what_went_right,
                        'what_went_wrong': l.what_went_wrong,
                        'learnings': l.learnings,
                        'would_take_again': l.would_take_again
                    }
                    for l in learnings
                ]
                
        except Exception as e:
            logger.error(f"Failed to get all learnings: {e}")
            return []
