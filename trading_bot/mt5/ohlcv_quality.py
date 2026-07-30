"""
OHLCV quality validation for live analysis and trading.

Rejects frames that are too short, stale, NaN-contaminated, OHLC-invalid,
or contain mid-session gaps that would corrupt ICT structure detection.
Weekend/session-close gaps and bounded metals rollover closures are exempted
without allowing ordinary daytime outages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

# Minimum bars required by timeframe (mirrors chart quality floors, raised
# slightly for analysis reliability).
_MIN_BARS = {
    "M1": 30,
    "M5": 30,
    "M15": 30,
    "M30": 24,
    "H1": 24,
    "H4": 20,
    "D1": 20,
}

_TF_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

# Gaps larger than this multiple of the median interval are treated as data
# corruption unless they look like a session/weekend break (>= 6h for LTF).
_GAP_CORRUPTION_MULT = 3.0
_SESSION_BREAK_HOURS = 6.0
_METALS_ROLLOVER_MAX_HOURS = 3.0
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass
class OhlcvQualityResult:
    valid: bool
    gate_id: str = ""
    reason: str = ""

    @classmethod
    def ok(cls) -> "OhlcvQualityResult":
        return cls(valid=True)

    @classmethod
    def fail(cls, gate_id: str, reason: str) -> "OhlcvQualityResult":
        return cls(valid=False, gate_id=gate_id, reason=reason)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_expected_metals_rollover_gap(
    *,
    symbol: str,
    previous_bar: pd.Timestamp,
    current_bar: pd.Timestamp,
) -> bool:
    """Return whether a bounded gap overlaps the metals daily rollover.

    MT5 timestamps are UTC. Converting to New York time keeps the 17:00–19:00
    rollover window correct across daylight-saving transitions.
    """
    from trading_bot.config import get_symbol_spec

    if get_symbol_spec(symbol).category != "metal":
        return False

    previous = _as_utc(previous_bar.to_pydatetime())
    current = _as_utc(current_bar.to_pydatetime())
    if current - previous > timedelta(hours=_METALS_ROLLOVER_MAX_HOURS):
        return False

    previous_ny = previous.astimezone(_NEW_YORK)
    current_ny = current.astimezone(_NEW_YORK)
    candidate_dates = {previous_ny.date(), current_ny.date()}
    for candidate_date in candidate_dates:
        rollover_start = datetime.combine(
            candidate_date, time(hour=17), tzinfo=_NEW_YORK
        )
        rollover_end = datetime.combine(
            candidate_date, time(hour=19), tzinfo=_NEW_YORK
        )
        if previous_ny < rollover_end and current_ny > rollover_start:
            return True
    return False


def validate_ohlcv(
    df: Optional[pd.DataFrame],
    *,
    symbol: str,
    timeframe: str,
    expected_count: int = 0,
    now: Optional[datetime] = None,
    require_closed_bar: bool = False,
) -> OhlcvQualityResult:
    """Validate an OHLCV DataFrame before analysis/Claude.

    Returns OhlcvQualityResult; callers should treat ``valid=False`` as a
    terminal ``market_data_quality`` decision.
    """
    tf = (timeframe or "M15").upper()
    if df is None or getattr(df, "empty", True):
        return OhlcvQualityResult.fail(
            "bar_count",
            f"{symbol} {tf}: no OHLCV data",
        )

    min_bars = _MIN_BARS.get(tf, 20)
    if expected_count and expected_count > 0:
        # Allow 70% of requested (MT5 sometimes returns slightly fewer)
        min_bars = max(min_bars, int(expected_count * 0.7))
    if len(df) < min_bars:
        return OhlcvQualityResult.fail(
            "bar_count",
            f"{symbol} {tf}: only {len(df)} bars (need >= {min_bars})",
        )

    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        return OhlcvQualityResult.fail(
            "ohlc",
            f"{symbol} {tf}: missing columns {sorted(missing)}",
        )

    subset = df[["open", "high", "low", "close"]]
    if subset.isna().any().any():
        return OhlcvQualityResult.fail(
            "nan",
            f"{symbol} {tf}: NaN values in OHLC",
        )

    bad = (df["high"] < df["low"]) | (df["high"] < df["open"]) | (df["high"] < df["close"]) | (
        df["low"] > df["open"]
    ) | (df["low"] > df["close"])
    if bool(bad.any()):
        return OhlcvQualityResult.fail(
            "ohlc",
            f"{symbol} {tf}: OHLC integrity failure on {int(bad.sum())} bar(s)",
        )

    if not isinstance(df.index, pd.DatetimeIndex):
        return OhlcvQualityResult.fail(
            "index",
            f"{symbol} {tf}: index is not DatetimeIndex",
        )

    if not df.index.is_monotonic_increasing:
        return OhlcvQualityResult.fail(
            "index",
            f"{symbol} {tf}: non-monotonic timestamps",
        )

    if df.index.has_duplicates:
        return OhlcvQualityResult.fail(
            "index",
            f"{symbol} {tf}: duplicate bar timestamps",
        )

    # Freshness: last bar must be within 2 candle periods of now
    tf_mins = _TF_MINUTES.get(tf, 15)
    now_utc = _as_utc(now or datetime.now(timezone.utc))
    last = df.index[-1]
    if getattr(last, "tzinfo", None) is None:
        last_dt = last.to_pydatetime().replace(tzinfo=timezone.utc)
    else:
        last_dt = _as_utc(last.to_pydatetime())
    age_mins = (now_utc - last_dt).total_seconds() / 60.0
    max_age = tf_mins * 2.5
    if age_mins > max_age:
        return OhlcvQualityResult.fail(
            "stale",
            f"{symbol} {tf}: last bar {age_mins:.0f}m old (max {max_age:.0f}m)",
        )

    if require_closed_bar and age_mins < 0:
        # Future bar — clock skew
        return OhlcvQualityResult.fail(
            "stale",
            f"{symbol} {tf}: last bar is in the future",
        )

    # Gap detection: flag mid-session corruption, exempt long session breaks
    if len(df) >= 3:
        deltas = df.index.to_series().diff().dropna()
        if len(deltas) > 0:
            median = deltas.median()
            if median is not None and median > pd.Timedelta(0):
                possible_corrupt = deltas[
                    (deltas > median * _GAP_CORRUPTION_MULT)
                    & (deltas < pd.Timedelta(hours=_SESSION_BREAK_HOURS))
                ]
                corrupt = possible_corrupt[
                    [
                        not _is_expected_metals_rollover_gap(
                            symbol=symbol,
                            previous_bar=df.index[df.index.get_loc(timestamp) - 1],
                            current_bar=timestamp,
                        )
                        for timestamp in possible_corrupt.index
                    ]
                ]
                if len(corrupt) > 0:
                    return OhlcvQualityResult.fail(
                        "gap",
                        f"{symbol} {tf}: {len(corrupt)} mid-session gap(s), "
                        f"largest={corrupt.max()}",
                    )

    return OhlcvQualityResult.ok()
