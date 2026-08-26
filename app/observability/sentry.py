"""Optional error tracking initialized only when a DSN is configured."""

from __future__ import annotations

from typing import Any, cast

from sentry_sdk.types import Event, Hint


def configure_sentry(dsn: str | None, environment: str, release: str) -> bool:
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        traces_sample_rate=0.05,
        before_send=_strip_sensitive_context,
    )
    return True


def _strip_sensitive_context(event: Event, hint: Hint) -> Event:
    del hint
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("headers", None)
        request.pop("cookies", None)
        request.pop("data", None)
    return cast(Event, _redact_sensitive_values(event))


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "jwt",
        "password",
        "private_token",
        "secret",
        "token",
    }
)


def _redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if str(key).lower() in _SENSITIVE_KEYS
                else _redact_sensitive_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value
