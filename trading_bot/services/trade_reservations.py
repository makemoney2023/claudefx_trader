"""
Exactly-once trade slot and risk reservation accounting.

One reservation owns a daily trade slot and optional risk budget for a single
trade attempt. Reservations transfer to pending orders or positions on success
and release idempotently on rejection/cancel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from contextlib import contextmanager
from typing import Callable, Dict, Optional
import uuid

from ..utils.logging import get_logger

logger = get_logger(__name__)


class ReservationState(str, Enum):
    RESERVED = "reserved"
    TRANSFERRED = "transferred"
    RELEASED = "released"
    CLOSED = "closed"


@dataclass
class TradeReservation:
    reservation_id: str
    symbol: str
    signal_id: Optional[str]
    risk_percent: float
    state: ReservationState = ReservationState.RESERVED
    pending_ticket: Optional[int] = None
    position_ticket: Optional[int] = None
    _slot_applied: bool = field(default=False, repr=False)
    _risk_applied: bool = field(default=False, repr=False)
    _slot_released: bool = field(default=False, repr=False)
    _risk_released: bool = field(default=False, repr=False)


class TradeReservationLedger:
    """Owns slot/risk mutations for bot-created trade attempts."""

    def __init__(
        self,
        risk_manager=None,
        get_daily_trades: Optional[Callable[[], int]] = None,
        set_daily_trades: Optional[Callable[[int], None]] = None,
    ):
        self.risk_manager = risk_manager
        self._get_daily_trades = get_daily_trades
        self._set_daily_trades = set_daily_trades
        self._reservations: Dict[str, TradeReservation] = {}
        self._by_ticket: Dict[int, str] = {}

    def reserve(
        self,
        symbol: str,
        signal_id: Optional[str] = None,
        risk_percent: float = 0.0,
    ) -> TradeReservation:
        reservation = TradeReservation(
            reservation_id=str(uuid.uuid4()),
            symbol=symbol,
            signal_id=signal_id,
            risk_percent=risk_percent,
        )
        if self._get_daily_trades and self._set_daily_trades:
            current = self._get_daily_trades()
            self._set_daily_trades(current + 1)
            reservation._slot_applied = True
        self._reservations[reservation.reservation_id] = reservation
        logger.debug(
            f"Reserved trade slot for {symbol} ({reservation.reservation_id[:8]})"
        )
        return reservation

    def commit_risk(self, reservation: TradeReservation) -> None:
        if reservation._risk_applied:
            return
        if reservation.state in (ReservationState.RELEASED, ReservationState.CLOSED):
            return
        if self.risk_manager and reservation.risk_percent:
            self.risk_manager.update_daily_risk(reservation.risk_percent)
        reservation._risk_applied = True

    def transfer_to_pending(self, reservation: TradeReservation, ticket: int) -> None:
        reservation.pending_ticket = ticket
        reservation.state = ReservationState.TRANSFERRED
        self._by_ticket[ticket] = reservation.reservation_id

    def transfer_to_position(self, reservation: TradeReservation, ticket: int) -> None:
        reservation.position_ticket = ticket
        if reservation.pending_ticket is not None:
            self._by_ticket.pop(reservation.pending_ticket, None)
        reservation.state = ReservationState.TRANSFERRED
        self._by_ticket[ticket] = reservation.reservation_id

    def restore_pending(
        self,
        reservation_id: str,
        symbol: str,
        ticket: int,
        risk_percent: float,
    ) -> TradeReservation:
        """Restore ownership already represented in persisted daily totals."""
        existing = self._reservations.get(reservation_id)
        if existing:
            self._by_ticket[ticket] = reservation_id
            return existing

        reservation = TradeReservation(
            reservation_id=reservation_id,
            symbol=symbol,
            signal_id=None,
            risk_percent=risk_percent,
            state=ReservationState.TRANSFERRED,
            pending_ticket=ticket,
            _slot_applied=True,
            _risk_applied=bool(risk_percent),
        )
        self._reservations[reservation_id] = reservation
        self._by_ticket[ticket] = reservation_id
        return reservation

    def restore_position(
        self,
        reservation_id: str,
        symbol: str,
        ticket: int,
        risk_percent: float,
        order_ticket: Optional[int] = None,
    ) -> TradeReservation:
        """Restore position ownership already represented in persisted daily totals."""
        existing = self._reservations.get(reservation_id)
        if existing:
            existing.position_ticket = ticket
            if order_ticket is not None:
                existing.pending_ticket = order_ticket
            existing.state = ReservationState.TRANSFERRED
            self._by_ticket[ticket] = reservation_id
            if order_ticket is not None:
                self._by_ticket[order_ticket] = reservation_id
            return existing

        reservation = TradeReservation(
            reservation_id=reservation_id,
            symbol=symbol,
            signal_id=None,
            risk_percent=risk_percent,
            state=ReservationState.TRANSFERRED,
            pending_ticket=order_ticket,
            position_ticket=ticket,
            _slot_applied=True,
            _risk_applied=bool(risk_percent),
        )
        self._reservations[reservation_id] = reservation
        self._by_ticket[ticket] = reservation_id
        if order_ticket is not None:
            self._by_ticket[order_ticket] = reservation_id
        return reservation

    def release(self, reservation: TradeReservation) -> bool:
        if reservation.state in (ReservationState.RELEASED, ReservationState.CLOSED):
            return False

        changed = False
        if reservation._slot_applied and not reservation._slot_released:
            if self._get_daily_trades and self._set_daily_trades:
                current = self._get_daily_trades()
                self._set_daily_trades(max(0, current - 1))
            reservation._slot_released = True
            changed = True

        if reservation._risk_applied and not reservation._risk_released:
            if self.risk_manager and reservation.risk_percent:
                self.risk_manager.update_daily_risk(-reservation.risk_percent)
            reservation._risk_released = True
            changed = True

        reservation.state = ReservationState.RELEASED
        if changed:
            logger.debug(
                f"Released reservation {reservation.reservation_id[:8]} for {reservation.symbol}"
            )
        return changed

    def mark_closed(self, reservation: TradeReservation) -> bool:
        if reservation.state == ReservationState.CLOSED:
            return False

        changed = False
        if reservation._risk_applied and not reservation._risk_released:
            if self.risk_manager and reservation.risk_percent:
                self.risk_manager.update_daily_risk(-reservation.risk_percent)
            reservation._risk_released = True
            changed = True

        reservation.state = ReservationState.CLOSED
        return changed

    def get_for_ticket(self, ticket: int) -> Optional[TradeReservation]:
        reservation_id = self._by_ticket.get(ticket)
        if reservation_id:
            return self._reservations.get(reservation_id)
        return None

    def get_by_id(self, reservation_id: Optional[str]) -> Optional[TradeReservation]:
        if not reservation_id:
            return None
        return self._reservations.get(reservation_id)

    def owns_ticket(self, ticket: int) -> bool:
        return ticket in self._by_ticket

    def register_ticket(self, reservation_id: str, ticket: int) -> None:
        if reservation_id in self._reservations:
            self._by_ticket[ticket] = reservation_id


@contextmanager
def reserved_trade_attempt(
    ledger: TradeReservationLedger,
    symbol: str,
    signal_id: Optional[str] = None,
    risk_percent: float = 0.0,
):
    """Reserve slot/risk for one trade attempt; release if never transferred."""
    reservation = ledger.reserve(
        symbol=symbol,
        signal_id=signal_id,
        risk_percent=risk_percent,
    )
    try:
        yield reservation
    finally:
        if reservation.state == ReservationState.RESERVED:
            ledger.release(reservation)
