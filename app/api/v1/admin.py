"""Admin-only mode, kill-switch, and reconciliation controls."""

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import AdminPrincipal
from app.api.dependencies import (
    BrokerDependency,
    ContainerDependency,
    ControlsDependency,
    SessionDependency,
)
from app.api.schemas import (
    CircuitResetRequest,
    KillSwitchRequest,
    MessageResponse,
    ModeRequest,
    ModeResponse,
)
from app.config.settings import Settings
from app.db.models import BrokerAccount, DailyMetric, Trade
from app.db.repositories import (
    AuditRepository,
    ConfigRepository,
    OrderRepository,
    PositionRepository,
)
from app.domain.enums import TradingEnvironment, TradingMode
from app.domain.errors import BrokerError, ConfigurationError
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.models import RiskLimits
from app.runtime_contract import RUNTIME_CONFIG_TYPE
from app.workers.persistence import SQLAlchemyCircuitStateStore

router = APIRouter(tags=["admin"])


def _persist_runtime_controls(
    session: Session,
    controls: ControlsDependency,
    *,
    actor_id: str,
) -> None:
    state = controls.durable_state()
    version = f"{state.updated_at.strftime('%Y%m%d%H%M%S%f')}-{uuid4().hex[:8]}"
    ConfigRepository(session).add_version(
        config_type=RUNTIME_CONFIG_TYPE,
        version=version,
        payload=state.to_payload(),
        created_by_user_id=None,
        activate=True,
    )
    # The external JWT subject is retained in the append-only audit row; it is
    # not forced into the local users table because Supabase-compatible issuers
    # may own user identity outside this database.
    del actor_id


def _demo_validation_evidence(session: Session, settings: Settings) -> tuple[bool, dict[str, Any]]:
    """Evaluate durable demo evidence; these thresholds do not predict future results."""

    closed_filter = (Trade.environment == "demo", Trade.closed_at.is_not(None))
    closed_trades = session.scalar(select(func.count(Trade.id)).where(*closed_filter)) or 0
    gross_profit = session.scalar(
        select(func.coalesce(func.sum(Trade.net_pnl), 0)).where(*closed_filter, Trade.net_pnl > 0)
    ) or Decimal("0")
    gross_loss = abs(
        session.scalar(
            select(func.coalesce(func.sum(Trade.net_pnl), 0)).where(
                *closed_filter, Trade.net_pnl < 0
            )
        )
        or Decimal("0")
    )
    daily_metric_count = (
        session.scalar(
            select(func.count(DailyMetric.id))
            .join(BrokerAccount, DailyMetric.broker_account_id == BrokerAccount.id)
            .where(BrokerAccount.account_type == "DEMO")
        )
        or 0
    )
    maximum_drawdown = session.scalar(
        select(func.max(DailyMetric.max_drawdown))
        .join(BrokerAccount, DailyMetric.broker_account_id == BrokerAccount.id)
        .where(BrokerAccount.account_type == "DEMO")
    )

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
        profit_factor_passed = profit_factor >= settings.minimum_demo_profit_factor
        profit_factor_value: str | None = str(profit_factor)
    else:
        profit_factor_passed = gross_profit > 0
        profit_factor_value = "unbounded" if gross_profit > 0 else None

    checks = {
        "minimum_closed_trades": int(closed_trades) >= settings.minimum_demo_trades,
        "maximum_drawdown": (
            maximum_drawdown is not None and maximum_drawdown <= settings.maximum_demo_drawdown
        ),
        "minimum_profit_factor": profit_factor_passed,
        "daily_metrics_present": int(daily_metric_count) > 0,
    }
    evidence: dict[str, Any] = {
        "required": settings.require_demo_validation_for_live,
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "closed_demo_trades": int(closed_trades),
            "maximum_demo_drawdown": (
                str(maximum_drawdown) if maximum_drawdown is not None else None
            ),
            "demo_profit_factor": profit_factor_value,
            "daily_metric_rows": int(daily_metric_count),
        },
        "thresholds": {
            "minimum_demo_trades": settings.minimum_demo_trades,
            "maximum_demo_drawdown": str(settings.maximum_demo_drawdown),
            "minimum_demo_profit_factor": str(settings.minimum_demo_profit_factor),
        },
        "disclaimer": "Demo metrics are safety gates, not predictions or guarantees.",
    }
    if not settings.require_demo_validation_for_live:
        evidence["passed"] = True
        evidence["checks"] = {"explicit_policy_requirement": False}
    return bool(evidence["passed"]), evidence


