"""Confidence calibration: reliability bins, Brier score, monotonicity."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_BINS: Tuple[Tuple[str, float, float], ...] = (
    ("50-59%", 0.50, 0.60),
    ("60-69%", 0.60, 0.70),
    ("70-79%", 0.70, 0.80),
    ("80-84%", 0.80, 0.85),
    ("85-100%", 0.85, 1.01),
)


def brier_score(samples: Iterable[Dict[str, Any]]) -> Optional[float]:
    rows = list(samples)
    if not rows:
        return None
    total = 0.0
    for s in rows:
        p = float(s["confidence"])
        o = 1.0 if s.get("won") else 0.0
        total += (p - o) ** 2
    return total / len(rows)


def reliability_bins(
    samples: Iterable[Dict[str, Any]],
    *,
    bins: Tuple[Tuple[str, float, float], ...] = DEFAULT_BINS,
    min_bin: int = 5,
) -> List[Dict[str, Any]]:
    rows = list(samples)
    out: List[Dict[str, Any]] = []
    for label, lo, hi in bins:
        group = [s for s in rows if lo <= float(s["confidence"]) < hi]
        n = len(group)
        if n == 0:
            out.append(
                {
                    "bin": label,
                    "n": 0,
                    "mean_confidence": None,
                    "empirical_win_rate": None,
                    "sparse": True,
                }
            )
            continue
        mean_c = sum(float(s["confidence"]) for s in group) / n
        wr = sum(1 for s in group if s.get("won")) / n
        out.append(
            {
                "bin": label,
                "n": n,
                "mean_confidence": mean_c,
                "empirical_win_rate": wr,
                "sparse": n < min_bin,
            }
        )
    return out


def is_monotonic_win_rates(bins: List[Dict[str, Any]]) -> bool:
    """Empirical win rate should non-decrease as confidence bin increases."""
    observed = [
        b["empirical_win_rate"]
        for b in bins
        if b.get("empirical_win_rate") is not None and not b.get("sparse")
    ]
    if len(observed) < 2:
        return True
    for a, b in zip(observed, observed[1:]):
        if b + 1e-9 < a:
            return False
    return True


def compute_calibration_report(
    samples: Iterable[Dict[str, Any]],
    *,
    min_bin: int = 5,
    min_total: int = 20,
) -> Dict[str, Any]:
    rows = list(samples)
    bins = reliability_bins(rows, min_bin=min_bin)
    bs = brier_score(rows)
    advisory = len(rows) < min_total or all(
        b.get("sparse") for b in bins if b["n"] > 0
    )
    return {
        "n": len(rows),
        "brier": bs,
        "bins": bins,
        "monotonic": is_monotonic_win_rates(bins),
        "advisory": advisory,
        "notes": {
            "execution_floor": 0.60,
            "a_plus_fast_path": 0.85,
            "validate_floors": (
                "Use bins around 0.60 and 0.85 once linked fills are sufficient"
            ),
        },
    }
