"""Thread-offloaded SQLAlchemy adapters and durable worker journal operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AccountSnapshot as AccountSnapshotRow,
)
from app.db.models import (
    AuditLog,
    BotInstance,
    BrokerAccount,
    BrokerConnection,
    ConfigVersion,
    DailyMetric,
    ExecutionAttempt,
    MarketSnapshot,
    Order,
    OutboxEvent,
    Position,
    RiskSnapshot,
    Signal,
    SignalCondition,
    Symbol,
    SystemEvent,
    Trade,
    TradeEvent,
)
from app.db.models import (
    Notification as NotificationRow,
)
from app.domain.enums import (
    AccountType,
    Direction,
    OrderStatus,
    OrderType,
    SignalAction,
    SignalStatus,
    TradeState,
    TradingMode,
)
from app.domain.models import (
    AccountSnapshot,
    BrokerOrder,
    BrokerPosition,
    Candle,
    ExecutionReport,
    OrderRequest,
    SymbolSpecification,
    TradeSignal,
)
from app.execution.idempotency import IdempotencyStatus
from app.execution.models import ExecutionOutcome, ExecutionStatus
from app.execution.positions import PositionAction
from app.execution.reconciliation import (
    LedgerOrder,
    LedgerPosition,
    LedgerStatus,
    ReconciliationReport,
)
from app.risk.circuit_breaker import CircuitState
from app.risk.demo_validation import DemoPerformance
from app.risk.models import RiskUsage

SessionFactory = sessionmaker[Session]
OrderIdResolver = Callable[[Session, OrderRequest], str]


@dataclass(frozen=True, slots=True)
class ManagedPosition:
    position_id: str
    trade_id: str
    ticket: str
    direction: Direction
    state: TradeState
    initial_volume: Decimal
    current_volume: Decimal
    open_price: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    slippage: Decimal
    seen_events: frozenset[str]


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    attempt_count: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("worker persistence timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _datetime(value: object | None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value)))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (Decimal, UUID, Enum)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


class SQLAlchemyCircuitStateStore:
    """Append-only circuit state using immutable ``ConfigVersion`` rows."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def load(self, account_id: str) -> CircuitState | None:
        return await asyncio.to_thread(self._load_sync, account_id)

    def _load_sync(self, account_id: str) -> CircuitState | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ConfigVersion)
                .where(ConfigVersion.config_type == self._config_type(account_id))
                .order_by(ConfigVersion.version.desc())
                .limit(1)
            )
            return self._from_row(account_id, row) if row is not None else None

    async def save(self, state: CircuitState, *, expected_version: int) -> CircuitState:
        return await asyncio.to_thread(self._save_sync, state, expected_version)

    def _save_sync(self, state: CircuitState, expected_version: int) -> CircuitState:
        current = self._load_sync(state.account_id)
        current_version = current.version if current is not None else 0
        if current_version != expected_version:
            raise RuntimeError("circuit state optimistic-lock conflict")
        persisted = replace(state, version=expected_version + 1)
        payload = self._payload(persisted)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        row = ConfigVersion(
            config_type=self._config_type(state.account_id),
            version=f"{persisted.version:020d}",
            checksum=hashlib.sha256(canonical.encode()).hexdigest(),
            payload=payload,
            is_active=False,
            created_at=state.updated_at,
        )
        session = self._session_factory()
        try:
            session.add(row)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise RuntimeError("circuit state optimistic-lock conflict") from exc
        finally:
            session.close()
        return persisted

    @staticmethod
    def _config_type(account_id: str) -> str:
        digest = hashlib.sha256(account_id.encode()).hexdigest()[:32]
        return f"circuit_state:{digest}"

    @staticmethod
    def _payload(state: CircuitState) -> dict[str, Any]:
        return {
            "account_id": state.account_id,
            "day": state.day.isoformat(),
            "week_start": state.week_start.isoformat(),
            "daily_locked": state.daily_locked,
            "weekly_locked": state.weekly_locked,
            "account_locked": state.account_locked,
            "kill_switch": state.kill_switch,
            "manual_reenable_required": state.manual_reenable_required,
            "reason": state.reason,
            "version": state.version,
            "updated_at": state.updated_at.isoformat(),
        }

    @staticmethod
    def _from_row(account_id: str, row: ConfigVersion) -> CircuitState:
        payload = row.payload
        return CircuitState(
            account_id=account_id,
            day=date.fromisoformat(str(payload["day"])),
            week_start=date.fromisoformat(str(payload["week_start"])),
            daily_locked=bool(payload.get("daily_locked", False)),
            weekly_locked=bool(payload.get("weekly_locked", False)),
            account_locked=bool(payload.get("account_locked", False)),
            kill_switch=bool(payload.get("kill_switch", False)),
            manual_reenable_required=bool(payload.get("manual_reenable_required", False)),
            reason=str(payload.get("reason", "")),
            version=int(row.version),
            updated_at=_datetime(payload["updated_at"]) or row.created_at,
        )


