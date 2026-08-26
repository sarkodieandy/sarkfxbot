"""Repositories for the durable signal-to-trade execution lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.models import (
    ExecutionAttempt,
    Order,
    Position,
    Signal,
    SignalCondition,
    Trade,
    TradeEvent,
)
from app.db.models.base import new_id, utc_now
from app.db.repositories.base import (
    Repository,
    enum_value,
    json_safe,
    redact_sensitive_json,
)


class SignalRepository(Repository[Signal]):
    model = Signal

    def create(self, **values: Any) -> Signal:
        values.setdefault("signal_id", new_id())
        for field in ("action", "direction", "status"):
            if field in values:
                values[field] = enum_value(values[field])
        for field in ("take_profits", "rationale"):
            if field in values:
                values[field] = redact_sensitive_json(values[field])
        return self.add(Signal(**values))

    def get(self, entity_id: str) -> Signal | None:
        return self.session.get(Signal, entity_id)

    def active(self, *, symbol: str | None = None) -> Sequence[Signal]:
        statement = select(Signal).where(
            Signal.status.in_(("ACTIVE", "APPROVAL_REQUIRED", "APPROVED"))
        )
        if symbol is not None:
            statement = statement.where(Signal.symbol == symbol)
        statement = statement.order_by(Signal.created_at.desc())
        return tuple(self.session.scalars(statement))

    def set_status(self, signal_id: str, status: object) -> Signal:
        signal = self.get(signal_id)
        if signal is None:
            raise LookupError(f"signal {signal_id} does not exist")
        signal.status = str(enum_value(status))
        signal.updated_at = utc_now()
        self.session.flush()
        return signal

    def add_condition(
        self,
        signal_id: str,
        *,
        condition_name: str,
        passed: bool,
        score: object = 0,
        observed_value: Any = None,
        details: Mapping[str, Any] | None = None,
    ) -> SignalCondition:
        condition = SignalCondition(
            signal_id=signal_id,
            condition_name=condition_name,
            passed=passed,
            score=score,
            observed_value=redact_sensitive_json(observed_value),
            details=redact_sensitive_json(dict(details or {})),
        )
        self.session.add(condition)
        self.session.flush()
        return condition


class OrderRepository(Repository[Order]):
    model = Order

    def create(self, **values: Any) -> Order:
        for field in ("direction", "order_type", "status"):
            if field in values:
                values[field] = enum_value(values[field])
        if "take_profits" in values:
            values["take_profits"] = json_safe(values["take_profits"])
        return self.add(Order(**values))

    def by_idempotency_key(self, idempotency_key: str) -> Order | None:
        return self.session.scalar(select(Order).where(Order.idempotency_key == idempotency_key))

    def by_broker_ticket(self, broker_account_id: str, broker_ticket: str) -> Order | None:
        return self.session.scalar(
            select(Order).where(
                Order.broker_account_id == broker_account_id,
                Order.broker_ticket == broker_ticket,
            )
        )

    def pending(
        self, *, broker_account_id: str | None = None, symbol: str | None = None
    ) -> Sequence[Order]:
        statement = select(Order).where(Order.status.in_(("PENDING", "PARTIALLY_FILLED")))
        if broker_account_id is not None:
            statement = statement.where(Order.broker_account_id == broker_account_id)
        if symbol is not None:
            statement = statement.where(Order.symbol == symbol)
        return tuple(self.session.scalars(statement.order_by(Order.created_at)))

    def set_status(
        self,
        order_id: str,
        status: object,
        *,
        broker_ticket: str | None = None,
        executed_price: object | None = None,
        broker_code: str | None = None,
        broker_message: str | None = None,
        occurred_at: datetime | None = None,
    ) -> Order:
        order = self.get(order_id)
        if order is None:
            raise LookupError(f"order {order_id} does not exist")
        normalized = str(enum_value(status))
        order.status = normalized
        if broker_ticket is not None:
            order.broker_ticket = broker_ticket
        if executed_price is not None:
            order.executed_price = Decimal(str(executed_price))
        order.broker_code = broker_code
        order.broker_message = broker_message
        event_time = occurred_at or utc_now()
        if normalized in {"FILLED", "PARTIALLY_FILLED"}:
            order.filled_at = event_time
        elif normalized in {"CANCELLED", "REJECTED"}:
            order.cancelled_at = event_time
        self.session.flush()
        return order


class PositionRepository(Repository[Position]):
    model = Position

    def create(self, **values: Any) -> Position:
        for field in ("direction", "state"):
            if field in values:
                values[field] = enum_value(values[field])
        if "take_profits" in values:
            values["take_profits"] = json_safe(values["take_profits"])
        return self.add(Position(**values))

    def by_broker_ticket(self, broker_account_id: str, broker_ticket: str) -> Position | None:
        return self.session.scalar(
            select(Position).where(
                Position.broker_account_id == broker_account_id,
                Position.broker_ticket == broker_ticket,
            )
        )

    def list_open(
        self, *, broker_account_id: str | None = None, symbol: str | None = None
    ) -> Sequence[Position]:
        statement = select(Position).where(Position.closed_at.is_(None))
        if broker_account_id is not None:
            statement = statement.where(Position.broker_account_id == broker_account_id)
        if symbol is not None:
            statement = statement.where(Position.symbol == symbol)
        return tuple(self.session.scalars(statement.order_by(Position.opened_at)))

    def upsert_broker_position(
        self,
        broker_account_id: str,
        broker_ticket: str,
        **values: Any,
    ) -> Position:
        position = self.by_broker_ticket(broker_account_id, broker_ticket)
        if position is None:
            return self.create(
                broker_account_id=broker_account_id,
                broker_ticket=broker_ticket,
                **values,
            )
        allowed = {
            "signal_id",
            "opening_order_id",
            "symbol",
            "direction",
            "initial_volume",
            "current_volume",
            "open_price",
            "current_price",
            "stop_loss",
            "take_profits",
            "realized_pnl",
            "unrealized_pnl",
            "state",
            "opened_at",
            "closed_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported position fields: {sorted(unknown)}")
        for field, value in values.items():
            if field in {"direction", "state"}:
                value = enum_value(value)
            elif field == "take_profits":
                value = json_safe(value)
            setattr(position, field, value)
        self.session.flush()
        return position

    def mark_closed(
        self,
        position_id: str,
        *,
        state: object = "CLOSED",
        closed_at: datetime | None = None,
        realized_pnl: object | None = None,
    ) -> Position:
        position = self.get(position_id)
        if position is None:
            raise LookupError(f"position {position_id} does not exist")
        position.state = str(enum_value(state))
        position.closed_at = closed_at or utc_now()
        position.current_volume = Decimal("0")
        if realized_pnl is not None:
            position.realized_pnl = Decimal(str(realized_pnl))
        self.session.flush()
        return position


class TradeRepository(Repository[Trade]):
    model = Trade

    def create(self, **values: Any) -> Trade:
        for field in ("direction", "state"):
            if field in values:
                values[field] = enum_value(values[field])
        return self.add(Trade(**values))

    def by_position(self, position_id: str) -> Trade | None:
        return self.session.scalar(select(Trade).where(Trade.position_id == position_id))

    def list_open(self, *, broker_account_id: str | None = None) -> Sequence[Trade]:
        statement = select(Trade).where(Trade.closed_at.is_(None))
        if broker_account_id is not None:
            statement = statement.where(Trade.broker_account_id == broker_account_id)
        return tuple(self.session.scalars(statement.order_by(Trade.opened_at)))

    def close(
        self,
        trade_id: str,
        *,
        state: object,
        exit_price: object,
        gross_pnl: object,
        net_pnl: object,
        closed_at: datetime | None = None,
        r_multiple: object | None = None,
        exit_reason: str | None = None,
    ) -> Trade:
        trade = self.get(trade_id)
        if trade is None:
            raise LookupError(f"trade {trade_id} does not exist")
        trade.state = str(enum_value(state))
        trade.exit_price = Decimal(str(exit_price))
        trade.gross_pnl = Decimal(str(gross_pnl))
        trade.net_pnl = Decimal(str(net_pnl))
        trade.realized_pnl = Decimal(str(net_pnl))
        trade.closed_at = closed_at or utc_now()
        trade.exit_reason = exit_reason
        if r_multiple is not None:
            trade.r_multiple = Decimal(str(r_multiple))
        self.session.flush()
        return trade


class ExecutionAttemptRepository(Repository[ExecutionAttempt]):
    model = ExecutionAttempt

    def begin(
        self,
        *,
        order_id: str,
        attempt_number: int,
        attempt_key: str,
        request_payload: Mapping[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> ExecutionAttempt:
        if attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        return self.add(
            ExecutionAttempt(
                order_id=order_id,
                attempt_number=attempt_number,
                attempt_key=attempt_key,
                status="STARTED",
                request_payload=redact_sensitive_json(dict(request_payload or {})),
                started_at=started_at or utc_now(),
            )
        )

    def by_attempt_key(self, attempt_key: str) -> ExecutionAttempt | None:
        return self.session.scalar(
            select(ExecutionAttempt).where(ExecutionAttempt.attempt_key == attempt_key)
        )

    def complete(
        self,
        attempt_id: str,
        *,
        status: str,
        response_payload: Mapping[str, Any] | None = None,
        broker_ticket: str | None = None,
        broker_code: str | None = None,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> ExecutionAttempt:
        attempt = self.get(attempt_id)
        if attempt is None:
            raise LookupError(f"execution attempt {attempt_id} does not exist")
        attempt.status = status
        attempt.response_payload = (
            redact_sensitive_json(dict(response_payload)) if response_payload is not None else None
        )
        attempt.broker_ticket = broker_ticket
        attempt.broker_code = broker_code
        attempt.error_message = error_message
        attempt.completed_at = completed_at or utc_now()
        self.session.flush()
        return attempt


class TradeEventRepository(Repository[TradeEvent]):
    model = TradeEvent

    def append(self, **values: Any) -> TradeEvent:
        for field in ("previous_state", "current_state"):
            if field in values and values[field] is not None:
                values[field] = enum_value(values[field])
        if "payload" in values:
            values["payload"] = redact_sensitive_json(values["payload"])
        return self.add(TradeEvent(**values))

    def for_trade(self, trade_id: str) -> Sequence[TradeEvent]:
        return tuple(
            self.session.scalars(
                select(TradeEvent)
                .where(TradeEvent.trade_id == trade_id)
                .order_by(TradeEvent.occurred_at, TradeEvent.id)
            )
        )

    def by_event_key(self, event_key: str) -> TradeEvent | None:
        return self.session.scalar(select(TradeEvent).where(TradeEvent.event_key == event_key))
