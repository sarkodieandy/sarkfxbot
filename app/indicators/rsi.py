"""Wilder relative-strength index."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum, isfinite


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_gain == 0.0 and average_loss == 0.0:
        return 50.0
    if average_loss == 0.0:
        return 100.0
    if average_gain == 0.0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Return RSI values aligned to closes using Wilder smoothing."""

    if period <= 0:
        raise ValueError("period must be positive")
    source = [float(value) for value in values]
    if any(not isfinite(value) for value in source):
        raise ValueError("indicator inputs must be finite")
    output: list[float | None] = [None] * len(source)
    if len(source) <= period:
        return output

    changes = [source[index] - source[index - 1] for index in range(1, len(source))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = fsum(gains[:period]) / period
    average_loss = fsum(losses[:period]) / period
    output[period] = _rsi_value(average_gain, average_loss)

    for change_index in range(period, len(changes)):
        average_gain = ((average_gain * (period - 1)) + gains[change_index]) / period
        average_loss = ((average_loss * (period - 1)) + losses[change_index]) / period
        output[change_index + 1] = _rsi_value(average_gain, average_loss)
    return output
