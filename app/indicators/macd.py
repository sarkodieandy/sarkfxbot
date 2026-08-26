"""Moving-average convergence/divergence indicator."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.indicators.ema import ema


@dataclass(frozen=True, slots=True)
class MACDResult:
    """Three aligned MACD components."""

    line: tuple[float | None, ...]
    signal: tuple[float | None, ...]
    histogram: tuple[float | None, ...]


def macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """Calculate MACD without filling unavailable warm-up values."""

    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    if signal_period <= 0:
        raise ValueError("signal_period must be positive")
    fast = ema(values, fast_period)
    slow = ema(values, slow_period)
    line: list[float | None] = [
        fast_value - slow_value if fast_value is not None and slow_value is not None else None
        for fast_value, slow_value in zip(fast, slow, strict=True)
    ]

    first_line = next((index for index, value in enumerate(line) if value is not None), None)
    signal: list[float | None] = [None] * len(line)
    if first_line is not None:
        compact = [value for value in line[first_line:] if value is not None]
        compact_signal = ema(compact, signal_period)
        for offset, value in enumerate(compact_signal):
            signal[first_line + offset] = value
    histogram = [
        line_value - signal_value if line_value is not None and signal_value is not None else None
        for line_value, signal_value in zip(line, signal, strict=True)
    ]
    return MACDResult(tuple(line), tuple(signal), tuple(histogram))
