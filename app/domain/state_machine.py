"""Explicit, validated lifecycle transitions for every trade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.enums import TradeState
from app.domain.errors import InvalidStateTransition

_ALLOWED: dict[TradeState, frozenset[TradeState]] = {
    TradeState.SCANNING: frozenset({TradeState.SIGNAL_FOUND, TradeState.ERROR}),
    TradeState.SIGNAL_FOUND: frozenset(
        {
            TradeState.WAITING_FOR_ENTRY,
            TradeState.ENTRY_READY,
            TradeState.CANCELLED,
            TradeState.ERROR,
        }
    ),
    TradeState.WAITING_FOR_ENTRY: frozenset(
        {TradeState.ENTRY_READY, TradeState.EXPIRED, TradeState.CANCELLED, TradeState.ERROR}
    ),
    TradeState.ENTRY_READY: frozenset(
        {TradeState.ORDER_PENDING, TradeState.ORDER_FILLED, TradeState.EXPIRED, TradeState.ERROR}
    ),
    TradeState.ORDER_PENDING: frozenset(
        {TradeState.ORDER_FILLED, TradeState.CANCELLED, TradeState.EXPIRED, TradeState.ERROR}
    ),
    TradeState.ORDER_FILLED: frozenset({TradeState.POSITION_OPEN, TradeState.ERROR}),
    TradeState.POSITION_OPEN: frozenset(
        {
            TradeState.TP1_HIT,
            TradeState.BREAK_EVEN,
            TradeState.CLOSING,
            TradeState.CLOSED,
            TradeState.STOPPED_OUT,
            TradeState.ERROR,
        }
    ),
    TradeState.TP1_HIT: frozenset(
        {
            TradeState.BREAK_EVEN,
            TradeState.CLOSING,
            TradeState.CLOSED,
            TradeState.STOPPED_OUT,
            TradeState.ERROR,
        }
    ),
    TradeState.BREAK_EVEN: frozenset(
        {TradeState.CLOSING, TradeState.CLOSED, TradeState.STOPPED_OUT, TradeState.ERROR}
    ),
    TradeState.CLOSING: frozenset({TradeState.CLOSED, TradeState.ERROR}),
    TradeState.ERROR: frozenset({TradeState.CANCELLED, TradeState.CLOSING, TradeState.CLOSED}),
    TradeState.CLOSED: frozenset(),
    TradeState.STOPPED_OUT: frozenset(),
    TradeState.EXPIRED: frozenset(),
    TradeState.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: TradeState
    current: TradeState
    occurred_at: datetime
    reason: str


class TradeStateMachine:
    """Keeps a local lifecycle and returns persistence-ready transitions."""

    def __init__(self, initial: TradeState = TradeState.SCANNING) -> None:
        self._state = initial

    @property
    def state(self) -> TradeState:
        return self._state

    def can_transition(self, target: TradeState) -> bool:
        return target in _ALLOWED[self._state]

    def transition(self, target: TradeState, reason: str) -> StateTransition:
        if not reason.strip():
            raise ValueError("a state transition reason is required")
        if not self.can_transition(target):
            raise InvalidStateTransition(f"cannot transition {self._state} -> {target}")
        event = StateTransition(self._state, target, datetime.now(UTC), reason)
        self._state = target
        return event
