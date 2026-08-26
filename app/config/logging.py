"""Structured logging with correlation context and defensive secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from pydantic import SecretStr

REDACTED = "[REDACTED]"
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "goldflow_correlation_id", default=None
)
_SENSITIVE_FRAGMENTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "jwt",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
    }
)
_STANDARD_LOG_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__)


def bind_correlation_id(correlation_id: str) -> contextvars.Token[str | None]:
    """Bind a non-empty correlation ID in the current async/thread context."""

    normalized = correlation_id.strip()
    if not normalized:
        raise ValueError("correlation_id cannot be empty")
    return _correlation_id.set(normalized)


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Restore the correlation context associated with *token*."""

    _correlation_id.reset(token)


def clear_correlation_id() -> None:
    _correlation_id.set(None)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class SecretRedactor:
    """Recursively redact sensitive fields and configured literal values."""

    def __init__(self, secret_values: Sequence[str] = ()) -> None:
        self._secret_values = tuple(
            sorted(
                {value for value in secret_values if value},
                key=len,
                reverse=True,
            )
        )

    @staticmethod
    def _sensitive_key(key: object) -> bool:
        normalized = str(key).lower().replace("-", "_")
        return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)

    def redact_text(self, value: str) -> str:
        redacted = value.replace("\r", "\\r").replace("\n", "\\n")
        for secret in self._secret_values:
            redacted = redacted.replace(secret, REDACTED)
        return redacted

    def redact(self, value: Any, *, key: object | None = None) -> Any:
        if key is not None and self._sensitive_key(key):
            return REDACTED
        if isinstance(value, SecretStr):
            return REDACTED
        if isinstance(value, Mapping):
            return {
                str(item_key): self.redact(item_value, key=item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.redact_text(str(value))


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record using UTC timestamps."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or SecretRedactor()

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.redactor.redact(getattr(record, "service", record.name.split(".")[0])),
            "event": self.redactor.redact(getattr(record, "event", "LOG_EVENT")),
            "symbol": self.redactor.redact(getattr(record, "symbol", None)),
            "signal_id": self.redactor.redact(getattr(record, "signal_id", None)),
            "trade_id": self.redactor.redact(getattr(record, "trade_id", None)),
            "broker_ticket": self.redactor.redact(getattr(record, "broker_ticket", None)),
            "account": self.redactor.redact(getattr(record, "account", None)),
            "environment": self.redactor.redact(getattr(record, "environment", None)),
            "message": self.redactor.redact_text(record.getMessage()),
            "metadata": self.redactor.redact(getattr(record, "metadata", {})),
        }
        correlation_id = getattr(record, "correlation_id", None) or get_correlation_id()
        if correlation_id:
            event["correlation_id"] = self.redactor.redact(correlation_id)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_KEYS
            and key not in {"message", "asctime", "correlation_id"}
            and not key.startswith("_")
        }
        if extras:
            event.update(self.redactor.redact(extras))
        if record.exc_info:
            event["exception"] = self.redactor.redact_text(self.formatException(record.exc_info))
        return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


class RedactingTextFormatter(logging.Formatter):
    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        self.redactor = redactor
        self.converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return self.redactor.redact_text(super().format(record))


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = True,
    secret_values: Sequence[str] = (),
    stream: TextIO | None = None,
) -> None:
    """Configure the root logger idempotently for application startup."""

    normalized_level = level.upper()
    numeric_level = logging.getLevelName(normalized_level)
    if not isinstance(numeric_level, int):
        raise ValueError(f"invalid log level: {level}")
    redactor = SecretRedactor(secret_values)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(redactor) if json_logs else RedactingTextFormatter(redactor))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)


def configure_logging_from_settings(settings: Any, stream: TextIO | None = None) -> None:
    """Configure logging from a Settings-like object without importing it eagerly."""

    configure_logging(
        level=settings.log_level,
        json_logs=settings.log_json,
        secret_values=settings.secret_values(),
        stream=stream,
    )
