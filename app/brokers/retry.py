"""Bounded exponential retry facade for connection and idempotent reads only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar

from app.brokers.base import (
    BrokerAdapter,
    BrokerHealth,
    IndeterminateBrokerResult,
)
from app.domain.enums import Direction, Timeframe
from app.domain.errors import BrokerUnavailableError
from app.domain.models import (
    AccountSnapshot,
    BrokerOrder,
    BrokerPosition,
    Candle,
    ExecutionReport,
    OrderCheckResult,
    OrderRequest,
    SymbolSpecification,
    Tick,
)

ResultT = TypeVar("ResultT")
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ReadRetryPolicy:
    maximum_attempts: int = 3
    initial_delay_seconds: float = 0.25
    maximum_delay_seconds: float = 2.0
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.maximum_attempts < 1:
            raise ValueError("maximum retry attempts must be positive")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial retry delay cannot be negative")
        if self.maximum_delay_seconds < self.initial_delay_seconds:
            raise ValueError("maximum retry delay cannot be below initial delay")
        if self.multiplier < 1:
            raise ValueError("retry multiplier must be at least one")


class RetryingBrokerAdapter(BrokerAdapter):
    """Retry safe reads while forwarding every broker mutation exactly once."""

    def __init__(
        self,
        delegate: BrokerAdapter,
        *,
        policy: ReadRetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._delegate = delegate
        self._policy = policy or ReadRetryPolicy()
        self._sleep = sleep

    async def _read(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        delay = self._policy.initial_delay_seconds
        for attempt in range(1, self._policy.maximum_attempts + 1):
            try:
                return await operation()
            except (BrokerUnavailableError, IndeterminateBrokerResult, TimeoutError):
                if attempt == self._policy.maximum_attempts:
                    raise
                await self._sleep(delay)
                delay = min(delay * self._policy.multiplier, self._policy.maximum_delay_seconds)
        raise RuntimeError("bounded read retry loop ended unexpectedly")

    async def connect(self) -> None:
        await self._read(self._delegate.connect)

    async def disconnect(self) -> None:
        await self._delegate.disconnect()

    async def health_check(self) -> BrokerHealth:
        delay = self._policy.initial_delay_seconds
        last = BrokerHealth(False, False, False, "health check did not run")
        for attempt in range(1, self._policy.maximum_attempts + 1):
            try:
                last = await self._delegate.health_check()
                if last.healthy and last.connected:
                    return last
            except (BrokerUnavailableError, IndeterminateBrokerResult, TimeoutError):
                if attempt == self._policy.maximum_attempts:
                    raise
            if attempt < self._policy.maximum_attempts:
                await self._sleep(delay)
                delay = min(delay * self._policy.multiplier, self._policy.maximum_delay_seconds)
        return last

    async def get_account(self) -> AccountSnapshot:
        return await self._read(self._delegate.get_account)

    async def get_symbols(self) -> tuple[SymbolSpecification, ...]:
        return await self._read(self._delegate.get_symbols)

    async def resolve_symbol(self, canonical_symbol: str) -> SymbolSpecification:
        return await self._read(lambda: self._delegate.resolve_symbol(canonical_symbol))

    async def get_tick(self, symbol: str) -> Tick:
        return await self._read(lambda: self._delegate.get_tick(symbol))

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        return await self._read(
            lambda: self._delegate.get_candles(symbol, timeframe, count, closed_only=closed_only)
        )

    async def get_positions(self, symbol: str | None = None) -> tuple[BrokerPosition, ...]:
        return await self._read(lambda: self._delegate.get_positions(symbol))

    async def get_orders(self, symbol: str | None = None) -> tuple[BrokerOrder, ...]:
        return await self._read(lambda: self._delegate.get_orders(symbol))

    async def is_market_open(self, symbol: str) -> bool:
        return await self._read(lambda: self._delegate.is_market_open(symbol))

    async def calculate_margin(self, request: OrderRequest, price: Decimal) -> Decimal:
        return await self._read(lambda: self._delegate.calculate_margin(request, price))

    async def calculate_profit(
        self,
        symbol: str,
        direction: Direction,
        volume: Decimal,
        open_price: Decimal,
        close_price: Decimal,
    ) -> Decimal:
        return await self._read(
            lambda: self._delegate.calculate_profit(
                symbol, direction, volume, open_price, close_price
            )
        )

    async def validate_order(self, request: OrderRequest) -> OrderCheckResult:
        return await self._read(lambda: self._delegate.validate_order(request))

    async def place_market_order(self, request: OrderRequest) -> ExecutionReport:
        return await self._delegate.place_market_order(request)

    async def place_pending_order(self, request: OrderRequest) -> ExecutionReport:
        return await self._delegate.place_pending_order(request)

    async def modify_position(
        self,
        ticket: str,
        *,
        stop_loss: Decimal,
        take_profit: Decimal | None,
    ) -> bool:
        return await self._delegate.modify_position(
            ticket, stop_loss=stop_loss, take_profit=take_profit
        )

    async def close_position(self, ticket: str, volume: Decimal | None = None) -> ExecutionReport:
        return await self._delegate.close_position(ticket, volume)

    async def cancel_order(self, ticket: str) -> bool:
        return await self._delegate.cancel_order(ticket)

    async def history_deals(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        return await self._read(lambda: self._delegate.history_deals(start, end))

    async def history_orders(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        return await self._read(lambda: self._delegate.history_orders(start, end))
