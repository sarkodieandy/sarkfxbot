"""Signal generation facade and expiration rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.enums import Direction, SignalAction, SignalStatus, Timeframe
from app.domain.models import Candle, TradeSignal
from app.strategies.base import Strategy


def entry_zone_contains(signal: TradeSignal, price: Decimal) -> bool:
    """Return whether a price is inside a trade signal's inclusive entry zone."""

    if signal.action not in (SignalAction.LONG, SignalAction.SHORT):
        return False
    if signal.entry_min is None or signal.entry_max is None:
        return False
    return signal.entry_min <= price <= signal.entry_max


def refresh_signal_status(
    signal: TradeSignal,
    *,
    now: datetime,
    current_price: Decimal | None = None,
) -> TradeSignal:
    """Expire active entries by time or after price leaves their allowed zone."""

    if now.tzinfo is None:
        raise ValueError("signal status timestamps must be timezone-aware")
    if signal.status is not SignalStatus.ACTIVE:
        return signal
    timed_out = signal.is_expired(now.astimezone(UTC))
    left_zone = current_price is not None and not entry_zone_contains(signal, current_price)
    if timed_out or left_zone:
        reason = "SIGNAL_TIME_EXPIRED" if timed_out else "PRICE_LEFT_ENTRY_ZONE"
        return replace(
            signal,
            status=SignalStatus.EXPIRED,
            rationale={**signal.rationale, "expiration_reason": reason},
        )
    return signal


class SignalEngine:
    """Small orchestration boundary that remains independent of execution."""

    def __init__(self, strategy: Strategy) -> None:
        self._strategy = strategy

    def scan(
        self,
        candles: Mapping[Timeframe, Sequence[Candle]],
        *,
        as_of: datetime | None = None,
        open_direction: Direction | None = None,
    ) -> TradeSignal:
        return self._strategy.evaluate(candles, as_of=as_of, open_direction=open_direction)
