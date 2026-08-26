from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.brokers.base import IndeterminateBrokerResult
from app.brokers.mock import MockBrokerAdapter, MockSendBehavior
from app.brokers.retry import RetryingBrokerAdapter
from app.config.settings import Settings
from app.container import build_broker
from app.domain.enums import Direction, OrderType
from app.domain.models import OrderRequest
from app.observability.health import HealthRegistry, HealthState
from app.observability.metrics import GoldFlowMetrics
from app.observability.sentry import _strip_sensitive_context


def test_health_registry_distinguishes_liveness_from_trading_readiness() -> None:
    registry = HealthRegistry()
    assert registry.snapshot()["live"] is True
    assert registry.ready is False

    for component in registry.REQUIRED_FOR_NEW_EXPOSURE:
        registry.update(component, HealthState.HEALTHY, "ok")
    assert registry.ready is True

    registry.update("redis", HealthState.DEGRADED, "coordination unavailable")
    snapshot = registry.snapshot()
    assert registry.ready is False
    assert snapshot["status"] == "not_ready"
    assert snapshot["components"]["redis"]["state"] == "degraded"


def test_heartbeat_validation_and_prometheus_health_gauge() -> None:
    registry = HealthRegistry()
    registry.heartbeat("strategy_worker")
    with pytest.raises(ValueError, match="positive"):
        registry.expire_heartbeats(timedelta(0))

    metrics = GoldFlowMetrics()
    metrics.health.labels(component="database").set(1)
    rendered = metrics.render().decode()
    assert 'goldflow_component_healthy{component="database"} 1.0' in rendered


def test_sentry_scrubber_removes_request_and_nested_secret_material() -> None:
    event = {
        "request": {"headers": {"authorization": "Bearer secret"}, "data": "private"},
        "extra": {"password": "secret", "nested": {"token": "secret", "safe": 1}},
    }
    scrubbed = _strip_sensitive_context(event, {})
    assert scrubbed["request"] == {}
    assert scrubbed["extra"]["password"] == "[REDACTED]"
    assert scrubbed["extra"]["nested"] == {"token": "[REDACTED]", "safe": 1}


@pytest.mark.asyncio
async def test_container_retry_wrapper_never_retries_order_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = MockBrokerAdapter.gold_demo(send_behaviors=(MockSendBehavior.TIMEOUT_AFTER_ACCEPT,))
    monkeypatch.setattr(
        MockBrokerAdapter,
        "gold_demo",
        classmethod(lambda cls: delegate),
    )
    broker = build_broker(Settings(_env_file=None, broker_type="mock"))
    assert isinstance(broker, RetryingBrokerAdapter)
    request = OrderRequest(
        signal_id=uuid4(),
        strategy_id="test",
        symbol="XAUUSDm",
        direction=Direction.LONG,
        order_type=OrderType.MARKET,
        volume=Decimal("0.01"),
        stop_loss=Decimal("1995"),
        take_profits=(Decimal("2010"),),
        idempotency_key="exactly-once-test",
        requested_price=Decimal("2000.20"),
    )

    with pytest.raises(IndeterminateBrokerResult):
        await broker.place_market_order(request)
    assert delegate.send_calls == 1
