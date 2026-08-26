"""GoldFlow FastAPI application factory and process entrypoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from sqlalchemy import text

from app import __version__
from app.api.auth import AuthService
from app.api.middleware import install_http_middleware
from app.api.v1.router import router as v1_router
from app.api.websocket import websocket_bearer_token
from app.brokers.base import BrokerAdapter
from app.config.logging import configure_logging_from_settings
from app.config.settings import Settings, get_settings
from app.container import AppContainer, build_container
from app.db.repositories import ConfigRepository
from app.db.session import Database
from app.domain.errors import BrokerError, ConfigurationError, RiskRejectedError
from app.observability.health import HealthState
from app.observability.sentry import configure_sentry
from app.runtime_contract import (
    EVENT_CHANNEL,
    RUNTIME_CONFIG_TYPE,
    WORKER_COMPONENTS,
    DurableRuntimeState,
    heartbeat_key,
    parse_event_message,
)

logger = logging.getLogger("goldflow.api")


def _load_durable_controls(container: AppContainer) -> None:
    """Hydrate active PostgreSQL controls so API restarts preserve operator intent."""

    with container.database.session_scope() as session:
        repository = ConfigRepository(session)
        runtime = repository.active_version(RUNTIME_CONFIG_TYPE)
        if runtime is not None:
            state = DurableRuntimeState.from_payload(
                runtime.payload,
                default_mode=container.settings.trading_mode,
                default_updated_at=runtime.activated_at or runtime.created_at,
            )
            container.controls.restore_runtime(state)
        strategy = repository.active_version("strategy")
        if strategy is not None:
            container.controls.restore_strategy(dict(strategy.payload))
        risk = repository.active_version("risk")
        if risk is not None:
            container.controls.restore_risk(dict(risk.payload))


async def _refresh_worker_health(container: AppContainer) -> None:
    if container.settings.app_env.value == "test" and container.redis is None:
        for component in WORKER_COMPONENTS:
            container.health.update(component, HealthState.HEALTHY, "embedded test worker")
        return
    if container.redis is None:
        for component in WORKER_COMPONENTS:
            container.health.update(component, HealthState.UNHEALTHY, "Redis heartbeat unavailable")
        return
    for component in WORKER_COMPONENTS:
        try:
            raw = await container.redis.get(heartbeat_key(component))
            if raw is None:
                container.health.update(component, HealthState.UNHEALTHY, "heartbeat missing")
                continue
            timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)
            age = (datetime.now(UTC) - timestamp).total_seconds()
            maximum_age = container.settings.heartbeat_interval_seconds * 2
            container.health.update(
                component,
                HealthState.HEALTHY if 0 <= age <= maximum_age else HealthState.UNHEALTHY,
                f"heartbeat age {age:.3f}s",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            container.health.update(component, HealthState.UNHEALTHY, type(exc).__name__)


async def _redis_event_listener(container: AppContainer) -> None:
    """Bridge worker Redis events into this API process's WebSocket clients."""

    if container.redis is None:
        return
    try:
        async with container.redis.pubsub() as pubsub:
            await pubsub.subscribe(EVENT_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    event, payload = parse_event_message(message["data"])
                except (KeyError, TypeError, ValueError):
                    logger.warning(
                        "invalid_worker_event_discarded",
                        extra={"service": "api", "event": "INVALID_WORKER_EVENT_DISCARDED"},
                    )
                    continue
                await container.websocket_hub.broadcast(event, payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # Redis pub/sub boundary; readiness polling remains active
        logger.error(
            "worker_event_listener_stopped",
            extra={
                "service": "api",
                "event": "WORKER_EVENT_LISTENER_STOPPED",
                "error_type": type(exc).__name__,
            },
        )


async def _initialize_services(container: AppContainer) -> None:
    health = container.health
    if (
        container.settings.app_env.value == "test"
        and container.database.engine.dialect.name == "sqlite"
    ):
        await asyncio.to_thread(container.database.create_schema)
    health.check_disk()
    health.update("clock", HealthState.HEALTHY, "UTC clock available")
    readiness_errors = container.settings.production_readiness_errors
    health.update(
        "configuration",
        HealthState.UNHEALTHY if readiness_errors else HealthState.HEALTHY,
        "; ".join(readiness_errors) if readiness_errors else "safety configuration valid",
    )
    try:
        await asyncio.to_thread(_database_ping, container.database)
        health.update("database", HealthState.HEALTHY, "database reachable")
    except Exception as exc:  # database driver boundary
        health.update("database", HealthState.UNHEALTHY, type(exc).__name__)
    else:
        try:
            await asyncio.to_thread(_load_durable_controls, container)
        except (ConfigurationError, LookupError, TypeError, ValueError) as exc:
            health.update("configuration", HealthState.UNHEALTHY, type(exc).__name__)

    redis_client: Redis | None = None
    try:
        redis_client = Redis.from_url(
            container.settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        await redis_client.ping()
        container.redis = redis_client
        health.update("redis", HealthState.HEALTHY, "Redis reachable")
    except Exception as exc:  # Redis is a coordination dependency, not persistent truth
        if redis_client is not None:
            await redis_client.aclose()
        container.redis = None
        redis_state = (
            HealthState.DEGRADED
            if container.settings.app_env.value == "test"
            else HealthState.UNHEALTHY
        )
        health.update(
            "redis",
            redis_state,
            f"unavailable: {type(exc).__name__}",
            {"test_environment_tolerated": container.settings.app_env.value == "test"},
        )

    if container.broker is None:
        health.update("broker", HealthState.UNHEALTHY, "broker is not configured")
        health.update("market_data", HealthState.UNHEALTHY, "broker is not configured")
    else:
        try:
            await container.broker.connect()
            broker_health = await container.broker.health_check()
            health.update(
                "broker",
                HealthState.HEALTHY if broker_health.healthy else HealthState.UNHEALTHY,
                broker_health.message,
            )
            if broker_health.healthy:
                symbol = await container.broker.resolve_symbol(container.settings.canonical_symbol)
                tick = await container.broker.get_tick(symbol.name)
                age = (datetime.now(UTC) - tick.timestamp).total_seconds()
                health.update(
                    "market_data",
                    (
                        HealthState.HEALTHY
                        if 0 <= age <= container.settings.max_tick_age_seconds
                        else HealthState.UNHEALTHY
                    ),
                    f"tick age {age:.3f}s",
                )
            else:
                health.update("market_data", HealthState.UNHEALTHY, "broker health check failed")
        except (BrokerError, OSError, TimeoutError) as exc:
            health.update("broker", HealthState.UNHEALTHY, type(exc).__name__)
            health.update("market_data", HealthState.UNHEALTHY, "market data unavailable")

    await _refresh_worker_health(container)
    if container.redis is not None:
        container.event_listener_task = asyncio.create_task(
            _redis_event_listener(container), name="goldflow-api-event-listener"
        )
    _sync_health_metrics(container)


def _sync_health_metrics(container: AppContainer) -> None:
    for component in container.health.components():
        container.metrics.health.labels(component=component.name).set(
            1 if component.state is HealthState.HEALTHY else 0
        )


def _database_ping(database: Database) -> None:
    with database.engine.connect() as connection:
        connection.execute(text("SELECT 1"))


async def _shutdown_services(container: AppContainer) -> None:
    if container.event_listener_task is not None:
        container.event_listener_task.cancel()
        with suppress(asyncio.CancelledError):
            await container.event_listener_task
        container.event_listener_task = None
    if container.broker is not None:
        try:
            await container.broker.disconnect()
        except (BrokerError, OSError, TimeoutError):
            logger.exception(
                "broker_disconnect_failed", extra={"event": "BROKER_DISCONNECT_FAILED"}
            )
    if container.redis is not None:
        await container.redis.aclose()
    container.database.dispose()


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    broker: BrokerAdapter | None = None,
) -> FastAPI:
    configured = settings or get_settings()
    container = build_container(configured, database=database, broker=broker)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        configure_logging_from_settings(configured)
        configure_sentry(configured.sentry_dsn, configured.app_env.value, __version__)
        await _initialize_services(container)
        logger.info(
            "application_started",
            extra={
                "service": "api",
                "event": "APPLICATION_STARTED",
                "environment": configured.trading_env.value,
                "mode": configured.trading_mode.value,
            },
        )
        try:
            yield
        finally:
            await _shutdown_services(container)

    app = FastAPI(
        title="GoldFlow Trading Engine",
        description=(
            "Risk-first XAUUSD signal, demo execution, trade management, and analytics API. "
            "Confidence scores are deterministic setup scores, not win probabilities."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )
    install_http_middleware(app, configured.rate_limit_per_minute)
    app.include_router(v1_router)

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(
        request: Request, exc: ConfigurationError
    ) -> JSONResponse:
        del request
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.exception_handler(RiskRejectedError)
    async def risk_error_handler(request: Request, exc: RiskRejectedError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc), "trade_allowed": False},
        )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        await _refresh_worker_health(container)
        _sync_health_metrics(container)
        return container.health.snapshot()

    @app.get("/ready", tags=["system"])
    async def ready() -> JSONResponse:
        await _refresh_worker_health(container)
        snapshot = container.health.snapshot()
        code = status.HTTP_200_OK if container.health.ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(status_code=code, content=snapshot)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        _sync_health_metrics(container)
        return Response(container.metrics.render(), media_type="text/plain; version=0.0.4")

    @app.websocket("/ws")
    async def websocket_updates(websocket: WebSocket, token: str = "") -> None:
        encoded_token = websocket_bearer_token(websocket, token)
        if encoded_token is None:
            await websocket.close(code=4401, reason="authentication required")
            return
        try:
            principal = AuthService(configured).decode_access_token(encoded_token)
        except HTTPException:
            await websocket.close(code=4401, reason="authentication required")
            return
        await container.websocket_hub.connect(websocket)
        try:
            await websocket.send_json(
                {
                    "event": "BOT_STATUS",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "payload": {
                        **container.health.snapshot(),
                        "principal_role": principal.role.value,
                    },
                }
            )
            while True:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json(
                        {"event": "PONG", "timestamp": datetime.now(UTC).isoformat()}
                    )
        except WebSocketDisconnect:
            return
        finally:
            await container.websocket_hub.disconnect(websocket)

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    run()
