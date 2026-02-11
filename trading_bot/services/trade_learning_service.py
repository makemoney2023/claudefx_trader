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
    WeeklyReviewModel
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
    ) -> Optional[TradeLearningModel]:
        """
        Store Claude's review of a trade.
        
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
                    would_take_again=review.get('would_take_again', True)
                )
                
                db_session.add(learning)
                await db_session.commit()
                await db_session.refresh(learning)
                
                logger.info(f"Stored trade learning for {trade_id} ({symbol}): Grade {learning.grade}")
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
                        'r_multiple': l.r_multiple,
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
                                patterns.append(f"[{learning.symbol}] {pattern} ({learning.r_multiple:.1f}R)")
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
    # CONTEXT BUILDING
    # =========================================================================
    
    async def build_context_for_claude(
        self,
        symbol: str,
        session: str = ""
    ) -> str:
        """
        Build dynamic learning context for Claude's analysis prompt.
        
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
            
            # Final warning
            context_parts.append("\n**IMPORTANT:** Consider these learnings before making your recommendation.")
            context_parts.append("If this setup matches a known mistake pattern, reduce confidence or recommend no_trade.")
            
            return "\n".join(context_parts) if len(context_parts) > 1 else ""
            
        except Exception as e:
            logger.error(f"Failed to build Claude context: {e}")
            return ""
    
    # =========================================================================
    # WEEKLY CONSOLIDATION
    # =========================================================================
    
    async def consolidate_weekly(
        self,
        claude_client: "ClaudeClient"
    ) -> Optional[WeeklyReviewModel]:
        """
        Perform weekly consolidation of learnings with Claude-generated insights.
        
        Args:
            claude_client: ClaudeClient instance for generating insights
            
        Returns:
            Created WeeklyReviewModel or None on failure
        """
        try:
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
            
            if not learnings:
                logger.info("No learnings to consolidate this week")
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
                    'r_multiple': l.r_multiple,
                    'what_went_right': l.what_went_right or [],
                    'what_went_wrong': l.what_went_wrong or [],
                    'learnings': l.learnings or []
                })
            
            # Have Claude generate insights
            insights = await claude_client.generate_weekly_insights(
                json.dumps(learnings_data, indent=2)
            )
            
            # Calculate statistics
            wins = len([l for l in learnings if l.outcome == 'win'])
            losses = len([l for l in learnings if l.outcome == 'loss'])
            total_pnl = sum(l.profit_loss for l in learnings)
            total_r = sum(l.r_multiple for l in learnings)
            
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
                        avg_r = sum(l.r_multiple for l in symbol_learnings) / sample_size if sample_size > 0 else 0
                        
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
