from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from app.api.auth import AuthService, Role
from app.brokers.mock import MockBrokerAdapter
from app.config.settings import Settings
from app.db.models import (
    AuditLog,
    BrokerAccount,
    ConfigVersion,
    DailyMetric,
    Position,
    Signal,
    Trade,
    User,
)
from app.domain.enums import Direction
from app.domain.models import BrokerPosition
from app.main import create_app
from app.risk.circuit_breaker import CircuitBreaker
from app.risk.models import RiskLimits, RiskUsage
from app.workers.persistence import SQLAlchemyCircuitStateStore

_JWT_SECRET = "api-integration-secret-that-is-definitely-longer-than-thirty-two-bytes"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "sqlite+pysqlite:///:memory:",
        "redis_url": "redis://127.0.0.1:1/0",
        "jwt_secret": _JWT_SECRET,
        "rate_limit_per_minute": 10_000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **cast(Any, values))


def _headers(settings: Settings, role: Role, subject: str = "operator-1") -> dict[str, str]:
    token = AuthService(settings).create_access_token(subject, role)
    return {"Authorization": f"Bearer {token}"}


def _seed_account(session: Any, *, external_id: str = "demo-1") -> BrokerAccount:
    user = User(
        email=f"{external_id}@example.test",
        password_hash="not-a-real-password-hash",
        role="admin",
    )
    session.add(user)
    session.flush()
    account = BrokerAccount(
        user_id=user.id,
        broker="Mock Exness",
        platform="MOCK",
        external_account_id=external_id,
        account_type="DEMO",
        server="mock-demo",
        currency="USD",
    )
    session.add(account)
    session.flush()
    return account


def _position(
    account: BrokerAccount,
    *,
    ticket: str,
    now: datetime,
    volume: Decimal = Decimal("1"),
    closed: bool = False,
) -> Position:
    return Position(
        broker_account_id=account.id,
        broker_ticket=ticket,
        symbol="XAUUSDm",
        direction="LONG",
        initial_volume=volume,
        current_volume=Decimal("0") if closed else volume,
        open_price=Decimal("2000"),
        current_price=Decimal("2002"),
        stop_loss=Decimal("1995"),
        take_profits=["2010", "2020"],
        state="CLOSED" if closed else "POSITION_OPEN",
        opened_at=now,
        closed_at=now if closed else None,
    )


def _trade(
    account: BrokerAccount,
    position: Position,
    *,
    ticket: str,
    now: datetime,
    net_pnl: Decimal,
    environment: str = "demo",
) -> Trade:
    return Trade(
        position_id=position.id,
        broker_account_id=account.id,
        strategy_id="gold_h1_m15_m5",
        strategy_version="1.0.0",
        broker_ticket=ticket,
        symbol="XAUUSDm",
        canonical_symbol="XAUUSD",
        direction="LONG",
        state="CLOSED",
        environment=environment,
        volume=Decimal("1"),
        entry_price=Decimal("2000"),
        exit_price=Decimal("2002"),
        stop_loss=Decimal("1995"),
        initial_stop_loss=Decimal("1995"),
        take_profit_1=Decimal("2010"),
        take_profit_2=Decimal("2020"),
        take_profit_3=None,
        risk_amount=Decimal("100"),
        risk_percentage=Decimal("0.01"),
        risk_reward=Decimal("2"),
        gross_pnl=net_pnl + Decimal("2"),
        net_pnl=net_pnl,
        realized_pnl=net_pnl,
        commission=Decimal("1"),
        swap=Decimal("0"),
        spread_cost_estimate=Decimal("0.5"),
        slippage=Decimal("0.1"),
        slippage_cost=Decimal("0.5"),
        r_multiple=net_pnl / Decimal("100"),
        exit_reason="TEST_EXIT",
        opened_at=now,
        closed_at=now,
    )


