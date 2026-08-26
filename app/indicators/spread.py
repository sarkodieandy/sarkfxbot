"""Spread statistics used by strategy-quality filters."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum, isfinite


def average_spread(values: Sequence[float], period: int = 20) -> list[float | None]:
    """Return an aligned rolling average of broker-native spreads."""

    if period <= 0:
        raise ValueError("period must be positive")
    source = [float(value) for value in values]
    if any(value < 0 or not isfinite(value) for value in source):
        raise ValueError("spreads must be finite and non-negative")
    output: list[float | None] = [None] * len(source)
    if len(source) < period:
        return output
    rolling = fsum(source[:period])
    output[period - 1] = rolling / period
    for index in range(period, len(source)):
        rolling += source[index] - source[index - period]
        output[index] = rolling / period
    return output


def spread_quality(current: float, average: float | None, maximum: float | None = None) -> float:
    """Return a bounded 0..1 quality score; it is not a trade probability."""

    current_value = float(current)
    if current_value < 0 or not isfinite(current_value):
        raise ValueError("current spread must be finite and non-negative")
    if maximum is not None:
        maximum_value = float(maximum)
        if maximum_value <= 0 or not isfinite(maximum_value):
            raise ValueError("maximum spread must be finite and positive")
        if current_value > maximum_value:
            return 0.0
    if average is None or average <= 0:
        return 1.0 if current_value == 0.0 else 0.5
    ratio = current_value / average
    return max(0.0, min(1.0, 1.5 - (0.5 * ratio)))
