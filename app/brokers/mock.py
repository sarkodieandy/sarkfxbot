"""Deterministic in-memory broker used by CI, backtests, and recovery tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.brokers.base import BrokerAdapter, BrokerHealth, IndeterminateBrokerResult
from app.brokers.symbols import resolve_symbol
from app.domain.enums import AccountType, Direction, OrderStatus, OrderType, Timeframe
from app.domain.errors import BrokerError, BrokerUnavailableError
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


class MockSendBehavior(StrEnum):
    NORMAL = "NORMAL"
    REJECT = "REJECT"
    TIMEOUT_BEFORE_ACCEPT = "TIMEOUT_BEFORE_ACCEPT"
    TIMEOUT_AFTER_ACCEPT = "TIMEOUT_AFTER_ACCEPT"


class MockBrokerAdapter(BrokerAdapter):
    """A stateful fake with an injected clock and scripted order-send failures."""

    def __init__(
        self,
        *,
        account: AccountSnapshot,
        symbols: Iterable[SymbolSpecification],
        ticks: Mapping[str, Tick] | None = None,
        candles: Mapping[tuple[str, Timeframe], Iterable[Candle]] | None = None,
        clock: Callable[[], datetime] | None = None,
        initially_connected: bool = False,
        market_open: bool = True,
        send_behaviors: Iterable[MockSendBehavior] = (),
    ) -> None:
        self._account = account
        self._symbols = {spec.name: spec for spec in symbols}
        self._ticks = dict(ticks or {})
        self._candles = {
            key: tuple(sorted(values, key=lambda candle: candle.timestamp))
            for key, values in (candles or {}).items()
        }
        self._clock = clock or (lambda: datetime.now(UTC))
        self._connected = initially_connected
        self._healthy = True
        self._trading_allowed = True
        self._market_open = market_open
        self._position_modification_allowed = True
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._reports: dict[str, ExecutionReport] = {}
        self._send_behaviors = deque(send_behaviors)
        self._ticket_sequence = 0
        self._deals: list[dict[str, Any]] = []
        self._order_history: list[dict[str, Any]] = []
        self.send_calls = 0
        self.order_check_calls = 0
        self.modify_calls = 0
        self.close_calls = 0

    @classmethod
    def gold_demo(
        cls,
        *,
        equity: Decimal = Decimal("1000"),
        symbol_name: str = "XAUUSDm",
        bid: Decimal = Decimal("2000.00"),
        ask: Decimal = Decimal("2000.20"),
        now: datetime | None = None,
        minimum_volume: Decimal = Decimal("0.01"),
        send_behaviors: Iterable[MockSendBehavior] = (),
    ) -> MockBrokerAdapter:
        timestamp = now or datetime.now(UTC)
        spec = SymbolSpecification(
            name=symbol_name,
            canonical_symbol="XAUUSD",
            base_currency="XAU",
            quote_currency="USD",
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.01"),
            tick_value=Decimal("1"),
            contract_size=Decimal("100"),
            volume_min=minimum_volume,
            volume_max=Decimal("100"),
            volume_step=minimum_volume,
            stops_level_points=10,
        )
        account = AccountSnapshot(
            broker="Mock Exness",
            platform="MOCK",
            account_id="demo-1",
            server="mock-demo",
            currency="USD",
            balance=equity,
            equity=equity,
            margin=Decimal("0"),
            free_margin=equity,
            leverage=500,
            account_type=AccountType.DEMO,
            timestamp=timestamp,
        )
        return cls(
            account=account,
            symbols=(spec,),
            ticks={symbol_name: Tick(symbol_name, bid, ask, timestamp)},
            clock=lambda: timestamp,
            initially_connected=True,
            send_behaviors=send_behaviors,
        )

    def set_health(
        self,
        *,
        connected: bool | None = None,
        healthy: bool | None = None,
        trading_allowed: bool | None = None,
    ) -> None:
        if connected is not None:
            self._connected = connected
        if healthy is not None:
            self._healthy = healthy
        if trading_allowed is not None:
            self._trading_allowed = trading_allowed

    def set_market_open(self, value: bool) -> None:
        self._market_open = value

    def set_position_modification_allowed(self, value: bool) -> None:
        self._position_modification_allowed = value

    def set_tick(self, tick: Tick) -> None:
        self._ticks[tick.symbol] = tick

    def set_account(self, account: AccountSnapshot) -> None:
        self._account = account

    def seed_position(self, position: BrokerPosition) -> None:
        self._positions[position.ticket] = position

    def corrupt_position_protection(
        self,
        ticket: str,
        *,
        stop_loss: Decimal,
        take_profit: Decimal | None,
    ) -> None:
        """Explicit fault injection for post-fill protection tests."""

        position = self._positions.get(ticket)
        if position is None:
            raise KeyError(f"mock position {ticket} does not exist")
        self._positions[ticket] = replace(position, stop_loss=stop_loss, take_profit=take_profit)

    def seed_order(self, order: BrokerOrder) -> None:
        self._orders[order.ticket] = order

    def queue_send_behavior(self, behavior: MockSendBehavior) -> None:
        self._send_behaviors.append(behavior)

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def health_check(self) -> BrokerHealth:
        return BrokerHealth(
            healthy=self._connected and self._healthy,
            connected=self._connected,
            trading_allowed=self._connected and self._healthy and self._trading_allowed,
            message="ok" if self._connected and self._healthy else "mock broker unavailable",
        )

    def _require_connection(self) -> None:
        if not self._connected or not self._healthy:
            raise BrokerUnavailableError("mock broker is disconnected or unhealthy")

    async def get_account(self) -> AccountSnapshot:
        self._require_connection()
        return replace(self._account, timestamp=self._clock())

    async def get_symbols(self) -> tuple[SymbolSpecification, ...]:
        self._require_connection()
        return tuple(self._symbols.values())

    async def resolve_symbol(self, canonical_symbol: str) -> SymbolSpecification:
        return resolve_symbol(canonical_symbol, await self.get_symbols())

    async def get_tick(self, symbol: str) -> Tick:
        self._require_connection()
        try:
            return self._ticks[symbol]
        except KeyError as exc:
            raise BrokerError(f"no tick is available for {symbol}") from exc

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        self._require_connection()
        if count <= 0:
            raise ValueError("candle count must be positive")
        candles = self._candles.get((symbol, timeframe), ())
        if closed_only:
            candles = tuple(candle for candle in candles if candle.complete)
        return tuple(candles[-count:])

    async def get_positions(self, symbol: str | None = None) -> tuple[BrokerPosition, ...]:
        self._require_connection()
        positions = tuple(self._positions.values())
        return (
            positions
            if symbol is None
            else tuple(item for item in positions if item.symbol == symbol)
        )

    async def get_orders(self, symbol: str | None = None) -> tuple[BrokerOrder, ...]:
        self._require_connection()
        orders = tuple(self._orders.values())
        return orders if symbol is None else tuple(item for item in orders if item.symbol == symbol)

    async def is_market_open(self, symbol: str) -> bool:
        self._require_connection()
        return self._market_open and symbol in self._symbols

    async def calculate_margin(self, request: OrderRequest, price: Decimal) -> Decimal:
        self._require_connection()
        spec = self._spec(request.symbol)
        leverage = Decimal(self._account.leverage)
        if leverage <= 0:
            raise BrokerError("account leverage is unavailable")
        return price * spec.contract_size * request.volume / leverage

    async def calculate_profit(
        self,
        symbol: str,
        direction: Direction,
        volume: Decimal,
        open_price: Decimal,
        close_price: Decimal,
    ) -> Decimal:
        self._require_connection()
        spec = self._spec(symbol)
        if spec.tick_value <= 0:
            raise BrokerError(f"tick value is unavailable for {symbol}")
        ticks = (close_price - open_price) / spec.tick_size
        return ticks * spec.tick_value * volume * Decimal(direction.sign)

    def _spec(self, symbol: str) -> SymbolSpecification:
        try:
            return self._symbols[symbol]
        except KeyError as exc:
            raise BrokerError(f"unknown broker symbol {symbol}") from exc

    async def validate_order(self, request: OrderRequest) -> OrderCheckResult:
        self.order_check_calls += 1
        reasons: list[str] = []
        health = await self.health_check()
        if not health.trading_allowed:
            reasons.append("BROKER_UNHEALTHY")
        try:
            spec = self._spec(request.symbol)
            tick = await self.get_tick(request.symbol)
        except BrokerError:
            return OrderCheckResult(
                False, ("SYMBOL_OR_TICK_UNAVAILABLE",), broker_code="MOCK_INVALID"
            )
        if not await self.is_market_open(request.symbol):
            reasons.append("MARKET_CLOSED")
        if request.volume < spec.volume_min or request.volume > spec.volume_max:
            reasons.append("INVALID_VOLUME")
        elif (request.volume - spec.volume_min) % spec.volume_step != 0:
            reasons.append("INVALID_VOLUME_STEP")
        price = request.requested_price or (
            tick.ask if request.direction is Direction.LONG else tick.bid
        )
        minimum_distance = spec.point * spec.stops_level_points
        if request.direction is Direction.LONG:
            if request.stop_loss >= price or any(tp <= price for tp in request.take_profits):
                reasons.append("INVALID_PROTECTION_SIDE")
            if price - request.stop_loss < minimum_distance:
                reasons.append("STOP_TOO_CLOSE")
        else:
            if request.stop_loss <= price or any(tp >= price for tp in request.take_profits):
                reasons.append("INVALID_PROTECTION_SIDE")
            if request.stop_loss - price < minimum_distance:
                reasons.append("STOP_TOO_CLOSE")
        margin = await self.calculate_margin(request, price)
        if margin > self._account.free_margin:
            reasons.append("INSUFFICIENT_MARGIN")
        return OrderCheckResult(
            accepted=not reasons,
            reasons=tuple(reasons),
            margin_required=margin,
            broker_code="MOCK_OK" if not reasons else "MOCK_REJECT",
        )

    def _next_ticket(self, prefix: str) -> str:
        self._ticket_sequence += 1
        return f"{prefix}{self._ticket_sequence}"

    async def _place(self, request: OrderRequest, *, pending: bool) -> ExecutionReport:
        self.send_calls += 1
        existing = self._reports.get(request.idempotency_key)
        if existing is not None:
            return existing
        behavior = (
            self._send_behaviors.popleft() if self._send_behaviors else MockSendBehavior.NORMAL
        )
        if behavior is MockSendBehavior.TIMEOUT_BEFORE_ACCEPT:
            raise IndeterminateBrokerResult("mock response timed out before acceptance was known")
        check = await self.validate_order(request)
        if not check.accepted or behavior is MockSendBehavior.REJECT:
            return ExecutionReport(
                success=False,
                idempotency_key=request.idempotency_key,
                broker_ticket=None,
                requested_price=request.requested_price,
                executed_price=None,
                volume=request.volume,
                broker_code="MOCK_REJECT",
                message=", ".join(check.reasons) or "scripted rejection",
                submitted_at=self._clock(),
            )
        tick = await self.get_tick(request.symbol)
        price = request.requested_price or (
            tick.ask if request.direction is Direction.LONG else tick.bid
        )
        ticket = self._next_ticket("O" if pending else "P")
        report = ExecutionReport(
            success=True,
            idempotency_key=request.idempotency_key,
            broker_ticket=ticket,
            requested_price=request.requested_price,
            executed_price=None if pending else price,
            volume=request.volume,
            broker_code="MOCK_PLACED" if pending else "MOCK_FILLED",
            message="pending order placed" if pending else "market order filled",
            submitted_at=self._clock(),
        )
        self._reports[request.idempotency_key] = report
        if pending:
            self._orders[ticket] = BrokerOrder(
                ticket=ticket,
                symbol=request.symbol,
                direction=request.direction,
                order_type=request.order_type,
                volume=request.volume,
                price=price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profits[-1],
                status=OrderStatus.PENDING,
                created_at=self._clock(),
                idempotency_key=request.idempotency_key,
            )
        else:
            self._positions[ticket] = BrokerPosition(
                ticket=ticket,
                symbol=request.symbol,
                direction=request.direction,
                volume=request.volume,
                open_price=price,
                current_price=price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profits[-1],
                profit=Decimal("0"),
                opened_at=self._clock(),
                strategy_id=request.strategy_id,
                signal_id=request.signal_id,
            )
            self._deals.append(
                {"ticket": ticket, "time": self._clock(), "entry": "IN", "price": price}
            )
        self._order_history.append(
            {"ticket": ticket, "time": self._clock(), "idempotency_key": request.idempotency_key}
        )
        if behavior is MockSendBehavior.TIMEOUT_AFTER_ACCEPT:
            raise IndeterminateBrokerResult("mock order accepted but response was lost")
        return report

    async def place_market_order(self, request: OrderRequest) -> ExecutionReport:
        if request.order_type is not OrderType.MARKET:
            raise BrokerError("market placement requires a MARKET request")
        return await self._place(request, pending=False)

    async def place_pending_order(self, request: OrderRequest) -> ExecutionReport:
        if request.order_type is OrderType.MARKET:
            raise BrokerError("pending placement requires LIMIT or STOP")
        if request.requested_price is None:
            raise BrokerError("pending orders require a requested price")
        return await self._place(request, pending=True)

    async def modify_position(
        self,
        ticket: str,
        *,
        stop_loss: Decimal,
        take_profit: Decimal | None,
    ) -> bool:
        self._require_connection()
        self.modify_calls += 1
        position = self._positions.get(ticket)
        if position is None or stop_loss <= 0 or not self._position_modification_allowed:
            return False
        self._positions[ticket] = replace(position, stop_loss=stop_loss, take_profit=take_profit)
        return True

    async def close_position(self, ticket: str, volume: Decimal | None = None) -> ExecutionReport:
        self._require_connection()
        self.close_calls += 1
        position = self._positions.get(ticket)
        if position is None:
            return ExecutionReport(
                False,
                f"close:{ticket}",
                ticket,
                None,
                None,
                Decimal("0"),
                broker_code="MOCK_NOT_FOUND",
                message="position not found",
                submitted_at=self._clock(),
            )
        close_volume = volume or position.volume
        if close_volume <= 0 or close_volume > position.volume:
            raise BrokerError("close volume is outside the open position volume")
        if close_volume == position.volume:
            del self._positions[ticket]
        else:
            self._positions[ticket] = replace(position, volume=position.volume - close_volume)
        self._deals.append(
            {"ticket": ticket, "time": self._clock(), "entry": "OUT", "volume": close_volume}
        )
        return ExecutionReport(
            True,
            f"close:{ticket}:{self.close_calls}",
            ticket,
            position.current_price,
            position.current_price,
            close_volume,
            broker_code="MOCK_CLOSED",
            message="position closed",
            submitted_at=self._clock(),
        )

    async def cancel_order(self, ticket: str) -> bool:
        self._require_connection()
        return self._orders.pop(ticket, None) is not None

    async def history_deals(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        self._require_connection()
        return tuple(item for item in self._deals if start <= item["time"] <= end)

    async def history_orders(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        self._require_connection()
        return tuple(item for item in self._order_history if start <= item["time"] <= end)
