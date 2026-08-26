"""Versioned strategy configuration endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.api.auth import AdminPrincipal, ViewerPrincipal
from app.api.dependencies import ControlsDependency, SessionDependency
from app.api.schemas import ConfigResponse, ConfigUpdateRequest
from app.db.repositories import AuditRepository, ConfigRepository
from app.domain.errors import ConfigurationError

router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.get("", response_model=ConfigResponse)
def strategy_config(
    controls: ControlsDependency,
    principal: ViewerPrincipal,
) -> ConfigResponse:
    del principal
    return ConfigResponse(config=controls.strategy_config())


@router.post("/config", response_model=ConfigResponse)
def update_strategy_config(
    request: ConfigUpdateRequest,
    controls: ControlsDependency,
    session: SessionDependency,
    principal: AdminPrincipal,
) -> ConfigResponse:
    before = controls.strategy_config()
    try:
        config, checksum = controls.update_strategy(request.values)
    except (ConfigurationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    ConfigRepository(session).add_version(
        config_type="strategy",
        version=str(config["version"]),
        payload=config,
        created_by_user_id=None,
        activate=True,
    )
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="STRATEGY_CONFIG_CHANGED",
        resource_type="strategy_config",
        resource_id=str(config["strategy_id"]),
        before_data=before,
        after_data={**config, "reason": request.reason},
    )
    return ConfigResponse(config=config, checksum=checksum)
