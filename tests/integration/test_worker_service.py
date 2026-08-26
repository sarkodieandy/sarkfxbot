from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.backtesting.data import load_candle_csv
from app.brokers.mock import MockBrokerAdapter
from app.config.settings import Settings
from app.db.models import (
    BotInstance,
    ConfigVersion,
    ExecutionAttempt,
    Notification,
    Order,
    Position,
    Signal,
    Trade,
    TradeEvent,
)
from app.db.repositories import ConfigRepository
from app.db.session import Database
from app.domain.enums import Direction, SignalAction, SignalStatus, Timeframe, TradingMode
from app.domain.models import Candle, TradeSignal
from app.execution.models import ExecutionStatus
from app.market.candles import candle_close_time
from app.notifications.base import NullNotifier
from app.runtime_contract import (
    EXECUTION_QUEUE,
    RUNTIME_CONFIG_TYPE,
    DurableRuntimeState,
    heartbeat_key,
)
from app.strategies.gold_h1_m15_m5 import GoldStrategyConfig
from app.workers.service import GoldFlowWorker


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []
        self.lists: dict[str, list[str]] = {}

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool:
        del px, ex
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            deleted += int(self.values.pop(name, None) is not None)
        return deleted

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        del script
        assert numkeys == 1
        key, token = keys_and_args
        if self.values.get(key) != token:
            return 0
        del self.values[key]
        return 1

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def rpush(self, name: str, *values: str) -> int:
        queue = self.lists.setdefault(name, [])
        queue.extend(values)
        return len(queue)

    async def lpop(self, name: str, count: int | None = None) -> str | list[str] | None:
        queue = self.lists.setdefault(name, [])
        if not queue:
            return None
        if count is None:
            return queue.pop(0)
        values = queue[:count]
        del queue[:count]
        return values


class FixedLongStrategy:
    strategy_id = "gold_h1_m15_m5"

    def __init__(self, version: str) -> None:
        self.strategy_version = version

    def evaluate(
        self,
        candles: Mapping[Timeframe, Sequence[Candle]],
        *,
        as_of: datetime | None = None,
        open_direction: Direction | None = None,
    ) -> TradeSignal:
        assert candles[Timeframe.M5]
        assert as_of is not None
        action = SignalAction.WAIT if open_direction is not None else SignalAction.LONG
        created_at = as_of - timedelta(minutes=1)
        return TradeSignal(
            symbol="XAUUSDm",
            canonical_symbol="XAUUSD",
            action=action,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            confidence_score=82,
            entry_min=Decimal("1999.50") if action is SignalAction.LONG else None,
            entry_max=Decimal("2000.50") if action is SignalAction.LONG else None,
            stop_loss=Decimal("1999.00") if action is SignalAction.LONG else None,
            take_profits=(
                (Decimal("2002.50"), Decimal("2004.00")) if action is SignalAction.LONG else ()
            ),
            risk_reward=Decimal("1.9") if action is SignalAction.LONG else None,
            rationale={"reason": "FIXED_INTEGRATION_SIGNAL"},
            status=SignalStatus.ACTIVE,
            created_at=created_at,
            expires_at=as_of + timedelta(minutes=15),
            signal_id=uuid5(NAMESPACE_URL, f"worker-test:{action.value}:{created_at.isoformat()}"),
        )


def _strategy_factory(config: GoldStrategyConfig, version: str) -> FixedLongStrategy:
    assert config.use_closed_candles_only
    return FixedLongStrategy(version)


class OfflineSessionFactory:
    @staticmethod
    def begin() -> None:
        raise OperationalError("connect", {}, ConnectionError("database offline"))


class OfflineDatabase:
    session_factory = OfflineSessionFactory()


@pytest.mark.asyncio
async def test_database_offline_aborts_startup_disconnects_and_never_sends() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=now)
    worker = GoldFlowWorker(
        settings=Settings(_env_file=None, app_env="test", broker_type="mock"),
        database=OfflineDatabase(),  # type: ignore[arg-type]
        broker=broker,
        redis=None,
        clock=lambda: now,
    )

    with pytest.raises(OperationalError):
        await worker.startup()

    assert broker.send_calls == 0
    assert not (await broker.health_check()).connected
    with pytest.raises(RuntimeError, match="startup recovery"):
        await worker.execute_signals()


@pytest.mark.asyncio
async def test_account_drawdown_durably_disables_auto_until_manual_reset() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    broker = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=now)
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    worker = GoldFlowWorker(
        settings=Settings(
            _env_file=None,
            app_env="test",
            trading_env="demo",
            trading_mode="AUTO",
            broker_type="mock",
        ),
        database=database,
        broker=broker,
        redis=FakeRedis(),
        clock=lambda: now,
        instance_key="drawdown-worker",
    )

    await worker.startup()
    await worker.snapshot_account()
    original = await broker.get_account()
    broker.set_account(
        replace(
            original,
            balance=Decimal("800"),
            equity=Decimal("800"),
            free_margin=Decimal("800"),
        )
    )
    await worker.snapshot_risk()

    with database.session_scope() as session:
        runtime = session.scalar(
            select(ConfigVersion).where(
                ConfigVersion.config_type == RUNTIME_CONFIG_TYPE,
                ConfigVersion.is_active.is_(True),
            )
        )
        assert runtime is not None
        assert runtime.payload["mode"] == TradingMode.SIGNAL.value
        assert runtime.payload["auto_disabled"] is True

    await worker.shutdown()
    database.dispose()


