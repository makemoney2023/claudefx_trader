"""
Structured confidence modifiers for live and replay parity.

Collects boosts/penalties/caps into ConfidenceDecision for telemetry
and consistent application order (caps cannot be undone by later boosts).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..utils.win_optimization import ConfidenceDecision, build_confidence_decision


MAX_SECONDARY_BOOST = 0.10


@dataclass
class SecondaryModifierContext:
    direction: str
    symbol: str
    retail_contrarian: Optional[str] = None
    vix_risk_mode: Optional[str] = None
    social_sentiment: Optional[Dict[str, Any]] = None
    options_flow: Optional[Dict[str, Any]] = None
    intermarket: Optional[Dict[str, Any]] = None
    seasonal: Optional[Dict[str, Any]] = None
    bond_yields: Optional[Dict[str, Any]] = None
    btc_dominance: Optional[Dict[str, Any]] = None
    silver_bullet_ready: bool = False
    at_breaker_block: bool = False
    current_price: float = 0.0


def apply_secondary_modifiers(
    base_confidence: float,
    ctx: SecondaryModifierContext,
) -> ConfidenceDecision:
    """Apply GATE 2b–2i + SB/breaker boosts with +10% aggregate cap."""
    boosts: List[Tuple[str, float]] = []
    penalties: List[Tuple[str, float]] = []

    if ctx.retail_contrarian:
        if ctx.direction == ctx.retail_contrarian:
            boosts.append(("retail_contrarian", 0.05))
        else:
            penalties.append(("retail_with_crowd", -0.10))

    if ctx.vix_risk_mode == "risk_off":
        if ctx.symbol in ("USDJPY", "USDCHF") and ctx.direction == "long":
            penalties.append(("vix_risk_off_fx_long", -0.05))
        elif ctx.symbol == "XAUUSD" and ctx.direction == "long":
            boosts.append(("vix_risk_off_gold", 0.05))
    elif ctx.vix_risk_mode == "risk_on":
        if ctx.symbol in ("AUDUSD", "NZDUSD") and ctx.direction == "long":
            boosts.append(("vix_risk_on_commodity", 0.03))

    social = ctx.social_sentiment or {}
    if social.get("contrarian_signal") == ctx.direction and social.get("volume") == "high":
        boosts.append(("social_contrarian", 0.03))

    options = ctx.options_flow or {}
    flow = options.get("flow")
    if flow == "bullish" and ctx.direction == "long":
        boosts.append(("options_flow", 0.02))
    elif flow == "bearish" and ctx.direction == "short":
        boosts.append(("options_flow", 0.02))

    inter = ctx.intermarket or {}
    risk_env = inter.get("risk_environment") or ""
    if "strong_risk_on" in risk_env:
        if ctx.symbol in ("AUDUSD", "NZDUSD") and ctx.direction == "long":
            boosts.append(("intermarket_risk_on", 0.05))
        elif ctx.symbol in ("USDJPY", "USDCHF") and ctx.direction == "short":
            penalties.append(("intermarket_risk_on_safe_short", -0.05))
    elif "strong_risk_off" in risk_env:
        if ctx.symbol == "XAUUSD" and ctx.direction == "long":
            boosts.append(("intermarket_risk_off_gold", 0.05))
        elif ctx.symbol in ("USDJPY", "USDCHF") and ctx.direction == "short":
            boosts.append(("intermarket_risk_off_safe", 0.03))

    seasonal = ctx.seasonal or {}
    accuracy = seasonal.get("historical_accuracy", 0) or 0
    bias = seasonal.get("current_month_bias", "unknown")
    if accuracy >= 65 and bias != "unknown":
        aligned = (ctx.direction == "long" and bias == "bullish") or (
            ctx.direction == "short" and bias == "bearish"
        )
        if aligned:
            boosts.append(("seasonal", 0.03 if accuracy >= 75 else 0.02))

    yields = ctx.bond_yields or {}
    if yields.get("eurusd_bias") and "EUR" in ctx.symbol:
        spread = abs(yields.get("spread", 0) or 0)
        if spread > 1.5:
            ybias = yields.get("eurusd_bias")
            aligned = (ctx.direction == "long" and ybias == "bullish") or (
                ctx.direction == "short" and ybias == "bearish"
            )
            if aligned:
                boosts.append(("yield_spread", 0.02))
            else:
                penalties.append(("yield_spread_conflict", -0.03))

    btc_dom = ctx.btc_dominance or {}
    if ctx.symbol == "BTCUSD" and btc_dom.get("trend") == "rising" and ctx.direction == "long":
        boosts.append(("btc_dominance", 0.03))
    elif ctx.symbol not in ("BTCUSD", "") and btc_dom:
        alt_sent = btc_dom.get("altcoin_sentiment", "neutral")
        if (ctx.direction == "long" and alt_sent == "bullish") or (
            ctx.direction == "short" and alt_sent == "bearish"
        ):
            boosts.append(("altcoin_sentiment", 0.03))

    decision = build_confidence_decision(base_confidence, boosts=boosts, penalties=penalties)

    net_boost = decision.final - base_confidence
    if net_boost > MAX_SECONDARY_BOOST:
        decision = build_confidence_decision(
            base_confidence,
            boosts=boosts,
            penalties=penalties,
            caps=[("secondary_boost_cap", base_confidence + MAX_SECONDARY_BOOST)],
        )

    if ctx.silver_bullet_ready and decision.final < 0.9:
        decision = build_confidence_decision(
            decision.final,
            boosts=[("silver_bullet", 0.10)],
            penalties=[],
            caps=decision.caps,
        )

    if ctx.at_breaker_block:
        decision = build_confidence_decision(
            decision.final,
            boosts=[("breaker_block", 0.10)],
            penalties=[],
            caps=decision.caps,
        )

    return decision


def confidence_decision_to_dict(decision: ConfidenceDecision) -> Dict[str, Any]:
    return {
        "base": round(decision.base, 4),
        "boosts": [{"name": n, "delta": round(d, 4)} for n, d in decision.boosts],
        "penalties": [{"name": n, "delta": round(d, 4)} for n, d in decision.penalties],
        "caps": [{"name": n, "cap": round(c, 4)} for n, c in decision.caps],
        "final": round(decision.final, 4),
    }