def test_factory_routes_auth_health_metrics_and_websocket() -> None:
    settings = _settings()
    broker = MockBrokerAdapter.gold_demo(now=datetime.now(UTC))
    app = create_app(settings, broker=broker)
    required_paths = {
        "/health",
        "/ready",
        "/metrics",
        "/ws",
        "/api/v1/account",
        "/api/v1/symbols",
        "/api/v1/signals",
        "/api/v1/signals/{signal_id}",
        "/api/v1/signals/{signal_id}/approve",
        "/api/v1/positions",
        "/api/v1/positions/{position_id}/close",
        "/api/v1/trades",
        "/api/v1/metrics",
        "/api/v1/strategy",
        "/api/v1/strategy/config",
        "/api/v1/risk",
        "/api/v1/risk/config",
        "/api/v1/mode",
        "/api/v1/admin/kill-switch",
        "/api/v1/admin/reconcile",
        "/api/v1/admin/circuit-reset",
    }
    route_paths = {
        path for route in app.routes if (path := getattr(route, "path", None)) is not None
    }
    assert required_paths - {"/ws", "/metrics"} <= set(app.openapi()["paths"])
    assert {"/ws", "/metrics"} <= route_paths

    viewer = _headers(settings, Role.VIEWER)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["components"]["redis"]["state"] == "degraded"
        assert client.get("/ready").status_code == 503
        prometheus = client.get("/metrics")
        assert prometheus.status_code == 200
        assert "goldflow_component_healthy" in prometheus.text

        assert client.get("/api/v1/account").status_code == 401
        assert client.get("/api/v1/account", headers=viewer).status_code == 200
        symbols = client.get("/api/v1/symbols", headers=viewer)
        assert symbols.status_code == 200
        assert symbols.json()[0]["canonical_symbol"] == "XAUUSD"
        for path in (
            "/api/v1/signals",
            "/api/v1/positions",
            "/api/v1/trades",
            "/api/v1/metrics",
            "/api/v1/strategy",
            "/api/v1/risk",
        ):
            assert client.get(path, headers=viewer).status_code == 200

        with client.websocket_connect(f"/ws?token={viewer['Authorization'][7:]}") as websocket:
            message = websocket.receive_json()
            assert message["event"] == "BOT_STATUS"
            assert message["payload"]["principal_role"] == "viewer"
            websocket.send_text("ping")
            assert websocket.receive_json()["event"] == "PONG"
        with (
            pytest.raises(WebSocketDisconnect) as denied,
            client.websocket_connect("/ws?token=invalid"),
        ):
            pass
        assert denied.value.code == 4401


