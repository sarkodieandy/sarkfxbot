from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.domain.enums import Direction
from app.domain.errors import RiskRejectedError
from app.risk.calculator import PositionSizer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("equity", "expected_volume"),
    [
        (Decimal("50"), Decimal("0.05")),
        (Decimal("100"), Decimal("0.10")),
        (Decimal("1000"), Decimal("1.00")),
    ],
)
async def test_position_sizing_for_small_accounts(
    equity: Decimal, expected_volume: Decimal
) -> None:
    broker = MockBrokerAdapter.gold_demo(equity=equity)
    account = await broker.get_account()
    spec = await broker.resolve_symbol("XAUUSD")
    result = await PositionSizer().calculate(
        broker=broker,
        account=account,
        symbol=spec,
        direction=Direction.LONG,
        entry_price=Decimal("2000.20"),
        stop_loss=Decimal("2000.10"),
        risk_fraction=Decimal("0.01"),
    )
    assert result.volume == expected_volume
    assert result.cash_risk == equity * Decimal("0.01")


@pytest.mark.asyncio
async def test_minimum_lot_that_exceeds_budget_rejects_without_moving_stop() -> None:
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("50"), minimum_volume=Decimal("0.01"))
    account = await broker.get_account()
    spec = await broker.resolve_symbol("XAUUSD")
    stop = Decimal("1999.20")
    with pytest.raises(RiskRejectedError, match="MINIMUM_VOLUME"):
        await PositionSizer().calculate(
            broker=broker,
            account=account,
            symbol=spec,
            direction=Direction.LONG,
            entry_price=Decimal("2000.20"),
            stop_loss=stop,
            risk_fraction=Decimal("0.01"),
        )
    assert stop == Decimal("1999.20")


@pytest.mark.asyncio
async def test_zero_equity_rejects() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    broker = MockBrokerAdapter.gold_demo(now=now)
    account = replace(
        await broker.get_account(),
        equity=Decimal("0"),
        free_margin=Decimal("0"),
    )
    with pytest.raises(RiskRejectedError, match="EQUITY"):
        await PositionSizer().calculate(
            broker=broker,
            account=account,
            symbol=await broker.resolve_symbol("XAUUSD"),
            direction=Direction.LONG,
            entry_price=Decimal("2000.20"),
            stop_loss=Decimal("1999.20"),
            risk_fraction=Decimal("0.01"),
        )
