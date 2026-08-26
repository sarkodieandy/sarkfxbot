"""initial_schema

Revision ID: a04e8847f625
Revises:
Create Date: 2026-08-26 10:28:18.571350
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a04e8847f625"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_instances",
        sa.Column("instance_key", sa.String(length=160), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_instances")),
        sa.UniqueConstraint("instance_key", name=op.f("uq_bot_instances_instance_key")),
    )
    op.create_index(
        op.f("ix_bot_instances_heartbeat_at"), "bot_instances", ["heartbeat_at"], unique=False
    )
    op.create_table(
        "notifications",
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        "ix_notifications_status_created", "notifications", ["status", "created_at"], unique=False
    )
    op.create_table(
        "outbox_events",
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("deduplication_key", name=op.f("uq_outbox_events_deduplication_key")),
    )
    op.create_index(
        "ix_outbox_events_delivery",
        "outbox_events",
        ["status", "available_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "audit_logs",
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_audit_logs_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_actor_created", "audit_logs", ["actor_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_correlation_id"), "audit_logs", ["correlation_id"], unique=False
    )
    op.create_index(
        "ix_audit_logs_resource_created",
        "audit_logs",
        ["resource_type", "resource_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_table(
        "broker_accounts",
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("broker", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=False),
        sa.Column("account_type", sa.String(length=16), nullable=False),
        sa.Column("server", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_broker_accounts_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broker_accounts")),
        sa.UniqueConstraint(
            "broker", "platform", "server", "external_account_id", name="uq_broker_account_identity"
        ),
    )
    op.create_index(
        op.f("ix_broker_accounts_user_id"), "broker_accounts", ["user_id"], unique=False
    )
    op.create_table(
        "config_versions",
        sa.Column("config_type", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_config_versions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_versions")),
        sa.UniqueConstraint("config_type", "version", name="uq_config_version"),
    )
    op.create_index(
        "ix_config_versions_active", "config_versions", ["config_type", "is_active"], unique=False
    )
    op.create_table(
        "strategy_configs",
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_strategy_configs_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_configs")),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_config_version"),
    )
    op.create_index(
        "ix_strategy_configs_enabled",
        "strategy_configs",
        ["strategy_id", "is_enabled"],
        unique=False,
    )
    op.create_table(
        "system_events",
        sa.Column("bot_instance_id", sa.String(length=36), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["bot_instance_id"],
            ["bot_instances.id"],
            name=op.f("fk_system_events_bot_instance_id_bot_instances"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_events")),
    )
    op.create_index(
        op.f("ix_system_events_bot_instance_id"), "system_events", ["bot_instance_id"], unique=False
    )
    op.create_index(
        op.f("ix_system_events_correlation_id"), "system_events", ["correlation_id"], unique=False
    )
    op.create_index(
        "ix_system_events_severity_created",
        "system_events",
        ["severity", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_events_type_created", "system_events", ["event_type", "created_at"], unique=False
    )
    op.create_table(
        "account_snapshots",
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("account_type", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("balance", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("equity", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("margin", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("free_margin", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_account_snapshots_broker_account_id_broker_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_snapshots")),
        sa.UniqueConstraint("broker_account_id", "timestamp", name="uq_account_snapshot_timestamp"),
    )
    op.create_index(
        op.f("ix_account_snapshots_timestamp"), "account_snapshots", ["timestamp"], unique=False
    )
    op.create_table(
        "broker_connections",
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("bot_instance_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bot_instance_id"],
            ["bot_instances.id"],
            name=op.f("fk_broker_connections_bot_instance_id_bot_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_broker_connections_broker_account_id_broker_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broker_connections")),
    )
    op.create_index(
        "ix_broker_connections_account_status",
        "broker_connections",
        ["broker_account_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_broker_connections_bot_instance_id"),
        "broker_connections",
        ["bot_instance_id"],
        unique=False,
    )
    op.create_table(
        "daily_metrics",
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("starting_equity", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("ending_equity", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("loss_count", sa.Integer(), nullable=False),
        sa.Column("profit_factor", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("statistics", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_daily_metrics_broker_account_id_broker_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_metrics")),
        sa.UniqueConstraint(
            "metric_date", "broker_account_id", "strategy_id", name="uq_daily_metric_scope"
        ),
    )
    op.create_index(
        op.f("ix_daily_metrics_metric_date"), "daily_metrics", ["metric_date"], unique=False
    )
    op.create_table(
        "risk_snapshots",
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("bot_instance_id", sa.String(length=36), nullable=True),
        sa.Column("balance", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("equity", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("free_margin", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("realized_daily_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("open_risk", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("daily_drawdown", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("weekly_drawdown", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("account_drawdown", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("peak_to_valley_drawdown", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column(
            "circuit_breaker_active", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["bot_instance_id"],
            ["bot_instances.id"],
            name=op.f("fk_risk_snapshots_bot_instance_id_bot_instances"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_risk_snapshots_broker_account_id_broker_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_snapshots")),
    )
    op.create_index(
        "ix_risk_snapshots_account_timestamp",
        "risk_snapshots",
        ["broker_account_id", "timestamp"],
        unique=False,
    )
    op.create_table(
        "symbols",
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=32), nullable=False),
        sa.Column("broker_symbol", sa.String(length=64), nullable=False),
        sa.Column("base_currency", sa.String(length=12), nullable=False),
        sa.Column("quote_currency", sa.String(length=12), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("digits", sa.Integer(), nullable=False),
        sa.Column("point", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("tick_value", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("contract_size", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume_min", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume_max", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume_step", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("stops_level_points", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_symbols_broker_account_id_broker_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbols")),
        sa.UniqueConstraint(
            "broker_account_id", "broker_symbol", name="uq_symbol_account_broker_symbol"
        ),
    )
    op.create_index(
        "ix_symbols_canonical_active", "symbols", ["canonical_symbol", "is_active"], unique=False
    )
    op.create_table(
        "market_snapshots",
        sa.Column("symbol_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("candle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("high", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("low", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("close", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("spread", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("is_closed", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_market_snapshots_symbol_id_symbols"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_snapshots")),
        sa.UniqueConstraint("symbol", "timeframe", "candle_time", name="uq_market_snapshot_candle"),
    )
    op.create_index(
        op.f("ix_market_snapshots_symbol_id"), "market_snapshots", ["symbol_id"], unique=False
    )
    op.create_index(
        "ix_market_snapshots_symbol_time",
        "market_snapshots",
        ["symbol", "candle_time"],
        unique=False,
    )
    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("strategy_config_id", sa.String(length=36), nullable=True),
        sa.Column("symbol_id", sa.String(length=36), nullable=True),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("entry_min", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("entry_max", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("stop_loss", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("take_profits", sa.JSON(), nullable=False),
        sa.Column("risk_reward", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("confidence_score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["strategy_config_id"],
            ["strategy_configs.id"],
            name=op.f("fk_signals_strategy_config_id_strategy_configs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["symbol_id"],
            ["symbols.id"],
            name=op.f("fk_signals_symbol_id_symbols"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("signal_id", name=op.f("pk_signals")),
    )
    op.create_index(op.f("ix_signals_expires_at"), "signals", ["expires_at"], unique=False)
    op.create_index(
        op.f("ix_signals_strategy_config_id"), "signals", ["strategy_config_id"], unique=False
    )
    op.create_index(
        "ix_signals_strategy_created", "signals", ["strategy_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_signals_symbol_id"), "signals", ["symbol_id"], unique=False)
    op.create_index(
        "ix_signals_symbol_status_created",
        "signals",
        ["symbol", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "orders",
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("broker_ticket", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("requested_price", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("executed_price", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("stop_loss", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("take_profits", sa.JSON(), nullable=False),
        sa.Column("maximum_slippage", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("actual_slippage", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("broker_code", sa.String(length=64), nullable=True),
        sa.Column("broker_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_orders_broker_account_id_broker_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.signal_id"],
            name=op.f("fk_orders_signal_id_signals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("broker_account_id", "broker_ticket", name="uq_order_account_ticket"),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_orders_idempotency_key")),
    )
    op.create_index(
        "ix_orders_account_status", "orders", ["broker_account_id", "status"], unique=False
    )
    op.create_index("ix_orders_signal_status", "orders", ["signal_id", "status"], unique=False)
    op.create_table(
        "signal_conditions",
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("condition_name", sa.String(length=128), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("observed_value", sa.JSON(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.signal_id"],
            name=op.f("fk_signal_conditions_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_conditions")),
        sa.UniqueConstraint("signal_id", "condition_name", name="uq_signal_condition_name"),
    )
    op.create_index(
        op.f("ix_signal_conditions_signal_id"), "signal_conditions", ["signal_id"], unique=False
    )
    op.create_table(
        "execution_attempts",
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempt_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("broker_ticket", sa.String(length=128), nullable=True),
        sa.Column("broker_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_execution_attempts_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_attempts")),
        sa.UniqueConstraint("attempt_key", name=op.f("uq_execution_attempts_attempt_key")),
        sa.UniqueConstraint("order_id", "attempt_number", name="uq_execution_attempt_number"),
    )
    op.create_index(
        op.f("ix_execution_attempts_broker_ticket"),
        "execution_attempts",
        ["broker_ticket"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_attempts_order_id"), "execution_attempts", ["order_id"], unique=False
    )
    op.create_index(
        "ix_execution_attempts_status_started",
        "execution_attempts",
        ["status", "started_at"],
        unique=False,
    )
    op.create_table(
        "positions",
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("signal_id", sa.String(length=36), nullable=True),
        sa.Column("opening_order_id", sa.String(length=36), nullable=True),
        sa.Column("broker_ticket", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("initial_volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("current_volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("open_price", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("current_price", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("stop_loss", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("take_profits", sa.JSON(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_positions_broker_account_id_broker_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opening_order_id"],
            ["orders.id"],
            name=op.f("fk_positions_opening_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.signal_id"],
            name=op.f("fk_positions_signal_id_signals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions")),
        sa.UniqueConstraint(
            "broker_account_id", "broker_ticket", name="uq_position_account_ticket"
        ),
        sa.UniqueConstraint("opening_order_id", name=op.f("uq_positions_opening_order_id")),
    )
    op.create_index(
        "ix_positions_account_state", "positions", ["broker_account_id", "state"], unique=False
    )
    op.create_index(op.f("ix_positions_signal_id"), "positions", ["signal_id"], unique=False)
    op.create_index(
        "ix_positions_symbol_closed", "positions", ["symbol", "closed_at"], unique=False
    )
    op.create_table(
        "trades",
        sa.Column("position_id", sa.String(length=36), nullable=False),
        sa.Column("signal_id", sa.String(length=36), nullable=True),
        sa.Column("broker_account_id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("broker_ticket", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("exit_price", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("stop_loss", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("initial_stop_loss", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("take_profit_1", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("take_profit_2", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("take_profit_3", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("risk_amount", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("risk_percentage", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("risk_reward", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("gross_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("net_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("commission", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("swap", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("spread_cost_estimate", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("slippage", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("slippage_cost", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("r_multiple", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("exit_reason", sa.String(length=128), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broker_account_id"],
            ["broker_accounts.id"],
            name=op.f("fk_trades_broker_account_id_broker_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_trades_position_id_positions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.signal_id"],
            name=op.f("fk_trades_signal_id_signals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trades")),
        sa.UniqueConstraint("broker_account_id", "broker_ticket", name="uq_trade_account_ticket"),
        sa.UniqueConstraint("position_id", name=op.f("uq_trades_position_id")),
    )
    op.create_index(op.f("ix_trades_signal_id"), "trades", ["signal_id"], unique=False)
    op.create_index(
        "ix_trades_strategy_closed", "trades", ["strategy_id", "closed_at"], unique=False
    )
    op.create_index("ix_trades_symbol_closed", "trades", ["symbol", "closed_at"], unique=False)
    op.create_table(
        "trade_events",
        sa.Column("trade_id", sa.String(length=36), nullable=True),
        sa.Column("position_id", sa.String(length=36), nullable=True),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("current_state", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_trade_events_position_id_positions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trade_id"],
            ["trades.id"],
            name=op.f("fk_trade_events_trade_id_trades"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trade_events")),
        sa.UniqueConstraint("event_key", name=op.f("uq_trade_events_event_key")),
    )
    op.create_index(
        op.f("ix_trade_events_position_id"), "trade_events", ["position_id"], unique=False
    )
    op.create_index(
        "ix_trade_events_position_occurred",
        "trade_events",
        ["position_id", "occurred_at"],
        unique=False,
    )
    op.create_index(op.f("ix_trade_events_trade_id"), "trade_events", ["trade_id"], unique=False)
    op.create_index(
        "ix_trade_events_trade_occurred", "trade_events", ["trade_id", "occurred_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_trade_events_trade_occurred", table_name="trade_events")
    op.drop_index(op.f("ix_trade_events_trade_id"), table_name="trade_events")
    op.drop_index("ix_trade_events_position_occurred", table_name="trade_events")
    op.drop_index(op.f("ix_trade_events_position_id"), table_name="trade_events")
    op.drop_table("trade_events")
    op.drop_index("ix_trades_symbol_closed", table_name="trades")
    op.drop_index("ix_trades_strategy_closed", table_name="trades")
    op.drop_index(op.f("ix_trades_signal_id"), table_name="trades")
    op.drop_table("trades")
    op.drop_index("ix_positions_symbol_closed", table_name="positions")
    op.drop_index(op.f("ix_positions_signal_id"), table_name="positions")
    op.drop_index("ix_positions_account_state", table_name="positions")
    op.drop_table("positions")
    op.drop_index("ix_execution_attempts_status_started", table_name="execution_attempts")
    op.drop_index(op.f("ix_execution_attempts_order_id"), table_name="execution_attempts")
    op.drop_index(op.f("ix_execution_attempts_broker_ticket"), table_name="execution_attempts")
    op.drop_table("execution_attempts")
    op.drop_index(op.f("ix_signal_conditions_signal_id"), table_name="signal_conditions")
    op.drop_table("signal_conditions")
    op.drop_index("ix_orders_signal_status", table_name="orders")
    op.drop_index("ix_orders_account_status", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_signals_symbol_status_created", table_name="signals")
    op.drop_index(op.f("ix_signals_symbol_id"), table_name="signals")
    op.drop_index("ix_signals_strategy_created", table_name="signals")
    op.drop_index(op.f("ix_signals_strategy_config_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_expires_at"), table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_market_snapshots_symbol_time", table_name="market_snapshots")
    op.drop_index(op.f("ix_market_snapshots_symbol_id"), table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_symbols_canonical_active", table_name="symbols")
    op.drop_table("symbols")
    op.drop_index("ix_risk_snapshots_account_timestamp", table_name="risk_snapshots")
    op.drop_table("risk_snapshots")
    op.drop_index(op.f("ix_daily_metrics_metric_date"), table_name="daily_metrics")
    op.drop_table("daily_metrics")
    op.drop_index(op.f("ix_broker_connections_bot_instance_id"), table_name="broker_connections")
    op.drop_index("ix_broker_connections_account_status", table_name="broker_connections")
    op.drop_table("broker_connections")
    op.drop_index(op.f("ix_account_snapshots_timestamp"), table_name="account_snapshots")
    op.drop_table("account_snapshots")
    op.drop_index("ix_system_events_type_created", table_name="system_events")
    op.drop_index("ix_system_events_severity_created", table_name="system_events")
    op.drop_index(op.f("ix_system_events_correlation_id"), table_name="system_events")
    op.drop_index(op.f("ix_system_events_bot_instance_id"), table_name="system_events")
    op.drop_table("system_events")
    op.drop_index("ix_strategy_configs_enabled", table_name="strategy_configs")
    op.drop_table("strategy_configs")
    op.drop_index("ix_config_versions_active", table_name="config_versions")
    op.drop_table("config_versions")
    op.drop_index(op.f("ix_broker_accounts_user_id"), table_name="broker_accounts")
    op.drop_table("broker_accounts")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource_created", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_correlation_id"), table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("users")
    op.drop_index("ix_outbox_events_delivery", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_notifications_status_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_bot_instances_heartbeat_at"), table_name="bot_instances")
    op.drop_table("bot_instances")
