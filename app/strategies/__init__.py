"""Trading strategy contracts and GoldFlow's first strategy."""

from app.strategies.base import Strategy
from app.strategies.gold_h1_m15_m5 import GoldStrategyConfig, GoldTrendPullbackStrategy

__all__ = ["GoldStrategyConfig", "GoldTrendPullbackStrategy", "Strategy"]
