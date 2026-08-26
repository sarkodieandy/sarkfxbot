from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BrokerAccount, Order, Signal
from app.db.session import Database
from app.domain.enums import Direction, OrderType, SignalAction
from app.domain.models import ExecutionReport, OrderRequest
from app.execution.idempotency import IdempotencyStatus
from app.execution.sqlalchemy_idempotency import SqlAlchemyIdempotencyStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SIGNAL_ID = UUID("00000000-0000-0000-0000-000000000001")


def seed_dependencies(database: Database) -> str:
    with database.session_scope() as session:
        account = BrokerAccount(
            broker="Mock",
            platform="MOCK",
            external_account_id="demo-1",
            account_type="DEMO",
            server="mock-demo",
            currency="USD",
        )
        session.add(account)
        session.add(
            Signal(
                signal_id=str(SIGNAL_ID),
                strategy_id="gold",
                strategy_version="1.0.0",
                symbol="XAUUSDm",
                canonical_symbol="XAUUSD",
                action=SignalAction.LONG.value,
                direction=Direction.LONG.value,
                entry_min=Decimal("2000"),
                entry_max=Decimal("2001"),
                stop_loss=Decimal("1999"),
                take_profits=["2002"],
                risk_reward=Decimal("2"),
                confidence_score=80,
                status="ACTIVE",
                rationale={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        return account.id


def request() -> OrderRequest:
    return OrderRequest(
        signal_id=SIGNAL_ID,
        strategy_id="gold",
        symbol="XAUUSDm",
        direction=Direction.LONG,
        order_type=OrderType.MARKET,
        volume=Decimal("0.1"),
        stop_loss=Decimal("1999"),
        take_profits=(Decimal("2002"),),
        idempotency_key="demo-1:signal-1",
        requested_price=Decimal("2000"),
    )


@pytest.mark.asyncio
async def test_sqlalchemy_store_claim_is_unique_and_survives_new_adapter() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    account_id = seed_dependencies(database)

    def resolve_order(session: Session, order_request: OrderRequest) -> str:
        existing = session.scalar(
            select(Order).where(Order.idempotency_key == order_request.idempotency_key)
        )
        if existing is not None:
            return existing.id
        order = Order(
            signal_id=str(order_request.signal_id),
            broker_account_id=account_id,
            idempotency_key=order_request.idempotency_key,
            symbol=order_request.symbol,
            direction=order_request.direction.value,
            order_type=order_request.order_type.value,
            status="PENDING",
            volume=order_request.volume,
            requested_price=order_request.requested_price,
            stop_loss=order_request.stop_loss,
            take_profits=[str(value) for value in order_request.take_profits],
            maximum_slippage=order_request.maximum_slippage,
        )
        session.add(order)
        session.flush()
        return order.id

    store = SqlAlchemyIdempotencyStore(database.session_factory, resolve_order)
    claimed, created = await store.claim(request(), NOW)
    duplicate, duplicate_created = await store.claim(request(), NOW)
    assert created
    assert not duplicate_created
    assert claimed.idempotency_key == duplicate.idempotency_key

    await store.update(
        request().idempotency_key,
        IdempotencyStatus.SUBMITTED,
        NOW,
        reason="send started",
    )
    report = ExecutionReport(
        True,
        request().idempotency_key,
        "P1",
        Decimal("2000"),
        Decimal("2000.1"),
        Decimal("0.1"),
        broker_code="DONE",
        submitted_at=NOW,
    )
    await store.update(
        request().idempotency_key,
        IdempotencyStatus.SUCCEEDED,
        NOW,
        report=report,
    )
    restarted_store = SqlAlchemyIdempotencyStore(database.session_factory, resolve_order)
    recovered = await restarted_store.get(request().idempotency_key)
    assert recovered is not None
    assert recovered.status is IdempotencyStatus.SUCCEEDED
    assert recovered.report is not None
    assert recovered.report.broker_ticket == "P1"
    database.dispose()
