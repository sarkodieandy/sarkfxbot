"""True range and Wilder average true range."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum, isfinite


def _validate_ohlc(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> tuple[list[float], list[float], list[float]]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("high, low, and close sequences must be the same length")
    high_values = [float(value) for value in highs]
    low_values = [float(value) for value in lows]
    close_values = [float(value) for value in closes]
    all_values = high_values + low_values + close_values
    if any(not isfinite(value) for value in all_values):
        raise ValueError("indicator inputs must be finite")
    if any(high < low for high, low in zip(high_values, low_values, strict=True)):
        raise ValueError("a high cannot be below its low")
    return high_values, low_values, close_values


def true_ranges(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    """Return true range for each bar; the first bar uses its own range."""

    high_values, low_values, close_values = _validate_ohlc(highs, lows, closes)
    output: list[float] = []
    for index, (high, low) in enumerate(zip(high_values, low_values, strict=True)):
        if index == 0:
            output.append(high - low)
            continue
        previous_close = close_values[index - 1]
        output.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return output


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    """Return Wilder ATR values aligned to the input bars."""

    if period <= 0:
        raise ValueError("period must be positive")
    ranges = true_ranges(highs, lows, closes)
    output: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return output
    previous = fsum(ranges[:period]) / period
    output[period - 1] = previous
    for index in range(period, len(ranges)):
        previous = ((previous * (period - 1)) + ranges[index]) / period
        output[index] = previous
    return output
