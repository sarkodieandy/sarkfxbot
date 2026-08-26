"""Persistent entities for trading, audit, risk, and operational state."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import (
    Base,
    TimestampMixin,
    UTCDateTime,
    UUIDPrimaryKeyMixin,
    utc_now,
)

PRICE = Numeric(24, 10)
MONEY = Numeric(24, 8)
RATIO = Numeric(18, 8)
VOLUME = Numeric(18, 8)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BotInstance(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "bot_instances"

    instance_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTING")
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now(), index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class BrokerAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "broker_accounts"
    __table_args__ = (
        UniqueConstraint(
            "broker",
            "platform",
            "server",
            "external_account_id",
            name="uq_broker_account_identity",
        ),
    )

    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    broker: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    server: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class BrokerConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "broker_connections"
    __table_args__ = (Index("ix_broker_connections_account_status", "broker_account_id", "status"),)

    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    bot_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("bot_instances.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCONNECTED")
    last_connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_disconnected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text())
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Symbol(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint(
            "broker_account_id", "broker_symbol", name="uq_symbol_account_broker_symbol"
        ),
        Index("ix_symbols_canonical_active", "canonical_symbol", "is_active"),
    )

    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    digits: Mapped[int] = mapped_column(Integer, nullable=False)
    point: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    tick_value: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    contract_size: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    volume_min: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    volume_max: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    volume_step: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    stops_level_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class StrategyConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_configs"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_config_version"),
        Index("ix_strategy_configs_enabled", "strategy_id", "is_enabled"),
    )

    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    effective_from: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_symbol_status_created", "symbol", "status", "created_at"),
        Index("ix_signals_strategy_created", "strategy_id", "created_at"),
    )

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_configs.id", ondelete="SET NULL"), index=True
    )
    symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="SET NULL"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16))
    entry_min: Mapped[Decimal | None] = mapped_column(PRICE)
    entry_max: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profits: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    risk_reward: Mapped[Decimal | None] = mapped_column(RATIO)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    rationale: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)


class SignalCondition(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "signal_conditions"
    __table_args__ = (
        UniqueConstraint("signal_id", "condition_name", name="uq_signal_condition_name"),
    )

    signal_id: Mapped[str] = mapped_column(
        ForeignKey("signals.signal_id", ondelete="CASCADE"), nullable=False, index=True
    )
    condition_name: Mapped[str] = mapped_column(String(128), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=Decimal("0"))
    observed_value: Mapped[Any | None] = mapped_column(JSON)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("broker_account_id", "broker_ticket", name="uq_order_account_ticket"),
        Index("ix_orders_account_status", "broker_account_id", "status"),
        Index("ix_orders_signal_status", "signal_id", "status"),
    )

    signal_id: Mapped[str] = mapped_column(
        ForeignKey("signals.signal_id", ondelete="RESTRICT"), nullable=False
    )
    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    broker_ticket: Mapped[str | None] = mapped_column(String(128))
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    volume: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    requested_price: Mapped[Decimal | None] = mapped_column(PRICE)
    executed_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profits: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    maximum_slippage: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    actual_slippage: Mapped[Decimal | None] = mapped_column(PRICE)
    broker_code: Mapped[str | None] = mapped_column(String(64))
    broker_message: Mapped[str | None] = mapped_column(Text())
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    filled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Position(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("broker_account_id", "broker_ticket", name="uq_position_account_ticket"),
        Index("ix_positions_account_state", "broker_account_id", "state"),
        Index("ix_positions_symbol_closed", "symbol", "closed_at"),
    )

    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    signal_id: Mapped[str | None] = mapped_column(
        ForeignKey("signals.signal_id", ondelete="SET NULL"), index=True
    )
    opening_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), unique=True
    )
    broker_ticket: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    initial_volume: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    current_volume: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    open_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profits: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="POSITION_OPEN")
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Trade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("broker_account_id", "broker_ticket", name="uq_trade_account_ticket"),
        Index("ix_trades_strategy_closed", "strategy_id", "closed_at"),
        Index("ix_trades_symbol_closed", "symbol", "closed_at"),
    )

    position_id: Mapped[str] = mapped_column(
        ForeignKey("positions.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    signal_id: Mapped[str | None] = mapped_column(
        ForeignKey("signals.signal_id", ondelete="SET NULL"), index=True
    )
    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_ticket: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    volume: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    initial_stop_loss: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    take_profit_1: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_2: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_3: Mapped[Decimal | None] = mapped_column(PRICE)
    risk_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    risk_percentage: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    risk_reward: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    swap: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    spread_cost_estimate: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0")
    )
    slippage: Mapped[Decimal] = mapped_column(PRICE, nullable=False, default=Decimal("0"))
    slippage_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    r_multiple: Mapped[Decimal | None] = mapped_column(RATIO)
    exit_reason: Mapped[str | None] = mapped_column(String(128))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ExecutionAttempt(UUIDPrimaryKeyMixin, Base):
    """Durable record around each non-blind broker submission attempt."""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint("order_id", "attempt_number", name="uq_execution_attempt_number"),
        Index("ix_execution_attempts_status_started", "status", "started_at"),
    )

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    broker_ticket: Mapped[str | None] = mapped_column(String(128), index=True)
    broker_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OutboxEvent(UUIDPrimaryKeyMixin, Base):
    """Transactional outbox record for reliable post-commit delivery."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_delivery", "status", "available_at", "created_at"),)

    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )


class TradeEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trade_events"
    __table_args__ = (
        Index("ix_trade_events_trade_occurred", "trade_id", "occurred_at"),
        Index("ix_trade_events_position_occurred", "position_id", "occurred_at"),
    )

    trade_id: Mapped[str | None] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), index=True
    )
    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(32))
    current_state: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )


class RiskSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "risk_snapshots"
    __table_args__ = (
        Index("ix_risk_snapshots_account_timestamp", "broker_account_id", "timestamp"),
    )

    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    bot_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("bot_instances.id", ondelete="SET NULL")
    )
    balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    free_margin: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    realized_daily_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    open_risk: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    daily_drawdown: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    weekly_drawdown: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    account_drawdown: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    peak_to_valley_drawdown: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    circuit_breaker_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )


class AccountSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (
        UniqueConstraint("broker_account_id", "timestamp", name="uq_account_snapshot_timestamp"),
    )

    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    margin: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    free_margin: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now(), index=True
    )


class DailyMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (
        UniqueConstraint(
            "metric_date",
            "broker_account_id",
            "strategy_id",
            name="uq_daily_metric_scope",
        ),
    )

    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    broker_account_id: Mapped[str] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    starting_equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    ending_equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profit_factor: Mapped[Decimal | None] = mapped_column(RATIO)
    max_drawdown: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class MarketSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "candle_time", name="uq_market_snapshot_candle"),
        Index("ix_market_snapshots_symbol_time", "symbol", "candle_time"),
    )

    symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="SET NULL"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    candle_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    spread: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    is_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_status_created", "status", "created_at"),)

    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class SystemEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_events"
    __table_args__ = (
        Index("ix_system_events_type_created", "event_type", "created_at"),
        Index("ix_system_events_severity_created", "severity", "created_at"),
    )

    bot_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("bot_instances.id", ondelete="SET NULL"), index=True
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )


class ConfigVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "config_versions"
    __table_args__ = (
        UniqueConstraint("config_type", "version", name="uq_config_version"),
        Index("ix_config_versions_active", "config_type", "is_active"),
    )

    config_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_resource_created", "resource_type", "resource_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
    )

    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=func.now()
    )
