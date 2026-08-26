"""Serializable backtesting value objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.domain.enums import Direction


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_balance: float = 10_000.0
    risk_fraction: float = 0.01
    value_per_price_unit_per_lot: float = 100.0
    commission_per_lot_per_side: float = 0.0
    fixed_spread: float = 0.0
    slippage: float = 0.0
    minimum_volume: float = 0.001
    maximum_volume: float = 100.0
    volume_step: float = 0.001
    history_window_bars: int = 250
    close_open_position_at_end: bool = True

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial balance must be positive")
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk fraction must be in (0, 1]")
        if self.value_per_price_unit_per_lot <= 0:
            raise ValueError("price-unit value must be positive")
        if (
            self.minimum_volume <= 0
            or self.maximum_volume < self.minimum_volume
            or self.volume_step <= 0
        ):
            raise ValueError("invalid backtest volume limits")
        if self.history_window_bars <= 0:
            raise ValueError("history window must be positive")
        costs = (self.commission_per_lot_per_side, self.fixed_spread, self.slippage)
        if any(value < 0 for value in costs):
            raise ValueError("backtest costs cannot be negative")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_id: str
    direction: Direction
    signal_time: datetime
    opened_at: datetime
    closed_at: datetime
    requested_entry: float
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    volume: float
    planned_risk: float
    gross_pnl: float
    costs: float
    net_pnl: float
    r_multiple: float
    exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["direction"] = self.direction.value
        for key in ("signal_time", "opened_at", "closed_at"):
            value[key] = value[key].isoformat()
        return value


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: float

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp.isoformat(), "equity": self.equity}


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy_id: str
    strategy_version: str
    started_at: datetime
    ended_at: datetime
    initial_balance: float
    final_balance: float
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: dict[str, Any]
    signals_generated: int
    signals_expired: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "signals_generated": self.signals_generated,
            "signals_expired": self.signals_expired,
            "metrics": self.metrics,
            "equity_curve": [point.to_dict() for point in self.equity_curve],
            "trades": [trade.to_dict() for trade in self.trades],
        }
