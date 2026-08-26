"""Broker-authoritative restart recovery and drift reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from app.brokers.base import BrokerAdapter
from app.domain.models import BrokerOrder, BrokerPosition


class LedgerStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class LedgerPosition:
    position: BrokerPosition
    status: LedgerStatus = LedgerStatus.OPEN
    recovered: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class LedgerOrder:
    order: BrokerOrder
    status: LedgerStatus = LedgerStatus.PENDING
    recovered: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))


class RecoveryLedger(Protocol):
    async def positions(self) -> tuple[LedgerPosition, ...]:
        raise RuntimeError("recovery ledger positions implementation is required")

    async def orders(self) -> tuple[LedgerOrder, ...]:
        raise RuntimeError("recovery ledger orders implementation is required")

    async def upsert_position(self, record: LedgerPosition) -> None:
        raise RuntimeError("recovery ledger upsert_position implementation is required")

    async def upsert_order(self, record: LedgerOrder) -> None:
        raise RuntimeError("recovery ledger upsert_order implementation is required")


class InMemoryRecoveryLedger:
    def __init__(self) -> None:
        self._positions: dict[str, LedgerPosition] = {}
        self._orders: dict[str, LedgerOrder] = {}
        self._lock = asyncio.Lock()

    async def positions(self) -> tuple[LedgerPosition, ...]:
        async with self._lock:
            return tuple(self._positions.values())

    async def orders(self) -> tuple[LedgerOrder, ...]:
        async with self._lock:
            return tuple(self._orders.values())

    async def upsert_position(self, record: LedgerPosition) -> None:
        async with self._lock:
            self._positions[record.position.ticket] = record

    async def upsert_order(self, record: LedgerOrder) -> None:
        async with self._lock:
            self._orders[record.order.ticket] = record


@dataclass(frozen=True, slots=True)
class ReconciliationIncident:
    code: str
    ticket: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    healthy: bool
    incidents: tuple[ReconciliationIncident, ...]
    recovered_positions: int = 0
    recovered_orders: int = 0
    closed_positions: int = 0
    cancelled_orders: int = 0


class ReconciliationService:
    def __init__(
        self,
        broker: BrokerAdapter,
        ledger: RecoveryLedger,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._ledger = ledger
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()

    async def recover_on_startup(self) -> ReconciliationReport:
        return await self.reconcile()

    async def reconcile(self) -> ReconciliationReport:
        async with self._lock:
            health = await self._broker.health_check()
            if not health.healthy or not health.connected:
                return ReconciliationReport(
                    False,
                    (
                        ReconciliationIncident(
                            "BROKER_UNAVAILABLE", "", "reconciliation deferred; ledger unchanged"
                        ),
                    ),
                )
            broker_positions = {item.ticket: item for item in await self._broker.get_positions()}
            broker_orders = {item.ticket: item for item in await self._broker.get_orders()}
            local_positions = {
                item.position.ticket: item for item in await self._ledger.positions()
            }
            local_orders = {item.order.ticket: item for item in await self._ledger.orders()}
            incidents: list[ReconciliationIncident] = []
            recovered_positions = 0
            recovered_orders = 0
            closed_positions = 0
            cancelled_orders = 0
            now = self._clock().astimezone(UTC)

            for ticket, broker_position in broker_positions.items():
                local_position = local_positions.get(ticket)
                if local_position is None or local_position.status is LedgerStatus.CLOSED:
                    await self._ledger.upsert_position(
                        LedgerPosition(broker_position, LedgerStatus.OPEN, True, now)
                    )
                    recovered_positions += 1
                    incidents.append(
                        ReconciliationIncident(
                            "BROKER_POSITION_RECOVERED",
                            ticket,
                            "broker position restored to ledger",
                        )
                    )
                elif local_position.position != broker_position:
                    await self._ledger.upsert_position(
                        replace(local_position, position=broker_position, updated_at=now)
                    )
                    incidents.append(
                        ReconciliationIncident(
                            "POSITION_DRIFT_REPAIRED",
                            ticket,
                            "broker values replaced ledger values",
                        )
                    )
            for ticket, local_position in local_positions.items():
                if local_position.status is LedgerStatus.OPEN and ticket not in broker_positions:
                    await self._ledger.upsert_position(
                        replace(local_position, status=LedgerStatus.CLOSED, updated_at=now)
                    )
                    closed_positions += 1
                    incidents.append(
                        ReconciliationIncident(
                            "LEDGER_POSITION_MARKED_CLOSED",
                            ticket,
                            "position absent at healthy broker",
                        )
                    )

            for ticket, broker_order in broker_orders.items():
                local_order = local_orders.get(ticket)
                if local_order is None or local_order.status is LedgerStatus.CANCELLED:
                    await self._ledger.upsert_order(
                        LedgerOrder(broker_order, LedgerStatus.PENDING, True, now)
                    )
                    recovered_orders += 1
                    incidents.append(
                        ReconciliationIncident(
                            "BROKER_ORDER_RECOVERED", ticket, "broker order restored to ledger"
                        )
                    )
                elif local_order.order != broker_order:
                    await self._ledger.upsert_order(
                        replace(local_order, order=broker_order, updated_at=now)
                    )
                    incidents.append(
                        ReconciliationIncident(
                            "ORDER_DRIFT_REPAIRED", ticket, "broker values replaced ledger values"
                        )
                    )
            for ticket, local_order in local_orders.items():
                if local_order.status is LedgerStatus.PENDING and ticket not in broker_orders:
                    await self._ledger.upsert_order(
                        replace(local_order, status=LedgerStatus.CANCELLED, updated_at=now)
                    )
                    cancelled_orders += 1
                    incidents.append(
                        ReconciliationIncident(
                            "LEDGER_ORDER_MARKED_CANCELLED",
                            ticket,
                            "order absent at healthy broker",
                        )
                    )

            return ReconciliationReport(
                True,
                tuple(incidents),
                recovered_positions,
                recovered_orders,
                closed_positions,
                cancelled_orders,
            )
