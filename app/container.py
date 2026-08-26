"""Explicit application dependency container; no hidden mutable broker state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from redis.asyncio import Redis

from app.api.runtime import RuntimeControls
from app.api.websocket import WebSocketHub
from app.brokers.base import BrokerAdapter
from app.brokers.mock import MockBrokerAdapter
from app.brokers.mt5 import MT5BrokerAdapter
from app.brokers.retry import RetryingBrokerAdapter
from app.brokers.serialized import SerializedBrokerAdapter
from app.config.settings import Settings
from app.db.session import Database
from app.notifications.base import Notifier, NullNotifier
from app.notifications.telegram import TelegramNotifier
from app.observability.health import HealthRegistry
from app.observability.metrics import GoldFlowMetrics


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    database: Database
    broker: BrokerAdapter | None
    controls: RuntimeControls
    notifier: Notifier
    health: HealthRegistry
    metrics: GoldFlowMetrics
    websocket_hub: WebSocketHub
    redis: Redis | None = None
    event_listener_task: asyncio.Task[None] | None = None


def build_broker(settings: Settings) -> BrokerAdapter | None:
    if settings.broker_type == "mock":
        return RetryingBrokerAdapter(SerializedBrokerAdapter(MockBrokerAdapter.gold_demo()))
    if settings.broker_type != "mt5":
        return None
    if not settings.mt5_login or not settings.mt5_server or settings.mt5_password is None:
        return None
    try:
        login = int(settings.mt5_login)
    except ValueError:
        return None
    adapter = MT5BrokerAdapter(
        login=login,
        server=settings.mt5_server,
        password=settings.mt5_password.get_secret_value(),
        terminal_path=settings.mt5_terminal_path,
    )
    return RetryingBrokerAdapter(SerializedBrokerAdapter(adapter))


def build_notifier(settings: Settings) -> Notifier:
    if (
        settings.telegram_enabled
        and settings.telegram_bot_token is not None
        and settings.telegram_chat_id
    ):
        return TelegramNotifier(
            settings.telegram_bot_token.get_secret_value(),
            settings.telegram_chat_id,
        )
    return NullNotifier()


def build_container(
    settings: Settings,
    *,
    database: Database | None = None,
    broker: BrokerAdapter | None = None,
) -> AppContainer:
    return AppContainer(
        settings=settings,
        database=database or Database(settings.database_url),
        broker=broker if broker is not None else build_broker(settings),
        controls=RuntimeControls(settings),
        notifier=build_notifier(settings),
        health=HealthRegistry(),
        metrics=GoldFlowMetrics(),
        websocket_hub=WebSocketHub(),
    )