def _record_rejected_mode_change(
    session: Session,
    *,
    actor_id: str,
    requested_mode: TradingMode,
    current_mode: TradingMode,
    reason: str,
    rejection: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=actor_id,
        action="TRADING_MODE_CHANGE_REJECTED",
        resource_type="runtime_control",
        resource_id="trading_mode",
        before_data={"mode": current_mode.value},
        after_data={
            "requested_mode": requested_mode.value,
            "reason": reason,
            "rejection": rejection,
            "demo_validation": evidence,
        },
    )
    session.commit()


@router.post("/mode", response_model=ModeResponse)
def change_mode(
    request: ModeRequest,
    controls: ControlsDependency,
    container: ContainerDependency,
    session: SessionDependency,
    principal: AdminPrincipal,
) -> ModeResponse:
    before = controls.snapshot()
    demo_evidence: dict[str, Any] | None = None
    if (
        request.mode is TradingMode.AUTO
        and container.settings.trading_env is TradingEnvironment.PRODUCTION
    ):
        passed, demo_evidence = _demo_validation_evidence(session, container.settings)
        if not passed:
            message = "production AUTO requires validated durable demo evidence"
            _record_rejected_mode_change(
                session,
                actor_id=principal.subject,
                requested_mode=request.mode,
                current_mode=before.mode,
                reason=request.reason,
                rejection=message,
                evidence=demo_evidence,
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"message": message, "demo_validation": demo_evidence},
            )
    try:
        after = controls.set_mode(request.mode)
    except ConfigurationError as exc:
        _record_rejected_mode_change(
            session,
            actor_id=principal.subject,
            requested_mode=request.mode,
            current_mode=before.mode,
            reason=request.reason,
            rejection=str(exc),
            evidence=demo_evidence,
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="TRADING_MODE_CHANGED",
        resource_type="runtime_control",
        resource_id="trading_mode",
        before_data={"mode": before.mode.value},
        after_data={
            "mode": after.mode.value,
            "reason": request.reason,
            "demo_validation": demo_evidence,
        },
    )
    _persist_runtime_controls(session, controls, actor_id=principal.subject)
    return ModeResponse.model_validate(after)


@router.post("/admin/kill-switch", response_model=ModeResponse)
async def kill_switch(
    request: KillSwitchRequest,
    controls: ControlsDependency,
    container: ContainerDependency,
    session: SessionDependency,
    principal: AdminPrincipal,
) -> ModeResponse:
    before = controls.snapshot()
    after = controls.set_kill_switch(request.enabled, request.reason)
    cancelled: list[str] = []
    failures: list[str] = []
    if request.enabled:
        if container.broker is None:
            failures.append("BROKER_UNAVAILABLE_RECONCILIATION_REQUIRED")
        else:
            try:
                for order in await container.broker.get_orders():
                    if await container.broker.cancel_order(order.ticket):
                        cancelled.append(order.ticket)
                    else:
                        failures.append(order.ticket)
            except (BrokerError, OSError, TimeoutError):
                failures.append("BROKER_UNAVAILABLE_RECONCILIATION_REQUIRED")
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="KILL_SWITCH_ACTIVATED" if request.enabled else "KILL_SWITCH_DEACTIVATED",
        resource_type="runtime_control",
        resource_id="kill_switch",
        before_data={"enabled": before.kill_switch},
        after_data={
            "enabled": after.kill_switch,
            "reason": request.reason,
            "cancelled_pending_tickets": cancelled,
            "cancellation_failures": failures,
        },
    )
    _persist_runtime_controls(session, controls, actor_id=principal.subject)
    return ModeResponse.model_validate(
        {
            "mode": after.mode,
            "kill_switch": after.kill_switch,
            "kill_switch_reason": after.kill_switch_reason,
            "auto_disabled": after.auto_disabled,
            "updated_at": after.updated_at,
            "pending_orders_cancelled": cancelled,
            "cancellation_failures": failures,
            "reconciliation_required": bool(failures),
        }
    )


