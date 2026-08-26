"""Broker abstraction used by execution, risk, and recovery services."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import Direction, Timeframe
from app.domain.errors import BrokerError, BrokerOperationUnsupported
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


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    """A non-throwing health result suitable for readiness and risk gates."""

    healthy: bool
    connected: bool
    trading_allowed: bool
    message: str = ""


class IndeterminateBrokerResult(BrokerError):
    """The broker may have accepted a mutation whose response was lost."""


class UnprotectedPositionError(BrokerError):
    """Broker state contains exposure without a valid stop loss."""

    def __init__(self, ticket: str, symbol: str) -> None:
        self.ticket = ticket
        self.symbol = symbol
        super().__init__(f"broker position {ticket} on {symbol} has no stop loss")


def broker_correlation_key(idempotency_key: str) -> str:
    """Return a non-secret MT5-comment-sized correlation token."""

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
    return f"gf:{digest}"


class BrokerAdapter(ABC):
    """Async, broker-neutral interface.

    Concrete adapters are responsible for keeping blocking SDK calls away from the
    event loop. Mutating methods are deliberately separate from read methods so an
    executor can apply stricter retry semantics to them.
    """

    @abstractmethod
    async def connect(self) -> None:
        raise BrokerOperationUnsupported("connect is not implemented by this broker adapter")

    @abstractmethod
    async def disconnect(self) -> None:
        raise BrokerOperationUnsupported("disconnect is not implemented by this broker adapter")

    @abstractmethod
    async def health_check(self) -> BrokerHealth:
        raise BrokerOperationUnsupported("health_check is not implemented by this broker adapter")

    @abstractmethod
    async def get_account(self) -> AccountSnapshot:
        raise BrokerOperationUnsupported("get_account is not implemented by this broker adapter")

    @abstractmethod
    async def get_symbols(self) -> tuple[SymbolSpecification, ...]:
        raise BrokerOperationUnsupported("get_symbols is not implemented by this broker adapter")

    @abstractmethod
    async def resolve_symbol(self, canonical_symbol: str) -> SymbolSpecification:
        raise BrokerOperationUnsupported("resolve_symbol is not implemented by this broker adapter")

    @abstractmethod
    async def get_tick(self, symbol: str) -> Tick:
        raise BrokerOperationUnsupported("get_tick is not implemented by this broker adapter")

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        raise BrokerOperationUnsupported("get_candles is not implemented by this broker adapter")

    @abstractmethod
    async def get_positions(self, symbol: str | None = None) -> tuple[BrokerPosition, ...]:
        raise BrokerOperationUnsupported("get_positions is not implemented by this broker adapter")

    @abstractmethod
    async def get_orders(self, symbol: str | None = None) -> tuple[BrokerOrder, ...]:
        raise BrokerOperationUnsupported("get_orders is not implemented by this broker adapter")

    @abstractmethod
    async def is_market_open(self, symbol: str) -> bool:
        raise BrokerOperationUnsupported("is_market_open is not implemented by this broker adapter")

    @abstractmethod
    async def calculate_margin(self, request: OrderRequest, price: Decimal) -> Decimal:
        raise BrokerOperationUnsupported(
            "calculate_margin is not implemented by this broker adapter"
        )

    @abstractmethod
    async def calculate_profit(
        self,
        symbol: str,
        direction: Direction,
        volume: Decimal,
        open_price: Decimal,
        close_price: Decimal,
    ) -> Decimal:
        raise BrokerOperationUnsupported(
            "calculate_profit is not implemented by this broker adapter"
        )

    @abstractmethod
    async def validate_order(self, request: OrderRequest) -> OrderCheckResult:
        raise BrokerOperationUnsupported("validate_order is not implemented by this broker adapter")

    @abstractmethod
    async def place_market_order(self, request: OrderRequest) -> ExecutionReport:
        raise BrokerOperationUnsupported(
            "place_market_order is not implemented by this broker adapter"
        )

    @abstractmethod
    async def place_pending_order(self, request: OrderRequest) -> ExecutionReport:
        raise BrokerOperationUnsupported(
            "place_pending_order is not implemented by this broker adapter"
        )

    @abstractmethod
    async def modify_position(
        self,
        ticket: str,
        *,
        stop_loss: Decimal,
        take_profit: Decimal | None,
    ) -> bool:
        raise BrokerOperationUnsupported(
            "modify_position is not implemented by this broker adapter"
        )

    @abstractmethod
    async def close_position(self, ticket: str, volume: Decimal | None = None) -> ExecutionReport:
        raise BrokerOperationUnsupported("close_position is not implemented by this broker adapter")

    @abstractmethod
    async def cancel_order(self, ticket: str) -> bool:
        raise BrokerOperationUnsupported("cancel_order is not implemented by this broker adapter")

    @abstractmethod
    async def history_deals(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        raise BrokerOperationUnsupported("history_deals is not implemented by this broker adapter")

    @abstractmethod
    async def history_orders(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        raise BrokerOperationUnsupported("history_orders is not implemented by this broker adapter")
