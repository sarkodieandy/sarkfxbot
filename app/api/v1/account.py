"""Broker account and symbol discovery endpoints."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from app.api.auth import ViewerPrincipal
from app.api.dependencies import BrokerDependency
from app.api.schemas import AccountResponse, SymbolResponse
from app.domain.errors import BrokerError

router = APIRouter(tags=["broker"])


@router.get("/account", response_model=AccountResponse)
async def account(
    broker: BrokerDependency,
    principal: ViewerPrincipal,
) -> AccountResponse:
    del principal
    try:
        snapshot = await broker.get_account()
    except (BrokerError, OSError, TimeoutError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return AccountResponse.model_validate(asdict(snapshot))


@router.get("/symbols", response_model=list[SymbolResponse])
async def symbols(
    broker: BrokerDependency,
    principal: ViewerPrincipal,
) -> list[SymbolResponse]:
    del principal
    try:
        values = await broker.get_symbols()
    except (BrokerError, OSError, TimeoutError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return [SymbolResponse.model_validate(asdict(value)) for value in values]
