"""
Shared exit policy matching live PositionManager ladder semantics.

Used by replay execution_policy and bar-walk simulations so TP1/TP2/giveback
fractions align with production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from ..services.gate_funnel import resolve_same_bar_tp_sl


@dataclass
class ExitPolicyConfig:
    """Mirrors PositionManager defaults."""
    tp1_close_fraction: float = 0.40
    tp2_close_fraction: float = 0.30
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    giveback_min_peak_r: float = 1.5
    giveback_threshold_forex: float = 0.55
    giveback_threshold_crypto: float = 0.65
    near_tp_giveback_forex: float = 0.60
    near_tp_giveback_crypto: float = 0.70
    trailing_start_r: float = 2.0
    trailing_step_r: float = 0.5
    dynamic_trail_lock_fraction: float = 0.50  # lock 50% of profit above 1R


@dataclass
class ExitSimState:
    direction: str
    entry: float
    sl: float
    tp: float
    sl_dist: float
    is_crypto: bool = False
    a_plus: bool = False
    remaining_fraction: float = 1.0
    realized_r: float = 0.0
    current_sl: float = 0.0
    peak_r: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    trailing_active: bool = False
    mfe_r: float = 0.0
    mae_r: float = 0.0

    def __post_init__(self):
        if self.current_sl == 0.0:
            self.current_sl = self.sl


def _favor_r(is_long: bool, entry: float, high: float, low: float, sl_dist: float) -> Tuple[float, float, float]:
    if sl_dist <= 0:
        return 0.0, 0.0, 0.0
    if is_long:
        fav = (high - entry) / sl_dist
        adv = (entry - low) / sl_dist
        current_r = fav  # approximate with high for bar peak checks
    else:
        fav = (entry - low) / sl_dist
        adv = (high - entry) / sl_dist
        current_r = fav
    return fav, adv, current_r


def step_exit_policy(
    state: ExitSimState,
    *,
    bar_open: float,
    high: float,
    low: float,
    close: float,
    config: ExitPolicyConfig,
) -> Tuple[Optional[str], float, Optional[float]]:
    """
    Advance one bar through exit policy.

    Returns (outcome, total_r, exit_price) or (None, 0, None) if still open.
    outcome: win | loss | giveback | timeout
    """
    is_long = state.direction == "long"
    sl_dist = state.sl_dist
    cfg = config

    if is_long:
        fav = (high - state.entry) / sl_dist if sl_dist else 0
        adv = (state.entry - low) / sl_dist if sl_dist else 0
        bar_r = (close - state.entry) / sl_dist if sl_dist else 0
    else:
        fav = (state.entry - low) / sl_dist if sl_dist else 0
        adv = (high - state.entry) / sl_dist if sl_dist else 0
        bar_r = (state.entry - close) / sl_dist if sl_dist else 0

    state.mfe_r = max(state.mfe_r, fav)
    state.mae_r = max(state.mae_r, adv)
    state.peak_r = max(state.peak_r, fav)

    sl_hit, tp_hit = resolve_same_bar_tp_sl(
        state.direction, bar_open, high, low, state.current_sl, state.tp
    )

    if tp_hit:
        tp_r = abs(state.tp - state.entry) / sl_dist if sl_dist else 1.0
        return "win", state.realized_r + state.remaining_fraction * tp_r, state.tp

    if sl_hit:
        if is_long:
            exit_r = (state.current_sl - state.entry) / sl_dist if sl_dist else -1.0
        else:
            exit_r = (state.entry - state.current_sl) / sl_dist if sl_dist else -1.0
        total = state.realized_r + state.remaining_fraction * exit_r
        outcome = "win" if total > 0 else "loss"
        return outcome, total, state.current_sl

    # TP1 @ 1R — skip partial for A+ runners
    if not state.a_plus and not state.tp1_hit and fav >= cfg.tp1_r:
        state.tp1_hit = True
        state.realized_r += cfg.tp1_close_fraction * cfg.tp1_r
        state.remaining_fraction = max(0.0, 1.0 - cfg.tp1_close_fraction)
        state.current_sl = state.entry  # break-even

    # Dynamic trail 1R–2R: lock fraction of profit above 1R
    if state.tp1_hit and not state.tp2_hit and 1.0 <= fav < cfg.tp2_r:
        lock_r = 1.0 + (fav - 1.0) * cfg.dynamic_trail_lock_fraction
        if is_long:
            state.current_sl = max(state.current_sl, state.entry + lock_r * sl_dist)
        else:
            state.current_sl = min(state.current_sl, state.entry - lock_r * sl_dist)

    # TP2 @ 2R
    if not state.tp2_hit and fav >= cfg.tp2_r:
        state.tp2_hit = True
        state.realized_r += cfg.tp2_close_fraction * cfg.tp2_r
        state.remaining_fraction = max(
            0.0, state.remaining_fraction - cfg.tp2_close_fraction
        )

    # Trailing after TP2 / 2R
    if state.peak_r >= cfg.trailing_start_r:
        state.trailing_active = True
        trail_r = state.peak_r - cfg.trailing_step_r
        if is_long:
            state.current_sl = max(state.current_sl, state.entry + trail_r * sl_dist)
        else:
            state.current_sl = min(state.current_sl, state.entry - trail_r * sl_dist)

    # Giveback protection
    if state.peak_r >= cfg.giveback_min_peak_r and bar_r > 0:
        threshold = (
            cfg.giveback_threshold_crypto if state.is_crypto else cfg.giveback_threshold_forex
        )
        giveback_pct = (state.peak_r - bar_r) / state.peak_r if state.peak_r > 0 else 0
        if giveback_pct >= threshold:
            total = state.realized_r + state.remaining_fraction * bar_r
            return "giveback", total, close

    return None, 0.0, None


def simulate_exit_policy_bars(
    *,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    bars: list,
    config: Optional[ExitPolicyConfig] = None,
    is_crypto: bool = False,
    a_plus: bool = False,
    max_bars: int = 200,
) -> Tuple[str, float, int, float, float]:
    """
    Walk OHLC bars through exit policy.

    bars: list of dicts with open/high/low/close keys
    Returns (outcome, total_r, bars_held, mfe_r, mae_r)
    """
    cfg = config or ExitPolicyConfig()
    sl_dist = abs(entry - sl) if sl else 1.0
    state = ExitSimState(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        sl_dist=sl_dist,
        is_crypto=is_crypto,
        a_plus=a_plus,
    )

    for i, bar in enumerate(bars):
        if i >= max_bars:
            break
        outcome, total_r, exit_px = step_exit_policy(
            state,
            bar_open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            config=cfg,
        )
        if outcome:
            label = "win" if total_r > 0 else "loss"
            if outcome == "giveback":
                label = "win" if total_r > 0 else "loss"
            return label, total_r, i + 1, state.mfe_r, state.mae_r

    last = bars[-1] if bars else {"close": entry}
    last_close = float(last["close"])
    if direction == "long":
        r = (last_close - entry) / sl_dist if sl_dist else 0
    else:
        r = (entry - last_close) / sl_dist if sl_dist else 0
    total = state.realized_r + state.remaining_fraction * r
    return "timeout", total, len(bars), state.mfe_r, state.mae_r