class SQLAlchemyRecoveryLedger:
    """Database ledger used by broker-authoritative reconciliation and recovery."""

    def __init__(self, session_factory: SessionFactory, broker_account_id: str) -> None:
        self._session_factory = session_factory
        self._broker_account_id = broker_account_id

    async def positions(self) -> tuple[LedgerPosition, ...]:
        return await asyncio.to_thread(self._positions_sync)

    def _positions_sync(self) -> tuple[LedgerPosition, ...]:
        with self._session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(Position).where(Position.broker_account_id == self._broker_account_id)
                )
            )
            result: list[LedgerPosition] = []
            for row in rows:
                trade = session.scalar(select(Trade).where(Trade.position_id == row.id))
                result.append(
                    LedgerPosition(
                        self._broker_position(row, trade),
                        LedgerStatus.CLOSED if row.closed_at is not None else LedgerStatus.OPEN,
                        False,
                        row.updated_at,
                    )
                )
            return tuple(result)

    async def orders(self) -> tuple[LedgerOrder, ...]:
        return await asyncio.to_thread(self._orders_sync)

    def _orders_sync(self) -> tuple[LedgerOrder, ...]:
        with self._session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(Order).where(
                        Order.broker_account_id == self._broker_account_id,
                        Order.broker_ticket.is_not(None),
                    )
                )
            )
            return tuple(
                LedgerOrder(
                    self._broker_order(row),
                    (
                        LedgerStatus.CANCELLED
                        if row.status in {OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value}
                        else LedgerStatus.PENDING
                    ),
                    False,
                    row.updated_at,
                )
                for row in rows
            )

    async def upsert_position(self, record: LedgerPosition) -> None:
        await asyncio.to_thread(self._upsert_position_sync, record)

    def _upsert_position_sync(self, record: LedgerPosition) -> None:
        item = record.position
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(Position).where(
                    Position.broker_account_id == self._broker_account_id,
                    Position.broker_ticket == item.ticket,
                )
            )
            signal_id = self._ensure_recovery_signal(
                session, item.symbol, item.direction, item.signal_id
            )
            if row is None:
                row = Position(
                    broker_account_id=self._broker_account_id,
                    signal_id=signal_id,
                    broker_ticket=item.ticket,
                    symbol=item.symbol,
                    direction=item.direction.value,
                    initial_volume=item.volume,
                    current_volume=item.volume,
                    open_price=item.open_price,
                    current_price=item.current_price,
                    stop_loss=item.stop_loss,
                    take_profits=([str(item.take_profit)] if item.take_profit is not None else []),
                    realized_pnl=Decimal("0"),
                    unrealized_pnl=item.profit,
                    state=TradeState.POSITION_OPEN.value,
                    opened_at=item.opened_at,
                )
                session.add(row)
                session.flush()
                self._ensure_recovery_trade(session, row, item, signal_id)
            else:
                row.current_volume = item.volume
                row.current_price = item.current_price
                row.stop_loss = item.stop_loss
                row.take_profits = [str(item.take_profit)] if item.take_profit is not None else []
                row.unrealized_pnl = item.profit
                trade = session.scalar(select(Trade).where(Trade.position_id == row.id))
                if record.status is LedgerStatus.CLOSED:
                    row.state = TradeState.CLOSED.value
                    row.current_volume = Decimal("0")
                    row.closed_at = record.updated_at
                    if trade is not None:
                        trade.state = TradeState.CLOSED.value
                        trade.exit_price = item.current_price
                        trade.realized_pnl = item.profit
                        trade.net_pnl = item.profit
                        trade.closed_at = record.updated_at
                        trade.exit_reason = "BROKER_RECONCILIATION"
                else:
                    row.state = TradeState.POSITION_OPEN.value
                    row.closed_at = None

    async def upsert_order(self, record: LedgerOrder) -> None:
        await asyncio.to_thread(self._upsert_order_sync, record)

    def _upsert_order_sync(self, record: LedgerOrder) -> None:
        item = record.order
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(Order).where(
                    Order.broker_account_id == self._broker_account_id,
                    Order.broker_ticket == item.ticket,
                )
            )
            if row is None:
                signal_id = self._ensure_recovery_signal(session, item.symbol, item.direction, None)
                key = item.idempotency_key or (f"recovered:{self._broker_account_id}:{item.ticket}")
                row = Order(
                    signal_id=signal_id,
                    broker_account_id=self._broker_account_id,
                    idempotency_key=key,
                    broker_ticket=item.ticket,
                    symbol=item.symbol,
                    direction=item.direction.value,
                    order_type=item.order_type.value,
                    status=item.status.value,
                    volume=item.volume,
                    requested_price=item.price,
                    stop_loss=item.stop_loss,
                    take_profits=([str(item.take_profit)] if item.take_profit is not None else []),
                    submitted_at=item.created_at,
                )
                session.add(row)
            else:
                row.status = (
                    OrderStatus.CANCELLED.value
                    if record.status is LedgerStatus.CANCELLED
                    else item.status.value
                )
                if record.status is LedgerStatus.CANCELLED:
                    row.cancelled_at = record.updated_at

    def _ensure_recovery_trade(
        self,
        session: Session,
        position: Position,
        item: BrokerPosition,
        signal_id: str,
    ) -> None:
        if session.scalar(select(Trade).where(Trade.position_id == position.id)) is not None:
            return
        account = session.get(BrokerAccount, self._broker_account_id)
        environment = (
            "demo"
            if account is not None and account.account_type == AccountType.DEMO.value
            else "production"
        )
        risk_amount = abs(item.open_price - item.stop_loss) * item.volume
        risk_reward = Decimal("0")
        if item.take_profit is not None and item.open_price != item.stop_loss:
            risk_reward = abs(item.take_profit - item.open_price) / abs(
                item.open_price - item.stop_loss
            )
        session.add(
            Trade(
                position_id=position.id,
                signal_id=signal_id,
                broker_account_id=self._broker_account_id,
                strategy_id=item.strategy_id or "broker_recovery",
                strategy_version="recovered",
                broker_ticket=item.ticket,
                symbol=item.symbol,
                canonical_symbol="XAUUSD",
                direction=item.direction.value,
                state=TradeState.POSITION_OPEN.value,
                environment=environment,
                volume=item.volume,
                entry_price=item.open_price,
                stop_loss=item.stop_loss,
                initial_stop_loss=item.stop_loss,
                take_profit_1=item.take_profit,
                risk_amount=risk_amount,
                risk_percentage=Decimal("0"),
                risk_reward=risk_reward,
                opened_at=item.opened_at,
            )
        )

    @staticmethod
    def _broker_position(row: Position, trade: Trade | None) -> BrokerPosition:
        signal_id: UUID | None = None
        if row.signal_id:
            try:
                signal_id = UUID(row.signal_id)
            except ValueError:
                signal_id = None
        targets = row.take_profits or []
        return BrokerPosition(
            ticket=row.broker_ticket,
            symbol=row.symbol,
            direction=Direction(row.direction),
            volume=row.current_volume,
            open_price=row.open_price,
            current_price=row.current_price,
            stop_loss=row.stop_loss,
            take_profit=Decimal(str(targets[0])) if targets else None,
            profit=row.unrealized_pnl,
            opened_at=row.opened_at,
            strategy_id=trade.strategy_id if trade is not None else "",
            signal_id=signal_id,
        )

    @staticmethod
    def _broker_order(row: Order) -> BrokerOrder:
        targets = row.take_profits or []
        return BrokerOrder(
            ticket=row.broker_ticket or "",
            symbol=row.symbol,
            direction=Direction(row.direction),
            order_type=OrderType(row.order_type),
            volume=row.volume,
            price=row.requested_price or row.executed_price or Decimal("0"),
            stop_loss=row.stop_loss,
            take_profit=Decimal(str(targets[0])) if targets else None,
            status=OrderStatus(row.status),
            created_at=row.submitted_at or row.created_at,
            idempotency_key=row.idempotency_key,
        )

    @staticmethod
    def _ensure_recovery_signal(
        session: Session,
        symbol: str,
        direction: Direction,
        signal_id: UUID | None,
    ) -> str:
        identifier = str(
            signal_id or uuid5(NAMESPACE_URL, f"goldflow:recovery:{symbol}:{direction.value}")
        )
        if session.get(Signal, identifier) is None:
            session.add(
                Signal(
                    signal_id=identifier,
                    strategy_id="broker_recovery",
                    strategy_version="recovered",
                    symbol=symbol,
                    canonical_symbol="XAUUSD",
                    action=direction.value,
                    direction=direction.value,
                    confidence_score=0,
                    status=SignalStatus.EXECUTED.value,
                    rationale={"reason": "BROKER_STATE_RECOVERED"},
                )
            )
            session.flush()
        return identifier


