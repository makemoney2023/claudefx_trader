"""
Database layer for the ICT Trading Bot.

Uses SQLAlchemy with async support for:
- Trade history persistence
- Analysis logs
- Configuration storage
"""

from datetime import datetime
from typing import Optional, List, AsyncGenerator

from sqlalchemy import String, Float, Integer, DateTime, Boolean, Text, JSON, ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import select, desc, text

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


# Database URL - using SQLite for simplicity, can upgrade to PostgreSQL
DATABASE_URL = "sqlite+aiosqlite:///./trading_bot.db"


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


# Models
class TradeModel(Base):
    """Trade record model."""
    __tablename__ = "trades"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Trade details
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    timeframe: Mapped[str] = mapped_column(String(10), default="")
    session: Mapped[str] = mapped_column(String(20), default="")
    
    # Entry
    entry_price: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    entry_reason: Mapped[str] = mapped_column(Text, default="")
    
    # Exit
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Risk management
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    position_size: Mapped[float] = mapped_column(Float)
    risk_amount: Mapped[float] = mapped_column(Float, default=0)
    
    # Results
    profit_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_loss_pips: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # ICT context
    market_structure: Mapped[str] = mapped_column(String(20), default="")
    ict_concepts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Claude analysis
    claude_confidence: Mapped[float] = mapped_column(Float, default=0)
    claude_reasoning: Mapped[str] = mapped_column(Text, default="")
    
    # Notes
    notes: Mapped[str] = mapped_column(Text, default="")
    
    # Gap 3: Link to source signal (simple FK, no ORM relationship to avoid complexity)
    signal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Note: Removed ORM relationships to avoid foreign key ambiguity issues
    # Use direct queries instead of relationship navigation


class AnalysisLogModel(Base):
    """Analysis log model for storing Claude's analysis history."""
    __tablename__ = "analysis_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    
    # Analysis context
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    session: Mapped[str] = mapped_column(String(20))
    
    # Market state
    market_structure: Mapped[str] = mapped_column(String(20))
    trend: Mapped[str] = mapped_column(String(20))
    
    # Analysis results
    signal_direction: Mapped[str] = mapped_column(String(10))
    confidence: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Full analysis
    analysis_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    warnings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Link to trade if signal was taken (simple FK, no ORM relationship)
    trade_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ConfigSnapshotModel(Base):
    """Configuration snapshot for tracking config changes."""
    __tablename__ = "config_snapshots"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Config data
    config_type: Mapped[str] = mapped_column(String(50))  # 'trading', 'timeframes', etc.
    config_data: Mapped[dict] = mapped_column(JSON)
    
    # Change tracking
    changed_by: Mapped[str] = mapped_column(String(50), default="api")
    change_reason: Mapped[str] = mapped_column(Text, default="")


class PerformanceSnapshotModel(Base):
    """Daily performance snapshot for tracking equity curve."""
    __tablename__ = "performance_snapshots"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    
    # Account state
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    
    # Daily stats
    trades_opened: Mapped[int] = mapped_column(Integer, default=0)
    trades_closed: Mapped[int] = mapped_column(Integer, default=0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0)
    daily_r: Mapped[float] = mapped_column(Float, default=0)
    
    # Cumulative stats
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    total_wins: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0)
    
    # Drawdown
    peak_equity: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float, default=0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0)


