"""Gold H1-M15-M5 Trend Pullback Strategy, version 1.0.0."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.enums import Direction, SignalAction, SignalStatus, Timeframe
from app.domain.models import Candle, TradeSignal
from app.indicators.atr import atr
from app.indicators.ema import ema
from app.indicators.rsi import rsi
from app.indicators.spread import average_spread, spread_quality
from app.market.candles import candle_close_time, closed_candles
from app.market.structure import MarketBias, StructureBreakKind, analyze_structure
from app.signals.confidence import (
    ConfidenceBreakdown,
    ConfidenceFactors,
    ConfidenceWeights,
    score_confidence,
)


@dataclass(frozen=True, slots=True)
class GoldStrategyConfig:
    """Configurable thresholds; none of these values are broker assumptions."""

    ema_fast_period: int = 20
    ema_slow_period: int = 50
    ema_long_period: int = 200
    require_ema_long_alignment: bool = False
    ema_slope_lookback: int = 3
    rsi_period: int = 14
    atr_period: int = 14
    structure_left_span: int = 2
    structure_right_span: int = 2
    pullback_atr_tolerance: float = 1.25
    rejection_wick_ratio: float = 1.5
    breakout_lookback: int = 5
    maximum_spread: float | None = None
    spread_average_period: int = 20
    minimum_atr_fraction: float = 0.0001
    maximum_atr_fraction: float = 0.02
    long_rsi_minimum: float = 40.0
    long_rsi_maximum: float = 75.0
    short_rsi_minimum: float = 25.0
    short_rsi_maximum: float = 60.0
    confidence_threshold: int = 75
    minimum_risk_reward: float = 1.8
    preferred_risk_reward: float = 2.0
    entry_zone_atr_fraction: float = 0.15
    stop_atr_multiple: float = 1.25
    stop_buffer_atr_fraction: float = 0.10
    signal_expiry_bars: int = 3
    weights: ConfidenceWeights = field(default_factory=ConfidenceWeights)
    use_closed_candles_only: bool = True

    def __post_init__(self) -> None:
        periods = (
            self.ema_fast_period,
            self.ema_slow_period,
            self.ema_long_period,
            self.ema_slope_lookback,
            self.rsi_period,
            self.atr_period,
            self.structure_left_span,
            self.structure_right_span,
            self.breakout_lookback,
            self.spread_average_period,
            self.signal_expiry_bars,
        )
        if any(value <= 0 for value in periods):
            raise ValueError("strategy periods and lookbacks must be positive")
        if self.ema_fast_period >= self.ema_slow_period:
            raise ValueError("fast EMA period must be below slow EMA period")
        if self.ema_long_period <= self.ema_slow_period:
            raise ValueError("long EMA period must exceed slow EMA period")
        if not 0 <= self.confidence_threshold <= 100:
            raise ValueError("confidence threshold must be between 0 and 100")
        if self.minimum_risk_reward <= 0:
            raise ValueError("minimum risk/reward must be positive")
        if self.preferred_risk_reward < self.minimum_risk_reward:
            raise ValueError("preferred risk/reward cannot be below minimum")
        positive = (
            self.pullback_atr_tolerance,
            self.rejection_wick_ratio,
            self.minimum_atr_fraction,
            self.maximum_atr_fraction,
            self.entry_zone_atr_fraction,
            self.stop_atr_multiple,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("strategy distance/volatility thresholds must be positive")
        if self.minimum_atr_fraction >= self.maximum_atr_fraction:
            raise ValueError("minimum ATR fraction must be below maximum")
        if self.maximum_spread is not None and self.maximum_spread <= 0:
            raise ValueError("maximum spread must be positive when configured")


@dataclass(frozen=True, slots=True)
class _DirectionEvaluation:
    direction: Direction
    h1_trend: bool
    m15_pullback: bool
    m5_confirmation: bool
    structure_alignment: bool
    spread_quality: float
    volatility_quality: float
    confidence: ConfidenceBreakdown
    confirmation_names: tuple[str, ...]


def _required_float(value: float | None, name: str) -> float:
    if value is None:
        raise ValueError(f"{name} is unavailable during indicator warm-up")
    return value


def _bullish_engulfing(previous: Candle, current: Candle) -> bool:
    return (
        previous.close < previous.open
        and current.close > current.open
        and current.open <= previous.close
        and current.close >= previous.open
    )


def _bearish_engulfing(previous: Candle, current: Candle) -> bool:
    return (
        previous.close > previous.open
        and current.close < current.open
        and current.open >= previous.close
        and current.close <= previous.open
    )


def _rejection(candle: Candle, direction: Direction, ratio: float) -> bool:
    body = max(abs(candle.close - candle.open), (candle.high - candle.low) * 0.05)
    lower_wick = min(candle.open, candle.close) - candle.low
    upper_wick = candle.high - max(candle.open, candle.close)
    if direction is Direction.LONG:
        return candle.close > candle.open and lower_wick >= body * ratio
    return candle.close < candle.open and upper_wick >= body * ratio


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 8)))


class GoldTrendPullbackStrategy:
    """Closed-candle, broker-independent H1 trend/M15 pullback/M5 entry logic."""

    strategy_id = "gold_h1_m15_m5"
    strategy_version = "1.0.0"
    strategy_name = "Gold H1-M15-M5 Trend Pullback Strategy"

    def __init__(
        self,
        config: GoldStrategyConfig | None = None,
        *,
        strategy_version: str | None = None,
    ) -> None:
        self.config = config or GoldStrategyConfig()
        if strategy_version is not None:
            if not strategy_version.strip():
                raise ValueError("strategy version cannot be blank")
            self.strategy_version = strategy_version.strip()

    def _prepare(
        self,
        candles: Mapping[Timeframe, Sequence[Candle]],
        as_of: datetime | None,
    ) -> dict[Timeframe, tuple[Candle, ...]]:
        required = (Timeframe.H1, Timeframe.M15, Timeframe.M5)
        missing = [timeframe.value for timeframe in required if timeframe not in candles]
        if missing:
            raise ValueError(f"missing required timeframes: {', '.join(missing)}")
        return {
            timeframe: closed_candles(
                candles[timeframe],
                as_of=as_of,
                use_closed_candles_only=self.config.use_closed_candles_only,
            )
            for timeframe in required
        }

    def _has_history(self, series: Mapping[Timeframe, Sequence[Candle]]) -> bool:
        h1_required = max(
            self.config.ema_slow_period + self.config.ema_slope_lookback,
            self.config.ema_long_period if self.config.require_ema_long_alignment else 0,
            (self.config.structure_left_span + self.config.structure_right_span + 1) * 3,
        )
        m15_required = max(self.config.ema_slow_period, self.config.atr_period + 1)
        m5_required = max(
            self.config.atr_period + 1,
            self.config.rsi_period + 1,
            self.config.breakout_lookback + 2,
            self.config.spread_average_period,
        )
        return (
            len(series[Timeframe.H1]) >= h1_required
            and len(series[Timeframe.M15]) >= m15_required
            and len(series[Timeframe.M5]) >= m5_required
        )

    def _trend_flags(
        self, h1: Sequence[Candle]
    ) -> tuple[bool, bool, bool, bool, dict[str, object]]:
        closes = [candle.close for candle in h1]
        fast = ema(closes, self.config.ema_fast_period)
        slow = ema(closes, self.config.ema_slow_period)
        latest_fast = _required_float(fast[-1], "H1 fast EMA")
        latest_slow = _required_float(slow[-1], "H1 slow EMA")
        earlier_slow = _required_float(
            slow[-1 - self.config.ema_slope_lookback], "H1 prior slow EMA"
        )
        long_value: float | None = None
        if self.config.require_ema_long_alignment:
            long_value = _required_float(
                ema(closes, self.config.ema_long_period)[-1], "H1 long EMA"
            )
        structure = analyze_structure(
            list(h1), self.config.structure_left_span, self.config.structure_right_span
        )
        latest_close = closes[-1]
        long_ema = (
            latest_close > latest_fast > latest_slow
            and latest_slow > earlier_slow
            and (long_value is None or latest_close > long_value)
        )
        short_ema = (
            latest_close < latest_fast < latest_slow
            and latest_slow < earlier_slow
            and (long_value is None or latest_close < long_value)
        )
        recent_break = structure.breaks[-1] if structure.breaks else None
        bullish_structure = structure.bullish_structure or (
            recent_break is not None
            and recent_break.direction is MarketBias.BULLISH
            and recent_break.kind in (StructureBreakKind.BOS, StructureBreakKind.CHOCH)
        )
        bearish_structure = structure.bearish_structure or (
            recent_break is not None
            and recent_break.direction is MarketBias.BEARISH
            and recent_break.kind in (StructureBreakKind.BOS, StructureBreakKind.CHOCH)
        )
        context: dict[str, object] = {
            "close": latest_close,
            "ema_fast": latest_fast,
            "ema_slow": latest_slow,
            "ema_slow_slope": latest_slow - earlier_slow,
            "ema_long": long_value,
            "structure_bias": structure.bias.value,
            "higher_high": structure.higher_high,
            "higher_low": structure.higher_low,
            "lower_high": structure.lower_high,
            "lower_low": structure.lower_low,
            "support": structure.support,
            "resistance": structure.resistance,
        }
        return long_ema, short_ema, bullish_structure, bearish_structure, context

    def _pullback_flags(self, m15: Sequence[Candle]) -> tuple[bool, bool, dict[str, object]]:
        closes = [candle.close for candle in m15]
        fast_value = _required_float(ema(closes, self.config.ema_fast_period)[-1], "M15 fast EMA")
        slow_value = _required_float(ema(closes, self.config.ema_slow_period)[-1], "M15 slow EMA")
        atr_value = _required_float(
            atr(
                [candle.high for candle in m15],
                [candle.low for candle in m15],
                closes,
                self.config.atr_period,
            )[-1],
            "M15 ATR",
        )
        latest = m15[-1]
        tolerance = atr_value * self.config.pullback_atr_tolerance
        touched_zone = (
            latest.low <= fast_value + tolerance and latest.high >= fast_value - tolerance
        )
        structure = analyze_structure(
            list(m15), self.config.structure_left_span, self.config.structure_right_span
        )
        recent_break = structure.breaks[-1] if structure.breaks else None
        bearish_break = recent_break is not None and recent_break.direction is MarketBias.BEARISH
        bullish_break = recent_break is not None and recent_break.direction is MarketBias.BULLISH
        long_pullback = touched_zone and latest.close >= slow_value and not bearish_break
        short_pullback = touched_zone and latest.close <= slow_value and not bullish_break
        return (
            long_pullback,
            short_pullback,
            {
                "close": latest.close,
                "ema_fast": fast_value,
                "ema_slow": slow_value,
                "atr": atr_value,
                "touched_ema_zone": touched_zone,
                "latest_structure_break": recent_break.direction.value if recent_break else None,
            },
        )

    def _confirmation_flags(
        self, m5: Sequence[Candle]
    ) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, object]]:
        previous, latest = m5[-2], m5[-1]
        earlier = m5[-1 - self.config.breakout_lookback : -1]
        structure = analyze_structure(
            list(m5), self.config.structure_left_span, self.config.structure_right_span
        )
        long_confirmations: list[str] = []
        short_confirmations: list[str] = []
        if _bullish_engulfing(previous, latest):
            long_confirmations.append("BULLISH_ENGULFING")
        if _bearish_engulfing(previous, latest):
            short_confirmations.append("BEARISH_ENGULFING")
        if _rejection(latest, Direction.LONG, self.config.rejection_wick_ratio):
            long_confirmations.append("BULLISH_REJECTION")
        if _rejection(latest, Direction.SHORT, self.config.rejection_wick_ratio):
            short_confirmations.append("BEARISH_REJECTION")
        if earlier and latest.close > max(candle.high for candle in earlier):
            long_confirmations.append("LOCAL_HIGH_BREAK")
        if earlier and latest.close < min(candle.low for candle in earlier):
            short_confirmations.append("LOCAL_SUPPORT_BREAK")
        if structure.higher_low:
            long_confirmations.append("HIGHER_LOW")
        if structure.lower_high:
            short_confirmations.append("LOWER_HIGH")
        return (
            tuple(long_confirmations),
            tuple(short_confirmations),
            {
                "long_confirmations": tuple(long_confirmations),
                "short_confirmations": tuple(short_confirmations),
                "structure_bias": structure.bias.value,
            },
        )

    def _quality(
        self, m5: Sequence[Candle]
    ) -> tuple[float, float, float, float, dict[str, object]]:
        closes = [candle.close for candle in m5]
        atr_value = _required_float(
            atr(
                [candle.high for candle in m5],
                [candle.low for candle in m5],
                closes,
                self.config.atr_period,
            )[-1],
            "M5 ATR",
        )
        rsi_value = _required_float(rsi(closes, self.config.rsi_period)[-1], "M5 RSI")
        average = average_spread(
            [candle.spread for candle in m5], self.config.spread_average_period
        )[-1]
        spread_score = spread_quality(m5[-1].spread, average, self.config.maximum_spread)
        atr_fraction = atr_value / m5[-1].close if m5[-1].close > 0 else 0.0
        volatility_score = float(
            self.config.minimum_atr_fraction <= atr_fraction <= self.config.maximum_atr_fraction
        )
        long_rsi = float(self.config.long_rsi_minimum <= rsi_value <= self.config.long_rsi_maximum)
        short_rsi = float(
            self.config.short_rsi_minimum <= rsi_value <= self.config.short_rsi_maximum
        )
        return (
            spread_score,
            volatility_score,
            long_rsi,
            short_rsi,
            {
                "atr": atr_value,
                "atr_fraction": atr_fraction,
                "rsi": rsi_value,
                "spread": m5[-1].spread,
                "average_spread": average,
            },
        )

    def _evaluate_directions(
        self, series: Mapping[Timeframe, Sequence[Candle]]
    ) -> tuple[_DirectionEvaluation, _DirectionEvaluation, dict[str, object]]:
        long_ema, short_ema, bullish_structure, bearish_structure, h1_context = self._trend_flags(
            series[Timeframe.H1]
        )
        long_pullback, short_pullback, m15_context = self._pullback_flags(series[Timeframe.M15])
        long_confirmations, short_confirmations, m5_context = self._confirmation_flags(
            series[Timeframe.M5]
        )
        spread_score, volatility_score, long_rsi, short_rsi, quality_context = self._quality(
            series[Timeframe.M5]
        )

        def build(
            direction: Direction,
            trend: bool,
            pullback: bool,
            confirmations: tuple[str, ...],
            structure: bool,
            rsi_quality: float,
        ) -> _DirectionEvaluation:
            factors = ConfidenceFactors(
                h1_trend=float(trend),
                m15_pullback=float(pullback),
                m5_confirmation=float(bool(confirmations)),
                structure_alignment=float(structure),
                spread_quality=spread_score,
                volatility_quality=volatility_score * rsi_quality,
            )
            return _DirectionEvaluation(
                direction=direction,
                h1_trend=trend,
                m15_pullback=pullback,
                m5_confirmation=bool(confirmations),
                structure_alignment=structure,
                spread_quality=spread_score,
                volatility_quality=volatility_score * rsi_quality,
                confidence=score_confidence(factors, self.config.weights),
                confirmation_names=confirmations,
            )

        return (
            build(
                Direction.LONG,
                long_ema and bullish_structure,
                long_pullback,
                long_confirmations,
                bullish_structure,
                long_rsi,
            ),
            build(
                Direction.SHORT,
                short_ema and bearish_structure,
                short_pullback,
                short_confirmations,
                bearish_structure,
                short_rsi,
            ),
            {"h1": h1_context, "m15": m15_context, "m5": m5_context, "quality": quality_context},
        )

    def _timestamp(self, series: Mapping[Timeframe, Sequence[Candle]]) -> datetime:
        latest = series[Timeframe.M5][-1]
        return candle_close_time(latest).astimezone(UTC)

    def _signal_id(self, symbol: str, action: SignalAction, created_at: datetime) -> UUID:
        value = ":".join(
            (self.strategy_id, self.strategy_version, symbol, action.value, created_at.isoformat())
        )
        return uuid5(NAMESPACE_URL, value)

    def _wait_or_exit(
        self,
        series: Mapping[Timeframe, Sequence[Candle]],
        action: SignalAction,
        confidence: int,
        rationale: dict[str, object],
    ) -> TradeSignal:
        created_at = self._timestamp(series)
        symbol = series[Timeframe.M5][-1].symbol
        return TradeSignal(
            symbol=symbol,
            canonical_symbol="XAUUSD",
            action=action,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            confidence_score=confidence,
            entry_min=None,
            entry_max=None,
            stop_loss=None,
            rationale=rationale,
            status=SignalStatus.ACTIVE,
            created_at=created_at,
            signal_id=self._signal_id(symbol, action, created_at),
        )

    def _trade_signal(
        self,
        series: Mapping[Timeframe, Sequence[Candle]],
        evaluation: _DirectionEvaluation,
        rationale: dict[str, object],
    ) -> TradeSignal:
        latest = series[Timeframe.M5][-1]
        m5 = series[Timeframe.M5]
        atr_value = _required_float(
            atr(
                [candle.high for candle in m5],
                [candle.low for candle in m5],
                [candle.close for candle in m5],
                self.config.atr_period,
            )[-1],
            "M5 ATR",
        )
        half_zone = atr_value * self.config.entry_zone_atr_fraction
        entry_min = latest.close - half_zone
        entry_max = latest.close + half_zone
        reference = (entry_min + entry_max) / 2.0
        recent = m5[-max(self.config.breakout_lookback * 2, self.config.atr_period) :]
        buffer = atr_value * self.config.stop_buffer_atr_fraction
        if evaluation.direction is Direction.LONG:
            structural_stop = min(candle.low for candle in recent) - buffer
            stop = min(structural_stop, entry_min - (atr_value * self.config.stop_atr_multiple))
            risk_distance = reference - stop
            take_profit_1 = reference + risk_distance * self.config.minimum_risk_reward
            take_profit_2 = reference + risk_distance * self.config.preferred_risk_reward
            action = SignalAction.LONG
        else:
            structural_stop = max(candle.high for candle in recent) + buffer
            stop = max(structural_stop, entry_max + (atr_value * self.config.stop_atr_multiple))
            risk_distance = stop - reference
            take_profit_1 = reference - risk_distance * self.config.minimum_risk_reward
            take_profit_2 = reference - risk_distance * self.config.preferred_risk_reward
            action = SignalAction.SHORT
        created_at = self._timestamp(series)
        expiry = created_at + timedelta(
            seconds=Timeframe.M5.seconds * self.config.signal_expiry_bars
        )
        targets: tuple[float, ...] = (take_profit_1,)
        if take_profit_2 != take_profit_1:
            targets += (take_profit_2,)
        return TradeSignal(
            symbol=latest.symbol,
            canonical_symbol="XAUUSD",
            action=action,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            confidence_score=evaluation.confidence.score,
            entry_min=_decimal(entry_min),
            entry_max=_decimal(entry_max),
            stop_loss=_decimal(stop),
            take_profits=tuple(_decimal(value) for value in targets),
            risk_reward=_decimal(self.config.minimum_risk_reward),
            rationale=rationale,
            status=SignalStatus.ACTIVE,
            created_at=created_at,
            expires_at=expiry,
            signal_id=self._signal_id(latest.symbol, action, created_at),
        )

    def evaluate(
        self,
        candles: Mapping[Timeframe, Sequence[Candle]],
        *,
        as_of: datetime | None = None,
        open_direction: Direction | None = None,
    ) -> TradeSignal:
        """Return exactly LONG, SHORT, WAIT, or EXIT using available closed bars."""

        series = self._prepare(candles, as_of)
        if not all(series.values()):
            raise ValueError("each strategy timeframe needs at least one closed candle")
        if not self._has_history(series):
            return self._wait_or_exit(
                series,
                SignalAction.WAIT,
                0,
                {"reason": "INDICATOR_WARMUP", "confidence_is_probability": False},
            )
        long_evaluation, short_evaluation, context = self._evaluate_directions(series)
        rationale: dict[str, object] = {
            **context,
            "long_confidence": long_evaluation.confidence.score,
            "short_confidence": short_evaluation.confidence.score,
            "confidence_components": {
                "long": long_evaluation.confidence.components,
                "short": short_evaluation.confidence.components,
            },
            "confidence_is_probability": False,
        }
        if open_direction is Direction.LONG and short_evaluation.h1_trend:
            return self._wait_or_exit(
                series,
                SignalAction.EXIT,
                short_evaluation.confidence.score,
                {**rationale, "reason": "H1_BEARISH_REVERSAL"},
            )
        if open_direction is Direction.SHORT and long_evaluation.h1_trend:
            return self._wait_or_exit(
                series,
                SignalAction.EXIT,
                long_evaluation.confidence.score,
                {**rationale, "reason": "H1_BULLISH_REVERSAL"},
            )
        if open_direction is not None:
            return self._wait_or_exit(
                series,
                SignalAction.WAIT,
                max(long_evaluation.confidence.score, short_evaluation.confidence.score),
                {**rationale, "reason": "POSITION_ALREADY_OPEN"},
            )

        candidates = sorted(
            (long_evaluation, short_evaluation),
            key=lambda item: (item.confidence.score, item.direction is Direction.LONG),
            reverse=True,
        )
        selected = candidates[0]
        all_required = selected.h1_trend and selected.m15_pullback and selected.m5_confirmation
        spread_allowed = selected.spread_quality > 0.0
        if (
            all_required
            and spread_allowed
            and selected.confidence.score >= self.config.confidence_threshold
        ):
            rationale["selected_direction"] = selected.direction.value
            rationale["entry_confirmations"] = selected.confirmation_names
            return self._trade_signal(series, selected, rationale)
        reason = "TRADE_SKIPPED_HIGH_SPREAD" if not spread_allowed else "CONDITIONS_NOT_MET"
        return self._wait_or_exit(
            series,
            SignalAction.WAIT,
            selected.confidence.score,
            {**rationale, "reason": reason},
        )