class WorkerPersistence:
    """Durable journal facade; every public operation uses a fresh DB session."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    async def ensure_broker_account(self, account: AccountSnapshot) -> str:
        return await asyncio.to_thread(self._ensure_broker_account_sync, account)

    def _ensure_broker_account_sync(self, account: AccountSnapshot) -> str:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(BrokerAccount).where(
                    BrokerAccount.broker == account.broker,
                    BrokerAccount.platform == account.platform,
                    BrokerAccount.server == account.server,
                    BrokerAccount.external_account_id == account.account_id,
                )
            )
            if row is None:
                row = BrokerAccount(
                    broker=account.broker,
                    platform=account.platform,
                    external_account_id=account.account_id,
                    account_type=account.account_type.value,
                    server=account.server,
                    currency=account.currency,
                    metadata_json={"last_broker_timestamp": account.timestamp.isoformat()},
                )
                session.add(row)
                session.flush()
            else:
                row.account_type = account.account_type.value
                row.currency = account.currency
                row.metadata_json = {
                    **row.metadata_json,
                    "last_broker_timestamp": account.timestamp.isoformat(),
                }
            return row.id

    async def ensure_symbol(
        self, broker_account_id: str, specification: SymbolSpecification
    ) -> str:
        return await asyncio.to_thread(self._ensure_symbol_sync, broker_account_id, specification)

    def _ensure_symbol_sync(
        self, broker_account_id: str, specification: SymbolSpecification
    ) -> str:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(Symbol).where(
                    Symbol.broker_account_id == broker_account_id,
                    Symbol.broker_symbol == specification.name,
                )
            )
            values = {
                "canonical_symbol": specification.canonical_symbol,
                "base_currency": specification.base_currency,
                "quote_currency": specification.quote_currency,
                "description": specification.description,
                "digits": specification.digits,
                "point": specification.point,
                "tick_size": specification.tick_size,
                "tick_value": specification.tick_value,
                "contract_size": specification.contract_size,
                "volume_min": specification.volume_min,
                "volume_max": specification.volume_max,
                "volume_step": specification.volume_step,
                "stops_level_points": specification.stops_level_points,
                "is_active": specification.trade_enabled,
            }
            if row is None:
                row = Symbol(
                    broker_account_id=broker_account_id,
                    broker_symbol=specification.name,
                    metadata_json={},
                    **values,
                )
                session.add(row)
                session.flush()
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            return row.id

    async def save_market_data(
        self, symbol_id: str, candles: Mapping[Any, Sequence[Candle]]
    ) -> int:
        return await asyncio.to_thread(self._save_market_data_sync, symbol_id, candles)

    def _save_market_data_sync(
        self, symbol_id: str, candles: Mapping[Any, Sequence[Candle]]
    ) -> int:
        inserted = 0
        with self.session_factory.begin() as session:
            for series in candles.values():
                for candle in series:
                    existing = session.scalar(
                        select(MarketSnapshot.id).where(
                            MarketSnapshot.symbol == candle.symbol,
                            MarketSnapshot.timeframe == candle.timeframe.value,
                            MarketSnapshot.candle_time == candle.timestamp,
                        )
                    )
                    if existing is not None:
                        continue
                    session.add(
                        MarketSnapshot(
                            symbol_id=symbol_id,
                            symbol=candle.symbol,
                            timeframe=candle.timeframe.value,
                            candle_time=candle.timestamp,
                            open=Decimal(str(candle.open)),
                            high=Decimal(str(candle.high)),
                            low=Decimal(str(candle.low)),
                            close=Decimal(str(candle.close)),
                            volume=Decimal(str(candle.volume)),
                            spread=Decimal(str(candle.spread)),
                            is_closed=candle.complete,
                            payload={},
                        )
                    )
                    inserted += 1
        return inserted

    async def save_signal(
        self,
        signal: TradeSignal,
        mode: TradingMode,
        *,
        symbol_id: str | None = None,
    ) -> tuple[str, bool]:
        return await asyncio.to_thread(self._save_signal_sync, signal, mode, symbol_id)

    def _save_signal_sync(
        self, signal: TradeSignal, mode: TradingMode, symbol_id: str | None
    ) -> tuple[str, bool]:
        identifier = str(signal.signal_id)
        with self.session_factory.begin() as session:
            if session.get(Signal, identifier) is not None:
                return identifier, False
            status = signal.status
            if mode is TradingMode.SEMI_AUTO and signal.action in {
                SignalAction.LONG,
                SignalAction.SHORT,
            }:
                status = SignalStatus.APPROVAL_REQUIRED
            row = Signal(
                signal_id=identifier,
                symbol_id=symbol_id,
                strategy_id=signal.strategy_id,
                strategy_version=signal.strategy_version,
                symbol=signal.symbol,
                canonical_symbol=signal.canonical_symbol,
                action=signal.action.value,
                direction=signal.direction.value if signal.direction is not None else None,
                entry_min=signal.entry_min,
                entry_max=signal.entry_max,
                stop_loss=signal.stop_loss,
                take_profits=[str(value) for value in signal.take_profits],
                risk_reward=signal.risk_reward,
                confidence_score=signal.confidence_score,
                status=status.value,
                rationale=_json_safe(signal.rationale),
                created_at=signal.created_at,
                updated_at=signal.created_at,
                expires_at=signal.expires_at,
            )
            session.add(row)
            session.flush()
            conditions = signal.rationale.get("conditions", {})
            if isinstance(conditions, Mapping):
                for name, observed in conditions.items():
                    passed = bool(
                        observed.get("passed", False) if isinstance(observed, Mapping) else observed
                    )
                    session.add(
                        SignalCondition(
                            signal_id=identifier,
                            condition_name=str(name)[:128],
                            passed=passed,
                            score=Decimal("1") if passed else Decimal("0"),
                            observed_value=_json_safe(observed),
                            details={},
                            evaluated_at=signal.created_at,
                        )
                    )
            self._journal(
                session,
                event_type="SIGNAL_PERSISTED",
                resource_type="signal",
                resource_id=identifier,
                payload={"action": signal.action.value, "status": status.value},
                deduplication_key=f"signal:{identifier}:created",
                now=signal.created_at,
            )
            return identifier, True

    async def execution_candidates(
        self, mode: TradingMode, now: datetime, *, limit: int = 50
    ) -> tuple[TradeSignal, ...]:
        return await asyncio.to_thread(self._execution_candidates_sync, mode, _utc(now), limit)

    def _execution_candidates_sync(
        self, mode: TradingMode, now: datetime, limit: int
    ) -> tuple[TradeSignal, ...]:
        if mode is TradingMode.SIGNAL:
            return ()
        required_status = (
            SignalStatus.ACTIVE.value if mode is TradingMode.AUTO else SignalStatus.APPROVED.value
        )
        with self.session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(Signal)
                    .where(
                        Signal.status == required_status,
                        Signal.action.in_([SignalAction.LONG.value, SignalAction.SHORT.value]),
                        (Signal.expires_at.is_(None) | (Signal.expires_at > now)),
                    )
                    .order_by(Signal.created_at)
                    .limit(limit)
                )
            )
            return tuple(self._signal_from_row(row) for row in rows)

    async def expire_signals(self, now: datetime) -> int:
        return await asyncio.to_thread(self._expire_signals_sync, _utc(now))

    def _expire_signals_sync(self, now: datetime) -> int:
        with self.session_factory.begin() as session:
            rows = tuple(
                session.scalars(
                    select(Signal).where(
                        Signal.status.in_(
                            [
                                SignalStatus.ACTIVE.value,
                                SignalStatus.APPROVAL_REQUIRED.value,
                                SignalStatus.APPROVED.value,
                            ]
                        ),
                        Signal.expires_at.is_not(None),
                        Signal.expires_at <= now,
                    )
                )
            )
            for row in rows:
                row.status = SignalStatus.EXPIRED.value
                row.updated_at = now
                self._journal(
                    session,
                    event_type="SIGNAL_EXPIRED",
                    resource_type="signal",
                    resource_id=row.signal_id,
                    payload={"signal_id": row.signal_id},
                    deduplication_key=f"signal:{row.signal_id}:expired",
                    now=now,
                )
            return len(rows)

    async def signal_by_id(self, signal_id: UUID | str) -> TradeSignal | None:
        return await asyncio.to_thread(self._signal_by_id_sync, str(signal_id))

    def _signal_by_id_sync(self, signal_id: str) -> TradeSignal | None:
        with self.session_factory() as session:
            row = session.get(Signal, signal_id)
            return self._signal_from_row(row) if row is not None else None

    @staticmethod
    def _signal_from_row(row: Signal) -> TradeSignal:
        return TradeSignal(
            symbol=row.symbol,
            canonical_symbol=row.canonical_symbol,
            action=SignalAction(row.action),
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
            confidence_score=row.confidence_score,
            entry_min=row.entry_min,
            entry_max=row.entry_max,
            stop_loss=row.stop_loss,
            take_profits=tuple(Decimal(str(value)) for value in row.take_profits),
            risk_reward=row.risk_reward,
            rationale=dict(row.rationale),
            status=SignalStatus(row.status),
            created_at=row.created_at,
            expires_at=row.expires_at,
            signal_id=UUID(row.signal_id),
        )

    def order_id_resolver(self, broker_account_id: str) -> OrderIdResolver:
        def resolve(session: Session, request: OrderRequest) -> str:
            existing = session.scalar(
                select(Order).where(Order.idempotency_key == request.idempotency_key)
            )
            if existing is not None:
                return existing.id
            if session.get(Signal, str(request.signal_id)) is None:
                raise RuntimeError("execution claim requires a persisted signal")
            row = Order(
                signal_id=str(request.signal_id),
                broker_account_id=broker_account_id,
                idempotency_key=request.idempotency_key,
                symbol=request.symbol,
                direction=request.direction.value,
                order_type=request.order_type.value,
                status=OrderStatus.PENDING.value,
                volume=request.volume,
                requested_price=request.requested_price,
                stop_loss=request.stop_loss,
                take_profits=[str(value) for value in request.take_profits],
                maximum_slippage=request.maximum_slippage,
                expires_at=request.expires_at,
            )
            session.add(row)
            session.flush()
            return row.id

        return resolve

    async def active_config(self, config_type: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._active_config_sync, config_type)

    def _active_config_sync(self, config_type: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.scalar(
                select(ConfigVersion)
                .where(
                    ConfigVersion.config_type == config_type,
                    ConfigVersion.is_active.is_(True),
                )
                .order_by(ConfigVersion.activated_at.desc(), ConfigVersion.created_at.desc())
                .limit(1)
            )
            return dict(row.payload) if row is not None else {}

    async def activate_config(
        self,
        config_type: str,
        version: str,
        payload: Mapping[str, Any],
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._activate_config_sync,
            config_type,
            version,
            payload,
            _utc(now),
        )

    def _activate_config_sync(
        self,
        config_type: str,
        version: str,
        payload: Mapping[str, Any],
        now: datetime,
    ) -> None:
        normalized = _json_safe(payload)
        if not isinstance(normalized, dict):
            raise ValueError("configuration payload must be an object")
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        with self.session_factory.begin() as session:
            for row in session.scalars(
                select(ConfigVersion).where(
                    ConfigVersion.config_type == config_type,
                    ConfigVersion.is_active.is_(True),
                )
            ):
                row.is_active = False
                row.activated_at = None
            session.add(
                ConfigVersion(
                    config_type=config_type,
                    version=version,
                    checksum=hashlib.sha256(canonical.encode()).hexdigest(),
                    payload=normalized,
                    is_active=True,
                    activated_at=now,
                )
            )

    async def risk_usage(self, broker_account_id: str, now: datetime) -> RiskUsage:
        return await asyncio.to_thread(self._risk_usage_sync, broker_account_id, _utc(now))

    def _risk_usage_sync(self, broker_account_id: str, now: datetime) -> RiskUsage:
        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        week_start_day = now.date() - timedelta(days=now.weekday())
        week_start = datetime.combine(week_start_day, time.min, tzinfo=UTC)
        with self.session_factory() as session:
            rows = tuple(
                session.scalars(select(Trade).where(Trade.broker_account_id == broker_account_id))
            )
            daily_loss = sum(
                (
                    max(-row.realized_pnl, Decimal("0"))
                    for row in rows
                    if row.closed_at is not None and row.closed_at >= day_start
                ),
                Decimal("0"),
            )
            weekly_loss = sum(
                (
                    max(-row.realized_pnl, Decimal("0"))
                    for row in rows
                    if row.closed_at is not None and row.closed_at >= week_start
                ),
                Decimal("0"),
            )
            open_risk = sum(
                (row.risk_amount for row in rows if row.closed_at is None),
                Decimal("0"),
            )
            snapshots = tuple(
                session.scalars(
                    select(AccountSnapshotRow).where(
                        AccountSnapshotRow.broker_account_id == broker_account_id
                    )
                )
            )
            peak = max((row.equity for row in snapshots), default=None)
        return RiskUsage(daily_loss, weekly_loss, open_risk, peak)

    async def demo_performance(self, strategy_version: str) -> DemoPerformance | None:
        return await asyncio.to_thread(self._demo_performance_sync, strategy_version)

    def _demo_performance_sync(self, strategy_version: str) -> DemoPerformance | None:
        with self.session_factory() as session:
            trades = tuple(
                session.scalars(
                    select(Trade).where(
                        Trade.environment == "demo",
                        Trade.strategy_version == strategy_version,
                        Trade.closed_at.is_not(None),
                    )
                )
            )
            if not trades:
                return None
            demo_accounts = select(BrokerAccount.id).where(
                BrokerAccount.account_type == AccountType.DEMO.value
            )
            metrics = tuple(
                session.scalars(
                    select(DailyMetric).where(DailyMetric.broker_account_id.in_(demo_accounts))
                )
            )
            maximum_drawdown = max((row.max_drawdown for row in metrics), default=Decimal("0"))
            profit = sum((max(row.realized_pnl, Decimal("0")) for row in trades), Decimal("0"))
            loss = sum((max(-row.realized_pnl, Decimal("0")) for row in trades), Decimal("0"))
            metric_pf = max(
                (row.profit_factor for row in metrics if row.profit_factor is not None),
                default=None,
            )
            if loss > 0:
                profit_factor = profit / loss
            elif profit > 0:
                profit_factor = Decimal("999999")
            else:
                profit_factor = metric_pf or Decimal("0")
            return DemoPerformance(
                strategy_version,
                len(trades),
                maximum_drawdown,
                profit_factor,
            )

    async def record_execution(
        self,
        broker_account_id: str,
        signal: TradeSignal,
        outcome: ExecutionOutcome,
        environment: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._record_execution_sync,
            broker_account_id,
            signal,
            outcome,
            environment,
            _utc(now),
        )

    def _record_execution_sync(
        self,
        broker_account_id: str,
        signal: TradeSignal,
        outcome: ExecutionOutcome,
        environment: str,
        now: datetime,
    ) -> None:
        request = outcome.request
        with self.session_factory.begin() as session:
            signal_row = session.get(Signal, str(signal.signal_id))
            if signal_row is not None:
                if outcome.status in {ExecutionStatus.FILLED, ExecutionStatus.PENDING}:
                    signal_row.status = SignalStatus.EXECUTED.value
                elif outcome.status in {
                    ExecutionStatus.REJECTED,
                    ExecutionStatus.CHECK_REJECTED,
                }:
                    signal_row.status = SignalStatus.REJECTED.value
                elif outcome.status in {
                    ExecutionStatus.UNKNOWN,
                    ExecutionStatus.PROTECTION_FAILED,
                }:
                    signal_row.status = SignalStatus.ERROR.value
                signal_row.rationale = {
                    **signal_row.rationale,
                    "last_execution_status": outcome.status.value,
                    "last_execution_reasons": list(outcome.reasons),
                    "last_execution_at": now.isoformat(),
                }

            order: Order | None = None
            if request is not None:
                order = session.scalar(
                    select(Order).where(Order.idempotency_key == request.idempotency_key)
                )
            if order is not None:
                report = outcome.report
                if report is not None:
                    order.broker_ticket = report.broker_ticket
                    order.executed_price = report.executed_price
                    order.actual_slippage = report.slippage
                    order.broker_code = report.broker_code
                    order.broker_message = report.message
                    order.submitted_at = report.submitted_at
                if outcome.status is ExecutionStatus.FILLED:
                    order.status = OrderStatus.FILLED.value
                    order.filled_at = now
                elif outcome.status is ExecutionStatus.PENDING:
                    order.status = OrderStatus.PENDING.value
                elif outcome.status in {
                    ExecutionStatus.REJECTED,
                    ExecutionStatus.CHECK_REJECTED,
                }:
                    order.status = OrderStatus.REJECTED.value
                    order.cancelled_at = now
                    order.broker_message = ", ".join(outcome.reasons)
                elif outcome.status is ExecutionStatus.PROTECTION_FAILED:
                    # The entry mutation succeeded; the post-fill protection path
                    # failed or safely closed it. Never misclassify the fill itself
                    # as a broker rejection.
                    order.status = OrderStatus.FILLED.value
                    order.filled_at = now
                    order.broker_message = ", ".join(outcome.reasons)
                elif outcome.status is ExecutionStatus.UNKNOWN:
                    order.broker_message = "ORDER_RESULT_UNKNOWN_RECONCILIATION_REQUIRED"

            position: Position | None = None
            trade: Trade | None = None
            filled_statuses = {
                ExecutionStatus.FILLED,
                ExecutionStatus.PROTECTION_FAILED,
            }
            if (
                outcome.status in filled_statuses
                and request is not None
                and outcome.report is not None
                and outcome.report.broker_ticket
            ):
                safely_closed = "UNPROTECTED_FILL_SAFELY_CLOSED" in outcome.reasons
                position = session.scalar(
                    select(Position).where(
                        Position.broker_account_id == broker_account_id,
                        Position.broker_ticket == outcome.report.broker_ticket,
                    )
                )
                entry_price = (
                    outcome.report.executed_price
                    or request.requested_price
                    or signal.entry_min
                    or Decimal("0")
                )
                if position is None:
                    position = Position(
                        broker_account_id=broker_account_id,
                        signal_id=str(signal.signal_id),
                        opening_order_id=order.id if order is not None else None,
                        broker_ticket=outcome.report.broker_ticket,
                        symbol=request.symbol,
                        direction=request.direction.value,
                        initial_volume=request.volume,
                        current_volume=(Decimal("0") if safely_closed else request.volume),
                        open_price=entry_price,
                        current_price=entry_price,
                        stop_loss=request.stop_loss,
                        take_profits=[str(value) for value in request.take_profits],
                        realized_pnl=Decimal("0"),
                        unrealized_pnl=Decimal("0"),
                        state=(
                            TradeState.CLOSED.value
                            if safely_closed
                            else (
                                TradeState.ERROR.value
                                if outcome.status is ExecutionStatus.PROTECTION_FAILED
                                else TradeState.POSITION_OPEN.value
                            )
                        ),
                        opened_at=outcome.report.submitted_at,
                        closed_at=now if safely_closed else None,
                    )
                    session.add(position)
                    session.flush()
                trade = session.scalar(select(Trade).where(Trade.position_id == position.id))
                if trade is None:
                    targets: list[Decimal | None] = list(request.take_profits)
                    targets.extend([None] * (3 - len(targets)))
                    risk = outcome.risk
                    trade = Trade(
                        position_id=position.id,
                        signal_id=str(signal.signal_id),
                        broker_account_id=broker_account_id,
                        strategy_id=signal.strategy_id,
                        strategy_version=signal.strategy_version,
                        broker_ticket=outcome.report.broker_ticket,
                        symbol=request.symbol,
                        canonical_symbol=signal.canonical_symbol,
                        direction=request.direction.value,
                        state=position.state,
                        environment=environment,
                        volume=request.volume,
                        entry_price=entry_price,
                        stop_loss=request.stop_loss,
                        initial_stop_loss=request.stop_loss,
                        take_profit_1=targets[0],
                        take_profit_2=targets[1],
                        take_profit_3=targets[2],
                        risk_amount=risk.risk_amount if risk is not None else Decimal("0"),
                        risk_percentage=(risk.risk_fraction if risk is not None else Decimal("0")),
                        risk_reward=(
                            risk.risk_reward
                            if risk is not None and risk.risk_reward is not None
                            else signal.risk_reward or Decimal("0")
                        ),
                        spread_cost_estimate=Decimal("0"),
                        slippage=outcome.report.slippage or Decimal("0"),
                        opened_at=outcome.report.submitted_at,
                        closed_at=now if safely_closed else None,
                        exit_reason=(
                            "UNPROTECTED_FILL_SAFELY_CLOSED"
                            if safely_closed
                            else (
                                "PROTECTION_FAILED"
                                if outcome.status is ExecutionStatus.PROTECTION_FAILED
                                else None
                            )
                        ),
                    )
                    session.add(trade)
                    session.flush()

            for index, transition in enumerate(outcome.transitions):
                raw_key = (
                    f"{request.idempotency_key if request else signal.signal_id}:"
                    f"{index}:{transition.current.value}"
                )
                event_key = "exec:" + hashlib.sha256(raw_key.encode()).hexdigest()
                if (
                    session.scalar(select(TradeEvent.id).where(TradeEvent.event_key == event_key))
                    is None
                ):
                    session.add(
                        TradeEvent(
                            trade_id=trade.id if trade is not None else None,
                            position_id=position.id if position is not None else None,
                            event_key=event_key,
                            event_type="EXECUTION_TRANSITION",
                            previous_state=transition.previous.value,
                            current_state=transition.current.value,
                            reason=transition.reason,
                            payload={"signal_id": str(signal.signal_id)},
                            occurred_at=transition.occurred_at,
                        )
                    )

            aggregate_id = request.idempotency_key if request else str(signal.signal_id)
            digest = hashlib.sha256(aggregate_id.encode()).hexdigest()
            failure_statuses = {
                ExecutionStatus.BLOCKED,
                ExecutionStatus.REJECTED,
                ExecutionStatus.CHECK_REJECTED,
                ExecutionStatus.PROTECTION_FAILED,
            }
            self._journal(
                session,
                event_type=f"EXECUTION_{outcome.status.value}",
                resource_type="order" if request is not None else "signal",
                resource_id=order.id if order is not None else str(signal.signal_id),
                payload={
                    "signal_id": str(signal.signal_id),
                    "status": outcome.status.value,
                    "reasons": list(outcome.reasons),
                    "requires_reconciliation": outcome.requires_reconciliation,
                },
                deduplication_key=f"execution:{digest}:{outcome.status.value}",
                now=now,
                severity=(
                    "ERROR"
                    if outcome.status
                    in {ExecutionStatus.UNKNOWN, ExecutionStatus.PROTECTION_FAILED}
                    else "WARNING" if outcome.status in failure_statuses else "INFO"
                ),
            )

    async def record_preexecution_block(
        self, signal_id: UUID | str, reasons: Sequence[str], now: datetime
    ) -> None:
        await asyncio.to_thread(
            self._record_preexecution_block_sync,
            str(signal_id),
            tuple(reasons),
            _utc(now),
        )

    def _record_preexecution_block_sync(
        self, signal_id: str, reasons: tuple[str, ...], now: datetime
    ) -> None:
        digest = hashlib.sha256((signal_id + "|" + "|".join(reasons)).encode()).hexdigest()
        with self.session_factory.begin() as session:
            row = session.get(Signal, signal_id)
            if row is not None:
                row.rationale = {
                    **row.rationale,
                    "preexecution_block_reasons": list(reasons),
                    "preexecution_blocked_at": now.isoformat(),
                }
            self._journal(
                session,
                event_type="EXECUTION_BLOCKED",
                resource_type="signal",
                resource_id=signal_id,
                payload={"reasons": list(reasons)},
                deduplication_key=f"preexecution:{digest}",
                now=now,
                severity="WARNING",
            )

    async def unknown_keys(self) -> tuple[str, ...]:
        return await asyncio.to_thread(self._unknown_keys_sync)

    def _unknown_keys_sync(self) -> tuple[str, ...]:
        with self.session_factory() as session:
            return tuple(
                session.scalars(
                    select(ExecutionAttempt.attempt_key).where(
                        ExecutionAttempt.status.in_(
                            [
                                IdempotencyStatus.SUBMITTED.value,
                                IdempotencyStatus.UNKNOWN.value,
                            ]
                        )
                    )
                )
            )

    async def open_position_plans(self, broker_account_id: str) -> tuple[ManagedPosition, ...]:
        return await asyncio.to_thread(self._open_position_plans_sync, broker_account_id)

    def _open_position_plans_sync(self, broker_account_id: str) -> tuple[ManagedPosition, ...]:
        with self.session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(Position).where(
                        Position.broker_account_id == broker_account_id,
                        Position.closed_at.is_(None),
                    )
                )
            )
            result: list[ManagedPosition] = []
            for row in rows:
                trade = session.scalar(select(Trade).where(Trade.position_id == row.id))
                if trade is None:
                    continue
                seen = frozenset(
                    session.scalars(
                        select(TradeEvent.event_type).where(TradeEvent.position_id == row.id)
                    )
                )
                targets = tuple(
                    value
                    for value in (
                        trade.take_profit_1,
                        trade.take_profit_2,
                        trade.take_profit_3,
                    )
                    if value is not None
                )
                result.append(
                    ManagedPosition(
                        row.id,
                        trade.id,
                        row.broker_ticket,
                        Direction(row.direction),
                        TradeState(row.state),
                        row.initial_volume,
                        row.current_volume,
                        row.open_price,
                        trade.initial_stop_loss,
                        targets,
                        trade.slippage,
                        seen,
                    )
                )
            return tuple(result)

    async def record_position_action(
        self,
        plan: ManagedPosition,
        action: PositionAction,
        *,
        event_type: str,
        state: TradeState | None,
        broker_position: BrokerPosition | None,
        now: datetime,
        event_key_suffix: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._record_position_action_sync,
            plan,
            action,
            event_type,
            state,
            broker_position,
            _utc(now),
            event_key_suffix,
        )

    def _record_position_action_sync(
        self,
        plan: ManagedPosition,
        action: PositionAction,
        event_type: str,
        state: TradeState | None,
        broker_position: BrokerPosition | None,
        now: datetime,
        event_key_suffix: str,
    ) -> None:
        with self.session_factory.begin() as session:
            position = session.get(Position, plan.position_id)
            trade = session.get(Trade, plan.trade_id)
            if position is None or trade is None:
                raise RuntimeError("managed position disappeared from durable ledger")
            if broker_position is not None:
                position.current_volume = broker_position.volume
                position.current_price = broker_position.current_price
                position.stop_loss = broker_position.stop_loss
                position.unrealized_pnl = broker_position.profit
            if state is not None and action.applied:
                position.state = state.value
                trade.state = state.value
            if (
                action.report is not None
                and action.report.success
                and event_type in {"CLOSED", "STOPPED_OUT"}
            ):
                position.current_volume = Decimal("0")
                position.closed_at = now
                trade.closed_at = now
                trade.exit_price = action.report.executed_price
                trade.exit_reason = event_type
                trade.realized_pnl = position.realized_pnl
                trade.net_pnl = position.realized_pnl
            raw_key = f"position:{plan.ticket}:{event_type}:{event_key_suffix}"
            event_key = "position:" + hashlib.sha256(raw_key.encode()).hexdigest()
            if (
                session.scalar(select(TradeEvent.id).where(TradeEvent.event_key == event_key))
                is None
            ):
                session.add(
                    TradeEvent(
                        trade_id=trade.id,
                        position_id=position.id,
                        event_key=event_key,
                        event_type=event_type,
                        previous_state=plan.state.value,
                        current_state=(state or plan.state).value,
                        reason=action.reason,
                        payload={
                            "applied": action.applied,
                            "broker_ticket": plan.ticket,
                            "report": self._report_payload(action.report),
                        },
                        occurred_at=now,
                    )
                )
            self._journal(
                session,
                event_type=f"POSITION_{event_type}",
                resource_type="position",
                resource_id=position.id,
                payload={
                    "ticket": plan.ticket,
                    "applied": action.applied,
                    "reason": action.reason,
                },
                deduplication_key=(
                    f"position-action:{plan.ticket}:{event_type}:{event_key_suffix}"
                ),
                now=now,
                severity="INFO" if action.applied else "WARNING",
            )

    @staticmethod
    def _report_payload(report: ExecutionReport | None) -> dict[str, Any] | None:
        if report is None:
            return None
        return {
            "success": report.success,
            "idempotency_key": report.idempotency_key,
            "broker_ticket": report.broker_ticket,
            "requested_price": _json_safe(report.requested_price),
            "executed_price": _json_safe(report.executed_price),
            "volume": str(report.volume),
            "broker_code": report.broker_code,
            "message": report.message,
            "submitted_at": report.submitted_at.isoformat(),
        }

    async def save_account_snapshot(self, broker_account_id: str, account: AccountSnapshot) -> None:
        await asyncio.to_thread(self._save_account_snapshot_sync, broker_account_id, account)

    def _save_account_snapshot_sync(self, broker_account_id: str, account: AccountSnapshot) -> None:
        with self.session_factory.begin() as session:
            exists = session.scalar(
                select(AccountSnapshotRow.id).where(
                    AccountSnapshotRow.broker_account_id == broker_account_id,
                    AccountSnapshotRow.timestamp == account.timestamp,
                )
            )
            if exists is None:
                session.add(
                    AccountSnapshotRow(
                        broker_account_id=broker_account_id,
                        account_type=account.account_type.value,
                        currency=account.currency,
                        balance=account.balance,
                        equity=account.equity,
                        margin=account.margin,
                        free_margin=account.free_margin,
                        leverage=account.leverage,
                        timestamp=account.timestamp,
                    )
                )

    async def save_risk_snapshot(
        self,
        broker_account_id: str,
        account: AccountSnapshot,
        usage: RiskUsage,
        *,
        circuit_breaker_active: bool,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._save_risk_snapshot_sync,
            broker_account_id,
            account,
            usage,
            circuit_breaker_active,
            _utc(now),
        )

    def _save_risk_snapshot_sync(
        self,
        broker_account_id: str,
        account: AccountSnapshot,
        usage: RiskUsage,
        circuit_breaker_active: bool,
        now: datetime,
    ) -> None:
        denominator = account.equity if account.equity > 0 else Decimal("1")
        account_drawdown = Decimal("0")
        if usage.peak_equity is not None:
            account_drawdown = max(
                Decimal("0"),
                (usage.peak_equity - account.equity) / usage.peak_equity,
            )
        with self.session_factory.begin() as session:
            session.add(
                RiskSnapshot(
                    broker_account_id=broker_account_id,
                    balance=account.balance,
                    equity=account.equity,
                    free_margin=account.free_margin,
                    realized_daily_pnl=-usage.daily_realized_loss,
                    unrealized_pnl=account.equity - account.balance,
                    open_risk=usage.open_risk,
                    daily_drawdown=usage.daily_realized_loss / denominator,
                    weekly_drawdown=usage.weekly_realized_loss / denominator,
                    account_drawdown=account_drawdown,
                    peak_to_valley_drawdown=account_drawdown,
                    circuit_breaker_active=circuit_breaker_active,
                    details={},
                    timestamp=now,
                )
            )

    async def record_broker_health(
        self,
        broker_account_id: str,
        *,
        connected: bool,
        healthy: bool,
        message: str,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._record_broker_health_sync,
            broker_account_id,
            connected,
            healthy,
            message,
            _utc(now),
        )

    def _record_broker_health_sync(
        self,
        broker_account_id: str,
        connected: bool,
        healthy: bool,
        message: str,
        now: datetime,
    ) -> None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(BrokerConnection)
                .where(BrokerConnection.broker_account_id == broker_account_id)
                .order_by(BrokerConnection.created_at.desc())
                .limit(1)
            )
            status = "CONNECTED" if connected and healthy else "DEGRADED"
            if row is None:
                row = BrokerConnection(
                    broker_account_id=broker_account_id,
                    status=status,
                    last_connected_at=now if connected else None,
                    last_disconnected_at=None if connected else now,
                    last_error=None if healthy else message,
                    details={},
                )
                session.add(row)
            else:
                row.status = status
                row.last_connected_at = now if connected else row.last_connected_at
                row.last_disconnected_at = now if not connected else row.last_disconnected_at
                row.last_error = None if healthy else message

    async def heartbeat(
        self,
        *,
        instance_key: str,
        hostname: str,
        version: str,
        environment: str,
        now: datetime,
        status: str = "RUNNING",
    ) -> str:
        return await asyncio.to_thread(
            self._heartbeat_sync,
            instance_key,
            hostname,
            version,
            environment,
            _utc(now),
            status,
        )

    def _heartbeat_sync(
        self,
        instance_key: str,
        hostname: str,
        version: str,
        environment: str,
        now: datetime,
        status: str,
    ) -> str:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(BotInstance).where(BotInstance.instance_key == instance_key)
            )
            if row is None:
                row = BotInstance(
                    instance_key=instance_key,
                    hostname=hostname,
                    version=version,
                    environment=environment,
                    status=status,
                    started_at=now,
                    heartbeat_at=now,
                    metadata_json={},
                )
                session.add(row)
                session.flush()
            else:
                row.hostname = hostname
                row.version = version
                row.environment = environment
                row.status = status
                row.heartbeat_at = now
                row.stopped_at = now if status == "STOPPED" else None
            return row.id

    async def aggregate_metrics(
        self, broker_account_id: str, account: AccountSnapshot, metric_date: date
    ) -> None:
        await asyncio.to_thread(
            self._aggregate_metrics_sync, broker_account_id, account, metric_date
        )

    def _aggregate_metrics_sync(
        self, broker_account_id: str, account: AccountSnapshot, metric_date: date
    ) -> None:
        start = datetime.combine(metric_date, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        with self.session_factory.begin() as session:
            trades = tuple(
                session.scalars(
                    select(Trade).where(
                        Trade.broker_account_id == broker_account_id,
                        Trade.closed_at >= start,
                        Trade.closed_at < end,
                    )
                )
            )
            snapshots = tuple(
                session.scalars(
                    select(AccountSnapshotRow)
                    .where(
                        AccountSnapshotRow.broker_account_id == broker_account_id,
                        AccountSnapshotRow.timestamp >= start,
                        AccountSnapshotRow.timestamp < end,
                    )
                    .order_by(AccountSnapshotRow.timestamp)
                )
            )
            wins = [row.realized_pnl for row in trades if row.realized_pnl > 0]
            losses = [-row.realized_pnl for row in trades if row.realized_pnl < 0]
            profit_factor = (
                sum(wins, Decimal("0")) / sum(losses, Decimal("0"))
                if losses
                else Decimal("999999") if wins else None
            )
            equities = [row.equity for row in snapshots]
            peak = max(equities, default=account.equity)
            trough = min(equities, default=account.equity)
            max_drawdown = (peak - trough) / peak if peak > 0 else Decimal("0")
            row = session.scalar(
                select(DailyMetric).where(
                    DailyMetric.metric_date == metric_date,
                    DailyMetric.broker_account_id == broker_account_id,
                    DailyMetric.strategy_id == "",
                )
            )
            values = {
                "currency": account.currency,
                "starting_equity": snapshots[0].equity if snapshots else account.equity,
                "ending_equity": snapshots[-1].equity if snapshots else account.equity,
                "realized_pnl": sum((item.realized_pnl for item in trades), Decimal("0")),
                "trade_count": len(trades),
                "win_count": len(wins),
                "loss_count": len(losses),
                "profit_factor": profit_factor,
                "max_drawdown": max_drawdown,
                "statistics": {},
            }
            if row is None:
                session.add(
                    DailyMetric(
                        metric_date=metric_date,
                        broker_account_id=broker_account_id,
                        strategy_id="",
                        **values,
                    )
                )
            else:
                for name, value in values.items():
                    setattr(row, name, value)

    async def record_reconciliation(self, report: ReconciliationReport, now: datetime) -> None:
        await asyncio.to_thread(self._record_reconciliation_sync, report, _utc(now))

    def _record_reconciliation_sync(self, report: ReconciliationReport, now: datetime) -> None:
        payload = {
            "healthy": report.healthy,
            "recovered_positions": report.recovered_positions,
            "recovered_orders": report.recovered_orders,
            "closed_positions": report.closed_positions,
            "cancelled_orders": report.cancelled_orders,
            "incidents": [
                {"code": item.code, "ticket": item.ticket, "detail": item.detail}
                for item in report.incidents
            ],
        }
        material = json.dumps(payload, sort_keys=True) + now.isoformat()
        with self.session_factory.begin() as session:
            self._journal(
                session,
                event_type="RECONCILIATION_COMPLETED",
                resource_type="worker",
                resource_id=None,
                payload=payload,
                deduplication_key=(
                    "reconciliation:" + hashlib.sha256(material.encode()).hexdigest()
                ),
                now=now,
                severity="INFO" if report.healthy and not report.incidents else "WARNING",
            )

    async def record_job_failure(self, job: str, error_type: str, now: datetime) -> None:
        await asyncio.to_thread(self._record_job_failure_sync, job, error_type, _utc(now))

    def _record_job_failure_sync(self, job: str, error_type: str, now: datetime) -> None:
        material = f"{job}:{error_type}:{now.isoformat()}"
        with self.session_factory.begin() as session:
            self._journal(
                session,
                event_type="WORKER_JOB_FAILED",
                resource_type="worker_job",
                resource_id=job,
                payload={"job": job, "error_type": error_type},
                deduplication_key=("job-failure:" + hashlib.sha256(material.encode()).hexdigest()),
                now=now,
                severity="ERROR",
            )

    async def claim_outbox(
        self,
        now: datetime,
        *,
        limit: int = 50,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> tuple[OutboxMessage, ...]:
        return await asyncio.to_thread(
            self._claim_outbox_sync,
            _utc(now),
            limit,
            stale_after,
        )

    def _claim_outbox_sync(
        self,
        now: datetime,
        limit: int,
        stale_after: timedelta,
    ) -> tuple[OutboxMessage, ...]:
        if limit < 1 or limit > 1_000:
            raise ValueError("outbox claim limit must be between 1 and 1000")
        if stale_after <= timedelta(0):
            raise ValueError("outbox stale interval must be positive")
        stale_before = now - stale_after
        with self.session_factory.begin() as session:
            rows = tuple(
                session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.available_at <= now,
                        (
                            (OutboxEvent.status == "PENDING")
                            | (
                                (OutboxEvent.status == "SENDING")
                                & (OutboxEvent.locked_at < stale_before)
                            )
                        ),
                    )
                    .order_by(OutboxEvent.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = "SENDING"
                row.locked_at = now
            return tuple(
                OutboxMessage(
                    event_id=row.id,
                    event_type=row.event_type,
                    aggregate_type=row.aggregate_type,
                    aggregate_id=row.aggregate_id,
                    payload=dict(row.payload),
                    attempt_count=row.attempt_count,
                )
                for row in rows
            )

    async def complete_outbox(
        self,
        message: OutboxMessage,
        *,
        delivered: bool,
        channel: str,
        recipient: str,
        error: str | None,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._complete_outbox_sync,
            message,
            delivered,
            channel,
            recipient,
            error,
            _utc(now),
        )

    def _complete_outbox_sync(
        self,
        message: OutboxMessage,
        delivered: bool,
        channel: str,
        recipient: str,
        error: str | None,
        now: datetime,
    ) -> None:
        with self.session_factory.begin() as session:
            row = session.get(OutboxEvent, message.event_id)
            if row is None:
                raise LookupError(f"outbox event {message.event_id} disappeared")
            row.attempt_count += 1
            row.locked_at = None
            if delivered:
                row.status = "PUBLISHED"
                row.published_at = now
                row.last_error = None
            else:
                row.status = "PENDING"
                row.last_error = (error or "notification delivery failed")[:2_000]
                delay_seconds = min(300, 2 ** min(row.attempt_count, 8))
                row.available_at = now + timedelta(seconds=delay_seconds)
            session.add(
                NotificationRow(
                    channel=channel[:32],
                    recipient=recipient[:255],
                    event_type=message.event_type[:64],
                    status="SENT" if delivered else "FAILED",
                    subject=message.event_type.replace("_", " ").title()[:255],
                    body=json.dumps(message.payload, default=str, sort_keys=True)[:20_000],
                    payload=_json_safe(message.payload),
                    attempt_count=row.attempt_count,
                    last_error=None if delivered else row.last_error,
                    sent_at=now if delivered else None,
                )
            )

    @staticmethod
    def _journal(
        session: Session,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        payload: Mapping[str, Any],
        deduplication_key: str,
        now: datetime,
        severity: str = "INFO",
    ) -> None:
        safe_payload = _json_safe(payload)
        session.add(
            SystemEvent(
                severity=severity,
                event_type=event_type[:64],
                message=event_type.replace("_", " ").title(),
                payload=safe_payload,
                correlation_id=resource_id,
                created_at=now,
            )
        )
        session.add(
            AuditLog(
                actor_type="SYSTEM",
                actor_id="production-worker",
                action=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                before_data=None,
                after_data=safe_payload,
                correlation_id=resource_id,
                created_at=now,
            )
        )
        key = deduplication_key[:255]
        exists = session.scalar(select(OutboxEvent.id).where(OutboxEvent.deduplication_key == key))
        if exists is None:
            session.add(
                OutboxEvent(
                    deduplication_key=key,
                    aggregate_type=resource_type,
                    aggregate_id=resource_id or "worker",
                    event_type=event_type,
                    payload=safe_payload,
                    status="PENDING",
                    available_at=now,
                    created_at=now,
                )
            )
