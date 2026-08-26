from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db import Database
from app.db.models import BrokerAccount, User

REQUIRED_TABLES = {
    "account_snapshots",
    "audit_logs",
    "bot_instances",
    "broker_accounts",
    "broker_connections",
    "config_versions",
    "daily_metrics",
    "execution_attempts",
    "market_snapshots",
    "notifications",
    "orders",
    "outbox_events",
    "positions",
    "risk_snapshots",
    "signal_conditions",
    "signals",
    "strategy_configs",
    "symbols",
    "system_events",
    "trade_events",
    "trades",
    "users",
}


@pytest.fixture
def database() -> object:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


def _seed_identity(database: Database) -> tuple[str, str]:
    with database.session_scope() as session:
        user = User(email="admin@example.test", password_hash="not-a-real-password-hash")
        session.add(user)
        session.flush()
        account = BrokerAccount(
            user_id=user.id,
            broker="Exness",
            platform="MT5",
            external_account_id="123456",
            account_type="DEMO",
            server="Exness-MT5Trial",
            currency="USD",
        )
        session.add(account)
        session.flush()
        return user.id, account.id


def test_metadata_creates_every_requested_and_safety_table(database: Database) -> None:
    assert set(sa.inspect(database.engine).get_table_names()) == REQUIRED_TABLES


def test_unit_of_work_requires_explicit_commit_and_round_trips_utc(
    database: Database,
) -> None:
    heartbeat_at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    with database.unit_of_work() as uow:
        uow.heartbeats.register(
            instance_key="worker-rollback",
            hostname="test-host",
            version="1.0.0",
            environment="test",
            at=heartbeat_at,
        )

    with database.unit_of_work() as uow:
        assert uow.heartbeats.by_instance_key("worker-rollback") is None
        instance = uow.heartbeats.register(
            instance_key="worker-1",
            hostname="test-host",
            version="1.0.0",
            environment="test",
            at=heartbeat_at,
        )
        uow.outbox.enqueue(
            deduplication_key="worker-1:started",
            aggregate_type="bot_instance",
            aggregate_id=instance.id,
            event_type="BOT_STARTED",
            payload={"instance_key": instance.instance_key},
            available_at=heartbeat_at,
        )
        uow.commit()

    with database.unit_of_work() as uow:
        loaded_instance = uow.heartbeats.by_instance_key("worker-1")
        assert loaded_instance is not None
        assert loaded_instance.heartbeat_at == heartbeat_at
        assert loaded_instance.heartbeat_at.tzinfo is UTC
        assert len(uow.outbox.pending(now=heartbeat_at)) == 1


def test_signal_order_position_trade_and_event_repositories(database: Database) -> None:
    _, account_id = _seed_identity(database)
    now = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)

    with database.unit_of_work() as uow:
        signal = uow.signals.create(
            strategy_id="gold_h1_m15_m5",
            strategy_version="1.0.0",
            symbol="XAUUSDm",
            canonical_symbol="XAUUSD",
            action="LONG",
            direction="LONG",
            entry_min=Decimal("2300"),
            entry_max=Decimal("2301"),
            stop_loss=Decimal("2295"),
            take_profits=[Decimal("2312"), Decimal("2320")],
            risk_reward=Decimal("2"),
            confidence_score=80,
            status="ACTIVE",
            rationale={"closed_candles": True},
            created_at=now,
        )
        uow.signals.add_condition(
            signal.signal_id,
            condition_name="h1_trend",
            passed=True,
            score=Decimal("30"),
        )
        order = uow.orders.create(
            signal_id=signal.signal_id,
            broker_account_id=account_id,
            idempotency_key="signal:order:1",
            symbol="XAUUSDm",
            direction="LONG",
            order_type="MARKET",
            status="PENDING",
            volume=Decimal("0.01"),
            requested_price=Decimal("2300.5"),
            stop_loss=Decimal("2295"),
            take_profits=[Decimal("2312"), Decimal("2320")],
        )
        attempt = uow.execution_attempts.begin(
            order_id=order.id,
            attempt_number=1,
            attempt_key="signal:order:1:attempt:1",
            request_payload={"validated": True},
            started_at=now,
        )
        uow.execution_attempts.complete(
            attempt.id,
            status="FILLED",
            broker_ticket="ticket-1",
            completed_at=now,
        )
        uow.orders.set_status(
            order.id,
            "FILLED",
            broker_ticket="ticket-1",
            executed_price=Decimal("2300.6"),
            occurred_at=now,
        )
        position = uow.positions.create(
            broker_account_id=account_id,
            signal_id=signal.signal_id,
            opening_order_id=order.id,
            broker_ticket="position-1",
            symbol="XAUUSDm",
            direction="LONG",
            initial_volume=Decimal("0.01"),
            current_volume=Decimal("0.01"),
            open_price=Decimal("2300.6"),
            current_price=Decimal("2300.6"),
            stop_loss=Decimal("2295"),
            take_profits=[Decimal("2312"), Decimal("2320")],
            state="POSITION_OPEN",
            opened_at=now,
        )
        trade = uow.trades.create(
            position_id=position.id,
            signal_id=signal.signal_id,
            broker_account_id=account_id,
            strategy_id="gold_h1_m15_m5",
            strategy_version="1.0.0",
            broker_ticket="position-1",
            symbol="XAUUSDm",
            canonical_symbol="XAUUSD",
            direction="LONG",
            state="POSITION_OPEN",
            environment="demo",
            volume=Decimal("0.01"),
            entry_price=Decimal("2300.6"),
            stop_loss=Decimal("2295"),
            initial_stop_loss=Decimal("2295"),
            take_profit_1=Decimal("2312"),
            take_profit_2=Decimal("2320"),
            risk_amount=Decimal("0.50"),
            risk_percentage=Decimal("0.01"),
            risk_reward=Decimal("2"),
            opened_at=now,
        )
        event = uow.trade_events.append(
            trade_id=trade.id,
            position_id=position.id,
            event_key="trade-1:position-open",
            event_type="STATE_TRANSITION",
            previous_state="ORDER_FILLED",
            current_state="POSITION_OPEN",
            reason="broker position reconciled",
            occurred_at=now,
        )
        uow.commit()

    with database.unit_of_work() as uow:
        stored_order = uow.orders.by_idempotency_key("signal:order:1")
        assert stored_order is not None
        assert stored_order.take_profits == ["2312", "2320"]
        assert uow.execution_attempts.by_attempt_key("signal:order:1:attempt:1") is not None
        assert len(uow.positions.list_open(symbol="XAUUSDm")) == 1
        assert uow.trades.by_position(position.id) is not None
        assert uow.trade_events.by_event_key(event.event_key or "") is not None
        assert len(uow.trade_events.for_trade(trade.id)) == 1


