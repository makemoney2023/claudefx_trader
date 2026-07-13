"""
Data-driven edge policies.

Pure functions that convert accumulated bot telemetry (mechanical baseline
agreement, setup playbook stats, MFE/MAE excursions, gate expectancy) into
sizing, gating, and exit-tuning decisions.

All policies FAIL OPEN: missing or insufficient data produces no behavior
change, so they only act once real evidence exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..utils.logging import get_logger

logger = get_logger(__name__)

# Sizing multipliers for mechanical/Claude ensemble agreement
AGREEMENT_SIZE_MULT = 1.15
DISAGREEMENT_SIZE_MULT = 0.70

# Playbook hard-gate thresholds
PLAYBOOK_MIN_SAMPLE = 10
PLAYBOOK_MAX_WIN_RATE = 0.35
PLAYBOOK_MAX_AVG_R = 0.0

# Exit trigger tuning bounds (R multiples)
EXIT_TUNING_MIN_SAMPLE = 10
TP1_R_FLOOR, TP1_R_CEIL = 0.8, 1.5
TP2_R_FLOOR, TP2_R_CEIL = 1.6, 3.0

# Gate tuning report thresholds
GATE_TUNING_MIN_SAMPLE = 10
GATE_EASE_FALSE_REJECTION = 0.50
GATE_REVIEW_FALSE_REJECTION = 0.35


@dataclass
class AgreementDecision:
    multiplier: float
    label: str  # agree | disagree | no_baseline | no_signal


def mech_agreement_size_multiplier(
    mech_setup: Optional[dict],
    claude_direction: str,
) -> AgreementDecision:
    """
    Ensemble sizing from mechanical-baseline vs Claude agreement.

    Two independent opinions agreeing is signal; disagreement is a warning.
    Size up on agreement, cut on disagreement, neutral when no baseline.
    """
    if not mech_setup or not isinstance(mech_setup, dict):
        return AgreementDecision(1.0, "no_baseline")
    mech_dir = mech_setup.get("direction")
    if mech_dir not in ("long", "short"):
        return AgreementDecision(1.0, "no_baseline")
    if claude_direction not in ("long", "short"):
        return AgreementDecision(1.0, "no_signal")
    if mech_dir == claude_direction:
        return AgreementDecision(AGREEMENT_SIZE_MULT, "agree")
    return AgreementDecision(DISAGREEMENT_SIZE_MULT, "disagree")


@dataclass
class PlaybookGateResult:
    blocked: bool
    reason: str = ""
    stats: Optional[dict] = None


def evaluate_playbook_gate(
    stats_rows: List[Dict[str, Any]],
    symbol: str,
    direction: str,
    session: str,
    *,
    trade_type: Optional[str] = None,
    min_sample: int = PLAYBOOK_MIN_SAMPLE,
    max_win_rate: float = PLAYBOOK_MAX_WIN_RATE,
    max_avg_r: float = PLAYBOOK_MAX_AVG_R,
) -> PlaybookGateResult:
    """
    Hard-block combos with proven negative expectancy.

    Blocks only when a matching (symbol, direction, session[, setup]) row has
    >= min_sample trades AND win rate below max_win_rate AND avg R <= max_avg_r.
    Everything else fails open.
    """
    if not stats_rows:
        return PlaybookGateResult(False)

    matches = [
        r for r in stats_rows
        if r.get("symbol") == symbol
        and r.get("direction") == direction
        and r.get("session") == session
        and (trade_type is None or r.get("setup") == trade_type)
    ]
    if not matches:
        return PlaybookGateResult(False)

    total_sample = sum(r.get("sample", 0) for r in matches)
    if total_sample < min_sample:
        return PlaybookGateResult(False)

    wins = sum(r.get("sample", 0) * r.get("win_rate", 0.0) for r in matches)
    win_rate = wins / total_sample if total_sample else 0.0
    avg_r = (
        sum(r.get("sample", 0) * r.get("avg_r", 0.0) for r in matches) / total_sample
        if total_sample else 0.0
    )

    if win_rate < max_win_rate and avg_r <= max_avg_r:
        return PlaybookGateResult(
            blocked=True,
            reason=(
                f"Playbook: {symbol} {direction} {session}"
                f"{f' {trade_type}' if trade_type else ''} has "
                f"{win_rate:.0%} win rate, avg {avg_r:+.2f}R over "
                f"{total_sample} trades — proven negative expectancy"
            ),
            stats={"sample": total_sample, "win_rate": win_rate, "avg_r": avg_r},
        )
    return PlaybookGateResult(False)


def exit_trigger_overrides_from_excursion(
    median_winner_mfe_r: float,
    sample_size: int,
    *,
    min_sample: int = EXIT_TUNING_MIN_SAMPLE,
) -> Optional[Dict[str, float]]:
    """
    Per-symbol TP1/TP2 R-trigger tuning from measured winner MFE.

    If winners typically run further than the default ladder assumes,
    extend the partial-close triggers (capped); if they die early,
    tighten toward the floor. Returns None without enough data.
    """
    if sample_size < min_sample or not median_winner_mfe_r or median_winner_mfe_r <= 0:
        return None

    tp1_r = min(max(0.4 * median_winner_mfe_r, TP1_R_FLOOR), TP1_R_CEIL)
    tp2_r = min(max(0.8 * median_winner_mfe_r, TP2_R_FLOOR), TP2_R_CEIL)
    if tp2_r < tp1_r + 0.5:
        tp2_r = tp1_r + 0.5
    return {"tp1_r": round(tp1_r, 2), "tp2_r": round(tp2_r, 2)}


def compute_slippage(
    direction: str,
    requested_entry: Optional[float],
    fill_price: Optional[float],
) -> float:
    """
    Signed slippage in price units. Positive = adverse (paid worse than asked).
    """
    if not requested_entry or not fill_price:
        return 0.0
    if direction == "long":
        return fill_price - requested_entry
    return requested_entry - fill_price


def build_gate_tuning_recommendations(
    gate_expectancy: Dict[str, Dict[str, Any]],
    *,
    min_sample: int = GATE_TUNING_MIN_SAMPLE,
) -> Dict[str, Dict[str, Any]]:
    """
    Turn gate false-rejection analytics into actionable tuning recommendations.

    ease   — the gate blocks mostly would-have-won trades (destroying expectancy)
    review — borderline; inspect the gate's threshold
    keep   — the gate blocks mostly would-have-lost trades (protective)
    """
    recommendations: Dict[str, Dict[str, Any]] = {}
    for gate_id, stats in gate_expectancy.items():
        count = stats.get("count", 0)
        rate = stats.get("false_rejection_rate")
        avg_r = stats.get("avg_hypothetical_r")

        if count < min_sample or rate is None:
            action = "insufficient_data"
        elif rate >= GATE_EASE_FALSE_REJECTION:
            action = "ease"
        elif rate >= GATE_REVIEW_FALSE_REJECTION:
            action = "review"
        else:
            action = "keep"

        recommendations[gate_id] = {
            "action": action,
            "count": count,
            "false_rejection_rate": rate,
            "avg_hypothetical_r": avg_r,
        }
    return recommendations
