"""Shared datetime helpers for safe naive/aware arithmetic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def as_utc(dt: datetime) -> datetime:
    """Normalize naive/aware datetimes to UTC-aware for safe arithmetic.

    Naive values are treated as UTC (MT5 history / Windows local-naive
    persistence convention in this codebase).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp and return UTC-aware, or None on failure."""
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError, AttributeError):
        return None
