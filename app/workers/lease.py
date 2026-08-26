"""Redis-backed distributed leases with fail-closed acquisition and safe release."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeVar

logger = logging.getLogger("goldflow.worker.lease")
ResultT = TypeVar("ResultT")

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
""".strip()


class AsyncRedis(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> Any:
        raise RuntimeError("Redis set implementation is required")

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        raise RuntimeError("Redis eval implementation is required")


class LeaseRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    LOCKED = "LOCKED"
    LOCK_UNAVAILABLE = "LOCK_UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class LeaseRunResult[ResultT]:
    status: LeaseRunStatus
    value: ResultT | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class RedisLease:
    client: AsyncRedis
    key: str
    token: str

    async def release(self) -> bool:
        """Delete only the lock still owned by this exact random token."""

        try:
            result = await self.client.eval(_RELEASE_SCRIPT, 1, self.key, self.token)
        except Exception as exc:  # Redis/network boundary
            logger.error(
                "redis_lease_release_failed",
                extra={"event": "REDIS_LEASE_RELEASE_FAILED", "error_type": type(exc).__name__},
            )
            return False
        return bool(result)


class RedisLeaseManager:
    """Coordinate singleton jobs; absent or unhealthy Redis never grants a lease."""

    def __init__(self, client: AsyncRedis | None, *, namespace: str = "goldflow") -> None:
        self._client = client
        self._namespace = namespace.strip(":") or "goldflow"

    async def acquire(self, name: str, *, ttl_seconds: int) -> RedisLease | None:
        if ttl_seconds < 1:
            raise ValueError("lease TTL must be positive")
        if self._client is None:
            return None
        key = f"{self._namespace}:lease:{name}"
        token = secrets.token_urlsafe(32)
        try:
            acquired = await self._client.set(
                key,
                token,
                nx=True,
                px=ttl_seconds * 1_000,
            )
        except Exception as exc:  # Redis/network boundary
            logger.error(
                "redis_lease_acquire_failed",
                extra={"event": "REDIS_LEASE_ACQUIRE_FAILED", "error_type": type(exc).__name__},
            )
            return None
        return RedisLease(self._client, key, token) if acquired else None

    async def run(
        self,
        name: str,
        operation: Callable[[], Awaitable[ResultT]],
        *,
        ttl_seconds: int,
        contain_exceptions: bool = True,
    ) -> LeaseRunResult[ResultT]:
        lease = await self.acquire(name, ttl_seconds=ttl_seconds)
        if lease is None:
            status = (
                LeaseRunStatus.LOCK_UNAVAILABLE if self._client is None else LeaseRunStatus.LOCKED
            )
            return LeaseRunResult(status)
        try:
            value = await operation()
            return LeaseRunResult(LeaseRunStatus.COMPLETED, value=value)
        except Exception as exc:
            if not contain_exceptions:
                raise
            logger.exception(
                "worker_job_failed",
                extra={
                    "event": "WORKER_JOB_FAILED",
                    "job": name,
                    "error_type": type(exc).__name__,
                },
            )
            return LeaseRunResult(LeaseRunStatus.FAILED, error_type=type(exc).__name__)
        finally:
            await lease.release()
