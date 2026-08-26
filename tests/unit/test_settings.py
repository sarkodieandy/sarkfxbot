from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

from app.config.settings import Settings, get_settings
from app.domain.enums import AccountType, TradingEnvironment, TradingMode


def test_defaults_are_demo_signal_and_fail_closed() -> None:
    settings = Settings(_env_file=None)

    assert settings.trading_env is TradingEnvironment.DEMO
    assert settings.trading_mode is TradingMode.SIGNAL
    assert settings.live_trading_permission.granted is False
    assert settings.can_execute_for_account(AccountType.DEMO) is False
    assert settings.can_execute_for_account(AccountType.REAL) is False


@pytest.mark.parametrize(
    ("environment", "enabled", "confirmation", "expected"),
    [
        ("production", True, "exact-token", True),
        ("demo", True, "exact-token", False),
        ("production", False, "exact-token", False),
        ("production", True, "wrong-token", False),
        ("production", True, "", False),
    ],
)
def test_live_permission_requires_all_three_gates(
    environment: str,
    enabled: bool,
    confirmation: str,
    expected: bool,
) -> None:
    settings = Settings(
        _env_file=None,
        trading_env=environment,
        trading_mode="auto",
        live_trading_enabled=enabled,
        live_trading_confirmation=SecretStr(confirmation),
        live_trading_confirmation_secret=SecretStr("exact-token"),
        jwt_secret=SecretStr("not-the-development-key"),
    )

    assert settings.live_trading_permission.granted is expected


def test_real_account_check_remains_a_runtime_gate() -> None:
    settings = Settings(
        _env_file=None,
        trading_env="production",
        trading_mode="AUTO",
        live_trading_enabled=True,
        live_trading_confirmation="exact-token",
        live_trading_confirmation_secret="exact-token",
        jwt_secret="production-test-key",
    )

    assert settings.live_trading_permitted is True
    assert settings.can_execute_for_account(AccountType.REAL) is True
    assert settings.can_execute_for_account(AccountType.DEMO) is False
    assert settings.can_execute_for_account(AccountType.UNKNOWN) is False


def test_demo_execution_requires_demo_environment_and_non_signal_mode() -> None:
    auto = Settings(_env_file=None, trading_env="demo", trading_mode="AUTO")
    semi_auto = Settings(_env_file=None, trading_env="demo", trading_mode="semi_auto")

    assert auto.can_execute_for_account(AccountType.DEMO) is True
    assert semi_auto.can_execute_for_account(AccountType.DEMO) is True
    assert auto.can_execute_for_account(AccountType.REAL) is False


def test_settings_normalize_environment_mode_symbol_and_origins() -> None:
    settings = Settings(
        _env_file=None,
        app_env="TEST",
        trading_env="DEMO",
        trading_mode="semi_auto",
        canonical_symbol="xauusd",
        cors_origins="https://admin.example/, http://localhost:3000,https://admin.example",
        trade_sessions="London,new_york,london",
    )

    assert settings.app_env is TradingEnvironment.TEST
    assert settings.trading_mode is TradingMode.SEMI_AUTO
    assert settings.canonical_symbol == "XAUUSD"
    assert settings.cors_origins == ["https://admin.example", "http://localhost:3000"]
    assert settings.trade_sessions == ["london", "new_york"]


def test_demo_validation_and_execution_quality_defaults_are_fail_closed() -> None:
    settings = Settings(_env_file=None)

    assert settings.require_demo_validation_for_live is True
    assert settings.minimum_demo_trades == 100
    assert settings.maximum_demo_drawdown == Decimal("0.10")
    assert settings.minimum_demo_profit_factor == Decimal("1.2")
    assert settings.maximum_spread == Decimal("0.50")
    assert settings.maximum_slippage == Decimal("0.30")
    assert settings.use_closed_candles_only is True
    assert settings.news_filter_enabled is False
    assert settings.trailing_stop_enabled is False


def test_settings_are_frozen_and_secrets_are_masked() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret="super-secret-value",
        mt5_password="broker-password",
    )

    assert "super-secret-value" not in repr(settings)
    assert "broker-password" not in repr(settings)
    assert "super-secret-value" not in str(settings.model_dump())
    with pytest.raises(ValidationError):
        settings.trading_mode = TradingMode.AUTO  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"risk_per_trade": Decimal("0")},
        {"risk_per_trade": Decimal("1.01")},
        {"max_daily_loss": Decimal("0.08"), "max_weekly_loss": Decimal("0.07")},
        {"minimum_rr": Decimal("2"), "preferred_rr": Decimal("1.9")},
        {"max_open_positions": 1, "max_gold_positions": 2},
        {"minimum_demo_trades": 0},
        {"maximum_demo_drawdown": Decimal("1.01")},
        {"minimum_demo_profit_factor": Decimal("0")},
        {"maximum_spread": Decimal("0")},
        {"maximum_slippage": Decimal("-0.01")},
        {"jwt_algorithm": "none"},
        {"cors_origins": "*"},
    ],
)
def test_invalid_risk_and_security_configuration_is_rejected(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **cast(Any, overrides))


def test_cached_settings_reads_environment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("TRADING_MODE", "AUTO")
    first = get_settings()
    monkeypatch.setenv("TRADING_MODE", "SIGNAL")

    assert first is get_settings()
    assert first.trading_mode is TradingMode.AUTO
    get_settings.cache_clear()


def test_production_readiness_reports_unsafe_execution_configuration() -> None:
    settings = Settings(
        _env_file=None,
        trading_env="production",
        trading_mode="AUTO",
        database_url="sqlite+pysqlite:///unsafe.db",
    )

    assert settings.can_execute_for_account(AccountType.REAL) is False
    assert any("live safety gate" in error for error in settings.production_readiness_errors)
    assert any("PostgreSQL" in error for error in settings.production_readiness_errors)


def test_logging_secret_material_includes_connection_urls_and_account_identifiers() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@db/goldflow",
        redis_url="redis://:password@redis:6379/0",
        mt5_login="123456",
        telegram_chat_id="987654",
    )

    secrets = settings.secret_values()
    assert settings.database_url in secrets
    assert settings.redis_url in secrets
    assert settings.mt5_login in secrets
    assert settings.telegram_chat_id in secrets
