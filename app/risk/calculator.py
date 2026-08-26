"""Broker-native, precision-safe position sizing."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from app.brokers.base import BrokerAdapter
from app.domain.enums import Direction
from app.domain.errors import RiskRejectedError
from app.domain.models import AccountSnapshot, SymbolSpecification
from app.risk.models import PositionSizingResult


class PositionSizer:
    """Size from broker-calculated stop loss without ever moving the stop."""

    async def calculate(
        self,
        *,
        broker: BrokerAdapter,
        account: AccountSnapshot,
        symbol: SymbolSpecification,
        direction: Direction,
        entry_price: Decimal,
        stop_loss: Decimal,
        risk_fraction: Decimal,
    ) -> PositionSizingResult:
        if account.equity <= 0 or account.free_margin <= 0:
            raise RiskRejectedError("ACCOUNT_EQUITY_OR_FREE_MARGIN_INVALID")
        if risk_fraction <= 0 or risk_fraction > 1:
            raise RiskRejectedError("RISK_FRACTION_INVALID")
        if entry_price <= 0 or stop_loss <= 0 or entry_price == stop_loss:
            raise RiskRejectedError("STOP_DISTANCE_INVALID")
        if direction is Direction.LONG and stop_loss >= entry_price:
            raise RiskRejectedError("LONG_STOP_NOT_BELOW_ENTRY")
        if direction is Direction.SHORT and stop_loss <= entry_price:
            raise RiskRejectedError("SHORT_STOP_NOT_ABOVE_ENTRY")

        allowed_cash_risk = account.equity * risk_fraction
        loss_at_one_lot = abs(
            await broker.calculate_profit(
                symbol.name,
                direction,
                Decimal("1"),
                entry_price,
                stop_loss,
            )
        )
        if loss_at_one_lot <= 0:
            raise RiskRejectedError("BROKER_LOSS_CALCULATION_INVALID")
        raw_volume = allowed_cash_risk / loss_at_one_lot
        capped = min(raw_volume, symbol.volume_max)
        if capped < symbol.volume_min:
            raise RiskRejectedError("BROKER_MINIMUM_VOLUME_EXCEEDS_RISK_LIMIT")

        step_count = ((capped - symbol.volume_min) / symbol.volume_step).to_integral_value(
            rounding=ROUND_FLOOR
        )
        volume = symbol.volume_min + step_count * symbol.volume_step
        if volume < symbol.volume_min or volume > symbol.volume_max:
            raise RiskRejectedError("ROUNDED_VOLUME_OUTSIDE_BROKER_LIMITS")

        actual_cash_risk = abs(
            await broker.calculate_profit(
                symbol.name,
                direction,
                volume,
                entry_price,
                stop_loss,
            )
        )
        if actual_cash_risk <= 0 or actual_cash_risk > allowed_cash_risk:
            raise RiskRejectedError("ROUNDED_VOLUME_EXCEEDS_RISK_LIMIT")
        return PositionSizingResult(
            volume=volume,
            cash_risk=actual_cash_risk,
            risk_fraction=actual_cash_risk / account.equity,
            allowed_cash_risk=allowed_cash_risk,
            loss_at_one_lot=loss_at_one_lot,
        )
