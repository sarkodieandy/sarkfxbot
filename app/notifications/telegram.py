"""Telegram Bot API notification adapter with bounded retry behavior."""

from __future__ import annotations

import asyncio
import html
from collections.abc import Callable

import httpx

from app.notifications.base import Notification, NotificationDeliveryError, Notifier


class TelegramNotifier(Notifier):
    """Send escaped HTML messages without ever logging the bot token."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout_seconds: float = 5.0,
        maximum_attempts: int = 3,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError("Telegram bot token and chat id are required")
        if timeout_seconds <= 0:
            raise ValueError("Telegram timeout must be positive")
        if maximum_attempts < 1 or maximum_attempts > 5:
            raise ValueError("maximum_attempts must be between 1 and 5")
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout_seconds
        self._attempts = maximum_attempts
        self._client_factory = client_factory

    @staticmethod
    def render(notification: Notification) -> str:
        title = html.escape(notification.title)
        message = html.escape(notification.message)
        return f"<b>{title}</b>\n\n{message}"

    async def send(self, notification: Notification) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": self.render(notification),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        last_error = "unknown error"
        for attempt in range(1, self._attempts + 1):
            try:
                async with self._client_factory(timeout=self._timeout) as client:
                    response = await client.post(self._url, json=payload)
                    response.raise_for_status()
                    body = response.json()
                    if isinstance(body, dict) and body.get("ok") is True:
                        return True
                    last_error = "Telegram returned ok=false"
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                last_error = type(exc).__name__
            if attempt < self._attempts:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        raise NotificationDeliveryError(
            f"Telegram delivery failed after {self._attempts} attempts: {last_error}"
        )
