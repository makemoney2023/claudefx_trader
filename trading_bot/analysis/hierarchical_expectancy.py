"""Hierarchical net-expectancy analytics with pooled fallbacks."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _net_r(trade: Dict[str, Any]) -> float:
    if trade.get("net_r") is not None:
        return float(trade["net_r"])
    return float(trade.get("r_multiple") or 0.0)


def _family(trade: Dict[str, Any]) -> str:
    if trade.get("family"):
        return str(trade["family"])
    fp = str(trade.get("setup_fingerprint") or "")
    return fp.split("|", 1)[0] if fp else "unknown"


def _segment_key(level: str, trade: Dict[str, Any]) -> str:
    fam = _family(trade)
    if level == "family":
        return fam
    if level == "symbol":
        return f"{fam}|{trade.get('symbol') or '?'}"
    if level == "session":
        return (
            f"{fam}|{trade.get('symbol') or '?'}|{trade.get('session') or '?'}"
        )
    if level == "regime":
        return (
            f"{fam}|{trade.get('symbol') or '?'}|"
            f"{trade.get('session') or '?'}|{trade.get('regime') or '?'}"
        )
    # leaf = full fingerprint or family|symbol|session|regime|direction
    fp = trade.get("setup_fingerprint")
    if fp:
        return str(fp)
    return (
        f"{fam}|{trade.get('symbol') or '?'}|{trade.get('session') or '?'}|"
        f"{trade.get('regime') or '?'}|{trade.get('direction') or '?'}"
    )


def _stats(rs: List[float]) -> Dict[str, Any]:
    n = len(rs)
    if n == 0:
        return {
            "n": 0,
            "win_rate": None,
            "avg_win_r": None,
            "avg_loss_r": None,
            "expectancy_net_r": None,
        }
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    wr = len(wins) / n
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = abs(sum(losses) / len(losses)) if losses else 0.0
    exp = sum(rs) / n
    return {
        "n": n,
        "win_rate": wr,
        "avg_win_r": avg_w,
        "avg_loss_r": avg_l,
        "expectancy_net_r": exp,
    }


def segment_decision(
    *,
    n: int,
    expectancy_net_r: Optional[float],
    min_sample_hard_block: int = 10,
) -> Dict[str, Any]:
    """Advisory-only until sample and promotion criteria allow hard blocks."""
    if n < min_sample_hard_block or expectancy_net_r is None:
        return {
            "action": "advisory",
            "hard_block": False,
            "reason": f"n={n} below hard-block sample {min_sample_hard_block}",
        }
    # Still advisory here — Phase 6 promotion evaluator owns hard activation
    return {
        "action": "advisory",
        "hard_block": False,
        "reason": "expectancy negative but hard-block deferred to promotion controls",
        "flag": "negative_expectancy" if expectancy_net_r < 0 else "ok",
    }


def compute_hierarchical_expectancy(
    trades: Iterable[Dict[str, Any]],
    *,
    min_sample: int = 5,
) -> Dict[str, Any]:
    """
    Report expectancy by family → symbol → session → regime → leaf.

    Sparse leaves get `pooled=True` with parent-level expectancy attached.
    """
    trade_list = list(trades)
    levels_out: List[Dict[str, Any]] = []
    parent_exp: Dict[str, float] = {}

    for level in ("family", "symbol", "session", "regime", "leaf"):
        buckets: Dict[str, List[float]] = defaultdict(list)
        for t in trade_list:
            buckets[_segment_key(level, t)].append(_net_r(t))

        for key, rs in sorted(buckets.items()):
            st = _stats(rs)
            row: Dict[str, Any] = {
                "level": level,
                "key": key,
                **st,
                "pooled": False,
            }
            if st["n"] < min_sample:
                # Walk up parents for fallback expectancy
                parent_key = "|".join(key.split("|")[:-1]) if "|" in key else key
                fallback = parent_exp.get(parent_key)
                if fallback is None and level != "family":
                    # try family
                    fallback = parent_exp.get(key.split("|", 1)[0])
                row["pooled"] = True
                row["pooled_expectancy_net_r"] = fallback
                row["decision"] = segment_decision(
                    n=st["n"], expectancy_net_r=fallback
                )
            else:
                parent_exp[key] = float(st["expectancy_net_r"])
                row["decision"] = segment_decision(
                    n=st["n"], expectancy_net_r=st["expectancy_net_r"]
                )
            levels_out.append(row)

    return {
        "min_sample": min_sample,
        "trade_count": len(trade_list),
        "levels": levels_out,
    }
