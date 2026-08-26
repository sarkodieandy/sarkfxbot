from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import Timeframe
from app.domain.errors import SymbolResolutionError
from app.domain.models import Candle, SymbolSpecification
from app.market.candles import candle_close_time, closed_candles
from app.market.news import DisabledNewsFilter
from app.market.sessions import SessionCalendar, TradingSession
from app.market.structure import MarketBias, SwingKind, analyze_structure, confirmed_swings
from app.market.symbols import canonicalize_symbol, resolve_symbol


def _candles(closes: list[float], timeframe: Timeframe = Timeframe.M5) -> list[Candle]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    return [
        Candle(
            symbol="XAUUSDm",
            timeframe=timeframe,
            timestamp=start + timedelta(seconds=timeframe.seconds * index),
            open=close - 0.05,
            high=close + 0.2,
            low=close - 0.2,
            close=close,
            volume=100,
            spread=0.2,
        )
        for index, close in enumerate(closes)
    ]


def _symbol(name: str, *, enabled: bool = True) -> SymbolSpecification:
    return SymbolSpecification(
        name=name,
        canonical_symbol="XAUUSD",
        base_currency="XAU",
        quote_currency="USD",
        digits=2,
        point=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        trade_enabled=enabled,
    )


def test_closed_candle_selection_never_exposes_an_unfinished_bar() -> None:
    values = _candles([10, 11])
    first_close = candle_close_time(values[0])
    assert closed_candles(values, as_of=first_close - timedelta(microseconds=1)) == ()
    assert closed_candles(values, as_of=first_close) == (values[0],)
    unfinished = Candle(
        symbol="XAUUSDm",
        timeframe=Timeframe.M5,
        timestamp=values[1].timestamp,
        open=11,
        high=12,
        low=10,
        close=11.5,
        complete=False,
    )
    assert closed_candles([values[0], unfinished]) == (values[0],)


def test_swing_is_only_available_after_right_span_confirmation() -> None:
    values = _candles([10, 12, 10, 11])
    short_history = confirmed_swings(values[:2], left_span=1, right_span=1)
    confirmed = confirmed_swings(values[:3], left_span=1, right_span=1)
    assert short_history == ()
    assert confirmed[0].kind is SwingKind.HIGH
    assert confirmed[0].index == 1
    assert confirmed[0].confirmed_index == 2


def test_structure_reports_higher_highs_lows_and_breaks() -> None:
    values = _candles([10, 12, 10.5, 13, 11.5, 14, 12.5, 15, 14])
    result = analyze_structure(values, left_span=1, right_span=1)
    assert result.bias is MarketBias.BULLISH
    assert result.higher_high is True
    assert result.higher_low is True
    assert result.support == pytest.approx(12.3)
    assert result.resistance == pytest.approx(15.2)
    assert result.breaks


def test_symbol_resolution_uses_metadata_and_fails_closed() -> None:
    disabled_exact = _symbol("XAUUSD", enabled=False)
    enabled_suffix = _symbol("XAUUSDm")
    assert canonicalize_symbol("gold") == "XAUUSD"
    assert resolve_symbol("GOLD", [disabled_exact, enabled_suffix]) is enabled_suffix
    with pytest.raises(SymbolResolutionError):
        resolve_symbol("XAUUSD", [_symbol("EURUSD", enabled=False)])


def test_sessions_and_disabled_news_filter_are_explicit() -> None:
    london = TradingSession("london", time(7), time(16))
    calendar = SessionCalendar((london,), frozenset({"london"}))
    timestamp = datetime(2025, 1, 6, 12, tzinfo=UTC)
    assert calendar.is_allowed(timestamp)
    news = DisabledNewsFilter()
    assert news.enabled is False
    assert not news.is_high_impact_event_nearby(
        timestamp, frozenset({"USD"}), timedelta(minutes=30)
    )
