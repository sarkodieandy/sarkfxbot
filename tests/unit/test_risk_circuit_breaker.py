from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.brokers.mock import MockBrokerAdapter
from app.risk.circuit_breaker import CircuitBreaker, InMemoryCircuitStateStore
from app.risk.models import RiskLimits, RiskUsage


@pytest.mark.asyncio
async def test_account_drawdown_requires_manual_reset() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    account = await MockBrokerAdapter.gold_demo(equity=Decimal("90"), now=now).get_account()
    circuit = CircuitBreaker(InMemoryCircuitStateStore(), RiskLimits())
    state = await circuit.evaluate(account, RiskUsage(peak_equity=Decimal("100")), now)
    assert state.account_locked
    assert state.manual_reenable_required
    next_day = await circuit.get(account.account_id, now + timedelta(days=1))
    assert next_day.account_locked
    with pytest.raises(PermissionError):
        await circuit.manual_reset(account.account_id, now, authorized=False)
    reset = await circuit.manual_reset(account.account_id, now, authorized=True)
    assert not reset.blocks_new_trades


@pytest.mark.asyncio
async def test_daily_lock_resets_next_utc_day_but_kill_switch_does_not() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    account = await MockBrokerAdapter.gold_demo(equity=Decimal("100"), now=now).get_account()
    circuit = CircuitBreaker(InMemoryCircuitStateStore(), RiskLimits())
    state = await circuit.evaluate(account, RiskUsage(daily_realized_loss=Decimal("3")), now)
    assert state.daily_locked
    killed = await circuit.activate_kill_switch(account.account_id, now)
    assert killed.kill_switch
    tomorrow = await circuit.get(account.account_id, now + timedelta(days=1))
    assert not tomorrow.daily_locked
    assert tomorrow.kill_switch


@pytest.mark.asyncio
async def test_configurable_daily_reset_hour_boundary() -> None:
    before_reset = datetime(2026, 1, 2, 4, 59, tzinfo=UTC)
    account = await MockBrokerAdapter.gold_demo(
        equity=Decimal("100"), now=before_reset
    ).get_account()
    circuit = CircuitBreaker(InMemoryCircuitStateStore(), RiskLimits(), daily_reset_hour_utc=5)
    locked = await circuit.evaluate(
        account, RiskUsage(daily_realized_loss=Decimal("3")), before_reset
    )
    assert locked.daily_locked
    after_reset = await circuit.get(account.account_id, datetime(2026, 1, 2, 5, 0, tzinfo=UTC))
    assert not after_reset.daily_locked


@pytest.mark.asyncio
async def test_configurable_weekly_reset_weekday_boundary() -> None:
    tuesday = datetime(2026, 1, 6, 23, 59, tzinfo=UTC)
    account = await MockBrokerAdapter.gold_demo(equity=Decimal("100"), now=tuesday).get_account()
    circuit = CircuitBreaker(InMemoryCircuitStateStore(), RiskLimits(), weekly_reset_weekday=2)
    locked = await circuit.evaluate(account, RiskUsage(weekly_realized_loss=Decimal("7")), tuesday)
    assert locked.weekly_locked
    wednesday = await circuit.get(account.account_id, datetime(2026, 1, 7, 0, 0, tzinfo=UTC))
    assert not wednesday.weekly_locked