class PositionStateModel(Base):
    """Position state for persistence across restarts."""
    __tablename__ = "position_states"
    
    ticket: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    volume: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    open_time: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="open")
    
    # Trade classification
    trade_type: Mapped[str] = mapped_column(String(20), default="intraday")
    
    # Position management state
    initial_sl: Mapped[float] = mapped_column(Float, default=0)
    be_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Multi-TP state (persisted for restart recovery)
    tp1: Mapped[float] = mapped_column(Float, default=0)
    tp2: Mapped[float] = mapped_column(Float, default=0)
    tp3: Mapped[float] = mapped_column(Float, default=0)
    tp1_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    tp2_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    initial_volume: Mapped[float] = mapped_column(Float, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TradeLearningModel(Base):
    """
    Stores Claude's review of individual trades (losses and big wins).
    
    Used for building dynamic learning context for future analysis.
    """
    __tablename__ = "trade_learnings"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(50), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    
    # Trade context
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    session: Mapped[str] = mapped_column(String(20), default="")
    setup_type: Mapped[str] = mapped_column(String(50), default="ICT")
    entry_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    original_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    timeframe: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default=None)
    
    # Trade results
    profit_loss: Mapped[float] = mapped_column(Float, default=0)
    r_multiple: Mapped[float] = mapped_column(Float, default=0)
    
    # Claude's review
    outcome: Mapped[str] = mapped_column(String(20))  # win, loss, breakeven
    grade: Mapped[str] = mapped_column(String(5))  # A, B, C, D, F
    analysis: Mapped[str] = mapped_column(Text, default="")
    what_went_right: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    what_went_wrong: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    learnings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    improvement_suggestions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    would_take_again: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KnowledgeBaseModel(Base):
    """
    Consolidated trading insights from Claude's analysis.
    
    Aggregated patterns and learnings with confidence scores.
    Expires after 90 days if not updated.
    """
    __tablename__ = "knowledge_base"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Knowledge categorization
    category: Mapped[str] = mapped_column(String(50), index=True)  # symbol_pattern, session_insight, mistake, best_setup
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)  # e.g., "EURUSD_london", "chasing_entries"
    
    # The insight
    insight: Mapped[str] = mapped_column(Text)
    
    # Confidence metrics
    confidence: Mapped[float] = mapped_column(Float, default=0.5)  # 0-1 based on sample size
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0)
    avg_r: Mapped[float] = mapped_column(Float, default=0)
    
    # Timestamps and expiry
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # 90 days from last update
    
    # Reference to last contributing trade
    last_trade_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class WeeklyReviewModel(Base):
    """
    Weekly performance review generated by Claude.
    
    Comprehensive analysis of the week's trades with patterns and recommendations.
    """
    __tablename__ = "weekly_reviews"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Week period
    week_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    week_end: Mapped[datetime] = mapped_column(DateTime)
    
    # Performance summary
    performance_grade: Mapped[str] = mapped_column(String(5))  # A, B, C, D, F
    summary: Mapped[str] = mapped_column(Text)
    
    # Statistics
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0)
    total_r: Mapped[float] = mapped_column(Float, default=0)
    
    # Claude's insights (JSON)
    patterns_identified: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    recurring_mistakes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    winning_patterns: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    recommendations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    symbol_insights: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    session_insights: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    focus_area: Mapped[str] = mapped_column(Text, default="")
    best_setup: Mapped[str] = mapped_column(String(100), default="")
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Database engine and session
engine = create_async_engine(DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Alias for backwards compatibility
AsyncSessionLocal = async_session_maker
async_session = async_session_maker  # Another common alias


async def init_db():
    """Initialize the database (create tables and migrate new columns)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Migrate: add missing columns to existing tables (SQLite ALTER TABLE)
    async with engine.begin() as conn:
        migrations = [
            ("position_states", "tp1", "FLOAT DEFAULT 0"),
            ("position_states", "tp2", "FLOAT DEFAULT 0"),
            ("position_states", "tp3", "FLOAT DEFAULT 0"),
            ("position_states", "tp1_hit", "BOOLEAN DEFAULT 0"),
            ("position_states", "tp2_hit", "BOOLEAN DEFAULT 0"),
            ("position_states", "initial_volume", "FLOAT DEFAULT 0"),
            ("position_states", "trade_type", "VARCHAR(20) DEFAULT 'intraday'"),
            ("trade_learnings", "entry_reason", "TEXT"),
            ("trade_learnings", "original_confidence", "FLOAT"),
            ("trade_learnings", "timeframe", "VARCHAR(10)"),
        ]
        for table, column, col_type in migrations:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info(f"Added column {table}.{column}")
            except Exception:
                pass  # Column already exists
    
    logger.info("Database initialized")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


# Repository classes for data access
class TradeRepository:
    """Repository for trade data access."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, trade_data: dict) -> TradeModel:
        """Create a new trade record."""
        trade = TradeModel(**trade_data)
        self.session.add(trade)
        await self.session.commit()
        await self.session.refresh(trade)
        return trade
    
    async def get_by_id(self, trade_id: str) -> Optional[TradeModel]:
        """Get a trade by its ID."""
        result = await self.session.execute(
            select(TradeModel).where(TradeModel.trade_id == trade_id)
        )
        return result.scalar_one_or_none()
    
    async def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        symbol: Optional[str] = None,
        direction: Optional[str] = None
    ) -> List[TradeModel]:
        """Get all trades with optional filtering."""
        query = select(TradeModel).order_by(desc(TradeModel.timestamp))
        
        if symbol:
            query = query.where(TradeModel.symbol == symbol)
        if direction:
            query = query.where(TradeModel.direction == direction)
        
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_open_trades(self) -> List[TradeModel]:
        """Get all open trades."""
        result = await self.session.execute(
            select(TradeModel)
            .where(TradeModel.exit_price.is_(None))
            .order_by(desc(TradeModel.entry_time))
        )
        return list(result.scalars().all())
    
    async def update(self, trade_id: str, **kwargs) -> Optional[TradeModel]:
        """Update a trade record."""
        trade = await self.get_by_id(trade_id)
        if trade:
            for key, value in kwargs.items():
                if hasattr(trade, key):
                    setattr(trade, key, value)
            await self.session.commit()
            await self.session.refresh(trade)
        return trade
    
    async def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        exit_reason: str
    ) -> Optional[TradeModel]:
        """Close a trade and calculate P/L."""
        trade = await self.get_by_id(trade_id)
        if not trade:
            return None
        
        trade.exit_price = exit_price
        trade.exit_time = exit_time
        trade.exit_reason = exit_reason
        
        # Calculate P/L using actual broker specs (tick_value when available)
        from ..config import get_symbol_spec, calculate_pl
        _spec = get_symbol_spec(trade.symbol)
        pip_value = _spec.pip_size
        
        if trade.direction == "long":
            trade.profit_loss_pips = (exit_price - trade.entry_price) / pip_value
        else:
            trade.profit_loss_pips = (trade.entry_price - exit_price) / pip_value
        
        # Use tick_value-based P/L when available (accurate for cross-currency pairs)
        if trade.direction == "long":
            trade.profit_loss = calculate_pl(trade.symbol, exit_price - trade.entry_price, trade.position_size)
        else:
            trade.profit_loss = calculate_pl(trade.symbol, trade.entry_price - exit_price, trade.position_size)
        
        # Calculate R multiple
        risk_pips = abs(trade.entry_price - trade.stop_loss) / pip_value
        if risk_pips > 0:
            trade.r_multiple = trade.profit_loss_pips / risk_pips
        
        await self.session.commit()
        await self.session.refresh(trade)
        return trade


