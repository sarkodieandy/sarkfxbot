"""Durable-shaped circuit-breaker state with explicit manual recovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from app.domain.models import AccountSnapshot
from app.risk.models import RiskLimits, RiskUsage


def _week_start(day: date, reset_weekday: int) -> date:
    return day - timedelta(days=(day.weekday() - reset_weekday) % 7)


@dataclass(frozen=True, slots=True)
class CircuitState:
    account_id: str
    day: date
    week_start: date
    daily_locked: bool = False
    weekly_locked: bool = False
    account_locked: bool = False
    kill_switch: bool = False
    manual_reenable_required: bool = False
    reason: str = ""
    version: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))

    @property
    def blocks_new_trades(self) -> bool:
        return (
            self.daily_locked
            or self.weekly_locked
            or self.account_locked
            or self.kill_switch
            or self.manual_reenable_required
        )


class CircuitStateStore(Protocol):
    async def load(self, account_id: str) -> CircuitState | None:
        raise RuntimeError("circuit state store load implementation is required")

    async def save(self, state: CircuitState, *, expected_version: int) -> CircuitState:
        raise RuntimeError("circuit state store save implementation is required")


class InMemoryCircuitStateStore:
    def __init__(self) -> None:
        self._states: dict[str, CircuitState] = {}
        self._lock = asyncio.Lock()

    async def load(self, account_id: str) -> CircuitState | None:
        async with self._lock:
            return self._states.get(account_id)

    async def save(self, state: CircuitState, *, expected_version: int) -> CircuitState:
        async with self._lock:
            current = self._states.get(state.account_id)
            current_version = current.version if current else 0
            if current_version != expected_version:
                raise RuntimeError("circuit state optimistic-lock conflict")
            persisted = replace(state, version=expected_version + 1)
            self._states[state.account_id] = persisted
            return persisted


class CircuitBreaker:
    def __init__(
        self,
        store: CircuitStateStore,
        limits: RiskLimits,
        *,
        daily_reset_hour_utc: int = 0,
        weekly_reset_weekday: int = 0,
    ) -> None:
        if not 0 <= daily_reset_hour_utc <= 23:
            raise ValueError("daily reset hour must be 0..23 UTC")
        if not 0 <= weekly_reset_weekday <= 6:
            raise ValueError("weekly reset weekday must be 0..6 (Monday..Sunday)")
        self._store = store
        self._limits = limits
        self._daily_reset_hour_utc = daily_reset_hour_utc
        self._weekly_reset_weekday = weekly_reset_weekday

    def _period_keys(self, now: datetime) -> tuple[date, date]:
        shifted_day = (now.astimezone(UTC) - timedelta(hours=self._daily_reset_hour_utc)).date()
        return shifted_day, _week_start(shifted_day, self._weekly_reset_weekday)

    async def get(self, account_id: str, now: datetime) -> CircuitState:
        now_utc = now.astimezone(UTC)
        day_key, week_key = self._period_keys(now_utc)
        state = await self._store.load(account_id)
        if state is None:
            return CircuitState(
                account_id=account_id,
                day=day_key,
                week_start=week_key,
                updated_at=now_utc,
            )
        daily_locked = state.daily_locked if state.day == day_key else False
        weekly_locked = state.weekly_locked if state.week_start == week_key else False
        if (
            state.day != day_key
            or state.week_start != week_key
            or daily_locked != state.daily_locked
            or weekly_locked != state.weekly_locked
        ):
            updated = replace(
                state,
                day=day_key,
                week_start=week_key,
                daily_locked=daily_locked,
                weekly_locked=weekly_locked,
                updated_at=now_utc,
            )
            return await self._store.save(updated, expected_version=state.version)
        return state

    async def evaluate(
        self,
        account: AccountSnapshot,
        usage: RiskUsage,
        now: datetime,
    ) -> CircuitState:
        state = await self.get(account.account_id, now)
        daily_used = usage.daily_realized_loss + usage.open_risk
        weekly_used = usage.weekly_realized_loss + usage.open_risk
        daily_locked = state.daily_locked or (
            account.equity <= 0 or daily_used >= account.equity * self._limits.maximum_daily_loss
        )
        weekly_locked = state.weekly_locked or (
            account.equity <= 0 or weekly_used >= account.equity * self._limits.maximum_weekly_loss
        )
        account_locked = state.account_locked
        if usage.peak_equity is not None:
            drawdown = max(
                Decimal("0"),
                (usage.peak_equity - account.equity) / usage.peak_equity,
            )
            account_locked = account_locked or (drawdown >= self._limits.maximum_account_drawdown)
        reasons: list[str] = []
        if daily_locked:
            reasons.append("DAILY_LOSS_LIMIT_REACHED")
        if weekly_locked:
            reasons.append("WEEKLY_LOSS_LIMIT_REACHED")
        if account_locked:
            reasons.append("ACCOUNT_DRAWDOWN_LIMIT_REACHED")
        updated = replace(
            state,
            daily_locked=daily_locked,
            weekly_locked=weekly_locked,
            account_locked=account_locked,
            manual_reenable_required=state.manual_reenable_required or account_locked,
            reason=", ".join(reasons) or state.reason,
            updated_at=now.astimezone(UTC),
        )
        if updated == state:
            return state
        return await self._store.save(updated, expected_version=state.version)

    async def activate_kill_switch(self, account_id: str, now: datetime) -> CircuitState:
        state = await self.get(account_id, now)
        updated = replace(
            state,
            kill_switch=True,
            reason="KILL_SWITCH_ACTIVE",
            updated_at=now.astimezone(UTC),
        )
        return await self._store.save(updated, expected_version=state.version)

    async def manual_reset(
        self,
        account_id: str,
        now: datetime,
        *,
        authorized: bool,
        clear_kill_switch: bool = False,
    ) -> CircuitState:
        if not authorized:
            raise PermissionError("manual circuit reset requires authorization")
        state = await self.get(account_id, now)
        updated = replace(
            state,
            daily_locked=False,
            weekly_locked=False,
            account_locked=False,
            kill_switch=False if clear_kill_switch else state.kill_switch,
            manual_reenable_required=False,
            reason="MANUAL_RESET",
            updated_at=now.astimezone(UTC),
        )
        return await self._store.save(updated, expected_version=state.version)
