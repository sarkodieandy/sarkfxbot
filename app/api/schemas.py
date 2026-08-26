"""Stable request/response contracts for the Flutter-ready REST API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import TradingMode


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class AccountResponse(ApiModel):
    broker: str
    platform: str
    account_id: str
    server: str
    currency: str
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    leverage: int
    account_type: str
    timestamp: datetime


class SymbolResponse(ApiModel):
    name: str
    canonical_symbol: str
    base_currency: str
    quote_currency: str
    digits: int
    point: Decimal
    tick_size: Decimal
    tick_value: Decimal
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    stops_level_points: int
    visible: bool
    trade_enabled: bool
    description: str


class SignalResponse(ApiModel):
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    canonical_symbol: str
    action: str
    direction: str | None
    entry_min: Decimal | None
    entry_max: Decimal | None
    stop_loss: Decimal | None
    take_profits: list[Any]
    risk_reward: Decimal | None
    confidence_score: int
    status: str
    rationale: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None


class PositionResponse(ApiModel):
    id: str
    broker_account_id: str
    signal_id: str | None
    broker_ticket: str
    symbol: str
    direction: str
    initial_volume: Decimal
    current_volume: Decimal
    open_price: Decimal
    current_price: Decimal
    stop_loss: Decimal
    take_profits: list[Any]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    state: str
    opened_at: datetime
    closed_at: datetime | None


class TradeResponse(ApiModel):
    id: str
    position_id: str
    signal_id: str | None
    broker_account_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    canonical_symbol: str
    direction: str
    state: str
    volume: Decimal
    entry_price: Decimal
    exit_price: Decimal | None
    stop_loss: Decimal
    initial_stop_loss: Decimal
    take_profit_1: Decimal | None
    take_profit_2: Decimal | None
    take_profit_3: Decimal | None
    risk_amount: Decimal
    risk_percentage: Decimal
    risk_reward: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    realized_pnl: Decimal
    commission: Decimal
    swap: Decimal
    spread_cost_estimate: Decimal
    slippage: Decimal
    slippage_cost: Decimal
    r_multiple: Decimal | None
    opened_at: datetime
    closed_at: datetime | None
    exit_reason: str | None
    environment: str
    broker_ticket: str
    created_at: datetime
    updated_at: datetime


class ModeRequest(BaseModel):
    mode: TradingMode
    reason: str = Field(min_length=3, max_length=500)


class ModeResponse(ApiModel):
    mode: TradingMode
    kill_switch: bool
    kill_switch_reason: str | None
    auto_disabled: bool
    updated_at: datetime
    pending_orders_cancelled: list[str] = Field(default_factory=list)
    cancellation_failures: list[str] = Field(default_factory=list)
    reconciliation_required: bool = False


class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class CircuitResetRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ConfigUpdateRequest(BaseModel):
    values: dict[str, Any]
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("values")
    @classmethod
    def non_empty_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("at least one configuration value is required")
        return value


class ConfigResponse(ApiModel):
    config: dict[str, Any]
    checksum: str | None = None


class ApprovalRequest(BaseModel):
    approved: bool = True
    reason: str = Field(min_length=3, max_length=500)


class ClosePositionRequest(BaseModel):
    volume: Decimal | None = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=500)


class MessageResponse(BaseModel):
    status: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
