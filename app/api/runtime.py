"""Process-local operator controls backed by immutable environment safety gates."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any

from app.config.settings import Settings
from app.domain.enums import TradingEnvironment, TradingMode
from app.domain.errors import ConfigurationError
from app.runtime_contract import DurableRuntimeState

_STRATEGY_BOOLEAN_FIELDS = frozenset({"require_ema_long_alignment", "use_closed_candles_only"})
_STRATEGY_INTEGER_FIELDS: dict[str, tuple[int, int]] = {
    "atr_period": (1, 1_000),
    "breakout_lookback": (1, 1_000),
    "confidence_threshold": (1, 100),
    "ema_fast_period": (1, 1_000),
    "ema_long_period": (1, 2_000),
    "ema_slope_lookback": (1, 1_000),
    "ema_slow_period": (1, 1_000),
    "rsi_period": (1, 1_000),
    "signal_expiry_bars": (1, 1_000),
    "spread_average_period": (1, 1_000),
    "structure_left_span": (1, 100),
    "structure_right_span": (1, 100),
}
_STRATEGY_POSITIVE_FIELDS = frozenset(
    {
        "entry_zone_atr_fraction",
        "maximum_atr_fraction",
        "maximum_spread",
        "minimum_atr_fraction",
        "minimum_risk_reward",
        "preferred_risk_reward",
        "pullback_atr_tolerance",
        "rejection_wick_ratio",
        "stop_atr_multiple",
        "stop_buffer_atr_fraction",
    }
)
_STRATEGY_RSI_FIELDS = frozenset(
    {"long_rsi_maximum", "long_rsi_minimum", "short_rsi_maximum", "short_rsi_minimum"}
)
_RISK_FRACTION_FIELDS = frozenset(
    {
        "risk_per_trade",
        "maximum_daily_loss",
        "maximum_weekly_loss",
        "maximum_account_drawdown",
        "tp1_close_fraction",
    }
)
_RISK_POSITION_FIELDS = frozenset({"maximum_open_positions", "maximum_gold_positions"})
_RISK_POSITIVE_FIELDS = frozenset(
    {
        "minimum_risk_reward",
        "preferred_risk_reward",
        "maximum_spread",
        "break_even_trigger_r",
        "trailing_atr_multiple",
    }
)
_RISK_NON_NEGATIVE_FIELDS = frozenset({"maximum_slippage"})
_RISK_BOOLEAN_FIELDS = frozenset(
    {"break_even_enabled", "tp1_partial_close_enabled", "trailing_stop_enabled"}
)
_PROHIBITED_RISK_FIELDS = frozenset(
    {"martingale", "grid", "averaging_down", "automatic_lot_doubling"}
)


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    mode: TradingMode
    kill_switch: bool
    kill_switch_reason: str | None
    auto_disabled: bool
    updated_at: datetime


class RuntimeControls:
    """Mutable operator intent that can only narrow environment-level permission."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = RLock()
        self._mode = settings.trading_mode
        self._kill_switch = False
        self._kill_switch_reason: str | None = None
        self._auto_disabled = False
        self._updated_at = datetime.now(UTC)
        self._strategy: dict[str, Any] = {
            "strategy_id": "gold_h1_m15_m5",
            "name": "Gold H1-M15-M5 Trend Pullback Strategy",
            "version": "1.0.0",
            "confidence_threshold": 75,
            "use_closed_candles_only": True,
        }
        self._risk: dict[str, Any] = {
            "risk_per_trade": str(settings.risk_per_trade),
            "maximum_daily_loss": str(settings.max_daily_loss),
            "maximum_weekly_loss": str(settings.max_weekly_loss),
            "maximum_account_drawdown": str(settings.max_account_drawdown),
            "maximum_open_positions": settings.max_open_positions,
            "maximum_gold_positions": settings.max_gold_positions,
            "minimum_risk_reward": str(settings.minimum_rr),
            "preferred_risk_reward": str(settings.preferred_rr),
            "maximum_spread": str(settings.maximum_spread),
            "maximum_slippage": str(settings.maximum_slippage),
            "tp1_partial_close_enabled": settings.tp1_partial_close_enabled,
            "tp1_close_fraction": str(settings.tp1_close_fraction),
            "break_even_enabled": settings.break_even_enabled,
            "break_even_trigger_r": str(settings.break_even_trigger_r),
            "trailing_stop_enabled": settings.trailing_stop_enabled,
            "trailing_atr_multiple": str(settings.trailing_atr_multiple),
        }

    def snapshot(self) -> ControlSnapshot:
        with self._lock:
            return ControlSnapshot(
                self._mode,
                self._kill_switch,
                self._kill_switch_reason,
                self._auto_disabled,
                self._updated_at,
            )

    @property
    def new_exposure_allowed(self) -> bool:
        snapshot = self.snapshot()
        return not snapshot.kill_switch and not snapshot.auto_disabled

    def _validate_mode(self, mode: TradingMode, *, auto_disabled: bool | None = None) -> None:
        if mode is not TradingMode.SIGNAL:
            if self._settings.trading_env not in {
                TradingEnvironment.DEMO,
                TradingEnvironment.PRODUCTION,
            }:
                raise ConfigurationError(
                    "broker execution modes require TRADING_ENV=demo or production"
                )
            if (
                self._settings.trading_env is TradingEnvironment.PRODUCTION
                and not self._settings.live_trading_permitted
            ):
                raise ConfigurationError("production execution safety gate is not satisfied")
        drawdown_latched = self._auto_disabled if auto_disabled is None else auto_disabled
        if drawdown_latched and mode is TradingMode.AUTO:
            raise ConfigurationError("AUTO mode requires explicit drawdown reset")

    def set_mode(self, mode: TradingMode) -> ControlSnapshot:
        self._validate_mode(mode)
        with self._lock:
            self._mode = mode
            self._updated_at = datetime.now(UTC)
            return self.snapshot()

    def set_kill_switch(self, enabled: bool, reason: str) -> ControlSnapshot:
        if not reason.strip():
            raise ValueError("kill-switch changes require a reason")
        with self._lock:
            self._kill_switch = enabled
            self._kill_switch_reason = reason if enabled else None
            self._updated_at = datetime.now(UTC)
            return self.snapshot()

    def trip_drawdown_protection(self, reason: str) -> ControlSnapshot:
        if not reason.strip():
            raise ValueError("drawdown protection requires a reason")
        with self._lock:
            self._auto_disabled = True
            self._mode = TradingMode.SIGNAL
            self._updated_at = datetime.now(UTC)
            return self.snapshot()

    def reset_drawdown_protection(self) -> ControlSnapshot:
        with self._lock:
            self._auto_disabled = False
            self._updated_at = datetime.now(UTC)
            return self.snapshot()

    def durable_state(self) -> DurableRuntimeState:
        snapshot = self.snapshot()
        return DurableRuntimeState(
            mode=snapshot.mode,
            kill_switch=snapshot.kill_switch,
            kill_switch_reason=snapshot.kill_switch_reason,
            auto_disabled=snapshot.auto_disabled,
            updated_at=snapshot.updated_at,
        )

    def restore_runtime(self, state: DurableRuntimeState) -> ControlSnapshot:
        """Restore a validated PostgreSQL snapshot without weakening environment gates."""

        self._validate_mode(state.mode, auto_disabled=state.auto_disabled)
        with self._lock:
            self._mode = state.mode
            self._kill_switch = state.kill_switch
            self._kill_switch_reason = state.kill_switch_reason
            self._auto_disabled = state.auto_disabled
            self._updated_at = state.updated_at
            return self.snapshot()

    def restore_strategy(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Hydrate an already-versioned active strategy configuration."""

        required = {"strategy_id", "name", "version"}
        if not required <= payload.keys():
            raise ValueError("active strategy configuration is missing identity fields")
        values = {key: value for key, value in payload.items() if key not in required}
        normalized = self._normalize_strategy_update(values)
        restored = {
            "strategy_id": str(payload["strategy_id"]),
            "name": str(payload["name"]),
            "version": str(payload["version"]),
            **normalized,
        }
        self._validate_strategy_relationships(restored)
        with self._lock:
            self._strategy = restored
            return deepcopy(self._strategy)

    def restore_risk(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Hydrate an active risk configuration through the normal allowlist."""

        normalized = self._normalize_risk_update(payload)
        restored = {**self._risk, **normalized}
        self._validate_risk_relationships(restored)
        with self._lock:
            self._risk = restored
            return deepcopy(self._risk)

    def strategy_config(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._strategy)

    def risk_config(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._risk)

    def update_strategy(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        protected = {"strategy_id", "name", "version"}
        if protected.intersection(payload):
            raise ValueError("strategy identity, name, and version cannot be overwritten in place")
        normalized = self._normalize_strategy_update(payload)
        with self._lock:
            updated = {**self._strategy, **normalized}
            self._validate_strategy_relationships(updated)
            updated["version"] = self._next_patch_version(str(self._strategy["version"]))
            self._strategy = updated
            self._updated_at = datetime.now(UTC)
            return deepcopy(updated), self._checksum(updated)

    def update_risk(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        prohibited = set(payload).intersection(_PROHIBITED_RISK_FIELDS)
        if prohibited:
            raise ValueError(f"prohibited risk features: {sorted(prohibited)}")
        normalized = self._normalize_risk_update(payload)
        with self._lock:
            updated = {**self._risk, **normalized}
            self._validate_risk_relationships(updated)
            self._risk = updated
            self._updated_at = datetime.now(UTC)
            return deepcopy(updated), self._checksum(updated)

    @staticmethod
    def _validate_config_values(payload: dict[str, Any]) -> None:
        if any(value is None for value in payload.values()):
            raise ValueError("configuration values cannot be null")
        if len(json.dumps(payload, default=str)) > 32_000:
            raise ValueError("configuration payload is too large")

    @staticmethod
    def _plain_int(value: Any, field: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"{field} must be between {minimum} and {maximum}")
        return int(value)

    @staticmethod
    def _decimal(
        value: Any,
        field: str,
        *,
        minimum: Decimal,
        maximum: Decimal | None = None,
        minimum_inclusive: bool = False,
    ) -> Decimal:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric")
        try:
            converted = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field} must be numeric") from exc
        if not converted.is_finite():
            raise ValueError(f"{field} must be finite")
        minimum_ok = converted >= minimum if minimum_inclusive else converted > minimum
        if not minimum_ok or (maximum is not None and converted > maximum):
            qualifier = "at least" if minimum_inclusive else "greater than"
            upper = f" and at most {maximum}" if maximum is not None else ""
            raise ValueError(f"{field} must be {qualifier} {minimum}{upper}")
        return converted

    @classmethod
    def _normalize_strategy_update(cls, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            _STRATEGY_BOOLEAN_FIELDS
            | frozenset(_STRATEGY_INTEGER_FIELDS)
            | _STRATEGY_POSITIVE_FIELDS
            | _STRATEGY_RSI_FIELDS
        )
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown strategy configuration fields: {sorted(unknown)}")
        normalized: dict[str, Any] = {}
        for field, value in payload.items():
            if field in _STRATEGY_BOOLEAN_FIELDS:
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be a boolean")
                normalized[field] = value
            elif field in _STRATEGY_INTEGER_FIELDS:
                minimum, maximum = _STRATEGY_INTEGER_FIELDS[field]
                normalized[field] = cls._plain_int(value, field, minimum, maximum)
            elif field in _STRATEGY_RSI_FIELDS:
                normalized[field] = float(
                    cls._decimal(
                        value,
                        field,
                        minimum=Decimal("0"),
                        maximum=Decimal("100"),
                        minimum_inclusive=True,
                    )
                )
            else:
                normalized[field] = float(cls._decimal(value, field, minimum=Decimal("0")))
        cls._validate_config_values(normalized)
        return normalized

    @classmethod
    def _normalize_risk_update(cls, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            _RISK_FRACTION_FIELDS
            | _RISK_POSITION_FIELDS
            | _RISK_POSITIVE_FIELDS
            | _RISK_NON_NEGATIVE_FIELDS
            | _RISK_BOOLEAN_FIELDS
        )
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown risk configuration fields: {sorted(unknown)}")
        normalized: dict[str, Any] = {}
        for field, value in payload.items():
            if field in _RISK_BOOLEAN_FIELDS:
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be a boolean")
                normalized[field] = value
            elif field in _RISK_POSITION_FIELDS:
                normalized[field] = cls._plain_int(value, field, 1, 10_000)
            elif field in _RISK_FRACTION_FIELDS:
                normalized[field] = str(
                    cls._decimal(
                        value,
                        field,
                        minimum=Decimal("0"),
                        maximum=Decimal("1"),
                    )
                )
            elif field in _RISK_NON_NEGATIVE_FIELDS:
                normalized[field] = str(
                    cls._decimal(
                        value,
                        field,
                        minimum=Decimal("0"),
                        minimum_inclusive=True,
                    )
                )
            else:
                normalized[field] = str(cls._decimal(value, field, minimum=Decimal("0")))
        cls._validate_config_values(normalized)
        return normalized

    @staticmethod
    def _validate_strategy_relationships(payload: dict[str, Any]) -> None:
        RuntimeControls._validate_config_values(payload)
        ema_fields = ("ema_fast_period", "ema_slow_period", "ema_long_period")
        if all(field in payload for field in ema_fields) and not (
            int(payload["ema_fast_period"])
            < int(payload["ema_slow_period"])
            < int(payload["ema_long_period"])
        ):
            raise ValueError("strategy EMA periods must satisfy fast < slow < long")
        for minimum, maximum in (
            ("minimum_atr_fraction", "maximum_atr_fraction"),
            ("long_rsi_minimum", "long_rsi_maximum"),
            ("short_rsi_minimum", "short_rsi_maximum"),
        ):
            if (
                minimum in payload
                and maximum in payload
                and Decimal(str(payload[minimum])) >= Decimal(str(payload[maximum]))
            ):
                raise ValueError(f"{minimum} must be below {maximum}")
        if (
            "minimum_risk_reward" in payload
            and "preferred_risk_reward" in payload
            and Decimal(str(payload["minimum_risk_reward"]))
            > Decimal(str(payload["preferred_risk_reward"]))
        ):
            raise ValueError("minimum_risk_reward cannot exceed preferred_risk_reward")

    @staticmethod
    def _validate_risk_relationships(payload: dict[str, Any]) -> None:
        RuntimeControls._validate_config_values(payload)
        daily = Decimal(str(payload["maximum_daily_loss"]))
        weekly = Decimal(str(payload["maximum_weekly_loss"]))
        account = Decimal(str(payload["maximum_account_drawdown"]))
        if daily > weekly or weekly > account:
            raise ValueError(
                "risk limits must satisfy daily loss <= weekly loss <= account drawdown"
            )
        if int(payload["maximum_gold_positions"]) > int(payload["maximum_open_positions"]):
            raise ValueError("maximum gold positions cannot exceed total open positions")
        if Decimal(str(payload["preferred_risk_reward"])) < Decimal(
            str(payload["minimum_risk_reward"])
        ):
            raise ValueError("preferred risk/reward cannot be below minimum risk/reward")

    @staticmethod
    def _next_patch_version(version: str) -> str:
        parts = version.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ConfigurationError("strategy version must use semantic x.y.z form")
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
