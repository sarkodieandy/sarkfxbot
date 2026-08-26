"""Open-position inspection and explicit manual-close endpoint."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.auth import TraderPrincipal, ViewerPrincipal
from app.api.dependencies import BrokerDependency, SessionDependency
from app.api.schemas import ClosePositionRequest, MessageResponse, PositionResponse
from app.db.models import Position
from app.db.repositories import AuditRepository, PositionRepository
from app.domain.errors import BrokerError

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=list[PositionResponse])
def list_positions(
    session: SessionDependency,
    principal: ViewerPrincipal,
    include_closed: bool = False,
) -> list[Position]:
    del principal
    statement = select(Position)
    if not include_closed:
        statement = statement.where(Position.closed_at.is_(None))
    return list(session.scalars(statement.order_by(Position.opened_at.desc()).limit(500)))


@router.post("/{position_id}/close", response_model=MessageResponse)
async def close_position(
    position_id: str,
    request: ClosePositionRequest,
    session: SessionDependency,
    broker: BrokerDependency,
    principal: TraderPrincipal,
) -> MessageResponse:
    position = session.get(Position, position_id)
    if position is None or position.closed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "open position not found")
    before_volume = position.current_volume
    requested_volume = request.volume
    if requested_volume is not None and requested_volume > before_volume:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "close volume is too large")
    try:
        report = await broker.close_position(position.broker_ticket, requested_volume)
    except (BrokerError, OSError, TimeoutError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if not report.success:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"broker rejected position close: {report.broker_code or 'UNKNOWN'}",
        )
    full_close = requested_volume is None or requested_volume == before_volume
    if full_close:
        PositionRepository(session).mark_closed(position.id, state="CLOSED")
    else:
        if requested_volume is None:
            raise RuntimeError("partial close requires an explicit volume")
        position.current_volume = before_volume - requested_volume
        session.flush()
    after_volume = position.current_volume
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="POSITION_CLOSE",
        resource_type="position",
        resource_id=position.id,
        before_data={"volume": str(before_volume)},
        after_data={
            "volume": str(after_volume),
            "closed_volume": str(requested_volume or before_volume),
            "broker_ticket": report.broker_ticket,
            "reason": request.reason,
        },
    )
    return MessageResponse(
        status="CLOSED" if full_close else "PARTIALLY_CLOSED",
        message="broker confirmed the close request",
        data={"position_id": position.id, "broker_ticket": position.broker_ticket},
    )
