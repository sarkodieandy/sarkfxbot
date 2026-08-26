"""Point-in-time-safe swing and market-structure analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.models import Candle
from app.market.candles import validate_candle_sequence


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class MarketBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE = "RANGE"


class StructureBreakKind(StrEnum):
    BOS = "BOS"
    CHOCH = "CHOCH"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    kind: SwingKind
    index: int
    confirmed_index: int
    price: float


@dataclass(frozen=True, slots=True)
class StructureBreak:
    kind: StructureBreakKind
    direction: MarketBias
    index: int
    level: float
    swing_index: int


@dataclass(frozen=True, slots=True)
class StructureAnalysis:
    bias: MarketBias
    swings: tuple[SwingPoint, ...]
    breaks: tuple[StructureBreak, ...]
    higher_high: bool
    higher_low: bool
    lower_high: bool
    lower_low: bool
    support: float | None
    resistance: float | None

    @property
    def bullish_structure(self) -> bool:
        return self.bias is MarketBias.BULLISH and self.higher_high and self.higher_low

    @property
    def bearish_structure(self) -> bool:
        return self.bias is MarketBias.BEARISH and self.lower_high and self.lower_low


def confirmed_swings(
    candles: list[Candle] | tuple[Candle, ...], left_span: int = 2, right_span: int = 2
) -> tuple[SwingPoint, ...]:
    """Find pivots and record when each became knowable.

    A candidate at index ``i`` is not available to downstream logic until
    ``i + right_span``.  Backtests additionally provide only historical slices.
    """

    if left_span <= 0 or right_span <= 0:
        raise ValueError("swing spans must be positive")
    validate_candle_sequence(candles)
    swings: list[SwingPoint] = []
    for index in range(left_span, len(candles) - right_span):
        candidate = candles[index]
        left = candles[index - left_span : index]
        right = candles[index + 1 : index + right_span + 1]
        high_is_swing = (
            all(candidate.high > item.high for item in left)
            and all(candidate.high >= item.high for item in right)
            and any(candidate.high > item.high for item in right)
        )
        low_is_swing = (
            all(candidate.low < item.low for item in left)
            and all(candidate.low <= item.low for item in right)
            and any(candidate.low < item.low for item in right)
        )
        if high_is_swing:
            swings.append(SwingPoint(SwingKind.HIGH, index, index + right_span, candidate.high))
        if low_is_swing:
            swings.append(SwingPoint(SwingKind.LOW, index, index + right_span, candidate.low))
    swings.sort(key=lambda item: (item.confirmed_index, item.index, item.kind))
    return tuple(swings)


def _comparisons(
    swings: tuple[SwingPoint, ...],
) -> tuple[bool, bool, bool, bool]:
    highs = [item.price for item in swings if item.kind is SwingKind.HIGH]
    lows = [item.price for item in swings if item.kind is SwingKind.LOW]
    higher_high = len(highs) >= 2 and highs[-1] > highs[-2]
    lower_high = len(highs) >= 2 and highs[-1] < highs[-2]
    higher_low = len(lows) >= 2 and lows[-1] > lows[-2]
    lower_low = len(lows) >= 2 and lows[-1] < lows[-2]
    return higher_high, higher_low, lower_high, lower_low


def _bias_from_swings(swings: tuple[SwingPoint, ...]) -> MarketBias:
    higher_high, higher_low, lower_high, lower_low = _comparisons(swings)
    if higher_high and higher_low:
        return MarketBias.BULLISH
    if lower_high and lower_low:
        return MarketBias.BEARISH
    return MarketBias.RANGE


def _structure_breaks(
    candles: list[Candle] | tuple[Candle, ...], swings: tuple[SwingPoint, ...]
) -> tuple[StructureBreak, ...]:
    newly_confirmed: dict[int, list[SwingPoint]] = {}
    for swing in swings:
        newly_confirmed.setdefault(swing.confirmed_index, []).append(swing)

    known_highs: list[SwingPoint] = []
    known_lows: list[SwingPoint] = []
    broken: set[tuple[SwingKind, int]] = set()
    events: list[StructureBreak] = []
    bias = MarketBias.RANGE
    for index, candle in enumerate(candles):
        for swing in newly_confirmed.get(index, ()):
            if swing.kind is SwingKind.HIGH:
                known_highs.append(swing)
            else:
                known_lows.append(swing)
        prior_bias = bias
        higher_high = len(known_highs) >= 2 and known_highs[-1].price > known_highs[-2].price
        lower_high = len(known_highs) >= 2 and known_highs[-1].price < known_highs[-2].price
        higher_low = len(known_lows) >= 2 and known_lows[-1].price > known_lows[-2].price
        lower_low = len(known_lows) >= 2 and known_lows[-1].price < known_lows[-2].price
        if higher_high and higher_low:
            bias = MarketBias.BULLISH
        elif lower_high and lower_low:
            bias = MarketBias.BEARISH
        else:
            bias = MarketBias.RANGE
        if known_highs and known_highs[-1].index < index:
            level = known_highs[-1]
            key = (level.kind, level.index)
            if candle.close > level.price and key not in broken:
                kind = (
                    StructureBreakKind.CHOCH
                    if prior_bias is MarketBias.BEARISH
                    else StructureBreakKind.BOS
                )
                events.append(
                    StructureBreak(kind, MarketBias.BULLISH, index, level.price, level.index)
                )
                broken.add(key)
        if known_lows and known_lows[-1].index < index:
            level = known_lows[-1]
            key = (level.kind, level.index)
            if candle.close < level.price and key not in broken:
                kind = (
                    StructureBreakKind.CHOCH
                    if prior_bias is MarketBias.BULLISH
                    else StructureBreakKind.BOS
                )
                events.append(
                    StructureBreak(kind, MarketBias.BEARISH, index, level.price, level.index)
                )
                broken.add(key)
    return tuple(events)


def analyze_structure(
    candles: list[Candle] | tuple[Candle, ...], left_span: int = 2, right_span: int = 2
) -> StructureAnalysis:
    """Summarize swings, HH/HL/LH/LL, BOS/CHOCH, support, and resistance."""

    swings = confirmed_swings(candles, left_span, right_span)
    higher_high, higher_low, lower_high, lower_low = _comparisons(swings)
    bias = _bias_from_swings(swings)
    breaks = _structure_breaks(candles, swings)
    support = next((item.price for item in reversed(swings) if item.kind is SwingKind.LOW), None)
    resistance = next(
        (item.price for item in reversed(swings) if item.kind is SwingKind.HIGH), None
    )
    return StructureAnalysis(
        bias=bias,
        swings=swings,
        breaks=breaks,
        higher_high=higher_high,
        higher_low=higher_low,
        lower_high=lower_high,
        lower_low=lower_low,
        support=support,
        resistance=resistance,
    )
