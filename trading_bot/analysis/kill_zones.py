"""
Kill Zone / Trading Session Module.

Implements ICT kill zone concepts:
- Asian session (ranging/accumulation)
- London session (initial manipulation/move)
- New York session (major moves)
- London close (reversals/profit taking)

Identifies optimal times to trade based on institutional activity.
"""

from dataclasses import dataclass
from enum import Enum
from datetime import datetime, time, timezone, timedelta
from typing import Optional, List, Tuple, Dict, Any
import pytz

from ..utils.logging import get_logger

logger = get_logger(__name__)


class TradingSession(Enum):
    """Major forex trading sessions."""
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "new_york"
    LONDON_CLOSE = "london_close"
    OFF_HOURS = "off_hours"


@dataclass
class SessionWindow:
    """
    Defines a trading session time window.
    
    All times are in EST (Eastern Standard Time) to match
    ICT methodology conventions.
    """
    name: str
    session: TradingSession
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    is_kill_zone: bool = False
    description: str = ""
    
    def contains_time(self, t: time) -> bool:
        """Check if a time falls within this session window."""
        start = time(self.start_hour, self.start_minute)
        end = time(self.end_hour, self.end_minute)
        
        if start <= end:
            return start <= t <= end
        else:
            # Handle overnight sessions (e.g., Asian)
            return t >= start or t <= end
    
    def get_duration_minutes(self) -> int:
        """Get session duration in minutes."""
        start_mins = self.start_hour * 60 + self.start_minute
        end_mins = self.end_hour * 60 + self.end_minute
        
        if end_mins >= start_mins:
            return end_mins - start_mins
        else:
            return (24 * 60 - start_mins) + end_mins


@dataclass
class SessionInfo:
    """Information about the current trading session."""
    current_session: TradingSession
    session_name: str
    is_kill_zone: bool
    is_tradeable: bool
    minutes_into_session: int
    minutes_remaining: int
    next_kill_zone: Optional[str] = None
    next_kill_zone_in_minutes: Optional[int] = None


# New York kill zone (America/New_York): 7:00–10:00. Claude ``ny_open`` window
# starts ``lead_minutes`` before the open (default 6:30) and ends at 10:00.
NY_KILL_ZONE_START = time(7, 0)
NY_KILL_ZONE_END = time(10, 0)
CLAUDE_NY_WINDOW_DEFAULT_LEAD_MINUTES = 30


def _as_new_york(dt: Optional[datetime] = None) -> datetime:
    zone = pytz.timezone("America/New_York")
    if dt is None:
        return datetime.now(zone)
    if dt.tzinfo is None:
        return zone.localize(dt)
    return dt.astimezone(zone)


def _clamp_lead_minutes(lead_minutes: int) -> int:
    try:
        lead = int(lead_minutes)
    except (TypeError, ValueError):
        lead = CLAUDE_NY_WINDOW_DEFAULT_LEAD_MINUTES
    return max(0, min(lead, 12 * 60))


def ny_claude_window_start_minutes(lead_minutes: int = CLAUDE_NY_WINDOW_DEFAULT_LEAD_MINUTES) -> int:
    """Minutes-from-midnight for the Claude NY warmup start."""
    start = NY_KILL_ZONE_START.hour * 60 + NY_KILL_ZONE_START.minute
    return start - _clamp_lead_minutes(lead_minutes)


def is_in_claude_ny_window(
    dt: Optional[datetime] = None,
    *,
    lead_minutes: int = CLAUDE_NY_WINDOW_DEFAULT_LEAD_MINUTES,
) -> bool:
    """True from (NY open − lead) through NY kill-zone end, inclusive."""
    now = _as_new_york(dt)
    current = now.hour * 60 + now.minute
    start = ny_claude_window_start_minutes(lead_minutes)
    end = NY_KILL_ZONE_END.hour * 60 + NY_KILL_ZONE_END.minute
    return start <= current <= end


