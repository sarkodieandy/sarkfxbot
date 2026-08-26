"""Typed, immutable settings with fail-closed trading permissions."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.domain.enums import AccountType, OrderType, TradingEnvironment, TradingMode

_INSECURE_JWT_DEFAULT = "development-only-change-me"


@dataclass(frozen=True, slots=True)
class LiveTradingPermission:
    """A secret-free immutable snapshot of the live-trading configuration gate.

    This gate deliberately does not include broker account verification.  A caller
    must still inspect the connected account and pass its type to
    :meth:`Settings.can_execute_for_account` immediately before execution.
    """

    production_environment: bool
    explicitly_enabled: bool
    confirmation_matches: bool

    @property
    def granted(self) -> bool:
        return self.production_environment and self.explicitly_enabled and self.confirmation_matches


class Settings(BaseSettings):
    """GoldFlow configuration loaded from environment variables or ``.env``.

    The model is frozen so permission-bearing values cannot be changed after
    startup.  Construct a fresh instance after changing the environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    app_env: TradingEnvironment = TradingEnvironment.DEVELOPMENT
    trading_env: TradingEnvironment = TradingEnvironment.DEMO
    trading_mode: TradingMode = TradingMode.SIGNAL

    live_trading_enabled: bool = False
    live_trading_confirmation: SecretStr | None = Field(default=None, repr=False)
    live_trading_confirmation_secret: SecretStr = Field(default=SecretStr(""), repr=False)

    database_url: str = Field(
        default="postgresql+psycopg://goldflow@localhost:5432/goldflow",
        repr=False,
    )
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)

    jwt_secret: SecretStr = Field(default=SecretStr(_INSECURE_JWT_DEFAULT), repr=False)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "goldflow"
    jwt_audience: str = "goldflow-api"
    access_token_minutes: int = Field(default=30, ge=1, le=1_440)

    api_host: str = "0.0.0.0"  # noqa: S104 - intentional server bind default
    api_port: int = Field(default=8000, ge=1, le=65_535)
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8080"]
    )
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)

    broker_type: str = "mock"
    canonical_symbol: str = "XAUUSD"
    entry_order_type: OrderType = OrderType.MARKET
    max_tick_age_seconds: int = Field(default=30, ge=1, le=3_600)
    maximum_spread: Decimal = Decimal("0.50")
    maximum_slippage: Decimal = Decimal("0.30")
    trade_sessions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["london", "new_york"]
    )
    use_closed_candles_only: bool = True
    news_filter_enabled: bool = False
    tp1_partial_close_enabled: bool = False
    tp1_close_fraction: Decimal = Decimal("0.50")
    break_even_enabled: bool = False
    break_even_trigger_r: Decimal = Decimal("1.0")
    trailing_stop_enabled: bool = False
    trailing_atr_multiple: Decimal = Decimal("2.0")
    heartbeat_interval_seconds: int = Field(default=30, ge=5, le=300)

    mt5_login: str | None = None
    mt5_server: str | None = None
    mt5_password: SecretStr | None = Field(default=None, repr=False)
    mt5_terminal_path: str | None = None

    risk_per_trade: Decimal = Decimal("0.01")
    max_daily_loss: Decimal = Decimal("0.03")
    max_weekly_loss: Decimal = Decimal("0.07")
    max_account_drawdown: Decimal = Decimal("0.10")
    max_open_positions: int = 1
    max_gold_positions: int = 1
    minimum_rr: Decimal = Decimal("1.8")
    preferred_rr: Decimal = Decimal("2.0")

    require_demo_validation_for_live: bool = True
    minimum_demo_trades: int = 100
    maximum_demo_drawdown: Decimal = Decimal("0.10")
    minimum_demo_profit_factor: Decimal = Decimal("1.2")

    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = Field(default=None, repr=False)
    telegram_chat_id: str | None = Field(default=None, repr=False)
    sentry_dsn: str | None = Field(default=None, repr=False)

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("app_env", "trading_env", mode="before")
    @classmethod
    def _normalize_environment(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("entry_order_type", mode="before")
    @classmethod
    def _normalize_order_type(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("broker_type", "canonical_symbol", "jwt_algorithm", mode="before")
    @classmethod
    def _normalize_uppercase_fields(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if info.field_name in {"canonical_symbol", "jwt_algorithm"}:
            return value.upper()
        return value.lower()

    @field_validator("jwt_algorithm")
    @classmethod
    def _validate_jwt_algorithm(cls, value: str) -> str:
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be HS256, HS384, or HS512")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                import json

                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON must be an array")
                return parsed
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    @field_validator("trade_sessions", mode="before")
    @classmethod
    def _parse_trade_sessions(cls, value: Any) -> Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                import json

                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("TRADE_SESSIONS JSON must be an array")
                return parsed
            return [name.strip() for name in stripped.split(",") if name.strip()]
        return value

    @field_validator("trade_sessions")
    @classmethod
    def _normalize_trade_sessions(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            name = value.strip().lower()
            if not name:
                raise ValueError("trade session names cannot be blank")
            if name not in normalized:
                normalized.append(name)
        return normalized

    @field_validator("cors_origins")
    @classmethod
    def _validate_cors_origins(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            origin = value.strip().rstrip("/")
            if origin == "*":
                raise ValueError("wildcard CORS origins are not allowed")
            if not origin.startswith(("http://", "https://")):
                raise ValueError("CORS origins must use http:// or https://")
            if origin not in cleaned:
                cleaned.append(origin)
        return cleaned

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> str:
        normalized = str(value).strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @model_validator(mode="after")
    def _validate_limits(self) -> Settings:
        percentages = {
            "risk_per_trade": self.risk_per_trade,
            "max_daily_loss": self.max_daily_loss,
            "max_weekly_loss": self.max_weekly_loss,
            "max_account_drawdown": self.max_account_drawdown,
        }
        for name, value in percentages.items():
            if value <= 0 or value > 1:
                raise ValueError(f"{name} must be greater than 0 and at most 1")
        if self.max_daily_loss > self.max_weekly_loss:
            raise ValueError("max_daily_loss cannot exceed max_weekly_loss")
        if self.minimum_rr <= 0 or self.preferred_rr < self.minimum_rr:
            raise ValueError("preferred_rr must be at least minimum_rr and both positive")
        if self.max_open_positions < 1 or self.max_gold_positions < 1:
            raise ValueError("position limits must be positive")
        if self.max_gold_positions > self.max_open_positions:
            raise ValueError("max_gold_positions cannot exceed max_open_positions")
        if self.minimum_demo_trades < 1:
            raise ValueError("minimum_demo_trades must be positive")
        if self.maximum_demo_drawdown <= 0 or self.maximum_demo_drawdown > 1:
            raise ValueError("maximum_demo_drawdown must be in (0, 1]")
        if self.minimum_demo_profit_factor <= 0:
            raise ValueError("minimum_demo_profit_factor must be positive")
        if self.maximum_spread <= 0:
            raise ValueError("maximum_spread must be positive")
        if self.maximum_slippage < 0:
            raise ValueError("maximum_slippage cannot be negative")
        if self.tp1_close_fraction <= 0 or self.tp1_close_fraction > 1:
            raise ValueError("tp1_close_fraction must be in (0, 1]")
        if self.break_even_trigger_r <= 0:
            raise ValueError("break_even_trigger_r must be positive")
        if self.trailing_atr_multiple <= 0:
            raise ValueError("trailing_atr_multiple must be positive")
        return self

    @property
    def live_trading_permission(self) -> LiveTradingPermission:
        supplied = (
            self.live_trading_confirmation.get_secret_value()
            if self.live_trading_confirmation is not None
            else ""
        )
        expected = self.live_trading_confirmation_secret.get_secret_value()
        matches = bool(supplied and expected) and hmac.compare_digest(supplied, expected)
        return LiveTradingPermission(
            production_environment=self.trading_env is TradingEnvironment.PRODUCTION,
            explicitly_enabled=self.live_trading_enabled,
            confirmation_matches=matches,
        )

    @property
    def live_trading_permitted(self) -> bool:
        """Compatibility convenience for callers that only need the config gate."""

        return self.live_trading_permission.granted

    def can_execute_for_account(self, account_type: AccountType) -> bool:
        """Apply configuration and runtime broker-account execution gates.

        Unknown accounts and SIGNAL mode never execute.  Demo execution is only
        allowed in the demo environment.  Real execution additionally requires
        the immutable three-part live permission gate.
        """

        if self.trading_mode is TradingMode.SIGNAL or account_type is AccountType.UNKNOWN:
            return False
        if account_type is AccountType.DEMO:
            return self.trading_env is TradingEnvironment.DEMO
        if account_type is AccountType.REAL:
            return self.live_trading_permission.granted

    @property
    def production_readiness_errors(self) -> tuple[str, ...]:
        """Return security misconfigurations without ever exposing secret values."""

        errors: list[str] = []
        if self.trading_env is TradingEnvironment.PRODUCTION:
            if len(self.jwt_secret.get_secret_value()) < 32 or (
                self.jwt_secret.get_secret_value() == _INSECURE_JWT_DEFAULT
            ):
                errors.append("JWT_SECRET must be a non-default value of at least 32 characters")
            if not self.cors_origins:
                errors.append("at least one explicit CORS origin is required")
            if not self.database_url.startswith("postgresql"):
                errors.append("production DATABASE_URL must use PostgreSQL")
            if (
                self.trading_mode is not TradingMode.SIGNAL
                and not self.live_trading_permission.granted
            ):
                errors.append(
                    "production execution mode is configured without the live safety gate"
                )
        elif self.live_trading_enabled:
            errors.append("LIVE_TRADING_ENABLED is only valid with TRADING_ENV=production")
        if self.telegram_enabled and (self.telegram_bot_token is None or not self.telegram_chat_id):
            errors.append("Telegram is enabled but its credentials are incomplete")
        return tuple(errors)

    def secret_values(self) -> tuple[str, ...]:
        """Return configured secret material for the logging redactor."""

        candidates: list[SecretStr | None] = [
            self.live_trading_confirmation,
            self.live_trading_confirmation_secret,
            self.jwt_secret,
            self.mt5_password,
            self.telegram_bot_token,
        ]
        values = [item.get_secret_value() for item in candidates if item is not None]
        values.extend(
            value
            for value in (
                self.database_url,
                self.redis_url,
                self.sentry_dsn,
                self.mt5_login,
                self.telegram_chat_id,
            )
            if value
        )
        return tuple(value for value in values if value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings snapshot."""

    return Settings()
