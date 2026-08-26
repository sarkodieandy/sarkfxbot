"""Deterministic demo-evidence gate for production AUTO mode.

Passing this gate only proves that configured historical demo thresholds were met;
it is not a probability estimate or a guarantee of future performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DemoPerformance:
    strategy_version: str
    total_trades: int
    maximum_drawdown: Decimal
    profit_factor: Decimal

    def __post_init__(self) -> None:
        if not self.strategy_version.strip():
            raise ValueError("demo performance requires a strategy version")
        if self.total_trades < 0:
            raise ValueError("demo trade count cannot be negative")
        if self.maximum_drawdown < 0 or self.maximum_drawdown > 1:
            raise ValueError("demo maximum drawdown must be a fraction in [0, 1]")
        if self.profit_factor < 0:
            raise ValueError("demo profit factor cannot be negative")


@dataclass(frozen=True, slots=True)
class DemoValidationDecision:
    allowed: bool
    reasons: tuple[str, ...]
    disclaimer: str = (
        "Demo thresholds do not guarantee future performance or prevent financial loss."
    )


def evaluate_demo_performance(
    performance: DemoPerformance | None,
    *,
    strategy_version: str,
    minimum_trades: int = 100,
    maximum_drawdown: Decimal = Decimal("0.10"),
    minimum_profit_factor: Decimal = Decimal("1.2"),
) -> DemoValidationDecision:
    if minimum_trades <= 0:
        raise ValueError("minimum demo trades must be positive")
    if maximum_drawdown <= 0 or maximum_drawdown > 1:
        raise ValueError("maximum demo drawdown must be in (0, 1]")
    if minimum_profit_factor <= 0:
        raise ValueError("minimum demo profit factor must be positive")
    if performance is None:
        return DemoValidationDecision(False, ("DEMO_VALIDATION_EVIDENCE_MISSING",))
    reasons: list[str] = []
    if performance.strategy_version != strategy_version:
        reasons.append("DEMO_STRATEGY_VERSION_MISMATCH")
    if performance.total_trades < minimum_trades:
        reasons.append("DEMO_TRADE_COUNT_BELOW_MINIMUM")
    if performance.maximum_drawdown > maximum_drawdown:
        reasons.append("DEMO_DRAWDOWN_ABOVE_MAXIMUM")
    if performance.profit_factor < minimum_profit_factor:
        reasons.append("DEMO_PROFIT_FACTOR_BELOW_MINIMUM")
    return DemoValidationDecision(not reasons, tuple(reasons))
