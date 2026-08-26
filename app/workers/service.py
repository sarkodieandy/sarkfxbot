"""Scheduled, restart-safe orchestration for scanning and protected execution."""

from __future__ import annotations

import logging
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import __version__
from app.brokers.base import BrokerAdapter
from app.config.settings import Settings
from app.db.session import Database
from app.domain.enums import Direction, OrderType, SignalAction, TradeState, TradingMode
from app.domain.errors import BrokerError, ConfigurationError
from app.domain.models import BrokerPosition, TradeSignal
from app.execution.executor import TradeExecutor
from app.execution.models import ExecutionCommand, ExecutionOutcome
from app.execution.positions import PositionAction, PositionManager, TrailingStopMode
from app.execution.reconciliation import ReconciliationService
from app.execution.sqlalchemy_idempotency import SqlAlchemyIdempotencyStore
from app.indicators.atr import atr
from app.market.sessions import default_session_calendar
from app.notifications.base import (
    Notification,
    NotificationDeliveryError,
    NotificationLevel,
    Notifier,
    NullNotifier,
)
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.models import RiskLimits
from app.runtime_contract import (
    EVENT_CHANNEL,
    EXECUTION_QUEUE,
    RUNTIME_CONFIG_TYPE,
    WORKER_COMPONENTS,
    DurableRuntimeState,
    event_message,
    heartbeat_key,
)
from app.strategies.base import Strategy
from app.strategies.gold_h1_m15_m5 import GoldStrategyConfig, GoldTrendPullbackStrategy
from app.workers.lease import LeaseRunStatus, RedisLeaseManager
from app.workers.persistence import (
    OutboxMessage,
    SQLAlchemyCircuitStateStore,
    SQLAlchemyRecoveryLedger,
    WorkerPersistence,
)

logger = logging.getLogger("goldflow.worker")
Clock = Callable[[], datetime]
StrategyFactory = Callable[[GoldStrategyConfig, str], Strategy]