def test_idempotency_key_is_unique(database: Database) -> None:
    _, account_id = _seed_identity(database)
    now = datetime.now(UTC)
    with pytest.raises(IntegrityError), database.session_scope() as session:
        from app.db.models import Order, Signal

        signal = Signal(
            signal_id="signal-one",
            strategy_id="strategy",
            strategy_version="1",
            symbol="XAUUSDm",
            canonical_symbol="XAUUSD",
            action="WAIT",
            confidence_score=0,
            created_at=now,
        )
        session.add(signal)
        session.flush()
        common = {
            "signal_id": signal.signal_id,
            "broker_account_id": account_id,
            "idempotency_key": "duplicate-key",
            "symbol": "XAUUSDm",
            "direction": "LONG",
            "order_type": "MARKET",
            "volume": Decimal("0.01"),
            "stop_loss": Decimal("2295"),
        }
        session.add(Order(**common))
        session.flush()
        session.add(Order(**common))
        session.flush()


def test_config_audit_system_and_heartbeat_repositories(database: Database) -> None:
    user_id, _ = _seed_identity(database)
    with database.unit_of_work() as uow:
        first = uow.config.add_version(
            config_type="risk",
            version="1",
            payload={"risk_per_trade": "0.01"},
            created_by_user_id=user_id,
            activate=True,
        )
        second = uow.config.add_version(
            config_type="risk",
            version="2",
            payload={"risk_per_trade": "0.005"},
            created_by_user_id=user_id,
            activate=True,
        )
        audit = uow.audit.record(
            user_id=user_id,
            actor_type="USER",
            actor_id=user_id,
            action="RISK_CONFIG_CHANGED",
            resource_type="config_version",
            resource_id=second.id,
            before_data={"version": "1"},
            after_data={"version": "2", "api_token": "must-not-persist"},
        )
        event = uow.system_events.record(
            severity="WARNING",
            event_type="CIRCUIT_BREAKER",
            message="daily loss guard active",
        )
        uow.commit()

    with database.unit_of_work() as uow:
        assert uow.config.active_version("risk").id == second.id  # type: ignore[union-attr]
        assert uow.session is not None
        first_row = uow.session.get(type(first), first.id)
        assert first_row is not None
        assert first_row.is_active is False
        stored_audit = uow.audit.recent(resource_type="config_version")[0]
        assert stored_audit.id == audit.id
        assert stored_audit.after_data == {"version": "2", "api_token": "[REDACTED]"}
        assert uow.system_events.recent(severity="WARNING")[0].id == event.id


def test_initial_migration_upgrades_and_downgrades(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")

    command.upgrade(config, "head")
    engine = sa.create_engine(f"sqlite+pysqlite:///{database_path}")
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert tables == REQUIRED_TABLES | {"alembic_version"}
        trade_columns = {column["name"] for column in inspector.get_columns("trades")}
        assert {
            "broker_ticket",
            "canonical_symbol",
            "initial_stop_loss",
            "take_profit_1",
            "take_profit_2",
            "take_profit_3",
            "risk_amount",
            "risk_percentage",
            "risk_reward",
            "realized_pnl",
            "spread_cost_estimate",
            "slippage",
            "exit_reason",
            "environment",
        } <= trade_columns
    finally:
        engine.dispose()

    command.check(config)
    command.downgrade(config, "base")
    engine = sa.create_engine(f"sqlite+pysqlite:///{database_path}")
    try:
        assert set(sa.inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()