@router.post("/admin/reconcile", response_model=MessageResponse)
async def reconcile(
    session: SessionDependency,
    broker: BrokerDependency,
    principal: AdminPrincipal,
) -> MessageResponse:
    try:
        broker_positions = await broker.get_positions()
        broker_orders = await broker.get_orders()
    except (BrokerError, OSError, TimeoutError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    db_positions = PositionRepository(session).list_open()
    db_orders = OrderRepository(session).pending()
    broker_position_tickets = {item.ticket for item in broker_positions}
    db_position_tickets = {item.broker_ticket for item in db_positions}
    broker_order_tickets = {item.ticket for item in broker_orders}
    db_order_tickets = {item.broker_ticket for item in db_orders if item.broker_ticket}
    mismatches = {
        "broker_only_positions": sorted(broker_position_tickets - db_position_tickets),
        "database_only_positions": sorted(db_position_tickets - broker_position_tickets),
        "broker_only_orders": sorted(broker_order_tickets - db_order_tickets),
        "database_only_orders": sorted(db_order_tickets - broker_order_tickets),
    }
    total = sum(len(value) for value in mismatches.values())
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="BROKER_RECONCILIATION",
        resource_type="system",
        resource_id="broker_state",
        before_data=None,
        after_data={"mismatches": mismatches},
    )
    return MessageResponse(
        status="MATCHED" if total == 0 else "MISMATCH",
        message=(
            "broker and database state match"
            if total == 0
            else "unsafe mismatches recorded; execution remains fail-closed pending repair"
        ),
        data=mismatches,
    )


@router.post("/admin/circuit-reset", response_model=MessageResponse)
async def reset_circuit_breaker(
    request: CircuitResetRequest,
    session: SessionDependency,
    broker: BrokerDependency,
    controls: ControlsDependency,
    container: ContainerDependency,
    principal: AdminPrincipal,
) -> MessageResponse:
    """Manually clear durable drawdown locks after an authorized review."""

    try:
        account = await broker.get_account()
    except (BrokerError, OSError, TimeoutError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    circuit = CircuitBreaker(
        SQLAlchemyCircuitStateStore(container.database.session_factory),
        RiskLimits(),
    )
    before = await circuit.get(account.account_id, controls.snapshot().updated_at)
    controls.reset_drawdown_protection()
    safe_runtime = controls.set_mode(TradingMode.SIGNAL)
    after = await circuit.manual_reset(
        account.account_id,
        safe_runtime.updated_at,
        authorized=True,
        clear_kill_switch=False,
    )
    _persist_runtime_controls(session, controls, actor_id=principal.subject)
    AuditRepository(session).record(
        actor_type="USER",
        actor_id=principal.subject,
        action="CIRCUIT_BREAKER_MANUAL_RESET",
        resource_type="runtime_control",
        resource_id=account.account_id,
        before_data={
            "daily_locked": before.daily_locked,
            "weekly_locked": before.weekly_locked,
            "account_locked": before.account_locked,
            "manual_reenable_required": before.manual_reenable_required,
        },
        after_data={
            "daily_locked": after.daily_locked,
            "weekly_locked": after.weekly_locked,
            "account_locked": after.account_locked,
            "manual_reenable_required": after.manual_reenable_required,
            "reason": request.reason,
        },
    )
    return MessageResponse(
        status="RESET",
        message="durable circuit locks cleared; normal risk gates remain active",
        data={
            "account_id": account.account_id,
            "mode": safe_runtime.mode.value,
            "kill_switch_active": after.kill_switch,
            "manual_reenable_required": after.manual_reenable_required,
        },
    )
