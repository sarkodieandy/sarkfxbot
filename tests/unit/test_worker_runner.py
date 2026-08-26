from __future__ import annotations

from typing import Any

import pytest

from app.config.settings import Settings
from app.domain.errors import ConfigurationError
from app.workers import runner


@pytest.mark.asyncio
async def test_worker_runner_rejects_unready_configuration_and_missing_broker() -> None:
    unready = Settings(
        _env_file=None,
        app_env="test",
        trading_env="demo",
        live_trading_enabled=True,
    )
    with pytest.raises(ConfigurationError, match="LIVE_TRADING_ENABLED"):
        await runner.run_worker(unready)

    missing_broker = Settings(
        _env_file=None,
        app_env="test",
        trading_env="demo",
        broker_type="unsupported",
    )
    with pytest.raises(ConfigurationError, match="configured broker adapter"):
        await runner.run_worker(missing_broker)


@pytest.mark.asyncio
async def test_worker_runner_starts_waits_and_closes_every_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    broker = object()
    notifier = object()

    class FakeDatabase:
        def __init__(self, url: str) -> None:
            assert url == "sqlite+pysqlite:///:memory:"
            events.append("database-created")

        def dispose(self) -> None:
            events.append("database-disposed")

    class FakeRedisClient:
        async def ping(self) -> bool:
            events.append("redis-ping")
            return True

        async def aclose(self) -> None:
            events.append("redis-closed")

    redis_client = FakeRedisClient()

    class FakeRedisFactory:
        @staticmethod
        def from_url(url: str, **kwargs: Any) -> FakeRedisClient:
            assert url == "redis://test"
            assert kwargs["decode_responses"] is True
            return redis_client

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["broker"] is broker
            assert kwargs["redis"] is redis_client
            assert kwargs["notifier"] is notifier
            events.append("worker-created")

        async def startup(self) -> None:
            events.append("worker-started")

        def start_scheduler(self) -> None:
            events.append("scheduler-started")

        async def shutdown(self) -> None:
            events.append("worker-stopped")

    class ImmediateEvent:
        def set(self) -> None:
            events.append("stop-set")

        async def wait(self) -> None:
            events.append("stop-waited")

    class FakeLoop:
        def __init__(self) -> None:
            self.calls = 0

        def add_signal_handler(self, signum: int, callback: Any) -> None:
            del signum, callback
            self.calls += 1
            if self.calls == 2:
                raise NotImplementedError

    monkeypatch.setattr(runner, "Database", FakeDatabase)
    monkeypatch.setattr(runner, "Redis", FakeRedisFactory)
    monkeypatch.setattr(runner, "GoldFlowWorker", FakeWorker)
    monkeypatch.setattr(runner, "build_broker", lambda settings: broker)
    monkeypatch.setattr(runner, "build_notifier", lambda settings: notifier)
    monkeypatch.setattr(runner.asyncio, "Event", ImmediateEvent)
    monkeypatch.setattr(runner.asyncio, "get_running_loop", lambda: FakeLoop())

    settings = Settings(
        _env_file=None,
        app_env="test",
        trading_env="demo",
        broker_type="mock",
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://test",
    )
    await runner.run_worker(settings)

    assert events == [
        "database-created",
        "redis-ping",
        "worker-created",
        "worker-started",
        "scheduler-started",
        "stop-waited",
        "worker-stopped",
        "redis-closed",
        "database-disposed",
    ]


def test_worker_console_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")
    events: list[object] = []

    def consume(coroutine: Any) -> None:
        events.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(
        runner,
        "configure_logging_from_settings",
        lambda configured: events.append(configured),
    )
    monkeypatch.setattr(runner.asyncio, "run", consume)

    runner.run()

    assert events[0] is settings
    assert len(events) == 2
