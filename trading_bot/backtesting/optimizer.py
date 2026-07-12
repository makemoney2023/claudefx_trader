"""
Walk-Forward Parameter Optimizer.

Optimizes confidence thresholds, R:R minimums, session penalties,
and cooldown durations using in-sample/out-of-sample walk-forward
analysis to prevent overfitting.

Usage:
    optimizer = WalkForwardOptimizer()
    best_params = await optimizer.optimize(lookback_days=180)
"""

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional

import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ParameterSet:
    """A set of gate parameters to optimize."""
    min_confidence: float = 0.60
    min_rr: float = 2.0
    max_daily_trades: int = 3
    session_penalty_asian: float = 0.05
    cooldown_minutes: int = 30
    counter_trend_rr_floor: float = 2.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            'min_confidence': self.min_confidence,
            'min_rr': self.min_rr,
            'max_daily_trades': self.max_daily_trades,
            'session_penalty_asian': self.session_penalty_asian,
            'cooldown_minutes': self.cooldown_minutes,
            'counter_trend_rr_floor': self.counter_trend_rr_floor,
        }


@dataclass
class OptimizationResult:
    """Results from walk-forward optimization."""
    best_params: ParameterSet
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    in_sample_trades: int
    out_of_sample_trades: int
    in_sample_win_rate: float
    out_of_sample_win_rate: float
    parameter_stability: float  # How consistent are results across folds
    all_results: List[Dict[str, Any]] = field(default_factory=list)
    strategy_in_sample_sharpe: float = 0.0
    strategy_out_of_sample_sharpe: float = 0.0
    execution_in_sample_sharpe: float = 0.0
    execution_out_of_sample_sharpe: float = 0.0
    bot_owned_trade_count: int = 0
    holdout_chronological: bool = True
    multiple_testing_note: str = (
        "Walk-forward reduces overfitting but does not eliminate multiple-testing risk "
        "across parameter grid searches. Treat outputs as suggestive, not load-bearing."
    )
    recommend_apply: bool = False
    guardrail_warnings: List[str] = field(default_factory=list)
    grid_combinations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'best_params': self.best_params.to_dict(),
            'in_sample_sharpe': round(self.in_sample_sharpe, 3),
            'out_of_sample_sharpe': round(self.out_of_sample_sharpe, 3),
            'in_sample_trades': self.in_sample_trades,
            'out_of_sample_trades': self.out_of_sample_trades,
            'in_sample_win_rate': round(self.in_sample_win_rate, 2),
            'out_of_sample_win_rate': round(self.out_of_sample_win_rate, 2),
            'parameter_stability': round(self.parameter_stability, 3),
            'strategy_in_sample_sharpe': round(self.strategy_in_sample_sharpe, 3),
            'strategy_out_of_sample_sharpe': round(self.strategy_out_of_sample_sharpe, 3),
            'execution_in_sample_sharpe': round(self.execution_in_sample_sharpe, 3),
            'execution_out_of_sample_sharpe': round(self.execution_out_of_sample_sharpe, 3),
            'bot_owned_trade_count': self.bot_owned_trade_count,
            'holdout_chronological': self.holdout_chronological,
            'multiple_testing_note': self.multiple_testing_note,
            'recommend_apply': self.recommend_apply,
            'guardrail_warnings': self.guardrail_warnings,
            'grid_combinations': self.grid_combinations,
        }


MIN_OOS_TRADES_FOR_APPLY = 15
MIN_PARAMETER_STABILITY = 0.60
MIN_OOS_SHARPE = 0.0


def evaluate_optimizer_guardrails(
    *,
    out_of_sample_trades: int,
    out_of_sample_sharpe: float,
    parameter_stability: float,
    fold_flags: List[str],
    grid_combinations: int,
) -> tuple[bool, List[str]]:
    """Return (recommend_apply, warnings). Conservative by default."""
    warnings: List[str] = []
    if out_of_sample_trades < MIN_OOS_TRADES_FOR_APPLY:
        warnings.append(
            f"OOS trade count {out_of_sample_trades} < {MIN_OOS_TRADES_FOR_APPLY}"
        )
    if out_of_sample_sharpe < MIN_OOS_SHARPE:
        warnings.append(f"OOS Sharpe {out_of_sample_sharpe:.3f} < {MIN_OOS_SHARPE}")
    if parameter_stability < MIN_PARAMETER_STABILITY:
        warnings.append(
            f"Parameter stability {parameter_stability:.2f} < {MIN_PARAMETER_STABILITY}"
        )
    for flag in fold_flags:
        if flag:
            warnings.append(f"Fold flag: {flag}")
    if grid_combinations > 200:
        warnings.append(
            f"Grid searched {grid_combinations} combos — multiple-testing risk elevated (advisory)"
        )
    critical = [w for w in warnings if "advisory" not in w]
    recommend = len(critical) == 0
    return recommend, warnings


