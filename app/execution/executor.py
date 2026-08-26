"""Single-send, risk-first trade execution orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.brokers.base import (
    BrokerAdapter,
    IndeterminateBrokerResult,
    broker_correlation_key,
)
from app.domain.enums import (
    OrderType,
    SignalAction,
    TradeState,
    TradingEnvironment,
    TradingMode,
)
from app.domain.errors import BrokerError, RiskRejectedError
from app.domain.models import ExecutionReport, OrderCheckResult, OrderRequest
from app.domain.state_machine import StateTransition, TradeStateMachine
from app.execution.idempotency import (
    ExecutionRecord,
    IdempotencyStatus,
    IdempotencyStore,
)
from app.execution.models import ExecutionCommand, ExecutionOutcome, ExecutionStatus
from app.risk.calculator import PositionSizer
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.demo_validation import evaluate_demo_performance
from app.risk.gates import RiskGateValidator
from app.risk.mode import evaluate_execution_permission
from app.risk.models import PreTradeSnapshot, RiskDecision, RiskLimits


@dataclass(frozen=True, slots=True)
class _ProtectionResult:
    protected: bool
    reason: str


Sleep = Callable[[float], Awaitable[None]]


class TradeExecutor:
    """The only component authorized to turn a signal into new broker exposure."""

    def __init__(
        self,
        *,
        broker: BrokerAdapter,
        idempotency: IdempotencyStore,
        limits: RiskLimits,
        circuit_breaker: CircuitBreaker | None = None,
        clock: Callable[[], datetime] | None = None,
        require_demo_validation_for_live: bool = True,
        minimum_demo_trades: int = 100,
        maximum_demo_drawdown: Decimal = Decimal("0.10"),
        minimum_demo_profit_factor: Decimal = Decimal("1.2"),
        protection_visibility_attempts: int = 3,
        protection_visibility_delay_seconds: float = 0.25,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if protection_visibility_attempts < 1:
            raise ValueError("protection visibility attempts must be positive")
        if protection_visibility_delay_seconds < 0:
            raise ValueError("protection visibility delay cannot be negative")
        self._broker = broker
        self._idempotency = idempotency
        self._limits = limits
        self._sizer = PositionSizer()
        self._gates = RiskGateValidator(limits)
        self._circuit = circuit_breaker
        self._clock = clock or (lambda: datetime.now(UTC))
        self._require_demo_validation_for_live = require_demo_validation_for_live
        self._minimum_demo_trades = minimum_demo_trades
        self._maximum_demo_drawdown = maximum_demo_drawdown
        self._minimum_demo_profit_factor = minimum_demo_profit_factor
        self._protection_visibility_attempts = protection_visibility_attempts
        self._protection_visibility_delay_seconds = protection_visibility_delay_seconds
        self._sleep = sleep
        self._execution_lock = asyncio.Lock()

    async def execute(self, command: ExecutionCommand) -> ExecutionOutcome:
        if command.mode is TradingMode.SIGNAL:
            return ExecutionOutcome(ExecutionStatus.SIGNAL_ONLY, ("SIGNAL_MODE_NEVER_EXECUTES",))
        async with self._execution_lock:
            return await self._execute_serialized(command)

    async def _execute_serialized(self, command: ExecutionCommand) -> ExecutionOutcome:
        now = self._clock().astimezone(UTC)
        signal = command.signal
        if signal.action not in (SignalAction.LONG, SignalAction.SHORT):
            return ExecutionOutcome(ExecutionStatus.BLOCKED, ("SIGNAL_HAS_NO_ENTRY_DIRECTION",))
        if signal.direction is None or signal.stop_loss is None or not signal.take_profits:
            return ExecutionOutcome(ExecutionStatus.BLOCKED, ("SIGNAL_PROTECTION_INCOMPLETE",))
        if signal.is_expired(now):
            return ExecutionOutcome(ExecutionStatus.BLOCKED, ("SIGNAL_EXPIRED",))

        try:
            health = await self._broker.health_check()
            if not health.healthy or not health.connected:
                return ExecutionOutcome(ExecutionStatus.BLOCKED, ("BROKER_UNHEALTHY",))
            account = await self._broker.get_account()
        except BrokerError as exc:
            return ExecutionOutcome(ExecutionStatus.BLOCKED, (type(exc).__name__,))

        permission = evaluate_execution_permission(
            mode=command.mode,
            environment=command.environment,
            account_type=account.account_type,
            approved=command.approved,
            live_trading_enabled=command.live_trading_enabled,
            configured_confirmation=command.configured_live_confirmation,
            required_confirmation=command.required_live_confirmation,
        )
        if not permission.allowed:
            return ExecutionOutcome(ExecutionStatus.BLOCKED, permission.reasons)
        if (
            command.environment is TradingEnvironment.PRODUCTION
            and command.mode is TradingMode.AUTO
            and self._require_demo_validation_for_live
        ):
            demo = evaluate_demo_performance(
                command.demo_performance,
                strategy_version=signal.strategy_version,
                minimum_trades=self._minimum_demo_trades,
                maximum_drawdown=self._maximum_demo_drawdown,
                minimum_profit_factor=self._minimum_demo_profit_factor,
            )
            if not demo.allowed:
                return ExecutionOutcome(ExecutionStatus.BLOCKED, demo.reasons)

        try:
            spec = await self._broker.resolve_symbol(signal.canonical_symbol)
            tick = await self._broker.get_tick(spec.name)
            price = command.requested_price or (tick.ask if signal.direction.sign > 0 else tick.bid)
            sizing = await self._sizer.calculate(
                broker=self._broker,
                account=account,
                symbol=spec,
                direction=signal.direction,
                entry_price=price,
                stop_loss=signal.stop_loss,
                risk_fraction=self._limits.risk_per_trade,
            )
        except (BrokerError, RiskRejectedError, ValueError) as exc:
            return ExecutionOutcome(ExecutionStatus.BLOCKED, (str(exc),))

        idempotency_key = f"{account.account_id}:{signal.signal_id}"
        request = OrderRequest(
            signal_id=signal.signal_id,
            strategy_id=signal.strategy_id,
            symbol=spec.name,
            direction=signal.direction,
            order_type=command.order_type,
            volume=sizing.volume,
            stop_loss=signal.stop_loss,
            take_profits=signal.take_profits,
            idempotency_key=idempotency_key,
            requested_price=price,
            entry_min=signal.entry_min,
            entry_max=signal.entry_max,
            expires_at=signal.expires_at,
            maximum_slippage=command.maximum_slippage,
        )

        existing = await self._idempotency.get(idempotency_key)
        if existing is not None:
            return self._outcome_from_record(existing)

        try:
            positions = await self._broker.get_positions()
            orders = await self._broker.get_orders()
            available_symbols = await self._broker.get_symbols()
            market_open = await self._broker.is_market_open(spec.name)
            required_margin = await self._broker.calculate_margin(request, price)
        except BrokerError as exc:
            return ExecutionOutcome(ExecutionStatus.BLOCKED, (type(exc).__name__,), request=request)

        circuit_locked = False
        kill_switch = False
        if self._circuit is not None:
            try:
                circuit = await self._circuit.evaluate(account, command.risk_usage, now)
            except RuntimeError:
                return ExecutionOutcome(
                    ExecutionStatus.BLOCKED,
                    ("CIRCUIT_STATE_UNAVAILABLE",),
                    request=request,
                )
            circuit_locked = circuit.blocks_new_trades
            kill_switch = circuit.kill_switch
        canonical = signal.canonical_symbol.upper()
        equivalent_symbols = frozenset(
            item.name
            for item in available_symbols
            if item.canonical_symbol.upper() == canonical
            or (
                item.base_currency.upper() == canonical[:3]
                and item.quote_currency.upper() == canonical[3:]
            )
        )
        snapshot = PreTradeSnapshot(
            now=now,
            account=account,
            symbol=spec,
            tick=tick,
            health=health,
            market_open=market_open,
            session_allowed=command.session_allowed,
            equivalent_symbols=equivalent_symbols,
            positions=positions,
            orders=orders,
            usage=command.risk_usage,
            required_margin=required_margin,
            kill_switch=kill_switch,
            circuit_locked=circuit_locked,
        )
        decision = self._gates.validate(request, sizing, snapshot)
        if not decision.accepted:
            return ExecutionOutcome(
                ExecutionStatus.BLOCKED,
                decision.reasons,
                request=request,
                risk=decision,
            )

        record, claimed = await self._idempotency.claim(request, now)
        if not claimed:
            return self._outcome_from_record(record, risk=decision)

        transitions: list[StateTransition] = []
        machine = TradeStateMachine(TradeState.SIGNAL_FOUND)
        transitions.append(machine.transition(TradeState.ENTRY_READY, "pre-trade gates accepted"))
        check = await self._safe_order_check(request)
        if not check.accepted:
            await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.REJECTED,
                now,
                reason=", ".join(check.reasons),
            )
            transitions.append(machine.transition(TradeState.ERROR, "broker order_check rejected"))
            return ExecutionOutcome(
                ExecutionStatus.CHECK_REJECTED,
                check.reasons,
                request=request,
                risk=decision,
                transitions=tuple(transitions),
            )

        transitions.append(machine.transition(TradeState.ORDER_PENDING, "order submission started"))
        await self._idempotency.update(
            idempotency_key, IdempotencyStatus.SUBMITTED, now, reason="order_send started"
        )
        try:
            report = (
                await self._broker.place_market_order(request)
                if request.order_type is OrderType.MARKET
                else await self._broker.place_pending_order(request)
            )
        except (IndeterminateBrokerResult, TimeoutError) as exc:
            await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.UNKNOWN,
                self._clock(),
                reason=str(exc),
            )
            transitions.append(
                machine.transition(TradeState.ERROR, "order result is indeterminate")
            )
            return ExecutionOutcome(
                ExecutionStatus.UNKNOWN,
                ("ORDER_RESULT_UNKNOWN_RECONCILIATION_REQUIRED",),
                request=request,
                risk=decision,
                transitions=tuple(transitions),
                requires_reconciliation=True,
            )
        except BrokerError as exc:
            # Even an SDK exception after mutation can be ambiguous. Fail into UNKNOWN;
            # only reconciliation may later classify it.
            await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.UNKNOWN,
                self._clock(),
                reason=str(exc),
            )
            transitions.append(machine.transition(TradeState.ERROR, "broker mutation failed"))
            return ExecutionOutcome(
                ExecutionStatus.UNKNOWN,
                ("BROKER_MUTATION_ERROR_RECONCILIATION_REQUIRED",),
                request=request,
                risk=decision,
                transitions=tuple(transitions),
                requires_reconciliation=True,
            )

        if not report.success:
            await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.REJECTED,
                self._clock(),
                report=report,
                reason=report.message,
            )
            transitions.append(machine.transition(TradeState.ERROR, "broker rejected order_send"))
            return ExecutionOutcome(
                ExecutionStatus.REJECTED,
                (report.message or "ORDER_REJECTED",),
                request=request,
                report=report,
                risk=decision,
                transitions=tuple(transitions),
            )

        reasons: list[str] = []
        if report.slippage is not None and report.slippage > request.maximum_slippage:
            reasons.append("EXECUTED_SLIPPAGE_EXCEEDED_TOLERANCE")
        if request.order_type is OrderType.MARKET:
            transitions.append(machine.transition(TradeState.ORDER_FILLED, "broker confirmed fill"))
            protection = await self._verify_or_repair_protection(request, report)
            if not protection.protected:
                await self._idempotency.update(
                    idempotency_key,
                    IdempotencyStatus.PROTECTION_FAILED,
                    self._clock(),
                    report=report,
                    reason=protection.reason,
                )
                transitions.append(
                    machine.transition(
                        TradeState.ERROR, "position protection could not be verified"
                    )
                )
                return ExecutionOutcome(
                    ExecutionStatus.PROTECTION_FAILED,
                    (protection.reason,),
                    request=request,
                    report=report,
                    risk=decision,
                    transitions=tuple(transitions),
                    requires_reconciliation=True,
                )
            await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.SUCCEEDED,
                self._clock(),
                report=report,
                reason=protection.reason,
            )
            transitions.append(machine.transition(TradeState.POSITION_OPEN, protection.reason))
            status = ExecutionStatus.FILLED
        else:
            await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.SUCCEEDED,
                self._clock(),
                report=report,
                reason="broker confirmed pending order",
            )
            status = ExecutionStatus.PENDING
        return ExecutionOutcome(
            status,
            tuple(reasons),
            request=request,
            report=report,
            risk=decision,
            transitions=tuple(transitions),
        )

    async def _verify_or_repair_protection(
        self,
        request: OrderRequest,
        report: ExecutionReport,
    ) -> _ProtectionResult:
        """Bound visibility checks, then repair once or close once; never retry a mutation."""

        matched_ticket: str | None = report.broker_ticket
        for attempt in range(1, self._protection_visibility_attempts + 1):
            try:
                positions = await self._broker.get_positions(request.symbol)
            except BrokerError:
                positions = ()
            position = next(
                (
                    item
                    for item in positions
                    if item.ticket == report.broker_ticket
                    or item.signal_id == request.signal_id
                    or item.strategy_id == broker_correlation_key(request.idempotency_key)
                ),
                None,
            )
            if position is not None:
                matched_ticket = position.ticket
                if (
                    position.stop_loss == request.stop_loss
                    and position.take_profit == request.take_profits[-1]
                ):
                    return _ProtectionResult(True, "broker position protection verified")
                break
            if attempt < self._protection_visibility_attempts:
                await self._sleep(self._protection_visibility_delay_seconds)

        if not matched_ticket:
            return _ProtectionResult(False, "POSITION_NOT_VISIBLE_AND_NO_TICKET_TO_PROTECT")
        try:
            repaired = await self._broker.modify_position(
                matched_ticket,
                stop_loss=request.stop_loss,
                take_profit=request.take_profits[-1],
            )
        except BrokerError:
            repaired = False
        if repaired:
            return _ProtectionResult(True, "broker confirmed protection repair")
        try:
            close_report = await self._broker.close_position(matched_ticket)
        except (BrokerError, IndeterminateBrokerResult, TimeoutError):
            return _ProtectionResult(False, "PROTECTION_REPAIR_AND_SAFE_CLOSE_UNCONFIRMED")
        if close_report.success:
            return _ProtectionResult(False, "UNPROTECTED_FILL_SAFELY_CLOSED")
        return _ProtectionResult(False, "PROTECTION_REPAIR_AND_SAFE_CLOSE_FAILED")

    async def _safe_order_check(self, request: OrderRequest) -> OrderCheckResult:
        try:
            return await self._broker.validate_order(request)
        except BrokerError as exc:
            return OrderCheckResult(False, (f"BROKER_ORDER_CHECK_ERROR:{type(exc).__name__}",))

    def _outcome_from_record(
        self,
        record: ExecutionRecord,
        *,
        risk: RiskDecision | None = None,
    ) -> ExecutionOutcome:
        status_map = {
            IdempotencyStatus.CLAIMED: ExecutionStatus.UNKNOWN,
            IdempotencyStatus.SUBMITTED: ExecutionStatus.UNKNOWN,
            IdempotencyStatus.UNKNOWN: ExecutionStatus.UNKNOWN,
            IdempotencyStatus.SUCCEEDED: (
                ExecutionStatus.FILLED
                if record.request.order_type is OrderType.MARKET
                else ExecutionStatus.PENDING
            ),
            IdempotencyStatus.PROTECTION_FAILED: ExecutionStatus.PROTECTION_FAILED,
            IdempotencyStatus.REJECTED: ExecutionStatus.REJECTED,
        }
        unresolved = record.status in {
            IdempotencyStatus.CLAIMED,
            IdempotencyStatus.SUBMITTED,
            IdempotencyStatus.UNKNOWN,
            IdempotencyStatus.PROTECTION_FAILED,
        }
        return ExecutionOutcome(
            status_map[record.status],
            ("DUPLICATE_EXECUTION_SUPPRESSED",),
            request=record.request,
            report=record.report,
            risk=risk,
            requires_reconciliation=unresolved,
        )

    async def reconcile_unknown(self, idempotency_key: str) -> ExecutionOutcome:
        """Resolve an unknown result from broker state without resubmitting it."""

        async with self._execution_lock:
            record = await self._idempotency.get(idempotency_key)
            if record is None:
                return ExecutionOutcome(ExecutionStatus.BLOCKED, ("EXECUTION_RECORD_NOT_FOUND",))
            if record.status not in {
                IdempotencyStatus.SUBMITTED,
                IdempotencyStatus.UNKNOWN,
            }:
                return self._outcome_from_record(record)
            try:
                positions = await self._broker.get_positions(record.request.symbol)
                orders = await self._broker.get_orders(record.request.symbol)
            except BrokerError:
                return ExecutionOutcome(
                    ExecutionStatus.UNKNOWN,
                    ("BROKER_UNAVAILABLE_RECONCILIATION_DEFERRED",),
                    request=record.request,
                    requires_reconciliation=True,
                )
            matched_position = next(
                (
                    item
                    for item in positions
                    if item.signal_id == record.request.signal_id
                    or item.strategy_id == broker_correlation_key(idempotency_key)
                ),
                None,
            )
            matched_order = next(
                (
                    item
                    for item in orders
                    if item.idempotency_key
                    in {idempotency_key, broker_correlation_key(idempotency_key)}
                ),
                None,
            )
            if matched_position is None and matched_order is None:
                return ExecutionOutcome(
                    ExecutionStatus.UNKNOWN,
                    ("NO_BROKER_MATCH_YET_RECONCILIATION_REQUIRED",),
                    request=record.request,
                    requires_reconciliation=True,
                )
            if matched_position is not None:
                ticket = matched_position.ticket
                price = matched_position.open_price
            elif matched_order is not None:
                ticket = matched_order.ticket
                price = matched_order.price
            else:
                return ExecutionOutcome(
                    ExecutionStatus.UNKNOWN,
                    ("RECONCILIATION_MATCH_DISAPPEARED",),
                    request=record.request,
                    requires_reconciliation=True,
                )
            report = ExecutionReport(
                success=True,
                idempotency_key=idempotency_key,
                broker_ticket=ticket,
                requested_price=record.request.requested_price,
                executed_price=price if matched_position else None,
                volume=record.request.volume,
                broker_code="RECONCILED",
                message="matched broker position" if matched_position else "matched broker order",
                submitted_at=self._clock(),
            )
            if matched_position is not None:
                protection = await self._verify_or_repair_protection(record.request, report)
                if not protection.protected:
                    failed = await self._idempotency.update(
                        idempotency_key,
                        IdempotencyStatus.PROTECTION_FAILED,
                        self._clock(),
                        report=report,
                        reason=protection.reason,
                    )
                    return self._outcome_from_record(failed)
            resolved = await self._idempotency.update(
                idempotency_key,
                IdempotencyStatus.SUCCEEDED,
                self._clock(),
                report=report,
                reason="reconciled with broker state",
            )
            return self._outcome_from_record(resolved)

    async def cancel_pending_entries(self) -> tuple[str, ...]:
        """Cancel pending entries after a kill switch without touching positions."""

        cancelled: list[str] = []
        async with self._execution_lock:
            for order in await self._broker.get_orders():
                if await self._broker.cancel_order(order.ticket):
                    cancelled.append(order.ticket)
        return tuple(cancelled)