class WorkerRedis(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> Any:
        raise RuntimeError("Redis set implementation is required")

    async def get(self, name: str) -> Any:
        raise RuntimeError("Redis get implementation is required")

    async def delete(self, *names: str) -> Any:
        raise RuntimeError("Redis delete implementation is required")

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        raise RuntimeError("Redis eval implementation is required")

    async def publish(self, channel: str, message: str) -> Any:
        raise RuntimeError("Redis publish implementation is required")

    async def rpush(self, name: str, *values: str) -> Any:
        raise RuntimeError("Redis queue implementation is required")

    async def lpop(self, name: str, count: int | None = None) -> Any:
        raise RuntimeError("Redis queue implementation is required")


def _default_strategy_factory(config: GoldStrategyConfig, version: str) -> Strategy:
    return GoldTrendPullbackStrategy(config, strategy_version=version)


class GoldFlowWorker:
    """One worker process; Redis leases coordinate multiple replicas.

    PostgreSQL contains durable intent and journals, Redis only coordinates jobs,
    and the broker remains authoritative for actual positions and orders.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        broker: BrokerAdapter,
        redis: WorkerRedis | None,
        notifier: Notifier | None = None,
        clock: Clock | None = None,
        strategy_factory: StrategyFactory = _default_strategy_factory,
        instance_key: str | None = None,
        hostname: str | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.broker = broker
        self.redis = redis
        self.notifier = notifier or NullNotifier()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._strategy_factory = strategy_factory
        self._instance_key = instance_key or f"worker-{uuid4()}"
        self._hostname = hostname or socket.gethostname()
        self.persistence = WorkerPersistence(database.session_factory)
        self._leases = RedisLeaseManager(redis)
        self._scheduler: AsyncIOScheduler | None = None
        self._broker_account_id: str | None = None
        self._symbol_id: str | None = None
        self._symbol_name: str | None = None
        self._executor: TradeExecutor | None = None
        self._reconciliation: ReconciliationService | None = None
        self._component_healthy = {component: False for component in WORKER_COMPONENTS}
        self._started = False

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("worker clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)

    def _require_started(self) -> tuple[str, str]:
        if not self._started or self._broker_account_id is None or self._symbol_name is None:
            raise RuntimeError("worker has not completed startup recovery")
        return self._broker_account_id, self._symbol_name

    async def startup(self) -> None:
        """Connect, identify, reconcile, and resume protection before scanning."""

        if self._started:
            return
        try:
            await self._startup_connected()
        except BaseException:
            self._started = False
            self._broker_account_id = None
            self._symbol_id = None
            self._symbol_name = None
            self._executor = None
            self._reconciliation = None
            for component in WORKER_COMPONENTS:
                self._component_healthy[component] = False
            try:
                await self.broker.disconnect()
            except Exception:
                logger.exception(
                    "worker_startup_disconnect_failed",
                    extra={"service": "worker", "event": "WORKER_STARTUP_DISCONNECT_FAILED"},
                )
            raise

    async def _startup_connected(self) -> None:
        await self.broker.connect()
        health = await self.broker.health_check()
        if not health.healthy or not health.connected:
            raise BrokerError("broker did not become healthy during worker startup")
        account = await self.broker.get_account()
        specification = await self.broker.resolve_symbol(self.settings.canonical_symbol)
        broker_account_id = await self.persistence.ensure_broker_account(account)
        symbol_id = await self.persistence.ensure_symbol(broker_account_id, specification)
        self._broker_account_id = broker_account_id
        self._symbol_id = symbol_id
        self._symbol_name = specification.name

        circuit_store = SQLAlchemyCircuitStateStore(self.database.session_factory)
        initial_limits = await self._risk_limits()
        circuit = CircuitBreaker(circuit_store, initial_limits)
        idempotency = SqlAlchemyIdempotencyStore(
            self.database.session_factory,
            self.persistence.order_id_resolver(broker_account_id),
        )
        self._executor = self._build_executor(idempotency, circuit, initial_limits)
        ledger = SQLAlchemyRecoveryLedger(self.database.session_factory, broker_account_id)
        self._reconciliation = ReconciliationService(self.broker, ledger, clock=self._clock)
        self._started = True

        report = await self._reconciliation.recover_on_startup()
        await self.persistence.record_reconciliation(report, self._now())
        await self.reconcile_unknown_executions()
        await self.manage_positions()
        for component in WORKER_COMPONENTS:
            self._component_healthy[component] = True
        await self.heartbeat()
        await self._publish(
            "BOT_STATUS",
            {
                "status": "RUNNING",
                "environment": self.settings.trading_env.value,
                "account_type": account.account_type.value,
                "symbol": specification.name,
                "reconciliation_healthy": report.healthy,
            },
        )

    def _build_executor(
        self,
        idempotency: SqlAlchemyIdempotencyStore,
        circuit: CircuitBreaker,
        limits: RiskLimits,
    ) -> TradeExecutor:
        return TradeExecutor(
            broker=self.broker,
            idempotency=idempotency,
            limits=limits,
            circuit_breaker=circuit,
            clock=self._clock,
            require_demo_validation_for_live=self.settings.require_demo_validation_for_live,
            minimum_demo_trades=self.settings.minimum_demo_trades,
            maximum_demo_drawdown=self.settings.maximum_demo_drawdown,
            minimum_demo_profit_factor=self.settings.minimum_demo_profit_factor,
        )

    async def _runtime_state(self) -> DurableRuntimeState:
        payload = await self.persistence.active_config(RUNTIME_CONFIG_TYPE)
        return DurableRuntimeState.from_payload(
            payload,
            default_mode=self.settings.trading_mode,
            default_updated_at=self._now(),
        )

    async def _risk_config(self) -> dict[str, Any]:
        return await self.persistence.active_config("risk")

    async def _risk_limits(self) -> RiskLimits:
        payload = await self._risk_config()

        def decimal_value(name: str, fallback: Decimal) -> Decimal:
            return Decimal(str(payload.get(name, fallback)))

        def integer_value(name: str, fallback: int) -> int:
            value = payload.get(name, fallback)
            if isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
            return int(value)

        return RiskLimits(
            risk_per_trade=decimal_value("risk_per_trade", self.settings.risk_per_trade),
            maximum_daily_loss=decimal_value("maximum_daily_loss", self.settings.max_daily_loss),
            maximum_weekly_loss=decimal_value("maximum_weekly_loss", self.settings.max_weekly_loss),
            maximum_account_drawdown=decimal_value(
                "maximum_account_drawdown", self.settings.max_account_drawdown
            ),
            maximum_open_positions=integer_value(
                "maximum_open_positions", self.settings.max_open_positions
            ),
            maximum_gold_positions=integer_value(
                "maximum_gold_positions", self.settings.max_gold_positions
            ),
            minimum_risk_reward=decimal_value("minimum_risk_reward", self.settings.minimum_rr),
            maximum_spread=decimal_value("maximum_spread", self.settings.maximum_spread),
            maximum_tick_age=timedelta(seconds=self.settings.max_tick_age_seconds),
        )

    async def _strategy(self) -> Strategy:
        payload = await self.persistence.active_config("strategy")
        configurable = {item.name for item in fields(GoldStrategyConfig) if item.name != "weights"}
        values: dict[str, Any] = {
            "maximum_spread": float(self.settings.maximum_spread),
            "use_closed_candles_only": self.settings.use_closed_candles_only,
        }
        for name in configurable:
            if name in payload:
                values[name] = payload[name]
        version = str(payload.get("version", GoldTrendPullbackStrategy.strategy_version))
        return self._strategy_factory(GoldStrategyConfig(**values), version)

    async def scan_market(self) -> TradeSignal | None:
        _, symbol = self._require_started()
        if self.settings.news_filter_enabled:
            raise ConfigurationError(
                "NEWS_FILTER_ENABLED has no configured authoritative calendar provider"
            )
        health = await self.broker.health_check()
        if not health.healthy or not health.connected:
            raise BrokerError("market scan requires a healthy broker")
        from app.domain.enums import Timeframe

        candles = {
            timeframe: await self.broker.get_candles(
                symbol,
                timeframe,
                260,
                closed_only=self.settings.use_closed_candles_only,
            )
            for timeframe in (Timeframe.H1, Timeframe.M15, Timeframe.M5)
        }
        if any(not series for series in candles.values()):
            raise BrokerError("H1, M15, and M5 closed candles are required")
        if self._symbol_id is None:
            raise RuntimeError("worker symbol persistence was not initialized")
        await self.persistence.save_market_data(self._symbol_id, candles)
        strategy = await self._strategy()
        now = self._now()
        positions = await self.broker.get_positions(symbol)
        open_direction = positions[0].direction if len(positions) == 1 else None
        signal = strategy.evaluate(candles, as_of=now, open_direction=open_direction)
        mode = (await self._runtime_state()).mode
        _, created = await self.persistence.save_signal(
            signal,
            mode,
            symbol_id=self._symbol_id,
        )
        tick = await self.broker.get_tick(symbol)
        await self._publish(
            "PRICE",
            {
                "symbol": symbol,
                "bid": str(tick.bid),
                "ask": str(tick.ask),
                "spread": str(tick.spread),
                "timestamp": tick.timestamp.isoformat(),
            },
        )
        if created:
            await self._publish(
                "SIGNAL",
                {
                    "signal_id": str(signal.signal_id),
                    "symbol": signal.symbol,
                    "action": signal.action.value,
                    "confidence": signal.confidence_score,
                    "strategy_version": signal.strategy_version,
                },
            )
            if signal.action in {SignalAction.LONG, SignalAction.SHORT}:
                await self._enqueue_execution_hint(signal)
            if signal.action is SignalAction.EXIT and mode is TradingMode.AUTO:
                await self._execute_strategy_exit(positions)
        return signal

    async def _enqueue_execution_hint(self, signal: TradeSignal) -> None:
        """Queue a short-lived wake-up hint; PostgreSQL remains durable truth."""

        if self.redis is None:
            return
        try:
            await self.redis.rpush(EXECUTION_QUEUE, str(signal.signal_id))
        except Exception as exc:
            logger.warning(
                "execution_hint_enqueue_failed",
                extra={
                    "service": "worker",
                    "event": "EXECUTION_HINT_ENQUEUE_FAILED",
                    "error_type": type(exc).__name__,
                },
            )

    async def _drain_execution_hints(self) -> None:
        """Drain wake-up hints without using Redis as an execution ledger."""

        if self.redis is None:
            return
        try:
            await self.redis.lpop(EXECUTION_QUEUE, 100)
        except Exception as exc:
            logger.warning(
                "execution_hint_drain_failed",
                extra={
                    "service": "worker",
                    "event": "EXECUTION_HINT_DRAIN_FAILED",
                    "error_type": type(exc).__name__,
                },
            )

    async def _execute_strategy_exit(self, positions: tuple[BrokerPosition, ...]) -> None:
        broker_account_id, _ = self._require_started()
        plans = {
            plan.ticket: plan
            for plan in await self.persistence.open_position_plans(broker_account_id)
        }
        for position in positions:
            plan = plans.get(position.ticket)
            if plan is None:
                continue
            report = await self.broker.close_position(position.ticket)
            action = PositionAction(
                report.success,
                "STRATEGY_EXIT_CONFIRMED" if report.success else "STRATEGY_EXIT_REJECTED",
                report=report,
            )
            if report.success:
                await self.persistence.record_position_action(
                    plan,
                    action,
                    event_type="CLOSED",
                    state=TradeState.CLOSED,
                    broker_position=None,
                    now=self._now(),
                    event_key_suffix="strategy-exit",
                )

    @staticmethod
    def _pending_price(signal: TradeSignal, order_type: OrderType) -> Decimal | None:
        if order_type is OrderType.MARKET:
            return None
        if signal.entry_min is None or signal.entry_max is None:
            return None
        if order_type is OrderType.LIMIT:
            return (signal.entry_min + signal.entry_max) / Decimal("2")
        return signal.entry_max if signal.action is SignalAction.LONG else signal.entry_min

    async def execute_signals(self) -> tuple[ExecutionOutcome, ...]:
        broker_account_id, _ = self._require_started()
        if self._executor is None:
            raise RuntimeError("execution service was not initialized")
        now = self._now()
        await self._drain_execution_hints()
        await self.persistence.expire_signals(now)
        runtime = await self._runtime_state()
        if runtime.kill_switch or runtime.auto_disabled:
            if runtime.kill_switch:
                await self._executor.cancel_pending_entries()
            await self._publish(
                "BOT_STATUS",
                {
                    "mode": runtime.mode.value,
                    "kill_switch": runtime.kill_switch,
                    "auto_disabled": runtime.auto_disabled,
                    "new_exposure_allowed": False,
                },
            )
            return ()
        if self.settings.news_filter_enabled:
            return ()
        limits = await self._risk_limits()
        circuit = CircuitBreaker(SQLAlchemyCircuitStateStore(self.database.session_factory), limits)
        idempotency = SqlAlchemyIdempotencyStore(
            self.database.session_factory,
            self.persistence.order_id_resolver(broker_account_id),
        )
        self._executor = self._build_executor(idempotency, circuit, limits)
        usage = await self.persistence.risk_usage(broker_account_id, now)
        sessions = default_session_calendar(frozenset(self.settings.trade_sessions))
        session_allowed = sessions.is_allowed(now)
        signals = await self.persistence.execution_candidates(runtime.mode, now)
        outcomes: list[ExecutionOutcome] = []
        for signal in signals:
            demo = await self.persistence.demo_performance(signal.strategy_version)
            confirmation = (
                self.settings.live_trading_confirmation.get_secret_value()
                if self.settings.live_trading_confirmation is not None
                else None
            )
            required_confirmation = (
                self.settings.live_trading_confirmation_secret.get_secret_value() or None
            )
            outcome = await self._executor.execute(
                ExecutionCommand(
                    signal=signal,
                    mode=runtime.mode,
                    environment=self.settings.trading_env,
                    order_type=self.settings.entry_order_type,
                    requested_price=self._pending_price(signal, self.settings.entry_order_type),
                    maximum_slippage=self.settings.maximum_slippage,
                    approved=signal.status.value == "APPROVED",
                    live_trading_enabled=self.settings.live_trading_enabled,
                    configured_live_confirmation=confirmation,
                    required_live_confirmation=required_confirmation,
                    demo_performance=demo,
                    session_allowed=session_allowed,
                    risk_usage=usage,
                )
            )
            await self.persistence.record_execution(
                broker_account_id,
                signal,
                outcome,
                self.settings.trading_env.value,
                self._now(),
            )
            await self._publish(
                "EXECUTION",
                {
                    "signal_id": str(signal.signal_id),
                    "status": outcome.status.value,
                    "reasons": list(outcome.reasons),
                    "broker_ticket": (
                        outcome.report.broker_ticket if outcome.report is not None else None
                    ),
                },
            )
            outcomes.append(outcome)
        return tuple(outcomes)

    async def reconcile_unknown_executions(self) -> int:
        broker_account_id, _ = self._require_started()
        if self._executor is None:
            raise RuntimeError("execution service was not initialized")
        resolved = 0
        for key in await self.persistence.unknown_keys():
            outcome = await self._executor.reconcile_unknown(key)
            if outcome.request is not None:
                signal = await self.persistence.signal_by_id(outcome.request.signal_id)
                if signal is not None:
                    await self.persistence.record_execution(
                        broker_account_id,
                        signal,
                        outcome,
                        self.settings.trading_env.value,
                        self._now(),
                    )
            if not outcome.requires_reconciliation:
                resolved += 1
        return resolved

    async def reconcile(self) -> bool:
        self._require_started()
        if self._reconciliation is None:
            raise RuntimeError("reconciliation service was not initialized")
        report = await self._reconciliation.reconcile()
        await self.persistence.record_reconciliation(report, self._now())
        await self.reconcile_unknown_executions()
        await self._publish(
            "HEALTH",
            {
                "reconciliation_healthy": report.healthy,
                "incident_count": len(report.incidents),
            },
        )
        return report.healthy

    @staticmethod
    def _profit_distance(position: BrokerPosition) -> Decimal:
        return (
            position.current_price - position.open_price
            if position.direction is Direction.LONG
            else position.open_price - position.current_price
        )

    @staticmethod
    def _target_reached(position: BrokerPosition, target: Decimal) -> bool:
        if position.direction is Direction.LONG:
            return position.current_price >= target
        return position.current_price <= target

    async def _management_values(self) -> Mapping[str, Any]:
        return await self._risk_config()

    async def manage_positions(self) -> int:
        broker_account_id, symbol = self._require_started()
        plans = await self.persistence.open_position_plans(broker_account_id)
        if not plans:
            return 0
        broker_positions = {item.ticket: item for item in await self.broker.get_positions(symbol)}
        values = await self._management_values()
        partial_enabled = bool(
            values.get("tp1_partial_close_enabled", self.settings.tp1_partial_close_enabled)
        )
        partial_fraction = Decimal(
            str(values.get("tp1_close_fraction", self.settings.tp1_close_fraction))
        )
        break_even_enabled = bool(
            values.get("break_even_enabled", self.settings.break_even_enabled)
        )
        break_even_trigger = Decimal(
            str(values.get("break_even_trigger_r", self.settings.break_even_trigger_r))
        )
        trailing_enabled = bool(
            values.get("trailing_stop_enabled", self.settings.trailing_stop_enabled)
        )
        trailing_multiple = Decimal(
            str(values.get("trailing_atr_multiple", self.settings.trailing_atr_multiple))
        )
        position_manager = PositionManager(
            self.broker,
            trailing_enabled=trailing_enabled,
        )
        tick = await self.broker.get_tick(symbol)
        applied = 0
        for plan in plans:
            position = broker_positions.get(plan.ticket)
            if position is None:
                continue
            if (
                partial_enabled
                and len(plan.take_profits) > 1
                and "TP1_HIT" not in plan.seen_events
                and self._target_reached(position, plan.take_profits[0])
            ):
                action = await position_manager.partial_close(plan.ticket, partial_fraction)
                if action.applied:
                    refreshed = next(
                        (
                            item
                            for item in await self.broker.get_positions(symbol)
                            if item.ticket == plan.ticket
                        ),
                        None,
                    )
                    state = (
                        plan.state if plan.state is TradeState.BREAK_EVEN else TradeState.TP1_HIT
                    )
                    await self.persistence.record_position_action(
                        plan,
                        action,
                        event_type="TP1_HIT",
                        state=state,
                        broker_position=refreshed,
                        now=self._now(),
                    )
                    applied += 1
                    if refreshed is not None:
                        position = refreshed
            risk_distance = abs(plan.open_price - plan.stop_loss)
            if (
                break_even_enabled
                and risk_distance > 0
                and "BREAK_EVEN" not in plan.seen_events
                and self._profit_distance(position) >= risk_distance * break_even_trigger
            ):
                action = await position_manager.move_to_break_even(
                    plan.ticket,
                    spread_price=tick.spread,
                    slippage_price=plan.slippage,
                )
                if action.applied:
                    await self.persistence.record_position_action(
                        plan,
                        action,
                        event_type="BREAK_EVEN",
                        state=TradeState.BREAK_EVEN,
                        broker_position=action.position,
                        now=self._now(),
                    )
                    applied += 1
            if trailing_enabled:
                from app.domain.enums import Timeframe

                candles = await self.broker.get_candles(symbol, Timeframe.M5, 20, closed_only=True)
                if len(candles) >= 15:
                    atr_value = atr(
                        [item.high for item in candles],
                        [item.low for item in candles],
                        [item.close for item in candles],
                        14,
                    )[-1]
                    if atr_value is not None:
                        action = await position_manager.trail(
                            plan.ticket,
                            mode=TrailingStopMode.ATR,
                            atr=Decimal(str(atr_value)),
                            atr_multiple=trailing_multiple,
                        )
                        if action.applied:
                            suffix = (
                                str(action.position.stop_loss)
                                if action.position is not None
                                else self._now().isoformat()
                            )
                            await self.persistence.record_position_action(
                                plan,
                                action,
                                event_type="TRAILING_STOP",
                                state=None,
                                broker_position=action.position,
                                now=self._now(),
                                event_key_suffix=suffix,
                            )
                            applied += 1
            await self._publish(
                "POSITION",
                {
                    "ticket": position.ticket,
                    "symbol": position.symbol,
                    "direction": position.direction.value,
                    "volume": str(position.volume),
                    "current_price": str(position.current_price),
                    "stop_loss": str(position.stop_loss),
                    "take_profit": str(position.take_profit),
                    "unrealized_pnl": str(position.profit),
                },
            )
        return applied

    async def snapshot_account(self) -> None:
        broker_account_id, _ = self._require_started()
        account = await self.broker.get_account()
        await self.persistence.save_account_snapshot(broker_account_id, account)
        await self.persistence.record_broker_health(
            broker_account_id,
            connected=True,
            healthy=True,
            message="ok",
            now=self._now(),
        )
        await self._publish(
            "PNL",
            {
                "balance": str(account.balance),
                "equity": str(account.equity),
                "unrealized_pnl": str(account.equity - account.balance),
                "currency": account.currency,
            },
        )

    async def snapshot_risk(self) -> None:
        broker_account_id, _ = self._require_started()
        account = await self.broker.get_account()
        usage = await self.persistence.risk_usage(broker_account_id, self._now())
        limits = await self._risk_limits()
        circuit = CircuitBreaker(SQLAlchemyCircuitStateStore(self.database.session_factory), limits)
        state = await circuit.evaluate(account, usage, self._now())
        await self.persistence.save_risk_snapshot(
            broker_account_id,
            account,
            usage,
            circuit_breaker_active=state.blocks_new_trades,
            now=self._now(),
        )
        if state.manual_reenable_required:
            await self._latch_drawdown_disable(state.reason)

    async def _latch_drawdown_disable(self, reason: str) -> None:
        runtime = await self._runtime_state()
        if runtime.auto_disabled:
            return
        now = self._now()
        latched = DurableRuntimeState(
            mode=TradingMode.SIGNAL,
            kill_switch=runtime.kill_switch,
            kill_switch_reason=runtime.kill_switch_reason,
            auto_disabled=True,
            updated_at=now,
        )
        await self.persistence.activate_config(
            RUNTIME_CONFIG_TYPE,
            f"drawdown-{now.strftime('%Y%m%d%H%M%S%f')}-{uuid4().hex[:8]}",
            latched.to_payload(),
            now,
        )
        await self._publish(
            "BOT_STATUS",
            {
                "mode": TradingMode.SIGNAL.value,
                "auto_disabled": True,
                "reason": reason or "ACCOUNT_DRAWDOWN_LIMIT_REACHED",
                "new_exposure_allowed": False,
            },
        )

    async def reset_circuit_periods(self) -> None:
        _, _ = self._require_started()
        account = await self.broker.get_account()
        circuit = CircuitBreaker(
            SQLAlchemyCircuitStateStore(self.database.session_factory),
            await self._risk_limits(),
        )
        await circuit.get(account.account_id, self._now())

    async def aggregate_metrics(self, metric_date: date | None = None) -> None:
        broker_account_id, _ = self._require_started()
        account = await self.broker.get_account()
        await self.persistence.aggregate_metrics(
            broker_account_id,
            account,
            metric_date or self._now().date(),
        )

    async def dispatch_outbox(self) -> int:
        self._require_started()
        messages = await self.persistence.claim_outbox(self._now())
        delivered_count = 0
        for message in messages:
            delivered, error = await self._deliver_outbox_message(message)
            channel = "telegram" if self.settings.telegram_enabled else "null"
            recipient = self.settings.telegram_chat_id or "disabled"
            await self.persistence.complete_outbox(
                message,
                delivered=delivered,
                channel=channel,
                recipient=recipient,
                error=error,
                now=self._now(),
            )
            if delivered:
                delivered_count += 1
        return delivered_count

    async def _deliver_outbox_message(self, message: OutboxMessage) -> tuple[bool, str | None]:
        level = (
            NotificationLevel.CRITICAL
            if any(word in message.event_type for word in ("FAILED", "UNKNOWN", "ERROR"))
            else (
                NotificationLevel.WARNING
                if any(word in message.event_type for word in ("REJECTED", "BLOCKED", "LIMIT"))
                else NotificationLevel.INFO
            )
        )
        notification = Notification(
            event=message.event_type,
            title=message.event_type.replace("_", " ").title(),
            message=str(message.payload),
            level=level,
            metadata={
                "aggregate_type": message.aggregate_type,
                "aggregate_id": message.aggregate_id,
            },
            created_at=self._now(),
        )
        try:
            delivered = await self.notifier.send(notification)
        except NotificationDeliveryError as exc:
            return False, type(exc).__name__
        return delivered, None if delivered else "NOTIFIER_RETURNED_FALSE"

    async def heartbeat(self, *, status: str = "RUNNING") -> None:
        now = self._now()
        await self.persistence.heartbeat(
            instance_key=self._instance_key,
            hostname=self._hostname,
            version=__version__,
            environment=self.settings.trading_env.value,
            now=now,
            status=status,
        )
        if self.redis is not None:
            ttl = self.settings.heartbeat_interval_seconds * 3
            for component, healthy in self._component_healthy.items():
                key = heartbeat_key(component)
                if healthy and status == "RUNNING":
                    await self.redis.set(key, now.isoformat(), ex=ttl)
                else:
                    await self.redis.delete(key)
        await self._publish(
            "HEALTH",
            {
                "status": status,
                "components": dict(self._component_healthy),
                "timestamp": now.isoformat(),
            },
        )

    async def _publish(self, event: str, payload: Mapping[str, Any]) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.publish(EVENT_CHANNEL, event_message(event, payload))
        except Exception as exc:  # Redis events do not control broker protection
            logger.warning(
                "worker_event_publish_failed",
                extra={
                    "service": "worker",
                    "event": "WORKER_EVENT_PUBLISH_FAILED",
                    "error_type": type(exc).__name__,
                },
            )

    async def _coordinated(
        self,
        name: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int,
        component: str | None = None,
    ) -> None:
        result = await self._leases.run(name, operation, ttl_seconds=ttl_seconds)
        if result.status is LeaseRunStatus.FAILED:
            if component is not None:
                self._component_healthy[component] = False
            try:
                await self.persistence.record_job_failure(
                    name, result.error_type or "UNKNOWN", self._now()
                )
            except Exception:
                logger.exception(
                    "worker_job_failure_journal_failed",
                    extra={"service": "worker", "event": "WORKER_JOB_JOURNAL_FAILED"},
                )
        elif result.status is LeaseRunStatus.COMPLETED and component is not None:
            self._component_healthy[component] = True
        elif result.status is LeaseRunStatus.LOCK_UNAVAILABLE and component is not None:
            self._component_healthy[component] = False

    async def scheduled_scan(self) -> None:
        await self._coordinated(
            "market-scan",
            self.scan_market,
            ttl_seconds=240,
            component="strategy_worker",
        )

    async def scheduled_execute(self) -> None:
        await self._coordinated(
            "execution",
            self.execute_signals,
            ttl_seconds=15,
            component="execution_worker",
        )

    async def scheduled_positions(self) -> None:
        await self._coordinated(
            "position-management",
            self.manage_positions,
            ttl_seconds=20,
            component="execution_worker",
        )

    async def scheduled_reconcile(self) -> None:
        await self._coordinated("reconciliation", self.reconcile, ttl_seconds=50)

    async def scheduled_account_snapshot(self) -> None:
        await self._coordinated("account-snapshot", self.snapshot_account, ttl_seconds=50)

    async def scheduled_risk_snapshot(self) -> None:
        await self._coordinated("risk-snapshot", self.snapshot_risk, ttl_seconds=50)

    async def scheduled_metrics(self) -> None:
        await self._coordinated("metrics-aggregation", self.aggregate_metrics, ttl_seconds=240)

    async def scheduled_reset(self) -> None:
        await self._coordinated("circuit-period-reset", self.reset_circuit_periods, ttl_seconds=50)

    async def scheduled_notifications(self) -> None:
        await self._coordinated(
            "notification-outbox",
            self.dispatch_outbox,
            ttl_seconds=20,
            component="notification_worker",
        )

    async def scheduled_heartbeat(self) -> None:
        await self._coordinated("worker-heartbeat", self.heartbeat, ttl_seconds=20)

    def start_scheduler(self) -> None:
        if not self._started:
            raise RuntimeError("worker must complete startup before scheduling jobs")
        if self._scheduler is not None:
            return
        scheduler = AsyncIOScheduler(timezone="UTC")
        common = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 30}
        scheduler.add_job(
            self.scheduled_scan,
            "cron",
            id="h1_scan",
            minute=1,
            second=5,
            **common,
        )
        scheduler.add_job(
            self.scheduled_scan,
            "cron",
            id="m15_setup_update",
            minute="1,16,31,46",
            second=10,
            **common,
        )
        scheduler.add_job(
            self.scheduled_scan,
            "cron",
            id="m5_confirmation",
            minute="*/5",
            second=15,
            **common,
        )
        scheduler.add_job(self.scheduled_execute, "interval", id="execution", seconds=2, **common)
        scheduler.add_job(
            self.scheduled_positions,
            "interval",
            id="position_management",
            seconds=5,
            **common,
        )
        scheduler.add_job(
            self.scheduled_notifications,
            "interval",
            id="notification_outbox",
            seconds=5,
            **common,
        )
        scheduler.add_job(
            self.scheduled_reconcile,
            "interval",
            id="broker_reconciliation",
            seconds=60,
            **common,
        )
        scheduler.add_job(
            self.scheduled_account_snapshot,
            "interval",
            id="account_snapshot",
            seconds=60,
            **common,
        )
        scheduler.add_job(
            self.scheduled_risk_snapshot,
            "interval",
            id="risk_snapshot",
            seconds=60,
            **common,
        )
        scheduler.add_job(
            self.scheduled_heartbeat,
            "interval",
            id="heartbeat",
            seconds=self.settings.heartbeat_interval_seconds,
            **common,
        )
        scheduler.add_job(
            self.scheduled_reset,
            "cron",
            id="daily_reset",
            hour=0,
            minute=0,
            second=5,
            **common,
        )
        scheduler.add_job(
            self.scheduled_metrics,
            "cron",
            id="metrics_aggregation",
            hour=0,
            minute=5,
            **common,
        )
        scheduler.start()
        self._scheduler = scheduler

    async def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        if self._started:
            for component in WORKER_COMPONENTS:
                self._component_healthy[component] = False
            try:
                await self.heartbeat(status="STOPPED")
            finally:
                await self.broker.disconnect()
            self._started = False


__all__ = ["GoldFlowWorker", "WorkerRedis"]
