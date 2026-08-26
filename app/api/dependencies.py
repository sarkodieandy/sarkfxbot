"""FastAPI dependency adapters for explicit application services."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.runtime import RuntimeControls
from app.brokers.base import BrokerAdapter
from app.container import AppContainer


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, AppContainer):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="application services are not initialized",
        )
    return container


ContainerDependency = Annotated[AppContainer, Depends(get_container)]


def get_broker(container: ContainerDependency) -> BrokerAdapter:
    if container.broker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="broker adapter is not configured",
        )
    return container.broker


BrokerDependency = Annotated[BrokerAdapter, Depends(get_broker)]


def get_controls(container: ContainerDependency) -> RuntimeControls:
    return container.controls


ControlsDependency = Annotated[RuntimeControls, Depends(get_controls)]


def get_db_session(container: ContainerDependency) -> Generator[Session, None, None]:
    session = container.database.session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


SessionDependency = Annotated[Session, Depends(get_db_session)]
