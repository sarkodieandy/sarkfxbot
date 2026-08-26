"""Repositories for audit, versioned configuration, and operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    BotInstance,
    ConfigVersion,
    OutboxEvent,
    StrategyConfig,
    SystemEvent,
)
from app.db.models.base import utc_now
from app.db.repositories.base import Repository, redact_sensitive_json


class AuditRepository(Repository[AuditLog]):
    model = AuditLog

    def record(self, **values: Any) -> AuditLog:
        """Append an audit record; audit rows are intentionally never updated here."""

        for field in ("before_data", "after_data"):
            if field in values and values[field] is not None:
                values[field] = redact_sensitive_json(values[field])
        return self.add(AuditLog(**values))

    def recent(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        statement = select(AuditLog)
        if resource_type is not None:
            statement = statement.where(AuditLog.resource_type == resource_type)
        if resource_id is not None:
            statement = statement.where(AuditLog.resource_id == resource_id)
        if actor_id is not None:
            statement = statement.where(AuditLog.actor_id == actor_id)
        return tuple(
            self.session.scalars(statement.order_by(AuditLog.created_at.desc()).limit(limit))
        )


class ConfigRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_strategy_config(self, **values: Any) -> StrategyConfig:
        if "parameters" in values:
            values["parameters"] = redact_sensitive_json(values["parameters"])
        config = StrategyConfig(**values)
        self.session.add(config)
        self.session.flush()
        return config

    def latest_strategy_config(
        self, strategy_id: str, *, enabled_only: bool = False
    ) -> StrategyConfig | None:
        statement = select(StrategyConfig).where(StrategyConfig.strategy_id == strategy_id)
        if enabled_only:
            statement = statement.where(StrategyConfig.is_enabled.is_(True))
        return self.session.scalar(
            statement.order_by(
                StrategyConfig.effective_from.desc(), StrategyConfig.created_at.desc()
            ).limit(1)
        )

    def add_version(
        self,
        *,
        config_type: str,
        version: str,
        payload: Mapping[str, Any],
        created_by_user_id: str | None = None,
        activate: bool = False,
    ) -> ConfigVersion:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        version_row = ConfigVersion(
            config_type=config_type,
            version=version,
            checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            payload=redact_sensitive_json(dict(payload)),
            created_by_user_id=created_by_user_id,
        )
        self.session.add(version_row)
        self.session.flush()
        if activate:
            self.activate(version_row.id)
        return version_row

    def active_version(self, config_type: str) -> ConfigVersion | None:
        return self.session.scalar(
            select(ConfigVersion).where(
                ConfigVersion.config_type == config_type,
                ConfigVersion.is_active.is_(True),
            )
        )

    def activate(self, version_id: str) -> ConfigVersion:
        target = self.session.get(ConfigVersion, version_id)
        if target is None:
            raise LookupError(f"config version {version_id} does not exist")
        active_rows = self.session.scalars(
            select(ConfigVersion).where(
                ConfigVersion.config_type == target.config_type,
                ConfigVersion.is_active.is_(True),
            )
        )
        for row in active_rows:
            row.is_active = False
            row.activated_at = None
        target.is_active = True
        target.activated_at = utc_now()
        self.session.flush()
        return target


class SystemEventRepository(Repository[SystemEvent]):
    model = SystemEvent

    def record(self, **values: Any) -> SystemEvent:
        if "payload" in values:
            values["payload"] = redact_sensitive_json(values["payload"])
        return self.add(SystemEvent(**values))

    def recent(
        self,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> Sequence[SystemEvent]:
        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        statement = select(SystemEvent)
        if event_type is not None:
            statement = statement.where(SystemEvent.event_type == event_type)
        if severity is not None:
            statement = statement.where(SystemEvent.severity == severity)
        return tuple(
            self.session.scalars(statement.order_by(SystemEvent.created_at.desc()).limit(limit))
        )


class HeartbeatRepository(Repository[BotInstance]):
    model = BotInstance

    def by_instance_key(self, instance_key: str) -> BotInstance | None:
        return self.session.scalar(
            select(BotInstance).where(BotInstance.instance_key == instance_key)
        )

    def register(
        self,
        *,
        instance_key: str,
        hostname: str,
        version: str,
        environment: str,
        metadata: Mapping[str, Any] | None = None,
        at: datetime | None = None,
    ) -> BotInstance:
        now = at or utc_now()
        instance = self.by_instance_key(instance_key)
        if instance is None:
            instance = BotInstance(
                instance_key=instance_key,
                hostname=hostname,
                version=version,
                environment=environment,
                status="RUNNING",
                started_at=now,
                heartbeat_at=now,
                metadata_json=redact_sensitive_json(dict(metadata or {})),
            )
            return self.add(instance)
        instance.hostname = hostname
        instance.version = version
        instance.environment = environment
        instance.status = "RUNNING"
        instance.heartbeat_at = now
        instance.stopped_at = None
        if metadata is not None:
            instance.metadata_json = redact_sensitive_json(dict(metadata))
        self.session.flush()
        return instance

    def beat(self, instance_key: str, *, at: datetime | None = None) -> BotInstance:
        instance = self.by_instance_key(instance_key)
        if instance is None:
            raise LookupError(f"bot instance {instance_key} does not exist")
        instance.heartbeat_at = at or utc_now()
        instance.status = "RUNNING"
        self.session.flush()
        return instance

    def mark_stopped(self, instance_key: str, *, at: datetime | None = None) -> BotInstance:
        instance = self.by_instance_key(instance_key)
        if instance is None:
            raise LookupError(f"bot instance {instance_key} does not exist")
        instance.stopped_at = at or utc_now()
        instance.status = "STOPPED"
        self.session.flush()
        return instance

    def stale_before(self, threshold: datetime) -> Sequence[BotInstance]:
        return tuple(
            self.session.scalars(
                select(BotInstance).where(
                    BotInstance.status == "RUNNING",
                    BotInstance.heartbeat_at < threshold,
                )
            )
        )


class OutboxRepository(Repository[OutboxEvent]):
    model = OutboxEvent

    def enqueue(
        self,
        *,
        deduplication_key: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        available_at: datetime | None = None,
    ) -> OutboxEvent:
        return self.add(
            OutboxEvent(
                deduplication_key=deduplication_key,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=redact_sensitive_json(dict(payload)),
                available_at=available_at or utc_now(),
            )
        )

    def pending(self, *, now: datetime | None = None, limit: int = 100) -> Sequence[OutboxEvent]:
        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        return tuple(
            self.session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == "PENDING",
                    OutboxEvent.available_at <= (now or utc_now()),
                )
                .order_by(OutboxEvent.created_at)
                .limit(limit)
            )
        )

    def mark_published(self, event_id: str, *, at: datetime | None = None) -> OutboxEvent:
        event = self.get(event_id)
        if event is None:
            raise LookupError(f"outbox event {event_id} does not exist")
        event.status = "PUBLISHED"
        event.published_at = at or utc_now()
        event.locked_at = None
        self.session.flush()
        return event

    def mark_failed(self, event_id: str, error: str) -> OutboxEvent:
        event = self.get(event_id)
        if event is None:
            raise LookupError(f"outbox event {event_id} does not exist")
        event.status = "PENDING"
        event.attempt_count += 1
        event.last_error = error
        event.locked_at = None
        self.session.flush()
        return event
