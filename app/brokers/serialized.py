"""Serialized facade for broker SDKs that cannot safely handle concurrent access."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar

from app.brokers.base import BrokerAdapter, BrokerHealth
from app.domain.enums import Direction, Timeframe
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


class SerializedBrokerAdapter(BrokerAdapter):
    """Serialize every call through one lock while preserving the broker contract."""

    def __init__(self, delegate: BrokerAdapter) -> None:
        self._delegate = delegate
        self._lock = asyncio.Lock()

    async def _run(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        async with self._lock:
            return await operation()

    async def connect(self) -> None:
        await self._run(self._delegate.connect)

    async def disconnect(self) -> None:
        await self._run(self._delegate.disconnect)

    async def health_check(self) -> BrokerHealth:
        return await self._run(self._delegate.health_check)

    async def get_account(self) -> AccountSnapshot:
        return await self._run(self._delegate.get_account)

    async def get_symbols(self) -> tuple[SymbolSpecification, ...]:
        return await self._run(self._delegate.get_symbols)

    async def resolve_symbol(self, canonical_symbol: str) -> SymbolSpecification:
        return await self._run(lambda: self._delegate.resolve_symbol(canonical_symbol))

    async def get_tick(self, symbol: str) -> Tick:
        return await self._run(lambda: self._delegate.get_tick(symbol))

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        return await self._run(
            lambda: self._delegate.get_candles(symbol, timeframe, count, closed_only=closed_only)
        )

    async def get_positions(self, symbol: str | None = None) -> tuple[BrokerPosition, ...]:
        return await self._run(lambda: self._delegate.get_positions(symbol))

    async def get_orders(self, symbol: str | None = None) -> tuple[BrokerOrder, ...]:
        return await self._run(lambda: self._delegate.get_orders(symbol))

    async def is_market_open(self, symbol: str) -> bool:
        return await self._run(lambda: self._delegate.is_market_open(symbol))

    async def calculate_margin(self, request: OrderRequest, price: Decimal) -> Decimal:
        return await self._run(lambda: self._delegate.calculate_margin(request, price))

    async def calculate_profit(
        self,
        symbol: str,
        direction: Direction,
        volume: Decimal,
        open_price: Decimal,
        close_price: Decimal,
    ) -> Decimal:
        return await self._run(
            lambda: self._delegate.calculate_profit(
                symbol, direction, volume, open_price, close_price
            )
        )

    async def validate_order(self, request: OrderRequest) -> OrderCheckResult:
        return await self._run(lambda: self._delegate.validate_order(request))

    async def place_market_order(self, request: OrderRequest) -> ExecutionReport:
        return await self._run(lambda: self._delegate.place_market_order(request))

    async def place_pending_order(self, request: OrderRequest) -> ExecutionReport:
        return await self._run(lambda: self._delegate.place_pending_order(request))

    async def modify_position(
        self,
        ticket: str,
        *,
        stop_loss: Decimal,
        take_profit: Decimal | None,
    ) -> bool:
        return await self._run(
            lambda: self._delegate.modify_position(
                ticket, stop_loss=stop_loss, take_profit=take_profit
            )
        )

    async def close_position(self, ticket: str, volume: Decimal | None = None) -> ExecutionReport:
        return await self._run(lambda: self._delegate.close_position(ticket, volume))

    async def cancel_order(self, ticket: str) -> bool:
        return await self._run(lambda: self._delegate.cancel_order(ticket))

    async def history_deals(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        return await self._run(lambda: self._delegate.history_deals(start, end))

    async def history_orders(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        return await self._run(lambda: self._delegate.history_orders(start, end))
