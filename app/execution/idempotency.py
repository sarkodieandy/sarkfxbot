"""Durable-shaped idempotency port and deterministic in-memory implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.domain.models import ExecutionReport, OrderRequest


class IdempotencyStatus(StrEnum):
    CLAIMED = "CLAIMED"
    SUBMITTED = "SUBMITTED"
    SUCCEEDED = "SUCCEEDED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    idempotency_key: str
    request: OrderRequest
    status: IdempotencyStatus
    report: ExecutionReport | None = None
    reason: str = ""
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))


class IdempotencyStore(Protocol):
    async def claim(self, request: OrderRequest, now: datetime) -> tuple[ExecutionRecord, bool]:
        raise RuntimeError("idempotency claim implementation is required")

    async def get(self, idempotency_key: str) -> ExecutionRecord | None:
        raise RuntimeError("idempotency get implementation is required")

    async def update(
        self,
        idempotency_key: str,
        status: IdempotencyStatus,
        now: datetime,
        *,
        report: ExecutionReport | None = None,
        reason: str = "",
    ) -> ExecutionRecord:
        raise RuntimeError("idempotency update implementation is required")


class InMemoryIdempotencyStore:
    """Mirrors the unique-key and version semantics expected from PostgreSQL."""

    def __init__(self) -> None:
        self._records: dict[str, ExecutionRecord] = {}
        self._lock = asyncio.Lock()

    async def claim(self, request: OrderRequest, now: datetime) -> tuple[ExecutionRecord, bool]:
        async with self._lock:
            existing = self._records.get(request.idempotency_key)
            if existing is not None:
                return existing, False
            timestamp = now.astimezone(UTC)
            record = ExecutionRecord(
                idempotency_key=request.idempotency_key,
                request=request,
                status=IdempotencyStatus.CLAIMED,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._records[request.idempotency_key] = record
            return record, True

    async def get(self, idempotency_key: str) -> ExecutionRecord | None:
        async with self._lock:
            return self._records.get(idempotency_key)

    async def update(
        self,
        idempotency_key: str,
        status: IdempotencyStatus,
        now: datetime,
        *,
        report: ExecutionReport | None = None,
        reason: str = "",
    ) -> ExecutionRecord:
        async with self._lock:
            current = self._records.get(idempotency_key)
            if current is None:
                raise KeyError(f"unknown idempotency key {idempotency_key}")
            allowed: dict[IdempotencyStatus, frozenset[IdempotencyStatus]] = {
                IdempotencyStatus.CLAIMED: frozenset(
                    {IdempotencyStatus.SUBMITTED, IdempotencyStatus.REJECTED}
                ),
                IdempotencyStatus.SUBMITTED: frozenset(
                    {
                        IdempotencyStatus.SUCCEEDED,
                        IdempotencyStatus.PROTECTION_FAILED,
                        IdempotencyStatus.REJECTED,
                        IdempotencyStatus.UNKNOWN,
                    }
                ),
                IdempotencyStatus.UNKNOWN: frozenset(
                    {
                        IdempotencyStatus.SUCCEEDED,
                        IdempotencyStatus.PROTECTION_FAILED,
                        IdempotencyStatus.REJECTED,
                    }
                ),
                IdempotencyStatus.SUCCEEDED: frozenset(),
                IdempotencyStatus.PROTECTION_FAILED: frozenset(),
                IdempotencyStatus.REJECTED: frozenset(),
            }
            if status not in allowed[current.status]:
                raise RuntimeError(f"invalid idempotency transition {current.status} -> {status}")
            updated = replace(
                current,
                status=status,
                report=report if report is not None else current.report,
                reason=reason,
                version=current.version + 1,
                updated_at=now.astimezone(UTC),
            )
            self._records[idempotency_key] = updated
            return updated