def minutes_until_claude_ny_window(
    dt: Optional[datetime] = None,
    *,
    lead_minutes: int = CLAUDE_NY_WINDOW_DEFAULT_LEAD_MINUTES,
) -> int:
    """Minutes until the next NY Claude window; 0 if already inside."""
    if is_in_claude_ny_window(dt, lead_minutes=lead_minutes):
        return 0
    now = _as_new_york(dt)
    current = now.hour * 60 + now.minute
    start = ny_claude_window_start_minutes(lead_minutes)
    if current < start:
        return start - current
    return (24 * 60 - current) + start


def format_claude_ny_window_label(
    lead_minutes: int = CLAUDE_NY_WINDOW_DEFAULT_LEAD_MINUTES,
) -> str:
    start = ny_claude_window_start_minutes(lead_minutes)
    return (
        f"NY Claude window "
        f"({start // 60:02d}:{start % 60:02d}–"
        f"{NY_KILL_ZONE_END.hour:02d}:{NY_KILL_ZONE_END.minute:02d} ET)"
    )


def claude_analysis_allowed(
    is_tradeable: bool,
    *,
    claude_kill_zone_only: bool = True,
    displacement_override: bool = False,
    lean_active: bool = False,
    analysis_window: str = "all_kill_zones",
    in_ny_window: bool = False,
) -> bool:
    """
    Whether Claude analysis (and judge/sizing) may run for this session.

    ``analysis_window="ny_open"`` is the spend-control mode: Claude runs only
    inside the NY window (open minus lead through NY kill-zone end). Lean and
    metals displacement overrides do **not** punch through that window.

    When ``analysis_window`` is ``all_kill_zones`` (legacy) and
    ``claude_kill_zone_only`` is True, analysis is restricted to ICT kill
    zones where ``is_tradeable`` is True (London 2–5, NY 7–10, London Close
    10–12 America/New_York) — unless ``displacement_override`` or
    ``lean_active`` is True.
    """
    window = (analysis_window or "all_kill_zones").strip().lower()
    if window in ("ny_open", "ny", "new_york"):
        return bool(in_ny_window)
    if lean_active:
        return True
    if displacement_override:
        return True
    if not claude_kill_zone_only:
        return True
    return bool(is_tradeable)


def resolve_outside_kz_cycle_symbols(
    cycle_symbols: List[str],
    *,
    crypto_symbols: frozenset,
    is_tradeable: bool,
    disp_override_symbols: Optional[List[str]] = None,
    lean_active: bool = False,
) -> Tuple[List[str], bool]:
    """
    Resolve which symbols the main cycle may analyze outside a kill zone.

    Returns ``(symbols, off_hours_mode)``. Empty symbols means skip the cycle.

    When lean is active, keep the full symbol set and clear off-hours mode so
    metals/forex are not crypto-filtered and ``off_hours_cap`` does not hard-block
    lean fades. Displacement-override lists already narrowed the cycle upstream.
    """
    symbols = list(cycle_symbols or [])
    overrides = list(disp_override_symbols or [])
    if is_tradeable or overrides:
        return symbols, False
    if lean_active:
        return symbols, False
    crypto_in_cycle = [s for s in symbols if s in crypto_symbols]
    return crypto_in_cycle, True


