"""
Market Regime Classifier.

Combines ADX trend strength, ATR volatility ratio, and AMD cycle phase
to classify the current market regime and recommend strategy adjustments.

Regimes:
    TRENDING_STRONG  - ADX>25, clear trend, with-trend entries only
    TRENDING_WEAK    - ADX 20-25, moderate trend
    RANGING          - ADX<20, favor limit orders at range edges
    VOLATILE_TRENDING - High ATR + trend, wider SL needed
    VOLATILE_RANGING  - High ATR + no trend, reduce size or sit out
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

from ..utils.candle_utils import calculate_atr
from ..utils.logging import get_logger

logger = get_logger(__name__)


class Regime(str, Enum):
    TRENDING_STRONG = "trending_strong"
    TRENDING_WEAK = "trending_weak"
    RANGING = "ranging"
    VOLATILE_TRENDING = "volatile_trending"
    VOLATILE_RANGING = "volatile_ranging"


REGIME_GUIDANCE = {
    Regime.TRENDING_STRONG: (
        "Strong trend detected. Favor WITH-TREND entries only. "
        "Market orders on displacement are preferred. "
        "Trailing stops work well."
    ),
    Regime.TRENDING_WEAK: (
        "Moderate trend. With-trend still preferred but be selective. "
        "Limit orders at pullbacks to key levels."
    ),
    Regime.RANGING: (
        "Ranging/choppy market. Only use limit orders at range extremes (VAH/VAL, OB edges). "
        "AVOID market orders — they chase price in a range. "
        "Expect mean reversion toward POC."
    ),
    Regime.VOLATILE_TRENDING: (
        "Volatile trending market. Widen SL by 1.5x to avoid noise stop-outs. "
        "Trade WITH the trend only. Reduce position size by 25%. "
        "Partial TP at 1R is more important here."
    ),
    Regime.VOLATILE_RANGING: (
        "Volatile ranging — DANGEROUS conditions. Consider sitting out. "
        "If trading, reduce size by 50%, widen SL by 1.5x, only limit orders at extremes. "
        "Many false breakouts expected."
    ),
}


@dataclass
class RegimeResult:
    """Classification result."""
    regime: Regime
    adx: float
    volatility_ratio: float
    market_phase: str
    confidence: float
    guidance: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            'regime': self.regime.value,
            'adx': round(self.adx, 2),
            'volatility_ratio': round(self.volatility_ratio, 2),
            'phase': self.market_phase,
            'confidence': round(self.confidence, 2),
            'guidance': self.guidance,
        }


def _compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Compute Average Directional Index from OHLCV data."""
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)

    n = len(df)
    if n < period + 1:
        return 0.0

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    # Smoothed sums (Wilder's smoothing)
    atr_smooth = np.zeros(n)
    plus_smooth = np.zeros(n)
    minus_smooth = np.zeros(n)

    atr_smooth[period] = np.sum(tr[1:period + 1])
    plus_smooth[period] = np.sum(plus_dm[1:period + 1])
    minus_smooth[period] = np.sum(minus_dm[1:period + 1])

    for i in range(period + 1, n):
        atr_smooth[i] = atr_smooth[i - 1] - atr_smooth[i - 1] / period + tr[i]
        plus_smooth[i] = plus_smooth[i - 1] - plus_smooth[i - 1] / period + plus_dm[i]
        minus_smooth[i] = minus_smooth[i - 1] - minus_smooth[i - 1] / period + minus_dm[i]

    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    dx = np.zeros(n)

    for i in range(period, n):
        if atr_smooth[i] > 0:
            plus_di[i] = 100 * plus_smooth[i] / atr_smooth[i]
            minus_di[i] = 100 * minus_smooth[i] / atr_smooth[i]
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

    # ADX = smoothed average of DX over last `period` bars
    adx_vals = dx[period:]
    if len(adx_vals) < period:
        return float(np.mean(adx_vals)) if len(adx_vals) > 0 else 0.0

    adx = float(np.mean(adx_vals[-period:]))
    return adx


class RegimeClassifier:
    """
    Classifies the current market regime by combining:
    1. ADX trend strength
    2. ATR volatility ratio (current vs 20-bar average)
    3. Market phase from PowerOfThree / AMD analyzer (if available)
    """

    def __init__(
        self,
        adx_strong: float = 25.0,
        adx_weak: float = 20.0,
        vol_high: float = 1.5,
        vol_low: float = 0.7,
    ):
        self.adx_strong = adx_strong
        self.adx_weak = adx_weak
        self.vol_high = vol_high
        self.vol_low = vol_low

    def classify(
        self,
        df: pd.DataFrame,
        market_phase: str = "unknown",
    ) -> Optional[RegimeResult]:
        """
        Classify the current market regime.

        Args:
            df: OHLCV DataFrame (M15 or H1 recommended)
            market_phase: AMD phase from the bot's AMD analyzer

        Returns:
            RegimeResult or None if data is insufficient
        """
        if df is None or len(df) < 30:
            return None

        try:
            adx = _compute_adx(df, period=14)
            atr_series = calculate_atr(df, period=14)
            current_atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0
            avg_atr = float(atr_series.iloc[-20:].mean()) if len(atr_series) >= 20 else current_atr

            vol_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0

            is_trending = adx >= self.adx_weak
            is_strong_trend = adx >= self.adx_strong
            is_high_vol = vol_ratio >= self.vol_high

            if is_high_vol:
                if is_trending:
                    regime = Regime.VOLATILE_TRENDING
                else:
                    regime = Regime.VOLATILE_RANGING
            elif is_strong_trend:
                regime = Regime.TRENDING_STRONG
            elif is_trending:
                regime = Regime.TRENDING_WEAK
            else:
                regime = Regime.RANGING

            confidence = min(1.0, 0.5 + abs(adx - self.adx_weak) / 20)
            guidance = REGIME_GUIDANCE.get(regime, "Use standard approach.")

            result = RegimeResult(
                regime=regime,
                adx=adx,
                volatility_ratio=vol_ratio,
                market_phase=market_phase,
                confidence=confidence,
                guidance=guidance,
            )

            logger.info(
                f"Regime: {regime.value} (ADX={adx:.1f}, vol_ratio={vol_ratio:.2f}x, "
                f"phase={market_phase})"
            )
            return result

        except Exception as e:
            logger.warning(f"Regime classification failed: {e}")
            return None
