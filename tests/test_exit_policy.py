"""Tests for shared exit policy aligned with PositionManager."""

import pytest

from trading_bot.execution.exit_policy import (
    ExitPolicyConfig,
    simulate_exit_policy_bars,
)


def _bars_from_path(closes, entry=1.1000, spread=0.0020):
    """Build synthetic OHLC bars from close prices."""
    bars = []
    for c in closes:
        bars.append({
            "open": c,
            "high": c + spread / 2,
            "low": c - spread / 2,
            "close": c,
        })
    return bars


class TestExitPolicyTP1Partial:
    def test_tp1_closes_40_percent_at_1r(self):
        entry, sl = 1.1000, 1.0980
        risk = entry - sl
        tp = entry + risk * 3
        # Bar peaks at 1.6R (close 1.1R + bar high spread) then reverses to SL
        bars = _bars_from_path([entry + risk * 1.1, entry - risk * 0.5])
        outcome, total_r, _, _, _ = simulate_exit_policy_bars(
            direction="long",
            entry=entry,
            sl=sl,
            tp=tp,
            bars=bars,
        )
        # Live parity: 40% at 1R (+0.40R); dynamic trail locks (1.6-1)*0.5 =
        # 0.30R above entry; runner (60%) stops there -> 0.40 + 0.6*0.30 = 0.58R
        assert outcome == "win"
        assert total_r == pytest.approx(0.58, abs=0.05)


class TestExitPolicyAPlus:
    def test_a_plus_skips_tp1_partial(self):
        entry, sl = 1.1000, 1.0980
        risk = entry - sl
        tp = entry + risk * 3
        bars = _bars_from_path([entry + risk * 1.1, entry - risk * 0.5])
        _, total_with_tp1, _, _, _ = simulate_exit_policy_bars(
            direction="long", entry=entry, sl=sl, tp=tp, bars=bars, a_plus=False
        )
        _, total_a_plus, _, _, _ = simulate_exit_policy_bars(
            direction="long", entry=entry, sl=sl, tp=tp, bars=bars, a_plus=True
        )
        assert total_a_plus != total_with_tp1


class TestExitPolicyGiveback:
    def test_giveback_closes_on_peak_reversal(self):
        entry, sl = 1.1000, 1.0980
        risk = entry - sl
        tp = entry + risk * 4
        # Peak at 2R then give back to ~0.5R
        bars = _bars_from_path([
            entry + risk * 2.0,
            entry + risk * 0.6,
        ])
        outcome, total_r, _, _, _ = simulate_exit_policy_bars(
            direction="long",
            entry=entry,
            sl=sl,
            tp=tp,
            bars=bars,
            config=ExitPolicyConfig(),
        )
        assert outcome in ("win", "loss")
        assert total_r > 0
