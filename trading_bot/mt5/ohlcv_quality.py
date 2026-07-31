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


def _recurring_metals_maintenance_gaps(
    *, symbol: str, gaps: pd.Series
) -> set[pd.Timestamp]:
    """Identify a broker's repeated daily metals closure from bar history.

    Two matching dates are enough: a typical M15 fetch (~200 bars) only spans
    ~2 trading sessions, so a 3-date rule permanently blocked LHFX XAUUSD when
    the window held exactly two 1h15 daily closures (2026-07-31).
    """
    from trading_bot.config import get_symbol_spec

    if get_symbol_spec(symbol).category != "metal" or len(gaps) < 2:
        return set()

    max_gap = pd.Timedelta(hours=_METALS_ROLLOVER_MAX_HOURS)
    duration_tolerance = pd.Timedelta(minutes=30)
    time_tolerance_minutes = 30
    min_matching_dates = 2
    recognized: set[pd.Timestamp] = set()

    def _utc_timestamp(value: pd.Timestamp) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC")

    def _minute_of_day(value: pd.Timestamp) -> int:
        utc_value = _utc_timestamp(value)
        return utc_value.hour * 60 + utc_value.minute

    for timestamp, duration in gaps.items():
        if duration > max_gap:
            continue

        target_minute = _minute_of_day(timestamp)
        matching_dates = set()
        matching_timestamps = []
        for other_timestamp, other_duration in gaps.items():
            if other_duration > max_gap or abs(other_duration - duration) > duration_tolerance:
                continue

            other_minute = _minute_of_day(other_timestamp)
            minute_difference = abs(other_minute - target_minute)
            circular_difference = min(minute_difference, 1440 - minute_difference)
            if circular_difference > time_tolerance_minutes:
                continue

            other_utc = _utc_timestamp(other_timestamp)
            matching_dates.add(other_utc.date())
            matching_timestamps.append(other_timestamp)

        if len(matching_dates) >= min_matching_dates:
            date_span = max(matching_dates) - min(matching_dates)
            if date_span <= timedelta(days=4):
                recognized.update(matching_timestamps)

    return recognized


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
                recurring_maintenance = _recurring_metals_maintenance_gaps(
                    symbol=symbol, gaps=possible_corrupt
                )
                corrupt = possible_corrupt[
                    [
                        (
                            timestamp not in recurring_maintenance
                            and not _is_expected_metals_rollover_gap(
                                symbol=symbol,
                                previous_bar=df.index[df.index.get_loc(timestamp) - 1],
                                current_bar=timestamp,
                            )
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
