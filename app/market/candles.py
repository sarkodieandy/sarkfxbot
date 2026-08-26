"""Candle validation and point-in-time-safe selection."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from app.domain.models import Candle


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(UTC)


def candle_close_time(candle: Candle) -> datetime:
    """Return the instant at which a bar is fully known."""

    return candle.timestamp + timedelta(seconds=candle.timeframe.seconds)


def validate_candle_sequence(candles: Sequence[Candle]) -> tuple[Candle, ...]:
    """Validate a homogeneous, strictly increasing candle sequence."""

    if not candles:
        return ()
    symbol = candles[0].symbol
    timeframe = candles[0].timeframe
    previous_timestamp: datetime | None = None
    for candle in candles:
        if candle.symbol != symbol or candle.timeframe is not timeframe:
            raise ValueError("a candle sequence must use one symbol and timeframe")
        if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
            raise ValueError("candle timestamps must be strictly increasing")
        previous_timestamp = candle.timestamp
    return tuple(candles)


def closed_candles(
    candles: Iterable[Candle],
    *,
    as_of: datetime | None = None,
    use_closed_candles_only: bool = True,
) -> tuple[Candle, ...]:
    """Sort and select candles that were available at ``as_of``.

    A candle is unavailable before its timeframe close even if a data source
    incorrectly marks it complete.  Conversely, ``complete=False`` always
    excludes a candle when closed-candle safety is enabled.
    """

    ordered = sorted(candles, key=lambda item: item.timestamp)
    validate_candle_sequence(ordered)
    reference = _as_utc(as_of) if as_of is not None else None
    selected: list[Candle] = []
    for candle in ordered:
        if reference is not None:
            availability = (
                candle_close_time(candle) if use_closed_candles_only else candle.timestamp
            )
            if availability > reference:
                continue
        if use_closed_candles_only and not candle.complete:
            continue
        selected.append(candle)
    return tuple(selected)


def latest_available_candles(
    candles: Iterable[Candle], as_of: datetime, count: int
) -> tuple[Candle, ...]:
    """Return at most ``count`` closed bars available at a historical instant."""

    if count <= 0:
        raise ValueError("count must be positive")
    available = closed_candles(candles, as_of=as_of)
    return available[-count:]
