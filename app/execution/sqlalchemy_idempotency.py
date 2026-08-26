"""PostgreSQL/SQLAlchemy-backed idempotency adapter for production wiring."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ExecutionAttempt
from app.domain.enums import Direction, OrderType
from app.domain.models import ExecutionReport, OrderRequest
from app.execution.idempotency import ExecutionRecord, IdempotencyStatus

OrderIdResolver = Callable[[Session, OrderRequest], str]


def _request_payload(request: OrderRequest) -> dict[str, Any]:
    return {
        "signal_id": str(request.signal_id),
        "strategy_id": request.strategy_id,
        "symbol": request.symbol,
        "direction": request.direction.value,
        "order_type": request.order_type.value,
        "volume": str(request.volume),
        "stop_loss": str(request.stop_loss),
        "take_profits": [str(item) for item in request.take_profits],
        "idempotency_key": request.idempotency_key,
        "requested_price": str(request.requested_price) if request.requested_price else None,
        "entry_min": str(request.entry_min) if request.entry_min else None,
        "entry_max": str(request.entry_max) if request.entry_max else None,
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "maximum_slippage": str(request.maximum_slippage),
        "comment": request.comment,
    }


def _request_from_payload(payload: dict[str, Any]) -> OrderRequest:
    return OrderRequest(
        signal_id=UUID(str(payload["signal_id"])),
        strategy_id=str(payload["strategy_id"]),
        symbol=str(payload["symbol"]),
        direction=Direction(str(payload["direction"])),
        order_type=OrderType(str(payload["order_type"])),
        volume=Decimal(str(payload["volume"])),
        stop_loss=Decimal(str(payload["stop_loss"])),
        take_profits=tuple(Decimal(str(item)) for item in payload["take_profits"]),
        idempotency_key=str(payload["idempotency_key"]),
        requested_price=(
            Decimal(str(payload["requested_price"]))
            if payload.get("requested_price") is not None
            else None
        ),
        entry_min=(
            Decimal(str(payload["entry_min"])) if payload.get("entry_min") is not None else None
        ),
        entry_max=(
            Decimal(str(payload["entry_max"])) if payload.get("entry_max") is not None else None
        ),
        expires_at=(
            datetime.fromisoformat(str(payload["expires_at"]))
            if payload.get("expires_at")
            else None
        ),
        maximum_slippage=Decimal(str(payload["maximum_slippage"])),
        comment=str(payload.get("comment", "goldflow")),
    )


def _report_payload(report: ExecutionReport) -> dict[str, Any]:
    return {
        "success": report.success,
        "idempotency_key": report.idempotency_key,
        "broker_ticket": report.broker_ticket,
        "requested_price": str(report.requested_price) if report.requested_price else None,
        "executed_price": str(report.executed_price) if report.executed_price else None,
        "volume": str(report.volume),
        "broker_code": report.broker_code,
        "message": report.message,
        "submitted_at": report.submitted_at.isoformat(),
    }


def _report_from_payload(payload: dict[str, Any] | None) -> ExecutionReport | None:
    if not payload or "report" not in payload:
        return None
    report = payload["report"]
    return ExecutionReport(
        success=bool(report["success"]),
        idempotency_key=str(report["idempotency_key"]),
        broker_ticket=(str(report["broker_ticket"]) if report["broker_ticket"] else None),
        requested_price=(
            Decimal(str(report["requested_price"]))
            if report["requested_price"] is not None
            else None
        ),
        executed_price=(
            Decimal(str(report["executed_price"])) if report["executed_price"] is not None else None
        ),
        volume=Decimal(str(report["volume"])),
        broker_code=str(report["broker_code"]) if report["broker_code"] else None,
        message=str(report["message"]),
        submitted_at=datetime.fromisoformat(str(report["submitted_at"])),
    )


class SqlAlchemyIdempotencyStore:
    """Durable adapter using ``execution_attempts.attempt_key`` as the unique claim.

    The supplied resolver must return/create the durable ``orders.id`` in the same
    transaction. This keeps account/signal foreign-key policy in the persistence
    composition root instead of guessing broker IDs inside execution code.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        order_id_resolver: OrderIdResolver,
    ) -> None:
        self._session_factory = session_factory
        self._order_id_resolver = order_id_resolver

    def _record(self, attempt: ExecutionAttempt) -> ExecutionRecord:
        response = attempt.response_payload or {}
        updated_text = response.get("updated_at")
        updated_at = (
            datetime.fromisoformat(str(updated_text))
            if updated_text
            else attempt.completed_at or attempt.started_at
        )
        return ExecutionRecord(
            idempotency_key=attempt.attempt_key,
            request=_request_from_payload(attempt.request_payload),
            status=IdempotencyStatus(attempt.status),
            report=_report_from_payload(response),
            reason=attempt.error_message or "",
            version=int(response.get("version", 1)),
            created_at=attempt.started_at,
            updated_at=updated_at,
        )

    def _claim_sync(self, request: OrderRequest, now: datetime) -> tuple[ExecutionRecord, bool]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(ExecutionAttempt).where(
                    ExecutionAttempt.attempt_key == request.idempotency_key
                )
            )
            if existing is not None:
                return self._record(existing), False
            order_id = self._order_id_resolver(session, request)
            attempt = ExecutionAttempt(
                order_id=order_id,
                attempt_number=1,
                attempt_key=request.idempotency_key,
                status=IdempotencyStatus.CLAIMED.value,
                request_payload=_request_payload(request),
                response_payload={"version": 1, "updated_at": now.astimezone(UTC).isoformat()},
                started_at=now.astimezone(UTC),
            )
            session.add(attempt)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raced = session.scalar(
                    select(ExecutionAttempt).where(
                        ExecutionAttempt.attempt_key == request.idempotency_key
                    )
                )
                if raced is None:
                    raise
                return self._record(raced), False
            session.refresh(attempt)
            return self._record(attempt), True

    async def claim(self, request: OrderRequest, now: datetime) -> tuple[ExecutionRecord, bool]:
        return await asyncio.to_thread(self._claim_sync, request, now)

    def _get_sync(self, idempotency_key: str) -> ExecutionRecord | None:
        with self._session_factory() as session:
            attempt = session.scalar(
                select(ExecutionAttempt).where(ExecutionAttempt.attempt_key == idempotency_key)
            )
            return self._record(attempt) if attempt is not None else None

    async def get(self, idempotency_key: str) -> ExecutionRecord | None:
        return await asyncio.to_thread(self._get_sync, idempotency_key)

    def _update_sync(
        self,
        idempotency_key: str,
        status: IdempotencyStatus,
        now: datetime,
        report: ExecutionReport | None,
        reason: str,
    ) -> ExecutionRecord:
        with self._session_factory() as session:
            attempt = session.scalar(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.attempt_key == idempotency_key)
                .with_for_update()
            )
            if attempt is None:
                raise KeyError(f"unknown idempotency key {idempotency_key}")
            current = IdempotencyStatus(attempt.status)
            allowed = {
                IdempotencyStatus.CLAIMED: {
                    IdempotencyStatus.SUBMITTED,
                    IdempotencyStatus.REJECTED,
                },
                IdempotencyStatus.SUBMITTED: {
                    IdempotencyStatus.SUCCEEDED,
                    IdempotencyStatus.PROTECTION_FAILED,
                    IdempotencyStatus.REJECTED,
                    IdempotencyStatus.UNKNOWN,
                },
                IdempotencyStatus.UNKNOWN: {
                    IdempotencyStatus.SUCCEEDED,
                    IdempotencyStatus.PROTECTION_FAILED,
                    IdempotencyStatus.REJECTED,
                },
                IdempotencyStatus.SUCCEEDED: set(),
                IdempotencyStatus.PROTECTION_FAILED: set(),
                IdempotencyStatus.REJECTED: set(),
            }
            if status not in allowed[current]:
                raise RuntimeError(f"invalid idempotency transition {current} -> {status}")
            prior = attempt.response_payload or {}
            response: dict[str, Any] = {
                "version": int(prior.get("version", 1)) + 1,
                "updated_at": now.astimezone(UTC).isoformat(),
            }
            if report is not None:
                response["report"] = _report_payload(report)
                attempt.broker_ticket = report.broker_ticket
                attempt.broker_code = report.broker_code
            attempt.status = status.value
            attempt.response_payload = response
            attempt.error_message = reason or None
            if status in {
                IdempotencyStatus.SUCCEEDED,
                IdempotencyStatus.PROTECTION_FAILED,
                IdempotencyStatus.REJECTED,
            }:
                attempt.completed_at = now.astimezone(UTC)
            session.commit()
            session.refresh(attempt)
            return self._record(attempt)

    async def update(
        self,
        idempotency_key: str,
        status: IdempotencyStatus,
        now: datetime,
        *,
        report: ExecutionReport | None = None,
        reason: str = "",
    ) -> ExecutionRecord:
        return await asyncio.to_thread(
            self._update_sync,
            idempotency_key,
            status,
            now,
            report,
            reason,
        )