# ICT Kill Zones (EST times)
# Full 24-hour coverage so there's no unnamed gap
KILL_ZONES = [
    # Asian pre-session: 12 AM - 2 AM EST
    SessionWindow(
        name="Asian (Pre-London)",
        session=TradingSession.ASIAN,
        start_hour=0,   # 12 AM EST
        start_minute=0,
        end_hour=2,     # 2 AM EST
        end_minute=0,
        is_kill_zone=False,
        description="Late Asian session, low volatility. Liquidity pools forming before London."
    ),
    
    # London Kill Zone - major moves begin: 2 AM - 5 AM EST
    SessionWindow(
        name="London Kill Zone",
        session=TradingSession.LONDON,
        start_hour=2,   # 2 AM EST
        start_minute=0,
        end_hour=5,     # 5 AM EST
        end_minute=0,
        is_kill_zone=True,
        description="Primary kill zone. Look for manipulation of Asian highs/lows, then real move."
    ),
    
    # London continuation: 5 AM - 7 AM EST
    SessionWindow(
        name="London Continuation",
        session=TradingSession.LONDON,
        start_hour=5,   # 5 AM EST
        start_minute=0,
        end_hour=7,     # 7 AM EST
        end_minute=0,
        is_kill_zone=False,
        description="London continuation. Trends may extend before NY opens."
    ),
    
    # New York Kill Zone - most liquid period: 7 AM - 10 AM EST
    SessionWindow(
        name="New York Kill Zone",
        session=TradingSession.NEW_YORK,
        start_hour=7,   # 7 AM EST
        start_minute=0,
        end_hour=10,    # 10 AM EST
        end_minute=0,
        is_kill_zone=True,
        description="Highest volume period. Major moves, often continues London direction or reverses."
    ),
    
    # London Close Kill Zone - profit taking, reversals: 10 AM - 12 PM EST
    SessionWindow(
        name="London Close Kill Zone",
        session=TradingSession.LONDON_CLOSE,
        start_hour=10,  # 10 AM EST
        start_minute=0,
        end_hour=12,    # 12 PM EST
        end_minute=0,
        is_kill_zone=True,
        description="Profit taking period. Watch for reversals of the day's move."
    ),
    
    # New York Afternoon: 12 PM - 5 PM EST
    SessionWindow(
        name="NY Afternoon",
        session=TradingSession.NEW_YORK,
        start_hour=12,  # 12 PM EST
        start_minute=0,
        end_hour=17,    # 5 PM EST
        end_minute=0,
        is_kill_zone=False,
        description="NY afternoon session. Lower volume, watch for late-day reversals."
    ),
    
    # Market transition: 5 PM - 7 PM EST (Sunday open / daily rollover)
    SessionWindow(
        name="Market Open / Rollover",
        session=TradingSession.OFF_HOURS,
        start_hour=17,  # 5 PM EST
        start_minute=0,
        end_hour=19,    # 7 PM EST
        end_minute=0,
        is_kill_zone=False,
        description="Daily rollover period. New trading day starts. Sunday market open."
    ),
    
    # Asian Session - typically ranging, accumulation: 7 PM - 12 AM EST
    SessionWindow(
        name="Asian Session",
        session=TradingSession.ASIAN,
        start_hour=19,  # 7 PM EST (previous day)
        start_minute=0,
        end_hour=0,     # 12 AM EST
        end_minute=0,
        is_kill_zone=False,
        description="Accumulation phase, often ranging. Look for liquidity pools forming."
    ),
]


