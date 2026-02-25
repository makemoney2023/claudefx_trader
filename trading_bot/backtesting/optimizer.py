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
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

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
        }


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

    for t in trades:
        conf = t.get('confidence', 0)
        rr = t.get('risk_reward', 0)
        session = t.get('session', '').lower()
        trade_date = t.get('date', '')

        # Confidence gate
        adjusted_conf = conf
        if 'asian' in session:
            adjusted_conf -= params.session_penalty_asian

        if adjusted_conf < params.min_confidence:
            continue

        # R:R gate
        if rr < params.min_rr:
            continue

        # Daily trade limit
        if trade_date in daily_counts and daily_counts[trade_date] >= params.max_daily_trades:
            continue

        daily_counts[trade_date] = daily_counts.get(trade_date, 0) + 1
        taken.append(t)

    return taken


def _compute_sharpe(trades: List[Dict[str, Any]]) -> float:
    """Compute annualized Sharpe ratio from R-multiples."""
    if len(trades) < 2:
        return 0.0
    r_vals = [t.get('r_multiple', 0) for t in trades]
    mean_r = np.mean(r_vals)
    std_r = np.std(r_vals)
    if std_r == 0:
        return 0.0
    return float(mean_r / std_r * np.sqrt(min(252, len(r_vals))))


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
    }

    def __init__(self, param_space: Optional[Dict[str, List]] = None):
        self.param_space = param_space or self.DEFAULT_PARAM_SPACE

    async def optimize(
        self,
        lookback_days: int = 180,
        n_folds: int = 3,
        train_ratio: float = 0.7,
    ) -> Optional[OptimizationResult]:
        """
        Run walk-forward optimization using historical trades from the database.

        Args:
            lookback_days: How far back to look for trades
            n_folds: Number of walk-forward folds
            train_ratio: Proportion of data for in-sample

        Returns:
            OptimizationResult or None if insufficient data
        """
        trades = await self._load_trades(lookback_days)
        if len(trades) < 20:
            logger.warning(f"Insufficient trades ({len(trades)}) for optimization (need 20+)")
            return None

        logger.info(f"[OPTIMIZER] Running walk-forward on {len(trades)} trades, {n_folds} folds")

        # Sort by timestamp
        trades.sort(key=lambda t: t.get('timestamp', ''))

        # Generate parameter combos
        keys = list(self.param_space.keys())
        values = list(self.param_space.values())
        combos = list(itertools.product(*values))

        fold_size = len(trades) // n_folds
        best_overall_params = None
        best_overall_oos_sharpe = -999
        all_results = []
        fold_best_params = []

        for fold in range(n_folds):
            fold_start = fold * (fold_size // 2)  # Overlapping folds
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

            # Validate on out-of-sample
            oos_taken = _simulate_gate_logic(test, best_combo)
            oos_sharpe = _compute_sharpe(oos_taken) if oos_taken else 0

            fold_best_params.append(best_combo)
            all_results.append({
                'fold': fold,
                'params': best_combo.to_dict(),
                'in_sample_sharpe': round(best_is_sharpe, 3),
                'out_of_sample_sharpe': round(oos_sharpe, 3),
                'in_sample_trades': len(_simulate_gate_logic(train, best_combo)),
                'out_of_sample_trades': len(oos_taken),
            })

            if oos_sharpe > best_overall_oos_sharpe:
                best_overall_oos_sharpe = oos_sharpe
                best_overall_params = best_combo

            logger.info(
                f"[OPTIMIZER] Fold {fold}: IS Sharpe={best_is_sharpe:.3f}, "
                f"OOS Sharpe={oos_sharpe:.3f}, params={best_combo.to_dict()}"
            )

        if best_overall_params is None:
            return None

        # Measure parameter stability across folds
        stability = self._measure_stability(fold_best_params)

        # Full in-sample and out-of-sample evaluation with best params
        split = int(len(trades) * train_ratio)
        full_is = _simulate_gate_logic(trades[:split], best_overall_params)
        full_oos = _simulate_gate_logic(trades[split:], best_overall_params)

        is_wins = sum(1 for t in full_is if t.get('r_multiple', 0) > 0)
        oos_wins = sum(1 for t in full_oos if t.get('r_multiple', 0) > 0)

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

        keys = ['min_confidence', 'min_rr', 'max_daily_trades']
        total_cv = 0
        counted = 0

        for key in keys:
            values = [getattr(p, key) for p in fold_params]
            mean = np.mean(values)
            std = np.std(values)
            if mean > 0:
                cv = std / mean  # Coefficient of variation
                total_cv += cv
                counted += 1

        avg_cv = total_cv / counted if counted > 0 else 0
        return max(0.0, 1.0 - avg_cv)

    async def _load_trades(self, lookback_days: int) -> List[Dict[str, Any]]:
        """Load historical trades from the database."""
        try:
            from sqlalchemy import select, and_
            from ..api.database import async_session_maker, TradeModel

            cutoff = datetime.now() - timedelta(days=lookback_days)

            async with async_session_maker() as session:
                q = select(TradeModel).where(
                    and_(
                        TradeModel.timestamp >= cutoff.isoformat(),
                        TradeModel.exit_price.isnot(None),
                    )
                )
                result = await session.execute(q)
                trades = result.scalars().all()

            return [
                {
                    'timestamp': t.timestamp,
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
                    'date': t.timestamp[:10] if t.timestamp else '',
                    'trade_type': t.trade_type or 'intraday',
                }
                for t in trades
            ]

        except Exception as e:
            logger.error(f"Failed to load trades for optimization: {e}")
            return []
