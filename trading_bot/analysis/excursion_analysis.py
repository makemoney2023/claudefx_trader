"""
MFE/MAE Excursion Analysis.

Computes Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion
(MAE) distributions from historical trade data to validate and optimize
SL/TP distances.

Key metrics:
- optimal_sl_pips: 90th percentile MAE of WINNING trades
- optimal_tp_pips: median MFE of winning trades
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any

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
    optimal_sl: float  # 90th percentile MAE of winners
    optimal_tp: float  # Median MFE of winners
    median_winner_mfe_r: float = 0.0  # Median winner MFE in R multiples
    winner_sample: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'direction': self.direction,
            'sample_size': self.sample_size,
            'avg_mfe': round(self.avg_mfe, 5),
            'median_mfe': round(self.median_mfe, 5),
            'p90_mfe': round(self.p90_mfe, 5),
            'avg_mae': round(self.avg_mae, 5),
            'median_mae': round(self.median_mae, 5),
            'p90_mae': round(self.p90_mae, 5),
            'optimal_sl': round(self.optimal_sl, 5),
            'optimal_tp': round(self.optimal_tp, 5),
            'median_winner_mfe_r': round(self.median_winner_mfe_r, 2),
            'winner_sample': self.winner_sample,
        }


class ExcursionAnalyzer:
    """
    Computes MFE/MAE distributions from historical trade data.

    MFE = Maximum Favorable Excursion (how far winners run in profit direction)
    MAE = Maximum Adverse Excursion (how far winners dip against you before TP)
    """

    async def compute(
        self,
        symbol: str,
        direction: str = "all",
        lookback_days: int = 90,
    ) -> Optional[ExcursionResult]:
        """
        Compute MFE/MAE from the replay backtest trades or live trade data.

        For live trades, the PositionManager tracks peak_r_multiple which
        approximates MFE. MAE is estimated from (entry - worst price during
        the trade).

        Args:
            symbol: Trading symbol
            direction: 'long', 'short', or 'all'
            lookback_days: Lookback period

        Returns:
            ExcursionResult or None if insufficient data
        """
        try:
            from datetime import datetime, timedelta
            from sqlalchemy import select, and_
            from ..api.database import async_session_maker, TradeModel

            cutoff = datetime.now() - timedelta(days=lookback_days)

            async with async_session_maker() as session:
                conditions = [
                    TradeModel.symbol == symbol,
                    TradeModel.timestamp >= cutoff.isoformat(),
                    TradeModel.exit_price.isnot(None),
                    TradeModel.entry_price.isnot(None),
                    TradeModel.stop_loss.isnot(None),
                ]
                if direction != "all":
                    conditions.append(TradeModel.direction == direction)

                q = select(TradeModel).where(and_(*conditions))
                result = await session.execute(q)
                trades = result.scalars().all()

            if len(trades) < 5:
                return None

            mfes = []
            maes = []
            winner_mfes = []
            winner_maes = []
            winner_mfe_rs = []

            for t in trades:
                entry = float(t.entry_price)
                sl = float(t.stop_loss)
                exit_p = float(t.exit_price) if t.exit_price else entry
                is_long = t.direction == 'long'
                is_winner = (t.profit_loss or 0) > 0
                sl_dist = abs(entry - sl)

                if sl_dist <= 0:
                    continue

                if is_long:
                    mfe_abs = max(0, exit_p - entry) if is_winner else 0
                    mae_abs = max(0, entry - exit_p) if not is_winner else sl_dist * 0.3
                else:
                    mfe_abs = max(0, entry - exit_p) if is_winner else 0
                    mae_abs = max(0, exit_p - entry) if not is_winner else sl_dist * 0.3

                # If we have peak_r_multiple stored, use it for better MFE estimate
                peak_r = getattr(t, 'r_multiple', None)
                if peak_r and peak_r > 0 and is_winner:
                    mfe_abs = max(mfe_abs, peak_r * sl_dist)

                mfes.append(mfe_abs)
                maes.append(mae_abs)

                if is_winner:
                    winner_mfes.append(mfe_abs)
                    winner_maes.append(mae_abs)
                    winner_mfe_rs.append(mfe_abs / sl_dist)

            if not mfes:
                return None

            mfes_arr = np.array(mfes)
            maes_arr = np.array(maes)
            w_mfes = np.array(winner_mfes) if winner_mfes else np.array([0])
            w_maes = np.array(winner_maes) if winner_maes else np.array([0])

            # Optimal SL = 90th percentile MAE of winning trades
            optimal_sl = float(np.percentile(w_maes, 90)) if len(w_maes) > 0 else 0
            # Optimal TP = median MFE of winning trades
            optimal_tp = float(np.median(w_mfes)) if len(w_mfes) > 0 else 0

            result = ExcursionResult(
                symbol=symbol,
                direction=direction,
                sample_size=len(trades),
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

            logger.info(
                f"[MFE/MAE] {symbol} ({direction}): {len(trades)} trades, "
                f"opt_SL={optimal_sl:.5f}, opt_TP={optimal_tp:.5f}"
            )
            return result

        except Exception as e:
            logger.warning(f"MFE/MAE computation failed for {symbol}: {e}")
            return None