class KillZoneChecker:
    """
    Checks trading session timing and kill zone validity.
    
    Uses ICT concepts to identify optimal trading times
    based on institutional activity patterns.
    """
    
    def __init__(
        self,
        timezone_str: str = "America/New_York",
        allowed_sessions: Optional[List[str]] = None
    ):
        """
        Initialize the kill zone checker.
        
        Args:
            timezone_str: Timezone for session calculations
            allowed_sessions: List of allowed session names for trading
        """
        self.timezone = pytz.timezone(timezone_str)
        self.allowed_sessions = allowed_sessions or ["london", "new_york"]
        self.kill_zones = KILL_ZONES
    
    def get_current_session(self, dt: Optional[datetime] = None) -> SessionInfo:
        """
        Get information about the current trading session.
        
        Args:
            dt: Optional datetime (uses now if not provided)
            
        Returns:
            SessionInfo with current session details
        """
        # Check if "all" sessions are allowed - if so, always tradeable
        allow_all = any(str(s).lower() == "all" for s in self.allowed_sessions)
        
        if dt is None:
            dt = datetime.now(self.timezone)
        elif dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)
        
        current_time = dt.time()
        
        # Find current session
        current_window = None
        for window in self.kill_zones:
            if window.contains_time(current_time):
                current_window = window
                break
        
        if current_window is None:
            return SessionInfo(
                current_session=TradingSession.OFF_HOURS,
                session_name="Off Hours",
                is_kill_zone=False,
                is_tradeable=allow_all,  # Allow trading if "all" is enabled
                minutes_into_session=0,
                minutes_remaining=0,
                next_kill_zone=self._find_next_kill_zone(current_time),
                next_kill_zone_in_minutes=self._minutes_to_next_kill_zone(current_time)
            )
        
        # Calculate minutes into session
        session_start = time(current_window.start_hour, current_window.start_minute)
        current_mins = current_time.hour * 60 + current_time.minute
        start_mins = session_start.hour * 60 + session_start.minute
        
        if current_mins >= start_mins:
            minutes_in = current_mins - start_mins
        else:
            minutes_in = (24 * 60 - start_mins) + current_mins
        
        minutes_remaining = current_window.get_duration_minutes() - minutes_in
        
        # Check if tradeable
        # If "all" is in allowed_sessions, always allow trading
        if allow_all:
            is_tradeable = True
            logger.debug("All sessions enabled - trading allowed during all sessions")
        else:
            # Only trade during kill zones in allowed sessions
            is_tradeable = (
                current_window.is_kill_zone and
                current_window.session.value in self.allowed_sessions
            )
        
        return SessionInfo(
            current_session=current_window.session,
            session_name=current_window.name,
            is_kill_zone=current_window.is_kill_zone,
            is_tradeable=is_tradeable,
            minutes_into_session=minutes_in,
            minutes_remaining=max(0, minutes_remaining),
            next_kill_zone=self._find_next_kill_zone(current_time) if not current_window.is_kill_zone else None,
            next_kill_zone_in_minutes=self._minutes_to_next_kill_zone(current_time) if not current_window.is_kill_zone else None
        )
    
    def get_kill_zone_timing_multiplier(self, dt: Optional[datetime] = None) -> float:
        """
        Get a confidence multiplier based on micro-timing within kill zones.
        
        ICT methodology: First 30-60 minutes of London and NY kill zones
        have the best entries (manipulation phase). The last 30 minutes
        of a kill zone often have exhausted moves.
        
        Returns:
            Multiplier: 1.2 (first 30 min), 1.1 (30-60 min), 1.0 (middle),
                        0.8 (last 30 min of KZ)
        """
        session_info = self.get_current_session(dt)
        
        if not session_info.is_kill_zone:
            return 0.9  # Outside kill zone: slight penalty
        
        minutes_in = session_info.minutes_into_session
        minutes_remaining = session_info.minutes_remaining
        total_duration = minutes_in + minutes_remaining
        
        # First 30 minutes: manipulation phase (best entries)
        if minutes_in <= 30:
            return 1.2
        
        # 30-60 minutes: early distribution (still good)
        if minutes_in <= 60:
            return 1.1
        
        # Last 30 minutes: move likely exhausted
        if minutes_remaining <= 30:
            return 0.8
        
        # Middle of session: normal
        return 1.0
    
    def get_session_quality_info(self, dt: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get detailed session quality information for Claude context.
        
        Returns timing quality, phase name, and recommendation.
        """
        session_info = self.get_current_session(dt)
        multiplier = self.get_kill_zone_timing_multiplier(dt)
        
        minutes_in = session_info.minutes_into_session
        
        # Determine phase
        if not session_info.is_kill_zone:
            phase = "off_hours"
            recommendation = "No entries recommended outside kill zones"
        elif minutes_in <= 30:
            phase = "manipulation"
            recommendation = "Best entry window - look for liquidity sweeps and manipulation"
        elif minutes_in <= 60:
            phase = "distribution_early"
            recommendation = "Good entry window - look for continuation after manipulation"
        elif session_info.minutes_remaining <= 30:
            phase = "exhaustion"
            recommendation = "Move likely exhausted - avoid new entries, manage existing"
        else:
            phase = "distribution"
            recommendation = "Normal trading window"
        
        return {
            'session_name': session_info.session_name,
            'is_kill_zone': session_info.is_kill_zone,
            'phase': phase,
            'minutes_into_session': minutes_in,
            'minutes_remaining': session_info.minutes_remaining,
            'timing_multiplier': multiplier,
            'recommendation': recommendation
        }
    
    def is_valid_session(self, dt: Optional[datetime] = None) -> bool:
        """
        Check if current time is in a valid trading session.
        
        Args:
            dt: Optional datetime to check
            
        Returns:
            True if in a valid/allowed trading session
        """
        session_info = self.get_current_session(dt)
        return session_info.is_tradeable
    
    def is_kill_zone(self, dt: Optional[datetime] = None) -> bool:
        """
        Check if current time is in any kill zone.
        
        Args:
            dt: Optional datetime to check
            
        Returns:
            True if in a kill zone
        """
        session_info = self.get_current_session(dt)
        return session_info.is_kill_zone
    
    def get_session_bias(self, session: TradingSession) -> str:
        """
        Get the typical bias/behavior for a session.
        
        Args:
            session: Trading session
            
        Returns:
            Description of typical session behavior
        """
        biases = {
            TradingSession.ASIAN: "Ranging/accumulation. Watch for liquidity building at highs/lows.",
            TradingSession.LONDON: "Initial manipulation then real move. Often sweeps Asian session levels.",
            TradingSession.NEW_YORK: "Highest volume. Continuation or reversal of London move.",
            TradingSession.LONDON_CLOSE: "Profit taking. Potential reversals and retracements.",
            TradingSession.OFF_HOURS: "Low liquidity. Avoid trading."
        }
        return biases.get(session, "Unknown session")
    
    def _find_next_kill_zone(self, current_time: time) -> Optional[str]:
        """Find the name of the next kill zone."""
        current_mins = current_time.hour * 60 + current_time.minute
        
        kill_zone_windows = [w for w in self.kill_zones if w.is_kill_zone]
        
        for window in kill_zone_windows:
            start_mins = window.start_hour * 60 + window.start_minute
            if start_mins > current_mins:
                return window.name
        
        # Wrap around to first kill zone of next day
        if kill_zone_windows:
            return kill_zone_windows[0].name
        return None
    
    def _minutes_to_next_kill_zone(self, current_time: time) -> Optional[int]:
        """Calculate minutes until next kill zone."""
        current_mins = current_time.hour * 60 + current_time.minute
        
        kill_zone_windows = [w for w in self.kill_zones if w.is_kill_zone]
        
        for window in kill_zone_windows:
            start_mins = window.start_hour * 60 + window.start_minute
            if start_mins > current_mins:
                return start_mins - current_mins
        
        # Wrap around to first kill zone of next day
        if kill_zone_windows:
            first_kz_start = kill_zone_windows[0].start_hour * 60 + kill_zone_windows[0].start_minute
            return (24 * 60 - current_mins) + first_kz_start
        
        return None
    
    def get_daily_schedule(self) -> List[dict]:
        """
        Get the full daily kill zone schedule.
        
        Returns:
            List of session windows with times and descriptions
        """
        schedule = []
        for window in self.kill_zones:
            schedule.append({
                "name": window.name,
                "session": window.session.value,
                "start": f"{window.start_hour:02d}:{window.start_minute:02d}",
                "end": f"{window.end_hour:02d}:{window.end_minute:02d}",
                "is_kill_zone": window.is_kill_zone,
                "description": window.description
            })
        return schedule
