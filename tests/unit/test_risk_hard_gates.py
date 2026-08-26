from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.brokers.base import BrokerHealth
from app.brokers.mock import MockBrokerAdapter
from app.domain.enums import Direction, OrderType
from app.domain.models import BrokerPosition, OrderRequest, Tick
from app.risk.calculator import PositionSizer
from app.risk.gates import RiskGateValidator
from app.risk.models import PreTradeSnapshot, RiskLimits, RiskUsage


async def valid_inputs() -> tuple[MockBrokerAdapter, OrderRequest, object, PreTradeSnapshot]:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("100"), now=now)
    account = await broker.get_account()
    spec = await broker.resolve_symbol("XAUUSD")
    tick = await broker.get_tick(spec.name)
    sizing = await PositionSizer().calculate(
        broker=broker,
        account=account,
        symbol=spec,
        direction=Direction.LONG,
        entry_price=tick.ask,
        stop_loss=Decimal("2000.10"),
        risk_fraction=Decimal("0.01"),
    )
    request = OrderRequest(
        signal_id=uuid4(),
        strategy_id="v1",
        symbol=spec.name,
        direction=Direction.LONG,
        order_type=OrderType.MARKET,
        volume=sizing.volume,
        stop_loss=Decimal("2000.10"),
        take_profits=(Decimal("2000.40"),),
        idempotency_key="key",
        requested_price=tick.ask,
        entry_min=Decimal("2000"),
        entry_max=Decimal("2001"),
    )
    margin = await broker.calculate_margin(request, tick.ask)
    snapshot = PreTradeSnapshot(
        now=now,
        account=account,
        symbol=spec,
        tick=tick,
        health=BrokerHealth(True, True, True, "ok"),
        market_open=True,
        session_allowed=True,
        required_margin=margin,
    )
    return broker, request, sizing, snapshot


@pytest.mark.asyncio
async def test_valid_order_passes_every_hard_gate() -> None:
    _, request, sizing, snapshot = await valid_inputs()
    decision = RiskGateValidator(RiskLimits()).validate(request, sizing, snapshot)  # type: ignore[arg-type]
    assert decision.accepted, decision.reasons


@pytest.mark.asyncio
async def test_stale_tick_high_spread_and_kill_switch_all_reject() -> None:
    _, request, sizing, snapshot = await valid_inputs()
    stale = Tick(
        snapshot.tick.symbol,
        Decimal("1999"),
        Decimal("2001"),
        snapshot.now - timedelta(minutes=5),
    )
    unsafe = PreTradeSnapshot(
        now=snapshot.now,
        account=snapshot.account,
        symbol=snapshot.symbol,
        tick=stale,
        health=snapshot.health,
        market_open=True,
        session_allowed=True,
        required_margin=snapshot.required_margin,
        kill_switch=True,
    )
    decision = RiskGateValidator(RiskLimits()).validate(request, sizing, unsafe)  # type: ignore[arg-type]
    assert not decision.accepted
    assert {"KILL_SWITCH_ACTIVE", "STALE_TICK", "TRADE_SKIPPED_HIGH_SPREAD"} <= set(
        decision.reasons
    )


@pytest.mark.asyncio
async def test_daily_and_weekly_limits_include_proposed_and_open_risk() -> None:
    _, request, sizing, snapshot = await valid_inputs()
    at_limit = PreTradeSnapshot(
        now=snapshot.now,
        account=snapshot.account,
        symbol=snapshot.symbol,
        tick=snapshot.tick,
        health=snapshot.health,
        market_open=True,
        session_allowed=True,
        usage=RiskUsage(
            daily_realized_loss=Decimal("1"),
            weekly_realized_loss=Decimal("5"),
            open_risk=Decimal("1"),
        ),
        required_margin=snapshot.required_margin,
    )
    decision = RiskGateValidator(RiskLimits()).validate(request, sizing, at_limit)  # type: ignore[arg-type]
    assert "DAILY_LOSS_LIMIT_REACHED" in decision.reasons
    assert "WEEKLY_LOSS_LIMIT_REACHED" in decision.reasons


@pytest.mark.asyncio
async def test_unhealthy_or_closed_market_rejects() -> None:
    _, request, sizing, snapshot = await valid_inputs()
    unsafe = PreTradeSnapshot(
        now=snapshot.now,
        account=snapshot.account,
        symbol=snapshot.symbol,
        tick=snapshot.tick,
        health=BrokerHealth(False, False, False, "down"),
        market_open=False,
        session_allowed=True,
        required_margin=snapshot.required_margin,
    )
    decision = RiskGateValidator(RiskLimits()).validate(request, sizing, unsafe)  # type: ignore[arg-type]
    assert {"BROKER_UNHEALTHY", "BROKER_TRADING_DISABLED", "MARKET_CLOSED"} <= set(decision.reasons)


@pytest.mark.asyncio
async def test_invalid_stop_margin_and_existing_position_all_reject() -> None:
    _, request, sizing, snapshot = await valid_inputs()
    existing = BrokerPosition(
        ticket="existing-position",
        symbol=snapshot.symbol.name,
        direction=Direction.LONG,
        volume=sizing.volume,
        open_price=snapshot.tick.ask,
        current_price=snapshot.tick.ask,
        stop_loss=request.stop_loss,
        take_profit=request.take_profits[-1],
        profit=Decimal("0"),
        opened_at=snapshot.now,
    )
    unsafe_request = replace(request, stop_loss=Decimal("2000.19"))
    unsafe = replace(
        snapshot,
        account=replace(snapshot.account, free_margin=Decimal("-1")),
        positions=(existing,),
        required_margin=Decimal("1000000"),
    )

    decision = RiskGateValidator(RiskLimits()).validate(  # type: ignore[arg-type]
        unsafe_request,
        sizing,
        unsafe,
    )

    assert {
        "INVALID_STOP_DISTANCE",
        "ACCOUNT_EQUITY_OR_MARGIN_INVALID",
        "INSUFFICIENT_OR_INVALID_MARGIN",
        "MAXIMUM_OPEN_POSITIONS_REACHED",
        "MAXIMUM_GOLD_POSITIONS_REACHED",
        "DUPLICATE_POSITION",
    } <= set(decision.reasons)
