"""Reliable notification contracts that never control protective execution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class NotificationLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Notification:
    event: str
    title: str
    message: str
    level: NotificationLevel = NotificationLevel.INFO
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class NotificationDeliveryError(RuntimeError):
    """A notification could not be delivered after bounded retries."""


class Notifier(ABC):
    """Notification adapter interface."""

    @abstractmethod
    async def send(self, notification: Notification) -> bool:
        """Deliver a notification and report success without exposing credentials."""
        raise NotificationDeliveryError("abstract notifier has no delivery adapter")


class NullNotifier(Notifier):
    """Explicitly disabled notifier used by default and in tests."""

    async def send(self, notification: Notification) -> bool:
        del notification
        return True


class CompositeNotifier(Notifier):
    """Best-effort fan-out; failures are logged and never stop position protection."""

    def __init__(self, notifiers: tuple[Notifier, ...]) -> None:
        self._notifiers = notifiers
        self._logger = logging.getLogger("goldflow.notifications")

    async def send(self, notification: Notification) -> bool:
        delivered = True
        for notifier in self._notifiers:
            try:
                delivered = await notifier.send(notification) and delivered
            except Exception as exc:
                delivered = False
                self._logger.error(
                    "notification_delivery_failed",
                    extra={"event": notification.event, "error_type": type(exc).__name__},
                )
        return delivered
