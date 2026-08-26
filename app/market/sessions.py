"""Configurable UTC trading-session filters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time


@dataclass(frozen=True, slots=True)
class TradingSession:
    """A named inclusive-start, exclusive-end UTC time window."""

    name: str
    start: time
    end: time
    weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("session name is required")
        if not self.weekdays or any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays must contain values from 0 through 6")

    def contains(self, timestamp: datetime) -> bool:
        if timestamp.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        value = timestamp.astimezone(UTC)
        current = value.time().replace(tzinfo=None)
        if self.start < self.end:
            return value.weekday() in self.weekdays and self.start <= current < self.end
        if current >= self.start:
            return value.weekday() in self.weekdays
        previous_weekday = (value.weekday() - 1) % 7
        return current < self.end and previous_weekday in self.weekdays


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    sessions: tuple[TradingSession, ...]
    allowed_names: frozenset[str]

    def __post_init__(self) -> None:
        known = {session.name.lower() for session in self.sessions}
        unknown = {name.lower() for name in self.allowed_names} - known
        if unknown:
            raise ValueError(f"unknown configured sessions: {sorted(unknown)}")

    def active_sessions(self, timestamp: datetime) -> tuple[str, ...]:
        return tuple(session.name for session in self.sessions if session.contains(timestamp))

    def is_allowed(self, timestamp: datetime) -> bool:
        allowed = {name.lower() for name in self.allowed_names}
        return any(
            session.name.lower() in allowed and session.contains(timestamp)
            for session in self.sessions
        )


def default_session_calendar(
    allowed_names: frozenset[str] = frozenset({"london", "new_york"}),
) -> SessionCalendar:
    """Provide conventional UTC windows that callers may replace/configure."""

    sessions = (
        TradingSession("asia", time(0, 0), time(8, 0)),
        TradingSession("london", time(7, 0), time(16, 0)),
        TradingSession("new_york", time(12, 0), time(21, 0)),
        TradingSession("london_new_york_overlap", time(12, 0), time(16, 0)),
    )
    return SessionCalendar(sessions, allowed_names)
