"""Trading-mode, environment, approval, and real-account execution gates."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from app.domain.enums import AccountType, TradingEnvironment, TradingMode


@dataclass(frozen=True, slots=True)
class ExecutionPermission:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_execution_permission(
    *,
    mode: TradingMode,
    environment: TradingEnvironment,
    account_type: AccountType,
    approved: bool = False,
    live_trading_enabled: bool = False,
    configured_confirmation: str | None = None,
    required_confirmation: str | None = None,
) -> ExecutionPermission:
    reasons: list[str] = []
    if mode is TradingMode.SIGNAL:
        reasons.append("SIGNAL_MODE_NEVER_EXECUTES")
    if mode is TradingMode.SEMI_AUTO and not approved:
        reasons.append("MANUAL_APPROVAL_REQUIRED")
    if environment not in {TradingEnvironment.DEMO, TradingEnvironment.PRODUCTION}:
        reasons.append("EXECUTION_ENVIRONMENT_NOT_ALLOWED")
    if account_type is AccountType.UNKNOWN:
        reasons.append("BROKER_ACCOUNT_TYPE_UNKNOWN")
    if environment is TradingEnvironment.DEMO and account_type is not AccountType.DEMO:
        reasons.append("DEMO_ENVIRONMENT_REQUIRES_VERIFIED_DEMO_ACCOUNT")
    if account_type is AccountType.REAL and environment is not TradingEnvironment.PRODUCTION:
        reasons.append("REAL_ACCOUNT_FORBIDDEN_OUTSIDE_PRODUCTION")
    if environment is TradingEnvironment.PRODUCTION:
        if account_type is not AccountType.REAL:
            reasons.append("PRODUCTION_REQUIRES_VERIFIED_REAL_ACCOUNT")
        if not live_trading_enabled:
            reasons.append("LIVE_TRADING_FLAG_DISABLED")
        if not configured_confirmation or not required_confirmation:
            reasons.append("LIVE_TRADING_CONFIRMATION_MISSING")
        elif not hmac.compare_digest(configured_confirmation, required_confirmation):
            reasons.append("LIVE_TRADING_CONFIRMATION_INVALID")
    elif account_type is AccountType.REAL:
        reasons.append("DEMO_FIRST_POLICY_BLOCKED_REAL_EXECUTION")
    return ExecutionPermission(not reasons, tuple(dict.fromkeys(reasons)))
