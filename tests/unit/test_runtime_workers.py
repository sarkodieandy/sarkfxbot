from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.enums import TradingMode
from app.runtime_contract import (
    EVENT_CHANNEL,
    EXECUTION_QUEUE,
    DurableRuntimeState,
    event_message,
    heartbeat_key,
    parse_event_message,
)
from app.workers.lease import LeaseRunStatus, RedisLeaseManager


class LeaseRedis:
    def __init__(self, *, acquired: bool = True, fail_eval: bool = False) -> None:
        self.acquired = acquired
        self.fail_eval = fail_eval
        self.values: dict[str, str] = {}

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        assert nx and px is not None
        if not self.acquired or name in self.values:
            return False
        self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        del script
        if self.fail_eval:
            raise ConnectionError("simulated Redis loss")
        assert numkeys == 1
        key, token = keys_and_args
        if self.values.get(key) != token:
            return 0
        del self.values[key]
        return 1


@pytest.mark.asyncio
async def test_redis_lease_statuses_release_and_exception_containment() -> None:
    async def value() -> int:
        return 42

    unavailable = await RedisLeaseManager(None).run("job", value, ttl_seconds=5)
    assert unavailable.status is LeaseRunStatus.LOCK_UNAVAILABLE

    locked = await RedisLeaseManager(LeaseRedis(acquired=False)).run("job", value, ttl_seconds=5)
    assert locked.status is LeaseRunStatus.LOCKED

    redis = LeaseRedis()
    completed = await RedisLeaseManager(redis).run("job", value, ttl_seconds=5)
    assert completed.status is LeaseRunStatus.COMPLETED and completed.value == 42
    assert redis.values == {}

    async def fail() -> int:
        raise ValueError("contained")

    failed = await RedisLeaseManager(redis).run("job", fail, ttl_seconds=5)
    assert failed.status is LeaseRunStatus.FAILED and failed.error_type == "ValueError"
    with pytest.raises(ValueError, match="contained"):
        await RedisLeaseManager(redis).run("job", fail, ttl_seconds=5, contain_exceptions=False)

    release_failure = LeaseRedis(fail_eval=True)
    lease = await RedisLeaseManager(release_failure).acquire("job", ttl_seconds=5)
    assert lease is not None and await lease.release() is False
    with pytest.raises(ValueError, match="TTL"):
        await RedisLeaseManager(redis).acquire("job", ttl_seconds=0)


def test_runtime_state_and_event_contract_validation() -> None:
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    state = DurableRuntimeState(
        mode=TradingMode.SEMI_AUTO,
        kill_switch=True,
        kill_switch_reason="operator drill",
        auto_disabled=False,
        updated_at=now,
    )
    restored = DurableRuntimeState.from_payload(state.to_payload(), default_mode=TradingMode.SIGNAL)
    assert restored == state
    assert heartbeat_key("execution_worker").endswith(":execution_worker")
    with pytest.raises(ValueError, match="unknown worker"):
        heartbeat_key("unknown")
    with pytest.raises(ValueError, match="reason"):
        DurableRuntimeState(TradingMode.SIGNAL, True, None, False, now)
    with pytest.raises(ValueError, match="AUTO"):
        DurableRuntimeState(TradingMode.AUTO, False, None, True, now)
    with pytest.raises(ValueError, match="timezone"):
        DurableRuntimeState(
            TradingMode.SIGNAL,
            False,
            None,
            False,
            datetime(2026, 8, 26),
        )
    with pytest.raises(ValueError, match="booleans"):
        DurableRuntimeState.from_payload({"kill_switch": "true"}, default_mode=TradingMode.SIGNAL)

    encoded = event_message("signal", {"id": "one"})
    event, payload = parse_event_message(encoded.encode())
    assert EVENT_CHANNEL == "goldflow:events"
    assert EXECUTION_QUEUE == "goldflow:execution:signals"
    assert event == "SIGNAL" and payload == {"id": "one"}
    for invalid in ("[]", "{}", '{"event":"X","payload":[]}'):
        with pytest.raises(ValueError):
            parse_event_message(invalid)
    with pytest.raises(ValueError, match="event names"):
        event_message("", {})
