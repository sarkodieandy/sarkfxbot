from __future__ import annotations

import pytest

from app.indicators import (
    atr,
    average_spread,
    ema,
    macd,
    relative_volume,
    rsi,
    spread_quality,
    true_ranges,
    volume_sma,
)


def test_ema_uses_sma_seed_and_aligned_warmup() -> None:
    values = ema([1, 2, 3, 4, 5], 3)
    assert values[:2] == [None, None]
    assert values[2:] == pytest.approx([2.0, 3.0, 4.0])


def test_rsi_handles_up_down_and_flat_series() -> None:
    assert rsi([1, 2, 3, 4], 3)[-1] == 100.0
    assert rsi([4, 3, 2, 1], 3)[-1] == 0.0
    assert rsi([2, 2, 2, 2], 3)[-1] == 50.0


def test_true_range_and_wilder_atr_include_gaps() -> None:
    ranges = true_ranges([10, 13, 12], [8, 11, 9], [9, 12, 10])
    assert ranges == [2.0, 4.0, 3.0]
    assert atr([10, 13, 12], [8, 11, 9], [9, 12, 10], 2) == [None, 3.0, 3.0]


def test_macd_components_are_aligned_and_histogram_is_difference() -> None:
    result = macd(list(range(1, 15)), fast_period=3, slow_period=5, signal_period=2)
    assert len(result.line) == len(result.signal) == len(result.histogram) == 14
    assert result.line[:4] == (None, None, None, None)
    last_line = result.line[-1]
    last_signal = result.signal[-1]
    assert last_line is not None and last_signal is not None
    assert result.histogram[-1] == pytest.approx(last_line - last_signal)


def test_volume_and_spread_statistics_are_deterministic() -> None:
    assert volume_sma([10, 20, 30, 60], 3) == [None, None, 20.0, 110 / 3]
    assert relative_volume([10, 20, 30], 3) == [None, None, 1.5]
    assert average_spread([0.1, 0.2, 0.3], 2) == pytest.approx([None, 0.15, 0.25], nan_ok=True)
    assert spread_quality(0.5, 0.5, maximum=0.4) == 0.0
    assert spread_quality(0.5, 0.5, maximum=1.0) == 1.0


def test_invalid_indicator_inputs_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="period"):
        ema([1.0], 0)
    with pytest.raises(ValueError, match="same length"):
        atr([1.0], [0.0, 1.0], [0.5])
    with pytest.raises(ValueError, match="non-negative"):
        volume_sma([1.0, -1.0], 2)
