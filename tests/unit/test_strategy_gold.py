from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.enums import Direction, SignalAction, SignalStatus, Timeframe
from app.domain.models import Candle
from app.signals.engine import entry_zone_contains, refresh_signal_status
from app.strategies.gold_h1_m15_m5 import GoldStrategyConfig, GoldTrendPullbackStrategy


def _series(
    timeframe: Timeframe,
    closes: list[float],
    *,
    spread: float = 0.1,
) -> list[Candle]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    result: list[Candle] = []
    for index, close in enumerate(closes):
        open_price = close - 0.05
        result.append(
            Candle(
                symbol="XAUUSDm",
                timeframe=timeframe,
                timestamp=start + timedelta(seconds=timeframe.seconds * index),
                open=open_price,
                high=max(open_price, close) + 0.25,
                low=min(open_price, close) - 0.25,
                close=close,
                volume=100 + index,
                spread=spread,
            )
        )
    return result


def _strategy(maximum_spread: float = 1.0) -> GoldTrendPullbackStrategy:
    return GoldTrendPullbackStrategy(
        GoldStrategyConfig(
            ema_fast_period=2,
            ema_slow_period=4,
            ema_long_period=6,
            ema_slope_lookback=1,
            rsi_period=3,
            atr_period=3,
            structure_left_span=1,
            structure_right_span=1,
            pullback_atr_tolerance=3.0,
            breakout_lookback=2,
            spread_average_period=3,
            maximum_spread=maximum_spread,
            minimum_atr_fraction=0.0001,
            maximum_atr_fraction=0.5,
            long_rsi_minimum=0,
            long_rsi_maximum=100,
            short_rsi_minimum=0,
            short_rsi_maximum=100,
            confidence_threshold=75,
        )
    )


def _bullish_data(spread: float = 0.1) -> dict[Timeframe, list[Candle]]:
    rising_swings = [10, 12, 10.5, 13, 11.5, 14, 12.5, 15, 13.5, 16, 14.5, 17]
    m15 = [10, 11, 10.5, 12, 11.3, 13, 12.2, 14, 13.2, 15, 14.4, 15.2]
    m5 = [10, 11, 10.5, 12, 11.3, 13, 12.2, 14, 13.0, 14.5, 13.5, 15.0]
    m5_candles = _series(Timeframe.M5, m5, spread=spread)
    m5_candles[-2] = replace(m5_candles[-2], open=14.2, high=14.45, low=13.25, close=13.5)
    m5_candles[-1] = replace(m5_candles[-1], open=13.3, high=15.25, low=13.05, close=15.0)
    return {
        Timeframe.H1: _series(Timeframe.H1, rising_swings, spread=spread),
        Timeframe.M15: _series(Timeframe.M15, m15, spread=spread),
        Timeframe.M5: m5_candles,
    }


def _bearish_data(spread: float = 0.1) -> dict[Timeframe, list[Candle]]:
    falling_swings = [20, 18, 19.5, 17, 18.5, 16, 17.5, 15, 16.5, 14, 15.5, 13]
    m15 = [20, 19, 19.5, 18, 18.7, 17, 17.8, 16, 16.8, 15, 15.6, 14.8]
    m5 = [20, 19, 19.5, 18, 18.7, 17, 17.8, 16, 17, 15.5, 16.5, 15]
    m5_candles = _series(Timeframe.M5, m5, spread=spread)
    m5_candles[-2] = replace(m5_candles[-2], open=15.8, high=16.75, low=15.55, close=16.5)
    m5_candles[-1] = replace(m5_candles[-1], open=16.7, high=16.95, low=14.75, close=15.0)
    return {
        Timeframe.H1: _series(Timeframe.H1, falling_swings, spread=spread),
        Timeframe.M15: _series(Timeframe.M15, m15, spread=spread),
        Timeframe.M5: m5_candles,
    }


def test_strategy_emits_deterministic_protected_long() -> None:
    strategy = _strategy()
    data = _bullish_data()
    first = strategy.evaluate(data)
    second = strategy.evaluate(data)
    assert first == second
    assert first.action is SignalAction.LONG
    assert first.stop_loss is not None and first.entry_min is not None
    assert first.stop_loss < first.entry_min
    assert first.take_profits
    assert first.risk_reward == Decimal("1.8")
    assert first.rationale["confidence_is_probability"] is False


def test_strategy_never_trades_above_configured_spread() -> None:
    signal = _strategy(maximum_spread=0.05).evaluate(_bullish_data(spread=0.1))
    assert signal.action is SignalAction.WAIT
    assert signal.rationale["reason"] == "TRADE_SKIPPED_HIGH_SPREAD"


def test_strategy_mirrors_conditions_for_a_protected_short() -> None:
    signal = _strategy().evaluate(_bearish_data())
    assert signal.action is SignalAction.SHORT
    assert signal.stop_loss is not None and signal.entry_max is not None
    assert signal.stop_loss > signal.entry_max
    assert all(target < signal.entry_min for target in signal.take_profits)


def test_open_position_exits_on_opposing_h1_trend() -> None:
    signal = _strategy().evaluate(_bearish_data(), open_direction=Direction.LONG)
    assert signal.action is SignalAction.EXIT


def test_entry_zone_and_signal_expiration_are_enforced() -> None:
    signal = _strategy().evaluate(_bullish_data())
    if signal.action is SignalAction.WAIT:
        assert not entry_zone_contains(signal, Decimal("15"))
        return
    assert signal.entry_min is not None and signal.expires_at is not None
    assert entry_zone_contains(signal, signal.entry_min)
    expired = refresh_signal_status(signal, now=signal.expires_at)
    assert expired.status is SignalStatus.EXPIRED
    assert expired.rationale["expiration_reason"] == "SIGNAL_TIME_EXPIRED"
