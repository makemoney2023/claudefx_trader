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
            
            # Gap 57: Verify order actually exists in MT5
            # Only verify for market orders — pending orders won't appear in positions yet
            is_pending_order = order_type in ['buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']
            if result.ticket and not bot.mt5_client.is_simulation and not is_pending_order:
                await asyncio.sleep(0.5)  # Brief delay for MT5 to process
                positions = await bot.mt5_client.get_positions(symbol=symbol)
                
                # MT5 Position is a dataclass, access attributes directly
                position_exists = any(
                    p.ticket == result.ticket for p in positions
                )
                
                if not position_exists:
                    logger.error(
                        f"⚠ Order reported success but position {result.ticket} not found in MT5! "
                        f"Manual verification required."
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
                    # Don't track the position if it doesn't exist;
                    # sync_with_mt5 adopts it if the fill appears later
                    return
                
                logger.info(f"  ✓ Position verified in MT5")
            elif is_pending_order:
                logger.info(f"  ⏳ Pending order placed — will verify when filled")
            
            # Track position — but ONLY for market orders (immediately filled)
            # Pending orders (buy_limit, sell_limit, etc.) are tracked by pending_order_manager
            # and will be picked up by sync_with_mt5 when they fill
            if result.ticket:
                # Validate SL/TP are real values before tracking
                tracked_sl = trade_signal.stop_loss if trade_signal.stop_loss and trade_signal.stop_loss > 0 else None
                tracked_tp = trade_signal.take_profit if trade_signal.take_profit and trade_signal.take_profit > 0 else None
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
                        reason=f"Pending {order_type} placed",
                        details={"ticket": result.ticket, "order_type": order_type},
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
                    )
                    
                    # Pending orders: do NOT send Telegram notification.
                    # These get cancelled/replaced frequently and would spam
                    # the user. Only notify when a trade actually fills.
                    logger.info(f"Pending order placed for {symbol} — Telegram notification deferred until fill")
                else:
                    # =============================================
                    # MARKET ORDER: Immediately filled, add to position_manager
                    # =============================================
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
                        a_plus=classify_a_plus(
                            setup_grade.value if hasattr(setup_grade, 'value') else str(setup_grade),
                            confluence_count or 0,
                        ),
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
                    await bot._record_terminal_decision(
                        "market_filled",
                        symbol,
                        direction=trade_signal.direction,
                        entry=result.fill_price or current_price,
                        sl=tracked_sl or 0.0,
                        tp=tracked_tp or 0.0,
                        confidence=trade_signal.confidence,
                        reason="Market order filled",
                        details={"ticket": result.ticket},
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
        
