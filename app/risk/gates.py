"""Fail-closed pre-trade rule pipeline."""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from app.domain.enums import Direction
from app.domain.models import OrderRequest
from app.risk.models import (
    PositionSizingResult,
    PreTradeSnapshot,
    RiskDecision,
    RiskLimits,
)


class RiskGateValidator:
    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def validate(
        self,
        request: OrderRequest,
        sizing: PositionSizingResult,
        snapshot: PreTradeSnapshot,
    ) -> RiskDecision:
        reasons: list[str] = []
        now = snapshot.now.astimezone(UTC)
        account = snapshot.account
        spec = snapshot.symbol
        tick = snapshot.tick
        price = request.requested_price or (
            tick.ask if request.direction is Direction.LONG else tick.bid
        )

        if snapshot.kill_switch:
            reasons.append("KILL_SWITCH_ACTIVE")
        if snapshot.circuit_locked:
            reasons.append("CIRCUIT_BREAKER_LOCKED")
        if not snapshot.health.healthy or not snapshot.health.connected:
            reasons.append("BROKER_UNHEALTHY")
        if not snapshot.health.trading_allowed:
            reasons.append("BROKER_TRADING_DISABLED")
        if not snapshot.market_open:
            reasons.append("MARKET_CLOSED")
        if not snapshot.session_allowed:
            reasons.append("TRADING_SESSION_NOT_ALLOWED")
        if not spec.trade_enabled:
            reasons.append("SYMBOL_TRADING_DISABLED")
        if tick.symbol != spec.name or request.symbol != spec.name:
            reasons.append("SYMBOL_MISMATCH")
        tick_age = now - tick.timestamp
        if tick_age.total_seconds() < -1:
            reasons.append("TICK_FROM_FUTURE")
        elif tick_age > self._limits.maximum_tick_age:
            reasons.append("STALE_TICK")
        if tick.spread > self._limits.maximum_spread:
            reasons.append("TRADE_SKIPPED_HIGH_SPREAD")
        if request.expires_at is not None and now >= request.expires_at:
            reasons.append("SIGNAL_EXPIRED")
        if request.entry_min is not None and price < request.entry_min:
            reasons.append("PRICE_OUTSIDE_ENTRY_ZONE")
        if request.entry_max is not None and price > request.entry_max:
            reasons.append("PRICE_OUTSIDE_ENTRY_ZONE")
        if request.maximum_slippage < 0:
            reasons.append("INVALID_SLIPPAGE_TOLERANCE")

        stop_distance = abs(price - request.stop_loss)
        minimum_stop_distance = spec.point * spec.stops_level_points
        if stop_distance <= 0 or stop_distance < minimum_stop_distance:
            reasons.append("INVALID_STOP_DISTANCE")
        if request.direction is Direction.LONG:
            if request.stop_loss >= price:
                reasons.append("LONG_STOP_NOT_BELOW_ENTRY")
            if any(target <= price for target in request.take_profits):
                reasons.append("LONG_TAKE_PROFIT_NOT_ABOVE_ENTRY")
        else:
            if request.stop_loss <= price:
                reasons.append("SHORT_STOP_NOT_ABOVE_ENTRY")
            if any(target >= price for target in request.take_profits):
                reasons.append("SHORT_TAKE_PROFIT_NOT_BELOW_ENTRY")

        risk_reward: Decimal | None = None
        if stop_distance > 0 and request.take_profits:
            reward_distances = tuple(abs(target - price) for target in request.take_profits)
            risk_reward = min(reward_distances) / stop_distance
            if risk_reward < self._limits.minimum_risk_reward:
                reasons.append("RISK_REWARD_BELOW_MINIMUM")

        if account.equity <= 0 or account.free_margin <= 0:
            reasons.append("ACCOUNT_EQUITY_OR_MARGIN_INVALID")
        if snapshot.required_margin <= 0 or snapshot.required_margin > account.free_margin:
            reasons.append("INSUFFICIENT_OR_INVALID_MARGIN")
        if request.volume != sizing.volume:
            reasons.append("ORDER_VOLUME_DIFFERS_FROM_RISK_SIZE")
        if sizing.cash_risk > sizing.allowed_cash_risk:
            reasons.append("TRADE_RISK_EXCEEDS_LIMIT")

        if len(snapshot.positions) >= self._limits.maximum_open_positions:
            reasons.append("MAXIMUM_OPEN_POSITIONS_REACHED")
        equivalent_symbols = set(snapshot.equivalent_symbols) | {spec.name}
        gold_positions = sum(item.symbol in equivalent_symbols for item in snapshot.positions)
        if gold_positions >= self._limits.maximum_gold_positions:
            reasons.append("MAXIMUM_GOLD_POSITIONS_REACHED")
        if any(item.symbol in equivalent_symbols for item in snapshot.positions):
            reasons.append("DUPLICATE_POSITION")
        if any(
            item.symbol in equivalent_symbols or item.idempotency_key == request.idempotency_key
            for item in snapshot.orders
        ):
            reasons.append("DUPLICATE_PENDING_ORDER")

        daily_capacity = account.equity * self._limits.maximum_daily_loss
        weekly_capacity = account.equity * self._limits.maximum_weekly_loss
        proposed_daily = (
            snapshot.usage.daily_realized_loss + snapshot.usage.open_risk + sizing.cash_risk
        )
        proposed_weekly = (
            snapshot.usage.weekly_realized_loss + snapshot.usage.open_risk + sizing.cash_risk
        )
        if proposed_daily >= daily_capacity:
            reasons.append("DAILY_LOSS_LIMIT_REACHED")
        if proposed_weekly >= weekly_capacity:
            reasons.append("WEEKLY_LOSS_LIMIT_REACHED")
        if snapshot.usage.peak_equity is not None:
            drawdown = max(
                Decimal("0"),
                (snapshot.usage.peak_equity - account.equity) / snapshot.usage.peak_equity,
            )
            if drawdown >= self._limits.maximum_account_drawdown:
                reasons.append("ACCOUNT_DRAWDOWN_LIMIT_REACHED")

        unique_reasons = tuple(dict.fromkeys(reasons))
        return RiskDecision(
            accepted=not unique_reasons,
            reasons=unique_reasons,
            risk_amount=sizing.cash_risk,
            risk_fraction=sizing.risk_fraction,
            risk_reward=risk_reward,
            required_margin=snapshot.required_margin,
        )
