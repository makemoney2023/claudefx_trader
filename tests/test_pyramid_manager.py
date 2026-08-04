"""TDD: pyramid add eligibility, sizing, and one-add max."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trading_bot.config import SymbolSpec
from trading_bot.execution.position_manager import Position
from trading_bot.execution.pyramid_manager import evaluate_pyramid_add
from trading_bot.execution.trade_execution import check_position_conflicts


def _pos(**kwargs) -> Position:
    base = dict(
        ticket=101,
        symbol="XAUUSD",
        direction="long",
        volume=0.02,
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        open_time=datetime.now(timezone.utc),
        current_price=2010.0,  # +1.0R (10 / 10)
        confidence=0.75,
        a_plus=False,
        trade_type="intraday",
        pyramid_adds_used=0,
        pyramid_eligible=True,
        pyramid_parent_ticket=None,
    )
    base.update(kwargs)
    p = Position(**{k: v for k, v in base.items() if k in Position.__dataclass_fields__})
    for k, v in base.items():
        if not hasattr(p, k) or getattr(p, k) != v:
            setattr(p, k, v)
    # Re-apply initial_volume after post_init if needed
    if "initial_volume" in kwargs:
        p.initial_volume = kwargs["initial_volume"]
    elif p.initial_volume == 0:
        p.initial_volume = p.volume
    return p


def _spec(volume_min: float = 0.01) -> SymbolSpec:
    return SymbolSpec(
        contract_size=100.0,
        pip_size=0.01,
        pip_value=1.0,
        min_sl_pips=50,
        category="metal",
        tick_value=1.0,
        volume_min=volume_min,
        volume_max=100.0,
        volume_step=0.01,
    )


def _cfg(**overrides):
    cfg = SimpleNamespace(
        enabled=True,
        trigger_r=1.0,
        max_adds=1,
        min_confidence=0.70,
        size_fraction=1.0,
        risk_fraction=0.02,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class TestPyramidEligibility:
    def test_disabled_returns_none(self):
        req, reason = evaluate_pyramid_add(
            _pos(),
            config=_cfg(enabled=False),
            account_equity=1000.0,
            symbol_spec=_spec(),
        )
        assert req is None
        assert reason == "disabled"

    def test_no_add_below_1r(self):
        req, reason = evaluate_pyramid_add(
            _pos(current_price=2005.0),  # 0.5R
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
        )
        assert req is None
        assert reason == "below_trigger_r"

    def test_no_add_when_low_confidence_and_not_a_plus(self):
        req, reason = evaluate_pyramid_add(
            _pos(confidence=0.65, a_plus=False),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
        )
        assert req is None
        assert reason == "quality_gate"

    def test_allows_a_plus_below_min_confidence(self):
        req, reason = evaluate_pyramid_add(
            _pos(confidence=0.60, a_plus=True),
            config=_cfg(),
            account_equity=10_000.0,
            symbol_spec=_spec(),
        )
        assert req is not None
        assert reason == "ok"

    def test_no_second_add_after_one_used(self):
        req, reason = evaluate_pyramid_add(
            _pos(pyramid_adds_used=1),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
        )
        assert req is None
        assert reason == "max_adds"

    def test_child_not_eligible(self):
        req, reason = evaluate_pyramid_add(
            _pos(pyramid_eligible=False, pyramid_parent_ticket=99),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
        )
        assert req is None
        assert reason == "not_eligible"

    def test_scalp_without_quality_blocked(self):
        req, reason = evaluate_pyramid_add(
            _pos(trade_type="scalp", confidence=0.65, a_plus=False),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
        )
        assert req is None
        assert reason == "quality_gate"

    def test_loss_cooldown_blocks(self):
        req, reason = evaluate_pyramid_add(
            _pos(),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
            in_loss_cooldown=True,
        )
        assert req is None
        assert reason == "loss_cooldown"

    def test_opposite_position_blocks(self):
        req, reason = evaluate_pyramid_add(
            _pos(),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
            has_opposite_position=True,
        )
        assert req is None
        assert reason == "opposite_position"

    def test_pending_add_blocks(self):
        req, reason = evaluate_pyramid_add(
            _pos(),
            config=_cfg(),
            account_equity=1000.0,
            symbol_spec=_spec(),
            add_already_pending=True,
        )
        assert req is None
        assert reason == "add_pending"


class TestPyramidSizing:
    def test_add_size_capped_to_primary_volume(self):
        req, reason = evaluate_pyramid_add(
            _pos(volume=0.03, initial_volume=0.03),
            config=_cfg(size_fraction=1.0),
            account_equity=50_000.0,
            symbol_spec=_spec(),
        )
        assert req is not None
        assert req.lots == pytest.approx(0.03)

    def test_size_fraction_reduces_lots(self):
        req, reason = evaluate_pyramid_add(
            _pos(volume=0.04, initial_volume=0.04),
            config=_cfg(size_fraction=0.5),
            account_equity=50_000.0,
            symbol_spec=_spec(),
        )
        assert req is not None
        assert req.lots == pytest.approx(0.02)

    def test_undersized_after_risk_cap_skips(self):
        # Tiny equity + wide SL → even min lot fails FINAL-RISK
        req, reason = evaluate_pyramid_add(
            _pos(
                volume=0.01,
                initial_volume=0.01,
                entry_price=2000.0,
                stop_loss=1900.0,  # huge risk distance
                current_price=2100.0,
            ),
            config=_cfg(risk_fraction=0.01),
            account_equity=100.0,
            symbol_spec=_spec(volume_min=0.01),
        )
        assert req is None
        assert reason in ("undersized", "final_risk_cap")

    def test_sl_and_tp_from_primary_current(self):
        pos = _pos(stop_loss=1995.0, take_profit=2040.0, tp3=2050.0)
        req, _ = evaluate_pyramid_add(
            pos,
            config=_cfg(),
            account_equity=50_000.0,
            symbol_spec=_spec(),
        )
        assert req is not None
        assert req.stop_loss == 1995.0
        assert req.take_profit == 2050.0  # prefer runner TP3


class TestPyramidConflictBypass:
    def test_stacking_blocked_without_pyramid_flag(self):
        positions = [SimpleNamespace(ticket=1, direction="long")]
        outcome = check_position_conflicts(positions, "long")
        assert outcome.blocked is True
        assert outcome.gate_id == "position_stacking"

    def test_stacking_allowed_for_tagged_pyramid_add(self):
        positions = [SimpleNamespace(ticket=1, direction="long")]
        outcome = check_position_conflicts(
            positions, "long", allow_pyramid_add=True
        )
        assert outcome.blocked is False
