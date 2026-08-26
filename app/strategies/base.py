"""Strategy protocol kept independent of any broker implementation."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.domain.enums import Direction, Timeframe
from app.domain.models import Candle, TradeSignal


class Strategy(Protocol):
    strategy_id: str
    strategy_version: str

    @abstractmethod
    def evaluate(
        self,
        candles: Mapping[Timeframe, Sequence[Candle]],
        *,
        as_of: datetime | None = None,
        open_direction: Direction | None = None,
    ) -> TradeSignal:
        """Evaluate only data available at ``as_of`` and return one canonical action."""
        raise TypeError("Strategy is a structural interface and cannot evaluate directly")
