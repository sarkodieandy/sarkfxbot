"""Thread-safe health/readiness registry with heartbeat and disk checks."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    state: HealthState
    checked_at: datetime
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthRegistry:
    """Aggregates component checks without treating liveness as trading readiness."""

    REQUIRED_FOR_NEW_EXPOSURE = frozenset(
        {
            "broker",
            "clock",
            "configuration",
            "database",
            "execution_worker",
            "market_data",
            "redis",
            "strategy_worker",
        }
    )

    def __init__(self) -> None:
        self._lock = RLock()
        self._components: dict[str, ComponentHealth] = {}
        self._started_at = datetime.now(UTC)

    def update(
        self,
        name: str,
        state: HealthState,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ComponentHealth:
        component = ComponentHealth(
            name=name,
            state=state,
            checked_at=datetime.now(UTC),
            message=message,
            metadata=metadata or {},
        )
        with self._lock:
            self._components[name] = component
        return component

    def heartbeat(self, worker: str) -> ComponentHealth:
        return self.update(worker, HealthState.HEALTHY, "heartbeat received")

    def expire_heartbeats(self, maximum_age: timedelta) -> tuple[str, ...]:
        if maximum_age <= timedelta(0):
            raise ValueError("heartbeat maximum age must be positive")
        now = datetime.now(UTC)
        expired: list[str] = []
        with self._lock:
            for name, component in tuple(self._components.items()):
                if name.endswith("_worker") and now - component.checked_at > maximum_age:
                    expired.append(name)
                    self._components[name] = ComponentHealth(
                        name=name,
                        state=HealthState.UNHEALTHY,
                        checked_at=now,
                        message="heartbeat expired",
                    )
        return tuple(expired)

    def check_disk(
        self, path: str | Path = ".", minimum_free_percent: float = 5.0
    ) -> ComponentHealth:
        usage = shutil.disk_usage(path)
        free_percent = (usage.free / usage.total) * 100 if usage.total else 0.0
        state = (
            HealthState.HEALTHY if free_percent >= minimum_free_percent else HealthState.DEGRADED
        )
        return self.update(
            "disk",
            state,
            f"{free_percent:.1f}% free",
            {"free_bytes": usage.free, "total_bytes": usage.total},
        )

    @property
    def ready(self) -> bool:
        with self._lock:
            return all(
                self._components.get(name) is not None
                and self._components[name].state is HealthState.HEALTHY
                for name in self.REQUIRED_FOR_NEW_EXPOSURE
            )

    def components(self) -> tuple[ComponentHealth, ...]:
        """Return a stable snapshot for metrics and external monitors."""

        with self._lock:
            return tuple(self._components[name] for name in sorted(self._components))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            components = {
                name: {
                    "state": item.state.value,
                    "checked_at": item.checked_at.isoformat(),
                    "message": item.message,
                    "metadata": item.metadata,
                }
                for name, item in sorted(self._components.items())
            }
        return {
            "status": "ready" if self.ready else "not_ready",
            "live": True,
            "started_at": self._started_at.isoformat(),
            "components": components,
        }
