"""Risk configuration and persistence-neutral decision records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.brokers.base import BrokerHealth
from app.domain.models import (
    AccountSnapshot,
    BrokerOrder,
    BrokerPosition,
    SymbolSpecification,
    Tick,
)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade: Decimal = Decimal("0.01")
    maximum_daily_loss: Decimal = Decimal("0.03")
    maximum_weekly_loss: Decimal = Decimal("0.07")
    maximum_account_drawdown: Decimal = Decimal("0.10")
    maximum_open_positions: int = 1
    maximum_gold_positions: int = 1
    minimum_risk_reward: Decimal = Decimal("1.8")
    maximum_spread: Decimal = Decimal("0.50")
    maximum_tick_age: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        fractions = (
            self.risk_per_trade,
            self.maximum_daily_loss,
            self.maximum_weekly_loss,
            self.maximum_account_drawdown,
        )
        if any(value <= 0 or value > 1 for value in fractions):
            raise ValueError("risk and drawdown fractions must be in (0, 1]")
        if self.maximum_open_positions <= 0 or self.maximum_gold_positions <= 0:
            raise ValueError("position limits must be positive")
        if self.minimum_risk_reward <= 0 or self.maximum_spread < 0:
            raise ValueError("risk/reward and spread limits are invalid")
        if self.maximum_tick_age <= timedelta(0):
            raise ValueError("maximum tick age must be positive")


@dataclass(frozen=True, slots=True)
class RiskUsage:
    """Loss amounts are positive cash usage, never signed P&L values."""

    daily_realized_loss: Decimal = Decimal("0")
    weekly_realized_loss: Decimal = Decimal("0")
    open_risk: Decimal = Decimal("0")
    peak_equity: Decimal | None = None

    def __post_init__(self) -> None:
        amounts = (self.daily_realized_loss, self.weekly_realized_loss, self.open_risk)
        if any(value < 0 for value in amounts):
            raise ValueError("risk usage amounts cannot be negative")
        if self.peak_equity is not None and self.peak_equity <= 0:
            raise ValueError("peak equity must be positive")


@dataclass(frozen=True, slots=True)
class PositionSizingResult:
    volume: Decimal
    cash_risk: Decimal
    risk_fraction: Decimal
    allowed_cash_risk: Decimal
    loss_at_one_lot: Decimal


@dataclass(frozen=True, slots=True)
class PreTradeSnapshot:
    now: datetime
    account: AccountSnapshot
    symbol: SymbolSpecification
    tick: Tick
    health: BrokerHealth
    market_open: bool
    session_allowed: bool = False
    equivalent_symbols: frozenset[str] = field(default_factory=frozenset)
    positions: tuple[BrokerPosition, ...] = ()
    orders: tuple[BrokerOrder, ...] = ()
    usage: RiskUsage = field(default_factory=RiskUsage)
    required_margin: Decimal = Decimal("0")
    kill_switch: bool = False
    circuit_locked: bool = False


@dataclass(frozen=True, slots=True)
class RiskDecision:
    accepted: bool
    reasons: tuple[str, ...]
    risk_amount: Decimal
    risk_fraction: Decimal
    risk_reward: Decimal | None
    required_margin: Decimal
