"""Application-layer command and outcome contracts for order execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.domain.enums import OrderType, TradingEnvironment, TradingMode
from app.domain.models import ExecutionReport, OrderRequest, TradeSignal
from app.domain.state_machine import StateTransition
from app.risk.demo_validation import DemoPerformance
from app.risk.models import RiskDecision, RiskUsage


class ExecutionStatus(StrEnum):
    SIGNAL_ONLY = "SIGNAL_ONLY"
    BLOCKED = "BLOCKED"
    CHECK_REJECTED = "CHECK_REJECTED"
    UNKNOWN = "UNKNOWN"
    FILLED = "FILLED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    PROTECTION_FAILED = "PROTECTION_FAILED"


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    signal: TradeSignal
    mode: TradingMode
    environment: TradingEnvironment
    order_type: OrderType = OrderType.MARKET
    requested_price: Decimal | None = None
    maximum_slippage: Decimal = Decimal("0.30")
    approved: bool = False
    live_trading_enabled: bool = False
    configured_live_confirmation: str | None = None
    required_live_confirmation: str | None = None
    demo_performance: DemoPerformance | None = None
    session_allowed: bool = False
    risk_usage: RiskUsage = field(default_factory=RiskUsage)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: ExecutionStatus
    reasons: tuple[str, ...] = ()
    request: OrderRequest | None = None
    report: ExecutionReport | None = None
    risk: RiskDecision | None = None
    transitions: tuple[StateTransition, ...] = ()
    requires_reconciliation: bool = False