def test_rbac_typed_config_validation_and_successful_actions_are_audited() -> None:
    settings = _settings(trading_env="demo")
    app = create_app(settings, broker=MockBrokerAdapter.gold_demo(now=datetime.now(UTC)))
    viewer = _headers(settings, Role.VIEWER, "viewer-1")
    trader = _headers(settings, Role.TRADER, "trader-1")
    admin = _headers(settings, Role.ADMIN, "admin-1")

    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/mode",
                headers=viewer,
                json={"mode": "SEMI_AUTO", "reason": "operator test"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/risk/config",
                headers=trader,
                json={"values": {"risk_per_trade": "0.02"}, "reason": "operator test"},
            ).status_code
            == 403
        )

        invalid_updates = (
            {"risk_per_trade": 2},
            {"unknown_key": 1},
            {"martingale": True},
            {"grid": True},
        )
        for values in invalid_updates:
            response = client.post(
                "/api/v1/risk/config",
                headers=admin,
                json={"values": values, "reason": "invalid configuration test"},
            )
            assert response.status_code == 422
        bad_strategy = client.post(
            "/api/v1/strategy/config",
            headers=admin,
            json={"values": {"arbitrary_python": "no"}, "reason": "invalid field test"},
        )
        assert bad_strategy.status_code == 422

        container = app.state.container
        with container.database.session_scope() as session:
            assert session.scalar(select(func.count(ConfigVersion.id))) == 0
            assert session.scalar(select(func.count(AuditLog.id))) == 0

        risk = client.post(
            "/api/v1/risk/config",
            headers=admin,
            json={"values": {"risk_per_trade": "0.02"}, "reason": "approved test change"},
        )
        assert risk.status_code == 200
        assert risk.json()["config"]["risk_per_trade"] == "0.02"
        strategy = client.post(
            "/api/v1/strategy/config",
            headers=admin,
            json={"values": {"confidence_threshold": 80}, "reason": "approved test change"},
        )
        assert strategy.status_code == 200
        mode = client.post(
            "/api/v1/mode",
            headers=admin,
            json={"mode": "SEMI_AUTO", "reason": "demo operator approval"},
        )
        assert mode.status_code == 200
        kill = client.post(
            "/api/v1/admin/kill-switch",
            headers=admin,
            json={"enabled": True, "reason": "operator safety drill"},
        )
        assert kill.status_code == 200
        assert kill.json()["kill_switch"] is True

        with container.database.session_scope() as session:
            actions = set(session.scalars(select(AuditLog.action)))
            assert {
                "RISK_CONFIG_CHANGED",
                "STRATEGY_CONFIG_CHANGED",
                "TRADING_MODE_CHANGED",
                "KILL_SWITCH_ACTIVATED",
            } <= actions
            assert session.scalar(select(func.count(ConfigVersion.id))) == 4
            runtime = session.scalar(
                select(ConfigVersion).where(
                    ConfigVersion.config_type == "runtime_controls",
                    ConfigVersion.is_active.is_(True),
                )
            )
            assert runtime is not None
            assert runtime.payload["mode"] == "SEMI_AUTO"
            assert runtime.payload["kill_switch"] is True


def test_admin_can_auditably_reset_durable_drawdown_latch() -> None:
    now = datetime.now(UTC)
    settings = _settings(trading_env="demo")
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("90"), now=now)
    app = create_app(settings, broker=broker)
    admin = _headers(settings, Role.ADMIN, "risk-admin")

    with TestClient(app) as client:
        store = SQLAlchemyCircuitStateStore(app.state.container.database.session_factory)
        circuit = CircuitBreaker(store, RiskLimits())
        account = asyncio.run(broker.get_account())
        locked = asyncio.run(circuit.evaluate(account, RiskUsage(peak_equity=Decimal("100")), now))
        assert locked.account_locked and locked.manual_reenable_required

        response = client.post(
            "/api/v1/admin/circuit-reset",
            headers=admin,
            json={"reason": "drawdown incident reviewed and account verified"},
        )

        assert response.status_code == 200
        assert response.json()["data"]["manual_reenable_required"] is False
        assert response.json()["data"]["mode"] == "SIGNAL"
        restored = asyncio.run(store.load(account.account_id))
        assert restored is not None and not restored.blocks_new_trades
        with app.state.container.database.session_scope() as session:
            actions = set(session.scalars(select(AuditLog.action)))
            assert "CIRCUIT_BREAKER_MANUAL_RESET" in actions


@pytest.mark.parametrize("unhealthy", [False, True])
def test_kill_switch_activates_without_a_usable_broker(unhealthy: bool) -> None:
    settings = _settings(broker_type="unconfigured")
    broker: MockBrokerAdapter | None = None
    if unhealthy:
        broker = MockBrokerAdapter.gold_demo(now=datetime.now(UTC))
        broker.set_health(healthy=False)
    app = create_app(settings, broker=broker)
    admin = _headers(settings, Role.ADMIN)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/admin/kill-switch",
            headers=admin,
            json={"enabled": True, "reason": "broker outage safety action"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["kill_switch"] is True
        assert payload["reconciliation_required"] is True
        assert "BROKER_UNAVAILABLE_RECONCILIATION_REQUIRED" in payload["cancellation_failures"]
        with app.state.container.database.session_scope() as session:
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "KILL_SWITCH_ACTIVATED")
            )
            assert audit is not None
            assert audit.after_data["enabled"] is True


