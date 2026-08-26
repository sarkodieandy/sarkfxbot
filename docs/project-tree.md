# Project tree

Generated runtime artifacts (`.venv`, caches, coverage output, logs, and backtest reports) are
intentionally omitted. They are ignored by `.gitignore`.

```text
goldflow/
├── .env.example
├── .gitignore
├── .python-version
├── Dockerfile
├── Makefile
├── README.md
├── alembic.ini
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── uv.lock
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── a04e8847f625_initial_schema.py
├── app/
│   ├── __init__.py
│   ├── container.py
│   ├── main.py
│   ├── runtime_contract.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dependencies.py
│   │   ├── middleware.py
│   │   ├── runtime.py
│   │   ├── schemas.py
│   │   ├── websocket.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── account.py
│   │       ├── admin.py
│   │       ├── metrics.py
│   │       ├── positions.py
│   │       ├── risk.py
│   │       ├── router.py
│   │       ├── signals.py
│   │       ├── strategy.py
│   │       └── trades.py
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── data.py
│   │   ├── engine.py
│   │   ├── metrics.py
│   │   ├── models.py
│   │   ├── reports.py
│   │   └── walk_forward.py
│   ├── brokers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── mock.py
│   │   ├── mt5.py
│   │   ├── retry.py
│   │   ├── serialized.py
│   │   └── symbols.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── logging.py
│   │   └── settings.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   └── entities.py
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── operations.py
│   │       └── trading.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── enums.py
│   │   ├── errors.py
│   │   ├── models.py
│   │   └── state_machine.py
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── executor.py
│   │   ├── idempotency.py
│   │   ├── models.py
│   │   ├── positions.py
│   │   ├── reconciliation.py
│   │   └── sqlalchemy_idempotency.py
│   ├── indicators/
│   │   ├── __init__.py
│   │   ├── atr.py
│   │   ├── ema.py
│   │   ├── macd.py
│   │   ├── rsi.py
│   │   ├── spread.py
│   │   └── volume.py
│   ├── market/
│   │   ├── __init__.py
│   │   ├── candles.py
│   │   ├── news.py
│   │   ├── sessions.py
│   │   ├── structure.py
│   │   └── symbols.py
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── telegram.py
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── metrics.py
│   │   └── sentry.py
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── calculator.py
│   │   ├── circuit_breaker.py
│   │   ├── demo_validation.py
│   │   ├── gates.py
│   │   ├── mode.py
│   │   └── models.py
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── confidence.py
│   │   └── engine.py
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── gold_h1_m15_m5.py
│   └── workers/
│       ├── __init__.py
│       ├── lease.py
│       ├── persistence.py
│       ├── runner.py
│       └── service.py
├── docs/
│   ├── api.md
│   ├── architecture.md
│   ├── deployment.md
│   ├── operations.md
│   ├── project-tree.md
│   ├── risk-management.md
│   ├── security.md
│   ├── strategy.md
│   └── windows-mt5-setup.md
├── sample_data/
│   ├── README.md
│   ├── generate_sample.py
│   └── xauusd_synthetic.csv
├── scripts/
│   ├── __init__.py
│   ├── _mt5_env.py
│   ├── connect_mt5.py
│   ├── create_token.py
│   ├── dev.ps1
│   ├── discover_symbols.py
│   ├── run_backtest.py
│   └── verify_demo.py
└── tests/
    ├── __init__.py
    ├── integration/
    │   ├── __init__.py
    │   ├── test_api_service_boundary.py
    │   ├── test_database.py
    │   ├── test_worker_persistence.py
    │   └── test_worker_service.py
    └── unit/
        ├── __init__.py
        ├── test_auth_jwt.py
        ├── test_backtest_engine.py
        ├── test_broker_mock.py
        ├── test_broker_mt5_safety.py
        ├── test_broker_retry.py
        ├── test_execution_safety.py
        ├── test_execution_sqlalchemy_idempotency.py
        ├── test_health_observability.py
        ├── test_indicators_core.py
        ├── test_logging.py
        ├── test_market_structure.py
        ├── test_notifications_telegram.py
        ├── test_positions_management.py
        ├── test_reconciliation_recovery.py
        ├── test_risk_circuit_breaker.py
        ├── test_risk_demo_validation.py
        ├── test_risk_hard_gates.py
        ├── test_risk_mode_gates.py
        ├── test_risk_sizing.py
        ├── test_runtime_workers.py
        ├── test_settings.py
        ├── test_strategy_gold.py
        ├── test_symbols_resolution.py
        └── test_worker_runner.py
```
