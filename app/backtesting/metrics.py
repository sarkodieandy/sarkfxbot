"""Complete, dependency-free trading performance metrics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from math import sqrt
from statistics import fmean, median, stdev
from typing import Any

from app.backtesting.models import BacktestTrade, EquityPoint
from app.domain.enums import Direction
from app.market.sessions import default_session_calendar


def _streaks(values: Sequence[float]) -> tuple[int, int]:
    longest_wins = longest_losses = current_wins = current_losses = 0
    for value in values:
        if value > 0:
            current_wins += 1
            current_losses = 0
        elif value < 0:
            current_losses += 1
            current_wins = 0
        else:
            current_wins = current_losses = 0
        longest_wins = max(longest_wins, current_wins)
        longest_losses = max(longest_losses, current_losses)
    return longest_wins, longest_losses


def _drawdown(equity_curve: Sequence[EquityPoint]) -> tuple[float, float]:
    if not equity_curve:
        return 0.0, 0.0
    peak = equity_curve[0].equity
    maximum_amount = maximum_fraction = 0.0
    for point in equity_curve:
        peak = max(peak, point.equity)
        amount = peak - point.equity
        fraction = amount / peak if peak > 0 else 0.0
        maximum_amount = max(maximum_amount, amount)
        maximum_fraction = max(maximum_fraction, fraction)
    return maximum_amount, maximum_fraction


def _period_key(trade: BacktestTrade, period: str) -> str:
    if period == "daily":
        return trade.closed_at.date().isoformat()
    if period == "weekly":
        iso_year, iso_week, _ = trade.closed_at.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "monthly":
        return trade.closed_at.strftime("%Y-%m")
    raise ValueError(f"unsupported metric period: {period}")


def _period_pnl(trades: Sequence[BacktestTrade], period: str) -> dict[str, float]:
    grouped: defaultdict[str, float] = defaultdict(float)
    for trade in trades:
        grouped[_period_key(trade, period)] += trade.net_pnl
    return dict(sorted(grouped.items()))


def _period_returns(
    trades: Sequence[BacktestTrade], initial_balance: float, period: str
) -> dict[str, float]:
    grouped = _period_pnl(trades, period)
    balance = initial_balance
    returns: dict[str, float] = {}
    for key, pnl in grouped.items():
        returns[key] = pnl / balance if balance else 0.0
        balance += pnl
    return returns


def _segment_metrics(trades: Sequence[BacktestTrade]) -> dict[str, float | int | None]:
    pnl = [trade.net_pnl for trade in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_loss = abs(sum(losses))
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "net_profit": sum(pnl),
        "average_r": fmean([trade.r_multiple for trade in trades]) if trades else 0.0,
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
    }


def _time_and_session_performance(
    trades: Sequence[BacktestTrade],
) -> tuple[dict[str, dict[str, float | int | None]], dict[str, dict[str, float | int | None]]]:
    by_hour: defaultdict[str, list[BacktestTrade]] = defaultdict(list)
    by_session: defaultdict[str, list[BacktestTrade]] = defaultdict(list)
    calendar = default_session_calendar(
        frozenset({"asia", "london", "new_york", "london_new_york_overlap"})
    )
    for trade in trades:
        by_hour[f"{trade.opened_at.hour:02d}:00"].append(trade)
        active = calendar.active_sessions(trade.opened_at)
        if not active:
            by_session["outside_configured_sessions"].append(trade)
        for name in active:
            by_session[name].append(trade)
    return (
        {key: _segment_metrics(values) for key, values in sorted(by_hour.items())},
        {key: _segment_metrics(values) for key, values in sorted(by_session.items())},
    )


def _risk_adjusted(daily_returns: Sequence[float]) -> tuple[float | None, float | None]:
    if len(daily_returns) < 2:
        return None, None
    mean = fmean(daily_returns)
    deviation = stdev(daily_returns)
    sharpe = (mean / deviation) * sqrt(252) if deviation > 0 else None
    downside = [min(value, 0.0) for value in daily_returns]
    downside_deviation = sqrt(sum(value * value for value in downside) / len(downside))
    sortino = (mean / downside_deviation) * sqrt(252) if downside_deviation > 0 else None
    return sharpe, sortino


def calculate_metrics(
    trades: Sequence[BacktestTrade],
    equity_curve: Sequence[EquityPoint],
    initial_balance: float,
) -> dict[str, Any]:
    """Calculate every metric required by the GoldFlow backtest contract."""

    pnl = [trade.net_pnl for trade in trades]
    r_multiples = [trade.r_multiple for trade in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    max_drawdown_amount, max_drawdown = _drawdown(equity_curve)
    daily_pnl = _period_pnl(trades, "daily")
    weekly_pnl = _period_pnl(trades, "weekly")
    monthly_pnl = _period_pnl(trades, "monthly")
    daily_returns = _period_returns(trades, initial_balance, "daily")
    weekly_returns = _period_returns(trades, initial_balance, "weekly")
    monthly_returns = _period_returns(trades, initial_balance, "monthly")
    sharpe, sortino = _risk_adjusted(tuple(daily_returns.values()))
    consecutive_wins, consecutive_losses = _streaks(pnl)
    total = len(trades)
    time_of_day, session_performance = _time_and_session_performance(trades)
    long_trades = tuple(trade for trade in trades if trade.direction is Direction.LONG)
    short_trades = tuple(trade for trade in trades if trade.direction is Direction.SHORT)
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / total if total else 0.0,
        "average_win": fmean(wins) if wins else 0.0,
        "average_loss": fmean(losses) if losses else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expectancy": fmean(pnl) if pnl else 0.0,
        "maximum_drawdown": max_drawdown,
        "maximum_drawdown_amount": max_drawdown_amount,
        "average_r": fmean(r_multiples) if r_multiples else 0.0,
        "median_r": median(r_multiples) if r_multiples else 0.0,
        "largest_win": max(wins) if wins else 0.0,
        "largest_loss": min(losses) if losses else 0.0,
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "daily_pnl": daily_pnl,
        "weekly_pnl": weekly_pnl,
        "monthly_pnl": monthly_pnl,
        "monthly_returns": monthly_returns,
        "weekly_returns": weekly_returns,
        "daily_returns": daily_returns,
        "long_performance": _segment_metrics(long_trades),
        "short_performance": _segment_metrics(short_trades),
        "time_of_day_performance": time_of_day,
        "session_performance": session_performance,
        "net_profit": sum(pnl),
        "return_on_initial_balance": sum(pnl) / initial_balance if initial_balance else 0.0,
    }
