from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.backtesting.engine import BacktestEngine
from app.backtesting.metrics import calculate_metrics
from app.backtesting.models import BacktestConfig, BacktestTrade, EquityPoint
from app.backtesting.reports import write_html_report, write_json_report
from app.backtesting.walk_forward import chronological_split, rolling_walk_forward_splits
from app.domain.enums import Direction, SignalAction, Timeframe
from app.domain.models import Candle, TradeSignal
from app.market.candles import candle_close_time


class OneShotStrategy:
    strategy_id = "one_shot"
    strategy_version = "1.0.0"

    def __init__(self) -> None:
        self.called_at: list[datetime] = []

    def evaluate(
        self,
        candles: Mapping[Timeframe, Sequence[Candle]],
        *,
        as_of: datetime | None = None,
        open_direction: Direction | None = None,
    ) -> TradeSignal:
        assert as_of is not None
        self.called_at.append(as_of)
        assert all(candle_close_time(values[-1]) <= as_of for values in candles.values() if values)
        common = {
            "symbol": "XAUUSDm",
            "canonical_symbol": "XAUUSD",
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "created_at": as_of,
        }
        if len(self.called_at) == 1:
            return TradeSignal(
                **common,
                action=SignalAction.LONG,
                confidence_score=100,
                entry_min=Decimal("99.9"),
                entry_max=Decimal("100.1"),
                stop_loss=Decimal("99"),
                take_profits=(Decimal("102"),),
                risk_reward=Decimal("2"),
                expires_at=as_of + timedelta(minutes=15),
                signal_id=UUID("00000000-0000-0000-0000-000000000001"),
            )
        return TradeSignal(
            **common,
            action=SignalAction.WAIT,
            confidence_score=0,
            entry_min=None,
            entry_max=None,
            stop_loss=None,
            signal_id=UUID(int=len(self.called_at)),
        )


def _candle(
    timeframe: Timeframe,
    timestamp: datetime,
    open_price: float,
    high: float,
    low: float,
    close: float,
    spread: float = 0.2,
) -> Candle:
    return Candle(
        "XAUUSDm",
        timeframe,
        timestamp,
        open_price,
        high,
        low,
        close,
        100,
        spread,
    )


def _backtest_data() -> dict[Timeframe, tuple[Candle, ...]]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    return {
        Timeframe.H1: (_candle(Timeframe.H1, start - timedelta(hours=1), 100, 101, 99, 100),),
        Timeframe.M15: (_candle(Timeframe.M15, start - timedelta(minutes=15), 100, 101, 99, 100),),
        Timeframe.M5: (
            _candle(Timeframe.M5, start, 100, 100.2, 99.8, 100),
            _candle(Timeframe.M5, start + timedelta(minutes=5), 100, 103, 99.8, 102.5),
            _candle(Timeframe.M5, start + timedelta(minutes=10), 102.5, 103, 102, 102.7),
        ),
    }


def test_backtest_synchronizes_timeframes_and_charges_costs() -> None:
    strategy = OneShotStrategy()
    result = BacktestEngine(
        strategy,
        BacktestConfig(
            initial_balance=1_000,
            risk_fraction=0.01,
            value_per_price_unit_per_lot=1,
            commission_per_lot_per_side=0.5,
            fixed_spread=0.2,
            slippage=0.05,
            volume_step=0.1,
        ),
    ).run(_backtest_data())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "TAKE_PROFIT"
    assert trade.costs > 0
    assert trade.net_pnl < trade.gross_pnl
    assert trade.volume == pytest.approx(8.6)
    assert result.metrics["total_trades"] == 1
    assert strategy.called_at


def _trade(pnl: float, closed_at: datetime, index: int) -> BacktestTrade:
    return BacktestTrade(
        signal_id=str(index),
        direction=Direction.LONG,
        signal_time=closed_at - timedelta(hours=1),
        opened_at=closed_at - timedelta(minutes=30),
        closed_at=closed_at,
        requested_entry=100,
        entry_price=100,
        exit_price=100 + pnl,
        stop_loss=99,
        take_profit=102,
        volume=1,
        planned_risk=10,
        gross_pnl=pnl,
        costs=0,
        net_pnl=pnl,
        r_multiple=pnl / 10,
        exit_reason="TEST",
    )


def test_full_metrics_include_drawdown_returns_and_streaks() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    trades = tuple(
        _trade(pnl, start + timedelta(days=index), index)
        for index, pnl in enumerate((10.0, 20.0, -5.0, -7.0), start=1)
    )
    curve = (
        EquityPoint(start, 100),
        EquityPoint(start + timedelta(days=1), 110),
        EquityPoint(start + timedelta(days=2), 130),
        EquityPoint(start + timedelta(days=3), 125),
        EquityPoint(start + timedelta(days=4), 118),
    )
    metrics = calculate_metrics(trades, curve, 100)
    required = {
        "total_trades",
        "wins",
        "losses",
        "win_rate",
        "average_win",
        "average_loss",
        "profit_factor",
        "expectancy",
        "maximum_drawdown",
        "average_r",
        "median_r",
        "largest_win",
        "largest_loss",
        "consecutive_wins",
        "consecutive_losses",
        "sharpe_ratio",
        "sortino_ratio",
        "monthly_returns",
        "weekly_returns",
        "daily_returns",
        "daily_pnl",
        "weekly_pnl",
        "monthly_pnl",
        "long_performance",
        "short_performance",
        "time_of_day_performance",
        "session_performance",
    }
    assert required <= metrics.keys()
    assert metrics["consecutive_wins"] == 2
    assert metrics["consecutive_losses"] == 2
    assert metrics["maximum_drawdown"] == pytest.approx(12 / 130)
    assert metrics["long_performance"]["total_trades"] == 4
    assert metrics["short_performance"]["total_trades"] == 0
    assert sum(metrics["daily_pnl"].values()) == pytest.approx(18)


def test_splits_are_chronological_and_reports_are_serializable(tmp_path: Path) -> None:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    m5 = tuple(
        _candle(Timeframe.M5, start + timedelta(minutes=5 * index), 100, 101, 99, 100)
        for index in range(20)
    )
    data = {Timeframe.M5: m5}
    split = chronological_split(data)
    assert split.train[Timeframe.M5][-1].timestamp < split.validation[Timeframe.M5][0].timestamp
    assert split.validation[Timeframe.M5][-1].timestamp < split.test[Timeframe.M5][0].timestamp
    folds = rolling_walk_forward_splits(
        data, train_bars=8, validation_bars=4, test_bars=4, step_bars=4
    )
    assert len(folds) == 2
    first_fold_start = folds[0].data.train[Timeframe.M5][0].timestamp
    second_fold_start = folds[1].data.train[Timeframe.M5][0].timestamp
    assert second_fold_start > first_fold_start

    result = BacktestEngine(OneShotStrategy()).run(_backtest_data())
    json_path = write_json_report(result, tmp_path / "report.json")
    html_path = write_html_report(result, tmp_path / "report.html")
    assert '"strategy_id": "one_shot"' in json_path.read_text(encoding="utf-8")
    assert "Backtests and confidence scores" in html_path.read_text(encoding="utf-8")
