"""Production worker process entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal

from redis.asyncio import Redis

from app.config.logging import configure_logging_from_settings
from app.config.settings import Settings, get_settings
from app.container import build_broker, build_notifier
from app.db.session import Database
from app.domain.errors import ConfigurationError
from app.workers.service import GoldFlowWorker

logger = logging.getLogger("goldflow.worker.runner")


async def run_worker(settings: Settings) -> None:
    readiness_errors = settings.production_readiness_errors
    if readiness_errors:
        raise ConfigurationError("; ".join(readiness_errors))
    broker = build_broker(settings)
    if broker is None:
        raise ConfigurationError("worker requires a fully configured broker adapter")
    database = Database(settings.database_url)
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    worker: GoldFlowWorker | None = None
    try:
        await redis.ping()
        worker = GoldFlowWorker(
            settings=settings,
            database=database,
            broker=broker,
            redis=redis,
            notifier=build_notifier(settings),
        )
        await worker.startup()
        worker.start_scheduler()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except NotImplementedError:  # Windows event loops may not support handlers
                continue
        logger.info(
            "worker_started",
            extra={
                "service": "worker",
                "event": "WORKER_STARTED",
                "environment": settings.trading_env.value,
                "mode": settings.trading_mode.value,
            },
        )
        await stop.wait()
    finally:
        if worker is not None:
            await worker.shutdown()
        await redis.aclose()
        database.dispose()


def run() -> None:
    settings = get_settings()
    configure_logging_from_settings(settings)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    run()