def test_partial_close_audit_preserves_pre_mutation_volume() -> None:
    now = datetime.now(UTC)
    settings = _settings()
    broker = MockBrokerAdapter.gold_demo(now=now)
    app = create_app(settings, broker=broker)
    trader = _headers(settings, Role.TRADER)

    with TestClient(app) as client:
        with app.state.container.database.session_scope() as session:
            account = _seed_account(session)
            position = _position(account, ticket="position-partial", now=now)
            session.add(position)
            session.flush()
            position_id = position.id
        broker.seed_position(
            BrokerPosition(
                ticket="position-partial",
                symbol="XAUUSDm",
                direction=Direction.LONG,
                volume=Decimal("1"),
                open_price=Decimal("2000"),
                current_price=Decimal("2002"),
                stop_loss=Decimal("1995"),
                take_profit=Decimal("2010"),
                profit=Decimal("2"),
                opened_at=now,
            )
        )

        response = client.post(
            f"/api/v1/positions/{position_id}/close",
            headers=trader,
            json={"volume": "0.4", "reason": "manual partial reduction"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PARTIALLY_CLOSED"
        with app.state.container.database.session_scope() as session:
            stored = session.get(Position, position_id)
            audit = session.scalar(select(AuditLog).where(AuditLog.resource_id == position_id))
            assert stored is not None and stored.current_volume == Decimal("0.6")
            assert audit is not None
            assert Decimal(audit.before_data["volume"]) == Decimal("1")
            assert Decimal(audit.after_data["volume"]) == Decimal("0.6")
            assert Decimal(audit.after_data["closed_volume"]) == Decimal("0.4")


def test_expired_signal_approval_persists_terminal_status_and_audit() -> None:
    now = datetime.now(UTC)
    settings = _settings()
    app = create_app(settings, broker=MockBrokerAdapter.gold_demo(now=now))
    trader = _headers(settings, Role.TRADER)

    with TestClient(app) as client:
        signal_id = "expired-signal"
        with app.state.container.database.session_scope() as session:
            session.add(
                Signal(
                    signal_id=signal_id,
                    strategy_id="gold_h1_m15_m5",
                    strategy_version="1.0.0",
                    symbol="XAUUSDm",
                    canonical_symbol="XAUUSD",
                    action="LONG",
                    direction="LONG",
                    entry_min=Decimal("2000"),
                    entry_max=Decimal("2001"),
                    stop_loss=Decimal("1995"),
                    take_profits=["2010"],
                    risk_reward=Decimal("2"),
                    confidence_score=80,
                    status="APPROVAL_REQUIRED",
                    rationale={},
                    created_at=now - timedelta(minutes=10),
                    expires_at=now - timedelta(minutes=1),
                )
            )

        response = client.post(
            f"/api/v1/signals/{signal_id}/approve",
            headers=trader,
            json={"approved": True, "reason": "late operator review"},
        )
        assert response.status_code == 409

        with app.state.container.database.session_scope() as session:
            stored = session.get(Signal, signal_id)
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "SIGNAL_APPROVAL_REJECTED")
            )
            assert stored is not None and stored.status == "EXPIRED"
            assert audit is not None
            assert audit.before_data["status"] == "APPROVAL_REQUIRED"
            assert audit.after_data["status"] == "EXPIRED"


