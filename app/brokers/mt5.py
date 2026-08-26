"""MetaTrader 5 adapter with lazy import and single-thread SDK confinement."""

from __future__ import annotations

import asyncio
import importlib
import platform
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any

from app.brokers.base import (
    BrokerAdapter,
    BrokerHealth,
    IndeterminateBrokerResult,
    UnprotectedPositionError,
    broker_correlation_key,
)
from app.brokers.symbols import canonicalize_symbol, resolve_symbol
from app.domain.enums import AccountType, Direction, OrderStatus, OrderType, Timeframe
from app.domain.errors import BrokerError, BrokerOperationUnsupported, BrokerUnavailableError
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


def _decimal(value: object, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


class MT5BrokerAdapter(BrokerAdapter):
    """Official MetaTrader5 package adapter.

    Passing ``mt5_module`` is supported solely for deterministic contract tests. In
    normal use, the official package is imported only when ``connect`` is called.
    """

    def __init__(
        self,
        *,
        login: int,
        server: str,
        password: str,
        terminal_path: str | None = None,
        broker_name: str = "Exness",
        mt5_module: Any | None = None,
    ) -> None:
        if login <= 0 or not server.strip() or not password:
            raise ValueError("MT5 login, server, and password are required")
        self._login = login
        self._server = server
        self._password = password
        self._terminal_path = terminal_path
        self._broker_name = broker_name
        self._module = mt5_module
        self._connected = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="goldflow-mt5")

    def _load_module(self) -> Any:
        if self._module is not None:
            return self._module
        if platform.system() != "Windows":
            raise BrokerOperationUnsupported(
                "MetaTrader5 requires a supported Windows Python environment "
                "and an installed terminal"
            )
        try:
            self._module = importlib.import_module("MetaTrader5")
        except ImportError as exc:
            raise BrokerOperationUnsupported(
                "MetaTrader5 is not installed; install the optional 'mt5' dependency on Windows"
            ) from exc
        return self._module

    async def _call(self, name: str, *args: object, **kwargs: object) -> Any:
        module = self._load_module()
        function = getattr(module, name, None)
        if function is None:
            raise BrokerOperationUnsupported(f"MetaTrader5 does not expose {name}")
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._pool, partial(function, *args, **kwargs))
        except TimeoutError as exc:
            raise IndeterminateBrokerResult(f"MT5 {name} timed out") from exc
        except IndeterminateBrokerResult:
            raise
        except (ConnectionError, OSError) as exc:
            raise BrokerUnavailableError(f"MT5 {name} connection failed") from exc
        except Exception as exc:
            raise BrokerError(f"MT5 {name} failed: {type(exc).__name__}") from exc

    async def _last_error_text(self) -> str:
        try:
            result = await self._call("last_error")
        except BrokerError:
            return "unavailable error detail"
        return str(result)[:200]

    async def connect(self) -> None:
        kwargs: dict[str, object] = {
            "login": self._login,
            "server": self._server,
            "password": self._password,
        }
        if self._terminal_path:
            kwargs["path"] = self._terminal_path
        initialized = bool(await self._call("initialize", **kwargs))
        if not initialized:
            detail = await self._last_error_text()
            raise BrokerUnavailableError(f"MT5 initialize failed: {detail}")
        account = await self._call("account_info")
        if account is None or int(getattr(account, "login", 0)) != self._login:
            await self._call("shutdown")
            raise BrokerUnavailableError("MT5 connected to an unexpected or unavailable account")
        self._connected = True

    async def disconnect(self) -> None:
        if self._connected:
            await self._call("shutdown")
        self._connected = False

    async def health_check(self) -> BrokerHealth:
        if not self._connected:
            return BrokerHealth(False, False, False, "MT5 is not connected")
        try:
            terminal = await self._call("terminal_info")
            account = await self._call("account_info")
        except BrokerError as exc:
            return BrokerHealth(False, False, False, str(exc))
        connected = bool(terminal is not None and getattr(terminal, "connected", False))
        trade_allowed = bool(
            connected
            and account is not None
            and getattr(terminal, "trade_allowed", False)
            and getattr(account, "trade_allowed", True)
        )
        return BrokerHealth(
            healthy=connected and account is not None,
            connected=connected,
            trading_allowed=trade_allowed,
            message="ok" if trade_allowed else "MT5 trading is not currently allowed",
        )

    def _require_connected(self) -> None:
        if not self._connected:
            raise BrokerUnavailableError("MT5 is not connected")

    def _account_type(self, raw: object) -> AccountType:
        module = self._load_module()
        trade_mode = getattr(raw, "trade_mode", None)
        if trade_mode == getattr(module, "ACCOUNT_TRADE_MODE_DEMO", object()):
            return AccountType.DEMO
        if trade_mode == getattr(module, "ACCOUNT_TRADE_MODE_REAL", object()):
            return AccountType.REAL
        return AccountType.UNKNOWN

    async def get_account(self) -> AccountSnapshot:
        self._require_connected()
        raw = await self._call("account_info")
        if raw is None:
            raise BrokerUnavailableError("MT5 account_info returned no account")
        return AccountSnapshot(
            broker=str(getattr(raw, "company", self._broker_name)),
            platform="MT5",
            account_id=str(getattr(raw, "login", self._login)),
            server=str(getattr(raw, "server", self._server)),
            currency=str(getattr(raw, "currency", "")),
            balance=_decimal(getattr(raw, "balance", None)),
            equity=_decimal(getattr(raw, "equity", None)),
            margin=_decimal(getattr(raw, "margin", None)),
            free_margin=_decimal(getattr(raw, "margin_free", None)),
            leverage=int(getattr(raw, "leverage", 0)),
            account_type=self._account_type(raw),
            timestamp=datetime.now(UTC),
        )

    def _symbol_specification(self, raw: object) -> SymbolSpecification:
        module = self._load_module()
        name = str(getattr(raw, "name", ""))
        base = str(getattr(raw, "currency_base", "")).upper()
        quote = str(getattr(raw, "currency_profit", "")).upper()
        compact = name.upper().replace("/", "").replace("_", "").replace("-", "")
        canonical = "XAUUSD" if base == "XAU" and quote == "USD" else canonicalize_symbol(compact)
        tick_value = max(
            _decimal(getattr(raw, "trade_tick_value_loss", None)),
            _decimal(getattr(raw, "trade_tick_value", None)),
            _decimal(getattr(raw, "trade_tick_value_profit", None)),
        )
        disabled = getattr(module, "SYMBOL_TRADE_MODE_DISABLED", -1)
        return SymbolSpecification(
            name=name,
            canonical_symbol=canonical,
            base_currency=base,
            quote_currency=quote,
            digits=int(getattr(raw, "digits", 0)),
            point=_decimal(getattr(raw, "point", None)),
            tick_size=_decimal(getattr(raw, "trade_tick_size", None)),
            tick_value=tick_value,
            contract_size=_decimal(getattr(raw, "trade_contract_size", None)),
            volume_min=_decimal(getattr(raw, "volume_min", None)),
            volume_max=_decimal(getattr(raw, "volume_max", None)),
            volume_step=_decimal(getattr(raw, "volume_step", None)),
            stops_level_points=int(getattr(raw, "trade_stops_level", 0)),
            visible=bool(getattr(raw, "visible", False)),
            trade_enabled=getattr(raw, "trade_mode", disabled) != disabled,
            description=str(getattr(raw, "description", "")),
        )

    async def get_symbols(self) -> tuple[SymbolSpecification, ...]:
        self._require_connected()
        raw_symbols = await self._call("symbols_get")
        if raw_symbols is None:
            raise BrokerError(f"MT5 symbols_get failed: {await self._last_error_text()}")
        specs: list[SymbolSpecification] = []
        for raw in raw_symbols:
            try:
                specs.append(self._symbol_specification(raw))
            except ValueError:
                # Invalid broker metadata must not be normalized into plausible values.
                continue
        return tuple(specs)

    async def resolve_symbol(self, canonical_symbol: str) -> SymbolSpecification:
        spec = resolve_symbol(canonical_symbol, await self.get_symbols())
        if not spec.visible:
            selected = bool(await self._call("symbol_select", spec.name, True))
            if not selected:
                raise BrokerError(f"MT5 could not select resolved symbol {spec.name}")
            raw = await self._call("symbol_info", spec.name)
            if raw is None:
                raise BrokerError(f"MT5 symbol_info failed for {spec.name}")
            spec = self._symbol_specification(raw)
        return spec

    async def get_tick(self, symbol: str) -> Tick:
        self._require_connected()
        raw = await self._call("symbol_info_tick", symbol)
        if raw is None:
            raise BrokerError(f"MT5 has no current tick for {symbol}")
        time_msc = int(getattr(raw, "time_msc", 0))
        seconds = time_msc / 1000 if time_msc else float(getattr(raw, "time", 0))
        if seconds <= 0:
            raise BrokerError(f"MT5 tick for {symbol} has no timestamp")
        return Tick(
            symbol=symbol,
            bid=_decimal(getattr(raw, "bid", None)),
            ask=_decimal(getattr(raw, "ask", None)),
            timestamp=datetime.fromtimestamp(seconds, tz=UTC),
        )

    def _timeframe_constant(self, timeframe: Timeframe) -> object:
        module = self._load_module()
        value = getattr(module, f"TIMEFRAME_{timeframe.value}", None)
        if value is None:
            raise BrokerOperationUnsupported(f"MT5 timeframe {timeframe.value} is unavailable")
        return value

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        self._require_connected()
        if count <= 0:
            raise ValueError("candle count must be positive")
        raw_symbol = await self._call("symbol_info", symbol)
        point = _decimal(getattr(raw_symbol, "point", None)) if raw_symbol else Decimal("0")
        if point <= 0:
            raise BrokerError(f"MT5 symbol {symbol} has no valid point size")
        start_position = 1 if closed_only else 0
        rows = await self._call(
            "copy_rates_from_pos",
            symbol,
            self._timeframe_constant(timeframe),
            start_position,
            count,
        )
        if rows is None:
            raise BrokerError(f"MT5 returned no candles for {symbol} {timeframe.value}")
        candles: list[Candle] = []
        for index, row in enumerate(rows):
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromtimestamp(float(row["time"]), tz=UTC),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["tick_volume"]),
                    spread=float(_decimal(row["spread"]) * point),
                    complete=closed_only or index < len(rows) - 1,
                )
            )
        return tuple(candles)

    def _direction(self, raw_type: object) -> Direction:
        module = self._load_module()
        buy_types = {
            getattr(module, "POSITION_TYPE_BUY", object()),
            getattr(module, "ORDER_TYPE_BUY", object()),
            getattr(module, "ORDER_TYPE_BUY_LIMIT", object()),
            getattr(module, "ORDER_TYPE_BUY_STOP", object()),
        }
        return Direction.LONG if raw_type in buy_types else Direction.SHORT

    async def get_positions(self, symbol: str | None = None) -> tuple[BrokerPosition, ...]:
        self._require_connected()
        rows = await self._call("positions_get", **({"symbol": symbol} if symbol else {}))
        if rows is None:
            raise BrokerError(f"MT5 positions_get failed: {await self._last_error_text()}")
        positions: list[BrokerPosition] = []
        for row in rows:
            stop_loss = _decimal(getattr(row, "sl", None))
            if stop_loss <= 0:
                ticket = str(getattr(row, "ticket", ""))
                raise UnprotectedPositionError(ticket, str(getattr(row, "symbol", "")))
            positions.append(
                BrokerPosition(
                    ticket=str(getattr(row, "ticket", "")),
                    symbol=str(getattr(row, "symbol", "")),
                    direction=self._direction(getattr(row, "type", None)),
                    volume=_decimal(getattr(row, "volume", None)),
                    open_price=_decimal(getattr(row, "price_open", None)),
                    current_price=_decimal(getattr(row, "price_current", None)),
                    stop_loss=stop_loss,
                    take_profit=(
                        _decimal(getattr(row, "tp", None))
                        if _decimal(getattr(row, "tp", None)) > 0
                        else None
                    ),
                    profit=_decimal(getattr(row, "profit", None)),
                    opened_at=datetime.fromtimestamp(float(getattr(row, "time", 0)), tz=UTC),
                    strategy_id=str(getattr(row, "comment", "")),
                )
            )
        return tuple(positions)

    def _order_type(self, raw_type: object) -> OrderType:
        module = self._load_module()
        if raw_type in {
            getattr(module, "ORDER_TYPE_BUY_LIMIT", object()),
            getattr(module, "ORDER_TYPE_SELL_LIMIT", object()),
        }:
            return OrderType.LIMIT
        if raw_type in {
            getattr(module, "ORDER_TYPE_BUY_STOP", object()),
            getattr(module, "ORDER_TYPE_SELL_STOP", object()),
        }:
            return OrderType.STOP
        return OrderType.MARKET

    async def get_orders(self, symbol: str | None = None) -> tuple[BrokerOrder, ...]:
        self._require_connected()
        rows = await self._call("orders_get", **({"symbol": symbol} if symbol else {}))
        if rows is None:
            raise BrokerError(f"MT5 orders_get failed: {await self._last_error_text()}")
        return tuple(
            BrokerOrder(
                ticket=str(getattr(row, "ticket", "")),
                symbol=str(getattr(row, "symbol", "")),
                direction=self._direction(getattr(row, "type", None)),
                order_type=self._order_type(getattr(row, "type", None)),
                volume=_decimal(getattr(row, "volume_current", None)),
                price=_decimal(getattr(row, "price_open", None)),
                stop_loss=_decimal(getattr(row, "sl", None)),
                take_profit=(
                    _decimal(getattr(row, "tp", None))
                    if _decimal(getattr(row, "tp", None)) > 0
                    else None
                ),
                status=OrderStatus.PENDING,
                created_at=datetime.fromtimestamp(float(getattr(row, "time_setup", 0)), tz=UTC),
                idempotency_key=str(getattr(row, "comment", "")),
            )
            for row in rows
        )

    async def is_market_open(self, symbol: str) -> bool:
        self._require_connected()
        module = self._load_module()
        raw = await self._call("symbol_info", symbol)
        if raw is None:
            return False
        return getattr(raw, "trade_mode", None) != getattr(
            module, "SYMBOL_TRADE_MODE_DISABLED", object()
        )

    async def calculate_margin(self, request: OrderRequest, price: Decimal) -> Decimal:
        module = self._load_module()
        order_type = (
            module.ORDER_TYPE_BUY if request.direction is Direction.LONG else module.ORDER_TYPE_SELL
        )
        result = await self._call(
            "order_calc_margin", order_type, request.symbol, float(request.volume), float(price)
        )
        if result is None:
            detail = await self._last_error_text()
            raise BrokerError(f"MT5 could not calculate margin: {detail}")
        return _decimal(result)

    async def calculate_profit(
        self,
        symbol: str,
        direction: Direction,
        volume: Decimal,
        open_price: Decimal,
        close_price: Decimal,
    ) -> Decimal:
        module = self._load_module()
        order_type = (
            module.ORDER_TYPE_BUY if direction is Direction.LONG else module.ORDER_TYPE_SELL
        )
        result = await self._call(
            "order_calc_profit",
            order_type,
            symbol,
            float(volume),
            float(open_price),
            float(close_price),
        )
        if result is None:
            detail = await self._last_error_text()
            raise BrokerError(f"MT5 could not calculate profit: {detail}")
        return _decimal(result)

    async def _trade_request(self, request: OrderRequest, *, pending: bool) -> dict[str, object]:
        module = self._load_module()
        tick = await self.get_tick(request.symbol)
        spec = await self.resolve_symbol(request.symbol)
        price = request.requested_price or (
            tick.ask if request.direction is Direction.LONG else tick.bid
        )
        if pending:
            type_names = {
                (Direction.LONG, OrderType.LIMIT): "ORDER_TYPE_BUY_LIMIT",
                (Direction.SHORT, OrderType.LIMIT): "ORDER_TYPE_SELL_LIMIT",
                (Direction.LONG, OrderType.STOP): "ORDER_TYPE_BUY_STOP",
                (Direction.SHORT, OrderType.STOP): "ORDER_TYPE_SELL_STOP",
            }
            type_name = type_names[(request.direction, request.order_type)]
            action = module.TRADE_ACTION_PENDING
        else:
            type_name = (
                "ORDER_TYPE_BUY" if request.direction is Direction.LONG else "ORDER_TYPE_SELL"
            )
            action = module.TRADE_ACTION_DEAL
        deviation = int(request.maximum_slippage / spec.point) if spec.point else 0
        raw_symbol = await self._call("symbol_info", request.symbol)
        payload: dict[str, object] = {
            "action": action,
            "symbol": request.symbol,
            "volume": float(request.volume),
            "type": getattr(module, type_name),
            "price": float(price),
            "sl": float(request.stop_loss),
            # Intermediate targets are worker-managed partial levels. The final
            # target stays attached at the broker if the worker is unavailable.
            "tp": float(request.take_profits[-1]),
            "deviation": deviation,
            "magic": 710001,
            "comment": broker_correlation_key(request.idempotency_key),
            "type_time": module.ORDER_TIME_GTC,
        }
        filling_mode = getattr(raw_symbol, "filling_mode", None)
        if filling_mode is not None:
            payload["type_filling"] = filling_mode
        if pending and request.expires_at is not None:
            specified = getattr(module, "ORDER_TIME_SPECIFIED", None)
            if specified is not None:
                payload["type_time"] = specified
                payload["expiration"] = int(request.expires_at.timestamp())
        return payload

    def _check_accepted(self, result: object) -> bool:
        module = self._load_module()
        accepted = {
            0,
            getattr(module, "TRADE_RETCODE_DONE", object()),
            getattr(module, "TRADE_RETCODE_PLACED", object()),
            getattr(module, "TRADE_RETCODE_DONE_PARTIAL", object()),
        }
        return getattr(result, "retcode", None) in accepted

    async def validate_order(self, request: OrderRequest) -> OrderCheckResult:
        payload = await self._trade_request(
            request, pending=request.order_type is not OrderType.MARKET
        )
        result = await self._call("order_check", payload)
        if result is None:
            return OrderCheckResult(
                False,
                ("MT5_ORDER_CHECK_NO_RESULT",),
                broker_code=await self._last_error_text(),
            )
        accepted = self._check_accepted(result)
        comment = str(getattr(result, "comment", ""))[:200]
        return OrderCheckResult(
            accepted=accepted,
            reasons=() if accepted else (comment or "MT5_ORDER_CHECK_REJECTED",),
            margin_required=(
                _decimal(getattr(result, "margin", None))
                if getattr(result, "margin", None) is not None
                else None
            ),
            broker_code=str(getattr(result, "retcode", "")),
        )

    async def _send(self, request: OrderRequest, *, pending: bool) -> ExecutionReport:
        payload = await self._trade_request(request, pending=pending)
        result = await self._call("order_send", payload)
        if result is None:
            detail = await self._last_error_text()
            raise IndeterminateBrokerResult(f"MT5 order_send returned no result: {detail}")
        success = self._check_accepted(result)
        ticket_value = getattr(result, "order", 0) or getattr(result, "deal", 0)
        return ExecutionReport(
            success=success,
            idempotency_key=request.idempotency_key,
            broker_ticket=str(ticket_value) if ticket_value else None,
            requested_price=request.requested_price,
            executed_price=(
                _decimal(getattr(result, "price", None))
                if getattr(result, "price", None) is not None
                else None
            ),
            volume=_decimal(getattr(result, "volume", request.volume)),
            broker_code=str(getattr(result, "retcode", "")),
            message=str(getattr(result, "comment", ""))[:200],
            submitted_at=datetime.now(UTC),
        )

    async def place_market_order(self, request: OrderRequest) -> ExecutionReport:
        if request.order_type is not OrderType.MARKET:
            raise BrokerError("market placement requires a MARKET request")
        return await self._send(request, pending=False)

    async def place_pending_order(self, request: OrderRequest) -> ExecutionReport:
        if request.order_type is OrderType.MARKET or request.requested_price is None:
            raise BrokerError("pending placement requires LIMIT/STOP and a requested price")
        return await self._send(request, pending=True)

    async def modify_position(
        self,
        ticket: str,
        *,
        stop_loss: Decimal,
        take_profit: Decimal | None,
    ) -> bool:
        module = self._load_module()
        payload = {
            "action": module.TRADE_ACTION_SLTP,
            "position": int(ticket),
            "sl": float(stop_loss),
            "tp": float(take_profit or Decimal("0")),
        }
        result = await self._call("order_send", payload)
        return result is not None and self._check_accepted(result)

    async def close_position(self, ticket: str, volume: Decimal | None = None) -> ExecutionReport:
        module = self._load_module()
        rows = await self._call("positions_get", ticket=int(ticket))
        if not rows:
            raise BrokerError(f"MT5 position {ticket} does not exist")
        raw = rows[0]
        symbol = str(getattr(raw, "symbol", ""))
        direction = self._direction(getattr(raw, "type", None))
        open_volume = _decimal(getattr(raw, "volume", None))
        close_volume = volume or open_volume
        if close_volume <= 0 or close_volume > open_volume:
            raise BrokerError("close volume is outside the open position volume")
        tick = await self.get_tick(symbol)
        close_direction = Direction.SHORT if direction is Direction.LONG else Direction.LONG
        price = tick.bid if direction is Direction.LONG else tick.ask
        payload = {
            "action": module.TRADE_ACTION_DEAL,
            "position": int(ticket),
            "symbol": symbol,
            "volume": float(close_volume),
            "type": (
                module.ORDER_TYPE_SELL
                if close_direction is Direction.SHORT
                else module.ORDER_TYPE_BUY
            ),
            "price": float(price),
            "magic": 710001,
            "comment": "gf:protective-close",
        }
        result = await self._call("order_send", payload)
        if result is None:
            raise IndeterminateBrokerResult("MT5 close returned no result")
        return ExecutionReport(
            success=self._check_accepted(result),
            idempotency_key=f"close:{ticket}",
            broker_ticket=str(getattr(result, "deal", "") or ticket),
            requested_price=price,
            executed_price=_decimal(getattr(result, "price", price)),
            volume=_decimal(getattr(result, "volume", close_volume)),
            broker_code=str(getattr(result, "retcode", "")),
            message=str(getattr(result, "comment", ""))[:200],
            submitted_at=datetime.now(UTC),
        )

    async def cancel_order(self, ticket: str) -> bool:
        module = self._load_module()
        result = await self._call(
            "order_send",
            {"action": module.TRADE_ACTION_REMOVE, "order": int(ticket)},
        )
        return result is not None and self._check_accepted(result)

    async def history_deals(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        rows = await self._call("history_deals_get", start, end)
        if rows is None:
            detail = await self._last_error_text()
            raise BrokerError(f"MT5 history_deals_get failed: {detail}")
        return tuple(self._history_row(item) for item in rows)

    async def history_orders(self, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        rows = await self._call("history_orders_get", start, end)
        if rows is None:
            detail = await self._last_error_text()
            raise BrokerError(f"MT5 history_orders_get failed: {detail}")
        return tuple(self._history_row(item) for item in rows)

    @staticmethod
    def _history_row(item: object) -> dict[str, Any]:
        as_dict = getattr(item, "_asdict", None)
        if callable(as_dict):
            return dict(as_dict())
        return {
            key: getattr(item, key)
            for key in ("ticket", "order", "position_id", "time", "symbol", "comment")
            if hasattr(item, key)
        }


__all__ = ["MT5BrokerAdapter"]
