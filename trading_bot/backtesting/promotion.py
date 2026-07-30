"""Replay → paper → live promotion evaluator with rollback key."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PromotionResult:
    promoted: bool
    reasons: List[str] = field(default_factory=list)
    rollback_config_key: str = ""
    criteria: dict = field(default_factory=dict)


def evaluate_promotion(
    *,
    parity_mismatches: int,
    paper_trades: int,
    candidate_net_expectancy: float,
    baseline_net_expectancy: float,
    bootstrap_ci_low: float,
    candidate_profit_factor: float,
    baseline_profit_factor: float,
    candidate_max_dd: float,
    baseline_max_dd: float,
    fill_success_rate: float,
    baseline_fill_success_rate: float,
    symbol_concentration: float,
    unresolved_data_quality_gaps: int = 0,
    min_paper_trades: int = 100,
    max_symbol_concentration: float = 0.60,
    max_dd_worsen: float = 0.02,
    rollback_config_key: str = "promotion.rollback_baseline",
) -> PromotionResult:
    """
    Gate a candidate policy for live activation.

    All checks are fail-closed: any unmet criterion rejects promotion.
    """
    reasons: List[str] = []
    criteria = {
        "parity_mismatches": parity_mismatches,
        "paper_trades": paper_trades,
        "paired_delta_net_r": candidate_net_expectancy - baseline_net_expectancy,
        "bootstrap_ci_low": bootstrap_ci_low,
        "candidate_profit_factor": candidate_profit_factor,
        "candidate_max_dd": candidate_max_dd,
        "symbol_concentration": symbol_concentration,
        "unresolved_data_quality_gaps": unresolved_data_quality_gaps,
    }

    if parity_mismatches != 0:
        reasons.append(f"parity mismatches={parity_mismatches} (must be 0)")
    if paper_trades < min_paper_trades:
        reasons.append(
            f"paper_trades={paper_trades} < required {min_paper_trades}"
        )
    if candidate_net_expectancy <= baseline_net_expectancy:
        reasons.append(
            "candidate net expectancy not improved vs baseline "
            f"({candidate_net_expectancy:.3f} <= {baseline_net_expectancy:.3f})"
        )
    if bootstrap_ci_low <= 0:
        reasons.append(
            f"bootstrap CI low={bootstrap_ci_low:.3f} not strictly positive"
        )
    if candidate_profit_factor + 1e-9 < baseline_profit_factor:
        reasons.append(
            "profit factor worse than baseline "
            f"({candidate_profit_factor:.3f} < {baseline_profit_factor:.3f})"
        )
    if fill_success_rate + 1e-9 < baseline_fill_success_rate:
        reasons.append(
            "fill/protection success worse than baseline "
            f"({fill_success_rate:.3f} < {baseline_fill_success_rate:.3f})"
        )
    if candidate_max_dd > baseline_max_dd + max_dd_worsen:
        reasons.append(
            "max drawdown materially worse than baseline "
            f"({candidate_max_dd:.3f} > {baseline_max_dd:.3f}+{max_dd_worsen})"
        )
    if symbol_concentration > max_symbol_concentration:
        reasons.append(
            "single-symbol dependence "
            f"({symbol_concentration:.0%} > {max_symbol_concentration:.0%})"
        )
    if unresolved_data_quality_gaps > 0:
        reasons.append(
            f"unresolved data-quality gaps={unresolved_data_quality_gaps}"
        )

    if reasons:
        return PromotionResult(
            promoted=False,
            reasons=reasons,
            rollback_config_key=rollback_config_key,
            criteria=criteria,
        )

    return PromotionResult(
        promoted=True,
        reasons=["all promotion criteria satisfied"],
        rollback_config_key=rollback_config_key,
        criteria=criteria,
    )


def bootstrap_paired_delta_ci_low(
    paired_deltas: List[float],
    *,
    n_boot: int = 500,
    alpha: float = 0.05,
    seed: int = 42,
) -> float:
    """Conservative lower CI bound for mean paired expectancy improvement."""
    import random

    if not paired_deltas:
        return 0.0
    rng = random.Random(seed)
    n = len(paired_deltas)
    means: List[float] = []
    for _ in range(n_boot):
        sample = [paired_deltas[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    idx = max(0, int(alpha * len(means)) - 1)
    return means[idx]