def test_trade_api_matches_complete_persistent_trade_schema() -> None:
    now = datetime.now(UTC)
    settings = _settings()
    app = create_app(settings, broker=MockBrokerAdapter.gold_demo(now=now))
    viewer = _headers(settings, Role.VIEWER)

    with TestClient(app) as client:
        with app.state.container.database.session_scope() as session:
            account = _seed_account(session, external_id="trade-schema")
            position = _position(account, ticket="schema-position", now=now, closed=True)
            session.add(position)
            session.flush()
            session.add(
                _trade(
                    account,
                    position,
                    ticket="schema-position",
                    now=now,
                    net_pnl=Decimal("20"),
                )
            )

        response = client.get("/api/v1/trades", headers=viewer)
        assert response.status_code == 200
        item = response.json()[0]
        expected = {
            "id",
            "position_id",
            "signal_id",
            "broker_account_id",
            "strategy_id",
            "strategy_version",
            "broker_ticket",
            "symbol",
            "canonical_symbol",
            "direction",
            "state",
            "environment",
            "volume",
            "entry_price",
            "exit_price",
            "stop_loss",
            "initial_stop_loss",
            "take_profit_1",
            "take_profit_2",
            "take_profit_3",
            "risk_amount",
            "risk_percentage",
            "risk_reward",
            "gross_pnl",
            "net_pnl",
            "realized_pnl",
            "commission",
            "swap",
            "spread_cost_estimate",
            "slippage",
            "slippage_cost",
            "r_multiple",
            "exit_reason",
            "opened_at",
            "closed_at",
            "created_at",
            "updated_at",
        }
        assert set(item) == expected


def test_production_auto_requires_and_audits_durable_demo_validation() -> None:
    now = datetime.now(UTC)
    settings = _settings(
        trading_env="production",
        live_trading_enabled=True,
        live_trading_confirmation="explicit-live-confirmation",
        live_trading_confirmation_secret="explicit-live-confirmation",
        minimum_demo_trades=2,
        maximum_demo_drawdown=Decimal("0.10"),
        minimum_demo_profit_factor=Decimal("1.2"),
        require_demo_validation_for_live=True,
    )
    app = create_app(settings, broker=None)
    admin = _headers(settings, Role.ADMIN)

    with TestClient(app) as client:
        rejected = client.post(
            "/api/v1/mode",
            headers=admin,
            json={"mode": "AUTO", "reason": "request production automation"},
        )
        assert rejected.status_code == 409
        evidence = rejected.json()["detail"]["demo_validation"]
        assert evidence["passed"] is False
        assert evidence["checks"]["minimum_closed_trades"] is False
        assert "not predictions or guarantees" in evidence["disclaimer"]

        with app.state.container.database.session_scope() as session:
            account = _seed_account(session, external_id="demo-validation")
            first_position = _position(account, ticket="demo-win", now=now, closed=True)
            second_position = _position(account, ticket="demo-loss", now=now, closed=True)
            session.add_all((first_position, second_position))
            session.flush()
            session.add_all(
                (
                    _trade(
                        account,
                        first_position,
                        ticket="demo-win",
                        now=now,
                        net_pnl=Decimal("20"),
                    ),
                    _trade(
                        account,
                        second_position,
                        ticket="demo-loss",
                        now=now,
                        net_pnl=Decimal("-5"),
                    ),
                    DailyMetric(
                        metric_date=now.date(),
                        broker_account_id=account.id,
                        strategy_id="gold_h1_m15_m5",
                        currency="USD",
                        starting_equity=Decimal("1000"),
                        ending_equity=Decimal("1015"),
                        realized_pnl=Decimal("15"),
                        trade_count=2,
                        win_count=1,
                        loss_count=1,
                        profit_factor=Decimal("4"),
                        max_drawdown=Decimal("0.05"),
                        statistics={},
                    ),
                )
            )

        approved = client.post(
            "/api/v1/mode",
            headers=admin,
            json={"mode": "AUTO", "reason": "validated demo evidence reviewed"},
        )
        assert approved.status_code == 200
        assert approved.json()["mode"] == "AUTO"
        with app.state.container.database.session_scope() as session:
            actions = list(session.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))
            assert "TRADING_MODE_CHANGE_REJECTED" in actions
            assert "TRADING_MODE_CHANGED" in actions
