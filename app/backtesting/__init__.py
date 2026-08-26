"""Point-in-time-safe backtesting, metrics, reports, and data splits."""

from app.backtesting.data import load_candle_csv
from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import calculate_metrics
from app.backtesting.models import BacktestConfig, BacktestResult, BacktestTrade, EquityPoint
from app.backtesting.reports import write_html_report, write_json_report
from app.backtesting.walk_forward import (
    DatasetSplit,
    WalkForwardFold,
    chronological_split,
    rolling_walk_forward_splits,
)

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BacktestTrade",
    "DatasetSplit",
    "EquityPoint",
    "WalkForwardFold",
    "calculate_metrics",
    "chronological_split",
    "load_candle_csv",
    "rolling_walk_forward_splits",
    "write_html_report",
    "write_json_report",
]
