"""Exponential moving-average calculations."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum, isfinite


def _validated(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    result = [float(value) for value in values]
    if any(not isfinite(value) for value in result):
        raise ValueError("indicator inputs must be finite")
    return result


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Return a standard EMA seeded with an initial simple moving average."""

    source = _validated(values, period)
    output: list[float | None] = [None] * len(source)
    if len(source) < period:
        return output

    previous = fsum(source[:period]) / period
    output[period - 1] = previous
    multiplier = 2.0 / (period + 1.0)
    for index in range(period, len(source)):
        previous = ((source[index] - previous) * multiplier) + previous
        output[index] = previous
    return output


def ema_last(values: Sequence[float], period: int) -> float | None:
    """Return only the latest EMA value, or ``None`` during warm-up."""

    values_out = ema(values, period)
    return values_out[-1] if values_out else None
