"""
Shared execution policy used by live orchestration and replay backtests.

Routes replay through the same judge semantics, pending lifecycle, final risk
math, confidence decisions, and position exit policy as production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..config import get_symbol_spec
from ..execution.scaling_position_sizer import enforce_final_risk_cap
from ..services.gate_funnel import resolve_same_bar_tp_sl
from ..services.trade_judge import JudgeOutcome, JudgeVerdict
from ..utils.win_optimization import (
    apply_demote_policy,
    build_confidence_decision,
    classify_a_plus,
    validate_signal_coherence,
)
from .replay_simulation import ReplaySignal, ReplayTrade, simulate_raw_trade
from .costs import apply_symbol_execution_costs


@dataclass
class PolicyReplayResult:
    strategy_trade: ReplayTrade
    execution_trade: Optional[ReplayTrade]
    decision_path: List[str] = field(default_factory=list)
    pending_outcome: Optional[str] = None
    judge_verdict: Optional[str] = None
    confidence_final: float = 0.0
    execution_blocked: bool = False


def evaluate_judge_gate(judge_outcome: JudgeOutcome) -> Tuple[bool, str]:
    """Shared fail-closed judge policy."""
    if judge_outcome.verdict == JudgeVerdict.REJECT:
        return False, "judge_reject"
    if judge_outcome.verdict == JudgeVerdict.UNAVAILABLE:
        return False, "judge_failure"
    if judge_outcome.verdict == JudgeVerdict.DEMOTE:
        return True, "judge_demote"
    return True, "judge_approve"


def simulate_pending_lifecycle(
    direction: str,
    order_type: str,
    entry: float,
    future_data: pd.DataFrame,
    *,
    max_bars: int = 48,
) -> Tuple[str, int, Optional[float]]:
    """
    Simulate pending placement, fill, or expiry.

    Returns (outcome, bars_held, fill_price).
    outcome: placed_unfilled | filled | expired
    """
    if future_data is None or future_data.empty:
        return "expired", 0, None

    ot = (order_type or "buy_limit").lower()
    is_long = direction == "long"

    for i, (_, bar) in enumerate(future_data.iterrows()):
        if i >= max_bars:
            return "expired", i, None

        high = float(bar["high"])
        low = float(bar["low"])

        if ot == "buy_limit" and is_long and low <= entry:
            return "filled", i + 1, entry
        if ot == "sell_limit" and not is_long and high >= entry:
            return "filled", i + 1, entry
        if ot == "buy_stop" and is_long and high >= entry:
            return "filled", i + 1, entry
        if ot == "sell_stop" and not is_long and low <= entry:
            return "filled", i + 1, entry

    return "expired", min(len(future_data), max_bars), None


def simulate_trade_with_exit_policy(
    signal: ReplaySignal,
    future_data: pd.DataFrame,
    *,
    pip_size: float = 0.0001,
    a_plus: bool = False,
    max_bars: int = 200,
) -> ReplayTrade:
    """Simulate partial exits, trailing, and giveback using shared exit_policy."""
    from ..execution.exit_policy import simulate_exit_policy_bars
    from ..config import get_symbol_spec

    if future_data is None or future_data.empty:
        return ReplayTrade(signal=signal, outcome="timeout")

    spec = get_symbol_spec(signal.symbol)
    is_crypto = spec.category == "crypto" if spec else False

    bars = [
        {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for _, row in future_data.iterrows()
    ]

    outcome, total_r, bars_held, mfe, mae = simulate_exit_policy_bars(
        direction=signal.direction,
        entry=signal.entry_price,
        sl=signal.stop_loss,
        tp=signal.take_profit,
        bars=bars,
        is_crypto=is_crypto,
        a_plus=a_plus,
        max_bars=max_bars,
    )

    exit_price = float(future_data.iloc[min(bars_held - 1, len(future_data) - 1)]["close"]) if bars_held else signal.entry_price
    return ReplayTrade(
        signal=signal,
        outcome=outcome,
        exit_price=exit_price,
        r_multiple=total_r,
        mfe_pips=mfe,
        mae_pips=mae,
        bars_held=bars_held,
    )


def run_policy_replay(
    signal: ReplaySignal,
    future_data: pd.DataFrame,
    judge_outcome: JudgeOutcome,
    *,
    current_price: float,
    account_equity: float = 2000.0,
    risk_fraction: float = 0.01,
    lots: float = 0.05,
    pip_size: float = 0.0001,
    confluence_count: int = 3,
    setup_grade: str = "B",
) -> PolicyReplayResult:
    """Full replay pipeline with shared live policies."""
    path: List[str] = []

    coherent, reason = validate_signal_coherence(
        signal.entry_price, signal.stop_loss, signal.take_profit, signal.direction
    )
    if not coherent:
        path.append(f"mechanical_reject:{reason}")
        blocked = ReplayTrade(signal=signal, outcome="timeout", r_multiple=0.0)
        return PolicyReplayResult(
            strategy_trade=blocked,
            execution_trade=None,
            decision_path=path,
            execution_blocked=True,
            judge_verdict="incoherent",
        )

    conf = build_confidence_decision(
        signal.confidence,
        penalties=[("replay", 0.0)],
    )
    path.append(f"confidence:{conf.final:.2f}")

    allowed, judge_path = evaluate_judge_gate(judge_outcome)
    path.append(judge_path)
    if not allowed:
        blocked = ReplayTrade(signal=signal, outcome="timeout", r_multiple=0.0)
        return PolicyReplayResult(
            strategy_trade=blocked,
            execution_trade=None,
            decision_path=path,
            execution_blocked=True,
            judge_verdict=judge_outcome.verdict.value,
            confidence_final=conf.final,
        )

    strategy_signal = signal
    strategy_future = future_data
    strategy_trade = simulate_raw_trade(strategy_signal, strategy_future, pip_size=pip_size)

    execution_signal = ReplaySignal(
        timestamp=signal.timestamp,
        symbol=signal.symbol,
        direction=signal.direction,
        confidence=conf.final,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        reasoning=signal.reasoning,
        trade_type=signal.trade_type,
        market_structure=signal.market_structure,
    )

    pending_outcome = None
    if judge_outcome.verdict == JudgeVerdict.DEMOTE:
        demote = apply_demote_policy(
            signal.direction,
            current_price,
            signal.entry_price,
            signal.stop_loss,
            signal.take_profit,
            "market",
            judge_outcome.suggested_entry,
        )
        execution_signal.entry_price = demote["demoted_entry"]
        execution_signal.stop_loss = demote["stop_loss"]
        execution_signal.take_profit = demote["take_profit"]
        lots *= demote.get("size_multiplier", 0.75)
        path.append(f"demote:{demote['action']}")

        pending_outcome, bars, fill_price = simulate_pending_lifecycle(
            signal.direction,
            demote["order_type"],
            demote["demoted_entry"],
            future_data,
        )
        path.append(f"pending:{pending_outcome}")
        if pending_outcome != "filled":
            return PolicyReplayResult(
                strategy_trade=strategy_trade,
                execution_trade=ReplayTrade(signal=execution_signal, outcome="timeout", r_multiple=0.0),
                decision_path=path,
                pending_outcome=pending_outcome,
                judge_verdict=judge_outcome.verdict.value,
                confidence_final=conf.final,
                execution_blocked=True,
            )
        if fill_price:
            execution_signal.entry_price = fill_price

    spec = get_symbol_spec(signal.symbol)
    allowed_risk, _, risk_reason = enforce_final_risk_cap(
        account_equity,
        risk_fraction,
        execution_signal.entry_price,
        execution_signal.stop_loss,
        lots,
        spec,
    )
    path.append(f"final_risk:{risk_reason}")
    if not allowed_risk:
        return PolicyReplayResult(
            strategy_trade=strategy_trade,
            execution_trade=ReplayTrade(signal=execution_signal, outcome="timeout", r_multiple=0.0),
            decision_path=path,
            judge_verdict=judge_outcome.verdict.value,
            confidence_final=conf.final,
            execution_blocked=True,
        )

    a_plus = classify_a_plus(setup_grade, confluence_count)
    execution_trade = simulate_trade_with_exit_policy(
        execution_signal,
        future_data,
        pip_size=pip_size,
        a_plus=a_plus,
    )
    execution_trade.r_multiple = apply_symbol_execution_costs(
        signal.symbol,
        execution_trade.r_multiple,
        bars_held=execution_trade.bars_held,
    )
    path.append(f"exit:{execution_trade.outcome}")

    return PolicyReplayResult(
        strategy_trade=strategy_trade,
        execution_trade=execution_trade,
        decision_path=path,
        pending_outcome=pending_outcome,
        judge_verdict=judge_outcome.verdict.value,
        confidence_final=conf.final,
        execution_blocked=False,
    )
