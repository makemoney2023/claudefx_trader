"""
Allowlisted Telegram settings — no raw .env edits, no secrets.

Telegram can toggle/set only the specs below. Values are validated,
written onto the live `settings` object, then persisted via
`save_config_to_env_local`. `reload_settings()` is never called.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from ..config import save_config_to_env_local, settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

Kind = Literal["mode", "bool", "float", "int", "list"]
ApplyKind = Literal["live", "needs_apply"]
Owner = Literal["trading", "root"]

PENDING_TTL_SECONDS = 60

# Same BTC/BIT-quoted names the bot refuses to trade.
DEFAULT_BLOCKED_PAIRS = (
    "ETHBTC",
    "XRPBIT",
    "LTCBTC",
    "XMRBTC",
    "ZECBTC",
    "DASHBTC",
    "EOSBIT",
    "IOTABIT",
    "ETHBIT",
    "ADABTC",
    "SOLBTC",
    "DOTBTC",
)

_TRUE = frozenset({"on", "true", "1", "yes", "enable", "enabled"})
_FALSE = frozenset({"off", "false", "0", "no", "disable", "disabled"})


class SettingError(ValueError):
    """User-facing validation error for a Telegram setting change."""


@dataclass(frozen=True)
class SettingSpec:
    name: str
    env_key: str
    attr: str
    kind: Kind
    persist_key: str
    persist_prefix: str = "TRADING_"
    allowed: Optional[Tuple[str, ...]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    apply: ApplyKind = "live"
    owner: Owner = "trading"
    dangerous_values: Optional[Tuple[Any, ...]] = None
    dangerous_gt: Optional[float] = None
    description: str = ""
    aliases: Tuple[str, ...] = ()


@dataclass
class ApplyResult:
    name: str
    old: Any
    new: Any
    needs_apply: bool
    persisted: bool = True


@dataclass
class PendingChange:
    code: str
    kind: str  # "setting" | "mode" | "symbol"
    name: str
    value: Any
    expires: float
    extra: Dict[str, Any] = field(default_factory=dict)


FLAG_SPECS: Tuple[SettingSpec, ...] = (
    SettingSpec(
        name="trust",
        env_key="TRADING_CLAUDE_SIGNAL_TRUST_MODE",
        attr="claude_signal_trust_mode",
        kind="mode",
        persist_key="claude_signal_trust_mode",
        allowed=("off", "active"),
        dangerous_values=("active",),
        description="Claude signal trust (off/active)",
    ),
    SettingSpec(
        name="ict",
        env_key="TRADING_ICT_CONFIRMATION_MODE",
        attr="ict_confirmation_mode",
        kind="mode",
        persist_key="ict_confirmation_mode",
        allowed=("disabled", "shadow", "active"),
        description="ICT confirmation gate",
    ),
    SettingSpec(
        name="lean",
        env_key="TRADING_LIQUIDITY_REVERSAL_LEAN_MODE",
        attr="liquidity_reversal_lean_mode",
        kind="mode",
        persist_key="liquidity_reversal_lean_mode",
        allowed=("off", "active"),
        description="Liquidity-reversal lean",
    ),
    SettingSpec(
        name="window",
        env_key="TRADING_CLAUDE_ANALYSIS_WINDOW",
        attr="claude_analysis_window",
        kind="mode",
        persist_key="claude_analysis_window",
        allowed=("ny_open", "all_kill_zones"),
        description="Claude analysis window",
    ),
    SettingSpec(
        name="news",
        env_key="TRADING_NEWS_GATES_ENABLED",
        attr="news_gates_enabled",
        kind="bool",
        persist_key="news_gates_enabled",
        description="News blackout / calendar gates",
    ),
    SettingSpec(
        name="pyramid",
        env_key="TRADING_PYRAMID_ENABLED",
        attr="pyramid_enabled",
        kind="bool",
        persist_key="pyramid_enabled",
        description="Confirmation pyramid adds",
    ),
    SettingSpec(
        name="scanner",
        env_key="TRADING_OPPORTUNITY_SCANNER_ENABLED",
        attr="opportunity_scanner_enabled",
        kind="bool",
        persist_key="opportunity_scanner_enabled",
        apply="needs_apply",
        description="Opportunity scanner (needs /apply)",
    ),
    SettingSpec(
        name="dryrun",
        env_key="TRADING_DRY_RUN",
        attr="dry_run",
        kind="bool",
        persist_key="dry_run",
        dangerous_values=(False,),
        description="Analyze but skip order execution",
    ),
    SettingSpec(
        name="demo",
        env_key="TRADING_DEMO_DATA_COLLECTION_MODE",
        attr="demo_data_collection_mode",
        kind="bool",
        persist_key="demo_data_collection_mode",
        dangerous_values=(True,),
        description="Demo data-collection (forces AGGRESSIVE)",
    ),
    SettingSpec(
        name="strictkz",
        env_key="STRICT_ICT_SESSIONS",
        attr="strict_ict_sessions",
        kind="bool",
        persist_key="strict_ict_sessions",
        persist_prefix="",
        owner="root",
        apply="needs_apply",
        description="Lock sessions to ICT kill zones (needs /apply)",
    ),
)

VALUE_SPECS: Tuple[SettingSpec, ...] = (
    SettingSpec(
        name="risk",
        env_key="TRADING_RISK_PER_TRADE",
        attr="risk_per_trade",
        kind="float",
        persist_key="risk_per_trade",
        min_value=0.0025,
        max_value=0.02,
        dangerous_gt=0.015,
        description="Risk per trade (0.25%–2%)",
    ),
    SettingSpec(
        name="maxlot",
        env_key="TRADING_MAX_POSITION_SIZE",
        attr="max_position_size",
        kind="float",
        persist_key="max_position_size",
        min_value=0.01,
        max_value=1.0,
        dangerous_gt=0.20,
        description="Max lots per trade",
    ),
    SettingSpec(
        name="exposure",
        env_key="TRADING_MAX_TOTAL_EXPOSURE",
        attr="max_total_exposure",
        kind="float",
        persist_key="max_total_exposure",
        min_value=0.01,
        max_value=5.0,
        dangerous_gt=3.0,
        description="Max total exposure (lots)",
    ),
    SettingSpec(
        name="trades",
        env_key="TRADING_MAX_DAILY_TRADES",
        attr="max_daily_trades",
        kind="int",
        persist_key="max_daily_trades",
        min_value=1,
        max_value=15,
        dangerous_gt=8,
        description="Max daily trades",
    ),
    SettingSpec(
        name="rr",
        env_key="TRADING_MIN_RISK_REWARD",
        attr="min_risk_reward",
        kind="float",
        persist_key="min_risk_reward",
        min_value=1.0,
        max_value=4.0,
        description="Minimum R:R",
    ),
    SettingSpec(
        name="conf",
        env_key="TRADING_GATE_MIN_CONFIDENCE",
        attr="gate_min_confidence",
        kind="float",
        persist_key="gate_min_confidence",
        min_value=0.60,
        max_value=0.90,
        description="Minimum Claude confidence",
    ),
    SettingSpec(
        name="lead",
        env_key="TRADING_CLAUDE_NY_LEAD_MINUTES",
        attr="claude_ny_lead_minutes",
        kind="int",
        persist_key="claude_ny_lead_minutes",
        min_value=0,
        max_value=60,
        description="Minutes before NY open for Claude",
    ),
    SettingSpec(
        name="hot",
        env_key="TRADING_OPPORTUNITY_SCANNER_HOT_LIST_SIZE",
        attr="opportunity_scanner_hot_list_size",
        kind="int",
        persist_key="opportunity_scanner_hot_list_size",
        min_value=1,
        max_value=8,
        description="Scanner hot-list size",
    ),
)

MODE_LOCK_SPEC = SettingSpec(
    name="modelock",
    env_key="TRADING_TELEGRAM_MODE_LOCK",
    attr="telegram_mode_lock",
    kind="mode",
    persist_key="telegram_mode_lock",
    allowed=("conservative", "normal", "aggressive", ""),
    description="Telegram scaling-mode lock (empty = auto)",
)

_ALL_SPECS: Tuple[SettingSpec, ...] = FLAG_SPECS + VALUE_SPECS + (MODE_LOCK_SPEC,)


def list_flag_specs() -> List[SettingSpec]:
    return list(FLAG_SPECS)


def list_value_specs() -> List[SettingSpec]:
    return list(VALUE_SPECS)


def get_spec(name: str) -> SettingSpec:
    key = (name or "").strip().lower().lstrip("/")
    for spec in _ALL_SPECS:
        aliases = (spec.name,) + spec.aliases
        if key in aliases:
            return spec
    raise SettingError(f"Unknown setting: {name}")


def _owner_obj(spec: SettingSpec):
    if spec.owner == "root":
        return settings
    return settings.trading


def get_current(spec: SettingSpec) -> Any:
    raw = getattr(_owner_obj(spec), spec.attr)
    if spec.kind == "bool":
        return bool(raw)
    if spec.kind == "mode":
        return "" if raw is None else str(raw).strip().lower()
    if spec.kind == "int":
        return int(raw)
    if spec.kind == "float":
        return float(raw)
    if spec.kind == "list":
        return list(raw or [])
    return raw


def format_value(spec: SettingSpec, value: Any = None) -> str:
    if value is None:
        value = get_current(spec)
    if spec.kind == "bool":
        return "on" if bool(value) else "off"
    if spec.name == "risk":
        return f"{float(value):.2%}"
    if spec.kind == "float":
        num = float(value)
        if num == int(num):
            return str(int(num))
        return f"{num:g}"
    if spec.kind == "list":
        return ", ".join(str(v) for v in value) if value else "(empty)"
    if spec.kind == "mode" and value == "":
        return "auto"
    return str(value)


def parse_bool(raw: Optional[str], current: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return not current
    token = str(raw).strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise SettingError(f"Expected on/off, got {raw}")


def parse_mode(raw: Optional[str], allowed: Sequence[str]) -> str:
    if raw is None or str(raw).strip() == "":
        raise SettingError(f"Expected one of: {', '.join(allowed)}")
    token = str(raw).strip().lower()
    if token not in allowed:
        raise SettingError(f"Expected one of: {', '.join(allowed)}")
    return token


def parse_percent_or_decimal(raw: str) -> float:
    token = str(raw).strip().replace(" ", "").replace(",", "")
    if not token:
        raise SettingError("Missing number")
    if token.endswith("%"):
        return float(token[:-1]) / 100.0
    return float(token)


def parse_number(raw: str, kind: Kind) -> float:
    try:
        value = parse_percent_or_decimal(raw) if kind == "float" else float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise SettingError(f"Not a number: {raw}") from exc
    if kind == "int":
        if abs(value - round(value)) > 1e-9:
            raise SettingError(f"Expected a whole number, got {raw}")
        return int(round(value))
    return float(value)


def clamp_number(spec: SettingSpec, value: float) -> Any:
    lo = spec.min_value
    hi = spec.max_value
    clamped = float(value)
    if lo is not None:
        clamped = max(lo, clamped)
    if hi is not None:
        clamped = min(hi, clamped)
    if spec.kind == "int":
        return int(round(clamped))
    return clamped


def parse_toggle(spec: SettingSpec, raw: Optional[str] = None) -> Any:
    current = get_current(spec)
    if spec.kind == "bool":
        return parse_bool(raw, current)
    if spec.kind == "mode":
        return parse_mode(raw, spec.allowed or ())
    raise SettingError(f"{spec.name} is not a toggle")


def parse_set(spec: SettingSpec, raw: Optional[str]) -> Any:
    if raw is None or str(raw).strip() == "":
        raise SettingError(f"Usage: /set {spec.name} <value>")
    if spec.kind in ("float", "int"):
        parsed = parse_number(raw, spec.kind)
        if spec.min_value is not None and parsed < spec.min_value:
            raise SettingError(
                f"{spec.name} {format_value(spec, parsed)} is below min "
                f"{format_value(spec, spec.min_value)}"
            )
        if spec.max_value is not None and parsed > spec.max_value:
            raise SettingError(
                f"{spec.name} {format_value(spec, parsed)} is above max "
                f"{format_value(spec, spec.max_value)}"
            )
        return clamp_number(spec, parsed)
    if spec.kind == "mode":
        return parse_mode(raw, spec.allowed or ())
    if spec.kind == "bool":
        return parse_bool(raw, get_current(spec))
    raise SettingError(f"{spec.name} cannot be set this way")


def is_dangerous_change(spec: SettingSpec, value: Any) -> bool:
    if spec.dangerous_values is not None and value in spec.dangerous_values:
        return True
    if spec.dangerous_gt is not None:
        try:
            return float(value) > spec.dangerous_gt
        except (TypeError, ValueError):
            return False
    return False


def is_dangerous_mode(mode: str) -> bool:
    return str(mode or "").strip().lower() == "aggressive"


def normalize_mode_lock(lock: Optional[str]) -> str:
    token = (lock or "").strip().lower()
    if token in ("", "auto"):
        return ""
    if token in ("conservative", "normal", "aggressive"):
        return token
    return ""


def resolve_telegram_locked_mode(
    lock: Optional[str],
    auto_mode: Any = None,
    data_collection: bool = False,
):
    """
    Mode forced by Telegram lock / demo / DEFENSIVE.

    Returns a TradingMode to apply, or None to keep the default cycle
    (Mon/Fri conservative + performance determine_mode).
    """
    from .scaling_manager import TradingMode

    # Demo/data-collection still forces AGGRESSIVE (same as the existing cycle).
    # DEFENSIVE still wins over a Telegram lock.
    if data_collection:
        return TradingMode.AGGRESSIVE
    if auto_mode == TradingMode.DEFENSIVE:
        return TradingMode.DEFENSIVE
    normalized = normalize_mode_lock(lock)
    if normalized:
        return TradingMode(normalized)
    return None


def normalize_symbol(raw: str) -> str:
    symbol = (raw or "").strip().upper().replace("/", "")
    if not symbol or not symbol.isalnum() or len(symbol) < 3 or len(symbol) > 16:
        raise SettingError(f"Invalid symbol: {raw}")
    return symbol


def is_blocked_symbol(
    symbol: str,
    extra_blocked: Optional[Iterable[str]] = None,
) -> bool:
    upper = (symbol or "").upper()
    if upper.endswith("BTC") or upper.endswith("BIT"):
        return True
    blocked = {b.upper() for b in (extra_blocked or DEFAULT_BLOCKED_PAIRS)}
    return upper in blocked


def validate_symbol(
    raw: str,
    extra_blocked: Optional[Iterable[str]] = None,
) -> str:
    symbol = normalize_symbol(raw)
    if is_blocked_symbol(symbol, extra_blocked):
        raise SettingError(f"Blocked symbol: {symbol}")
    return symbol


def apply_setting(
    spec: SettingSpec,
    value: Any,
    *,
    persist: bool = True,
    record_activity: bool = True,
) -> ApplyResult:
    """Mutate live settings and persist. Does not call reload_settings()."""
    old = get_current(spec)
    if spec.kind == "mode" and spec.allowed is not None and value not in spec.allowed:
        raise SettingError(f"Expected one of: {', '.join(spec.allowed)}")
    if spec.kind in ("float", "int"):
        value = clamp_number(spec, value)

    setattr(_owner_obj(spec), spec.attr, value)

    persisted = False
    if persist:
        try:
            save_config_to_env_local(
                {spec.persist_key: value},
                prefix=spec.persist_prefix,
            )
            persisted = True
        except Exception as exc:
            logger.warning(f"Failed to persist {spec.env_key}: {exc}")

    if record_activity:
        _record_config_activity(spec, old, value)

    return ApplyResult(
        name=spec.name,
        old=old,
        new=value,
        needs_apply=spec.apply == "needs_apply",
        persisted=persisted,
    )


def apply_bundle(
    pairs: Sequence[Tuple[SettingSpec, Any]],
    *,
    persist: bool = True,
    record_activity: bool = True,
) -> List[ApplyResult]:
    results: List[ApplyResult] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    for spec, value in pairs:
        old = get_current(spec)
        if spec.kind in ("float", "int"):
            value = clamp_number(spec, value)
        setattr(_owner_obj(spec), spec.attr, value)
        grouped.setdefault(spec.persist_prefix, {})[spec.persist_key] = value
        if record_activity:
            _record_config_activity(spec, old, value)
        results.append(
            ApplyResult(
                name=spec.name,
                old=old,
                new=value,
                needs_apply=spec.apply == "needs_apply",
                persisted=False,
            )
        )
    if persist:
        for prefix, updates in grouped.items():
            try:
                save_config_to_env_local(updates, prefix=prefix)
                for result in results:
                    result.persisted = True
            except Exception as exc:
                logger.warning(f"Failed to persist bundle ({prefix}): {exc}")
    return results


def apply_symbols(
    symbols: Sequence[str],
    *,
    persist: bool = True,
    record_activity: bool = True,
) -> ApplyResult:
    old = list(settings.trading.symbols)
    new = [validate_symbol(s) for s in symbols]
    settings.trading.symbols = new
    persisted = False
    if persist:
        try:
            save_config_to_env_local({"symbols": new}, prefix="TRADING_")
            persisted = True
        except Exception as exc:
            logger.warning(f"Failed to persist TRADING_SYMBOLS: {exc}")
    if record_activity:
        _record_config_activity_raw(
            "symbols",
            f"Symbols updated: {', '.join(old) or '(empty)'} → {', '.join(new) or '(empty)'}",
            {"old": old, "new": new},
        )
    return ApplyResult(
        name="symbols",
        old=old,
        new=new,
        needs_apply=True,
        persisted=persisted,
    )


def make_confirm_code() -> str:
    return f"{random.randint(10, 99):02d}"


def pending_expires_at(now: Optional[float] = None) -> float:
    return (now if now is not None else time.monotonic()) + PENDING_TTL_SECONDS


def is_pending_expired(expires: float, now: Optional[float] = None) -> bool:
    return (now if now is not None else time.monotonic()) >= expires


def new_pending(
    kind: str,
    name: str,
    value: Any,
    extra: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> PendingChange:
    return PendingChange(
        code=make_confirm_code(),
        kind=kind,
        name=name,
        value=value,
        expires=pending_expires_at(now),
        extra=extra or {},
    )


def _record_config_activity(spec: SettingSpec, old: Any, new: Any) -> None:
    _record_config_activity_raw(
        spec.name,
        f"{spec.name}: {format_value(spec, old)} → {format_value(spec, new)}",
        {"name": spec.name, "env_key": spec.env_key, "old": old, "new": new},
    )


def _record_config_activity_raw(name: str, message: str, details: dict) -> None:
    try:
        from ..api.routes.activity import add_activity

        add_activity("config_change", message, details=details)
    except Exception as exc:
        logger.debug(f"config_change activity skipped for {name}: {exc}")


async def try_save_config_snapshot(
    config_type: str,
    config_data: dict,
    reason: str,
) -> None:
    """Best-effort DB snapshot. Fail-open — Telegram must not depend on DB."""
    try:
        from ..api.database import ConfigSnapshotRepository, async_session

        async with async_session() as session:
            repo = ConfigSnapshotRepository(session)
            await repo.save_snapshot(
                config_type=config_type,
                config_data=json.loads(json.dumps(config_data, default=str)),
                changed_by="telegram",
                reason=reason,
            )
    except Exception as exc:
        logger.debug(f"Config snapshot skipped: {exc}")
