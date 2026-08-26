from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.brokers.mock import MockBrokerAdapter, MockSendBehavior
from app.domain.enums import (
    AccountType,
    SignalAction,
    TradingEnvironment,
    TradingMode,
)
from app.domain.models import BrokerPosition, ExecutionReport, OrderRequest, TradeSignal
from app.execution.executor import TradeExecutor
from app.execution.idempotency import IdempotencyStatus, InMemoryIdempotencyStore
from app.execution.models import ExecutionCommand, ExecutionStatus
from app.risk.demo_validation import DemoPerformance
from app.risk.models import RiskLimits

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


class WrongProtectionMock(MockBrokerAdapter):
    async def place_market_order(self, request: OrderRequest) -> ExecutionReport:
        report = await super().place_market_order(request)
        if report.broker_ticket is None:
            raise RuntimeError("mock fill did not return a ticket")
        self.corrupt_position_protection(
            report.broker_ticket,
            stop_loss=Decimal("1"),
            take_profit=None,
        )
        return report


class HiddenFillMock(MockBrokerAdapter):
    async def get_positions(self, symbol: str | None = None) -> tuple[BrokerPosition, ...]:
        positions = await super().get_positions(symbol)
        return () if self.send_calls else positions


async def no_wait(_: float) -> None:
    return None


def signal() -> TradeSignal:
    return TradeSignal(
        symbol="XAUUSD",
        canonical_symbol="XAUUSD",
        action=SignalAction.LONG,
        strategy_id="gold",
        strategy_version="1.0.0",
        confidence_score=80,
        entry_min=Decimal("2000"),
        entry_max=Decimal("2001"),
        stop_loss=Decimal("1999.20"),
        take_profits=(Decimal("2002.20"),),
        risk_reward=Decimal("2"),
        created_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


def executor(broker: MockBrokerAdapter) -> TradeExecutor:
    return TradeExecutor(
        broker=broker,
        idempotency=InMemoryIdempotencyStore(),
        limits=RiskLimits(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_demo_auto_sends_once_with_stop_and_target() -> None:
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=NOW)
    outcome = await executor(broker).execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO, session_allowed=True)
    )
    assert outcome.status is ExecutionStatus.FILLED, outcome.reasons
    assert broker.send_calls == 1
    assert outcome.request is not None
    assert outcome.request.stop_loss == signal().stop_loss


@pytest.mark.asyncio
async def test_signal_mode_never_calls_broker_send() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    outcome = await executor(broker).execute(
        ExecutionCommand(signal(), TradingMode.SIGNAL, TradingEnvironment.DEMO)
    )
    assert outcome.status is ExecutionStatus.SIGNAL_ONLY
    assert broker.send_calls == 0


@pytest.mark.asyncio
async def test_real_account_is_blocked_in_demo_environment() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    broker.set_account(replace(await broker.get_account(), account_type=AccountType.REAL))
    outcome = await executor(broker).execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO)
    )
    assert outcome.status is ExecutionStatus.BLOCKED
    assert "REAL_ACCOUNT_FORBIDDEN_OUTSIDE_PRODUCTION" in outcome.reasons
    assert broker.send_calls == 0


@pytest.mark.asyncio
async def test_production_auto_requires_matching_demo_evidence() -> None:
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=NOW)
    broker.set_account(replace(await broker.get_account(), account_type=AccountType.REAL))
    outcome = await executor(broker).execute(
        ExecutionCommand(
            signal(),
            TradingMode.AUTO,
            TradingEnvironment.PRODUCTION,
            live_trading_enabled=True,
            configured_live_confirmation="confirmed",
            required_live_confirmation="confirmed",
            session_allowed=True,
        )
    )
    assert outcome.status is ExecutionStatus.BLOCKED
    assert outcome.reasons == ("DEMO_VALIDATION_EVIDENCE_MISSING",)
    assert broker.send_calls == 0


@pytest.mark.asyncio
async def test_production_auto_accepts_threshold_demo_evidence() -> None:
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=NOW)
    broker.set_account(replace(await broker.get_account(), account_type=AccountType.REAL))
    outcome = await executor(broker).execute(
        ExecutionCommand(
            signal(),
            TradingMode.AUTO,
            TradingEnvironment.PRODUCTION,
            live_trading_enabled=True,
            configured_live_confirmation="confirmed",
            required_live_confirmation="confirmed",
            demo_performance=DemoPerformance("1.0.0", 100, Decimal("0.10"), Decimal("1.2")),
            session_allowed=True,
        )
    )
    assert outcome.status is ExecutionStatus.FILLED, outcome.reasons
    assert broker.send_calls == 1