def _simulate_gate_logic(
    trades: List[Dict[str, Any]],
    params: ParameterSet,
) -> List[Dict[str, Any]]:
    """
    Apply gate logic to historical trades using the given parameters.
    Returns the subset of trades that would have been taken.
    """
    taken = []
    daily_counts: Dict[str, int] = {}
    last_trade_time: Optional[str] = None

    for t in trades:
        conf = t.get('confidence', 0)
        rr = t.get('risk_reward', 0)
        session = t.get('session', '').lower()
        trade_date = t.get('date', '')
        timestamp = t.get('timestamp', '')

        adjusted_conf = conf
        if 'asian' in session:
            adjusted_conf -= params.session_penalty_asian

        if adjusted_conf < params.min_confidence:
            continue

        # Counter-trend trades require higher R:R floor
        direction = t.get('direction', '')
        trend = t.get('trend', '')
        is_counter_trend = (
            (direction == 'long' and trend == 'bearish') or
            (direction == 'short' and trend == 'bullish')
        )
        effective_min_rr = params.counter_trend_rr_floor if is_counter_trend else params.min_rr
        if rr < effective_min_rr:
            continue

        if trade_date in daily_counts and daily_counts[trade_date] >= params.max_daily_trades:
            continue

        # Cooldown gate: skip if too close to previous trade
        if last_trade_time and timestamp and params.cooldown_minutes > 0:
            try:
                prev_dt = datetime.fromisoformat(last_trade_time)
                curr_dt = datetime.fromisoformat(timestamp)
                if (curr_dt - prev_dt).total_seconds() < params.cooldown_minutes * 60:
                    continue
            except (ValueError, TypeError):
                pass

        daily_counts[trade_date] = daily_counts.get(trade_date, 0) + 1
        taken.append(t)
        last_trade_time = timestamp

    return taken


def _compute_sharpe(trades: List[Dict[str, Any]]) -> float:
    """Compute annualized Sharpe ratio from R-multiples, scaled by actual trade frequency."""
    if len(trades) < 2:
        return 0.0
    r_vals = [t.get('r_multiple', 0) for t in trades]
    mean_r = np.mean(r_vals)
    std_r = np.std(r_vals, ddof=1)
    if std_r == 0:
        return 0.0
    timestamps = [t.get('timestamp', '') for t in trades if t.get('timestamp')]
    if len(timestamps) >= 2:
        try:
            first = datetime.fromisoformat(timestamps[0])
            last = datetime.fromisoformat(timestamps[-1])
            span_days = max((last - first).days, 1)
            trades_per_year = len(trades) / span_days * 365
        except (ValueError, TypeError):
            trades_per_year = len(trades)
    else:
        trades_per_year = len(trades)
    annualization = np.sqrt(max(trades_per_year, 1))
    return float(mean_r / std_r * annualization)


