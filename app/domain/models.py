"""Immutable value objects at GoldFlow's external and internal boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.domain.enums import (
    AccountType,
    Direction,
    OrderStatus,
    OrderType,
    SignalAction,
    SignalStatus,
    Timeframe,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float = 0.0
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("candle high is below an OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("candle low is above an OHLC value")
        if self.volume < 0 or self.spread < 0:
            raise ValueError("candle volume and spread cannot be negative")


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    bid: Decimal
    ask: Decimal
    timestamp: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("tick must have positive ask >= bid")

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class SymbolSpecification:
    name: str
    canonical_symbol: str
    base_currency: str
    quote_currency: str
    digits: int
    point: Decimal
    tick_size: Decimal
    tick_value: Decimal
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    stops_level_points: int = 0
    visible: bool = True
    trade_enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        positive = (
            self.point,
            self.tick_size,
            self.contract_size,
            self.volume_min,
            self.volume_max,
            self.volume_step,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("symbol price and volume specifications must be positive")
        if self.tick_value < 0 or self.volume_min > self.volume_max:
            raise ValueError("invalid tick value or volume bounds")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    broker: str
    platform: str
    account_id: str
    server: str
    currency: str
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    leverage: int
    account_type: AccountType
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        if self.leverage < 0:
            raise ValueError("leverage cannot be negative")


@dataclass(frozen=True, slots=True)
class TradeSignal:
    symbol: str
    canonical_symbol: str
    action: SignalAction
    strategy_id: str
    strategy_version: str
    confidence_score: int
    entry_min: Decimal | None
    entry_max: Decimal | None
    stop_loss: Decimal | None
    take_profits: tuple[Decimal, ...] = ()
    risk_reward: Decimal | None = None
    rationale: dict[str, Any] = field(default_factory=dict)
    status: SignalStatus = SignalStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    signal_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at))
            if self.expires_at <= self.created_at:
                raise ValueError("signal expiry must be after creation")
        if not 0 <= self.confidence_score <= 100:
            raise ValueError("confidence score must be between 0 and 100")
        if self.action in (SignalAction.LONG, SignalAction.SHORT):
            required = (self.entry_min, self.entry_max, self.stop_loss)
            if any(value is None for value in required):
                raise ValueError("trade signals require entry bounds and stop loss")
            if (
                self.entry_min is not None
                and self.entry_max is not None
                and self.entry_min > self.entry_max
            ):
                raise ValueError("entry_min cannot exceed entry_max")

    @property
    def direction(self) -> Direction | None:
        if self.action is SignalAction.LONG:
            return Direction.LONG
        if self.action is SignalAction.SHORT:
            return Direction.SHORT
        return None

    def is_expired(self, now: datetime | None = None) -> bool:
        reference = _utc(now) if now is not None else datetime.now(UTC)
        return self.expires_at is not None and reference >= self.expires_at


@dataclass(frozen=True, slots=True)
class OrderRequest:
    signal_id: UUID
    strategy_id: str
    symbol: str
    direction: Direction
    order_type: OrderType
    volume: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    idempotency_key: str
    requested_price: Decimal | None = None
    entry_min: Decimal | None = None
    entry_max: Decimal | None = None
    expires_at: datetime | None = None
    maximum_slippage: Decimal = Decimal("0")
    comment: str = "goldflow"

    def __post_init__(self) -> None:
        if self.volume <= 0 or self.stop_loss <= 0:
            raise ValueError("order volume and stop loss must be positive")
        if not self.take_profits or any(value <= 0 for value in self.take_profits):
            raise ValueError("at least one positive take profit is required")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency key is required")
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at))


@dataclass(frozen=True, slots=True)
class OrderCheckResult:
    accepted: bool
    reasons: tuple[str, ...] = ()
    margin_required: Decimal | None = None
    broker_code: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    success: bool
    idempotency_key: str
    broker_ticket: str | None
    requested_price: Decimal | None
    executed_price: Decimal | None
    volume: Decimal
    broker_code: str | None = None
    message: str = ""
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "submitted_at", _utc(self.submitted_at))

    @property
    def slippage(self) -> Decimal | None:
        if self.requested_price is None or self.executed_price is None:
            return None
        return abs(self.executed_price - self.requested_price)


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    ticket: str
    symbol: str
    direction: Direction
    volume: Decimal
    open_price: Decimal
    current_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    profit: Decimal
    opened_at: datetime
    strategy_id: str = ""
    signal_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "opened_at", _utc(self.opened_at))
        if self.volume <= 0 or self.open_price <= 0 or self.stop_loss <= 0:
            raise ValueError("broker position has invalid protected trade values")


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    ticket: str
    symbol: str
    direction: Direction
    order_type: OrderType
    volume: Decimal
    price: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    status: OrderStatus
    created_at: datetime
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _utc(self.created_at))
