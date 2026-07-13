"""Mutable trade decision context passed through pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TradeContext:
    symbol: str
    is_crypto: bool = False
    direction: str = ""
    confidence: float = 0.0
    actual_rr: float = 0.0
    current_price: float = 0.0
    trade_signal: Any = None
    market_data: Dict[str, Any] = field(default_factory=dict)
    analysis_results: Dict[str, Any] = field(default_factory=dict)
    pd_analysis: Any = None
    d1_bias: str = ""
    h4_bias: str = ""
    m15_bias: str = ""
    regime_type: str = ""
    utc_hour: int = 0
    weak_hours: tuple = ()
    is_counter_trend_scalp: bool = False
    is_index: bool = False
    off_hours_mode: bool = False
    post_cooldown: bool = False
    amd_phase: str = ""
    order_type: str = "market"
    trade_type: str = "intraday"
    m15_opposes: bool = False
    confluence_count: int = 0
    relative_volume: float = 1.0
    scaling_aggressive: bool = False
    gate_path: List[str] = field(default_factory=list)
    confidence_adjustments: List[str] = field(default_factory=list)
    block_reason: Optional[str] = None

    def apply_outcome(self, outcome) -> None:
        """Apply non-blocking gate mutations (confidence caps)."""
        from .gate_outcome import GateOutcome

        if not isinstance(outcome, GateOutcome):
            return
        self.gate_path.extend(outcome.gate_path)
        if outcome.confidence_cap is not None:
            old = self.confidence
            self.confidence = min(self.confidence, outcome.confidence_cap)
            if self.trade_signal is not None and hasattr(self.trade_signal, "confidence"):
                self.trade_signal.confidence = min(
                    float(getattr(self.trade_signal, "confidence", self.confidence)),
                    outcome.confidence_cap,
                )
            if old != self.confidence:
                self.confidence_adjustments.append(
                    f"{outcome.gate_path[-1] if outcome.gate_path else 'gate'}: "
                    f"{old:.0%}->{self.confidence:.0%}"
                )
        if outcome.confidence_delta is not None:
            old = self.confidence
            self.confidence = max(0.40, self.confidence - outcome.confidence_delta)
            if self.trade_signal is not None and hasattr(self.trade_signal, "confidence"):
                self.trade_signal.confidence = self.confidence
            if old != self.confidence:
                self.confidence_adjustments.append(
                    f"session_penalty: {old:.0%}->{self.confidence:.0%}"
                )

    @classmethod
    def from_signal(
        cls,
        *,
        symbol: str,
        trade_signal: Any,
        market_data: dict,
        analysis_results: dict,
        current_price: float,
        is_crypto: bool = False,
        pd_analysis: Any = None,
        off_hours_mode: bool = False,
        post_cooldown: bool = False,
        utc_hour: int = 0,
        weak_hours: tuple = (),
        is_index: bool = False,
        is_counter_trend_scalp: bool = False,
        actual_rr: float = 0.0,
    ) -> "TradeContext":
        d1 = (market_data.get("d1_bias") or "").lower()
        h4 = (market_data.get("h4_bias") or "").lower()
        m15 = (market_data.get("m15_bias") or "").lower()
        regime_data = market_data.get("regime", {}) if market_data else {}
        regime_type = (
            regime_data.get("regime", "").lower()
            if isinstance(regime_data, dict)
            else ""
        )
        direction = getattr(trade_signal, "direction", "") or ""
        return cls(
            symbol=symbol,
            is_crypto=is_crypto,
            direction=direction.lower(),
            confidence=float(getattr(trade_signal, "confidence", 0.0) or 0.0),
            actual_rr=actual_rr,
            current_price=current_price,
            trade_signal=trade_signal,
            market_data=market_data or {},
            analysis_results=analysis_results or {},
            pd_analysis=pd_analysis,
            d1_bias=d1,
            h4_bias=h4,
            m15_bias=m15,
            regime_type=regime_type,
            utc_hour=utc_hour,
            weak_hours=weak_hours,
            is_counter_trend_scalp=is_counter_trend_scalp,
            is_index=is_index,
            off_hours_mode=off_hours_mode,
            post_cooldown=post_cooldown,
            amd_phase=(getattr(trade_signal, "amd_phase", "") or "").lower(),
            order_type=getattr(trade_signal, "order_type", "market") or "market",
            trade_type=getattr(trade_signal, "trade_type", "intraday") or "intraday",
        )
