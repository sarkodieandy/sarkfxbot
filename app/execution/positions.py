"""Protected position management with broker-volume and stop constraints."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from app.brokers.base import BrokerAdapter
from app.domain.enums import Direction
from app.domain.errors import BrokerError
from app.domain.models import BrokerPosition, ExecutionReport


class TrailingStopMode(StrEnum):
    ATR = "ATR"
    FIXED = "FIXED"
    STRUCTURE = "STRUCTURE"


@dataclass(frozen=True, slots=True)
class PositionAction:
    applied: bool
    reason: str
    position: BrokerPosition | None = None
    report: ExecutionReport | None = None


class PositionManager:
    def __init__(self, broker: BrokerAdapter, *, trailing_enabled: bool = False) -> None:
        self._broker = broker
        self._trailing_enabled = trailing_enabled

    async def _position(self, ticket: str) -> BrokerPosition:
        position = next(
            (item for item in await self._broker.get_positions() if item.ticket == ticket),
            None,
        )
        if position is None:
            raise BrokerError(f"position {ticket} was not found")
        return position

    async def ensure_protection(
        self,
        ticket: str,
        *,
        required_stop_loss: Decimal,
        required_take_profit: Decimal,
    ) -> PositionAction:
        """Repair protection or close if the broker refuses the repair."""

        position = await self._position(ticket)
        if (
            position.stop_loss == required_stop_loss
            and position.take_profit == required_take_profit
        ):
            return PositionAction(True, "PROTECTION_CONFIRMED", position=position)
        modified = await self._broker.modify_position(
            ticket,
            stop_loss=required_stop_loss,
            take_profit=required_take_profit,
        )
        if modified:
            return PositionAction(
                True,
                "PROTECTION_REPAIRED",
                position=await self._position(ticket),
            )
        report = await self._broker.close_position(ticket)
        return PositionAction(
            report.success,
            "UNPROTECTED_POSITION_CLOSED" if report.success else "PROTECTION_AND_CLOSE_FAILED",
            report=report,
        )

    async def partial_close(self, ticket: str, fraction: Decimal) -> PositionAction:
        if fraction <= 0 or fraction > 1:
            raise ValueError("partial-close fraction must be in (0, 1]")
        position = await self._position(ticket)
        if fraction == 1:
            report = await self._broker.close_position(ticket)
            return PositionAction(report.success, "POSITION_CLOSED", report=report)
        spec = await self._broker.resolve_symbol(position.symbol)
        raw_close = position.volume * fraction
        close_volume = (raw_close / spec.volume_step).to_integral_value(
            rounding=ROUND_FLOOR
        ) * spec.volume_step
        remaining = position.volume - close_volume
        if close_volume < spec.volume_min:
            return PositionAction(False, "PARTIAL_VOLUME_BELOW_BROKER_MINIMUM", position=position)
        if remaining != 0 and remaining < spec.volume_min:
            return PositionAction(
                False, "PARTIAL_CLOSE_LEAVES_INVALID_REMAINDER", position=position
            )
        report = await self._broker.close_position(ticket, close_volume)
        return PositionAction(report.success, "PARTIAL_CLOSE_EXECUTED", report=report)

    async def move_to_break_even(
        self,
        ticket: str,
        *,
        spread_price: Decimal,
        commission_price_equivalent: Decimal = Decimal("0"),
        slippage_price: Decimal = Decimal("0"),
    ) -> PositionAction:
        costs = spread_price + commission_price_equivalent + slippage_price
        if any(value < 0 for value in (spread_price, commission_price_equivalent, slippage_price)):
            raise ValueError("break-even cost components cannot be negative")
        position = await self._position(ticket)
        buffer = costs
        target = (
            position.open_price + buffer
            if position.direction is Direction.LONG
            else position.open_price - buffer
        )
        improves = (
            target > position.stop_loss
            if position.direction is Direction.LONG
            else target < position.stop_loss
        )
        still_protectable = (
            target < position.current_price
            if position.direction is Direction.LONG
            else target > position.current_price
        )
        if not improves:
            return PositionAction(False, "BREAK_EVEN_WOULD_NOT_IMPROVE_STOP", position=position)
        if not still_protectable:
            return PositionAction(
                False, "PRICE_HAS_NOT_CLEARED_BREAK_EVEN_BUFFER", position=position
            )
        modified = await self._broker.modify_position(
            ticket,
            stop_loss=target,
            take_profit=position.take_profit,
        )
        return PositionAction(
            modified,
            "BREAK_EVEN_APPLIED" if modified else "BREAK_EVEN_BROKER_REJECTED",
            position=await self._position(ticket) if modified else position,
        )

    async def trail(
        self,
        ticket: str,
        *,
        mode: TrailingStopMode,
        fixed_distance: Decimal | None = None,
        atr: Decimal | None = None,
        atr_multiple: Decimal = Decimal("1"),
        structure_stop: Decimal | None = None,
    ) -> PositionAction:
        if not self._trailing_enabled:
            return PositionAction(False, "TRAILING_DISABLED")
        position = await self._position(ticket)
        if mode is TrailingStopMode.FIXED:
            if fixed_distance is None or fixed_distance <= 0:
                raise ValueError("fixed trailing requires a positive distance")
            distance = fixed_distance
            candidate = (
                position.current_price - distance
                if position.direction is Direction.LONG
                else position.current_price + distance
            )
        elif mode is TrailingStopMode.ATR:
            if atr is None or atr <= 0 or atr_multiple <= 0:
                raise ValueError("ATR trailing requires positive ATR and multiple")
            distance = atr * atr_multiple
            candidate = (
                position.current_price - distance
                if position.direction is Direction.LONG
                else position.current_price + distance
            )
        else:
            if structure_stop is None or structure_stop <= 0:
                raise ValueError("structure trailing requires a positive structure stop")
            candidate = structure_stop
        improves = (
            candidate > position.stop_loss
            if position.direction is Direction.LONG
            else candidate < position.stop_loss
        )
        valid_side = (
            candidate < position.current_price
            if position.direction is Direction.LONG
            else candidate > position.current_price
        )
        if not improves or not valid_side:
            return PositionAction(False, "TRAILING_STOP_NOT_AN_IMPROVEMENT", position=position)
        modified = await self._broker.modify_position(
            ticket,
            stop_loss=candidate,
            take_profit=position.take_profit,
        )
        return PositionAction(
            modified,
            "TRAILING_STOP_APPLIED" if modified else "TRAILING_STOP_BROKER_REJECTED",
            position=await self._position(ticket) if modified else position,
        )
