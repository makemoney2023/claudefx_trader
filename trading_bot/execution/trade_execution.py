"""Live trade execution coordinator — position conflicts, order normalization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from ..config import settings
from ..execution.scaling_position_sizer import verify_post_sizing_risk
from ..services.gate_outcome import GateOutcome
from ..services.trade_reservations import ReservationState
from ..utils.logging import get_logger
from ..utils.win_optimization import order_type_matches_direction

logger = get_logger(__name__)


def check_position_conflicts(
    existing_positions: List[Any],
    direction: str,
) -> GateOutcome:
    """Block opposite-direction conflict or same-direction stacking."""
    if not existing_positions:
        return GateOutcome.pass_through("position_check")

    opposite_dir = "short" if direction == "long" else "long"
    conflicting = [p for p in existing_positions if p.direction == opposite_dir]
    if conflicting:
        return GateOutcome.block(
            gate_id="position_conflict",
            reason=f"Opposite {opposite_dir} position open (ticket={conflicting[0].ticket})",
            stage="position_conflict",
        )

    same_dir = [p for p in existing_positions if p.direction == direction]
    if same_dir:
        return GateOutcome.block(
            gate_id="position_stacking",
            reason="Same-direction position already open",
            stage="position_stacking",
        )

    return GateOutcome.pass_through("position_check")


def auto_convert_to_pending(
    order_type: str,
    direction: str,
    entry_price: float,
    current_price: float,
) -> str:
    """Convert market to limit/stop when entry differs from market."""
    if order_type != "market" or not entry_price or current_price <= 0:
        return order_type
    price_diff_pct = abs(entry_price - current_price) / current_price
    if price_diff_pct <= 0.001:
        return order_type
    if direction == "long":
        return "buy_limit" if entry_price < current_price else "buy_stop"
    return "sell_limit" if entry_price > current_price else "sell_stop"


def fix_limit_stop_labels(
    order_type: str,
    entry_price: float,
    current_price: float,
) -> str:
    """Fix mislabeled limit/stop relative to current price."""
    if order_type not in ("buy_limit", "sell_limit", "buy_stop", "sell_stop"):
        return order_type
    if not entry_price or current_price <= 0:
        return order_type
    if order_type == "buy_limit" and entry_price > current_price * 1.001:
        return "buy_stop"
    if order_type == "sell_limit" and entry_price < current_price * 0.999:
        return "sell_stop"
    if order_type == "buy_stop" and entry_price < current_price * 0.999:
        return "buy_limit"
    if order_type == "sell_stop" and entry_price > current_price * 1.001:
        return "sell_limit"
    return order_type


def validate_limit_zone(
    order_type: str,
    retrace_pct: Optional[float],
) -> GateOutcome:
    """ICT zone validation for limit orders."""
    if order_type not in ("buy_limit", "sell_limit") or retrace_pct is None:
        return GateOutcome.pass_through("limit_zone")

    if order_type == "buy_limit" and retrace_pct > 0.70:
        return GateOutcome.block(
            gate_id="zone_block",
            reason=f"buy_limit in premium zone ({retrace_pct:.0%})",
            stage="limit_zone",
        )
    if order_type == "sell_limit" and retrace_pct < 0.30:
        return GateOutcome.block(
            gate_id="zone_block",
            reason=f"sell_limit in discount zone ({retrace_pct:.0%})",
            stage="limit_zone",
        )
    outcome = GateOutcome.pass_through("limit_zone")
    if order_type == "buy_limit" and retrace_pct > 0.55:
        outcome.confidence_cap = 0.60
    elif order_type == "sell_limit" and retrace_pct < 0.45:
        outcome.confidence_cap = 0.60
    return outcome


def resolve_premium_discount(analysis_results: dict) -> Tuple[Optional[str], Optional[float]]:
    try:
        pd_data = analysis_results.get("premium_discount", {})
        if isinstance(pd_data, dict):
            return pd_data.get("current_zone"), pd_data.get("retracement_percent")
        if hasattr(pd_data, "current_zone"):
            zone = (
                pd_data.current_zone.value
                if hasattr(pd_data.current_zone, "value")
                else str(pd_data.current_zone)
            )
            return zone, getattr(pd_data, "retracement_percent", None)
    except Exception:
        pass
    return None, None


@dataclass
class ExecutionPrepResult:
    order_type: str
    entry_price: float
    blocked: bool = False
    gate_id: str = ""
    reason: str = ""
    confidence_cap: Optional[float] = None


@dataclass
class TickRefineResult:
    allowed: bool
    adjusted_entry: Optional[float] = None
    reason: str = ""


@dataclass
class ExecutionResult:
    blocked: bool = False
    gate_id: str = ""
    reason: str = ""
    dry_run: bool = False
    broker_result: Any = None
    order_type: str = ""
    entry_price: float = 0.0
    final_sl: float = 0.0
    final_tp: float = 0.0
    final_entry: float = 0.0
    symbol_spec: Any = None
    position_lots: float = 0.0


def adjust_sl_for_spread(sl: float, direction: str, spread: float) -> float:
    """Widen SL by half the spread to reduce premature stop-outs."""
    if spread <= 0 or not sl:
        return sl
    if direction == "long":
        return sl - (spread * 0.5)
    return sl + (spread * 0.5)


def pending_expiration_minutes(*, is_crypto: bool, session_remaining: int) -> int:
    if is_crypto:
        return 480
    return min(max(session_remaining, 60), 480)


def evaluate_tick_refine(
    *,
    direction: str,
    entry_price: float,
    current_price: float,
    tick_bid: float,
    tick_ask: float,
    final_sl: float,
    final_tp: float,
    atr_14: float,
    min_rr: float = 1.5,
) -> TickRefineResult:
    tick_price = tick_ask if direction == "long" else tick_bid
    reference_entry = entry_price or current_price
    tick_dev = abs(tick_price - reference_entry)
    tick_max_dev = (
        atr_14 * 0.5 if atr_14 > 0 else reference_entry * 0.003
    )
    if tick_dev <= tick_max_dev or tick_max_dev <= 0:
        return TickRefineResult(allowed=True)

    new_entry = tick_price
    sl_dist = abs(new_entry - final_sl)
    tp_dist = abs(final_tp - new_entry)
    new_rr = tp_dist / sl_dist if sl_dist > 0 else 0.0
    if new_rr < min_rr:
        return TickRefineResult(
            allowed=False,
            reason=f"Tick refine blocked: R:R dropped to {new_rr:.2f}",
        )
    return TickRefineResult(allowed=True, adjusted_entry=new_entry)


class ExecutionCoordinator:
    """Prepares order type and pre-flight execution checks."""

    def prepare_order(
        self,
        *,
        trade_signal: Any,
        current_price: float,
        existing_positions: Optional[List[Any]],
        analysis_results: dict,
    ) -> ExecutionPrepResult:
        direction = trade_signal.direction
        entry_price = trade_signal.entry_price or current_price
        order_type = getattr(trade_signal, "order_type", "market") or "market"

        if existing_positions:
            conflict = check_position_conflicts(existing_positions, direction)
            if conflict.blocked:
                return ExecutionPrepResult(
                    order_type=order_type,
                    entry_price=entry_price,
                    blocked=True,
                    gate_id=conflict.gate_id,
                    reason=conflict.reason,
                )

        order_type = auto_convert_to_pending(
            order_type, direction, trade_signal.entry_price or 0, current_price
        )
        order_type = fix_limit_stop_labels(order_type, entry_price, current_price)
        trade_signal.order_type = order_type

        if not order_type_matches_direction(order_type, direction):
            return ExecutionPrepResult(
                order_type=order_type,
                entry_price=entry_price,
                blocked=True,
                gate_id="direction_order_type_mismatch",
                reason=(
                    f"order_type={order_type} incompatible with direction={direction}"
                ),
            )

        _, retrace = resolve_premium_discount(analysis_results)
        zone_outcome = validate_limit_zone(order_type, retrace)
        if zone_outcome.blocked:
            return ExecutionPrepResult(
                order_type=order_type,
                entry_price=entry_price,
                blocked=True,
                gate_id=zone_outcome.gate_id,
                reason=zone_outcome.reason,
            )

        cap = zone_outcome.confidence_cap
        if cap is not None:
            trade_signal.confidence = min(float(trade_signal.confidence), cap)

        return ExecutionPrepResult(
            order_type=order_type,
            entry_price=entry_price,
            confidence_cap=cap,
        )

    async def execute(
        self,
        *,
        bot: Any,
        symbol: str,
        trade_signal: Any,
        order_type: str,
        entry_price: float,
        current_price: float,
        position_size: Any,
        size_result: Any,
        account_info: Any,
        market_data: dict,
        is_crypto: bool,
        trade_reservation: Any,
    ) -> ExecutionResult:
        """Post-prep risk checks, tick refine, and broker order placement."""
        from ..config import get_symbol_spec

        final_sl = trade_signal.stop_loss
        final_tp = trade_signal.take_profit
        try:
            import MetaTrader5 as mt5

            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if tick and tick.ask > 0 and tick.bid > 0:
                spread = tick.ask - tick.bid
                adjusted = adjust_sl_for_spread(
                    final_sl or 0.0, trade_signal.direction, spread
                )
                if adjusted != final_sl and final_sl:
                    logger.info(
                        f"[SPREAD-BUF] {symbol}: SL adjusted by 0.5x spread ({spread:.5f}): "
                        f"{trade_signal.stop_loss:.5f} -> {adjusted:.5f}"
                    )
                    final_sl = adjusted
        except Exception as exc:
            logger.debug(f"[SPREAD-BUF] Could not adjust SL for spread: {exc}")

        symbol_spec = get_symbol_spec(symbol)
        final_entry = trade_signal.entry_price or current_price

        verified_lots, _, verify_reason = verify_post_sizing_risk(
            final_lots=position_size.lots,
            target_lots=size_result.lots,
            entry_price=final_entry,
            stop_loss=final_sl,
            symbol=symbol,
        )
        if verify_reason:
            logger.warning(f"[POST-SIZING] {symbol}: blocked — {verify_reason}")
            return ExecutionResult(
                blocked=True,
                gate_id="post_sizing_risk",
                reason=verify_reason,
                order_type=order_type,
                entry_price=entry_price,
                final_sl=final_sl or 0.0,
                final_tp=final_tp or 0.0,
                final_entry=final_entry,
                symbol_spec=symbol_spec,
            )
        position_size.lots = verified_lots

        final_lots, _, final_risk_reason = bot._enforce_final_risk_before_order(
            symbol=symbol,
            entry=final_entry,
            stop_loss=final_sl,
            lots=position_size.lots,
            account_equity=account_info.equity,
            symbol_spec=symbol_spec,
            risk_fraction=(
                size_result.risk_percent if hasattr(size_result, "risk_percent") else None
            ),
        )
        if final_risk_reason:
            logger.warning(f"[FINAL-RISK] {symbol}: blocked — {final_risk_reason}")
            print(f"[FINAL-RISK] {symbol}: BLOCKED — {final_risk_reason}", flush=True)
            return ExecutionResult(
                blocked=True,
                gate_id="final_risk_block",
                reason=final_risk_reason,
                order_type=order_type,
                entry_price=entry_price,
                final_sl=final_sl or 0.0,
                final_tp=final_tp or 0.0,
                final_entry=final_entry,
                symbol_spec=symbol_spec,
            )
        position_size.lots = final_lots

        if settings.trading.dry_run:
            print(
                f"[DRY-RUN] Would place {order_type} {trade_signal.direction.upper()} "
                f"{symbol} @ {trade_signal.entry_price:.5f} "
                f"(SL: {final_sl:.5f}, TP: {final_tp:.5f}, "
                f"Lots: {position_size.lots}, Conf: {trade_signal.confidence:.0%})",
                flush=True,
            )
            logger.info(
                f"[DRY-RUN] {symbol}: {order_type} {trade_signal.direction} "
                f"@ {trade_signal.entry_price}, SL={final_sl}, TP={final_tp}, "
                f"lots={position_size.lots}, confidence={trade_signal.confidence:.0%}"
            )
            return ExecutionResult(
                dry_run=True,
                order_type=order_type,
                entry_price=entry_price,
                final_sl=final_sl or 0.0,
                final_tp=final_tp or 0.0,
                final_entry=final_entry,
                symbol_spec=symbol_spec,
                position_lots=position_size.lots,
            )

        broker_result = None
        if order_type == "market":
            tick_refine = await self._run_tick_refine(
                bot=bot,
                symbol=symbol,
                trade_signal=trade_signal,
                current_price=current_price,
                market_data=market_data,
                final_sl=final_sl,
                final_tp=final_tp,
            )
            if not tick_refine.allowed:
                print(
                    f"[TICK-REFINE] {symbol}: BLOCKED — {tick_refine.reason}",
                    flush=True,
                )
                return ExecutionResult(
                    blocked=True,
                    gate_id="tick_refine_block",
                    reason=tick_refine.reason or "Tick refine blocked",
                    order_type=order_type,
                    entry_price=entry_price,
                    final_sl=final_sl or 0.0,
                    final_tp=final_tp or 0.0,
                    final_entry=trade_signal.entry_price or current_price,
                    symbol_spec=symbol_spec,
                )
            if tick_refine.adjusted_entry is not None:
                trade_signal.entry_price = tick_refine.adjusted_entry
                final_entry = tick_refine.adjusted_entry

            logger.info(f"Executing MARKET order (AMD: {trade_signal.amd_phase})")
            broker_result = await bot._place_market_with_final_risk(
                symbol=symbol,
                direction=trade_signal.direction,
                lots=position_size.lots,
                stop_loss=final_sl,
                take_profit=final_tp,
                account_equity=account_info.equity,
                symbol_spec=symbol_spec,
                risk_fraction=(
                    size_result.risk_percent
                    if hasattr(size_result, "risk_percent")
                    else None
                ),
                comment="ICT_Bot",
            )
            # None means final-risk cap blocked before broker send.
            # A failed OrderResult is a broker rejection — pass through for reconcile.
            if broker_result is None:
                return ExecutionResult(
                    blocked=True,
                    gate_id="final_risk_block",
                    reason="Final risk blocked market order",
                    order_type=order_type,
                    entry_price=entry_price,
                    final_sl=final_sl or 0.0,
                    final_tp=final_tp or 0.0,
                    final_entry=trade_signal.entry_price or current_price,
                    symbol_spec=symbol_spec,
                )
        elif order_type in ("buy_limit", "sell_limit", "buy_stop", "sell_stop"):
            broker_result = await self._place_pending_order(
                bot=bot,
                symbol=symbol,
                trade_signal=trade_signal,
                order_type=order_type,
                entry_price=entry_price,
                position_size=position_size,
                size_result=size_result,
                final_sl=final_sl,
                final_tp=final_tp,
                is_crypto=is_crypto,
                trade_reservation=trade_reservation,
            )
        else:
            logger.info(f"📈 Executing MARKET order (fallback from {order_type})")
            broker_result = await bot.order_manager.place_market_order(
                symbol=symbol,
                direction=trade_signal.direction,
                volume=position_size.lots,
                stop_loss=final_sl,
                take_profit=final_tp,
                comment="ICT_Bot",
            )
            order_type = "market"

        if (
            broker_result
            and getattr(broker_result, "success", False)
            and getattr(broker_result, "converted_to_market", False)
        ):
            order_type = "market"
            if getattr(broker_result, "final_order_type", None) in ("buy", "sell"):
                trade_signal.order_type = "market"

        # Transfer reservation immediately on market fills so cycle cancel
        # cannot release a still-RESERVED slot after broker success.
        if (
            broker_result
            and getattr(broker_result, "success", False)
            and trade_reservation is not None
            and getattr(trade_reservation, "state", None) == ReservationState.RESERVED
        ):
            is_market_fill = (
                order_type == "market"
                or getattr(broker_result, "converted_to_market", False)
                or getattr(broker_result, "final_order_type", "") in ("buy", "sell")
            )
            ticket = getattr(broker_result, "ticket", None) or getattr(
                broker_result, "order_id", None
            )
            if is_market_fill and ticket and hasattr(bot, "reservation_ledger"):
                bot.reservation_ledger.transfer_to_position(trade_reservation, ticket)

        return ExecutionResult(
            broker_result=broker_result,
            order_type=order_type,
            entry_price=entry_price,
            final_sl=final_sl or 0.0,
            final_tp=final_tp or 0.0,
            final_entry=final_entry,
            symbol_spec=symbol_spec,
            position_lots=position_size.lots,
        )

    async def _run_tick_refine(
        self,
        *,
        bot: Any,
        symbol: str,
        trade_signal: Any,
        current_price: float,
        market_data: dict,
        final_sl: float,
        final_tp: float,
    ) -> TickRefineResult:
        try:
            tick_info = await bot.mt5_client.get_symbol_info(symbol)
            if not tick_info or getattr(tick_info, "ask", 0) <= 0:
                return TickRefineResult(allowed=True)
            refine = evaluate_tick_refine(
                direction=trade_signal.direction,
                entry_price=trade_signal.entry_price or current_price,
                current_price=current_price,
                tick_bid=tick_info.bid,
                tick_ask=tick_info.ask,
                final_sl=final_sl,
                final_tp=final_tp,
                atr_14=market_data.get("atr_14", 0),
            )
            if refine.adjusted_entry is not None:
                logger.info(
                    f"[TICK-REFINE] {symbol}: Entry adjusted to live tick "
                    f"{refine.adjusted_entry:.5f} (was {current_price:.5f})"
                )
            elif not refine.allowed:
                logger.warning(f"[TICK-REFINE] {symbol}: {refine.reason}")
            return refine
        except Exception as exc:
            logger.debug(f"[TICK-REFINE] Error for {symbol}: {exc}")
            return TickRefineResult(allowed=True)

    async def _place_pending_order(
        self,
        *,
        bot: Any,
        symbol: str,
        trade_signal: Any,
        order_type: str,
        entry_price: float,
        position_size: Any,
        size_result: Any,
        final_sl: float,
        final_tp: float,
        is_crypto: bool,
        trade_reservation: Any,
    ) -> Any:
        session = (
            bot.kill_zone_checker.get_current_session()
            if bot.kill_zone_checker
            else None
        )
        session_remaining = int(getattr(session, "minutes_remaining", 240)) if session else 240
        expiration_minutes = pending_expiration_minutes(
            is_crypto=is_crypto,
            session_remaining=session_remaining,
        )
        logger.info(
            f"⏳ Placing PENDING {order_type} order @ {entry_price}, "
            f"expires in {expiration_minutes}min"
        )

        existing_orders = [
            order
            for order in bot.pending_order_manager.get_active_orders(symbol=symbol)
            if order.direction == trade_signal.direction
        ]
        for old_order in existing_orders:
            old_success = await bot._cancel_pending_for_replacement(old_order)
            if old_success:
                old_hash = bot._get_signal_hash(
                    symbol, old_order.direction, old_order.price
                )
                bot._recent_signal_hashes.discard(old_hash)
                bot._signal_hash_expiry.pop(old_hash, None)
                print(
                    f"[PENDING] Cancelled old #{old_order.ticket} {symbol} "
                    f"{old_order.direction} @ {old_order.price} — replaced by newer "
                    f"signal @ {entry_price}",
                    flush=True,
                )

        result = await bot.order_manager.place_pending_order(
            symbol=symbol,
            direction=trade_signal.direction,
            order_type=order_type,
            volume=position_size.lots,
            price=entry_price,
            stop_loss=final_sl,
            take_profit=final_tp,
            expiration_minutes=expiration_minutes,
            comment="ICT_Bot_Pending",
        )
        # PRICE-FIX may convert limit/stop to a market DEAL — do not track as pending.
        if getattr(result, "converted_to_market", False):
            logger.info(
                f"[PRICE-FIX] {symbol}: {order_type} filled as market "
                f"({getattr(result, 'final_order_type', 'market')}) — skipping pending track"
            )
            return result

        if result.success and (result.ticket or result.order_id):
            await bot.pending_order_manager.add_order(
                ticket=result.ticket or result.order_id,
                symbol=symbol,
                order_type=order_type,
                direction=trade_signal.direction,
                volume=position_size.lots,
                price=entry_price,
                stop_loss=final_sl,
                take_profit=final_tp,
                expiration_minutes=expiration_minutes,
                risk_percent=(
                    size_result.risk_percent if hasattr(size_result, "risk_percent") else None
                ),
                reservation_id=(
                    trade_reservation.reservation_id if trade_reservation else None
                ),
            )
            if trade_reservation:
                bot.reservation_ledger.transfer_to_pending(
                    trade_reservation,
                    result.ticket or result.order_id,
                )
        return result
