from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.notifications.base import (
    CompositeNotifier,
    Notification,
    NotificationDeliveryError,
    Notifier,
    NullNotifier,
)
from app.notifications.telegram import TelegramNotifier


class _FakeClient:
    def __init__(
        self,
        response: httpx.Response,
        capture: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self._response = response
        self._capture = capture

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        self._capture.append((url, json))
        return self._response


class _ClientFactory:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, **kwargs: object) -> _FakeClient:
        assert kwargs["timeout"] == 1.0
        return _FakeClient(self.responses.pop(0), self.calls)


def _response(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("POST", "https://api.telegram.org/test"),
    )


@pytest.mark.asyncio
async def test_telegram_escapes_html_and_sends_expected_payload() -> None:
    factory = _ClientFactory([_response(200, {"ok": True})])
    notifier = TelegramNotifier(
        "bot-secret",
        "chat-1",
        timeout_seconds=1.0,
        maximum_attempts=1,
        client_factory=factory,
    )
    notification = Notification("SIGNAL_FOUND", "Gold <LONG>", "Price & risk")

    assert await notifier.send(notification) is True
    url, payload = factory.calls[0]
    assert url.endswith("/botbot-secret/sendMessage")
    assert payload["text"] == "<b>Gold &lt;LONG&gt;</b>\n\nPrice &amp; risk"
    assert payload["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_telegram_failure_is_bounded_and_never_exposes_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [_response(503, {"ok": False}), _response(503, {"ok": False})]
    factory = _ClientFactory(responses)

    async def no_sleep(delay: float) -> None:
        assert delay == 0.25

    monkeypatch.setattr("app.notifications.telegram.asyncio.sleep", no_sleep)
    notifier = TelegramNotifier(
        "never-print-this-token",
        "chat-1",
        timeout_seconds=1.0,
        maximum_attempts=2,
        client_factory=factory,
    )
    with pytest.raises(NotificationDeliveryError) as error:
        await notifier.send(Notification("ORDER_ERROR", "Order", "Rejected"))
    assert len(factory.calls) == 2
    assert "never-print-this-token" not in str(error.value)


class _FailingNotifier(Notifier):
    async def send(self, notification: Notification) -> bool:
        del notification
        raise NotificationDeliveryError("safe failure")


@pytest.mark.asyncio
async def test_composite_notification_failure_does_not_block_other_channels() -> None:
    notifier = CompositeNotifier((_FailingNotifier(), NullNotifier()))
    assert await notifier.send(Notification("BOT_STOPPED", "Bot", "Stopped")) is False


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TelegramNotifier("", "chat"),
        lambda: TelegramNotifier("token", ""),
        lambda: TelegramNotifier("token", "chat", timeout_seconds=0),
        lambda: TelegramNotifier("token", "chat", maximum_attempts=6),
    ],
)
def test_invalid_telegram_configuration_is_rejected(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        factory()
