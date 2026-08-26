from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.brokers.mock import MockBrokerAdapter
from app.db.models import Notification, Order, OutboxEvent, Position, Trade
from app.db.session import Database
from app.domain.enums import Direction, OrderStatus, OrderType
from app.domain.models import BrokerOrder, BrokerPosition
from app.execution.reconciliation import LedgerOrder, LedgerPosition, LedgerStatus
from app.risk.circuit_breaker import CircuitState
from app.workers.persistence import (
    SQLAlchemyCircuitStateStore,
    SQLAlchemyRecoveryLedger,
    WorkerPersistence,
)

NOW = datetime(2026, 1, 5, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sqlalchemy_circuit_state_optimistic_lock_and_round_trip() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    store = SQLAlchemyCircuitStateStore(database.session_factory)
    assert await store.load("demo-account") is None
    initial = CircuitState(
        account_id="demo-account",
        day=date(2026, 1, 5),
        week_start=date(2026, 1, 5),
        daily_locked=True,
        reason="DAILY_LOSS_LIMIT_REACHED",
        updated_at=NOW,
    )
    saved = await store.save(initial, expected_version=0)
    assert saved.version == 1
    loaded = await store.load("demo-account")
    assert loaded is not None
    assert loaded.daily_locked and loaded.reason == "DAILY_LOSS_LIMIT_REACHED"
    with pytest.raises(RuntimeError, match="optimistic-lock"):
        await store.save(replace(saved, reason="stale writer"), expected_version=0)
    database.dispose()


@pytest.mark.asyncio
async def test_sqlalchemy_recovery_ledger_creates_updates_and_closes_broker_truth() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    persistence = WorkerPersistence(database.session_factory)
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    account_id = await persistence.ensure_broker_account(await broker.get_account())
    ledger = SQLAlchemyRecoveryLedger(database.session_factory, account_id)
    assert await ledger.positions() == ()
    assert await ledger.orders() == ()

    signal_id = uuid4()
    broker_position = BrokerPosition(
        ticket="recovered-position-1",
        symbol="XAUUSDm",
        direction=Direction.LONG,
        volume=Decimal("0.10"),
        open_price=Decimal("2000"),
        current_price=Decimal("2002"),
        stop_loss=Decimal("1995"),
        take_profit=Decimal("2010"),
        profit=Decimal("20"),
        opened_at=NOW,
        strategy_id="gold_h1_m15_m5",
        signal_id=signal_id,
    )
    await ledger.upsert_position(LedgerPosition(broker_position, LedgerStatus.OPEN, True, NOW))
    positions = await ledger.positions()
    assert len(positions) == 1 and positions[0].recovered is False
    assert positions[0].position.signal_id == signal_id

    changed = replace(
        broker_position,
        volume=Decimal("0.05"),
        current_price=Decimal("2005"),
        stop_loss=Decimal("2000.50"),
        profit=Decimal("25"),
    )
    await ledger.upsert_position(LedgerPosition(changed, LedgerStatus.OPEN, False, NOW))
    assert (await ledger.positions())[0].position.volume == Decimal("0.05")
    await ledger.upsert_position(
        LedgerPosition(changed, LedgerStatus.CLOSED, False, NOW + timedelta(minutes=1))
    )

    broker_order = BrokerOrder(
        ticket="recovered-order-1",
        symbol="XAUUSDm",
        direction=Direction.SHORT,
        order_type=OrderType.LIMIT,
        volume=Decimal("0.10"),
        price=Decimal("2010"),
        stop_loss=Decimal("2015"),
        take_profit=Decimal("2000"),
        status=OrderStatus.PENDING,
        created_at=NOW,
        idempotency_key="recovered-key",
    )
    await ledger.upsert_order(LedgerOrder(broker_order, LedgerStatus.PENDING, True, NOW))
    orders = await ledger.orders()
    assert len(orders) == 1 and orders[0].status is LedgerStatus.PENDING
    await ledger.upsert_order(
        LedgerOrder(
            replace(broker_order, status=OrderStatus.CANCELLED),
            LedgerStatus.CANCELLED,
            False,
            NOW + timedelta(minutes=1),
        )
    )

    with database.session_scope() as session:
        position = session.scalar(select(Position))
        trade = session.scalar(select(Trade))
        order = session.scalar(select(Order))
        assert position is not None and position.closed_at is not None
        assert position.current_volume == 0
        assert trade is not None and trade.exit_reason == "BROKER_RECONCILIATION"
        assert order is not None and order.status == OrderStatus.CANCELLED.value
    database.dispose()


@pytest.mark.asyncio
async def test_outbox_failure_backoff_retry_and_delivery_journal() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    persistence = WorkerPersistence(database.session_factory)
    await persistence.record_job_failure("scanner", "BrokerUnavailable", NOW)

    with pytest.raises(ValueError, match="limit"):
        await persistence.claim_outbox(NOW, limit=0)
    with pytest.raises(ValueError, match="stale"):
        await persistence.claim_outbox(NOW, stale_after=timedelta(0))

    claimed = await persistence.claim_outbox(NOW)
    assert len(claimed) == 1
    await persistence.complete_outbox(
        claimed[0],
        delivered=False,
        channel="telegram",
        recipient="test-recipient",
        error="temporary failure",
        now=NOW,
    )
    assert await persistence.claim_outbox(NOW) == ()
    retry = await persistence.claim_outbox(NOW + timedelta(minutes=10))
    assert len(retry) == 1 and retry[0].attempt_count == 1
    await persistence.complete_outbox(
        retry[0],
        delivered=True,
        channel="telegram",
        recipient="test-recipient",
        error=None,
        now=NOW + timedelta(minutes=10),
    )
    with database.session_scope() as session:
        row = session.scalar(select(OutboxEvent))
        assert row is not None and row.status == "PUBLISHED" and row.attempt_count == 2
        assert session.scalar(select(func.count(Notification.id))) == 2
    database.dispose()