@pytest.mark.asyncio
async def test_timeout_after_fill_never_blindly_retries_and_reconciles() -> None:
    broker = MockBrokerAdapter.gold_demo(
        equity=Decimal("1000"),
        now=NOW,
        send_behaviors=(MockSendBehavior.TIMEOUT_AFTER_ACCEPT,),
    )
    store = InMemoryIdempotencyStore()
    trade_executor = TradeExecutor(
        broker=broker,
        idempotency=store,
        limits=RiskLimits(),
        clock=lambda: NOW,
    )
    command = ExecutionCommand(
        signal(), TradingMode.AUTO, TradingEnvironment.DEMO, session_allowed=True
    )
    first = await trade_executor.execute(command)
    second = await trade_executor.execute(command)
    assert first.status is ExecutionStatus.UNKNOWN
    assert second.status is ExecutionStatus.UNKNOWN
    assert first.requires_reconciliation and second.requires_reconciliation
    assert broker.send_calls == 1
    assert first.request is not None
    reconciled = await trade_executor.reconcile_unknown(first.request.idempotency_key)
    assert reconciled.status is ExecutionStatus.FILLED
    third = await trade_executor.execute(command)
    assert third.status is ExecutionStatus.FILLED
    assert broker.send_calls == 1


@pytest.mark.asyncio
async def test_restart_reconciles_submitted_record_without_resending() -> None:
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=NOW)
    store = InMemoryIdempotencyStore()
    trade_executor = TradeExecutor(
        broker=broker,
        idempotency=store,
        limits=RiskLimits(),
        clock=lambda: NOW,
    )
    completed = await trade_executor.execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO, session_allowed=True)
    )
    assert completed.request is not None
    submitted_store = InMemoryIdempotencyStore()
    await submitted_store.claim(completed.request, NOW)
    await submitted_store.update(
        completed.request.idempotency_key, IdempotencyStatus.SUBMITTED, NOW
    )
    restarted = TradeExecutor(
        broker=broker,
        idempotency=submitted_store,
        limits=RiskLimits(),
        clock=lambda: NOW,
    )
    recovered = await restarted.reconcile_unknown(completed.request.idempotency_key)
    assert recovered.status is ExecutionStatus.FILLED
    assert broker.send_calls == 1


@pytest.mark.asyncio
async def test_expired_signal_is_never_sent() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    expired = replace(
        signal(),
        created_at=NOW - timedelta(minutes=10),
        expires_at=NOW - timedelta(minutes=1),
    )
    outcome = await executor(broker).execute(
        ExecutionCommand(expired, TradingMode.AUTO, TradingEnvironment.DEMO)
    )
    assert outcome.status is ExecutionStatus.BLOCKED
    assert broker.send_calls == 0


@pytest.mark.asyncio
async def test_execution_defaults_to_session_closed() -> None:
    broker = MockBrokerAdapter.gold_demo(now=NOW)
    outcome = await executor(broker).execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO)
    )
    assert outcome.status is ExecutionStatus.BLOCKED
    assert "TRADING_SESSION_NOT_ALLOWED" in outcome.reasons
    assert broker.send_calls == 0


@pytest.mark.asyncio
async def test_wrong_fill_protection_is_repaired_once_before_claiming_open() -> None:
    broker = WrongProtectionMock.gold_demo(equity=Decimal("1000"), now=NOW)
    assert isinstance(broker, WrongProtectionMock)
    outcome = await executor(broker).execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO, session_allowed=True)
    )
    assert outcome.status is ExecutionStatus.FILLED
    assert broker.modify_calls == 1
    position = (await broker.get_positions())[0]
    assert position.stop_loss == signal().stop_loss
    assert position.take_profit == signal().take_profits[0]


@pytest.mark.asyncio
async def test_failed_protection_repair_closes_once_and_requires_reconciliation() -> None:
    broker = WrongProtectionMock.gold_demo(equity=Decimal("1000"), now=NOW)
    assert isinstance(broker, WrongProtectionMock)
    broker.set_position_modification_allowed(False)
    outcome = await executor(broker).execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO, session_allowed=True)
    )
    assert outcome.status is ExecutionStatus.PROTECTION_FAILED
    assert outcome.requires_reconciliation
    assert outcome.reasons == ("UNPROTECTED_FILL_SAFELY_CLOSED",)
    assert broker.modify_calls == 1
    assert broker.close_calls == 1
    assert await broker.get_positions() == ()


@pytest.mark.asyncio
async def test_missing_fill_visibility_attempts_one_repair_without_resending() -> None:
    broker = HiddenFillMock.gold_demo(equity=Decimal("1000"), now=NOW)
    assert isinstance(broker, HiddenFillMock)
    trade_executor = TradeExecutor(
        broker=broker,
        idempotency=InMemoryIdempotencyStore(),
        limits=RiskLimits(),
        clock=lambda: NOW,
        protection_visibility_attempts=2,
        protection_visibility_delay_seconds=0,
        sleep=no_wait,
    )
    outcome = await trade_executor.execute(
        ExecutionCommand(signal(), TradingMode.AUTO, TradingEnvironment.DEMO, session_allowed=True)
    )
    assert outcome.status is ExecutionStatus.FILLED
    assert broker.modify_calls == 1
    assert broker.send_calls == 1
