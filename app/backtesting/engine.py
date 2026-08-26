"""Synchronized, cost-aware, closed-candle backtest execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import floor

from app.backtesting.metrics import calculate_metrics
from app.backtesting.models import BacktestConfig, BacktestResult, BacktestTrade, EquityPoint
from app.domain.enums import Direction, SignalAction, Timeframe
from app.domain.models import Candle, TradeSignal
from app.market.candles import candle_close_time, validate_candle_sequence
from app.strategies.base import Strategy


@dataclass(slots=True)
class _OpenPosition:
    signal: TradeSignal
    direction: Direction
    requested_entry: float
    raw_entry: float
    entry_price: float
    opened_at: datetime
    stop_loss: float
    take_profit: float
    volume: float
    planned_risk: float
    entry_commission: float


class BacktestEngine:
    """Execute decisions at M5 close with H1/M15 bars available at that instant."""

    def __init__(self, strategy: Strategy, config: BacktestConfig | None = None) -> None:
        self.strategy = strategy
        self.config = config or BacktestConfig()

    def _validate_data(
        self, candles: Mapping[Timeframe, Sequence[Candle]]
    ) -> dict[Timeframe, tuple[Candle, ...]]:
        required = (Timeframe.H1, Timeframe.M15, Timeframe.M5)
        missing = [value.value for value in required if value not in candles]
        if missing:
            raise ValueError(f"backtest is missing timeframes: {', '.join(missing)}")
        validated: dict[Timeframe, tuple[Candle, ...]] = {}
        for timeframe in required:
            ordered = tuple(sorted(candles[timeframe], key=lambda item: item.timestamp))
            validated[timeframe] = validate_candle_sequence(ordered)
            if any(not candle.complete for candle in ordered):
                validated[timeframe] = tuple(candle for candle in ordered if candle.complete)
        if any(not validated[timeframe] for timeframe in required):
            raise ValueError("each backtest timeframe needs closed candle data")
        symbols = {candle.symbol for values in validated.values() for candle in values}
        if len(symbols) != 1:
            raise ValueError("all backtest candles must use one resolved broker symbol")
        return validated

    def _spread(self, candle: Candle) -> float:
        return max(candle.spread, self.config.fixed_spread)

    def _fill_pending(
        self, signal: TradeSignal, candle: Candle, balance: float
    ) -> _OpenPosition | None:
        if signal.entry_min is None or signal.entry_max is None or signal.stop_loss is None:
            return None
        entry_min = float(signal.entry_min)
        entry_max = float(signal.entry_max)
        if candle.high < entry_min or candle.low > entry_max:
            return None
        if entry_min <= candle.open <= entry_max:
            raw_entry = candle.open
        elif candle.open < entry_min:
            raw_entry = entry_min
        else:
            raw_entry = entry_max
        direction = signal.direction
        if direction is None or not signal.take_profits:
            return None
        execution_cost = (self._spread(candle) / 2.0) + self.config.slippage
        entry_price = raw_entry + (direction.sign * execution_cost)
        stop = float(signal.stop_loss)
        if (direction is Direction.LONG and stop >= entry_price) or (
            direction is Direction.SHORT and stop <= entry_price
        ):
            return None
        price_risk = abs(entry_price - stop)
        risk_budget = max(balance, 0.0) * self.config.risk_fraction
        volume = risk_budget / (price_risk * self.config.value_per_price_unit_per_lot)
        if volume < self.config.minimum_volume:
            return None
        volume = min(volume, self.config.maximum_volume)
        volume = floor((volume / self.config.volume_step) + 1e-12) * self.config.volume_step
        if volume < self.config.minimum_volume:
            return None
        planned_risk = price_risk * volume * self.config.value_per_price_unit_per_lot
        return _OpenPosition(
            signal=signal,
            direction=direction,
            requested_entry=(entry_min + entry_max) / 2.0,
            raw_entry=raw_entry,
            entry_price=entry_price,
            opened_at=candle.timestamp,
            stop_loss=stop,
            take_profit=float(signal.take_profits[0]),
            volume=volume,
            planned_risk=planned_risk,
            entry_commission=volume * self.config.commission_per_lot_per_side,
        )

    @staticmethod
    def _trigger(position: _OpenPosition, candle: Candle) -> tuple[float, str] | None:
        """Resolve ambiguous same-bar SL/TP conservatively in favor of the stop."""

        stop_hit = candle.low <= position.stop_loss <= candle.high
        target_hit = candle.low <= position.take_profit <= candle.high
        if stop_hit:
            return position.stop_loss, "STOP_LOSS"
        if target_hit:
            return position.take_profit, "TAKE_PROFIT"
        return None

    def _close(
        self,
        position: _OpenPosition,
        candle: Candle,
        raw_exit: float,
        reason: str,
        closed_at: datetime,
    ) -> BacktestTrade:
        exit_cost = (self._spread(candle) / 2.0) + self.config.slippage
        exit_price = raw_exit - (position.direction.sign * exit_cost)
        gross_pnl = (
            position.direction.sign
            * (raw_exit - position.raw_entry)
            * position.volume
            * self.config.value_per_price_unit_per_lot
        )
        price_pnl_after_spread = (
            position.direction.sign
            * (exit_price - position.entry_price)
            * position.volume
            * self.config.value_per_price_unit_per_lot
        )
        exit_commission = position.volume * self.config.commission_per_lot_per_side
        total_commission = position.entry_commission + exit_commission
        net_pnl = price_pnl_after_spread - total_commission
        costs = gross_pnl - net_pnl
        return BacktestTrade(
            signal_id=str(position.signal.signal_id),
            direction=position.direction,
            signal_time=position.signal.created_at,
            opened_at=position.opened_at,
            closed_at=closed_at,
            requested_entry=position.requested_entry,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            volume=position.volume,
            planned_risk=position.planned_risk,
            gross_pnl=gross_pnl,
            costs=costs,
            net_pnl=net_pnl,
            r_multiple=net_pnl / position.planned_risk if position.planned_risk else 0.0,
            exit_reason=reason,
        )

    def run(self, candles: Mapping[Timeframe, Sequence[Candle]]) -> BacktestResult:
        """Run one deterministic backtest; supplied strategy/config are never mutated."""

        data = self._validate_data(candles)
        m5 = data[Timeframe.M5]
        balance = self.config.initial_balance
        equity_curve: list[EquityPoint] = [EquityPoint(m5[0].timestamp, balance)]
        trades: list[BacktestTrade] = []
        pending: TradeSignal | None = None
        position: _OpenPosition | None = None
        signals_generated = signals_expired = 0
        available_ends = {timeframe: 0 for timeframe in data}

        for candle in m5:
            decision_time = candle_close_time(candle)

            if position is not None:
                trigger = self._trigger(position, candle)
                if trigger is not None:
                    raw_exit, reason = trigger
                    trade = self._close(position, candle, raw_exit, reason, decision_time)
                    trades.append(trade)
                    balance += trade.net_pnl
                    equity_curve.append(EquityPoint(decision_time, balance))
                    position = None

            if position is None and pending is not None:
                if pending.expires_at is not None and candle.timestamp >= pending.expires_at:
                    signals_expired += 1
                    pending = None
                else:
                    position = self._fill_pending(pending, candle, balance)
                    if position is not None:
                        pending = None
                        trigger = self._trigger(position, candle)
                        if trigger is not None:
                            raw_exit, reason = trigger
                            trade = self._close(position, candle, raw_exit, reason, decision_time)
                            trades.append(trade)
                            balance += trade.net_pnl
                            equity_curve.append(EquityPoint(decision_time, balance))
                            position = None

            available: dict[Timeframe, tuple[Candle, ...]] = {}
            for timeframe, values in data.items():
                end = available_ends[timeframe]
                while end < len(values) and candle_close_time(values[end]) <= decision_time:
                    end += 1
                available_ends[timeframe] = end
                start = max(0, end - self.config.history_window_bars)
                available[timeframe] = values[start:end]
            if all(available.values()):
                open_direction = position.direction if position is not None else None
                signal = self.strategy.evaluate(
                    available, as_of=decision_time, open_direction=open_direction
                )
                if signal.action is SignalAction.EXIT and position is not None:
                    trade = self._close(
                        position, candle, candle.close, "STRATEGY_EXIT", decision_time
                    )
                    trades.append(trade)
                    balance += trade.net_pnl
                    equity_curve.append(EquityPoint(decision_time, balance))
                    position = None
                elif (
                    signal.action in (SignalAction.LONG, SignalAction.SHORT)
                    and position is None
                    and pending is None
                ):
                    pending = signal
                    signals_generated += 1

        last = m5[-1]
        ended_at = candle_close_time(last)
        if position is not None and self.config.close_open_position_at_end:
            trade = self._close(position, last, last.close, "END_OF_DATA", ended_at)
            trades.append(trade)
            balance += trade.net_pnl
            equity_curve.append(EquityPoint(ended_at, balance))
        if pending is not None:
            signals_expired += 1
        if equity_curve[-1].timestamp != ended_at:
            equity_curve.append(EquityPoint(ended_at, balance))
        metrics = calculate_metrics(trades, equity_curve, self.config.initial_balance)
        return BacktestResult(
            strategy_id=self.strategy.strategy_id,
            strategy_version=self.strategy.strategy_version,
            started_at=m5[0].timestamp,
            ended_at=ended_at,
            initial_balance=self.config.initial_balance,
            final_balance=balance,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            metrics=metrics,
            signals_generated=signals_generated,
            signals_expired=signals_expired,
        )
