"""Cross-process Redis/PostgreSQL contracts shared by the API and workers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import TradingMode

RUNTIME_CONFIG_TYPE = "runtime_controls"
EVENT_CHANNEL = "goldflow:events"
EXECUTION_QUEUE = "goldflow:execution:signals"
HEARTBEAT_KEY_PREFIX = "goldflow:heartbeat"
WORKER_COMPONENTS = (
    "strategy_worker",
    "execution_worker",
    "notification_worker",
)


def heartbeat_key(component: str) -> str:
    if component not in WORKER_COMPONENTS:
        raise ValueError(f"unknown worker component {component}")
    return f"{HEARTBEAT_KEY_PREFIX}:{component}"


@dataclass(frozen=True, slots=True)
class DurableRuntimeState:
    """Operator state persisted in PostgreSQL and consumed by every process."""

    mode: TradingMode
    kill_switch: bool
    kill_switch_reason: str | None
    auto_disabled: bool
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None:
            raise ValueError("runtime state timestamp must be timezone-aware")
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(UTC))
        if self.kill_switch and not (self.kill_switch_reason or "").strip():
            raise ValueError("an active kill switch requires a reason")
        if self.auto_disabled and self.mode is TradingMode.AUTO:
            raise ValueError("AUTO cannot be active while drawdown protection is latched")

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "kill_switch": self.kill_switch,
            "kill_switch_reason": self.kill_switch_reason,
            "auto_disabled": self.auto_disabled,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        default_mode: TradingMode,
        default_updated_at: datetime | None = None,
    ) -> DurableRuntimeState:
        raw_updated_at = payload.get("updated_at")
        if raw_updated_at is None:
            updated_at = default_updated_at or datetime.now(UTC)
        elif isinstance(raw_updated_at, datetime):
            updated_at = raw_updated_at
        else:
            updated_at = datetime.fromisoformat(str(raw_updated_at).replace("Z", "+00:00"))
        kill_switch = payload.get("kill_switch", False)
        auto_disabled = payload.get("auto_disabled", False)
        if not isinstance(kill_switch, bool) or not isinstance(auto_disabled, bool):
            raise ValueError("runtime safety flags must be booleans")
        raw_reason = payload.get("kill_switch_reason")
        reason = None if raw_reason is None else str(raw_reason).strip() or None
        return cls(
            mode=TradingMode(str(payload.get("mode", default_mode.value)).upper()),
            kill_switch=kill_switch,
            kill_switch_reason=reason,
            auto_disabled=auto_disabled,
            updated_at=updated_at,
        )


def event_message(event: str, payload: Mapping[str, Any]) -> str:
    """Encode a bounded, JSON-only Redis pub/sub envelope."""

    normalized = event.strip().upper()
    if not normalized or len(normalized) > 128:
        raise ValueError("event names must contain 1..128 characters")
    return json.dumps(
        {
            "event": normalized,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": dict(payload),
        },
        default=str,
        separators=(",", ":"),
    )


def parse_event_message(value: str | bytes) -> tuple[str, dict[str, Any]]:
    raw = value.decode("utf-8") if isinstance(value, bytes) else value
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("event envelope must be a JSON object")
    event = decoded.get("event")
    payload = decoded.get("payload")
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event envelope requires an event name")
    if not isinstance(payload, dict):
        raise ValueError("event envelope payload must be an object")
    return event.strip().upper(), payload