class AnalysisLogRepository:
    """Repository for analysis log data access."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, analysis_data: dict) -> AnalysisLogModel:
        """Create a new analysis log."""
        log = AnalysisLogModel(**analysis_data)
        self.session.add(log)
        await self.session.commit()
        await self.session.refresh(log)
        return log
    
    async def get_recent(
        self,
        limit: int = 50,
        symbol: Optional[str] = None
    ) -> List[AnalysisLogModel]:
        """Get recent analysis logs."""
        query = select(AnalysisLogModel).order_by(desc(AnalysisLogModel.timestamp))
        
        if symbol:
            query = query.where(AnalysisLogModel.symbol == symbol)
        
        query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())


class ConfigSnapshotRepository:
    """Repository for config snapshot data access."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def save_snapshot(
        self,
        config_type: str,
        config_data: dict,
        changed_by: str = "api",
        reason: str = ""
    ) -> ConfigSnapshotModel:
        """Save a configuration snapshot."""
        snapshot = ConfigSnapshotModel(
            config_type=config_type,
            config_data=config_data,
            changed_by=changed_by,
            change_reason=reason
        )
        self.session.add(snapshot)
        await self.session.commit()
        await self.session.refresh(snapshot)
        return snapshot
    
    async def get_latest(self, config_type: str) -> Optional[ConfigSnapshotModel]:
        """Get the latest config snapshot of a type."""
        result = await self.session.execute(
            select(ConfigSnapshotModel)
            .where(ConfigSnapshotModel.config_type == config_type)
            .order_by(desc(ConfigSnapshotModel.timestamp))
            .limit(1)
        )
        return result.scalar_one_or_none()


class PerformanceRepository:
    """Repository for performance snapshot data access."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def save_snapshot(self, snapshot_data: dict) -> PerformanceSnapshotModel:
        """Save a performance snapshot."""
        snapshot = PerformanceSnapshotModel(**snapshot_data)
        self.session.add(snapshot)
        await self.session.commit()
        await self.session.refresh(snapshot)
        return snapshot
    
    async def get_equity_curve(
        self,
        days: int = 90
    ) -> List[PerformanceSnapshotModel]:
        """Get equity curve data for the last N days."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        result = await self.session.execute(
            select(PerformanceSnapshotModel)
            .where(PerformanceSnapshotModel.date >= cutoff)
            .order_by(PerformanceSnapshotModel.date)
        )
        return list(result.scalars().all())


class PositionStateRepository:
    """Repository for position state persistence."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def save_position(self, position_data: dict) -> PositionStateModel:
        """Save or update a position state."""
        ticket = position_data.get('ticket')
        
        # Check if position already exists
        existing = await self.get_by_ticket(ticket)
        
        if existing:
            # Update existing
            for key, value in position_data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        else:
            # Create new
            position = PositionStateModel(**position_data)
            self.session.add(position)
            await self.session.commit()
            await self.session.refresh(position)
            return position
    
    async def get_by_ticket(self, ticket: int) -> Optional[PositionStateModel]:
        """Get position by ticket."""
        result = await self.session.execute(
            select(PositionStateModel).where(PositionStateModel.ticket == ticket)
        )
        return result.scalar_one_or_none()
    
    async def get_all_open(self) -> List[PositionStateModel]:
        """Get all open positions."""
        result = await self.session.execute(
            select(PositionStateModel)
            .where(PositionStateModel.status == "open")
            .order_by(PositionStateModel.open_time)
        )
        return list(result.scalars().all())
    
    async def delete_position(self, ticket: int):
        """Remove a position (when closed)."""
        position = await self.get_by_ticket(ticket)
        if position:
            await self.session.delete(position)
            await self.session.commit()
    
    async def clear_all(self):
        """Clear all position states."""
        await self.session.execute(
            select(PositionStateModel).delete()
        )
        await self.session.commit()
