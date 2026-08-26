from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.brokers.base import IndeterminateBrokerResult
from app.brokers.mock import MockBrokerAdapter, MockSendBehavior
from app.brokers.retry import ReadRetryPolicy, RetryingBrokerAdapter
from app.domain.enums import Direction, OrderType
from app.domain.errors import BrokerUnavailableError
from app.domain.models import OrderRequest, Tick


class FlakyTickBroker(MockBrokerAdapter):
    read_attempts = 0

    async def get_tick(self, symbol: str) -> Tick:
        self.read_attempts += 1
        if self.read_attempts < 3:
            raise BrokerUnavailableError("transient read failure")
        return await super().get_tick(symbol)


async def no_wait(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_idempotent_read_uses_bounded_retry() -> None:
    flaky = FlakyTickBroker.gold_demo()
    assert isinstance(flaky, FlakyTickBroker)
    broker = RetryingBrokerAdapter(
        flaky,
        policy=ReadRetryPolicy(maximum_attempts=3, initial_delay_seconds=0),
        sleep=no_wait,
    )
    tick = await broker.get_tick("XAUUSDm")
    assert tick.ask == Decimal("2000.20")
    assert flaky.read_attempts == 3


@pytest.mark.asyncio
async def test_order_send_is_never_retried_by_retry_facade() -> None:
    delegate = MockBrokerAdapter.gold_demo(
        now=datetime(2026, 1, 1, tzinfo=UTC),
        send_behaviors=(MockSendBehavior.TIMEOUT_AFTER_ACCEPT,),
    )
    broker = RetryingBrokerAdapter(delegate, sleep=no_wait)
    request = OrderRequest(
        signal_id=uuid4(),
        strategy_id="gold",
        symbol="XAUUSDm",
        direction=Direction.LONG,
        order_type=OrderType.MARKET,
        volume=Decimal("0.1"),
        stop_loss=Decimal("1999"),
        take_profits=(Decimal("2003"),),
        idempotency_key="single-send",
        requested_price=Decimal("2000.2"),
    )
    with pytest.raises(IndeterminateBrokerResult):
        await broker.place_market_order(request)
    assert delegate.send_calls == 1
