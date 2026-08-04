"""Post-execution fill handling — positions, DB, notifications."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from ..execution.position_manager import Position
from ..services.live_trade_gates import compute_booked_risk_percent
from ..utils.logging import get_logger
from ..utils.notifications import notify, NotificationType
from ..utils.win_optimization import classify_a_plus
from ..api.websocket import broadcast_trade_update

logger = get_logger(__name__)


def _is_market_fill(result: Any, order_type: str) -> bool:
    """True when broker fill is (or was converted to) a market DEAL."""
    if getattr(result, "converted_to_market", False):
        return True
    final_ot = (getattr(result, "final_order_type", None) or "").lower()
    if final_ot in ("buy", "sell"):
        return True
    return order_type not in ("buy_limit", "sell_limit", "buy_stop", "sell_stop")


def _resolve_ticket_from_positions(
    positions: list,
    *,
    reported_ticket: Optional[int],
    order_id: Optional[int],
    fill_volume: Optional[float],
) -> Optional[int]:
    """Match a live position ticket when broker order/deal identity differs."""
    if not positions:
        return None
    tickets = {getattr(p, "ticket", None) for p in positions}
    if reported_ticket and reported_ticket in tickets:
        return reported_ticket
    if order_id and order_id in tickets:
        return order_id
    if len(positions) == 1:
        return positions[0].ticket
    if fill_volume is not None:
        matches = [
            p for p in positions
            if abs(float(getattr(p, "volume", 0) or 0) - float(fill_volume)) < 0.001
        ]
        if len(matches) == 1:
            return matches[0].ticket
    return None


class TradeFillHandler:
    """Handles broker success/failure after ExecutionCoordinator.execute()."""

    @staticmethod
    async def handle_result(
        bot: Any,
        *,
        symbol: str,
        result: Any,
        order_type: str,
        entry_price: float,
        current_price: float,
        trade_signal: Any,
        position_size: Any,
        size_result: Any,
        account_info: Any,
        trade_reservation: Any,
        signal_hash: str,
        final_sl: float,
        final_tp: float,
        final_entry: float,
        judge_verdict: Optional[dict],
        confluence_factors,
        confluence_count,
        setup_grade,
        take_profit_levels: Optional[dict],
        save_trade_to_db,
    ) -> None:
        """Record fills, track positions, and handle execution failures."""
        if result.success:
            _risk_pct = compute_booked_risk_percent(
                position_size.lots,
                final_entry,
                final_sl,
                symbol,
                account_info.balance,
            )
            if _risk_pct <= 0:
                _risk_pct = (
                    size_result.risk_percent
                    if hasattr(size_result, 'risk_percent')
                    else bot.risk_manager.risk_per_trade
                )
            if trade_reservation:
                trade_reservation.risk_percent = _risk_pct
                bot.reservation_ledger.commit_risk(trade_reservation)
            print(
                f"[RISK] {symbol}: Daily risk +{_risk_pct*100:.1f}%, "
                f"total: {bot.risk_manager.daily_risk_used*100:.1f}%/"
                f"{bot.risk_manager.max_daily_risk*100:.0f}%",
                flush=True,
            )
            
            # Gap 21: Track signal hash to prevent duplicates
            bot._recent_signal_hashes.add(signal_hash)
            bot._signal_hash_expiry[signal_hash] = datetime.now(timezone.utc)
            
            logger.info(f"✓ Trade executed: {trade_signal.direction.upper()} {symbol}")
            logger.info(f"  Ticket: {result.ticket}, Fill Price: {result.fill_price}")

            # Partial-fill policy + broker SL/TP drift check
            from .fill_validation import evaluate_partial_fill, validate_broker_protections

            _req_lots = float(getattr(position_size, "lots", 0) or 0)
            _fill_lots = float(getattr(result, "fill_volume", 0) or _req_lots)
            if getattr(result, "partial_fill", False) or (
                _req_lots > 0 and _fill_lots > 0 and _fill_lots + 1e-9 < _req_lots
            ):
                _pf = evaluate_partial_fill(
                    requested_lots=_req_lots, filled_lots=_fill_lots
                )
                if not _pf.accept:
                    logger.error(f"[PARTIAL-FILL] {symbol}: {_pf.reason}")
                    await bot._record_terminal_decision(
                        "execution_failure",
                        symbol,
                        direction=trade_signal.direction,
                        entry=result.fill_price or current_price,
                        sl=final_sl or 0.0,
                        tp=final_tp or 0.0,
                        confidence=trade_signal.confidence,
                        reason=_pf.reason,
                        details={"gate_id": _pf.gate_id},
                    )
                    return

            _prot = validate_broker_protections(
                direction=trade_signal.direction,
                intended_sl=final_sl or trade_signal.stop_loss or 0.0,
                intended_tp=final_tp or trade_signal.take_profit or 0.0,
                broker_sl=getattr(result, "broker_sl", None),
                broker_tp=getattr(result, "broker_tp", None),
                tolerance_price=abs(
                    (final_sl or trade_signal.stop_loss or 0.0)
                    - (trade_signal.entry_price or current_price or 0.0)
                ) * 0.05
                if (final_sl or trade_signal.stop_loss)
                else 0.0,
            )
            if not _prot.ok:
                logger.warning(f"[PROTECTION] {symbol}: {_prot.reason}")
            
            # Gap 57: Verify order actually exists in MT5
            # Only verify for market orders — pending orders won't appear in positions yet
            # PRICE-FIX conversions must be treated as market fills.
            is_market = _is_market_fill(result, order_type)
            is_pending_order = not is_market
            if result.ticket and not bot.mt5_client.is_simulation and is_market:
                await asyncio.sleep(0.5)  # Brief delay for MT5 to process
                positions = await bot.mt5_client.get_positions(symbol=symbol)

                resolved = _resolve_ticket_from_positions(
                    positions or [],
                    reported_ticket=result.ticket,
                    order_id=getattr(result, "order_id", None),
                    fill_volume=getattr(result, "fill_volume", None),
                )
                if resolved:
                    if resolved != result.ticket:
                        logger.warning(
                            f"[TICKET-RESOLVE] {symbol}: reported ticket "
                            f"{result.ticket} → position ticket {resolved}"
                        )
                        result.ticket = resolved
                    logger.info(f"  ✓ Position verified in MT5 (ticket={result.ticket})")
                else:
                    logger.error(
                        f"⚠ Order reported success but position {result.ticket} not found in MT5! "
                        f"Retaining reservation for reconcile; sync_with_mt5 may adopt later."
                    )
                    await bot._record_terminal_decision(
                        "execution_failure",
                        symbol,
                        direction=trade_signal.direction,
                        entry=result.fill_price or current_price,
                        sl=final_sl or 0.0,
                        tp=final_tp or 0.0,
                        confidence=trade_signal.confidence,
                        reason=f"Order success but position {result.ticket} not found in MT5",
                        details={"ticket": result.ticket, "unverified_fill": True},
                    )
                    # Keep ownership so reservation scope cannot free the slot
                    if trade_reservation and hasattr(bot, "reservation_ledger"):
                        from ..services.trade_reservations import ReservationState
                        if trade_reservation.state == ReservationState.RESERVED:
                            bot.reservation_ledger.transfer_to_position(
                                trade_reservation, result.ticket
                            )
                    return
                
            elif is_pending_order:
                logger.info(f"  ⏳ Pending order placed — will verify when filled")
            
            # Track position — but ONLY for market orders (immediately filled)
            # Pending orders (buy_limit, sell_limit, etc.) are tracked by pending_order_manager
            # and will be picked up by sync_with_mt5 when they fill
            if result.ticket:
                # Prefer spread-adjusted finals from execution; fall back to signal
                tracked_sl = (
                    final_sl if final_sl and final_sl > 0
                    else (trade_signal.stop_loss if trade_signal.stop_loss and trade_signal.stop_loss > 0 else None)
                )
                tracked_tp = (
                    final_tp if final_tp and final_tp > 0
                    else (trade_signal.take_profit if trade_signal.take_profit and trade_signal.take_profit > 0 else None)
                )
                if getattr(result, "broker_sl", None):
                    tracked_sl = result.broker_sl
                if getattr(result, "broker_tp", None):
                    tracked_tp = result.broker_tp
                if not tracked_sl:
                    logger.error(f"CRITICAL: Position {result.ticket} has no valid SL! trade_signal.stop_loss={trade_signal.stop_loss}")
                if not tracked_tp:
                    logger.warning(f"Position {result.ticket} has no TP set: trade_signal.take_profit={trade_signal.take_profit}")
                
                if is_pending_order:
                    # =============================================
                    # PENDING ORDER: Do NOT add to position_manager!
                    # MT5's get_positions() doesn't return pending orders,
                    # so sync_with_mt5 would falsely detect them as "closed".
                    # They're already tracked by pending_order_manager.
                    # When they fill, sync_with_mt5 will pick them up as new positions.
                    # =============================================
                    print(f"[PENDING] {symbol}: Pending {order_type} placed (ticket={result.ticket}, entry={entry_price:.5f}, SL={trade_signal.stop_loss}, TP={trade_signal.take_profit})", flush=True)
                    logger.info(f"Pending order {result.ticket} tracked by pending_order_manager (NOT position_manager)")
                    await bot._record_terminal_decision(
                        "pending_placed",
                        symbol,
                        direction=trade_signal.direction,
                        entry=entry_price,
                        sl=trade_signal.stop_loss or 0.0,
                        tp=trade_signal.take_profit or 0.0,
                        confidence=trade_signal.confidence,
                        session=(
                            bot.kill_zone_checker.get_current_session().session_name
                            if bot.kill_zone_checker else ""
                        ),
                        reason=f"Pending {order_type} placed",
                        details={
                            "ticket": result.ticket,
                            "order_type": order_type,
                            "regime": getattr(bot, "_last_regime_by_symbol", {}).get(symbol),
                        },
                    )
                    
                    # Add to activity feed as pending order (not "trade opened")
                    from ..api.routes.activity import add_activity
                    add_activity(
                        "pending_order_placed",
                        f"Pending {order_type.upper()} {trade_signal.direction.upper()} {symbol} @ {entry_price:.5f}",
                        symbol,
                        {
                            "ticket": result.ticket,
                            "order_type": order_type,
                            "direction": trade_signal.direction,
                            "entry_price": entry_price,
                            "stop_loss": trade_signal.stop_loss,
                            "take_profit": trade_signal.take_profit,
                            "lots": position_size.lots,
                            "confidence": trade_signal.confidence
                        }
                    )
                    asyncio.create_task(broadcast_trade_update({
                        "event": "pending_order_placed",
                        "ticket": result.ticket,
                        "symbol": symbol,
                        "order_type": order_type,
                        "direction": trade_signal.direction,
                        "entry_price": entry_price,
                        "stop_loss": trade_signal.stop_loss,
                        "take_profit": trade_signal.take_profit,
                        "lots": position_size.lots,
                        "confidence": trade_signal.confidence
                    }))
                    
                    # Save to database with full analysis context
                    _fp = getattr(trade_signal, "setup_fingerprint", None)
                    if isinstance(_fp, dict):
                        _fp = _fp.get("key")
                    await save_trade_to_db(
                        ticket=result.ticket,
                        symbol=symbol,
                        direction=trade_signal.direction,
                        entry_price=entry_price,
                        stop_loss=trade_signal.stop_loss or 0.0,
                        take_profit=trade_signal.take_profit or 0.0,
                        position_size=position_size.lots,
                        confidence=trade_signal.confidence,
                        reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                        judge_verdict=judge_verdict.get('verdict', 'APPROVE') if judge_verdict else None,
                        judge_reason=judge_verdict.get('reason', '') if judge_verdict else None,
                        judge_risk_flags=judge_verdict.get('risk_flags', []) if judge_verdict else None,
                        trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                        order_type=order_type,
                        amd_phase=getattr(trade_signal, 'amd_phase', 'unknown'),
                        market_structure=getattr(trade_signal, 'market_structure', ''),
                        confluence_factors=confluence_factors if confluence_factors else None,
                        confluence_count=confluence_count if confluence_count else None,
                        ict_concepts={
                            'order_blocks': getattr(trade_signal, 'order_blocks', []),
                            'fvg_zones': getattr(trade_signal, 'fvg_zones', []),
                            'liquidity_targets': getattr(trade_signal, 'liquidity_targets', []),
                            'manipulation_complete': getattr(trade_signal, 'manipulation_complete', False),
                        },
                        timeframe="M15",
                        session_name=bot.kill_zone_checker.get_current_session().session_name if bot.kill_zone_checker else "",
                        risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else bot.risk_manager.risk_per_trade,
                        setup_fingerprint=_fp,
                        regime=getattr(trade_signal, "regime", None),
                    )
                    
                    # Pending orders: do NOT send Telegram notification.
                    # These get cancelled/replaced frequently and would spam
                    # the user. Only notify when a trade actually fills.
                    logger.info(f"Pending order placed for {symbol} — Telegram notification deferred until fill")
                else:
                    # =============================================
                    # MARKET ORDER: Immediately filled, add to position_manager
                    # =============================================
                    _conf = float(getattr(trade_signal, "confidence", 0.0) or 0.0)
                    _a_plus = classify_a_plus(
                        setup_grade.value if hasattr(setup_grade, 'value') else str(setup_grade),
                        confluence_count or 0,
                    )
                    _is_pyramid_child = bool(getattr(trade_signal, "is_pyramid_add", False))
                    _parent_ticket = getattr(trade_signal, "pyramid_parent_ticket", None)
                    position = Position(
                        ticket=result.ticket,
                        symbol=symbol,
                        direction=trade_signal.direction,
                        volume=result.fill_volume or position_size.lots,
                        entry_price=result.fill_price or current_price,
                        stop_loss=tracked_sl or (result.fill_price or current_price),
                        take_profit=tracked_tp or 0.0,
                        open_time=datetime.now(timezone.utc),
                        trade_type=getattr(trade_signal, 'trade_type', 'intraday') or 'intraday',
                        reservation_id=trade_reservation.reservation_id if trade_reservation else None,
                        a_plus=_a_plus,
                        confidence=_conf,
                        pyramid_eligible=(
                            False
                            if _is_pyramid_child
                            else (_a_plus or _conf >= 0.70)
                        ),
                        pyramid_parent_ticket=_parent_ticket if _is_pyramid_child else None,
                    )
                    if trade_reservation:
                        bot.reservation_ledger.transfer_to_position(
                            trade_reservation,
                            result.ticket,
                        )
                    
                    # Set multi-TP levels for partial close management
                    # Scalps: single TP — close full position, no partials
                    # Intraday/Swing: multi-TP with partial close management
                    _pos_trade_type = getattr(trade_signal, 'trade_type', 'intraday') or 'intraday'
                    
                    if _pos_trade_type == 'scalp':
                        # Scalps: single TP, full close. No multi-TP complexity.
                        position.tp1 = position.take_profit
                        position.tp2 = 0.0
                        position.tp3 = 0.0
                        logger.info(f"  SCALP: Single TP at {position.tp1:.5f} (full close)")
                    elif take_profit_levels:
                        position.tp1 = take_profit_levels.get('tp1', 0.0) or 0.0
                        position.tp2 = take_profit_levels.get('tp2', 0.0) or 0.0
                        position.tp3 = take_profit_levels.get('tp3', 0.0) or 0.0
                        logger.info(
                            f"  Multi-TP set: TP1={position.tp1}, TP2={position.tp2}, TP3={position.tp3}"
                        )
                    elif trade_signal.stop_loss and trade_signal.take_profit:
                        # Fallback: auto-calculate TP levels from SL/TP
                        _entry = result.fill_price or current_price
                        _sl_dist = abs(_entry - trade_signal.stop_loss)
                        if trade_signal.direction == 'long':
                            position.tp1 = _entry + (_sl_dist * 1.0)   # 1R
                            position.tp2 = _entry + (_sl_dist * 2.0)   # 2R
                            position.tp3 = _entry + (_sl_dist * 3.0)   # 3R
                        else:
                            position.tp1 = _entry - (_sl_dist * 1.0)   # 1R
                            position.tp2 = _entry - (_sl_dist * 2.0)   # 2R
                            position.tp3 = _entry - (_sl_dist * 3.0)   # 3R
                        logger.info(
                            f"  Multi-TP (auto): TP1={position.tp1:.5f}, TP2={position.tp2:.5f}, TP3={position.tp3:.5f}"
                        )
                    
                    bot.position_manager.add_position(position)
                    print(f"[TRADE] {symbol}: Market order filled (ticket={result.ticket}, fill={result.fill_price})", flush=True)
                    
                    # Execution cost + regime telemetry for edge analysis
                    from ..services.edge_policies import compute_slippage
                    _requested = trade_signal.entry_price or current_price
                    _slippage = compute_slippage(
                        trade_signal.direction, _requested, result.fill_price
                    )
                    _session = (
                        bot.kill_zone_checker.get_current_session().session_name
                        if bot.kill_zone_checker else ""
                    )
                    _regime = getattr(bot, "_last_regime_by_symbol", {}).get(symbol)
                    await bot._record_terminal_decision(
                        "market_filled",
                        symbol,
                        direction=trade_signal.direction,
                        entry=result.fill_price or current_price,
                        sl=tracked_sl or 0.0,
                        tp=tracked_tp or 0.0,
                        confidence=trade_signal.confidence,
                        session=_session,
                        reason="Market order filled",
                        details={
                            "ticket": result.ticket,
                            "requested_entry": _requested,
                            "fill_price": result.fill_price,
                            "slippage": round(_slippage, 6),
                            "regime": _regime,
                        },
                    )
                    
                    # Track in correlation service
                    if bot.correlation_service:
                        bot.correlation_service.set_open_position(
                            symbol, position_size.lots, trade_signal.direction
                        )
                    
                    # Add to activity feed
                    from ..api.routes.activity import add_activity
                    add_activity(
                        "trade_opened",
                        f"Opened {trade_signal.direction.upper()} {symbol} @ {result.fill_price or current_price:.5f}",
                        symbol,
                        {
                            "ticket": result.ticket,
                            "direction": trade_signal.direction,
                            "entry_price": result.fill_price or current_price,
                            "stop_loss": trade_signal.stop_loss,
                            "take_profit": trade_signal.take_profit,
                            "lots": position_size.lots,
                            "confidence": trade_signal.confidence
                        }
                    )
                    asyncio.create_task(broadcast_trade_update({
                        "event": "trade_opened",
                        "ticket": result.ticket,
                        "symbol": symbol,
                        "direction": trade_signal.direction,
                        "entry_price": result.fill_price or current_price,
                        "stop_loss": trade_signal.stop_loss,
                        "take_profit": trade_signal.take_profit,
                        "lots": position_size.lots,
                        "confidence": trade_signal.confidence
                    }))
                    
                    # Save trade to database with full analysis context
                    _fp = getattr(trade_signal, "setup_fingerprint", None)
                    if isinstance(_fp, dict):
                        _fp = _fp.get("key")
                    await save_trade_to_db(
                        ticket=result.ticket,
                        symbol=symbol,
                        direction=trade_signal.direction,
                        entry_price=result.fill_price or current_price,
                        stop_loss=trade_signal.stop_loss or 0.0,
                        take_profit=trade_signal.take_profit or 0.0,
                        position_size=position_size.lots,
                        confidence=trade_signal.confidence,
                        reasoning=trade_signal.reasoning if hasattr(trade_signal, 'reasoning') else "",
                        judge_verdict=judge_verdict.get('verdict', 'APPROVE') if judge_verdict else None,
                        judge_reason=judge_verdict.get('reason', '') if judge_verdict else None,
                        judge_risk_flags=judge_verdict.get('risk_flags', []) if judge_verdict else None,
                        trade_type=getattr(trade_signal, 'trade_type', 'intraday'),
                        order_type=order_type,
                        amd_phase=getattr(trade_signal, 'amd_phase', 'unknown'),
                        market_structure=getattr(trade_signal, 'market_structure', ''),
                        confluence_factors=confluence_factors if confluence_factors else None,
                        confluence_count=confluence_count if confluence_count else None,
                        ict_concepts={
                            'order_blocks': getattr(trade_signal, 'order_blocks', []),
                            'fvg_zones': getattr(trade_signal, 'fvg_zones', []),
                            'liquidity_targets': getattr(trade_signal, 'liquidity_targets', []),
                            'manipulation_complete': getattr(trade_signal, 'manipulation_complete', False),
                        },
                        timeframe="M15",
                        session_name=bot.kill_zone_checker.get_current_session().session_name if bot.kill_zone_checker else "",
                        risk_percent=size_result.risk_percent if hasattr(size_result, 'risk_percent') else bot.risk_manager.risk_per_trade,
                        setup_fingerprint=_fp,
                        regime=getattr(trade_signal, "regime", None),
                    )
                    
                    # Send Telegram notification
                    await notify(
                        NotificationType.TRADE_OPENED,
                        f"Trade opened: {symbol}",
                        symbol=symbol,
                        direction=trade_signal.direction,
                        entry_price=result.fill_price or current_price,
                        stop_loss=trade_signal.stop_loss or 0.0,
                        take_profit=trade_signal.take_profit or 0.0,
                        lots=position_size.lots,
                        confidence=trade_signal.confidence,
                        ticket=result.ticket
                    )
        else:
            logger.error(f"✗ Trade execution failed for {symbol}: {result.message}")
            reconciled_ticket = await bot._reconcile_fill_after_ambiguous_order(
                symbol=symbol,
                direction=trade_signal.direction,
                lots=position_size.lots,
                reservation=trade_reservation,
                stop_loss=final_sl or 0.0,
                take_profit=final_tp or 0.0,
            )
            if reconciled_ticket:
                logger.warning(
                    f"[RECONCILE] {symbol}: execution reported failure but position "
                    f"{reconciled_ticket} found in MT5 — reservation retained"
                )
                from ..api.routes.activity import add_activity
                add_activity(
                    "reconcile_fill",
                    f"Recovered ambiguous fill for {symbol} (ticket={reconciled_ticket})",
                    symbol,
                    {"ticket": reconciled_ticket, "reason": result.message},
                )
            else:
                await bot._record_terminal_decision(
                    "execution_failure",
                    symbol,
                    direction=trade_signal.direction,
                    entry=trade_signal.entry_price or current_price,
                    sl=trade_signal.stop_loss or 0.0,
                    tp=trade_signal.take_profit or 0.0,
                    confidence=trade_signal.confidence,
                    reason=result.message or "broker rejected order",
                )
                bot._release_trade_reservation(trade_reservation)
            
            # Log error to activity feed
            from ..api.routes.activity import add_activity
            add_activity(
                "error",
                f"Trade execution failed for {symbol}: {result.message}",
                symbol,
                {"error": result.message}
            )
        
