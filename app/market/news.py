"""Economic-calendar filtering boundary.

No calendar data is fabricated.  The default provider explicitly reports that
filtering is disabled and therefore never blocks a timestamp.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta


class NewsFilter(ABC):
    """Interface for a future authoritative economic-calendar provider."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this provider can make calendar-based decisions."""
        return False

    @abstractmethod
    def is_high_impact_event_nearby(
        self,
        timestamp: datetime,
        currencies: frozenset[str],
        window: timedelta,
    ) -> bool:
        """Return whether verified high-impact news is within ``window``."""
        return False


@dataclass(frozen=True, slots=True)
class DisabledNewsFilter(NewsFilter):
    """Explicitly disabled provider used until real calendar data is configured."""

    @property
    def enabled(self) -> bool:
        return False

    def is_high_impact_event_nearby(
        self,
        timestamp: datetime,
        currencies: frozenset[str],
        window: timedelta,
    ) -> bool:
        if timestamp.tzinfo is None:
            raise ValueError("news-filter timestamps must be timezone-aware")
        if window < timedelta(0):
            raise ValueError("news window cannot be negative")
        return False