class WalkForwardOptimizer:
    """
    Walk-forward optimizer for trading gate parameters.

    Splits historical trades into overlapping in-sample (train) and
    out-of-sample (test) windows, optimizes on in-sample, validates
    on out-of-sample, and returns robust parameter estimates.
    """

    DEFAULT_PARAM_SPACE = {
        'min_confidence': [0.55, 0.60, 0.65, 0.70],
        'min_rr': [1.5, 2.0, 2.5, 3.0],
        'max_daily_trades': [2, 3, 4, 5],
        'session_penalty_asian': [0.0, 0.05, 0.10],
        'cooldown_minutes': [0, 30, 60],
        'counter_trend_rr_floor': [2.0, 2.5, 3.0],
    }

    def __init__(self, param_space: Optional[Dict[str, List]] = None):
        self.param_space = param_space or self.DEFAULT_PARAM_SPACE

    async def optimize(
        self,
        lookback_days: int = 180,
        n_folds: int = 3,
        train_ratio: float = 0.7,
        progress_callback=None,
    ) -> Optional[OptimizationResult]:
        """
        Run walk-forward optimization using historical trades from the database.

        Args:
            lookback_days: How far back to look for trades
            n_folds: Number of walk-forward folds
            train_ratio: Proportion of data for in-sample
            progress_callback: Optional async fn(pct: int, step: str)

        Returns:
            OptimizationResult or None if insufficient data
        """
        trades = await self._load_trades(lookback_days)
        if len(trades) < 20:
            logger.warning(f"Insufficient trades ({len(trades)}) for optimization (need 20+)")
            return None

        logger.info(f"[OPTIMIZER] Running walk-forward on {len(trades)} trades, {n_folds} folds")

        trades.sort(key=lambda t: t.get('timestamp', ''))

        keys = list(self.param_space.keys())
        values = list(self.param_space.values())
        combos = list(itertools.product(*values))

        # Non-overlapping folds (anchored expanding window)
        fold_size = len(trades) // n_folds
        best_overall_params = None
        best_overall_oos_sharpe = -999
        all_results = []
        fold_best_params = []

        for fold in range(n_folds):
            if progress_callback:
                pct = int(fold / max(n_folds, 1) * 80)
                try:
                    await progress_callback(pct, f"Fold {fold + 1}/{n_folds} ({len(combos)} combos)")
                except Exception:
                    pass

            fold_start = fold * fold_size
            fold_end = min(fold_start + fold_size, len(trades))
            fold_trades = trades[fold_start:fold_end]

            split_idx = int(len(fold_trades) * train_ratio)
            train = fold_trades[:split_idx]
            test = fold_trades[split_idx:]

            if len(train) < 5 or len(test) < 3:
                continue

            best_is_sharpe = -999
            best_combo = None

            for combo in combos:
                params = ParameterSet(**dict(zip(keys, combo)))
                taken = _simulate_gate_logic(train, params)
                if len(taken) < 3:
                    continue
                sharpe = _compute_sharpe(taken)
                if sharpe > best_is_sharpe:
                    best_is_sharpe = sharpe
                    best_combo = params

            if best_combo is None:
                continue

            oos_taken = _simulate_gate_logic(test, best_combo)
            oos_sharpe = _compute_sharpe(oos_taken) if oos_taken else 0

            oos_flagged = ""
            if len(oos_taken) < 10:
                oos_flagged = " [LOW_OOS_COUNT]"
            elif best_is_sharpe > 0 and oos_sharpe < best_is_sharpe * 0.5:
                oos_flagged = " [OOS_DEGRADED]"

            fold_best_params.append(best_combo)
            all_results.append({
                'fold': fold,
                'params': best_combo.to_dict(),
                'in_sample_sharpe': round(best_is_sharpe, 3),
                'out_of_sample_sharpe': round(oos_sharpe, 3),
                'in_sample_trades': len(_simulate_gate_logic(train, best_combo)),
                'out_of_sample_trades': len(oos_taken),
                'flag': oos_flagged.strip() if oos_flagged else None,
            })

            if oos_sharpe > best_overall_oos_sharpe:
                best_overall_oos_sharpe = oos_sharpe
                best_overall_params = best_combo

            logger.info(
                f"[OPTIMIZER] Fold {fold}: IS Sharpe={best_is_sharpe:.3f}, "
                f"OOS Sharpe={oos_sharpe:.3f}{oos_flagged}, params={best_combo.to_dict()}"
            )

        if best_overall_params is None:
            return None

        if progress_callback:
            try:
                await progress_callback(90, "Computing final evaluation...")
            except Exception:
                pass

        stability = self._measure_stability(fold_best_params)

        split = int(len(trades) * train_ratio)
        full_is = _simulate_gate_logic(trades[:split], best_overall_params)
        full_oos = _simulate_gate_logic(trades[split:], best_overall_params)

        is_wins = sum(1 for t in full_is if t.get('r_multiple', 0) > 0)
        oos_wins = sum(1 for t in full_oos if t.get('r_multiple', 0) > 0)

        fold_flags = [r.get('flag') or '' for r in all_results]
        recommend, warnings = evaluate_optimizer_guardrails(
            out_of_sample_trades=len(full_oos),
            out_of_sample_sharpe=_compute_sharpe(full_oos),
            parameter_stability=stability,
            fold_flags=fold_flags,
            grid_combinations=len(combos),
        )

        result = OptimizationResult(
            best_params=best_overall_params,
            in_sample_sharpe=_compute_sharpe(full_is),
            out_of_sample_sharpe=_compute_sharpe(full_oos),
            in_sample_trades=len(full_is),
            out_of_sample_trades=len(full_oos),
            in_sample_win_rate=is_wins / len(full_is) * 100 if full_is else 0,
            out_of_sample_win_rate=oos_wins / len(full_oos) * 100 if full_oos else 0,
            parameter_stability=stability,
            all_results=all_results,
            strategy_in_sample_sharpe=_compute_sharpe(trades[:split]),
            strategy_out_of_sample_sharpe=_compute_sharpe(trades[split:]),
            execution_in_sample_sharpe=_compute_sharpe(full_is),
            execution_out_of_sample_sharpe=_compute_sharpe(full_oos),
            bot_owned_trade_count=len(trades),
            holdout_chronological=True,
            recommend_apply=recommend,
            guardrail_warnings=warnings,
            grid_combinations=len(combos),
        )

        logger.info(
            f"[OPTIMIZER] Best params: {best_overall_params.to_dict()}, "
            f"OOS Sharpe={result.out_of_sample_sharpe:.3f}, "
            f"stability={stability:.3f}"
        )
        return result

    def _measure_stability(self, fold_params: List[ParameterSet]) -> float:
        """Measure how consistent parameters are across folds (0-1, higher=more stable)."""
        if len(fold_params) < 2:
            return 1.0

        keys = ['min_confidence', 'min_rr', 'max_daily_trades', 'cooldown_minutes', 'counter_trend_rr_floor']
        total_cv = 0
        counted = 0

        for key in keys:
            values = [getattr(p, key) for p in fold_params]
            mean = np.mean(values)
            std = np.std(values)
            if mean > 0:
                cv = std / mean
                total_cv += cv
                counted += 1

        avg_cv = total_cv / counted if counted > 0 else 0
        return max(0.0, 1.0 - avg_cv)

    async def _load_trades(self, lookback_days: int) -> List[Dict[str, Any]]:
        """Load bot-owned historical trades from the database (chronological)."""
        try:
            from sqlalchemy import select, and_, or_
            from ..api.database import async_session_maker, TradeModel

            cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

            async with async_session_maker() as session:
                q = select(TradeModel).where(
                    and_(
                        TradeModel.timestamp >= cutoff,
                        TradeModel.exit_price.isnot(None),
                        or_(
                            TradeModel.judge_verdict.isnot(None),
                            TradeModel.claude_confidence > 0,
                            TradeModel.signal_id.isnot(None),
                        ),
                    )
                ).order_by(TradeModel.timestamp)
                result = await session.execute(q)
                trades = result.scalars().all()

            loaded = []
            for t in trades:
                ts = t.timestamp.isoformat() if hasattr(t.timestamp, "isoformat") else str(t.timestamp)
                loaded.append({
                    'timestamp': ts,
                    'symbol': t.symbol,
                    'direction': t.direction,
                    'session': t.session or '',
                    'confidence': t.claude_confidence or 0.5,
                    'risk_reward': (
                        abs(float(t.take_profit) - float(t.entry_price)) /
                        abs(float(t.entry_price) - float(t.stop_loss))
                        if t.entry_price and t.stop_loss and t.take_profit
                        and abs(float(t.entry_price) - float(t.stop_loss)) > 0
                        else 0
                    ),
                    'r_multiple': t.r_multiple or 0,
                    'profit_loss': t.profit_loss or 0,
                    'date': ts[:10] if ts else '',
                    'trade_type': t.trade_type or 'intraday',
                    'trend': getattr(t, 'market_structure', '') or '',
                    'bot_owned': True,
                })
            return loaded

        except Exception as e:
            logger.error(f"Failed to load trades for optimization: {e}")
            return []
