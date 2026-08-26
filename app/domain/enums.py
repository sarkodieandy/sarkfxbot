"""Canonical enumerations used throughout GoldFlow."""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    SIGNAL = "SIGNAL"
    SEMI_AUTO = "SEMI_AUTO"
    AUTO = "AUTO"


class TradingEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    BACKTEST = "backtest"
    DEMO = "demo"
    PRODUCTION = "production"


class AccountType(StrEnum):
    DEMO = "DEMO"
    REAL = "REAL"
    UNKNOWN = "UNKNOWN"


class SignalAction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    EXIT = "EXIT"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class SignalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TradeState(StrEnum):
    SCANNING = "SCANNING"
    SIGNAL_FOUND = "SIGNAL_FOUND"
    WAITING_FOR_ENTRY = "WAITING_FOR_ENTRY"
    ENTRY_READY = "ENTRY_READY"
    ORDER_PENDING = "ORDER_PENDING"
    ORDER_FILLED = "ORDER_FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    TP1_HIT = "TP1_HIT"
    BREAK_EVEN = "BREAK_EVEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    STOPPED_OUT = "STOPPED_OUT"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class Timeframe(StrEnum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def seconds(self) -> int:
        return {
            Timeframe.M1: 60,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.M30: 1_800,
            Timeframe.H1: 3_600,
            Timeframe.H4: 14_400,
            Timeframe.D1: 86_400,
        }[self]
