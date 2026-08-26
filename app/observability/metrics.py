"""Prometheus-compatible GoldFlow metrics with bounded label cardinality."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class GoldFlowMetrics:
    """Owns a registry so tests and multiple app factories do not collide."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.signals_total = Counter(
            "goldflow_signals_total",
            "Signals evaluated by action",
            ("action", "strategy"),
            registry=self.registry,
        )
        self.trades_total = Counter(
            "goldflow_trades_total",
            "Trades by final outcome",
            ("outcome", "direction"),
            registry=self.registry,
        )
        self.realized_pnl = Gauge(
            "goldflow_realized_pnl",
            "Cumulative realized P&L in account currency",
            registry=self.registry,
        )
        self.current_drawdown = Gauge(
            "goldflow_current_drawdown_ratio",
            "Current peak-to-valley equity drawdown ratio",
            registry=self.registry,
        )
        self.order_failures_total = Counter(
            "goldflow_order_failures_total",
            "Order failures by safe reason code",
            ("reason",),
            registry=self.registry,
        )
        self.broker_disconnects_total = Counter(
            "goldflow_broker_disconnects_total",
            "Broker disconnections",
            registry=self.registry,
        )
        self.signal_latency = Histogram(
            "goldflow_signal_latency_seconds",
            "Signal evaluation latency",
            registry=self.registry,
        )
        self.execution_latency = Histogram(
            "goldflow_execution_latency_seconds",
            "Validated order execution latency",
            registry=self.registry,
        )
        self.spread = Gauge(
            "goldflow_spread_price_units",
            "Current spread in broker-native price units",
            ("symbol",),
            registry=self.registry,
        )
        self.health = Gauge(
            "goldflow_component_healthy",
            "Component health (1 healthy, 0 otherwise)",
            ("component",),
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
