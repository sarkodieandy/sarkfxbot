"""Chronological train/validation/test and rolling walk-forward splits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import Timeframe
from app.domain.models import Candle


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    train: dict[Timeframe, tuple[Candle, ...]]
    validation: dict[Timeframe, tuple[Candle, ...]]
    test: dict[Timeframe, tuple[Candle, ...]]


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    index: int
    data: DatasetSplit


def _slice_by_bounds(
    candles: Mapping[Timeframe, Sequence[Candle]],
    starts_at: datetime,
    ends_before: datetime,
) -> dict[Timeframe, tuple[Candle, ...]]:
    return {
        timeframe: tuple(
            candle
            for candle in values
            if candle.timestamp >= starts_at and candle.timestamp < ends_before
        )
        for timeframe, values in candles.items()
    }


def _boundaries(
    m5: Sequence[Candle],
    start: int,
    train_end: int,
    validation_end: int,
    test_end: int,
) -> tuple[datetime, datetime, datetime, datetime]:
    final_step = m5[-1].timestamp - m5[-2].timestamp if len(m5) >= 2 else None
    if final_step is None:
        raise ValueError("at least two M5 bars are required for chronological splitting")
    return (
        m5[start].timestamp,
        m5[train_end].timestamp,
        m5[validation_end].timestamp,
        m5[test_end - 1].timestamp + final_step,
    )


def chronological_split(
    candles: Mapping[Timeframe, Sequence[Candle]],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> DatasetSplit:
    """Make disjoint chronological datasets; the test set is never optimized."""

    if Timeframe.M5 not in candles:
        raise ValueError("M5 data is required to establish split boundaries")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation fractions must leave a test set")
    m5 = candles[Timeframe.M5]
    if len(m5) < 5:
        raise ValueError("at least five M5 bars are required")
    train_end = max(1, int(len(m5) * train_fraction))
    validation_end = max(train_end + 1, int(len(m5) * (train_fraction + validation_fraction)))
    validation_end = min(validation_end, len(m5) - 1)
    start, train_boundary, validation_boundary, end = _boundaries(
        m5, 0, train_end, validation_end, len(m5)
    )
    return DatasetSplit(
        train=_slice_by_bounds(candles, start, train_boundary),
        validation=_slice_by_bounds(candles, train_boundary, validation_boundary),
        test=_slice_by_bounds(candles, validation_boundary, end),
    )


def rolling_walk_forward_splits(
    candles: Mapping[Timeframe, Sequence[Candle]],
    *,
    train_bars: int,
    validation_bars: int,
    test_bars: int,
    step_bars: int | None = None,
) -> tuple[WalkForwardFold, ...]:
    """Return fixed-width rolling folds with strictly later out-of-sample tests."""

    values = (train_bars, validation_bars, test_bars)
    if any(value <= 0 for value in values):
        raise ValueError("walk-forward window sizes must be positive")
    if Timeframe.M5 not in candles:
        raise ValueError("M5 data is required to establish split boundaries")
    step = test_bars if step_bars is None else step_bars
    if step <= 0:
        raise ValueError("walk-forward step must be positive")
    m5 = candles[Timeframe.M5]
    window = train_bars + validation_bars + test_bars
    folds: list[WalkForwardFold] = []
    for fold_index, offset in enumerate(range(0, len(m5) - window + 1, step)):
        train_end = offset + train_bars
        validation_end = train_end + validation_bars
        test_end = validation_end + test_bars
        start, train_boundary, validation_boundary, end = _boundaries(
            m5, offset, train_end, validation_end, test_end
        )
        folds.append(
            WalkForwardFold(
                fold_index,
                DatasetSplit(
                    train=_slice_by_bounds(candles, start, train_boundary),
                    validation=_slice_by_bounds(candles, train_boundary, validation_boundary),
                    test=_slice_by_bounds(candles, validation_boundary, end),
                ),
            )
        )
    return tuple(folds)
