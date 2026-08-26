"""Signal journal and semi-automatic approval endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.auth import TraderPrincipal, ViewerPrincipal
from app.api.dependencies import SessionDependency
from app.api.schemas import ApprovalRequest, MessageResponse, SignalResponse
from app.db.models import Signal
from app.db.repositories import AuditRepository, SignalRepository

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=list[SignalResponse])
def list_signals(
    session: SessionDependency,
    principal: ViewerPrincipal,
    signal_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Signal]:
    del principal
    statement = select(Signal)
    if signal_status:
        statement = statement.where(Signal.status == signal_status.upper())
    return list(session.scalars(statement.order_by(Signal.created_at.desc()).limit(limit)))


@router.get("/{signal_id}", response_model=SignalResponse)
def get_signal(
    signal_id: str,
    session: SessionDependency,
    principal: ViewerPrincipal,
) -> Signal:
    del principal
    signal = session.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "signal not found")
    return signal


@router.post("/{signal_id}/approve", response_model=MessageResponse)
def approve_signal(
    signal_id: str,
    request: ApprovalRequest,
    session: SessionDependency,
    principal: TraderPrincipal,
) -> MessageResponse:
    repository = SignalRepository(session)
    signal = repository.get(signal_id)
    if signal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "signal not found")
    before = signal.status
    if signal.expires_at is not None and signal.expires_at <= datetime.now(UTC):
        repository.set_status(signal_id, "EXPIRED")
        AuditRepository(session).record(
            actor_type="USER",
            actor_id=principal.subject,
            action="SIGNAL_APPROVAL_REJECTED",
            resource_type="signal",
            resource_id=signal_id,
            before_data={"status": before},
            after_data={"status": "EXPIRED", "reason": request.reason},
        )
        # HTTP exceptions roll the request transaction back in the session dependency.
        # Persist this terminal transition before returning the conflict response.
        session.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "signal has expired")
    if signal.status not in {"ACTIVE", "APPROVAL_REQUIRED"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"signal cannot be approved from status {signal.status}",
        )
    target = "APPROVED" if request.approved else "REJECTED"
    repository.set_status(signal_id, target)
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="SIGNAL_APPROVAL" if request.approved else "SIGNAL_REJECTION",
        resource_type="signal",
        resource_id=signal_id,
        before_data={"status": before},
        after_data={"status": target, "reason": request.reason},
    )
    return MessageResponse(
        status=target,
        message=(
            "signal approved and queued for mandatory risk revalidation"
            if request.approved
            else "signal rejected"
        ),
        data={"signal_id": signal_id},
    )
