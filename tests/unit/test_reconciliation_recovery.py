from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.domain.enums import Direction, OrderStatus, OrderType
from app.domain.models import BrokerOrder, BrokerPosition
from app.execution.reconciliation import (
    InMemoryRecoveryLedger,
    LedgerOrder,
    LedgerPosition,
    LedgerStatus,
    ReconciliationService,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def position(ticket: str) -> BrokerPosition:
    return BrokerPosition(
        ticket=ticket,
        symbol="XAUUSDm",
        direction=Direction.LONG,
        volume=Decimal("0.1"),
        open_price=Decimal("2000"),
        current_price=Decimal("2001"),
        stop_loss=Decimal("1999"),
        take_profit=Decimal("2002"),
        profit=Decimal("10"),
        opened_at=NOW,
    )


def order(ticket: str) -> BrokerOrder:
    return BrokerOrder(
        ticket=ticket,
        symbol="XAUUSDm",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        volume=Decimal("0.1"),
        price=Decimal("1998"),
        stop_loss=Decimal("1997"),
        take_profit=Decimal("2000"),
        status=OrderStatus.PENDING,
        created_at=NOW,
        idempotency_key=f"key:{ticket}",
    )


@pytest.mark.asyncio
async def test_startup_recovers_broker_only_position_and_order() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.seed_position(position("P1"))
    broker.seed_order(order("O1"))
    ledger = InMemoryRecoveryLedger()
    report = await ReconciliationService(broker, ledger, clock=lambda: NOW).recover_on_startup()
    assert report.healthy
    assert report.recovered_positions == 1
    assert report.recovered_orders == 1
    assert (await ledger.positions())[0].recovered
    assert (await ledger.orders())[0].recovered


@pytest.mark.asyncio
async def test_healthy_broker_absence_closes_stale_ledger_records() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    ledger = InMemoryRecoveryLedger()
    await ledger.upsert_position(LedgerPosition(position("P1"), updated_at=NOW))
    await ledger.upsert_order(LedgerOrder(order("O1"), updated_at=NOW))
    report = await ReconciliationService(broker, ledger, clock=lambda: NOW).reconcile()
    assert report.closed_positions == 1
    assert report.cancelled_orders == 1
    assert (await ledger.positions())[0].status is LedgerStatus.CLOSED
    assert (await ledger.orders())[0].status is LedgerStatus.CANCELLED


@pytest.mark.asyncio
async def test_unhealthy_broker_never_closes_ledger_state() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.set_health(connected=False)
    ledger = InMemoryRecoveryLedger()
    await ledger.upsert_position(LedgerPosition(position("P1"), updated_at=NOW))
    report = await ReconciliationService(broker, ledger, clock=lambda: NOW).reconcile()
    assert not report.healthy
    assert (await ledger.positions())[0].status is LedgerStatus.OPEN
