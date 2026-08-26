"""Shared repository operations that never own transaction boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models import Base


class Repository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: str) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        if limit < 1 or limit > 1_000 or offset < 0:
            raise ValueError("limit must be 1..1000 and offset cannot be negative")
        statement: Select[tuple[ModelT]] = select(self.model).offset(offset).limit(limit)
        return tuple(self.session.scalars(statement))


def enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def json_safe(value: Any) -> Any:
    """Convert domain values to deterministic JSON-compatible primitives."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Enum)):
        return str(getattr(value, "value", value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "jwt",
        "password",
        "secret",
        "token",
    }
)


def redact_sensitive_json(value: Any) -> Any:
    """Remove credential-like fields before durable JSON persistence."""

    safe = json_safe(value)
    if isinstance(safe, dict):
        result: dict[str, Any] = {}
        for key, item in safe.items():
            normalized = key.lower().replace("-", "_")
            result[key] = (
                "[REDACTED]"
                if any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)
                else redact_sensitive_json(item)
            )
        return result
    if isinstance(safe, list):
        return [redact_sensitive_json(item) for item in safe]
    return safe
