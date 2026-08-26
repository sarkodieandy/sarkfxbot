import itertools

from app.domain.enums import AccountType, TradingEnvironment, TradingMode
from app.risk.mode import evaluate_execution_permission


def test_demo_auto_allows_only_non_real_verified_account() -> None:
    allowed = evaluate_execution_permission(
        mode=TradingMode.AUTO,
        environment=TradingEnvironment.DEMO,
        account_type=AccountType.DEMO,
    )
    blocked = evaluate_execution_permission(
        mode=TradingMode.AUTO,
        environment=TradingEnvironment.DEMO,
        account_type=AccountType.REAL,
    )
    assert allowed.allowed
    assert not blocked.allowed
    assert "REAL_ACCOUNT_FORBIDDEN_OUTSIDE_PRODUCTION" in blocked.reasons


def test_semi_auto_requires_approval() -> None:
    result = evaluate_execution_permission(
        mode=TradingMode.SEMI_AUTO,
        environment=TradingEnvironment.DEMO,
        account_type=AccountType.DEMO,
    )
    assert not result.allowed
    assert result.reasons == ("MANUAL_APPROVAL_REQUIRED",)


def test_production_gate_requires_every_condition() -> None:
    for enabled, configured, required in itertools.product(
        (False, True), (None, "confirm"), (None, "confirm")
    ):
        result = evaluate_execution_permission(
            mode=TradingMode.AUTO,
            environment=TradingEnvironment.PRODUCTION,
            account_type=AccountType.REAL,
            live_trading_enabled=enabled,
            configured_confirmation=configured,
            required_confirmation=required,
        )
        expected = enabled and configured == required == "confirm"
        assert result.allowed is expected


def test_signal_mode_never_executes_even_with_live_flags() -> None:
    result = evaluate_execution_permission(
        mode=TradingMode.SIGNAL,
        environment=TradingEnvironment.PRODUCTION,
        account_type=AccountType.REAL,
        live_trading_enabled=True,
        configured_confirmation="confirm",
        required_confirmation="confirm",
    )
    assert not result.allowed
    assert "SIGNAL_MODE_NEVER_EXECUTES" in result.reasons


def test_execution_is_forbidden_in_non_trading_environments() -> None:
    for environment in (
        TradingEnvironment.DEVELOPMENT,
        TradingEnvironment.TEST,
        TradingEnvironment.BACKTEST,
    ):
        result = evaluate_execution_permission(
            mode=TradingMode.AUTO,
            environment=environment,
            account_type=AccountType.DEMO,
        )
        assert not result.allowed
        assert "EXECUTION_ENVIRONMENT_NOT_ALLOWED" in result.reasons


def test_demo_environment_requires_verified_demo_account() -> None:
    for account_type in (AccountType.REAL, AccountType.UNKNOWN):
        result = evaluate_execution_permission(
            mode=TradingMode.AUTO,
            environment=TradingEnvironment.DEMO,
            account_type=account_type,
        )
        assert not result.allowed
        assert "DEMO_ENVIRONMENT_REQUIRES_VERIFIED_DEMO_ACCOUNT" in result.reasons
