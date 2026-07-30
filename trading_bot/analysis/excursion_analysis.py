"""
MFE/MAE Excursion Analysis.

Computes Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion
(MAE) distributions from measured peak/trough R multiples persisted on
closed trades. Heuristic estimates (exit-as-MFE, 0.3R winner MAE) are gone —
without measured extrema a trade is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ExcursionResult:
    """MFE/MAE analysis result."""
    symbol: str
    direction: str
    sample_size: int
    avg_mfe: float
    median_mfe: float
    p90_mfe: float
    avg_mae: float
    median_mae: float
    p90_mae: float
    optimal_sl: float  # 90th percentile MAE of winners (price units)
    optimal_tp: float  # Median MFE of winners (price units)
    median_winner_mfe_r: float = 0.0
    winner_sample: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "sample_size": self.sample_size,
            "avg_mfe": round(self.avg_mfe, 5),
            "median_mfe": round(self.median_mfe, 5),
            "p90_mfe": round(self.p90_mfe, 5),
            "avg_mae": round(self.avg_mae, 5),
            "median_mae": round(self.median_mae, 5),
            "p90_mae": round(self.p90_mae, 5),
            "optimal_sl": round(self.optimal_sl, 5),
            "optimal_tp": round(self.optimal_tp, 5),
            "median_winner_mfe_r": round(self.median_winner_mfe_r, 2),
            "winner_sample": self.winner_sample,
        }


def _measured_peak_r(trade) -> Optional[float]:
    for attr in ("peak_r_multiple", "mfe_r"):
        val = getattr(trade, attr, None)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _measured_trough_r(trade) -> Optional[float]:
    for attr in ("trough_r_multiple", "mae_r"):
        val = getattr(trade, attr, None)
        if val is not None:
            try:
                # mae_r is stored as adverse magnitude (positive); trough is signed
                f = float(val)
                if attr == "mae_r":
                    return -abs(f)
                return f
            except (TypeError, ValueError):
                continue
    return None


class ExcursionAnalyzer:
    """Computes MFE/MAE distributions from measured trade extrema."""

    def compute_from_trades(
        self,
        trades: Sequence[Any],
        *,
        symbol: str,
        direction: str = "all",
    ) -> Optional[ExcursionResult]:
        """Pure computation over an in-memory trade sequence (testable)."""
        mfes: List[float] = []
        maes: List[float] = []
        winner_mfes: List[float] = []
        winner_maes: List[float] = []
        winner_mfe_rs: List[float] = []
        used = 0

        for t in trades:
            if direction != "all" and getattr(t, "direction", None) != direction:
                continue
            entry = float(getattr(t, "entry_price", 0) or 0)
            sl = float(getattr(t, "stop_loss", 0) or 0)
            sl_dist = abs(entry - sl)
            if sl_dist <= 0:
                continue

            peak_r = _measured_peak_r(t)
            trough_r = _measured_trough_r(t)
            if peak_r is None or trough_r is None:
                continue

            mfe_abs = max(0.0, peak_r) * sl_dist
            mae_abs = abs(min(0.0, trough_r)) * sl_dist
            is_winner = (getattr(t, "profit_loss", 0) or 0) > 0

            mfes.append(mfe_abs)
            maes.append(mae_abs)
            used += 1
            if is_winner:
                winner_mfes.append(mfe_abs)
                winner_maes.append(mae_abs)
                winner_mfe_rs.append(max(0.0, peak_r))

        if used < 5:
            return None

        mfes_arr = np.array(mfes)
        maes_arr = np.array(maes)
        w_mfes = np.array(winner_mfes) if winner_mfes else np.array([0.0])
        w_maes = np.array(winner_maes) if winner_maes else np.array([0.0])

        optimal_sl = float(np.percentile(w_maes, 90)) if len(winner_maes) else 0.0
        optimal_tp = float(np.median(w_mfes)) if len(winner_mfes) else 0.0

        return ExcursionResult(
            symbol=symbol,
            direction=direction,
            sample_size=used,
            avg_mfe=float(np.mean(mfes_arr)),
            median_mfe=float(np.median(mfes_arr)),
            p90_mfe=float(np.percentile(mfes_arr, 90)),
            avg_mae=float(np.mean(maes_arr)),
            median_mae=float(np.median(maes_arr)),
            p90_mae=float(np.percentile(maes_arr, 90)),
            optimal_sl=optimal_sl,
            optimal_tp=optimal_tp,
            median_winner_mfe_r=(
                float(np.median(winner_mfe_rs)) if winner_mfe_rs else 0.0
            ),
            winner_sample=len(winner_mfe_rs),
        )

    async def compute(
        self,
        symbol: str,
        direction: str = "all",
        lookback_days: int = 90,
    ) -> Optional[ExcursionResult]:
        """Load closed trades from DB and compute measured MFE/MAE."""
        try:
            from sqlalchemy import select, and_
            from ..api.database import async_session_maker, TradeModel

            cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

            async with async_session_maker() as session:
                conditions = [
                    TradeModel.symbol == symbol,
                    TradeModel.timestamp >= cutoff,
                    TradeModel.exit_price.isnot(None),
                    TradeModel.entry_price.isnot(None),
                    TradeModel.stop_loss.isnot(None),
                ]
                if direction != "all":
                    conditions.append(TradeModel.direction == direction)

                q = select(TradeModel).where(and_(*conditions))
                result = await session.execute(q)
                trades = result.scalars().all()

            computed = self.compute_from_trades(
                trades, symbol=symbol, direction=direction
            )
            if computed:
                logger.info(
                    f"[MFE/MAE] {symbol} ({direction}): {computed.sample_size} trades, "
                    f"opt_SL={computed.optimal_sl:.5f}, opt_TP={computed.optimal_tp:.5f}"
                )
            return computed

        except Exception as e:
            logger.warning(f"MFE/MAE computation failed for {symbol}: {e}")
            return None
