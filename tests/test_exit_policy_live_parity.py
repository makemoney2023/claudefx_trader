"""Exit policy (replay) must mirror live PositionManager ladder semantics.

Regressions covered:
1. Dynamic trail 1R-2R locked `1.0 + (fav-1)*0.5` R — a full 1R more than
   live's `(r-1)*0.5` — inflating replay expectancy.
2. A+ runners skipped the break-even move entirely (live still moves to BE).
3. Post-TP2 trailing was continuous (peak - 0.5) instead of live's stepped
   `int(r/step)*step - step`.
"""

import pytest

from trading_bot.execution.exit_policy import (
    ExitPolicyConfig,
    ExitSimState,
    step_exit_policy,
)


def _state(**kwargs):
    base = dict(
        direction="long",
        entry=1.0000,
        sl=0.9900,   # sl_dist = 0.0100 → 1R = 100 pips
        tp=1.1000,   # far TP so ladder logic drives exits
        sl_dist=0.0100,
    )
    base.update(kwargs)
    return ExitSimState(**base)


def _step(state, high, low, close=None, bar_open=None):
    return step_exit_policy(
        state,
        bar_open=bar_open if bar_open is not None else (close or high),
        high=high,
        low=low,
        close=close if close is not None else high,
        config=ExitPolicyConfig(),
    )


class TestDynamicTrailMatchesLive:
    def test_lock_at_1_5r_is_quarter_r(self):
        state = _state()
        # Bar reaches 1.5R favorable (high = entry + 1.5 * sl_dist)
        _step(state, high=1.0150, low=1.0000, close=1.0140)
        assert state.tp1_hit is True
        # Live: locked = (1.5 - 1.0) * 0.5 = 0.25R above entry, plus BE buffer floor
        expected_sl = 1.0000 + 0.25 * 0.0100
        assert state.current_sl == pytest.approx(expected_sl)

    def test_lock_never_exceeds_live_formula(self):
        state = _state()
        _step(state, high=1.0190, low=1.0000, close=1.0180)  # 1.9R fav
        # Live: locked = (1.9 - 1.0) * 0.5 = 0.45R
        assert state.current_sl <= 1.0000 + 0.45 * 0.0100 + 1e-9


class TestAPlusBreakEvenParity:
    def test_a_plus_still_moves_to_break_even_at_1r(self):
        state = _state(a_plus=True)
        _step(state, high=1.0105, low=1.0000, close=1.0100)  # 1.05R fav
        assert state.tp1_hit is True
        # No partial for A+ …
        assert state.realized_r == 0.0
        assert state.remaining_fraction == 1.0
        # … but SL must move to break-even (entry + 0.25R buffer, live parity)
        assert state.current_sl >= 1.0000

    def test_a_plus_be_includes_live_buffer(self):
        state = _state(a_plus=True)
        _step(state, high=1.0105, low=1.0000, close=1.0100)
        assert state.current_sl == pytest.approx(1.0000 + 0.25 * 0.0100)


class TestSteppedTrailingParity:
    def test_trailing_at_2_3r_locks_1_5r(self):
        state = _state()
        _step(state, high=1.0150, low=1.0000, close=1.0140)  # TP1 + dyn trail
        _step(state, high=1.0230, low=1.0140, close=1.0220)  # 2.3R peak
        # Live: trail_r = int(2.3/0.5)*0.5 = 2.0 → SL = entry + (2.0-0.5)R = +1.5R
        assert state.current_sl == pytest.approx(1.0000 + 1.5 * 0.0100)

    def test_trailing_at_2_9r_locks_2_0r(self):
        state = _state()
        _step(state, high=1.0150, low=1.0000, close=1.0140)
        _step(state, high=1.0290, low=1.0140, close=1.0280)  # 2.9R peak
        # Live: trail_r = int(2.9/0.5)*0.5 = 2.5 → SL = +2.0R
        assert state.current_sl == pytest.approx(1.0000 + 2.0 * 0.0100)


class TestNearTpReversalParity:
    def test_near_tp_reversal_closes_position(self):
        # TP at 2R so near-TP arm level is 85% of 2R = 1.7R
        state = _state(tp=1.0200)
        outcome, total_r, exit_px = _step(
            state, high=1.0180, low=1.0100, close=1.0175, bar_open=1.0120
        )  # peak 1.8R > 1.7R arms near-TP; no exit yet
        assert outcome is None
        # Reversal: bar closes at 0.6R → giveback from peak = (1.8-0.6)/1.8 = 67% >= 60%
        outcome, total_r, exit_px = _step(
            state, high=1.0120, low=1.0055, close=1.0060, bar_open=1.0110
        )
        assert outcome is not None
        assert total_r > 0
