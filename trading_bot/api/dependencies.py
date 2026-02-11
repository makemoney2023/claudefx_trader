"""
FastAPI dependencies for dependency injection.
"""

from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .database import (
    get_session,
    TradeRepository,
    AnalysisLogRepository,
    ConfigSnapshotRepository,
    PerformanceRepository
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting a database session."""
    async for session in get_session():
        yield session


async def get_trade_repository(
    session: AsyncSession = Depends(get_db_session)
) -> TradeRepository:
    """Dependency for getting a trade repository."""
    return TradeRepository(session)


async def get_analysis_repository(
    session: AsyncSession = Depends(get_db_session)
) -> AnalysisLogRepository:
    """Dependency for getting an analysis log repository."""
    return AnalysisLogRepository(session)


async def get_config_repository(
    session: AsyncSession = Depends(get_db_session)
) -> ConfigSnapshotRepository:
    """Dependency for getting a config snapshot repository."""
    return ConfigSnapshotRepository(session)


async def get_performance_repository(
    session: AsyncSession = Depends(get_db_session)
) -> PerformanceRepository:
    """Dependency for getting a performance repository."""
    return PerformanceRepository(session)
