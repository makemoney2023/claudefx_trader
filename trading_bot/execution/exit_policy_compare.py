"""Compare exit-policy variants using measured MFE/MAE (no bar replay required)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _target_r(trade: Dict[str, Any]) -> float:
    entry = float(trade.get("entry") or 0)
    sl = float(trade.get("sl") or 0)
    tp = float(trade.get("tp") or 0)
    if not entry or not sl:
        return 2.0
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 2.0
    return abs(tp - entry) / sl_dist if tp else 2.0


def _net(r: float, cost_r: float) -> float:
    return float(r) - float(cost_r or 0.0)


def _policy_full_target(trade: Dict[str, Any]) -> float:
    mfe = float(trade.get("mfe_r") or 0.0)
    mae = float(trade.get("mae_r") or 0.0)
    target = _target_r(trade)
    cost = float(trade.get("cost_r") or 0.0)
    # Conservative: adverse hit stop before target if mae>=1 and mfe < target
    if mae >= 1.0 and mfe < target:
        return _net(-1.0, cost)
    if mfe >= target:
        return _net(target, cost)
    return _net(float(trade.get("realized_r") or 0.0), cost)


def _policy_partial_be(trade: Dict[str, Any]) -> float:
    """40% at 1R, remainder to BE; runner captured at min(mfe, 2R) if reached."""
    mfe = float(trade.get("mfe_r") or 0.0)
    mae = float(trade.get("mae_r") or 0.0)
    cost = float(trade.get("cost_r") or 0.0)
    if mae >= 1.0 and mfe < 1.0:
        return _net(-1.0, cost)
    if mfe < 1.0:
        return _net(float(trade.get("realized_r") or 0.0), cost)
    # TP1 40% at 1R, rest at min(mfe, 2.0) with BE after TP1 (floor 0)
    runner = max(0.0, min(mfe, 2.0))
    r = 0.4 * 1.0 + 0.6 * runner
    return _net(r, cost)


def _policy_structure_trail(trade: Dict[str, Any]) -> float:
    mfe = float(trade.get("mfe_r") or 0.0)
    mae = float(trade.get("mae_r") or 0.0)
    cost = float(trade.get("cost_r") or 0.0)
    if mae >= 1.0 and mfe < 1.0:
        return _net(-1.0, cost)
    if mfe < 1.0:
        return _net(float(trade.get("realized_r") or 0.0), cost)
    # Trail locks 50% of peak above 1R
    locked = 1.0 + 0.5 * max(0.0, mfe - 1.0)
    return _net(locked, cost)


def _policy_giveback(trade: Dict[str, Any]) -> float:
    mfe = float(trade.get("mfe_r") or 0.0)
    mae = float(trade.get("mae_r") or 0.0)
    cost = float(trade.get("cost_r") or 0.0)
    if mae >= 1.0 and mfe < 1.5:
        if mfe < 1.0:
            return _net(-1.0, cost)
    if mfe >= 1.5:
        # Exit retaining 45% of peak (giveback ~55%)
        return _net(mfe * 0.45, cost)
    return _net(float(trade.get("realized_r") or 0.0), cost)


def _policy_time_stop(trade: Dict[str, Any]) -> float:
    """Proxy: if MFE never reached 1R, cut at realized (often timeout)."""
    mfe = float(trade.get("mfe_r") or 0.0)
    cost = float(trade.get("cost_r") or 0.0)
    realized = float(trade.get("realized_r") or 0.0)
    if mfe < 1.0:
        return _net(min(realized, 0.0), cost)
    return _net(realized, cost)


def _policy_baseline(trade: Dict[str, Any]) -> float:
    return _net(
        float(trade.get("realized_r") or 0.0),
        float(trade.get("cost_r") or 0.0),
    )


POLICIES = {
    "baseline_ladder": _policy_baseline,
    "full_target": _policy_full_target,
    "partial_be": _policy_partial_be,
    "structure_trail": _policy_structure_trail,
    "giveback_protection": _policy_giveback,
    "time_stop": _policy_time_stop,
}


def compare_exit_policies(
    trades: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    trade_list = list(trades)
    policies: Dict[str, Dict[str, Any]] = {}
    for name, fn in POLICIES.items():
        rs = [fn(t) for t in trade_list]
        n = len(rs)
        exp = sum(rs) / n if n else None
        wr = (sum(1 for r in rs if r > 0) / n) if n else None
        policies[name] = {
            "n": n,
            "expectancy_net_r": exp,
            "win_rate": wr,
            "sum_net_r": sum(rs) if n else 0.0,
        }
    ranking = sorted(
        (
            (name, stats["expectancy_net_r"])
            for name, stats in policies.items()
            if stats["expectancy_net_r"] is not None
        ),
        key=lambda x: x[1],
        reverse=True,
    )
    return {
        "policies": policies,
        "best": ranking[0][0] if ranking else None,
        "ranking": [name for name, _ in ranking],
        "note": (
            "Counterfactual from measured MFE/MAE; activate live time-stops "
            "only after replay/paper promotion."
        ),
    }
