"""Persistent trade journal endpoint."""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.auth import ViewerPrincipal
from app.api.dependencies import SessionDependency
from app.api.schemas import TradeResponse
from app.db.models import Trade

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("", response_model=list[TradeResponse])
def list_trades(
    session: SessionDependency,
    principal: ViewerPrincipal,
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Trade]:
    del principal
    statement = select(Trade)
    if symbol:
        statement = statement.where(Trade.symbol == symbol)
    return list(session.scalars(statement.order_by(Trade.opened_at.desc()).limit(limit)))
