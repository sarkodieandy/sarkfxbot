"""Pure, dependency-free technical indicators.

Every function returns a sequence aligned to its input.  Values that cannot yet
be known because the warm-up period is incomplete are represented by ``None``.
"""

from app.indicators.atr import atr, true_ranges
from app.indicators.ema import ema, ema_last
from app.indicators.macd import MACDResult, macd
from app.indicators.rsi import rsi
from app.indicators.spread import average_spread, spread_quality
from app.indicators.volume import relative_volume, volume_sma

__all__ = [
    "MACDResult",
    "atr",
    "average_spread",
    "ema",
    "ema_last",
    "macd",
    "relative_volume",
    "rsi",
    "spread_quality",
    "true_ranges",
    "volume_sma",
]
