"""Volume-derived helpers."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum, isfinite


def volume_sma(values: Sequence[float], period: int = 20) -> list[float | None]:
    """Return a rolling arithmetic mean for tick or exchange volume."""

    if period <= 0:
        raise ValueError("period must be positive")
    source = [float(value) for value in values]
    if any(value < 0 or not isfinite(value) for value in source):
        raise ValueError("volumes must be finite and non-negative")
    output: list[float | None] = [None] * len(source)
    if len(source) < period:
        return output
    window_sum = fsum(source[:period])
    output[period - 1] = window_sum / period
    for index in range(period, len(source)):
        window_sum += source[index] - source[index - period]
        output[index] = window_sum / period
    return output


def relative_volume(values: Sequence[float], period: int = 20) -> list[float | None]:
    """Return current volume divided by its rolling mean."""

    source = [float(value) for value in values]
    averages = volume_sma(source, period)
    return [
        (value / average if average and average > 0.0 else 0.0) if average is not None else None
        for value, average in zip(source, averages, strict=True)
    ]
