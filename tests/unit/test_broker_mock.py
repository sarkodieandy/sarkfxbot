from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.domain.enums import Direction, OrderType
from app.domain.models import OrderRequest


@pytest.mark.asyncio
async def test_mock_broker_places_protected_market_order() -> None:
    broker = MockBrokerAdapter.gold_demo()
    request = OrderRequest(
        signal_id=uuid4(),
        strategy_id="test",
        symbol="XAUUSDm",
        direction=Direction.LONG,
        order_type=OrderType.MARKET,
        volume=Decimal("0.10"),
        stop_loss=Decimal("1999.20"),
        take_profits=(Decimal("2002.20"),),
        idempotency_key="demo:key",
        requested_price=Decimal("2000.20"),
    )
    check = await broker.validate_order(request)
    assert check.accepted
    report = await broker.place_market_order(request)
    positions = await broker.get_positions()
    assert report.success
    assert len(positions) == 1
    assert positions[0].stop_loss == request.stop_loss
    assert positions[0].take_profit == request.take_profits[0]


@pytest.mark.asyncio
async def test_mock_profit_calculation_is_directional() -> None:
    broker = MockBrokerAdapter.gold_demo()
    long_profit = await broker.calculate_profit(
        "XAUUSDm", Direction.LONG, Decimal("0.1"), Decimal("2000"), Decimal("2001")
    )
    short_profit = await broker.calculate_profit(
        "XAUUSDm", Direction.SHORT, Decimal("0.1"), Decimal("2000"), Decimal("2001")
    )
    assert long_profit == Decimal("10")
    assert short_profit == Decimal("-10")


@pytest.mark.asyncio
async def test_mock_history_is_read_only_and_time_bounded() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    broker = MockBrokerAdapter.gold_demo(now=now)
    assert await broker.history_deals(now, now) == ()
    assert broker.send_calls == 0
