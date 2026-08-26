from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.domain.enums import Direction
from app.domain.models import BrokerPosition
from app.execution.positions import PositionManager, TrailingStopMode

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def open_position(*, volume: Decimal = Decimal("0.10")) -> BrokerPosition:
    return BrokerPosition(
        ticket="P1",
        symbol="XAUUSDm",
        direction=Direction.LONG,
        volume=volume,
        open_price=Decimal("2000"),
        current_price=Decimal("2002"),
        stop_loss=Decimal("1999"),
        take_profit=Decimal("2004"),
        profit=Decimal("20"),
        opened_at=NOW,
        strategy_id="gold",
        signal_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_partial_close_respects_broker_step_and_minimum() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.seed_position(open_position())
    result = await PositionManager(broker).partial_close("P1", Decimal("0.5"))
    remaining = (await broker.get_positions())[0]
    assert result.applied
    assert remaining.volume == Decimal("0.05")


@pytest.mark.asyncio
async def test_break_even_includes_spread_commission_and_slippage_buffers() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.seed_position(open_position())
    result = await PositionManager(broker).move_to_break_even(
        "P1",
        spread_price=Decimal("0.20"),
        commission_price_equivalent=Decimal("0.03"),
        slippage_price=Decimal("0.02"),
    )
    updated = (await broker.get_positions())[0]
    assert result.applied
    assert updated.stop_loss == Decimal("2000.25")


@pytest.mark.asyncio
async def test_trailing_is_disabled_by_default() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.seed_position(open_position())
    result = await PositionManager(broker).trail(
        "P1", mode=TrailingStopMode.FIXED, fixed_distance=Decimal("1")
    )
    assert not result.applied
    assert result.reason == "TRAILING_DISABLED"
    assert broker.modify_calls == 0


@pytest.mark.asyncio
async def test_enabled_trailing_never_moves_stop_backwards() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.seed_position(open_position())
    manager = PositionManager(broker, trailing_enabled=True)
    applied = await manager.trail("P1", mode=TrailingStopMode.FIXED, fixed_distance=Decimal("1"))
    backwards = await manager.trail(
        "P1", mode=TrailingStopMode.STRUCTURE, structure_stop=Decimal("2000")
    )
    assert applied.applied
    assert not backwards.applied
