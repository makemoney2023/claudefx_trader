from __future__ import annotations

from ..config import settings


def is_claude_signal_trust_active() -> bool:
    mode = (getattr(settings.trading, "claude_signal_trust_mode", "off") or "off").lower()
    return mode == "active"


def should_apply_claude_signal_trust(direction: str) -> bool:
    if not is_claude_signal_trust_active():
        return False
    return (direction or "").strip().lower() in ("long", "short")


def should_ignore_judge_demote(*, direction: str) -> bool:
    return should_apply_claude_signal_trust(direction)


def should_soft_pass_playbook(*, direction: str) -> bool:
    """Playbook is expectancy strategy — soft-pass under wide Claude trust."""
    return should_apply_claude_signal_trust(direction)