@pytest.mark.asyncio
async def test_worker_end_to_end_execution_management_and_shared_kill_switch() -> None:
    series = load_candle_csv("sample_data/xauusd_synthetic.csv")
    now = candle_close_time(series[Timeframe.M5][-1])
    assert now.weekday() < 5 and 12 <= now.hour < 16

    base = MockBrokerAdapter.gold_demo(equity=Decimal("1000"), now=now)
    account = await base.get_account()
    symbols = await base.get_symbols()
    tick = await base.get_tick("XAUUSDm")
    candles = {("XAUUSDm", timeframe): values for timeframe, values in series.items()}
    broker = MockBrokerAdapter(
        account=account,
        symbols=symbols,
        ticks={"XAUUSDm": tick},
        candles=candles,
        clock=lambda: now,
        initially_connected=True,
    )
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    redis = FakeRedis()
    settings = Settings(
        _env_file=None,
        app_env="test",
        trading_env="demo",
        trading_mode="AUTO",
        broker_type="mock",
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://test",
        trade_sessions=["london", "new_york"],
        tp1_partial_close_enabled=False,
        break_even_enabled=False,
    )
    worker = GoldFlowWorker(
        settings=settings,
        database=database,
        broker=broker,
        redis=redis,
        notifier=NullNotifier(),
        clock=lambda: now,
        strategy_factory=_strategy_factory,
        instance_key="integration-worker",
        hostname="test-host",
    )

    await worker.startup()
    signal = await worker.scan_market()
    assert signal is not None and signal.action is SignalAction.LONG
    assert redis.lists[EXECUTION_QUEUE] == [str(signal.signal_id)]
    outcomes = await worker.execute_signals()
    assert len(outcomes) == 1
    assert outcomes[0].status is ExecutionStatus.FILLED
    assert broker.send_calls == 1
    assert redis.lists[EXECUTION_QUEUE] == []
    broker_position = (await broker.get_positions())[0]
    assert broker_position.take_profit == Decimal("2004.00")

    with database.session_scope() as session:
        assert session.scalar(select(func.count(Signal.signal_id))) >= 1
        assert session.scalar(select(func.count(Order.id))) == 1
        assert session.scalar(select(func.count(ExecutionAttempt.id))) == 1
        assert session.scalar(select(func.count(Position.id))) == 1
        assert session.scalar(select(func.count(Trade.id))) == 1
        risk = ConfigRepository(session).add_version(
            config_type="risk",
            version="worker-test-risk-1",
            payload={
                "tp1_partial_close_enabled": True,
                "tp1_close_fraction": "0.50",
                "break_even_enabled": True,
                "break_even_trigger_r": "1.0",
                "trailing_stop_enabled": False,
            },
            activate=True,
        )
        assert risk.is_active

    broker.seed_position(replace(broker_position, current_price=Decimal("2001.50")))
    assert await worker.manage_positions() == 1
    moved = (await broker.get_positions())[0]
    assert moved.stop_loss > moved.open_price

    broker.seed_position(replace(moved, current_price=Decimal("2002.60")))
    assert await worker.manage_positions() == 1
    reduced = (await broker.get_positions())[0]
    assert reduced.volume == broker_position.volume * Decimal("0.50")
    with database.session_scope() as session:
        events = set(session.scalars(select(TradeEvent.event_type)))
        assert {"BREAK_EVEN", "TP1_HIT"} <= events

    await worker.snapshot_account()
    await worker.snapshot_risk()
    await worker.aggregate_metrics(now.date())
    await worker.scheduled_notifications()
    await worker.scheduled_heartbeat()
    assert await redis.get(heartbeat_key("strategy_worker")) is not None
    assert redis.published
    with database.session_scope() as session:
        assert session.scalar(select(func.count(Notification.id))) > 0
        instance = session.scalar(
            select(BotInstance).where(BotInstance.instance_key == "integration-worker")
        )
        assert instance is not None and instance.status == "RUNNING"

        runtime = DurableRuntimeState(
            mode=TradingMode.AUTO,
            kill_switch=True,
            kill_switch_reason="integration safety drill",
            auto_disabled=False,
            updated_at=now,
        )
        ConfigRepository(session).add_version(
            config_type=RUNTIME_CONFIG_TYPE,
            version=f"kill-{uuid4()}",
            payload=runtime.to_payload(),
            activate=True,
        )

    second = replace(
        signal,
        signal_id=uuid4(),
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    await worker.persistence.save_signal(second, TradingMode.AUTO)
    assert await worker.execute_signals() == ()
    assert broker.send_calls == 1
    assert await broker.get_orders() == ()

    await worker.shutdown()
    with database.session_scope() as session:
        active_runtime = session.scalar(
            select(ConfigVersion).where(
                ConfigVersion.config_type == RUNTIME_CONFIG_TYPE,
                ConfigVersion.is_active.is_(True),
            )
        )
        assert active_runtime is not None and active_runtime.payload["kill_switch"] is True
    database.dispose()
