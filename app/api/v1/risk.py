"""Risk configuration and current guardrail state endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.auth import AdminPrincipal, ViewerPrincipal
from app.api.dependencies import ControlsDependency, SessionDependency
from app.api.schemas import ConfigResponse, ConfigUpdateRequest
from app.db.repositories import AuditRepository, ConfigRepository
from app.domain.errors import ConfigurationError

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("", response_model=ConfigResponse)
def risk_config(
    controls: ControlsDependency,
    principal: ViewerPrincipal,
) -> ConfigResponse:
    del principal
    config = controls.risk_config()
    state = controls.snapshot()
    config["kill_switch"] = state.kill_switch
    config["auto_disabled"] = state.auto_disabled
    config["new_exposure_allowed"] = controls.new_exposure_allowed
    return ConfigResponse(config=config)


@router.post("/config", response_model=ConfigResponse)
def update_risk_config(
    request: ConfigUpdateRequest,
    controls: ControlsDependency,
    session: SessionDependency,
    principal: AdminPrincipal,
) -> ConfigResponse:
    before = controls.risk_config()
    try:
        config, checksum = controls.update_risk(request.values)
    except (ConfigurationError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    version = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    ConfigRepository(session).add_version(
        config_type="risk",
        version=version,
        payload=config,
        created_by_user_id=None,
        activate=True,
    )
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="RISK_CONFIG_CHANGED",
        resource_type="risk_config",
        resource_id=version,
        before_data=before,
        after_data={**config, "reason": request.reason},
    )
    return ConfigResponse(config=config, checksum=checksum)
